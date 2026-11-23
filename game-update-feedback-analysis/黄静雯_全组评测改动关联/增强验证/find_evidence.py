"""给 Agent 和看板的本地只读入口：限定游戏、版本，查已逐条核对的引文。"""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import time
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from experiment_data import HERE,BASE,CATALOG,normalize,read_rows,write_json,digest
from semantic_features import Encoder
from get_encoder import DEFAULT_DIR,FILES

APPROVED=BASE/'窗口内已复核可引用关联.jsonl'
ITEMS=CATALOG/'items/items.jsonl'
VECTORS=HERE/'引用检索向量.npz'
META=HERE/'引用检索索引.json'
OUTSIDE_TOPICS=[
    'Steam 商店购买游戏的付款、是否打折、退款流程。','Steam payment, sales, discounts, and how to request a purchase refund.',
    '忘记密码、游戏账号安全与联系客服。','Account recovery, reset a password, contacting the support desk.',
    '本地电脑的安装、磁盘空间和驱动程序故障排查。','Computer setup, disk space, installing graphics drivers and troubleshooting hardware.',
    '当地天气预报、下雨、交通和旅游。','Local weather forecasts, travel, and public transport.',
    '上课考试安排、学校作业或饮食与健康。','School exam schedules, nutrition and personal health.']

def load_encoder():
    local=HERE/'编码器'
    return Encoder(Path(os.environ.get('STEAM_E5_DIR',local if local.is_dir() else DEFAULT_DIR)))

def texts(item):
    return [item['title']+'。'+item['description']+' '+' '.join(item['aliases_zh']),
            item['title_en']+'. '+' '.join(item['aliases_en'])]

def build():
    started=time.perf_counter()
    its=read_rows(ITEMS);refs=read_rows(APPROVED)
    enc=load_encoder()
    docs=['passage: '+s for it in its for s in texts(it)]
    # 评测引文切成模型支持的长度。一个引文可有多段，检索按最高段分排序，返回原始已复核引文。
    snippets=[[r['evidence'][a:b] for a,b in enc.spans(r['evidence'])] or [r['evidence']] for r in refs]
    ev=['passage: '+s for slices in snippets for s in slices]
    outside=['passage: '+s for s in OUTSIDE_TOPICS]
    embed=enc.encode(docs+ev+outside)
    with VECTORS.open('wb') as f:
        np.savez_compressed(f,topics=np.stack([embed[s] for s in docs]).reshape(len(its),2,-1),
            evidence=np.stack([embed[s] for s in ev]),offsets=np.cumsum([0]+[len(x) for x in snippets]),
            outside=np.stack([embed[s] for s in outside]))
    write_json(META,dict(catalog_sha256=digest(ITEMS),approved_sha256=digest(APPROVED),
        vector_sha256=digest(VECTORS),weights=FILES,topics=len(its),evidence=len(refs),seconds=time.perf_counter()-started))

