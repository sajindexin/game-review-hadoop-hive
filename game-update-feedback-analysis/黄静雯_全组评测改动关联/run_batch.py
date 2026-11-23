"""正式窗口全量打分；对窗口外的历史评测另做保守的长期提及检索。"""
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import json
import re
import time

import joblib
import pyarrow as pa
import pyarrow.parquet as pq

from group_model import index_catalog,features,pm
from prepare_group import ROOT,DATA,WINDOWS,CATALOG,OLD,connect,read_rows,write_rows,write_json,digest,records

def long_terms(items):
    # 只保留在同一款游戏中能唯一对应目录条目的明确名称。较短中文、
    # 单个英文单词及通用英语短语不作为长期提及的入口。
    ownership=defaultdict(set)
    options=defaultdict(set)
    for it in items:
        key=it['patch_id'],it['item_id']
        for lang,title in [('en','title_en'),('zh','title')]:
            for val in [it[title],*it['aliases_'+lang]]:
                alias=pm.normalize(val)
                if not alias:continue
                ownership[it['appid'],alias].add(key)
                strong=(len(re.findall('[\u3400-\u9fff]',alias))>=4 if lang=='zh'
                        else len(re.findall('[a-z]+',alias))>=2 and len(alias)>=9)
                if strong and alias not in {'new hairstyles','new hair styles','map expansion','larger map','bigger map','new hairstyle','recent items inventory','equipment loadouts','gear loadouts','save fov','new map'}:
                    options[key].add((it['appid'],lang,alias))
    terms=defaultdict(list)
    for key,vals in options.items():
        for app,lang,alias in sorted(vals):
            if len(ownership[app,alias])==1:
                term=re.escape(alias).replace(r'\ ',' ')
                # Rust regex (DuckDB) 和 Python 同用的边界语法。
                if lang=='en':term='(?:^|[^a-z])'+term+'(?:$|[^a-z])'
                terms[key].append(dict(text=alias,pattern=term))
    return terms

def extract_long(db,patches,terms):
    path=ROOT/'窗口外明确主题候选.parquet'
    db.read_parquet(str(DATA)).create_view('src')
    db.execute("""create temp table history (appid integer,patch_id varchar,review_id varchar,language varchar,
      language_std varchar,created_ts bigint,updated_ts bigint,voted_up boolean,day_offset bigint,
      edited_after_window boolean,review varchar)""")
    by_app=defaultdict(list)
    for p in patches:by_app[p['appid']].append(p['release_day'])
    for p in patches:
        pid=p['patch_id'];app=p['appid'];day=p['release_day']
        rules=[t['pattern'] for (k,_),lst in terms.items() if k==pid for t in lst]
        if not rules:continue
        exclude=' AND '.join('created_ts NOT BETWEEN ? AND ?' for _ in by_app[app])
        bounds=[v for d in by_app[app] for v in ((d-14)*86400,(d+15)*86400-1)]
        db.execute(f"""insert into history select appid::int,?,review_id,language,language_std,created_ts,updated_ts,
          voted_up, created_ts//86400-?, NULL::boolean, review_text
          from src where appid=? and language_std in ('en','zh-Hans','zh-Hant') and created_ts>=?
          and {exclude} and regexp_matches(lower(review_text),?)""",
          [pid,day,str(app),(day+15)*86400,*bounds,'|'.join(rules)])
    db.sql('select * from history order by patch_id,review_id').write_parquet(str(path),compression='zstd')
    return path,dict(input_by_language=records(db.execute('select language_std,count(*) n from src group by 1 order by 2 desc')),
                     shortlisted_reviews=db.execute('select count(distinct(appid,review_id)) from history').fetchone()[0],
                     shortlisted_review_version_pairs=db.execute('select count(*) from history').fetchone()[0])

SCHEMA=pa.schema([(c,pa.string()) for c in ('review_id','patch_id','item_id')]+[
 ('rank',pa.int8()),('score',pa.float64()),('threshold',pa.float64()),('span_start',pa.int32()),
 ('span_end',pa.int32()),('accepted',pa.bool_())])

