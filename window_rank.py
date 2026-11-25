"""Bounded English source-window retrieval for weak paragraph lexical matches.

Cognate prefixes and the existing bilingual dictionary are retrieval
features only. No translated or generated prose is returned.
"""
from __future__ import annotations
import math
from collections import Counter
from resolver import norm,anchors
from alignment import segments,text_and_starts

STOP={'however','because','therefore','another','through','without','different','people','against','before','between','within','together','toujours','encore','autrefois','lorsque','puisque','cependant','contre','depuis','toutes','toutefois'}


def cognates(text):
    return {w[:5] for w in norm(text).split() if len(w)>=6 and w.isalpha() and w not in STOP}


def rank_short_windows(quote,docs,top=5,max_windows=12000):
    if top<1 or max_windows<1: raise ValueError('invalid_window_budget')
    qn=len(norm(quote).split()); qa=anchors(quote,0); qc=cognates(quote)
    result={'method':'sentence_windows_cognates_v1','status':'no_candidate','candidates':[],
            'examined_windows':0,'truncated':False,'auto_publish':False,'rights':'unreviewed'}
    if qn<4 or not (qa or qc): return result
    rows=[]
    for doc in docs:
        full,_=text_and_starts(doc); parts=segments(doc,'sentence')
        for i in range(len(parts)):
            for count in range(1,min(6,len(parts)-i)+1):
                lo,hi=parts[i].start,parts[i+count-1].end
                text=full[lo:hi]; n=len(norm(text).split())
                if n>max(24,qn*2.8): break
                if n<max(4,qn*.35): continue
                if result['examined_windows']>=max_windows:
                    result['truncated']=True; break
                result['examined_windows']+=1
                da=anchors(text,1); dc=cognates(text)
                shared_a=qa&da; shared_c=qc&dc
                # A single cognate is only a weak signal; do not propose
                # unrelated windows based on one ubiquitous prefix alone.
                if not shared_a and len(shared_c)<2: continue
                overlap=(2*len(shared_a)+len(shared_c))/max(1,2*len(qa)+len(qc))
                precision=(2*len(shared_a)+len(shared_c))/max(1,2*len(da)+len(dc))
                length=math.exp(-abs(math.log(n/max(1,qn))))
                rows.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                             'char_start':lo,'char_end':hi,'text':text,
                             'score':round(.60*overlap+.25*precision+.15*length,6),
                             'shared_dictionary_anchors':len(shared_a),'shared_cognate_prefixes':len(shared_c),
                             'score_is_probability':False,'boundary_verified':False,'rights':'unreviewed',
                             'method':'sentence_windows_cognates_v1'})
            if result['truncated']: break
        if result['truncated']: break
    for r in sorted(rows,key=lambda x:(-x['score'],x['char_end']-x['char_start'],x['url'],x['char_start'])):
        if any(x['url']==r['url'] and x['sha256']==r['sha256'] and x['char_start']<r['char_end'] and x['char_end']>r['char_start'] for x in result['candidates']): continue
        result['candidates'].append(r)
        if len(result['candidates'])==top: break
    result['status']='partial_window_budget' if result['truncated'] else 'review_required' if result['candidates'] else 'no_candidate'
    return result
