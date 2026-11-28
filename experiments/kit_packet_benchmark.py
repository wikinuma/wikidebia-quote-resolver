"""Read-only real-debate packet projection experiment, not a publication Work."""
import argparse
from collections import Counter, defaultdict
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from historical_citations import load_json, first_candidate, second_candidate, verify_and_record, unique_index
from citation_registry import CitationRegistry, DECISIONS
from evidence_audit import Auditor, load_report
from grouped_review import pinned_loader
from pilot_full import PilotClient
from resolver import ROOT, WD_API
from work_library import WorkLibrary

# The pinned kit checkout is supplied on PYTHONPATH by the experiment workflow.
from experimental_prepared_citations import project, restore, canonical, prepared_index, source_parameters_key
from wikidebia_graph_extract import (PageRecord, PageMissingError, crawl_graph,
    parse_debate_wikitext, parse_argument_wikitext, iter_templates, _find_outer,
    DEBATE_TEMPLATE_KEYS, ARGUMENT_TEMPLATE_KEYS)
from wikidebia_content_review import _citation_records
from wikidebia_citation_passages import inventory_passages
from wikidebia_translation_review import (TRANSLATION_REVIEW_SCHEMA, _blank_argument,
    _translation_risk_profile, _build_translation_review_units)
import wikidebia_translation_packet as kit_packet
import wikidebia_translation_patch as kit_patch


def title_key(title):
    return title.replace('_', ' ').strip()


class PublicPages:
    """Bounded public GET-only adapter to the kit graph reader."""
    def __init__(self, client):
        self.client = client
        self.aliases = {}
        self.cache = {}

    def fetch_many(self, titles):
        titles = list(dict.fromkeys(titles))
        missing = [t for t in titles if t not in self.cache]
        for offset in range(0, len(missing), 15):
            batch = missing[offset:offset+15]
            q = self.client.api(WD_API, action='query', titles='|'.join(batch),
                                redirects=1, prop='revisions',
                                rvprop='ids|timestamp|content', rvslots='main')['query']
            mapping = {title_key(x['from']): title_key(x['to'])
                       for k in ('normalized','redirects') for x in q.get(k, [])}
            found = {title_key(p['title']): p for p in q['pages']}
            for t in batch:
                key = title_key(t)
                seen = set()
                while key in mapping and key not in seen:
                    seen.add(key)
                    key = mapping[key]
                p = found.get(key)
                if p is None or 'missing' in p:
                    self.cache[t] = None
                    continue
                rev, = p['revisions']
                raw = rev['slots']['main']['content']
                from urllib.parse import quote
                self.aliases[t] = p['title']
                self.cache[t] = PageRecord(t, p['title'], raw,
                    url='https://fr.wikidebates.org/wiki/'+quote(p['title'].replace(' ','_')),
                    revision_id=rev['revid'], revision_timestamp=rev['timestamp'])
        return {t: self.cache[t] for t in titles if self.cache[t] is not None}

    def fetch(self, title):
        rows = self.fetch_many([title])
        if title not in rows:
            raise PageMissingError(title)
        return rows[title]

    def parents(self, title):
        q = self.client.api(WD_API, action='query', list='backlinks', bltitle=title,
                            blnamespace=0, blfilterredir='nonredirects', bllimit=30)
        return [x['title'] for x in q['query']['backlinks']]


