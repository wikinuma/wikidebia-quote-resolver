"""Local proof registry for prepared, published MIA-linked translations.

This is an explicit editorial handoff, not a retrieval shortcut. The caller
must read the pinned wiki revision and independently review the actual passage.
Documents passed here are checked for exact identity and content, not freshness
of their network fetch. No publication, rights decision or semantic inference.
"""
import json

from alignment import OMISSION, text_and_starts
from alignment_store import document_identity, encode
from evidence_audit import document_provider, span_receipt, valid_digest
from navigation import citation_record, templates
from resolver import sha

DECISIONS = frozenset({'complete_equivalent','boundary_adjustment','omission_adjustment'})


def fingerprint(obj):
    return sha(encode(obj).encode())


def require_digest(value):
    if not valid_digest(value):
        raise ValueError('invalid_digest')
    return value


def source_identity(raw_page, ref):
    """Check the exact raw occurrence; identify content independently of page
    title, revision, position and parameter order. Preserve *all* parameter
    values, including the literal link and edition metadata, in the identity.
    """
    index, revision = ref.get('template_index'), ref.get('revision')
    if type(index) is not int or index < 0 or type(revision) is not int or revision <= 0:
        raise ValueError('invalid_source_reference')
    for i,t in enumerate(templates(raw_page,{'citation'})):
        if i != index:
            continue
        rec = citation_record(t,i,include_text=True,include_link_policy=True)
        if any(rec[k] != ref.get(k) for k in ('template_sha256','quote_sha256')):
            raise ValueError('source_reference_mismatch')
        if rec['link_policy']['route'] != 'mia' or not rec['quote_words']:
            raise ValueError('source_outside_mia_policy_or_empty')
        params = sorted((str(p.name).strip(),str(p.value).strip()) for p in t.params)
        # citation_record already rejects normalized duplicate parameters.
        quote_fields = [v for k,v in params if k=='citation']
        if len(quote_fields)!=1:
            raise ValueError('missing_literal_citation_field')
        identity = {
            'schema':'wikidebia-mia-prepared-source-1',
            'provider':'mia',
            'mia_url':rec['link_policy']['mia_url'],
            'quote_sha256':rec['quote_sha256'],
            'quote_wikitext_sha256':sha(quote_fields[0].encode()),
            'metadata_sha256':fingerprint([(k,v) for k,v in params if k!='citation']),
        }
        return {**identity,'source_key':fingerprint(identity),
                'has_explicit_ellipsis':bool(OMISSION.search(rec['text'])),
                'occurrence':{'revision':revision,'template_index':index,
                              'template_sha256':rec['template_sha256']}}
    raise ValueError('citation_index_not_found')


def target_identity(doc, candidate):
    """Check the exact English candidate and return only coordinates/hashes."""
    document_provider(doc.url,'en')
    require_digest(doc.digest)
    if doc.revision is not None and (type(doc.revision) is not int or doc.revision<=0):
        raise ValueError('invalid_document_revision')
    if candidate.get('url')!=doc.url or candidate.get('revision')!=doc.revision:
        raise ValueError('candidate_document_identity_mismatch')
    receipt=span_receipt(doc,candidate)
    if receipt['status']!='verified_document_and_span':
        raise ValueError('candidate_document_or_span_mismatch')
    result={'document':document_identity(doc),
            'char_start':receipt['char_start'],'char_end':receipt['char_end'],
            'text_sha256':receipt['actual_text_sha256']}
    return {**result,'candidate_key':fingerprint(result)}


def selected_fragments(source, target, doc, decision, ranges):
    if decision not in DECISIONS or not isinstance(ranges,list) or not ranges:
        raise ValueError('not_an_accepted_equivalence')
    length=target['char_end']-target['char_start']
    text,_=text_and_starts(doc)
    fragments=[]
    previous=None
    for pair in ranges:
        if not isinstance(pair,list) or len(pair)!=2:
            raise ValueError('invalid_fragment')
        a,b=pair
        if type(a) is not int or type(b) is not int or not 0<=a<b<=length:
            raise ValueError('invalid_fragment_coordinates')
        if previous is not None and a<=previous:
            raise ValueError('overlapping_or_adjacent_fragments')
        lo,hi=target['char_start']+a,target['char_start']+b
        if not text[lo:hi].strip():
            raise ValueError('empty_fragment')
        fragments.append({'char_start':lo,'char_end':hi,
                          'text_sha256':sha(text[lo:hi].encode())})
        previous=b
    if decision=='complete_equivalent' and ranges!=[[0,length]]:
        raise ValueError('complete_span_required')
    if decision=='boundary_adjustment' and (len(ranges)!=1 or ranges==[[0,length]]):
        raise ValueError('boundary_adjustment_requires_strict_subspan')
    if decision=='omission_adjustment' and (len(ranges)<2 or not source['has_explicit_ellipsis']):
        raise ValueError('explicit_source_omission_required')
    return fragments


def prepare_review_source(raw_page,ref,doc,candidate):
    """Identifiers to bind a review packet before its editorial judgment."""
    return {'source':source_identity(raw_page,ref),'target':target_identity(doc,candidate)}


def check_target(doc,target):
    candidate={'url':target['document']['url'],'revision':target['document']['revision'],
               'sha256':target['document']['body_sha256'],'text_sha256':target['text_sha256'],
               'char_start':target['char_start'],'char_end':target['char_end']}
    if target_identity(doc,candidate)!=target:
        raise ValueError('target_evidence_changed')


