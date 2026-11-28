"""Explicit alternative MIA PDF source editions; candidate generation only.

The PDF is never an English receipt target. Only the extracted French
section is used for location and alignment. Its offsets address extracted
blocks, not PDF bytes or printed page coordinates. No OCR or model calls.
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

from alignment import OMISSION, exact_occurrences
from alignment_store import document_identity
from bibliography import match_catalog
from navigation import canonical_mia, error_kind
from resolver import Document, norm, rank, sha, MAX_BYTES
from source_evidence import omission_candidates
from source_refinement import fragment_paths, long_variants
from window_rank import rank_short_windows


def load_pdf_catalog(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 'wikidebia-source-pdf-catalog-1':
        raise ValueError('invalid_source_pdf_catalog')
    seen = set()
    for e in data['entries']:
        for key in ('source_url', 'pdf_url', 'english_toc'):
            value = e[key]
            if not isinstance(value, str) or canonical_mia(value) != value:
                raise ValueError('invalid_pdf_catalog_url')
        if (e['source_url'] in seen or not e['source_url'].startswith('https://www.marxists.org/francais/')
                or not e['pdf_url'].startswith('https://www.marxists.org/francais/')
                or not e['pdf_url'].endswith('.pdf') or e['pdf_url'] == e['source_url']
                or '/francais/' in e['english_toc']):
            raise ValueError('invalid_pdf_catalog_scope')
        seen.add(e['source_url'])
        if not all(isinstance(e.get(k), str) and e[k].strip()
                   for k in ('source_title','authors','rights_note','scope')):
            raise ValueError('missing_pdf_catalog_evidence')
        if e['pdf_url'] not in e['evidence'] or e['english_toc'] not in e['evidence']:
            raise ValueError('missing_pdf_catalog_links')
        if type(e.get('skip_pages')) is not int or not 0 <= e['skip_pages'] <= 20:
            raise ValueError('invalid_pdf_frontmatter')
        if type(e.get('footer_page_offset')) is not int or not -20 <= e['footer_page_offset'] <= 20:
            raise ValueError('invalid_pdf_footer_offset')
        sections = e['sections']
        if not 2 <= len(sections) <= 100 or sections[-1]['en_url'] is not None:
            raise ValueError('missing_pdf_terminal_heading')
        headings = [norm(x['heading']) for x in sections]
        if any(not x for x in headings) or len(headings) != len(set(headings)):
            raise ValueError('ambiguous_pdf_headings')
        targets = [x['en_url'] for x in sections[:-1]]
        if any(not isinstance(u,str) or canonical_mia(u)!=u or '/francais/' in u
               or u == e['english_toc'] or u.endswith('.pdf') for u in targets):
            raise ValueError('invalid_pdf_section_target')
        if len(set(targets)) != len(targets):
            raise ValueError('duplicate_pdf_target')
    return tuple(data['entries'])


def extract_pdf(body):
    if not body.startswith(b'%PDF-') or not 0 < len(body) <= MAX_BYTES:
        raise ValueError('invalid_source_pdf_body')
    # stdin/stdout, no shell, no temporary publication or OCR.
    result = subprocess.run(['pdftotext', '-enc', 'UTF-8', '-', '-'],
                            input=body, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=60, check=True)
    if not 0 < len(result.stdout) <= 8_000_000:
        raise ValueError('source_pdf_text_size')
    text = result.stdout.decode('utf-8', errors='strict')
    if not norm(text):
        raise ValueError('source_pdf_no_text_layer')
    return text


def section_documents(text, entry, digest):
    # Keep paragraph and line structure. Drop only a standalone final
    # integer matching the documented printed-page offset on each page.
    pages = text.split('\f')
    if pages and not pages[-1].strip():
        pages.pop()
    if not entry['skip_pages'] < len(pages) <= 1000:
        raise ValueError('source_pdf_page_count')
    lines, page_numbers = [], []
    footer_matches = 0
    for i, page in enumerate(pages):
        if i < entry['skip_pages']:
            continue
        part = page.splitlines()
        last = next((j for j in range(len(part)-1,-1,-1) if part[j].strip()),None)
        if last is not None and part[last].strip() == str(i + entry['footer_page_offset']):
            footer_matches += 1
            part = part[:last] + part[last+1:]
        lines.extend(part)
        page_numbers.extend([i+1]*len(part))
        lines.append('')
        page_numbers.append(i+1)
    # Explicit full heading(s), not a fuzzy title or a chapter number alone.
    # Allow a heading wrapped onto two physical lines.
    locations = []
    for s in entry['sections']:
        needle = norm(s['heading'])
        matches = []
        for i,line in enumerate(lines):
            if not line.strip():
                continue
            if norm(line) == needle:
                matches.append((i,i+1))
            elif i+1 < len(lines) and lines[i+1].strip() and norm(line+' '+lines[i+1]) == needle:
                matches.append((i,i+2))
        if len(matches) != 1:
            raise ValueError('source_pdf_heading_missing_or_ambiguous')
        locations.append(matches[0])
    if any(a[1]>=b[0] for a,b in zip(locations,locations[1:])):
        raise ValueError('source_pdf_heading_order')
    out = []
    for s, (start,after), (end,_) in zip(entry['sections'],locations,locations[1:]):
        blocks = [re.sub(r'\s+',' ',p).strip()
                  for p in re.split(r'\n\s*\n','\n'.join(lines[after:end])) if p.strip()]
        if sum(len(norm(b).split()) for b in blocks) < 40:
            raise ValueError('source_pdf_section_too_short')
        d = Document(entry['pdf_url'],blocks,digest,s['heading'],headings=(s['heading'],))
        out.append((s,d,{'heading':s['heading'],'pdf_page_start':page_numbers[start],
                         'pdf_page_end':page_numbers[end-1],
                         'extracted_document':document_identity(d),
                         'coordinate_scope':'extracted_section_blocks_v1'}))
    return out, {'pages':len(pages),'removed_numbered_footers':footer_matches}


def locate(quote, sections):
    """Unique exact or conservative near-exact source location in a
    documented section. Ambiguity/truncation is not proof of absence."""
    found = []
    for section,doc,scope in sections:
        hits,truncated = exact_occurrences(quote,doc)
        if truncated:
            return None,'source_pdf_exact_truncated'
        found.extend((section,doc,scope,h,'exact_in_examined_documents') for h in hits)
    if len(found)>1:
        return None,'source_pdf_ambiguous'
    if found:
        return found[0],'exact'
    diagnostics = []
    for section,doc,scope in sections:
        if OMISSION.search(quote):
            r = omission_candidates(quote,[doc])
            if not r['candidates'] and not r['truncated']:
                r = fragment_paths(quote,[doc])
        else:
            r = long_variants(quote,[doc])
        diagnostics.append(r.get('status'))
        if r['truncated'] or r.get('qualifying_hits',0)>len(r['candidates']) and len(r['candidates'])>=5:
            return None,'source_pdf_variant_truncated'
        for h in r['candidates']:
            exact_omission = h.get('match')=='explicit_omission_review' and h['token_edits']==0
            if (exact_omission or (h.get('similarity') or 0)>=.95) and not h.get('boundary_ambiguous'):
                found.append((section,doc,scope,h,'variant_review_required'))
    if len(found)>1:
        return None,'source_pdf_ambiguous'
    return (found[0],'variant') if found else (None,'source_pdf_unconfirmed')


class SourcePDFs:
    def __init__(self, client, entries):
        self.client = client
        self.entries = tuple(entries)
        self.cache = {}

    def prepare(self, entry):
        """Fail before a campaign retry if the alternative edition cannot
        be parsed into its documented scope. Reuse the verified extraction."""
        pdf = entry['pdf_url']
        if pdf not in self.cache:
            body,final = self.client.get(pdf)
            if final != pdf:
                raise ValueError('source_pdf_redirect')
            self.cache[pdf] = section_documents(extract_pdf(body),entry,sha(body))
        sections,extraction = self.cache[pdf]
        return {**extraction,'url':pdf,'sections':len(sections),
                'body_sha256':sections[0][1].digest}

    def retrieve(self, row, quote, alignment_cache, fetch_leaf):
        entry = match_catalog(row['navigation'].get('input',{}),self.entries)
        if entry is None:
            return None
        pdf = entry['pdf_url']
        row['alternate_source_pdf'] = {'url':pdf,'scope':entry['scope']}
        evidence = row['alternate_source_pdf']
        try:
            self.prepare(entry)
            sections,extraction = self.cache[pdf]
        except Exception as exc:
            row['status']='source_pdf_unavailable'
            evidence['error']=error_kind(exc)
            return row
        evidence.update(extraction)
        hit,reason = locate(quote,sections)
        evidence['location_status']=reason
        if hit is None:
            row['status']=reason
            return row
        section,fr,scope,h,state = hit
        evidence.update(scope)
        # The existing source checks refer to the exact extracted section
        # identity. Do not mislabel it as an HTML page or full PDF offset.
        row['source'] = {'status':state,'occurrences':[h] if state.startswith('exact_') else [],
                         'variants':[h] if state=='variant_review_required' else [],
                         'stages':[{'provider':'mia','root':pdf,
                                    'scope':'documented_pdf_section',
                                    'pages':[{'url':pdf,'sha256':fr.digest}],
                                    'issues':[]}]}
        url=section['en_url']
        info={'provider':'mia','root':url,'scope':'documented_pdf_source_chapter',
              'pages':[],'issues':[]}
        row['target_attempts']=[info]
        try:
            en=fetch_leaf(url)
            if en.url!=url or not en.blocks:
                raise ValueError('source_pdf_target_mismatch')
        except Exception as exc:
            info['issues'].append({'kind':'target_error','error':error_kind(exc)})
            row['status']='source_pdf_target_unavailable'
            return row
        info['pages']=[{'url':en.url,'sha256':en.digest,'blocks':len(en.blocks)}]
        aligned=alignment_cache.candidate(row['source'],[fr],[en],pdf,
                                          'documented_alternate_pdf_section')
        candidates=rank(quote,[en])
        if not candidates:
            candidates=rank_short_windows(quote,[en])['candidates']
        row.update(target_provider='mia',target_scope=info['scope'],
                   section_alignment=aligned,candidates=candidates,
                   status='source_pdf_candidate_review_required' if aligned['candidates'] or candidates
                   else 'source_pdf_no_target_candidate',
                   auto_publish=False,rights='unreviewed')
        return row