def predict(path,mode,bundle,index,items,terms):
    since=time.perf_counter()
    tag='窗口内' if mode=='window' else '窗口外'
    parquet=ROOT/(tag+'模型候选打分.parquet')
    results=ROOT/(tag+'模型关联.jsonl')
    stats=defaultdict(Counter)
    oldindex=index_catalog(False) if bundle['variant']=='expanded' else index
    compiled={k:re.compile('|'.join(t['pattern'] for t in lst)) for k,lst in terms.items() if lst}
    def handle(batch,writer,log):
        pid=batch[0]['patch_id'];old=batch[0]['appid'] in OLD
        var,est,t=('base',bundle['old_estimator'],bundle['old_threshold']) if old else (bundle['variant'],bundle['estimator'],bundle['threshold'])
        cs=features((oldindex if old else index)[pid],batch,var)
        scored=pm.score(est,list(zip(batch,cs)))
        part=[]
        for r,cands in scored:
            n=stats[pid,r['language_std']];n['input']+=1;n['with_candidates']+=bool(cands)
            n['candidate_pairs']+=len(cands);accepted=0
            for rank,((_,item,_,a,b),s) in enumerate(cands,1):
                quote=r['review'][a:b];passed=s>=t
                if passed and mode=='history':
                    gate=compiled.get((pid,item))
                    passed=bool(gate and gate.search(pm.normalize(quote)))
                part.append(dict(review_id=r['review_id'],patch_id=pid,item_id=item,rank=rank,
                    score=s,threshold=t,span_start=a,span_end=b,accepted=passed))
                if passed:
                    it=items[pid,item];meta={k:v for k,v in r.items() if k!='review'}
                    meta.update(item_id=item,item_title=it['title'],source_url=it['source_url'],score=s,
                         threshold=t,model_id='hjw_patch_link_v1' if old else bundle['model_id'],evidence=quote,
                         span_start=a,span_end=b,text_field='review_text',scope=mode,verification='模型预测，待复核')
                    log.write(json.dumps(meta,ensure_ascii=False)+'\n');accepted+=1
            n['accepted_pairs']+=accepted;n['accepted_reviews']+=bool(accepted)
        if part:writer.write_table(pa.Table.from_pylist(part,schema=SCHEMA))
    temporary=parquet.with_suffix('.tmp');lines=results.with_suffix('.tmp')
    with pq.ParquetWriter(temporary,SCHEMA,compression='zstd') as writer, lines.open('w',encoding='utf8') as log:
        for chunk in pq.ParquetFile(path).iter_batches(batch_size=384):
            batches=defaultdict(list)
            for r in chunk.to_pylist():batches[r['patch_id']].append(r)
            for batch in batches.values():handle(batch,writer,log)
    temporary.replace(parquet);lines.replace(results)
    return dict(by_patch_language=[dict(patch_id=pid,language_std=lang,**v) for (pid,lang),v in sorted(stats.items())],
        total=dict(sum(stats.values(),Counter())),elapsed_seconds=time.perf_counter()-since)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['window','history']);mode=ap.parse_args().mode
    start=time.perf_counter()
    bundle=joblib.load(ROOT/'全组关联模型.joblib')
    freeze=json.loads((ROOT/'开发组选型.json').read_text('utf8'))
    assert digest(ROOT/'全组关联模型.joblib')==freeze['model_sha256']
    items=read_rows(CATALOG/'items/items.jsonl')
    assert digest(CATALOG/'items/items.jsonl')==bundle['catalog_sha256']
    index=index_catalog(bundle['variant']=='expanded')
    patches=read_rows(CATALOG/'patches/patches.jsonl')
    terms=long_terms(items)
    path=WINDOWS; extra={}
    if mode=='history':
        write_rows(ROOT/'长期检索词表.jsonl',[dict(patch_id=p,item_id=i,terms=v) for (p,i),v in sorted(terms.items())])
        path,extra=extract_long(connect(),patches,terms)
    stats=predict(path,mode,bundle,index,{(i['patch_id'],i['item_id']):i for i in items},terms)
    stats.update(extra);stats['total_wall_seconds']=time.perf_counter()-start;stats['model_sha256']=freeze['model_sha256']
    assert stats['total']['input']==pq.ParquetFile(path).metadata.num_rows
    write_json(ROOT/('window_批量计数.json' if mode=='window' else 'history_批量计数.json'),stats)
    print(mode,stats['total'],round(stats['total_wall_seconds'],2),flush=True)

if __name__=='__main__':
    main()
