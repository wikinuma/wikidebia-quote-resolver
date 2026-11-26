"""Interleave independent candidate rankings without another model call.

Exact deduplication preserves document and location provenance. The lexical
and semantic lists remain available separately. This is a review-order
heuristic, not an acceptance or confidence calibration rule.
"""
from evidence_audit import read_span
from resolver import sha


def key(row):
    lo,hi=row['char_start'],row['char_end']
    if type(lo) is not int or type(hi) is not int or not 0<=lo<hi:
        raise ValueError('invalid_candidate_coordinates')
    h=row['text_sha256']
    if not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h):
        raise ValueError('invalid_candidate_digest')
    return (row['url'],row.get('sha256',row.get('document_sha256')),row.get('revision'),lo,hi,h)


def interleave(first,second,limit=5):
    if type(limit) is not int or limit<1: raise ValueError('invalid_fusion_limit')
    seen=set();out=[]
    for i in range(max(len(first),len(second))):
        for arm in (first,second):
            if i>=len(arm): continue
            c=arm[i];k=key(c)
            if k in seen: continue
            seen.add(k);out.append(c)
            if len(out)>=limit:return out
    return out


def canonical_candidates(candidates,docs):
    """Unify block and character coordinates against exact collected text."""
    by={(d.url,d.digest,d.revision):d for d in docs}
    out=[]
    for c in candidates:
        d=by.get((c['url'],c['sha256'],c.get('revision')))
        if d is None: raise ValueError('candidate_document_not_collected')
        lo,hi,text=read_span(d,c)
        h=sha(text.encode())
        if ('text' in c and c['text']!=text) or ('text_sha256' in c and c['text_sha256']!=h):
            raise ValueError('candidate_not_exact_source_text')
        row={**c,'char_start':lo,'char_end':hi,'text_sha256':h,
             'boundary_verified':False,'rights':'unreviewed','auto_publish':False}
        out.append(row)
    return out


def fused_review_queue(semantic,lexical,docs,limit=5):
    s=canonical_candidates(semantic,docs);l=canonical_candidates(lexical,docs)
    return {'method':'interleaved_semantic_lexical_v1','candidates':interleave(s,l,limit),
            'fallback_preserved':True,'auto_publish':False,'rights':'unreviewed'}