def passage_identity(entry):
    """A new exact receipt of the same pinned wiki passage is not a
    conflicting translation. Body/parser digests still bind each receipt.
    Do not equate unversioned documents or different revisions/coordinates.
    """
    document = entry['target']['document']
    revision = document['revision']
    if (type(revision) is int and revision > 0
            and document_provider(document['url'], 'en') == 'wikirouge'):
        document = {'url': document['url'], 'revision': revision}
    return fingerprint({'document': document, 'fragments': entry['fragments']})


class CitationRegistry:
    """Use the same private SQLite connection as WorkLibrary.

    Accepted editorial receipts only, append-only and idempotent. Conflicting
    target editions/fragments require explicit resolution. No lookup is wired
    to retrieval or production yet, and evaluation-only labels are not seeded.
    """
    def __init__(self, db):
        self.db=db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS prepared_citations_v1 '
                       '(entry_sha256 TEXT PRIMARY KEY, source_key TEXT NOT NULL, entry TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS prepared_citations_source_v1 '
                       'ON prepared_citations_v1(source_key)')

    def record(self,raw_page,ref,doc,candidate,review):
        source=source_identity(raw_page,ref)
        target=target_identity(doc,candidate)
        if review.get('source_key')!=source['source_key'] or review.get('candidate_key')!=target['candidate_key']:
            raise ValueError('review_identity_mismatch')
        packet=require_digest(review.get('review_packet_sha256'))
        reviewer=review.get('reviewer')
        if not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>120:
            raise ValueError('missing_reviewer_identity')
        decision=review.get('decision')
        fragments=selected_fragments(source,target,doc,decision,review.get('ranges'))
        entry={'schema':'wikidebia-prepared-citation-receipt-1','source':source,
               'target':target,'decision':decision,'fragments':fragments,
               'review_packet_sha256':packet,'reviewer':reviewer,
               'semantic_status':'editorial_decision_recorded',
               'rights':'unreviewed','auto_publish':False}
        raw=encode(entry)
        key=sha(raw.encode())
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO prepared_citations_v1 VALUES (?,?,?)',
                            (key,source['source_key'],raw))
        return {'entry_sha256':key,'source_key':source['source_key'],'status':'recorded'}

    def lookup(self,raw_page,ref,*,preferred_document=None):
        source=source_identity(raw_page,ref)
        grouped={}
        for key,raw in self.db.execute(
                'SELECT entry_sha256,entry FROM prepared_citations_v1 WHERE source_key=? ORDER BY entry_sha256',
                (source['source_key'],)):
            if sha(raw.encode())!=key:
                raise ValueError('registry_integrity_mismatch')
            entry=json.loads(raw)
            if (entry.get('schema')!='wikidebia-prepared-citation-receipt-1'
                    or entry['source']['source_key']!=source['source_key']
                    or entry['decision'] not in DECISIONS
                    or entry['rights']!='unreviewed' or entry['auto_publish'] is not False):
                raise ValueError('registry_scope_mismatch')
            # Different reviewers or candidate envelopes can agree on exactly
            # the same edition and selected spans. Do not confuse that with
            # conflicting published translations.
            proof=passage_identity(entry)
            grouped.setdefault(proof,[]).append({'entry_sha256':key,**entry})
        if not grouped:
            return {'status':'miss','source_key':source['source_key']}
        if len(grouped)!=1:
            return {'status':'ambiguous_reviewed_passages','source_key':source['source_key'],
                    'distinct_passages':len(grouped)}
        entries=next(iter(grouped.values()))
        # Prefer a receipt bound to the exact supplied document. Never
        # reconstruct a passage merely from the semantic group identity.
        chosen=next((e for e in entries if e['target']['document']==preferred_document),entries[0])
        return {'status':'reviewed_receipt_found','source_key':source['source_key'],
                'receipt':chosen,'receipt_count':len(entries),
                'rights':'unreviewed','auto_publish':False}

    def reconstruct(self,raw_page,ref,doc):
        """Private materialization only; never claim that doc is freshly read."""
        try:
            preferred=document_identity(doc)
        except (AttributeError, TypeError, ValueError):
            preferred=None
        result=self.lookup(raw_page,ref,preferred_document=preferred)
        if result['status']!='reviewed_receipt_found':
            return result
        entry=result['receipt']
        try:
            check_target(doc,entry['target'])
            source=source_identity(raw_page,ref)
            lo=entry['target']['char_start']
            ranges=[[f['char_start']-lo,f['char_end']-lo] for f in entry['fragments']]
            if selected_fragments(source,entry['target'],doc,entry['decision'],ranges)!=entry['fragments']:
                raise ValueError('fragment_evidence_changed')
        except (ValueError,KeyError,TypeError,AttributeError):
            return {'status':'target_evidence_changed','source_key':result['source_key'],
                    'rights':'unreviewed','auto_publish':False}
        text,_=text_and_starts(doc)
        return {**result,'status':'reviewed_passage_reconstructed',
                'fragments':[{**f,'text':text[f['char_start']:f['char_end']]} for f in entry['fragments']],
                'new_semantic_review':False,'fresh_read_not_established_here':True,
                'editorial_review_required':True,'rights':'unreviewed','auto_publish':False}
