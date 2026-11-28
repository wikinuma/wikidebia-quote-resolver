"""Bounded source-side evidence beyond a single exact selector.

Only actual normalized tokens from retrieved documents are matched. Explicit
omissions and approximate matches remain variants for review, never exact
contiguous citations or automatic authorizations.
"""
from __future__ import annotations
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from resolver import norm,sha
from alignment import tokens_with_offsets,text_and_starts,OMISSION
from variants import prefix_distance

GAP = re.compile(r'\[\s*(?:\.{3,}|\u2026)\s*\]|\(\s*(?:\.{3,}|\u2026)\s*\)|\.{3,}|\u2026')


def positions(words,needle,limit):
    out=[]
    for i,w in enumerate(words):
        if w==needle[0] and words[i:i+len(needle)]==needle:
            if len(out)>=limit: return out,True
            out.append(i)
    return out,False


def record(doc,text,tokens,start,end,qwords,edits,kind,**extra):
    lo,hi=tokens[start][1],tokens[end-1][2]
    passage=text[lo:hi]
    return {'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
            'char_start':lo,'char_end':hi,'text':passage,
            'normalized_sha256':sha(norm(passage).encode()),
            'query_words':qwords,'source_words':end-start,
            'token_edits':edits,'boundary_verified':False,
            'match':kind,'rights':'unreviewed',**extra}


def omission_candidates(quote,docs,max_gap=500,max_paths=100):
    """All retained fragments must occur in order in the SAME document.
    Never infer an omission without an explicit marker. Cap path expansion.
    """
    result={'method':'explicit_omission_fragments_v1','candidates':[],
            'status':'not_applicable','truncated':False,'auto_publish':False}
    if not GAP.search(quote): return result
    fragments=[norm(p).split() for p in GAP.split(quote) if norm(p)]
    qwords=sum(map(len,fragments))
    result.update(fragments=len(fragments),fragment_words=list(map(len,fragments)))
    # Tiny fragments provide too little evidence; do not bridge them.
    if not fragments or len(fragments)>8 or qwords<8 or qwords>250 or any(len(f)<3 for f in fragments):
        result['status']='unsupported_fragments'; return result
    if max_gap<0 or max_paths<1: raise ValueError('invalid_omission_budget')
    rows=[]; paths_examined=0
    for doc in docs:
        text,_=text_and_starts(doc); ts=tokens_with_offsets(text); ws=[t[0] for t in ts]
        hits=[]
        for frag in fragments:
            p,truncated=positions(ws,frag,max_paths)
            result['truncated']|=truncated
            hits.append(p)
        if not all(hits): continue
        paths=[[(p,p+len(fragments[0]))] for p in hits[0]]
        for k in range(1,len(fragments)):
            new=[]
            for path in paths:
                end=path[-1][1]; candidates=hits[k]
                i=bisect_left(candidates,end)
                while i<len(candidates) and candidates[i]-end<=max_gap:
                    if len(new)>=max_paths:
                        result['truncated']=True; break
                    p=candidates[i]; new.append(path+[(p,p+len(fragments[k]))]); i+=1
                if len(new)>=max_paths: break
            paths=new
            if not paths: break
        for path in paths:
            if paths_examined>=max_paths:
                result['truncated']=True; break
            paths_examined+=1
            gaps=[b[0]-a[1] for a,b in zip(path,path[1:])]
            coords=[{'char_start':ts[a][1],'char_end':ts[b-1][2]} for a,b in path]
            rows.append(record(doc,text,ts,path[0][0],path[-1][1],qwords,0,'explicit_omission_review',
                               fragment_ranges=coords,omitted_words=sum(gaps),gap_words=gaps,
                               similarity=None,boundary_ambiguous=False))
        if paths_examined>=max_paths: break
    rows.sort(key=lambda r:(r['omitted_words'],r['url'],r['char_start']))
    result.update(candidates=rows[:5],qualifying_hits=len(rows),
                  status='partial_omission_budget' if result['truncated'] else
                         'omission_review_required' if rows else 'no_candidate')
    return result