class EvidenceSearch:
    def __init__(self):
        meta=json.loads(META.read_text('utf8'))
        if any(digest(p)!=meta[k] for p,k in [(ITEMS,'catalog_sha256'),(APPROVED,'approved_sha256'),(VECTORS,'vector_sha256')]):
            raise ValueError('输入批次与向量索引不一致，请重新 build')
        self.items=read_rows(ITEMS);self.refs=read_rows(APPROVED)
        self.by_patch=defaultdict(list);self.by_item=defaultdict(list)
        for i,it in enumerate(self.items):self.by_patch[it['appid'],it['patch_id']].append(i)
        for i,r in enumerate(self.refs):self.by_item[r['patch_id'],r['item_id']].append(i)
        with np.load(VECTORS,allow_pickle=False) as v:
            self.topic_vectors=v['topics'];self.evidence_vectors=v['evidence'];self.offsets=v['offsets'];self.outside=v['outside']
        self.encoder=load_encoder()
        self.vec=TfidfVectorizer(analyzer='char',ngram_range=(2,4),dtype=np.float32,sublinear_tf=True)
        self.topic_tfidf=self.vec.fit_transform([normalize(s) for i in self.items for s in texts(i)])

    def topic_rank(self,appid,patch,query,method,qvec=None):
        inds=self.by_patch.get((appid,patch),[])
        if not inds:return []
        if qvec is None:qvec=self.encoder.encode(['query: '+query])['query: '+query]
        ds=(self.topic_vectors[inds]@qvec).max(axis=1)
        lx=(self.topic_tfidf@self.vec.transform([normalize(query)]).T).toarray().reshape(-1,2).max(1)[inds]
        # 两种得分量纲不同。按名次融合，相同分数同名次。没有任何词面重合时只使用语义。
        def ranks(values):
            return np.array([1+np.sum(values>v) for v in values],dtype=float)
        if method=='lexical':fused=lx
        elif method=='semantic':fused=ds
        elif method=='hybrid':
            fused=2/(20+ranks(ds))
            if lx.max()>0:fused+=1/(20+ranks(lx))
        else:raise ValueError(method)
        order=sorted(range(len(inds)),key=lambda i:(-float(fused[i]),self.items[inds[i]]['item_id']))
        return [self.items[inds[i]]['item_id'] for i in order]

    def lookup(self,appid,patch,query='',item=None,phase='all',language='all',limit=3):
        eligible=self.by_patch.get((appid,patch),[])
        if not eligible:return dict(status='unknown_game_or_patch',quotes=[])
        valid={self.items[i]['item_id']:self.items[i] for i in eligible}
        if item and item not in valid:return dict(status='unknown_item',quotes=[])
        query=query.strip()
        if not query and not item:return dict(status='query_or_item_required',quotes=[])
        if phase not in {'all','before','release_day','after'} or language not in {'all','en','zh','zh-Hans','zh-Hant'}:
            return dict(status='unknown_filter',quotes=[])
        if len(query)>2048 or (query and len(self.encoder.tokenizer.encode(query,add_special_tokens=False).ids)>450):
            return dict(status='please_shorten_query',quotes=[])
        embeds=self.encoder.encode(['query: '+query])['query: '+query] if query else None
        if not item and float((self.outside@embeds).max())>float((self.topic_vectors[eligible]@embeds).max()):
            return dict(status='outside_catalog',quotes=[])
        ranked=self.topic_rank(appid,patch,query,'hybrid',embeds) if not item else [item]
        selected=ranked[0];it=valid[selected]
        filtered=[]
        for i in self.by_item.get((patch,selected),[]):
            r=self.refs[i]
            if language!='all' and (language!='zh' and r['language_std']!=language or language=='zh' and not r['language_std'].startswith('zh')):continue
            if phase!='all' and (r['phase']!=phase or r['edited_after_window']):continue
            if phase=='before' and r['before_review_edited_after_release']:continue
            if phase=='release_day' and r['updated_ts'] is not None and r['updated_ts']//86400>r['created_ts']//86400:continue
            score=float((self.evidence_vectors[self.offsets[i]:self.offsets[i+1]]@embeds).max()) if query else 0
            filtered.append((score,i))
        filtered.sort(key=lambda s:(-s[0],self.refs[s[1]]['review_id']))
        fields=['appid','patch_id','item_id','item_title','review_id','language_std','phase','day_offset',
            'created_ts','updated_ts','edited_after_window','before_review_edited_after_release','evidence',
            'span_start','span_end','source_url','verification']
        quotes=[{k:self.refs[i][k] for k in fields} for _,i in filtered[:max(1,min(10,limit))]]
        return dict(status=('verified_item_quotes' if item else 'suggested_topic_with_verified_quotes') if quotes else 'no_verified_quotes',
            topic_selection='explicit_item' if item else 'suggested_topic',topic=selected,title=it['title'],
            other_topic_suggestions=ranked[1:3],available=len(filtered),quotes=quotes)

