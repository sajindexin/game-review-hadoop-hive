"""合并全组每条评测及候选，并用清洗明细、版本目录独立核对。"""
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
from time import perf_counter
import json
import os

import pyarrow.parquet as pq
from prepare_group import ROOT,DATA,WINDOWS,CATALOG,connect,records,read_rows,write_json,digest


REVIEW_FILE = '全量逐条覆盖.parquet'
CANDIDATE_FILE = '全量候选分数.parquet'
REPORT_FILE = '全量覆盖验收.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


@contextmanager
def publication_lock(parts):
    # OS releases the lock on close, including when the process exits.
    with (parts / '.verify-publish.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def publish_verified(staged, root):
    data_files = (REVIEW_FILE, CANDIDATE_FILE)
    previous = staged / 'previous'
    previous.mkdir()
    for name in data_files:
        destination = root / name
        if destination.exists():
            try:
                os.link(destination, previous / name)
            except OSError:
                copy2(destination, previous / name)

    replaced = []
    report_path = root / REPORT_FILE
    saved_report = previous / REPORT_FILE
    try:
        # A verified report only points to a complete set of matching files.
        if report_path.exists():
            report_path.replace(saved_report)
        for name in data_files:
            (staged / name).replace(root / name)
            replaced.append(name)
        (staged / REPORT_FILE).replace(report_path)
    except BaseException:
        for name in reversed(replaced):
            backup = previous / name
            if backup.exists():
                backup.replace(root / name)
            else:
                (root / name).unlink()
        if saved_report.exists():
            saved_report.replace(report_path)
        raise


def verify_outputs(db, staged, start):
    db.read_parquet(str(DATA)).create_view('dwd')
    db.read_parquet(str(WINDOWS)).create_view('window_input')
    db.read_parquet(str(ROOT/'窗口内模型候选打分.parquet')).create_view('window_model')
    parts=ROOT/'全量分批结果'
    manifests=[json.loads(p.read_text('utf8')) for p in sorted(parts.glob('*/complete.json'))]
    require(len(manifests)==pq.ParquetFile(DATA).num_row_groups, '全量分块数量与源 Parquet 行组数不一致')
    folders=[parts/f"{v['unit']:03d}" for v in manifests]
    db.read_parquet([str(p/'reviews.parquet') for p in folders]).create_view('extra')
    db.read_parquet([str(p/'candidates.parquet') for p in folders]).create_view('extra_pairs')
    db.execute("""create temp view window_counts as select review_id,patch_id,count(*) k,
                count_if(accepted) h from window_model group by 1,2""")
    db.execute("""create temp view base_rows as select w.appid,w.review_id,w.patch_id,w.language_std,
          w.created_ts,w.day_offset::int day_offset,'window_zh_en' scope,
          coalesce(c.k,0)::tinyint candidate_count,coalesce(c.h,0)::tinyint threshold_hits
          from window_input w left join window_counts c using (review_id,patch_id)""")
    def export_staged(query, name, view):
        path = staged / name
        db.sql(query).write_parquet(str(path), compression='zstd')
        db.read_parquet(str(path)).create_view(view)
        return path
    reviews=export_staged('select * from base_rows union all by name select * from extra',
              REVIEW_FILE,'coverage')
    candidates=export_staged("""select review_id,patch_id,item_id,rank,score,threshold,
              span_start,span_end,accepted as above_threshold from window_model
              union all by name select * from extra_pairs""",CANDIDATE_FILE,'pairs')
    patches=read_rows(CATALOG/'patches/patches.jsonl')
    db.execute('create temp table versions(appid integer,patch_id varchar,day0 bigint)')
    db.executemany('insert into versions values (?,?,?)',[(p['appid'],p['patch_id'],p['release_day']) for p in patches])
    items=read_rows(CATALOG/'items/items.jsonl')
    db.execute('create temp table items(patch_id varchar,item_id varchar)')
    db.executemany('insert into items values (?,?)',[(p['patch_id'],p['item_id']) for p in items])
    # 按日期现算参照版，独立于生产程序的二分搜索写法。
    db.execute("""create temp table calendar as with d as
         (select distinct appid::int appid, created_ts//86400 as review_day from dwd)
         select d.*,v.patch_id,review_day-day0 as delta from d join versions v using(appid)
         qualify row_number() over(partition by appid,review_day order by
            case when abs(review_day-day0)<=14 then 0 when review_day>=day0 then 1 else 2 end,
            abs(review_day-day0),patch_id)=1""")
    def scalar(sql):return db.execute(sql).fetchone()[0]
    n=scalar('select count(*) from dwd')
    require(pq.ParquetFile(reviews).metadata.num_rows==n, '覆盖表的评测数与源明细不一致')
    require(scalar('select count(*) from (select appid,review_id from coverage group by 1,2 having count(*)<>1)')==0, '覆盖表存在重复评测主键')
    require(scalar("""select count(*) from coverage c left join
       (select appid::int appid,review_id,language_std,created_ts from dwd) d using(appid,review_id)
       left join calendar a on a.appid=d.appid and a.review_day=d.created_ts//86400
       where d.review_id is null or c.created_ts is distinct from d.created_ts
       or c.language_std is distinct from d.language_std or c.patch_id is distinct from a.patch_id
       or c.day_offset is distinct from a.delta or c.scope is distinct from
       (case when abs(a.delta)<=14 then 'window' when a.delta>14 then 'later' else 'early' end ||
        case when d.language_std in ('en','zh-Hans','zh-Hant') then '_zh_en' else '_other' end)""")==0, '评测主键、发布时间、语种或参照版本对账失败')
    require(scalar("""select count(*) from pairs p left join items i using(patch_id,item_id)
       where i.item_id is null or score is null or not isfinite(score) or score not between 0 and 1
       or rank not between 1 and 5 or span_start<0 or span_end<=span_start
       or above_threshold is distinct from (score>=threshold)""")==0, '候选分数、阈值、改动、排名或原文区间异常')
    # 独立核对候选唯一性、排名、原文字数、每一条评测（含零候选）的命中数。
    db.execute("""create temp table pair_counts as select review_id,patch_id,count(*) k,
       count_if(above_threshold) h,max(span_end) max_end, count(distinct item_id) n_items,
       count(distinct rank) n_ranks,max(rank) last_rank from pairs group by 1,2""")
    require(scalar('select count(*) from pair_counts where k<>n_items or k<>n_ranks or k<>last_rank')==0, '候选条目或候选排名重复或不连续')
    require(scalar("""select count(*) from coverage c full join pair_counts p using(review_id,patch_id)
       where c.review_id is null or candidate_count<>coalesce(k,0) or threshold_hits<>coalesce(h,0)""")==0, '候选数或过阈值数对账失败')
    require(scalar("""select count(*) from pair_counts p join coverage c using(review_id,patch_id)
       join (select appid::int appid,review_id,review_len from dwd) d using(appid,review_id)
       where p.max_end>d.review_len""")==0, '候选引文区间超过评测正文长度')
    by_scope=records(db.execute("""select scope,count(*) reviews,count_if(candidate_count>0) with_candidates,
        sum(candidate_count) candidate_pairs,count_if(threshold_hits>0) model_hit_reviews,
        sum(threshold_hits) above_threshold_pairs from coverage group by scope order by scope"""))
    by_app=records(db.execute("""select appid,scope,count(*) reviews,sum(candidate_count) candidate_pairs,
        sum(threshold_hits) above_threshold_pairs from coverage group by 1,2 order by 1,2"""))
    totals=dict(sum((Counter({k:r[k] for k in ('reviews','with_candidates','candidate_pairs',
                    'model_hit_reviews','above_threshold_pairs')}) for r in by_scope),Counter()))
    require(totals['reviews']==n and totals['candidate_pairs']==pq.ParquetFile(candidates).metadata.num_rows, '汇总后的评测或候选数量与 Parquet 不一致')
    require(sum(r['reviews'] for r in by_scope if r['scope'].startswith('window'))==324123, '全语言版本窗口评测数与 Hive 不一致')
    source_sha=digest(DATA)
    require(source_sha==json.loads((ROOT/'输入与抽样批次.json').read_text('utf8'))['input_sha256'], '源 Parquet 的 SHA-256 与输入清单不一致')
    result=dict(status='verified',totals=totals,by_scope=by_scope,by_app_scope=by_app,
        zero_candidate_reviews=n-totals['with_candidates'],parts=len(manifests),source_sha256=source_sha,
        model_sha256=digest(ROOT/'全组关联模型.joblib'),reviews_sha256=digest(reviews),
        candidates_sha256=digest(candidates),external_test_languages=['en','zh-Hans','zh-Hant'],
        verification_seconds=perf_counter()-start,verified_at_utc=datetime.now(timezone.utc).isoformat(),
        checks=['全组主键唯一且与原始清洗表逐键对齐','参照版本以独立日历复算','阈值、条目、排名及原文区间有效',
                '每条评测的候选数及过阈值数对账','全语言版本窗口条数与 Hive 一致'])
    return result


def verify_full():
    start = perf_counter()
    parts = ROOT / '全量分批结果'
    with TemporaryDirectory(prefix='.full-verify-', dir=ROOT) as temp:
        staged = Path(temp)
        with closing(connect()) as db:
            result = verify_outputs(db, staged, start)
        write_json(staged / REPORT_FILE, result)
        with publication_lock(parts):
            publish_verified(staged, ROOT)
    print(json.dumps(result['totals'], ensure_ascii=False), flush=True)
    return result


if __name__=='__main__':
    verify_full()
