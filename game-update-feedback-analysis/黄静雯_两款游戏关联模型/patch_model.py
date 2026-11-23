"""Retrieve official patch items and learn bilingual review-to-item links."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import re
import unicodedata

import joblib
import lightgbm
import numpy as np
import sklearn
from lightgbm import LGBMClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT.parent
LABELS = OUTPUTS / "黄静雯_评测标注"
CATALOG = OUTPUTS / "黄静雯_两款游戏版本与改动"
BUNDLE = ROOT / "model.joblib"
FEATURES = ["passage_cosine", "review_cosine", "longest_alias", "distinct_aliases", "local_margin"]
MODEL_ID = "hjw_patch_link_v1"
SEED = 20261120


def rows(path):
    with path.open(encoding="utf8") as stream:
        for line in stream:
            yield json.loads(line)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def passages(text):
    # Preserve the exact source span including offsets. Split unusually long sentences.
    for match in re.finditer(r"[^\r\n。！？!?；;]+(?:[。！？!?；;]+|$)?", text):
        for sentence in re.finditer(r".+?(?:\.(?=\s)|$)", match.group(), re.S):
            start = match.start() + sentence.start()
            end = match.start() + sentence.end()
            for offset in range(start, end, 260):
                stop = min(end, offset + 280)
                if text[offset:stop].strip():
                    yield offset, stop


class CatalogIndex:
    def __init__(self, items):
        self.items = items
        descriptions = []
        self.patterns = []
        for item in items:
            patterns = []
            for lang, title in (("en","title_en"), ("zh","title")):
                aliases = sorted({normalize(t) for t in [item[title], *item["aliases_"+lang]] if t.strip()})
                descriptions.append(" ".join(aliases))
                for alias in aliases:
                    expression = re.escape(alias)
                    if lang == "en":
                        expression = r"(?<![a-z])"+expression+r"(?![a-z])"
                    patterns.append((alias,re.compile(expression)))
            self.patterns.append(patterns)
        self.vec = TfidfVectorizer(analyzer="char", ngram_range=(2,4), dtype=np.float32,
                                   sublinear_tf=True, lowercase=False)
        self.matrix = self.vec.fit_transform(descriptions).T

    def similarity(self, texts):
        sim = (self.vec.transform(texts) @ self.matrix).toarray()
        return sim.reshape(len(texts),len(self.items),2).max(axis=2)

    def candidates(self, reviews):
        spans = [list(passages(r["review"])) for r in reviews]
        norm = [normalize(r["review"]) for r in reviews]
        full = self.similarity(norm)
        texts = [normalize(r["review"][a:b]) for r, ss in zip(reviews,spans) for a,b in ss]
        if not texts:
            return [[] for _ in reviews]
        local = self.similarity(texts)
        results = []
        offset = 0
        for k, ss in enumerate(spans):
            candidates = []
            if ss:
                sims = local[offset:offset+len(ss)]
                for j, item in enumerate(self.items):
                    matches = [(alias,rule) for alias,rule in self.patterns[j] if rule.search(norm[k])]
                    hits = [sum(bool(rule.search(t)) for _,rule in matches)
                            for t in texts[offset:offset+len(ss)]] if matches else np.zeros(len(ss))
                    best = int(np.argmax(np.minimum(hits,2) + sims[:,j]))
                    score = float(sims[best,j]); whole = float(full[k,j])
                    if max(score,whole)==0 and not matches:
                        continue
                    a,b = ss[best]
                    longest = max((min(20,len(alias)) for alias,rule in matches
                                   if rule.search(texts[offset+best])),default=0)
                    features = [score, whole, longest/20, min(hits[best],3)/3,
                                score-float(np.max(np.delete(sims[best],j)))]
                    rank_score = float(longest>0) + .8*score + .2*whole
                    candidates.append((rank_score,item["item_id"],features,a,b))
                candidates.sort(key=lambda c:(-c[0],c[1]))
            results.append(candidates[:5])
            offset += len(ss)
        return results


def model(name):
    if name == "logistic":
        return make_pipeline(StandardScaler(),LogisticRegression(C=1, class_weight="balanced",
                                                                 max_iter=400,random_state=SEED))
    return LGBMClassifier(n_estimators=50,learning_rate=.06,num_leaves=5,max_depth=3,
                          min_child_samples=8,reg_lambda=2,class_weight="balanced",
                          verbosity=-1,n_jobs=2,random_state=SEED)


def scored_examples(records, indices):
    examples = []
    for r in records:
        if r["annotation_label"]=="待判":
            continue
        cs = indices[r["patch_id"]].candidates([r])[0]
        examples.append((r,cs))
    return examples


def fit(name, examples):
    xs=[]; ys=[]
    for r,cs in examples:
        for _,item,features,_,_ in cs:
            xs.append(features); ys.append(int(item in r["item_ids"]))
    assert len(set(ys))==2
    return model(name).fit(np.asarray(xs),ys)


def score(estimator, examples):
    xs=[c[2] for _,cs in examples for c in cs]
    scores=iter(estimator.predict_proba(np.asarray(xs))[:,1] if xs else [])
    return [(r,[(c,float(next(scores))) for c in cs]) for r,cs in examples]


def metrics(scored,threshold):
    gold = retrieved = tp = fp = predicted_reviews = number = 0
    for r,cs in scored:
        expected=set(r["item_ids"])
        proposed={c[1] for c,_ in cs}
        accepted={c[1] for c,s in cs if s>=threshold}
        gold+=len(expected); retrieved+=len(expected & proposed)
        tp+=len(expected & accepted); fp+=len(accepted-expected)
        predicted_reviews+=bool(accepted); number+=len(cs)
    fn=gold-tp
    return dict(reviews=len(scored),candidate_pairs=number,positive_pairs=gold,
                accepted_reviews=predicted_reviews,tp=tp,fp=fp,fn=fn,
                recall_at_5=retrieved/gold if gold else None,
                precision=tp/(tp+fp) if tp+fp else None,recall=tp/gold if gold else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                f05=1.25*tp/(1.25*tp+fp+.25*fn) if 1.25*tp+fp+.25*fn else None)


def choose_threshold(scored):
    grid=[.05*i for i in range(1,20)]
    return max(grid,key=lambda t:(metrics(scored,t)["f05"] or 0,metrics(scored,t)["recall"] or 0,t))


def load_catalog():
    items=list(rows(CATALOG/"items/items.jsonl"))
    patches=list(rows(CATALOG/"patches/patches.jsonl"))
    catalogs={p["patch_id"]:CatalogIndex([i for i in items if i["patch_id"]==p["patch_id"]])
              for p in patches}
    return catalogs,patches


def train():
    indices,patches=load_catalog()
    initial=list(rows(LABELS/"AI逐条复核250条.jsonl"))
    extra=list(rows(LABELS/"留出版本定向补样12条.jsonl"))
    assert len({(r["appid"],r["review_id"]) for r in initial+extra})==len(initial)+len(extra)
    development=[r for r in initial if r["split"]=="development"]
    test=[r for r in initial if r["split"]=="holdout"]
    versions={r["patch_id"] for r in development}
    assert versions.isdisjoint({r["patch_id"] for r in test+extra})
    examples=scored_examples(development,indices)
    reports={}; fits={}; cutoffs={}
    for name in ("logistic","lightgbm"):
        oof=[]
        for pid in sorted(versions):
            training=[e for e in examples if e[0]["patch_id"]!=pid]
            validation=[e for e in examples if e[0]["patch_id"]==pid]
            oof.extend(score(fit(name,training),validation))
        t=choose_threshold(oof)
        reports[name]={"cutoff":t,"oof":metrics(oof,t),
             "by_language":{l:metrics([z for z in oof if z[0]["language"]==l],t)
                            for l in sorted({z[0]["language"] for z in oof})},
             "by_patch":{p:metrics([z for z in oof if z[0]["patch_id"]==p],t) for p in sorted(versions)}}
        fits[name]=fit(name,examples);cutoffs[name]=t
    lr,gb=reports["logistic"],reports["lightgbm"]
    wins=sum((gb["by_patch"][p]["f05"] or 0)>(lr["by_patch"][p]["f05"] or 0)
             for p in versions if lr["by_patch"][p]["positive_pairs"])
    name="lightgbm" if gb["oof"]["f05"]>lr["oof"]["f05"]+.02 and wins>=2 else "logistic"
    estimator=fits[name]; t=cutoffs[name]
    signature=hashlib.sha256((CATALOG/"items/items.jsonl").read_bytes()).hexdigest()
    joblib.dump(dict(name=name,estimator=estimator,threshold=t,features=FEATURES,
                     catalog_sha256=signature,model_id=MODEL_ID),BUNDLE)
    audit={}
    for group,records in (("random_holdout",test),("targeted_diagnostic",extra)):
        predictions=score(estimator,scored_examples(records,indices))
        audit[group]=dict(overall=metrics(predictions,t),by_language={
            l:metrics([z for z in predictions if z[0]["language"]==l],t)
            for l in sorted({z[0]["language"] for z in predictions})},
            cases=[dict(review_id=r["review_id"],patch_id=r["patch_id"],gold=r["item_ids"],
                        predicted=[c[1] for c,s in cs if s>=t]) for r,cs in predictions])
    write_json(ROOT/"evaluation.json",dict(selected=name,models=reports,holdouts=audit,
               labeled_development=len(development),usable_development=len(examples),
               versions=sorted(versions),features=FEATURES,label_source="AI逐条复核",
               software=dict(sklearn=sklearn.__version__,lightgbm=lightgbm.__version__,numpy=np.__version__)))
    print(name,t,[(m,v["oof"]) for m,v in reports.items()],flush=True)
    print([(k,v["overall"]) for k,v in audit.items()],flush=True)


def infer(reviews_path):
    indices,patches=load_catalog()
    bundle=joblib.load(BUNDLE)
    assert bundle["catalog_sha256"]==hashlib.sha256((CATALOG/"items/items.jsonl").read_bytes()).hexdigest()
    win=defaultdict(list)
    for p in patches:
        for d in range(-14,15):
            win[p["appid"],p["release_day"]+d].append((p["patch_id"],d))
    queues=defaultdict(list); stats=defaultdict(Counter); by_patch=defaultdict(Counter)
    def flush(pid,out):
        batch=queues[pid]
        if not batch:return
        candidates=indices[pid].candidates(batch)
        for (r,cs) in score(bundle["estimator"],list(zip(batch,candidates))):
            tally=by_patch[pid]; tally["in_window"]+=1; tally["with_candidates"]+=bool(cs)
            tally["candidate_pairs"]+=len(cs)
            accepted=0
            for (_,item,_,a,b),s in cs:
                if s<bundle["threshold"]: continue
                quote=r["review"][a:b]
                rec={k:v for k,v in r.items() if k!="review"}
                rec.update(item_id=item,score=round(s,8),threshold=bundle["threshold"],
                           model_id=MODEL_ID,evidence=quote,span_start=a,span_end=b)
                out.write(json.dumps(rec,ensure_ascii=False)+"\n")
                accepted+=1
            tally["accepted_reviews"]+=bool(accepted);tally["accepted_pairs"]+=accepted
        batch.clear()
    dest=ROOT/"评测改动关联.jsonl"
    tmp=dest.with_suffix(".part")
    with tmp.open("w",encoding="utf8") as out:
        for r in rows(reviews_path):
            app=r["appid"]; v=r["payload"]; day=v["timestamp_created"]//86400
            stats[app]["input"]+=1
            if (app,day) not in win:continue
            stats[app]["in_window"]+=1
            for pid,off in win[app,day]:
                queues[pid].append(dict(appid=app,review_id=r["item_id"],patch_id=pid,
                   language=v["language"],day_offset=off,created_ts=v["timestamp_created"],
                   updated_ts=v["timestamp_updated"],edited_after_window=v["timestamp_updated"]>=(day-off+15)*86400,
                   review=v["review"]))
                if len(queues[pid])>=256:flush(pid,out)
        for pid in queues:flush(pid,out)
    tmp.replace(dest)
    write_json(ROOT/"batch_counts.json",dict(input_file=reviews_path.name,by_game=stats,by_patch=by_patch))
    print(dict(stats),dict(by_patch),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["train","predict"])
    parser.add_argument("--reviews",type=Path,default=OUTPUTS/"steam_two_games_20261120/reviews.jsonl")
    args=parser.parse_args()
    if args.action=="train":train()
    else:infer(args.reviews)