def acceptance():
    s=EvidenceSearch();cases=read_rows(HERE/'证据查询验收题.jsonl')
    app_for={i['patch_id']:i['appid'] for i in s.items};results=[]
    methods=['lexical','semantic','hybrid']
    vectors=s.encoder.encode(['query: '+c['query'] for c in cases])
    for c in cases:
        p=c['patch_id'];app=app_for[p]
        assert len({s.items[i]['item_id'] for i in s.by_patch[app,p] if s.by_item.get((p,s.items[i]['item_id']))})>=2
        ranks={k:s.topic_rank(app,p,c['query'],k,vectors['query: '+c['query']]).index(c['item_id'])+1 for k in methods}
        got=s.lookup(app,p,c['query'])
        if got['quotes']:assert all(r['item_id']==got['topic'] for r in got['quotes'])
        results.append(dict(c,ranks=ranks,first_item=got.get('topic'),status=got['status'],retrieved_ids=[r['review_id'] for r in got['quotes']]))
    if s.lookup(440,'steam_440_20171020',item='440_20171020_02')['status']!='no_verified_quotes':raise AssertionError('无引文状态')
    assert s.lookup(730,'sdv_20201221_15',item='S15_05')['status']=='unknown_game_or_patch'
    assert s.lookup(413150,'sdv_20201221_15',item='D02')['status']=='unknown_item'
    for app,patch,item in [(413150,'sdv_20201221_15','S15_05'),(2358720,'steam_2358720_20241214','2358720_20241214_02')]:
        expected={r['review_id'] for r in s.refs if r['appid']==app and r['patch_id']==patch and r['item_id']==item
                  and 1<=r['day_offset']<=14 and r['language_std'].startswith('zh') and r['edited_after_window'] is False}
        filtered=s.lookup(app,patch,item=item,phase='after',language='zh',limit=10)
        assert filtered['available']==len(expected) and len(filtered['quotes'])==min(10,len(expected))
        assert all(r['review_id'] in expected for r in filtered['quotes'])
    counts={k:dict(hit_at_1=sum(r['ranks'][k]==1 for r in results),mrr=float(np.mean([1/r['ranks'][k] for r in results]))) for k in methods}
    negatives=['明天出门要不要带伞？','买了以后想把钱退回来，该找谁？','I forgot my login details, how can I get back into my account?',
        '这个周末坐哪趟公交去机场？','When is this game next going to be fifty percent off?','我的 C 盘装不下了，怎么挪安装位置？']
    negative_results=[dict(query=q,status=s.lookup(413150,'sdv_20201221_15',q)['status']) for q in negatives]
    write_json(HERE/'证据检索场景验收.json',dict(query_set_sha256=digest(HERE/'证据查询验收题.jsonl'),queries=len(results),
        methods=counts,unit='在给定版本的所有官方条目中，问题的预期条目排第几',source='预先编写的课程演示问题',
        returned_expected_topic_and_quote=sum(bool(r['retrieved_ids']) and r['first_item']==r['item_id'] for r in results),
        outside_controls=negative_results,filters_verified=['空证据','跨游戏版本','跨版本条目','语种和更新后时间'],cases=results))
    print(counts,[(r['case'],r['status']) for r in results if not r['retrieved_ids']],negative_results)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','acceptance','lookup'])
    p.add_argument('--appid',type=int);p.add_argument('--patch');p.add_argument('--query',default='');p.add_argument('--item')
    p.add_argument('--phase',choices=['all','before','release_day','after'],default='all')
    p.add_argument('--language',choices=['all','zh','zh-Hans','zh-Hant','en'],default='all')
    p.add_argument('--limit',default=3,type=int);args=p.parse_args()
    if args.action=='build':build()
    elif args.action=='acceptance':acceptance()
    else:
        if args.appid is None or not args.patch:p.error('lookup 需要 --appid 和 --patch')
        print(json.dumps(EvidenceSearch().lookup(args.appid,args.patch,args.query,args.item,args.phase,args.language,args.limit),ensure_ascii=False))
