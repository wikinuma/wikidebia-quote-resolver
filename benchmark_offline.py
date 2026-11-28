#!/usr/bin/env python3
"""Tiny exploratory ranking check: genuine short anchors + invented distractors.
Not a full-text benchmark; not independent of the hand-built lexicon.
"""
import json
from pathlib import Path
from resolver import Document, norm, rank, sha

cases=json.loads((Path(__file__).parent/'cases.json').read_text())['cases']
# All distractors are fictional, not attributed to the original authors.
# The positive blocks contain only the short published anchor used as a locator.
distractors=[
    ['free development of social property','each citizen is free to choose','the development of industry'],
    ['the rotting of the old system','a passively growing mass','the historical movement of society'],
    ['all allies have common interests','the best of all possible allies','property is an important concern'],
    ['the fish can live in troubled waters','they are ready for a struggle','the revolution has begun'],
    ['the exploited and oppressed class','this struggle has now reached a different stage','the old society has passed away'],
    ['serfdom in Russia before the reform','remnants of the ancient order','religion and society'],
]
results=[]
for c,decoys in zip(cases,distractors):
    positive=c['expected_needle']; url=c['expected_en_mia']
    docs=[Document(f'https://www.marxists.org/test-fixture/distractor-{i}',[d],sha(d.encode())) for i,d in enumerate(decoys)]
    docs.append(Document(url,[positive],sha(positive.encode())))
    ranked=rank(c['selector'],docs,top=5)
    hit=next((i for i,r in enumerate(ranked,1) if r['url']==url and norm(positive) in norm(r['text'])),None)
    results.append({'id':c['id'],'positive_rank':hit,'candidate_count':len(ranked),'first_is_distractor':bool(ranked and ranked[0]['url']!=url),'top_candidates':[{'label':'published_anchor' if r['url']==url else 'synthetic_distractor','score':r['score']} for r in ranked]})
print(json.dumps({'scope':'manually selected short excerpts and synthetic distractors, not full quotes or live corpus','cases':results,'top_1':sum(r['positive_rank']==1 for r in results),'top_5':sum(r['positive_rank'] is not None for r in results),'n':len(results)},ensure_ascii=False,indent=2))
