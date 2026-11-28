"""Conservative, reusable alignment of known parallel documents.

A variant or omission identifies an envelope for review, never an exact
quotation. Raw extracted wording and all alternatives remain available.
"""
from collections import OrderedDict
from alignment import align_sections, text_and_starts
from resolver import norm, sha
from alignment_store import alignment_key


def select_hit_metadata(source, expected_url):
    if source.get('status')=='exact_in_examined_documents':
        hits=source.get('occurrences',[])
        if len(hits)!=1 or source.get('occurrences_truncated'):
            return None,None
        hit=hits[0]
        if hit.get('url')!=expected_url:
            return None,None
        kind='exact_normalized_source'
    elif source.get('status')=='variant_review_required':
        hits=source.get('variants',[])
        # Multiple positions in the same document are ambiguous even when
        # mirrors contain the same words. Other differing normalized variants
        # also prevent an automatic priority decision.
        if len(hits)>=5 or any(d.get('truncated') for d in source.get('source_evidence_diagnostics',[])) or any(
                s.get('variant_search_truncated') for s in source.get('stages',[])):
            return None,None
        local=[h for h in hits if h.get('url')==expected_url]
        if len(local)!=1 or len({h.get('normalized_sha256') for h in hits})!=1:
            return None,None
        hit=local[0]
        if not hit.get('normalized_sha256'):
            return None,None
        kind=hit.get('match')
        if kind=='explicit_omission_review':
            if hit.get('token_edits')!=0 or len(hit.get('fragment_ranges',[]))<1:
                return None,None
        elif kind=='fragment_variant_omission_review':
            if (hit.get('similarity') or 0)<.95 or not hit.get('fragment_ranges'):
                return None,None
        elif kind not in ('approximate_tokens_review','multi_anchor_variant_review') or (hit.get('similarity') or 0)<.95:
            return None,None
    else:
        return None,None
    return hit,kind


def select_source_hit(source, docs, expected_url):
    hit,kind=select_hit_metadata(source,expected_url)
    if hit is None:
        return None,None
    matches=[d for d in docs if d.url==expected_url and d.digest==hit.get('sha256')]
    if len(matches)!=1:
        return None,None
    text,_=text_and_starts(matches[0]); lo,hi=hit.get('char_start'),hit.get('char_end')
    if type(lo) is not int or type(hi) is not int or not 0<=lo<hi<=len(text) or text[lo:hi]!=hit.get('text'):
        return None,None
    if kind!='exact_normalized_source' and sha(norm(text[lo:hi]).encode())!=hit['normalized_sha256']:
        return None,None
    return matches[0],{**hit,'evidence_kind':kind}


class DirectAlignmentCache:
    def __init__(self,max_entries=16,store=None):
        self.entries=OrderedDict()
        self.store=store
        self.max_entries=max_entries
        self.hits=0
        self.computations=0

    def get(self,fr,en,kind,max_cells=250_000):
        key=alignment_key(fr,en,kind,max_cells)
        if key in self.entries:
            self.hits+=1
            self.entries.move_to_end(key)
            return self.entries[key]
        result=self.store.load(key) if self.store is not None else None
        if result is None:
            result=align_sections(fr,en,kind,max_cells)
            self.computations+=1
            if self.store is not None:
                self.store.save(key,result)
        self.entries[key]=result
        if len(self.entries)>self.max_entries:
            self.entries.popitem(last=False)
        return result

    def candidate(self,source,fr_docs,en_docs,expected_source,route_method):
        result={'status':'not_applicable','candidates':[],'auto_publish':False,'rights':'unreviewed',
                'method':'cached_direct_span_alignment_v1'}
        if len(en_docs)!=1:
            return result
        fr,hit=select_source_hit(source,fr_docs,expected_source)
        if fr is None:
            return result
        en=en_docs[0]
        nf,ne=(sum(len(norm(b).split()) for b in d.blocks) for d in (fr,en))
        if min(nf,ne)<40 or not .5<=ne/max(1,nf)<=2:
            return {**result,'status':'incompatible_document_lengths'}
        result['pair']={'status':'bibliographic_document_hypothesis','fr_url':fr.url,'en_url':en.url,
                        'route_method':route_method,'length_ratio':round(ne/nf,4)}
        attempts=[]; etext,_=text_and_starts(en)
        for kind in ('sentence','paragraph'):
            aligned=self.get(fr,en,kind)
            attempts.append({'kind':kind,'status':aligned['status'],'alignment':{k:v for k,v in aligned.items() if k!='links'}})
            result['attempts']=attempts
            result['status']=aligned['status']
            if aligned['status']=='alignment_budget_exceeded':
                continue
            if aligned['status']!='aligned_candidates_only':
                break
            relevant=[l for l in aligned['links'] if l['fr_start'] is not None and
                      l['fr_start']<hit['char_end'] and l['fr_end']>hit['char_start']]
            if not relevant or any(l['en_start'] is None for l in relevant):
                result['status']='aligned_gap_or_no_candidate'
                continue
            lo,hi=relevant[0]['en_start'],relevant[-1]['en_end']
            words=len(norm(etext[lo:hi]).split()); source_words=len(norm(hit['text']).split())
            if not max(3,source_words*.25)<=words<=max(30,source_words*3):
                result['status']='implausible_aligned_span'
                continue
            omitted=hit['evidence_kind'] in ('explicit_omission_review','fragment_variant_omission_review')
            result['status']='review_required'
            result['candidates']=[{'url':en.url,'sha256':en.digest,'revision':en.revision,'char_start':lo,'char_end':hi,
                                  'text':etext[lo:hi],'links':relevant,'pair_route':route_method,
                                  'source_evidence_kind':hit['evidence_kind'],
                                  'source_boundary_ambiguous':hit.get('boundary_ambiguous',False),
                                  'omission_envelope_only':omitted,'alignment_unit':kind,
                                  'boundary_verified':False,'rights':'unreviewed'}]
            break
        return result


def review_packet(row):
    """One initial candidate, not an acceptance rule. Keep all alternatives
    in the parent result for a second pass when the reviewer rejects it.
    """
    aligned=row.get('section_alignment',{}).get('candidates',[])
    lexical=row.get('candidates',[])
    unique={}
    for c in aligned+lexical:
        unique.setdefault(sha(norm(c['text']).encode()),c)
    primary=aligned[:1] or lexical[:1]
    return {'method':'alignment_first_then_lexical_v1','candidates':primary,
            'deferred_distinct_candidates':max(0,len(unique)-len(primary)),
            'first_pass_only':True,'fallback_preserved':True,
            'auto_publish':False,'rights':'unreviewed'}
