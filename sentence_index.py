"""Bounded sentence-vector retrieval. No translated/generated text or approval.

A sentence is encoded once per exact document text, independently of the
quotation. Candidate windows use weighted sums of those vectors; this is
an approximation of embedding the whole window, not the same neural score.
"""
from collections import OrderedDict
import math

from alignment import segments,text_and_starts
from resolver import norm,sha


class SentenceVectorIndex:
    def __init__(self,encoder,max_documents=32,max_segments=12000):
        if min(max_documents,max_segments)<1:
            raise ValueError('invalid_index_budget')
        self.encoder=encoder
        self.max_documents=max_documents
        self.max_segments=max_segments
        self.entries=OrderedDict()
        self.hits=0
        self.builds=0

    def document(self,doc,parts):
        import numpy as np
        text,_=text_and_starts(doc)
        key=(self.encoder.model_id,sha(text.encode()),'sentence_sqrt_words_v1')
        if key in self.entries:
            self.hits+=1
            self.entries.move_to_end(key)
            return self.entries[key]
        if not parts or len(parts)>self.max_segments:
            raise ValueError('segment_budget_exceeded')
        vectors=np.asarray(self.encoder.encode([p.text for p in parts]),dtype=np.float64)
        if vectors.ndim!=2 or vectors.shape[0]!=len(parts):
            raise ValueError('invalid_embedding_shape')
        lengths=np.linalg.norm(vectors,axis=1)
        if not np.isfinite(vectors).all() or np.any(lengths<=0):
            raise ValueError('invalid_embedding_vector')
        words=np.asarray([max(1,len(norm(p.text).split())) for p in parts],dtype=np.int64)
        weighted=vectors/lengths[:,None]*np.sqrt(words)[:,None]
        prefix=np.vstack((np.zeros((1,vectors.shape[1])),np.cumsum(weighted,axis=0)))
        counts=np.r_[0,np.cumsum(words)]
        item=(prefix,counts)
        self.entries[key]=item;self.builds+=1
        while len(self.entries)>self.max_documents:
            self.entries.popitem(last=False)
        return item

    def rank(self,quote,docs,top=5,max_windows=120000,max_span=64):
        import numpy as np
        if min(top,max_windows,max_span)<1:
            raise ValueError('invalid_window_budget')
        qn=len(norm(quote).split())
        result={'method':'pooled_sentence_cosine_v1','status':'no_candidate',
                'candidates':[],'window_count':0,'sentence_count':0,
                'model_id':self.encoder.model_id,'auto_publish':False,'rights':'unreviewed'}
        if qn<4: return result
        minimum=max(4,math.ceil(qn*.35));maximum=max(24,math.floor(qn*2.8))
        plans=[]
        # Preflight the whole candidate space before any neural inference.
        for doc in docs:
            parts=segments(doc,'sentence')
            if not parts: continue
            result['sentence_count']+=len(parts)
            if result['sentence_count']>self.max_segments:
                result['status']='segment_budget_exceeded';return result
            counts=np.r_[0,np.cumsum([max(1,len(norm(p.text).split())) for p in parts])]
            windows=[]
            for i in range(len(parts)):
                a=max(i+1,int(np.searchsorted(counts,counts[i]+minimum,side='left')))
                b=min(len(parts),i+max_span,int(np.searchsorted(counts,counts[i]+maximum,side='right'))-1)
                if a<=b:
                    windows.append((i,a,b))
                    result['window_count']+=b-a+1
                    if result['window_count']>max_windows:
                        result['status']='window_budget_exceeded';return result
            plans.append((doc,parts,windows))
        if not result['window_count']: return result
        q=np.asarray(self.encoder.encode([quote])[0],dtype=np.float64)
        length=np.linalg.norm(q)
        if q.ndim!=1 or not np.isfinite(q).all() or not length>0:
            raise ValueError('invalid_query_vector')
        q=q/length
        rows=[]
        for doc,parts,windows in plans:
            if not windows: continue
            prefix,counts=self.document(doc,parts)
            if prefix.shape[1]!=q.shape[0]:
                raise ValueError('embedding_dimension_mismatch')
            for i,a,b in windows:
                ends=np.arange(a,b+1)
                vs=prefix[ends]-prefix[i]
                den=np.linalg.norm(vs,axis=1)
                cos=np.divide(vs@q,den,out=np.full(len(ends),-1.),where=den>1e-12)
                cos=np.clip(cos,-1,1)
                words=counts[ends]-counts[i]
                preference=np.minimum(words/qn,qn/words)
                scores=.90*cos+.10*preference
                for j,score,similarity in zip(ends,scores,cos):
                    lo,hi=parts[i].start,parts[int(j)-1].end
                    rows.append((round(float(score),8),round(float(similarity),8),doc,lo,hi))
        selected=[]
        for score,cos,doc,lo,hi in sorted(rows,key=lambda r:(-r[0],r[4]-r[3],r[2].url,r[2].digest,r[3])):
            if any(x['url']==doc.url and x['sha256']==doc.digest and x['char_start']<hi and x['char_end']>lo for x in selected):
                continue
            text,_=text_and_starts(doc)
            selected.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                             'char_start':lo,'char_end':hi,'text':text[lo:hi],
                             'score':score,'cosine':cos,'score_is_probability':False,
                             'boundary_verified':False,'rights':'unreviewed','auto_publish':False})
            if len(selected)==top: break
        result['candidates']=selected
        result['status']='review_required' if selected else 'no_candidate'
        return result
