"""Bounded, review-only alignment of *already identified parallel sections*.

No translation generation. Offset coordinates refer to the extracted plain text
(joined paragraphs), not the HTML or printed edition.
"""
from __future__ import annotations
import bisect
import math
import re
import unicodedata
from dataclasses import dataclass
from resolver import Document, anchors, norm, sha

# Bare ellipses may be original punctuation or an editorial omission.
# In either case, do not label them an exact contiguous citation.
OMISSION = re.compile(r'\.{3,}|\u2026')


def text_and_starts(doc):
    starts=[]; offset=0
    for b in doc.blocks:
        starts.append(offset); offset+=len(b)+2
    return '\n\n'.join(doc.blocks), starts


def tokens_with_offsets(text):
    chars=[]; offsets=[]
    for i,c in enumerate(text):
        if c in '\u00ad\u200b': continue
        folded=unicodedata.normalize('NFKD',c.casefold().replace('\u0153','oe'))
        for k in folded:
            if not unicodedata.combining(k):
                chars.append(k); offsets.append(i)
    tokens=[]
    for m in re.finditer(r'[^\W_]+',''.join(chars),re.UNICODE):
        end=offsets[m.end()-1]+1
        # Include decomposed trailing combining marks in the extracted substring.
        while end<len(text) and unicodedata.combining(text[end]): end+=1
        tokens.append((m.group(),offsets[m.start()],end))
    return tokens


def exact_occurrences(quote,doc,limit=100):
    """All normalized contiguous token matches, including same-paragraph repeats.
    Explicit ellipses are not a contiguous match. No claim about final punctuation.
    """
    if limit<1: raise ValueError('limit must be positive')
    q=norm(quote).split(); text,starts=text_and_starts(doc)
    if len(q)<4 or OMISSION.search(quote): return [],False
    ts=tokens_with_offsets(text); words=[t[0] for t in ts]
    matches=[]
    for i,w in enumerate(words):
        if w!=q[0] or words[i:i+len(q)]!=q: continue
        lo,hi=ts[i][1],ts[i+len(q)-1][2]
        a=bisect.bisect_right(starts,lo)-1; b=bisect.bisect_right(starts,hi-1)-1
        if len(matches)>=limit: return matches,True
        matches.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                        'char_start':lo,'char_end':hi,'block_start':a,'block_end':b+1,
                        'text':text[lo:hi],'match':'normalized_contiguous_tokens',
                        'punctuation_verified':False})
    return matches,False


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    text: str


def segments(doc,kind='paragraph'):
    text,starts=text_and_starts(doc); out=[]
    if kind not in ('paragraph','sentence'): raise ValueError('unknown segment kind')
    for b,offset in zip(doc.blocks,starts):
        # Conservative sentence segmentation. Abbreviations and quotations can
        # still split imperfectly; this is candidate extraction, not proof.
        cuts=[0,len(b)]
        if kind=='sentence':
            cuts += [m.end() for m in re.finditer(r'[.!?\u2026][\"\u2019\u201d\u00bb)]*\s+(?=[A-Z\u00c0-\u00de0-9\"\u00ab\u201c])',b)]
        cuts=sorted(set(cuts))
        for a,z in zip(cuts,cuts[1:]):
            s=b[a:z]; lo=a+len(s)-len(s.lstrip()); hi=z-len(s)+len(s.rstrip())
            if lo<hi: out.append(Segment(offset+lo,offset+hi,text[offset+lo:offset+hi]))
    return out


def feature_windows(parts,side):
    result={}
    for i in range(len(parts)):
        for n in (1,2):
            if i+n>len(parts): continue
            s=' '.join(x.text for x in parts[i:i+n]); words=norm(s).split()
            result[i,n]=(max(1,len(words)),anchors(s,side),set(re.findall(r'\b\d+\b',s)))
    return result