def multi_anchor_candidates(quote,docs,max_seed_hits=10000,max_alignments=32,max_cells=2500000,
                            max_words=250,max_token_edits=24,distance=prefix_distance):
    """Use several independent unique four-token seeds instead of one
    preselected phrase. Group approximately co-located seeds before bounded
    edit distance. Reject unsupported matches; never reinterpret long gaps.
    """
    result={'method':'multi_anchor_token_edit_v1','candidates':[],
            'status':'no_candidate','seed_hits':0,'alignments':0,'cells':0,
            'truncated':False,'auto_publish':False}
    q=norm(quote).split()
    if len(q)<8 or len(q)>max_words or OMISSION.search(quote):
        result['status']='unsupported_length_or_omission'; return result
    if min(max_seed_hits,max_alignments,max_cells,max_words,max_token_edits)<1: raise ValueError('invalid_multi_anchor_budget')
    size=4; counts=Counter(tuple(q[i:i+size]) for i in range(len(q)-size+1))
    seeds={tuple(q[i:i+size]):i for i in range(len(q)-size+1) if counts[tuple(q[i:i+size])]==1}
    if not seeds: return result
    budget=min(max_token_edits,max(2,len(q)//4)); rows=[]; pending=[]
    for doc in docs:
        text,_=text_and_starts(doc); ts=tokens_with_offsets(text); ws=[t[0] for t in ts]
        groups=defaultdict(list)
        for i in range(len(ws)-size+1):
            pos=seeds.get(tuple(ws[i:i+size]))
            if pos is None: continue
            if result['seed_hits']>=max_seed_hits:
                result['truncated']=True; break
            result['seed_hits']+=1
            groups[(i-pos)//(budget+1)].append((i,pos))
        for key,group in groups.items():
            near=groups.get(key-1,[])+group+groups.get(key+1,[])
            # Require at least two distinct, non-overlapping quote seeds.
            offsets={p for _,p in near}
            if max(offsets)-min(offsets)<size: continue
            # Choose one representative of this diagonal cluster; prefer
            # middle-of-quote seeds for stable left/right boundaries.
            i,pos=min(group,key=lambda v:(abs(v[1]-len(q)/2),v[0],v[1]))
            support=len(offsets)
            pending.append((-support,doc.url,i,pos,doc,text,ts,ws))
        if result['truncated']: break
    for support,_,i,pos,doc,text,ts,ws in sorted(pending,key=lambda x:x[:4]):
        if result['alignments']>=max_alignments:
            result['truncated']=True; break
        j=i+size
        left=ws[max(0,i-pos-budget):i][::-1]; right=ws[j:j+len(q)-pos-size+budget]
        cells=(pos+1)*(len(left)+1)+(len(q)-pos-size+1)*(len(right)+1)
        if result['cells']+cells>max_cells:
            result['truncated']=True; break
        result['cells']+=cells; result['alignments']+=1
        lc,ln,la=distance(q[:pos][::-1],left,budget)
        rc,rn,ra=distance(q[pos+size:],right,budget)
        edits=lc+rc
        if edits>budget: continue
        start,end=i-ln,j+rn
        # Recheck the chosen interval contains at least two disjoint
        # original seeds (not merely neighboring repeated boilerplate).
        confirmed={seeds[tuple(ws[k:k+size])] for k in range(start,end-size+1) if tuple(ws[k:k+size]) in seeds}
        if not confirmed or max(confirmed)-min(confirmed)<size: continue
        rows.append(record(doc,text,ts,start,end,len(q),edits,'multi_anchor_variant_review',
                           similarity=round(1-edits/max(len(q),end-start),4),
                           boundary_ambiguous=la or ra,seed_support=len(confirmed)))
    rows.sort(key=lambda r:(r['token_edits'],abs(r['source_words']-r['query_words']),r['url'],r['char_start']))
    for row in rows:
        if any(x['url']==row['url'] and x['sha256']==row['sha256'] and x['char_start']<row['char_end'] and x['char_end']>row['char_start'] for x in result['candidates']): continue
        result['candidates'].append(row)
        if len(result['candidates'])==5: break
    result['qualifying_hits']=len(rows)
    result['status']='partial_multi_anchor_budget' if result['truncated'] else 'variant_review_required' if result['candidates'] else 'no_candidate'
    return result
