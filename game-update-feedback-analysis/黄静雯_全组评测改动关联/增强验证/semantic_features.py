"""在本机编码官方改动和玩家原文，保留相似度与可回查的原文区间。"""
from collections import defaultdict
import time
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from experiment_data import HERE,BASE,new_catalog,CATALOG,OUT,read_rows,write_rows,write_json,digest
from get_encoder import DEFAULT_DIR,FILES,checksum
from group_model import pm

class Encoder:
    def __init__(self,model_dir=DEFAULT_DIR):
        for name,(expected,_) in FILES.items():
            if checksum(model_dir/name)!=expected:raise ValueError(f'模型校验未通过 {name}')
        self.tokenizer=Tokenizer.from_file(str(model_dir/'tokenizer.json'))
        opt=ort.SessionOptions();opt.intra_op_num_threads=2;opt.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(model_dir/'model_qint8_avx512_vnni.onnx'),opt,providers=['CPUExecutionProvider'])
        self.elapsed=0

    def spans(self,text):
        spans=[]
        for a,b in pm.passages(text):
            offsets=self.tokenizer.encode(text[a:b],add_special_tokens=False).offsets
            if len(offsets)<=168:
                spans.append((a,b));continue
            for i in range(0,len(offsets),144):
                stop=min(i+168,len(offsets))
                spans.append((a+offsets[i][0],a+offsets[stop-1][1]))
                if stop==len(offsets):break
        return spans

    def encode(self,texts):
        start=time.perf_counter()
        strings=sorted(set(texts))
        tokens=self.tokenizer.encode_batch(strings)
        order=sorted(range(len(strings)),key=lambda i:len(tokens[i].ids))
        vectors={}
        for pos in range(0,len(order),16):
            members=order[pos:pos+16]
            width=max(len(tokens[i].ids) for i in members)
            if width>512:raise ValueError(f'超过模型序列长度 {width}')
            ids=np.full((len(members),width),self.tokenizer.token_to_id('<pad>'),np.int64)
            attention=np.zeros_like(ids)
            for j,i in enumerate(members):
                seq=tokens[i].ids;ids[j,:len(seq)]=seq;attention[j,:len(seq)]=1
            hidden=self.session.run(['last_hidden_state'],dict(input_ids=ids,attention_mask=attention,token_type_ids=np.zeros_like(ids)))[0]
            pooled=(hidden*attention[:,:,None].astype(np.float32)).sum(1)/attention.sum(1)[:,None]
            pooled/=np.maximum(np.linalg.norm(pooled,axis=1,keepdims=True),1e-12)
            for i,v in zip(members,pooled):vectors[strings[i]]=v.astype(np.float32)
        self.elapsed+=time.perf_counter()-start
        return vectors

def query_texts(item):
    return ['query: '+item['title']+'。'+item['description']+' '+', '.join(item['aliases_zh']),
            'query: '+item['title_en']+'. '+', '.join(item['aliases_en'])]

def compute(rows,items,encoder):
    by_patch=defaultdict(list)
    for it in items:by_patch[it['patch_id']].append(it)
    used={r['patch_id'] for r in rows}
    catalogs={p:pm.CatalogIndex(by_patch[p]) for p in used}
    queries=encoder.encode(q for p in used for it in by_patch[p] for q in query_texts(it))
    span_map={r['review']:encoder.spans(r['review']) for r in rows}
    passages=encoder.encode('passage: '+s[a:b] for s,ss in span_map.items() for a,b in ss)
    all_features=[]
    for r in rows:
        p=r['patch_id'];text=r['review'];it=by_patch[p];spans=span_map[text]
        lexical=catalogs[p].candidates([r])[0]
        exact=[candidate['item_id'] for candidate,patterns in zip(it,catalogs[p].patterns)
               if any(rule.search(pm.normalize(text)) for _,rule in patterns)]
        old={c[1]:c for c in lexical}
        x=dict(r,exact=exact,candidates=[])
        if spans:
            passages_mat=np.stack([passages['passage: '+text[a:b]] for a,b in spans])
            query_mat=np.stack([queries[q] for item in it for q in query_texts(item)])
            sim=(passages_mat@query_mat.T).reshape(len(spans),len(it),2).max(axis=2)
            best=np.argmax(sim,axis=0);peak=sim[best,np.arange(len(it))]
            dense=set(sorted(range(len(it)),key=lambda j:(-float(peak[j]),it[j]['item_id']))[:2])
            for j,item in enumerate(it):
                iid=item['item_id'];prior=old.get(iid)
                if prior is None and j not in dense:continue
                loc=int(best[j]);others=np.delete(sim[loc],j)
                margin=float(peak[j]-(others.max() if len(others) else 0))
                x['candidates'].append(dict(item_id=iid,lexical=list(prior[2]) if prior else None,
                    lexical_span=[prior[3],prior[4]] if prior else None,dense_top2=j in dense,
                    semantic=float(peak[j]),margin=margin,semantic_span=list(spans[loc])))
        all_features.append(x)
    return all_features,dict(reviews=len(rows),distinct_inputs=len(passages),item_queries=len(queries),encode_seconds=encoder.elapsed)

def development():
    old=[r for r in read_rows(OUT/'黄静雯_评测标注/AI逐条复核250条.jsonl') if r['split']=='development' and r['annotation_label']!='待判']
    added=[r for r in read_rows(BASE/'development_AI标注.jsonl') if r['annotation_label']!='待判']
    for r in old:r.update(case='P'+str(r['review_id']),sample_type='personal',language_std={'english':'en','schinese':'zh-Hans','tchinese':'zh-Hant'}[r['language']])
    return old+added

def build(kind):
    if kind=='dev':
        rows=development();items=read_rows(CATALOG/'items/items.jsonl')
    elif kind=='new':
        rows=read_rows(HERE/'新版本抽样.jsonl');_,items=new_catalog()
    else:raise ValueError(kind)
    feat,stats=compute(rows,items,Encoder())
    file=HERE/(kind+'_特征.jsonl')
    write_rows(file,feat)
    write_json(HERE/(kind+'_编码计时.json'),dict(stats,sha256=digest(file)))
    print(kind,stats,flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['dev','new']);build(p.parse_args().kind)