def align_sections(fr,en,kind='paragraph',max_cells=250_000):
    """Monotone dynamic programming, 1:1/1:2/2:1/2:2 and gaps.

    Costs are heuristic, not calibrated confidence. A caller must establish
    that the two documents contain the same section independently of the quote.
    """
    f,e=segments(fr,kind),segments(en,kind); n,m=len(f),len(e)
    if not n or not m: return {'status':'empty_section','links':[]}
    if (n+1)*(m+1)>max_cells: return {'status':'alignment_budget_exceeded','links':[],'cells':(n+1)*(m+1)}
    ff,ef=feature_windows(f,0),feature_windows(e,1)
    ratio=sum(ef[j,1][0] for j in range(m))/sum(ff[i,1][0] for i in range(n))
    ratio=max(.55,min(1.65,ratio))
    costs=[[math.inf]*(m+1) for _ in range(n+1)]; costs[0][0]=0.0
    prev={}
    for i in range(n+1):
        for j in range(m+1):
            if not math.isfinite(costs[i][j]): continue
            for a,b in ((1,1),(1,2),(2,1),(2,2),(1,0),(0,1)):
                if i+a>n or j+b>m: continue
                if not a or not b: cost=1.35
                else:
                    nf,af,df=ff[i,a]; ne,ae,de=ef[j,b]
                    length=abs(math.log(ne/(nf*ratio)))
                    shared=2*len(af&ae)/max(1,len(af)+len(ae))
                    numbers=.20 if df and de and not df&de else 0
                    cost=.30+1.15*length+.40*(a+b-2)-.65*shared+numbers
                new=costs[i][j]+cost
                if new<costs[i+a][j+b]-1e-12:
                    costs[i+a][j+b]=new; prev[i+a,j+b]=(i,j,a,b,cost)
    links=[]; i,j=n,m
    while i or j:
        p,q,a,b,cost=prev[i,j]
        links.append({'fr_start':f[p].start if a else None,'fr_end':f[i-1].end if a else None,
                      'en_start':e[q].start if b else None,'en_end':e[j-1].end if b else None,
                      'fr_segments':a,'en_segments':b,'cost':round(cost,5)})
        i,j=p,q
    return {'status':'aligned_candidates_only','links':list(reversed(links)),
            'cost':round(costs[n][m],5),'cells':(n+1)*(m+1),'unit':kind,'length_ratio':round(ratio,4)}


def aligned_candidates(quote,fr,en,kind='paragraph',max_cells=250_000,alignment_cache=None):
    matches,truncated=exact_occurrences(quote,fr)
    result={'method':'monotone_length_anchors_v1','fr_occurrences':matches,'occurrences_truncated':truncated,
            'candidates':[],'auto_publish':False,'scope':'caller-identified parallel section; not proof of fidelity'}
    if not matches:
        result['status']='source_fr_omission_unverified' if OMISSION.search(quote) else 'source_fr_unconfirmed'
        return result
    if truncated or len(matches)>1:
        result['status']='ambiguous_source_occurrence'; return result
    alignment=(align_sections(fr,en,kind,max_cells) if alignment_cache is None
               else alignment_cache.get(fr,en,kind,max_cells))
    result['alignment']={k:v for k,v in alignment.items() if k!='links'}
    if alignment['status']!='aligned_candidates_only': result['status']=alignment['status']; return result
    hit=matches[0]; etext,_=text_and_starts(en)
    relevant=[l for l in alignment['links'] if l['fr_start'] is not None and l['fr_start']<hit['char_end'] and l['fr_end']>hit['char_start']]
    if not relevant or any(l['en_start'] is None for l in relevant):
        result['status']='aligned_gap_or_no_candidate'; return result
    lo=relevant[0]['en_start']; hi=relevant[-1]['en_end']
    result['status']='review_required'
    result['candidates']=[{'url':en.url,'sha256':en.digest,'revision':en.revision,'char_start':lo,'char_end':hi,
                           'text':etext[lo:hi],'boundary_verified':False,'rights':'unreviewed','links':relevant}]
    return result