def find_debate(pages, titles):
    """Choose from actual argument edges, not arbitrary textual backlinks.
    The bounded search favours known reviewed citations; not a random sample.
    """
    counts = Counter(titles)
    starts = [t for t, _ in counts.most_common(15)]
    frontier = {t: {t} for t in starts}
    roots = defaultdict(set)
    visited = set()
    for _ in range(3):
        if not frontier:
            break
        links = defaultdict(set)
        for child in list(frontier)[:20]:
            if child in visited:
                continue
            visited.add(child)
            for parent in pages.parents(child):
                links[parent].add(child)
        next_frontier = defaultdict(set)
        for parent, record in pages.fetch_many(sorted(links)[:120]).items():
            calls = iter_templates(record.text)
            debate = _find_outer(calls, DEBATE_TEMPLATE_KEYS)
            argument = _find_outer(calls, ARGUMENT_TEMPLATE_KEYS)
            if debate:
                parsed = parse_debate_wikitext(record.text)
                children = parsed.pro + parsed.con
            elif argument:
                parsed = parse_argument_wikitext(record.text, stop_on_detailed_debate=True,
                                                  stop_on_existing_english_translation=False)
                children = parsed.justifications + parsed.objections
            else:
                continue
            names = {title_key(x.page) for x in children}
            evidence = set()
            for child in links[parent]:
                if title_key(child) in names:
                    evidence.update(frontier[child])
            if not evidence:
                continue
            if debate:
                roots[parent].update(evidence)
            else:
                next_frontier[parent].update(evidence)
        if roots:
            break
        frontier = next_frontier
    if not roots:
        raise ValueError('no_real_debate_found_in_bounded_backlinks')
    # Try the few smallest actual debate roots first, preserving the
    # number of reviewed descendants as a secondary ranking criterion.
    def rank_root(title):
        record = pages.fetch(title)
        parsed = parse_debate_wikitext(record.text)
        return (len({title_key(x.page) for x in parsed.pro + parsed.con}),
                -sum(counts[c] for c in roots[title]), title)
    return sorted(roots, key=rank_root), _


def load_history(args):
    first, fd = load_json(ROOT/'first-candidate-review.json')
    second, sd = load_json(ROOT/'second-pass-review-ledger.json')
    fr, frd = load_json(args.first_registry, second['previous_registry_sha256'])
    sr, srd = load_json(args.second_report, second['report_sha256'])
    old, _ = load_json(args.audit)
    if (fr['review_sha256'] != fd or fr['reports'] != first['reports']
            or sr['reports'] != first['reports'] or sr['registry_sha256'] != frd
            or old['schema'] != 'wikidebia-historical-prepared-audit-1'
            or old['provenance']['first']['report_sha256'] != frd
            or old['provenance']['second']['report_sha256'] != srd):
        raise ValueError('historical_input_binding_failed')
    cohorts = [load_report(p,d,'refined') for p,d in
               zip((args.development,args.holdout), first['reports'])]
    cases = {c['id']: c for report in cohorts for c in report['sample']}
    fdec, sdec = unique_index(first['entries'],'case_id'), unique_index(second['cases'],'case_id')
    frows, srows = unique_index(fr['cases'],'case_id'), unique_index(sr['cases'],'id')
    items = []
    seen = set()
    for row in old['cases']:
        cid, origin = row['case_id'], row['origin']
        if (row['status'] != 'historical_decision_recorded' or cid in seen
                or origin not in ('first','second')):
            continue
        case = cases[cid]
        decision = (fdec if origin=='first' else sdec)[cid]
        if decision['decision'] not in DECISIONS or row['source_ref'] != case['citation_ref']:
            raise ValueError('historical_case_scope_mismatch')
        candidate, fragments = (first_candidate(frows[cid],decision,case['citation_ref'])
            if origin=='first' else second_candidate(srows[cid],decision,case['citation_ref']))
        provenance = {'origin':origin, 'ledger':old['provenance'][origin], 'case_id':cid,
                      'decision':decision,'historical_candidate':candidate,
                      'source_ref':case['citation_ref']}
        items.append((case,decision,candidate,fragments,provenance))
        seen.add(cid)
    if not items:
        raise ValueError('no_historical_proofs')
    return items


def materialize(items, pages, audit, registry):
    loader = pinned_loader(audit.client)
    results = []
    issues = Counter()
    for case, decision, candidate, fragments, provenance in items:
        if title_key(case['wiki_title']) not in pages:
            continue
        ref = case['citation_ref']
        try:
            raw = loader(case['wiki_title'],ref['revision'])
            rec = verify_and_record(registry,raw,ref,audit,candidate,fragments,
                                    decision['decision'],provenance)
            doc = audit.document(rec['target_url'],'en')
            reconstructed = registry.reconstruct(raw,ref,doc)
            if reconstructed['status'] != 'reviewed_passage_reconstructed':
                issues[reconstructed['status']] += 1
                continue
            results.append(reconstructed)
        except (ValueError,KeyError,TypeError,AttributeError,IndexError) as exc:
            reason = str(exc)
            issues[reason if reason.replace('_','').isalnum() and len(reason)<100
                   else type(exc).__name__] += 1
    return results, dict(issues)


