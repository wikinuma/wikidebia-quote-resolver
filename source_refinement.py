"""Bounded source recovery for long quotations and explicitly omitted fragments.

All coordinates address the actual extracted document. Neither a fuzzy match
nor an omission envelope proves an exact citation or authorizes its reuse.
"""
from __future__ import annotations

from bisect import bisect_left
from resolver import norm, sha
from alignment import tokens_with_offsets, text_and_starts
from source_evidence import GAP, positions, multi_anchor_candidates


def bounded_prefix_distance(query, source, budget):
    """Exact prefix edit distance when <= budget; otherwise budget+1.

    A feasible edit path stays within the diagonal band. Costs above the
    threshold cannot lead to a feasible final path. This avoids quadratic
    work on long fragments without enlarging the accepted edit distance.
    """
    if budget < 0:
        raise ValueError('negative_edit_budget')
    if not query:
        return 0,0,False
    n=min(len(source),len(query)+budget)
    invalid=budget+1
    prev={j:j for j in range(min(n,budget)+1)}
    for i,word in enumerate(query,1):
        row={}
        for j in range(max(0,i-budget),min(n,i+budget)+1):
            if j==0:
                cost=i
            else:
                cost=min(prev.get(j,invalid)+1,row.get(j-1,invalid)+1,
                         prev.get(j-1,invalid)+(word!=source[j-1]))
            if cost<=budget:
                row[j]=cost
        if not row:
            return invalid,0,False
        prev=row
    cost=min(prev.values())
    ends=[j for j,c in prev.items() if c==cost]
    return cost,min(ends,key=lambda j:(abs(j-len(query)),j)),len(ends)>1


def long_variants(quote,docs):
    return multi_anchor_candidates(quote,docs,max_words=600,max_token_edits=40,
                                   distance=bounded_prefix_distance,max_cells=10_000_000)


def fragment_paths(quote, docs, max_gap=500, max_paths=100):
    """Resolve retained fragments independently and join only ordered spans
    from the same exact document. Preserve edge omissions, ambiguity and
    the number of omitted words. No implicit gaps are introduced.
    """
    out={'method':'bounded_fragment_alignment_v2','status':'not_applicable',
         'candidates':[],'truncated':False,'auto_publish':False}
    if not GAP.search(quote):
        return out
    fragments=[norm(p).split() for p in GAP.split(quote) if norm(p)]
    words=sum(map(len,fragments))
    out.update(fragments=len(fragments),fragment_words=list(map(len,fragments)))
    if not 1<=len(fragments)<=8 or not 8<=words<=600 or any(len(f)<3 for f in fragments):
        out['status']='unsupported_fragments'
        return out
    if max_gap<0 or max_paths<1:
        raise ValueError('invalid_omission_budget')
    rows=[]; examined=0
    for doc in docs:
        text,_=text_and_starts(doc); ts=tokens_with_offsets(text)
        ws=[t[0] for t in ts]; starts=[t[1] for t in ts]; ends=[t[2] for t in ts]
        groups=[]
        for frag in fragments:
            hit,truncated=positions(ws,frag,max_paths)
            out['truncated']|=truncated
            spans=[(i,i+len(frag),0,False) for i in hit]
            # Exact hits take priority within the document. Approximation
            # never creates an artificial cross-document bridge.
            if not spans and len(frag)>=8:
                v=long_variants(' '.join(frag),[doc])
                out['truncated']|=v['truncated']
                if v.get('qualifying_hits',0)>len(v['candidates']):
                    # Overlapping seed clusters may collapse; we still refuse
                    # a unique-fragment claim when alternatives are hidden.
                    out['truncated']=True
                for h in v['candidates']:
                    if h.get('similarity',0)<.95:
                        continue
                    lo=bisect_left(starts,h['char_start'])
                    hi=bisect_left(ends,h['char_end'])+1
                    if lo>=len(ts) or hi>len(ts) or lo>=hi or starts[lo]!=h['char_start'] or ends[hi-1]!=h['char_end']:
                        continue
                    spans.append((lo,hi,h['token_edits'],h['boundary_ambiguous']))
            groups.append(sorted(set(spans)))
            if not spans:
                break
        if len(groups)!=len(fragments) or not all(groups):
            continue
        paths=[(x,) for x in groups[0]]
        for group in groups[1:]:
            new=[]
            for path in paths:
                for span in group:
                    gap=span[0]-path[-1][1]
                    if not 0<=gap<=max_gap:
                        continue
                    new.append(path+(span,))
                    if len(new)>max_paths:
                        out['truncated']=True
                        break
                if len(new)>max_paths:
                    break
            paths=new[:max_paths]
            if not paths:
                break
        for path in paths:
            if examined>=max_paths:
                out['truncated']=True
                break
            examined+=1
            edits=sum(x[2] for x in path)
            retained=sum(x[1]-x[0] for x in path)
            similarity=1-edits/max(words,retained)
            if similarity<.95:
                continue
            lo,hi=starts[path[0][0]],ends[path[-1][1]-1]
            passage=text[lo:hi]
            gaps=[b[0]-a[1] for a,b in zip(path,path[1:])]
            rows.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                         'char_start':lo,'char_end':hi,'text':passage,
                         'normalized_sha256':sha(norm(passage).encode()),
                         'query_words':words,'source_words':path[-1][1]-path[0][0],
                         'token_edits':edits,'similarity':round(similarity,4),
                         'fragment_ranges':[{'char_start':starts[a],'char_end':ends[b-1],
                                             'token_edits':e} for a,b,e,_ in path],
                         'gap_words':gaps,'omitted_words':sum(gaps),
                         'edge_omission_only':len(path)==1,
                         'match':'fragment_variant_omission_review',
                         'boundary_ambiguous':any(x[3] for x in path),
                         'boundary_verified':False,'rights':'unreviewed'})
    rows.sort(key=lambda h:(h['token_edits'],h['omitted_words'],h['url'],h['char_start']))
    out['qualifying_hits']=len(rows)
    out['truncated']|=len(rows)>5
    out['candidates']=rows[:5]
    out['status']='partial_fragment_budget' if out['truncated'] else 'variant_review_required' if rows else 'no_candidate'
    return out
