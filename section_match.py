"""Review-only chapter pairing by bilingual heading signatures.

A small reusable vocabulary maps ordinary headings, not work IDs or gold quote
locations. Headings must match independently of the selected quotation.
These are structural hypotheses, not verified edition equivalence.
"""
from __future__ import annotations
import re
from dataclasses import replace
from collections import defaultdict
from resolver import norm, clean
from alignment import aligned_candidates, OMISSION

# Each group denotes a common heading concept, not a title or a work mapping.
# Deliberately conservative; unknown vocabulary is preserved, never dropped.
CONCEPTS={
    'competition':('concurrence','competition'),
    'struggle':('lutte','luttes','struggle','struggles'),
    'class':('classe','classes','class'),
    'bourgeois':('bourgeois','bourgeoise','bourgeoisie'),
    'proletarian':('proletaire','proletaires','proletariat','proletarian','proletarians'),
    'communist':('communiste','communistes','communist','communists'),
    'socialist':('socialiste','socialistes','socialist','socialists'),
    'worker':('travailleur','travailleurs','ouvrier','ouvriers','worker','workers','working'),
    'war':('guerre','guerres','war','wars'),
    'peasant':('paysan','paysans','peasant','peasants'),
    'production':('production','productions'),
    'property':('propriete','proprietes','property'),
    'industry':('industrie','industries','industry'),
    'industrial':('industriel','industriels','industrielle','industrielles','industrial'),
    'agricultural':('agricole','agricoles','agricultural'),
    'revolution':('revolution','revolutions'),
    'political':('politique','politiques','political'),
    'economic':('economique','economiques','economic'),
    'movement':('mouvement','mouvements','movement','movements'),
    'result':('resultat','resultats','result','results'),
    'town':('ville','villes','town','towns','city','cities'),
    'great':('grand','grands','grande','grandes','great'),
    'private':('prive','prives','privee','privees','private'),
    'capitalist':('capitaliste','capitalistes','capitalist','capitalists'),
    'literature':('litterature','literature'),
    'attitude':('attitude','attitudes'),
    'social':('social','sociaux','sociale','sociales'),
    'condition':('condition','conditions'),
}
TERM={word:key for key,words in CONCEPTS.items() for word in words}
STOP=set('le la les l du des de d un une au aux en et a the of and in on to for'.split())
GENERIC={'introduction','preface','prefaces','avant propos','conclusion','notes','annexe','appendix','appendices','sommaire','contents','table des matieres','index','chapter','chapitre'}


def heading_signature(label):
    # A number may disambiguate a repeated book title, but is never evidence
    # by itself. Chapter numbering is not assumed identical across editions.
    label=re.sub(r'\s*\[\d+\]\s*$','',label)
    m=re.match(r'^\s*(?:(?:chapitre|chapter)\s+)?([IVXLCDM]+|\d+)\s*[:.\-–)]\s*',label,re.I)
    number=m.group(1).lower() if m else None
    if m: label=label[m.end():]
    n=norm(label)
    if not n or n in GENERIC: return (),number
    words=[w for w in n.split() if w not in STOP]
    if not 1<=len(words)<=12 or any(w.isdigit() for w in words):
        return (),number
    return tuple(sorted(TERM.get(w,w) for w in words)),number


def extract_headings(root):
    out=[]
    for node in root.find_all(['h1','h2','h3','h4','p']):
        if node.name=='p':
            classes=' '.join(node.get('class',[])).lower()
            styled=bool(re.search(r'\b(chapter|title|heading|subtitle|section)\b',classes))
            centered=str(node.get('align','')).lower()=='center'
            emphasized=node.find(['b','strong']) is not None
            if not styled and not (centered and emphasized): continue
        # Section edit links and note markers are not heading text.
        text=clean(node.get_text(' ',strip=True))
        text=re.sub(r'\s*\[\s*(?:edit(?: source)?|modifier(?: le code)?)\s*\]\s*$','',text,flags=re.I)
        if 1<=len(norm(text).split())<=20 and len(text)<=220 and text not in out:
            out.append(text)
    return tuple(out[:80])


