"""给每条全组评测选一个同游戏参照版本，并保留可追溯的模型候选和覆盖记录。"""
import argparse
import bisect
import collections
import concurrent.futures as futures
from datetime import datetime
import json
import os
import time

import joblib
import pyarrow as pa
import pyarrow.parquet as pq

from prepare_group import ROOT, DATA, CATALOG, OLD, digest, read_rows
from group_model import index_catalog, features, pm

PARTS = ROOT / '全量分批结果'
RUN_NAME = 'all_reviews_same_game_reference_v1'
LANGS = {'en', 'zh-Hans', 'zh-Hant'}
CHOICE = pa.schema([('appid',pa.int32()), ('review_id',pa.string()), ('patch_id',pa.string()),
    ('language_std',pa.string()), ('created_ts',pa.int64()), ('day_offset',pa.int32()),
    ('scope',pa.string()), ('candidate_count',pa.int8()), ('threshold_hits',pa.int8())])
PAIRS = pa.schema([('review_id',pa.string()), ('patch_id',pa.string()), ('item_id',pa.string()),
    ('rank',pa.int8()), ('score',pa.float64()), ('threshold',pa.float64()),
    ('span_start',pa.int32()), ('span_end',pa.int32()), ('above_threshold',pa.bool_())])
COLUMNS = ['appid','review_id','language_std','created_ts','review_text']

def save_json(path, value):
    tmp = path.with_suffix('.writing')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19: raise
            time.sleep(.05)

def init_worker():
    global model, indexes, versions, result_cache, source
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000)
    model = joblib.load(ROOT/'全组关联模型.joblib')
    assert model['variant'] == 'refit'
    indexes = index_catalog()
    versions = collections.defaultdict(list)
    for p in read_rows(CATALOG/'patches/patches.jsonl'):
        versions[p['appid']].append((p['release_day'],p['patch_id']))
    versions = {app: sorted(val) for app,val in versions.items()}
    result_cache = collections.OrderedDict()
    source = pq.ParquetFile(DATA)

def route(app, day, language):
    options = versions[app]
    # 相邻正式窗口互不重叠。优先窗口，以后选最近一次已上线版本，最早之前选首个作基线。
    i = bisect.bisect_right(options, (day+14,chr(0x10ffff))) - 1
    day0,pid = options[max(i,0)]
    delta = day-day0
    phase = 'window' if abs(delta)<=14 else 'later' if delta>14 else 'early'
    return pid,delta,phase+('_zh_en' if language in LANGS else '_other')

def score_unique(pid, rows):
    app = rows[0]['appid']
    estimator = model['old_estimator'] if app in OLD else model['estimator']
    threshold = model['old_threshold'] if app in OLD else model['threshold']
    unique = dict.fromkeys(r['review_text'] for r in rows)
    pending = []
    for value in unique:
        cache_key = (pid,value)
        if cache_key in result_cache:
            unique[value] = result_cache[cache_key]
            result_cache.move_to_end(cache_key)
        else:
            pending.append({'review': value})
    if pending:
        cs = features(indexes[pid],pending,'base' if app in OLD else model['variant'])
        for r,cands in pm.score(estimator,list(zip(pending,cs))):
            value = r['review']
            result = [(c[1],v,c[3],c[4]) for c,v in cands]
            unique[value] = result
            if len(value)<=600:
                result_cache[pid,value] = result
                if len(result_cache)>20000: result_cache.popitem(last=False)
    return unique,threshold

