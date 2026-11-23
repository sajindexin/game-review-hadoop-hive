"""训练比较、冻结模型并在整款留出游戏上一次评估。借用个人阶段的特征计算。"""
import argparse
from collections import Counter
from copy import deepcopy
import json
import sys
from time import perf_counter

import joblib
import numpy as np

from prepare_group import ROOT,OUT,LEGACY,CATALOG,OLD,HOLD,read_rows,write_rows,write_json,digest
sys.path.insert(0,str(LEGACY))
import patch_model as pm

def index_catalog(expand=False):
    items=deepcopy(read_rows(CATALOG/'items/items.jsonl'))
    terms={r['item_id']:r for r in read_rows(ROOT/'候选词扩展.jsonl')} if expand else {}
    for r in items:
        if r['item_id'] in terms:
            for lang in ('zh','en'):
                r['aliases_'+lang]+=terms[r['item_id']][lang]
    return {p:pm.CatalogIndex([i for i in items if i['patch_id']==p])
            for p in sorted({i['patch_id'] for i in items})}

def features(index, batch, variant):
    raw=index.candidates(batch)
    if variant!='expanded':return raw
    return [[(rank,item,[*xs,float(xs[2]>0)],a,b) for rank,item,xs,a,b in cs] for cs in raw]

def examples(records, indices, variant):
    return [(r,features(indices[r['patch_id']],[r],variant)[0]) for r in records if r['annotation_label']!='待判']

def metrics(scored,threshold):
    m=pm.metrics(scored,threshold)
    m['false_positive_reviews']=sum(bool({c[1] for c,s in cs if s>=threshold}-set(r['item_ids'])) for r,cs in scored)
    return m

def report(scored,t):
    return {group:metrics([(r,cs) for r,cs in scored if group=='all' or r['sample_type']==group],t)
            for group in ('all','stratified','targeted')}

def judgments(scored,t):
    rows=[]
    for r,cs in scored:
        proposed=[c[1] for c,_ in cs];accepted=[c[1] for c,s in cs if s>=t]
        rows.append(dict(case=r['case'],appid=r['appid'],review_id=r['review_id'],patch_id=r['patch_id'],
            language=r['language_std'],sample_type=r['sample_type'],gold=r['item_ids'],retrieved=proposed,
            predicted=accepted,scores={c[1]:v for c,v in cs},missed=sorted(set(r['item_ids'])-set(accepted)),
            spurious=sorted(set(accepted)-set(r['item_ids']))))
    return rows