def numbered_equal(a,b):
    if not a or not b: return False
    romans={x.lower():str(i) for i,x in enumerate(('I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','XIII','XIV','XV','XVI','XVII','XVIII','XIX','XX'),1)}
    return romans.get(a,a)==romans.get(b,b)


def find_section_pair(fr,en_docs,work_titles=(),authors=(),fr_inventory=()):
    excluded={heading_signature(t)[0] for t in (*work_titles,*authors) if t}
    fr_labels=[(label,*heading_signature(label)) for label in fr.headings]
    # Book-wide titles, author names and other labels shared with a different
    # FR chapter are excluded unless the same explicit chapter number occurs
    # on both sides (the heading words must still match).
    shared_fr=set()
    for other in fr_inventory:
        if other.url!=fr.url:
            shared_fr.update(heading_signature(x)[0] for x in other.headings)
    matches={}
    fn=sum(len(norm(b).split()) for b in fr.blocks)
    for en in en_docs:
        n=sum(len(norm(b).split()) for b in en.blocks)
        if min(fn,n)<40 or not .50<=n/max(1,fn)<=2.0: continue
        labels=[(label,*heading_signature(label)) for label in en.headings]
        evidence=[]
        for fl,fs,fi in fr_labels:
            if not fs: continue
            for el,es,ei in labels:
                if fs!=es: continue
                # Single-token headings must have an explicit bilingual
                # concept, not merely a shared surname or a generic term.
                if len(fs)==1 and fs[0] not in CONCEPTS: continue
                if fs in excluded or fs in shared_fr:
                    if not numbered_equal(fi,ei): continue
                if (fl,el) not in {(x['fr_heading'],x['en_heading']) for x in evidence}:
                    evidence.append({'fr_heading':fl,'en_heading':el,'normalized_concepts':list(fs),'same_explicit_number':numbered_equal(fi,ei)})
        if evidence:
            matches[en.url]=(en,evidence,round(n/fn,4))
    result={'status':'no_heading_pair','possible_sections':len(matches),'auto_publish':False}
    if len(matches)!=1:
        if matches: result.update(status='ambiguous_heading_pair',candidate_urls=sorted(matches))
        return None,result
    target,evidence,ratio=next(iter(matches.values()))
    result.update(status='heading_pair_hypothesis',fr_url=fr.url,en_url=target.url,
                  length_ratio=ratio,heading_evidence=evidence,
                  scope='bilingual_heading_signature_not_verified_parallel_edition')
    return target,result


def automatic_alignment(quote,source,fr_docs,en_docs,work_titles,authors,max_cells=250000):
    result={'status':'not_applicable','candidates':[],'auto_publish':False,
            'rights':'unreviewed','method':'heading_hypothesis_sentence_alignment_v1'}
    hits=source.get('occurrences',[])
    if source.get('status')!='exact_in_examined_documents' or len(hits)!=1 or source.get('occurrences_truncated') or OMISSION.search(quote):
        return result
    hit=hits[0]
    fs=[d for d in fr_docs if d.url==hit['url'] and d.digest==hit['sha256']]
    if len(fs)!=1: return {**result,'status':'source_document_unavailable'}
    fr=fs[0]
    target,pair=find_section_pair(fr,en_docs,work_titles,authors,fr_docs)
    result['pair']=pair
    if target is None: return {**result,'status':pair['status']}
    # A long chapter may exceed the sentence DP budget. Fall back to
    # paragraph alignment without increasing the maximum matrix size.
    attempts=[]
    for kind in ('sentence','paragraph'):
        aligned=aligned_candidates(quote,fr,target,kind,max_cells=max_cells)
        attempts.append({'kind':kind,'status':aligned['status'],'alignment':aligned.get('alignment')})
        if aligned['status']!='alignment_budget_exceeded': break
    result['attempts']=attempts
    result['alignment']=aligned.get('alignment')
    result['status']=aligned['status']
    result['candidates']=[{**c,'pair_route':'heading_pair_hypothesis','boundary_verified':False,'rights':'unreviewed','alignment_unit':kind} for c in aligned['candidates']]
    return result