def checkpoint(unit, signature):
    out = PARTS / f'{unit:03d}'
    out.mkdir(exist_ok=True)
    paths = [out/'reviews.parquet',out/'candidates.parquet']
    marker = out/'complete.json'
    if marker.exists():
        saved = json.loads(marker.read_text('utf8'))
        if saved['signature']==signature and all(p.exists() for p in paths): return saved
        raise RuntimeError(f'分块 {unit} 与冻结批次不一致，请使用独立输出目录')
    temp = [p.with_suffix('.partial') for p in paths]
    total = collections.Counter(scanned=0,extra=0,pairs=0,hits=0)
    last = time.monotonic()
    with pq.ParquetWriter(temp[0],CHOICE,compression='zstd') as rw, pq.ParquetWriter(temp[1],PAIRS,compression='zstd') as cw:
        for data in source.iter_batches(batch_size=512,row_groups=[unit],columns=COLUMNS):
            by_patch = collections.defaultdict(list)
            for r in data.to_pylist():
                r['appid']=int(r['appid'])
                pid,delta,scope=route(r['appid'],r['created_ts']//86400,r['language_std'])
                total['scanned']+=1
                if scope=='window_zh_en': continue
                r.update(patch_id=pid,day_offset=delta,scope=scope)
                by_patch[pid].append(r)
            summaries=[];pairs=[]
            for pid,batch in by_patch.items():
                found,t=score_unique(pid,batch)
                for r in batch:
                    candidates=found[r.pop('review_text')]
                    hits=0
                    for rank,(item,s,a,b) in enumerate(candidates,1):
                        above=s>=t;hits+=above
                        pairs.append(dict(review_id=r['review_id'],patch_id=pid,item_id=item,rank=rank,
                            score=s,threshold=t,span_start=a,span_end=b,above_threshold=above))
                    r.update(candidate_count=len(candidates),threshold_hits=hits)
                    summaries.append(r);total['hits']+=hits
            if summaries: rw.write_table(pa.Table.from_pylist(summaries,schema=CHOICE))
            if pairs: cw.write_table(pa.Table.from_pylist(pairs,schema=PAIRS))
            total['extra']+=len(summaries);total['pairs']+=len(pairs)
            if time.monotonic()-last>=3:
                save_json(out/'live.json',dict(total));last=time.monotonic()
    for tmp,p in zip(temp,paths): os.replace(tmp,p)
    assert total['scanned']==source.metadata.row_group(unit).num_rows
    assert total['extra']==pq.ParquetFile(paths[0]).metadata.num_rows
    assert total['pairs']==pq.ParquetFile(paths[1]).metadata.num_rows
    result=dict(signature=signature,unit=unit,**total)
    save_json(marker,result)
    return result

def read_state(units):
    done={};live={}
    for u in range(units):
        p=PARTS/f'{u:03d}'
        for dest,f in [(done,p/'complete.json'),(live,p/'live.json')]:
            if f.is_file():
                try: dest[u]=json.loads(f.read_text('utf8'));break
                except (PermissionError,FileNotFoundError): pass
    return done,live

def run(workers):
    PARTS.mkdir(exist_ok=True)
    meta=pq.ParquetFile(DATA)
    frozen=json.loads((ROOT/'输入与抽样批次.json').read_text('utf8'))
    model_hash=digest(ROOT/'全组关联模型.joblib')
    assert model_hash==json.loads((ROOT/'开发组选型.json').read_text('utf8'))['model_sha256']
    assert digest(CATALOG/'items/items.jsonl')==frozen['items_sha256']
    assert digest(CATALOG/'patches/patches.jsonl')==frozen['patches_sha256']
    assert digest(DATA)==frozen['input_sha256']
    n=meta.metadata.num_rows;units=meta.num_row_groups
    window_stats=json.loads((ROOT/'window_批量计数.json').read_text('utf8'))
    assert window_stats['model_sha256']==model_hash
    window=window_stats['total']['input']
    sign='|'.join([RUN_NAME,model_hash,frozen['input_sha256'],frozen['patches_sha256']])
    done,stale=read_state(units)
    assert all(s['signature']==sign for s in done.values())
    for u in stale: save_json(PARTS/f'{u:03d}'/'live.json',{'extra':0,'pairs':0,'hits':0})
    initial=sum(s['extra'] for s in done.values())
    started=time.monotonic()
    def publish(status,message=''):
        ds,ls=read_state(units)
        extra=sum(v['extra'] for v in [*ds.values(),*ls.values()])
        elapsed=time.monotonic()-started
        eta=(n-window-extra)*elapsed/(extra-initial)/60 if extra>initial+1000 else None
        state=dict(status=status,total_reviews=n,done_reviews=window+extra,window_done=window,
            extra_done=extra,finished_parts=len(ds),parts=units,eta_minutes=round(eta,1) if eta is not None else None,
            updated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),message=message)
        save_json(ROOT/'full_progress.json',state)
        return ds
    publish('running')
    try:
        with futures.ProcessPoolExecutor(max_workers=workers,initializer=init_worker) as pool:
            jobs={pool.submit(checkpoint,u,sign) for u in range(units) if u not in done}
            while jobs:
                complete,jobs=futures.wait(jobs,timeout=4,return_when=futures.FIRST_COMPLETED)
                for job in complete: job.result()
                publish('running')
    except BaseException as e:
        publish('failed',f'{type(e).__name__}: {e}');raise
    try:
        ds=publish('checking','Checking row counts and source keys.')
        assert len(ds)==units and sum(v['extra'] for v in ds.values())+window==n
        from full_verify import verify_full
        verify_full()
    except Exception as e:
        publish('failed',f'{type(e).__name__}: {e}');raise
    publish('complete','All review IDs reconciled against the original cleaned table.')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--workers',type=int,default=3)
    opts=parser.parse_args()
    PARTS.mkdir(exist_ok=True)
    with (PARTS/'active.lock').open('a+b') as lock:
        if os.name=='nt':
            import msvcrt
            if not lock.tell():lock.write(b'0');lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(opts.workers)