def train():
    started=perf_counter()
    source=read_rows(OUT/'黄静雯_评测标注/AI逐条复核250条.jsonl')
    source=[r for r in source if r['split']=='development' and r['annotation_label']!='待判']
    dev=read_rows(ROOT/'development_AI标注.jsonl')
    assert all(r['appid'] not in HOLD|OLD for r in dev)
    assert all(r['item_id'].startswith(str(r['appid'])) for r in read_rows(CATALOG/'items/items.jsonl') if r['appid'] not in OLD)
    base=joblib.load(LEGACY/'model.joblib')
    assert base['catalog_sha256']==digest(OUT/'黄静雯_两款游戏版本与改动/items/items.jsonl')
    idx=index_catalog(); ref=examples(dev,idx,'base')
    original=pm.score(base['estimator'],ref);old_cut=base['threshold']
    stats={'base':report(original,old_cut)}
    cuts={'base':old_cut};fits={'base':base['estimator']};times={};traces={'base':judgments(original,old_cut)}
    fit_counts={}; groups=sorted({r['appid'] for r in dev})
    for variant in ('refit','expanded'):
        elapsed=perf_counter()
        expanded=variant=='expanded';use=index_catalog(expanded) if expanded else idx
        seed=examples(source,use,variant)
        new=examples(dev,use,variant)
        # 对留一本轮开发游戏，保守地屏蔽该游戏的补充词形；独立测试使用冻结词表。
        valdata=examples(dev,idx,variant) if expanded else new
        scored=[];fit_seconds=0
        for app in groups:
            val=[z for z in valdata if z[0]['appid']==app]
            txt={pm.normalize(r['review']) for r,_ in val}
            tr=[z for z in seed+new if z[0]['appid']!=app and pm.normalize(z[0]['review']) not in txt]
            start=perf_counter();m=pm.fit('logistic',tr);fit_seconds+=perf_counter()-start
            scored.extend(pm.score(m,val))
        candidates=[round(i/100,2) for i in range(50,100,2)]+[.95,.99]
        allowed=[t for t in candidates if report(scored,t)['stratified']['fp']<=stats['base']['stratified']['fp']+1]
        t=max(allowed or [.999999],key=lambda x:(metrics(scored,x)['f05'] or 0,metrics(scored,x)['recall'] or 0,x))
        stats[variant]=report(scored,t);cuts[variant]=t;traces[variant]=judgments(scored,t)
        start=perf_counter();fits[variant]=pm.fit('logistic',seed+new);final_secs=perf_counter()-start
        times[variant]=dict(cross_validation_fits_s=fit_seconds,final_fit_s=final_secs,feature_and_selection_s=perf_counter()-elapsed)
        fit_counts[variant]={'usable_reviews':len(seed+new),'candidate_pairs':sum(len(cs) for _,cs in seed+new),
            'positive_candidate_pairs':sum(c[1] in r['item_ids'] for r,cs in seed+new for c in cs)}
    eligible=[k for k in ('refit','expanded') if stats[k]['all']['f05']>=stats['base']['all']['f05']+.02
              and stats[k]['stratified']['fp']<=stats['base']['stratified']['fp']+1]
    selected=max(eligible,key=lambda k:(stats[k]['all']['f05'],-len(k))) if eligible else 'base'
    bundle=dict(variant=selected,estimator=fits[selected],threshold=cuts[selected],
        old_estimator=base['estimator'],old_threshold=old_cut,old_apps=sorted(OLD),features=list(pm.FEATURES)+(['any_exact_alias'] if selected=='expanded' else []),
        model_id='hjw_group_patch_v1',base_sha256=digest(LEGACY/'model.joblib'),
        catalog_sha256=digest(CATALOG/'items/items.jsonl'),vocabulary_sha256=digest(ROOT/'候选词扩展.jsonl'),
        dev_labels_sha256=digest(ROOT/'development_AI标注.jsonl'))
    joblib.dump(bundle,ROOT/'全组关联模型.joblib')
    write_json(ROOT/'开发组选型.json',dict(selected=selected,thresholds=cuts,reports=stats,training_counts=fit_counts,
         per_variant_seconds=times,total_seconds=perf_counter()-started,model_sha256=digest(ROOT/'全组关联模型.joblib'),
         development_games=groups,held_out_games=sorted(HOLD),ordinary_and_targeted_pooled_for_selection=True))
    for k,v in traces.items():write_rows(ROOT/('开发逐条_'+k+'.jsonl'),v)
    print(selected,cuts,json.dumps(stats,ensure_ascii=False),times,fit_counts)

def evaluate():
    started=perf_counter();bundle=joblib.load(ROOT/'全组关联模型.joblib')
    frozen=json.loads((ROOT/'开发组选型.json').read_text('utf8'))
    assert digest(ROOT/'全组关联模型.joblib')==frozen['model_sha256']
    test=read_rows(ROOT/'sealed_test_AI标注.jsonl')
    assert {r['appid'] for r in test}==HOLD
    training=read_rows(ROOT/'development_AI标注.jsonl')+[r for r in read_rows(OUT/'黄静雯_评测标注/AI逐条复核250条.jsonl') if r['split']=='development']
    train_texts={pm.normalize(r['review']) for r in training if r['annotation_label']!='待判'}
    assert not {r['review_id'] for r in training}&{r['review_id'] for r in test}
    results={}
    for name,v,m,t in [('base','base',bundle['old_estimator'],bundle['old_threshold']),
                       ('selected',bundle['variant'],bundle['estimator'],bundle['threshold'])]:
        ex=examples(test,index_catalog(v=='expanded'),v)
        scored=pm.score(m,ex)
        results[name]=dict(overall=report(scored,t),
             by_game={str(g):report([z for z in scored if z[0]['appid']==g],t) for g in sorted(HOLD)},
             by_language={g:report([z for z in scored if z[0]['language_std']==g],t) for g in sorted({r['language_std'] for r in test})},
             novel_text_only=report([z for z in scored if pm.normalize(z[0]['review']) not in train_texts],t))
        write_rows(ROOT/('盲测逐条_'+name+'.jsonl'),judgments(scored,t))
    write_json(ROOT/'跨游戏独立评估.json',dict(model_sha256=frozen['model_sha256'],split='4 whole games held out',
          source='AI独立标注',n_total=len(test),uncertain=sum(r['annotation_label']=='待判' for r in test),
          results=results,evaluation_seconds=perf_counter()-started))
    print(json.dumps({k:v['overall'] for k,v in results.items()},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['train','evaluate'])
    action=parser.parse_args().action
    train() if action=='train' else evaluate()
