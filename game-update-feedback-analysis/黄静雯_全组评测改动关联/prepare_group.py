"""固定发布批次、选择跨游戏验证集；抽样阶段不加载模型。"""
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from time import perf_counter

import duckdb

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent
DATA = OUT / '黄静雯_全组清洗数据验收/v1/cleaned_reviews.parquet'
CATALOG = OUT / '黄静雯_全组版本与改动'
LEGACY = OUT / '黄静雯_两款游戏关联模型'
WINDOWS = ROOT / '中英文窗口评测.parquet'
OLD = {252490, 413150}
HOLD = {2358720, 105600, 359550, 440}
FOCUS = {2358720, 1245620}
SEED = 'HJW-20261123-v1'

# 宽泛主题仅用于独立诊断抽样，不作为模型输入或自动标签。顺序对应正式目录。
TOPICS = {
 '105600_20200516': ['journey mode|journey difficulty|duplica|旅途模式|旅行模式|研究物品','master mode|master difficulty|大师|大師'],
 '105600_20220928': ['loadout|gear set|配装|配裝|装备栏|裝備欄','rubbl|rubble maker|碎石|瓦砾|瓦礫'],
 '1245620_20221207': ['colosse|colise|arena|斗技|鬥技|竞技场|競技場','hairstyle|hair style|发型|髮型'],
 '1245620_20240620': ['recent item|inventory tab|最新获得|最近获得|最近取得|新获得|新物品','horse|torrent|骑马|騎馬|灵马|靈馬'],
 '2358720_20240923': ['hundred.eye|hundred eye|百眼|百目|蜈蚣精|蜈蚣精','spikeshaft|mana bonus|狼牙棒'],
 '2358720_20241214': ['gauntlet|boss rush|rematch|再战|再戰|复战|復戰|连战|連戰|挑战模式','journeyer|地图|地圖|行旅图|行旅圖|navigation map|mini.?map'],
 '271590_20230613': ['overthrow|merryweather|颠覆|顛覆|梅利威瑟','lsa operation|los santos angels|洛圣都天使|洛聖都天使'],
 '271590_20240625': ['bounty|bounties|bail office|悬赏|懸賞|保释|保釋|赏金|賞金','dispatch|警车|警車|派遣'],
 '359550_20231206': ['zoto|canister|tubar|冰冻|冰凍|冷冻|冷凍','lair|巢穴'],
 '359550_20240312': ['death.?mark|deimos|戴莫斯|死亡标记|死亡標記','shield|盾牌|盾兵|持盾'],
 '4000_20240313': ['preset|loadout|预设|預設','duplicat|npc health|复制器|複製器'],
 '4000_20250326': ['treason|klaus veen|资源挂载|資源掛載','shader|着色器|著色器'],
 '440_20171020': ['mercenary park|mercenarypark|佣兵公园|傭兵公園','jetpack|thruster|喷气背包|噴射背包|推进器|推進器'],
 '440_20230712': ['selbyen|vsh|saxton hale|seal map|海豹|萨克斯顿|薩克斯頓','war paint|战绘|戰繪'],
 '550_20200924': ['lighthouse|last stand|灯塔|燈塔|最后一搏|最後一搏|最后一刻','pitchfork|shovel|草叉|铁铲|鐵鏟'],
 '550_20210708': ['intro|controller|gamepad|片头|片頭|手柄','fov|field of view|视野|視野'],
 '570_20230420': ['larger map|bigger map|new map|map size|地图|地圖','universal|全才|万能英雄|萬能英雄'],
 '570_20240523': ['innate|先天|天生技能','facet|命石'],
 '730_20240207': ['arms race|gun game|军备竞赛|軍備競賽|军备模式','sticker|贴纸|貼紙'],
 '730_20241002': ['armory|armoury|battle pass|通行证|通行證|军械库|军火库|軍械庫|武库','charm|keychain|挂件|掛件|挂饰|掛飾|吊坠|吊墜'],
}

def read_rows(path):
    with path.open(encoding='utf8') as f:
        return [json.loads(line) for line in f if line.strip()]

def write_rows(path, rows):
    with path.open('w',encoding='utf8') as f:
        for row in rows:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')

def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), 'utf8')

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def connect():
    local_temp=Path('E:/CodexData/temp')
    default=local_temp if local_temp.is_dir() else Path(tempfile.gettempdir())
    return duckdb.connect(config={'threads':2,'memory_limit':'700MB',
        'temp_directory':str(Path(os.environ.get('STEAM_QUERY_TEMP',default/'steam-group-model-query')))})

