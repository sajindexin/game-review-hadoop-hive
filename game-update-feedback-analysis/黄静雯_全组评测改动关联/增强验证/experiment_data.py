"""根据已冻结的版本目录取样；此程序不导入或调用语义模型。"""
from collections import Counter
from datetime import date,timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

HERE=Path(__file__).resolve().parent
BASE=HERE.parent
sys.path.insert(0,str(BASE))
from prepare_group import DATA,CATALOG,OUT,connect,read_rows,write_rows,write_json,records,digest
SEED='hjw-independent-patch-audit-20261123'
SPEC=HERE/'新版本目录.json'

def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC',text).casefold().split())

def new_catalog():
    patches=json.loads(SPEC.read_text('utf8'));items=[]
    for p in patches:
        for code,zh,en,desc,az,ae,narrow,wide in p['items']:
            items.append(dict(appid=p['appid'],patch_id=p['patch_id'],item_id=code,title=zh,
                title_en=en,description=desc,aliases_zh=az.split('|'),aliases_en=ae.split('|'),source_url=p['url']))
    return patches,items

def previous_labels():
    paths=[BASE/'development_AI标注.jsonl',BASE/'sealed_test_AI标注.jsonl',
        BASE/'W_复核工作单.jsonl',BASE/'H_复核工作单.jsonl',OUT/'黄静雯_两款游戏关联模型/已复核可引用关联.jsonl',
        OUT/'黄静雯_评测标注/AI逐条复核250条.jsonl',OUT/'黄静雯_评测标注/留出版本定向补样12条.jsonl']
    return {str(r['review_id']) for f in paths for r in read_rows(f)}

def official_snapshots(patches):
    import requests
    for p in patches:
        out=HERE/('官方公告_'+str(p['appid'])+'.json')
        if out.exists():
            assert json.loads(out.read_text('utf8'))['gid']==p['gid'];continue
        day=(date.fromisoformat(p['date'])-date(1970,1,1)).days
        res=requests.get('https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/',params=
            dict(appid=p['appid'],count=8,maxlength=0,enddate=(day+1)*86400,feeds='steam_community_announcements'),timeout=(8,18))
        res.raise_for_status()
        news=next(t for t in res.json()['appnews']['newsitems'] if str(t['gid'])==p['gid'])
        assert news['date']//86400==day and news['contents']
        write_json(out,news)

def sample():
    patches,items=new_catalog();official_snapshots(patches)
    db=connect();db.read_parquet(str(DATA)).create_view('src');previous=previous_labels()
    seen={normalize(t[0]) for t in db.execute('select review_text from src where review_id in (select unnest(?))',[sorted(previous)]).fetchall()}
    data={};selected=[];counts={}
    for p in patches:
        day=(date.fromisoformat(p['date'])-date(1970,1,1)).days
        rows=records(db.execute("""select appid::int appid,review_id,language_std,created_ts,updated_ts,
               created_ts//86400-? as day_offset,review_text as review from src
               where appid=? and created_ts between ? and ? and language_std in ('en','zh-Hans','zh-Hant')
               order by md5(?||review_id),review_id""",[day,str(p['appid']),(day-14)*86400,(day+181)*86400-1,SEED]))
        for r in rows:r.update(patch_id=p['patch_id'],lang='en' if r['language_std']=='en' else 'zh')
        data[p['patch_id']]=rows
        counts[p['patch_id']]=dict(in_window=sum(abs(r['day_offset'])<=14 for r in rows),later=sum(r['day_offset']>14 for r in rows))
    def pick(pool,n,group):
        took=0
        for r in pool:
            identity=normalize(r['review'])
            if not identity or identity in seen or r['review_id'] in previous:continue
            seen.add(identity)
            selected.append(dict(r,sample_type=group));took+=1
            if took==n:break
    for p in patches:
        rows=data[p['patch_id']]
        for lang in ('en','zh'):
            for lo,hi in [(-14,-1),(0,0),(1,14)]:
                pick((r for r in rows if r['lang']==lang and lo<=r['day_offset']<=hi),4,'ordinary')
        for group,i,n in [('topic_specific',6,5),('topic_broad',7,2),('long_term',6,3)]:
            for it in p['items']:
                matcher=re.compile(it[i],re.IGNORECASE)
                for lang in ('en','zh'):
                    pick((r for r in rows if r['lang']==lang and (r['day_offset']>14)==(group=='long_term')
                          and matcher.search(r['review'])),n,group)
    # 打乱普通组与定向组的展示顺序。盲标工作单隐藏抽取原因和所有模型输出。
    selected.sort(key=lambda r:hashlib.sha256(('label-blind/'+r['review_id']).encode()).hexdigest())
    for i,r in enumerate(selected,1):r['case']=f'N{i:03d}'
    write_rows(HERE/'新版本抽样.jsonl',selected)
    write_rows(HERE/'新版本盲标工作单.jsonl',[{k:r[k] for k in ('case','appid','patch_id','review_id','language_std','review')} for r in selected])
    write_json(HERE/'独立抽样登记.json',dict(seed=SEED,catalog_sha256=digest(SPEC),sample_sha256=digest(HERE/'新版本抽样.jsonl'),
        prev_ids=len(previous),available=counts,selected=dict(Counter(r['sample_type'] for r in selected))))
    print(len(selected),counts,Counter(r['sample_type'] for r in selected))

if __name__=='__main__':
    sample()