def source_rows(graph):
    """Build source-only experimental locks from actual public raw arguments.
    No editorial approval or synthetic attestations are asserted.
    """
    metas, contents = [], []
    skipped = 0
    for title, parsed in graph.parsed_arguments.items():
        record = graph.page_records[title]
        outer = _find_outer(iter_templates(record.text), ARGUMENT_TEMPLATE_KEYS)
        if outer is None:
            skipped += 1
            continue
        node_id = f'A{len(metas)+1:04d}'
        meta = {'entity_id':node_id,'canonical_title':title,'displayed_title':title,
                'page_origin':'preexisting','sections':[], 'keywords':[]}
        raw_citations = outer.get('citations')
        content = {'id':node_id,'page_origin':'preexisting','summary':outer.get('résumé','resume'),
                   'citations':_citation_records(raw_citations,node_id),
                   'citation_passages':inventory_passages(raw_citations,node_id)}
        metas.append(meta)
        contents.append(content)
    return metas,contents,skipped


def run_packets(metas, contents, reconstructed, tmp):
    """Use actual packet and draft constructors; only workspace/lock loading
    is substituted. No actual Work is initialized, edited or published.
    """
    work = tmp/'workspace'
    source = work/'content-reviewed-copy'
    source.mkdir(parents=True)
    (work/'reviews/en').mkdir(parents=True)
    args = []
    for m,c in zip(metas,contents):
        args.append({'id':c['id'],'review_risk':_translation_risk_profile(m,c),
                     'translation':_blank_argument(m,c)})
    units = _build_translation_review_units(args)
    by_id = {i:u['id'] for u in units for i in u['page_ids']}
    for row in args:
        row['review_unit_id'] = by_id[row['id']]
    review = {'schema':TRANSLATION_REVIEW_SCHEMA,'debate_id':'prepared_citation_experiment',
              'work_id':'EDIT-TEST-001','status':'draft','review_sha256':None,
              'prepared_content_reviewed_copy_sha256':'experiment',
              'arguments':args,'review_units':units,'vocabulary':[],
              'relation_labels':[],'section_mapping':{}}
    (work/'reviews/en/translation_review.json').write_text(json.dumps(review,ensure_ascii=False),encoding='utf-8')
    meta = {'content_reviewed_copy':{'tree_sha256':'experiment'}}
    metadata_lock = {'arguments':metas}
    content_lock = {'arguments':contents}
    baseline = projected = count = 0
    matched_units = 0
    per_unit = []
    with patch.object(kit_packet,'_load_workspace',return_value=(work,meta)), \
         patch.object(kit_packet,'_assert_content_copy',return_value=source), \
         patch.object(kit_packet,'_source_snapshot',return_value=({},metadata_lock,content_lock,{})), \
         patch.object(kit_patch,'_load_workspace',return_value=(work,meta)):
        for unit in units:
            uid=unit['id']
            kit_patch.prepare_patch(tmp,review['debate_id'],review['work_id'],uid)
            packet=json.loads((work/'audits/translation-packets'/f'{uid}.json').read_text())
            draft=json.loads((work/'reviews/en/unit-patches'/f'{uid}.json').read_text())
            view, edit, manifest, stats = project(packet,draft,reconstructed)
            n=stats['withheld_citations']
            baseline += stats['baseline_input_bytes']
            effective = stats['projected_input_bytes'] if n else stats['baseline_input_bytes']
            projected += effective
            count += n
            if n:
                matched_units += 1
                rebuilt=restore(packet,draft,edit,manifest,stats['manifest_sha256'])
                expected=copy.deepcopy(draft)
                for row in expected['arguments']:
                    selected=manifest['prepared'].get(row['id'],{})
                    for cit in row['translation'].get('citations') or []:
                        if cit['id'] in selected:
                            cit['translated_citation']=selected[cit['id']]['text']
                    if selected:
                        row['translation']['status']='pending'
                expected['unit_decision']['status']='pending'
                if expected != rebuilt:
                    raise ValueError('roundtrip_mismatch')
                per_unit.append({'unit':uid,'withheld':n,'baseline_bytes':stats['baseline_input_bytes'],
                                 'projected_bytes':effective,
                                 'saved_bytes':stats['baseline_input_bytes']-effective})
    return {'units':len(units),'units_with_prepared_citations':matched_units,
            'withheld_citations':count,'baseline_input_bytes':baseline,
            'projected_input_bytes':projected,'saved_input_bytes':baseline-projected,
            'saved_percent':round(100*(baseline-projected)/baseline,3) if baseline else None,
            'affected_units':per_unit,'roundtrip_exact_except_prepared_text':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('development','holdout','first-registry','second-report','audit','out'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    items=load_history(a)
    with tempfile.TemporaryDirectory(prefix='kit-packet-experiment-') as d:
        tmp=Path(d)
        client=PilotClient(tmp/'cache',budget=240,delay=.25)
        pages=PublicPages(client)
        roots, discovery_depth=find_debate(pages,[c['wiki_title'] for c,*_ in items])
        graph=None
        attempts=[]
        for root in roots[:3]:
            try:
                graph=crawl_graph(pages,debate_title=root,stop_on_detailed_debate=True,
                                  stop_on_existing_english_translation=False,max_pages=500,
                                  allow_missing=True,progress_every=0)
                attempts.append({'debate':root,'status':'missing_pages' if graph.missing_pages else 'complete',
                                 'missing_pages':len(graph.missing_pages)})
                break
            except RuntimeError as exc:
                if not str(exc).startswith('Limite de sécurité atteinte'):
                    raise
                attempts.append({'debate':root,'status':'page_cap_reached'})
        if graph is None:
            # The verified backlinks give the minimum depth needed to
            # include at least one prepared citation. Preserve the graph
            # safety cap and mark the result as a partial scope.
            graph=crawl_graph(pages,debate_title=roots[0],stop_on_detailed_debate=True,
                              stop_on_existing_english_translation=False,max_pages=500,
                              max_depth=discovery_depth,allow_missing=True,progress_every=0)
            attempts.append({'debate':roots[0],'status':'bounded_depth',
                             'max_depth':discovery_depth})
        metas,contents,skipped=source_rows(graph)
        with WorkLibrary(tmp/'works.sqlite3') as lib:
            reconstructed,issues=materialize(items,{title_key(x) for x in graph.page_records},
                                            Auditor(client),CitationRegistry(lib.db))
        stats=run_packets(metas,contents,reconstructed,tmp)
        total=sum(len(c['citations']) for c in contents)
        result={'schema':'wikidebia-kit-packet-experiment-1',
                'debate':graph.debate_title,'debate_revision':graph.debate_record.revision_id,
                'url':graph.debate_record.url,'arguments_in_graph':len(graph.parsed_arguments),
                'arguments_in_packets':len(metas),'arguments_skipped':skipped,
                'missing_pages':len(graph.missing_pages),'citations':total,
                'reconstructed_historical_receipts':len(reconstructed),'materialization_issues':issues,
                **stats,'network_requests':client.requests,'hosted_model_calls':0,
                'scope':'source-only reconstructed locks, real public debate graph; existing English frontier disabled',
                'graph_attempts':attempts,'depth_frontiers':len(graph.depth_frontiers),
                'complete_graph_within_dedicated_debate_frontiers':not bool(graph.depth_frontiers or graph.missing_pages or skipped),
                'provider_tokens_measured':False,'rights_approved':False,'remote_write_performed':False}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(result['withheld_citations']==0)


if __name__=='__main__':
    raise SystemExit(main())