def records(cursor):
    cols=[d[0] for d in cursor.description]
    return [dict(zip(cols,row)) for row in cursor.fetchall()]

def main():
    started=perf_counter()
    db=connect()
    patches=read_rows(CATALOG/'patches/patches.jsonl')
    assert digest(DATA)=='6602f7bbf9983a58627a100f238e71483b5cf3e8deb19070fc761afb39702e08'
    db.read_parquet(str(DATA)).create_view('src')
    db.execute('create table versions(appid varchar,patch_id varchar,day0 bigint)')
    db.executemany('insert into versions values (?,?,?)',[(str(p['appid']),p['patch_id'],p['release_day']) for p in patches])
    db.execute("""create view matching as select p.appid::int appid,p.patch_id,r.review_id,
      r.language,r.language_std,r.created_ts,r.updated_ts,r.voted_up,
      r.created_ts//86400-p.day0 day_offset, coalesce(r.updated_ts>=(p.day0+15)*86400,false) edited_after_window,
      r.review_text review
      from src r join versions p on r.appid=p.appid and r.created_ts//86400 between p.day0-14 and p.day0+14""")
    totals=records(db.execute('select appid,patch_id,language_std,count(*) n from matching group by all order by 1,2,3'))
    db.sql("""select * from matching where language_std in ('en','zh-Hans','zh-Hant') order by patch_id,review_id""").write_parquet(str(WINDOWS),compression='zstd')
    db.read_parquet(str(WINDOWS)).create_view('win')
    db.execute("""create temp view eligible as select *,case when language_std='en' then 'en' else 'zh' end lang_group,
      case when day_offset<0 then 'before' when day_offset>0 then 'after' else 'release' end phase
      from win where appid not in (252490,413150)""")
    ordinary=records(db.execute("""select * exclude(rank_n) from (
      select *,row_number() over(partition by patch_id,phase,lang_group order by md5(?||review_id),review_id) rank_n
      from eligible) t where rank_n<=case when appid in (1245620,2358720) then 8 else 2 end""",[SEED]))
    ids={r['review_id'] for r in ordinary}
    for r in ordinary:r['sample_type']='stratified'
    targeted=[]
    for pid,terms in sorted(TOPICS.items()):
        for term in terms:
            pool=records(db.execute("""select * exclude(duplicate_text,rank_n) from (
              select *,row_number() over(partition by lang_group order by md5(?||review_id),review_id) rank_n
              from (select *,row_number() over(partition by lower(review) order by review_id) duplicate_text
               from eligible where patch_id=? and day_offset>=0 and regexp_matches(lower(review),?))
              where duplicate_text=1) where rank_n<=12 order by lang_group,rank_n""",[SEED+'-diag','steam_'+pid,term]))
            counts=Counter()
            for r in pool:
                g=r['lang_group']
                if counts[g]>=2 or r['review_id'] in ids:continue
                r['sample_type']='targeted';ids.add(r['review_id']);counts[g]+=1;targeted.append(r)
    samples=sorted(ordinary+targeted,key=lambda r:(r['appid'],r['patch_id'],r['sample_type'],r['review_id']))
    assert len({(r['appid'],r['review_id']) for r in samples})==len(samples)
    for group in ('development','sealed_test'):
        rs=[r for r in samples if (r['appid'] in HOLD)==(group=='sealed_test')]
        for i,r in enumerate(rs,1):
            r['split']=group;r['case']=('D' if group=='development' else 'T')+f'{i:03d}'
        write_rows(ROOT/(group+'_blind.jsonl'),rs)
    total=sum(r['n'] for r in totals)
    m=sum(r['n'] for r in totals if r['language_std'] in ['en','zh-Hans','zh-Hant'])
    assert total==324123 and m==db.execute('select count(distinct (appid,review_id)) from win').fetchone()[0]
    src_count=db.execute('select count(*) from src').fetchone()[0]
    write_json(ROOT/'输入与抽样批次.json',dict(source_rows=src_count,window_rows=total,eligible=m,
       out_of_window=src_count-total,other_languages_in_window=total-m,by_patch_language=totals,
       input_sha256=digest(DATA),patches_sha256=digest(CATALOG/'patches/patches.jsonl'),
       items_sha256=digest(CATALOG/'items/items.jsonl'),base_sha256=digest(LEGACY/'model.joblib'),
       sample_sha256={g:digest(ROOT/(g+'_blind.jsonl')) for g in ('development','sealed_test')},
       elapsed_seconds=round(perf_counter()-started,3),seed=SEED))
    print(total,m,Counter((r['split'],r['sample_type']) for r in samples))

if __name__=='__main__':
    main()
