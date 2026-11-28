#!/usr/bin/env python3
"""Synthetic structural stress tests, NOT real full-text retrieval metrics.
Positive targets and negative cases are declared before ranking. Texts are
invented for the benchmark, not attributed to Marx, Engels, or other authors.
"""
import json
from resolver import Document, sha, norm
from compare import compare_methods


def d(parts,lang):
    return Document('https://wikirouge.net/texts/'+lang+'/synthetic_fixture',parts,sha('\n\n'.join(parts).encode()))

# Different vocabulary, paragraph boundaries, repeated concepts, and explicit
# negatives. No changes to the v0.1 bilingual lexicon were made for these tests.
base_fr=['Les archives ont été ouvertes en 1924.','La richesse du pays a augmenté rapidement.','Les paysans ont vendu leurs terres aux nouveaux propriétaires.','Le conseil municipal a refusé le projet de pont.','Les écoles publiques ont rouvert en 1932.']
base_en=['The archives opened in 1924.','The wealth of the country increased rapidly.','The peasants sold their land to the new owners.','The town council rejected the bridge proposal.','The public schools reopened in 1932.']
CASES=[
 ('ordinary',base_fr,base_en,base_fr[1],base_en[1],True),
 ('different_vocabulary',base_fr,base_en,base_fr[3],base_en[3],True),
 ('proper_numbers',base_fr,base_en,base_fr[4],base_en[4],True),
 ('split_en',base_fr[:1]+['La richesse du pays a augmenté. Les paysans ont acheté des terres.']+base_fr[3:],base_en[:1]+['The wealth of the country increased.','The peasants bought land.']+base_en[3:],'La richesse du pays a augmenté. Les paysans ont acheté des terres.','The wealth of the country increased.\n\nThe peasants bought land.',True),
 ('merged_en',base_fr,base_en[:1]+[base_en[1]+' '+base_en[2]]+base_en[3:],base_fr[2],base_en[2],True),
 ('cross_paragraph',base_fr,base_en,base_fr[1]+' '+base_fr[2],base_en[1]+'\n\n'+base_en[2],True),
 ('inside_sentence',base_fr,base_en,'Le conseil municipal a refusé','The town council rejected',True),
 ('added_preface',base_fr,['Editorial note on the circumstances of publication.']+base_en,base_fr[3],base_en[3],True),
 ('semantic_decoy', ['La propriété ne constitue pas un droit absolu.','Les paysans ont travaillé ensemble.','Le conseil approuve ce compromis après délibération.','La propriété constitue un droit absolu.'],['Property is not an absolute right.','The peasants worked together.','After deliberation the council accepts this compromise.','Property constitutes an absolute right.'],'La propriété ne constitue pas un droit absolu.','Property is not an absolute right.',True),
 ('reordered_edition',base_fr,[base_en[0],base_en[3],base_en[2],base_en[1],base_en[4]],base_fr[3],base_en[3],True),
 ('fr_missing',base_fr,base_en,'Un passage absent des archives historiques.',None,True),
 ('fr_repeated',base_fr+[base_fr[1]],base_en+[base_en[1]],base_fr[1],None,True),
 ('explicit_omission',base_fr,base_en,'La richesse du pays [...] augmenté rapidement.',None,True),
 ('en_missing',base_fr,base_en[:3]+base_en[4:],base_fr[3],None,True),
 ('unpaired_sections',base_fr,base_en,base_fr[3],None,False),
]
rows=[]
for id,f,e,q,expected,paired in CASES:
    fr,en=d(f,'fr'),d(e,'en')
    r=compare_methods(q,[fr],[en],[(fr,en,'synthetic_known_pair')] if paired else [])
    row={'id':id,'expected_kind':'positive' if expected else 'negative_or_unresolved','source_status':r['source_status'],'methods':{}}
    for name in ('lexical','alignment_paragraph','alignment_sentence'):
        cand=r['methods'][name]['candidates']; rank=None
        if expected:
            rank=next((i for i,c in enumerate(cand,1) if ' '+norm(expected)+' ' in ' '+norm(c['text'])+' '),None)
        row['methods'][name]={'rank':rank,'candidate_count':len(cand),'abstained':not cand,
           'top1_exact_normalized_boundary':bool(expected and cand and norm(expected)==norm(cand[0]['text']))}
    assert not r['auto_publish']; rows.append(row)
summary={}
for name in ('lexical','alignment_paragraph','alignment_sentence'):
    pos=[r['methods'][name] for r in rows if r['expected_kind']=='positive']; neg=[r['methods'][name] for r in rows if r['expected_kind']!='positive']
    summary[name]={'positive_n':len(pos),'top1_contains_expected':sum(r['rank']==1 for r in pos),'top5_contains_expected':sum(r['rank'] is not None for r in pos),'top1_exact_normalized_boundary':sum(r['top1_exact_normalized_boundary'] for r in pos),'negative_or_unresolved_n':len(neg),'negative_or_unresolved_with_candidates':sum(not r['abstained'] for r in neg)}
print(json.dumps({'scope':'synthetic structural scenarios; pair identity given; no actual document retrieval or model inference; containment not equivalence','summary':summary,'cases':rows},indent=2,ensure_ascii=False))
