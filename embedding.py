"""Optional local semantic retrieval; no hosted inference, no silent truncation.
Actual neural model execution is not required for the default lexical workflow.
"""
from __future__ import annotations
import hashlib
import math
import sqlite3
from pathlib import Path
from resolver import norm, sha
from alignment import segments, text_and_starts


def candidate_windows(quote,docs,max_windows=6000):
    """Exact source substrings; sentence windows plus paragraph windows.
    Bounded by word-length ratio. Return an explicit limit flag, not a silent cut.
    """
    qn=len(norm(quote).split()); rows=[]; seen=set()
    for doc in docs:
        text,_=text_and_starts(doc)
        for kind,max_span in [('sentence',6),('paragraph',4)]:
            parts=segments(doc,kind)
            for i in range(len(parts)):
                for n in range(1,min(max_span,len(parts)-i)+1):
                    lo,hi=parts[i].start,parts[i+n-1].end; t=text[lo:hi]; count=len(norm(t).split())
                    if count>max(24,qn*2.8): break
                    if count<max(3,qn*.35): continue
                    k=(doc.url,doc.digest,lo,hi)
                    if k in seen: continue
                    if len(rows)>=max_windows: return [],True
                    seen.add(k); rows.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                        'char_start':lo,'char_end':hi,'text':t,'boundary_verified':False,'rights':'unreviewed'})
    return rows,False


class LocalEncoder:
    """Optional dependency. Explicit local safetensors snapshot; CPU only.
    SQLite cache stores float32 vectors keyed by exact model files and input.
    Long passages are split into fitting chunks, then mean pooled, not truncated.
    """
    def __init__(self,model_dir,cache_path):
        import numpy as np
        import sentence_transformers
        from sentence_transformers import SentenceTransformer
        import transformers, torch
        self.np=np; path=Path(model_dir).resolve()
        if not path.is_dir() or not list(path.rglob('*.safetensors')):
            raise ValueError('local model directory with safetensors required')
        h=hashlib.sha256()
        for p in sorted(x for x in path.rglob('*') if x.is_file()):
            # Include filenames and full file digests; no cache reuse across snapshots.
            fhash=hashlib.sha256()
            with p.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''): fhash.update(block)
            h.update(str(p.relative_to(path)).encode()+b'\0'+fhash.digest())
        h.update(f'{sentence_transformers.__version__}|{transformers.__version__}|{torch.__version__}|cpu|chunk_mean_v1'.encode())
        self.model_id=h.hexdigest()
        self.model=SentenceTransformer(str(path),device='cpu',local_files_only=True,
                     trust_remote_code=False,model_kwargs={'use_safetensors':True})
        self.max_tokens=self.model.max_seq_length
        if not isinstance(self.max_tokens,int) or self.max_tokens<8: raise ValueError('invalid model token limit')
        self.dim=self.model.get_sentence_embedding_dimension()
        self.stats={'cache_hits':0,'encoded_texts':0,'chunked_texts':0,'encoded_chunks':0}
        cp=Path(cache_path); cp.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(cp,timeout=30)
        self.db.execute('CREATE TABLE IF NOT EXISTS vectors (model TEXT, text_sha TEXT, dim INTEGER, vec BLOB, PRIMARY KEY(model,text_sha))')

    def close(self): self.db.close()

    def token_length(self,text):
        return len(self.model.tokenizer(text,add_special_tokens=True,truncation=False,verbose=False)['input_ids'])

    def chunks(self,text):
        if self.token_length(text)<=self.max_tokens: return [text]
        # Split at whitespace using bounded binary search, preserving order.
        words=text.split(); out=[]; i=0
        while i<len(words):
            lo=i+1; hi=min(len(words),i+self.max_tokens); best=i
            while lo<=hi:
                mid=(lo+hi)//2; candidate=' '.join(words[i:mid])
                if self.token_length(candidate)<=self.max_tokens: best=mid; lo=mid+1
                else: hi=mid-1
            if best==i: raise ValueError('unsegmentable overlong token')
            out.append(' '.join(words[i:best])); i=best
        return out

    def encode(self,texts):
        np=self.np; out=[]; missing={}; vectors={}
        for text in dict.fromkeys(texts):
            key=sha(text.encode()); row=self.db.execute('SELECT dim,vec FROM vectors WHERE model=? AND text_sha=?',(self.model_id,key)).fetchone()
            if row and row[0]==self.dim and len(row[1])==self.dim*4:
                v=np.frombuffer(row[1],dtype='<f4')
                if np.isfinite(v).all() and abs(float(np.linalg.norm(v))-1)<.005:
                    vectors[text]=v; self.stats['cache_hits']+=1; continue
            missing[text]=self.chunks(text)
        # Batch per <= 64 original texts. All chunks fit the actual tokenizer limit.
        keys=list(missing)
        for start in range(0,len(keys),64):
            group=keys[start:start+64]; all_chunks=[x for t in group for x in missing[t]]
            raw=self.model.encode(all_chunks,batch_size=32,normalize_embeddings=True,convert_to_numpy=True,show_progress_bar=False)
            pos=0
            for t in group:
                count=len(missing[t]); v=np.asarray(raw[pos:pos+count].mean(axis=0),dtype='<f4'); pos+=count
                length=float(np.linalg.norm(v))
                if not np.isfinite(v).all() or length<=0: raise ValueError('invalid model vector')
                v=v/length; vectors[t]=v; self.stats['encoded_texts']+=1; self.stats['encoded_chunks']+=count
                self.stats['chunked_texts']+=int(count>1)
                self.db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',(self.model_id,sha(t.encode()),self.dim,v.astype('<f4').tobytes()))
            self.db.commit()
        return [vectors[t] for t in texts]


def semantic_rank(quote,docs,encoder,top=5,max_windows=6000):
    rows,truncated=candidate_windows(quote,docs,max_windows)
    result={'method':'local_multilingual_cosine_v1','status':'review_required','candidates':[],
            'auto_publish':False,'model_id':getattr(encoder,'model_id','test_encoder'),'window_count':len(rows)}
    if truncated: result['status']='embedding_window_budget_exceeded'; return result
    if not rows: result['status']='no_candidate'; return result
    vectors=encoder.encode([quote]+[r['text'] for r in rows]); q=vectors[0]
    def cosine(a,b):
        den=math.sqrt(sum(float(x)**2 for x in a)*sum(float(x)**2 for x in b))
        return sum(float(x)*float(y) for x,y in zip(a,b))/den if den else -1
    for row,v in zip(rows,vectors[1:]):
        row['score']=round(cosine(q,v),6)
        row['score_is_probability']=False
    # Deduplicate overlapping candidates only within the same source document.
    # Keep a broad window when it scores highest, but never call it exact quote.
    selected=[]
    for row in sorted(rows,key=lambda r:(-r['score'],r['char_end']-r['char_start'],r['url'],r['char_start'])):
        if any(r['url']==row['url'] and r['sha256']==row['sha256'] and r['char_start']<row['char_end'] and r['char_end']>row['char_start'] for r in selected): continue
        selected.append(row)
        if len(selected)>=top: break
    result['candidates']=selected
    return result
