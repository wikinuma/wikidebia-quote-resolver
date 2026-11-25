"""Experimental bibliographic navigation through Wikirouge, without guessed MIA URLs.

Inputs are original Citation templates; the held-out answer fields in cases-v02
are used only after routing. A route is not an approved quotation or edition.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from resolver import API, WD_API, ROOT, clean, norm, sha
from pilot_full import PilotClient, WR, html_url, soup, wr_title, wr_interlanguage, replay_mismatches

MIA_HOSTS={'marxists.org','www.marxists.org'}
MIA_PATH=re.compile(r"/[A-Za-z0-9._~/%()+,-]+")
URL_PATTERN=re.compile(r'(?<![\w./:@-])(?:https?://|//)?(?:www\.)?marxists\.org/[^\s<>{}\[\]|"\x27]+',re.I)


def canonical_mia(value):
    if not isinstance(value,str): return None
    try:
        value=value.strip()
        if value.startswith('//'): value='https:'+value
        elif re.match(r'^(?:www\.)?marxists\.org/',value,re.I): value='https://'+value
        p=urlsplit(value)
        if (p.scheme not in ('http','https') or p.hostname not in MIA_HOSTS or p.username or p.password
                or p.port not in (None,80,443) or p.query or not MIA_PATH.fullmatch(p.path)
                or any(x in ('.','..') for x in p.path.split('/'))):
            return None
        return 'https://www.marxists.org'+p.path
    except ValueError:
        return None


def mia_urls(value):
    return sorted({u for x in URL_PATTERN.findall(value) if (u:=canonical_mia(x))})


def safe_title(title):
    return isinstance(title,str) and 0<len(title)<=250 and not re.search(r'[\[\]{}|<>\x00-\x1f]',title) and '::' not in title and not title.startswith(('!','~','+','-','*','?'))


def raw_parse(client,endpoint,title=None,oldid=None):
    if (title is None)==(oldid is None): raise ValueError('exactly_one_parse_selector_required')
    params={'action':'parse','prop':'wikitext|revid'}
    if title is not None:
        if not safe_title(title): raise ValueError('unsafe_title')
        params.update(page=title,redirects=1)
    else: params['oldid']=oldid
    p=client.api(endpoint,**params)['parse']
    raw=p['wikitext']
    if isinstance(raw,dict): raw=raw['*']
    if not isinstance(raw,str) or not isinstance(p.get('revid'),int) or p['revid']<=0:
        raise ValueError('missing_raw_revision')
    if oldid is not None and p['revid']!=oldid: raise ValueError('revision_mismatch')
    return p['title'],p['revid'],raw


def templates(raw,names):
    import mwparserfromhell
    nodes=mwparserfromhell.parse(raw).filter_templates(recursive=True)
    for t in nodes:
        name=norm(str(t.name).replace('_',' '))
        if name.startswith('modele '): name=name[7:]
        elif name.startswith('template '): name=name[9:]
        if name in names: yield t


def fields(t):
    out={}
    for p in t.params:
        name=norm(str(p.name).replace('_',' '))
        if name in out: raise ValueError('duplicate_template_parameter')
        out[name]=p.value
    return out


def plain(value):
    if value is None: return ''
    return clean(soup(value.strip_code()).get_text(' ',strip=False))


def extract_citation(raw,selector):
    needle=norm(selector)
    if len(needle.split())<3: raise ValueError('selector_too_short')
    matches=[]
    for t in templates(raw,{'citation'}):
        f=fields(t); q=plain(f.get('citation'))
        if ' '+needle+' ' in ' '+norm(q)+' ':
            matches.append((f,q))
    if len(matches)!=1: raise ValueError('raw_quote_selector_matches='+str(len(matches)))
    f,q=matches[0]
    return {'quote_sha256':sha(q.encode()),'quote_words':len(norm(q).split()),
            'authors':plain(f.get('auteurs'))[:300],
            'work':plain(f.get('ouvrage'))[:300],
            'article':plain(f.get('article'))[:300],
            'date':plain(f.get('date'))[:120],
            'mia_urls':mia_urls(str(f.get('lien',''))),
            'link_present':bool(str(f.get('lien','')).strip()),
            'source_kind':'raw_citation_template_not_generated_translation'}


def text_metadata(raw):
    found=list(templates(raw,{'text','subpage'}))
    if len(found)!=1:
        return {'status':'missing_or_ambiguous_text_template','mia_urls':[],'authors':''}
    f=fields(found[0])
    authors=plain(f.get('auteur s') or f.get('author s') or f.get('auteurs') or f.get('authors'))
    return {'status':'raw_text_metadata','mia_urls':mia_urls(str(f.get('mia url',''))),
            'authors':authors[:300],
            'date':plain(f.get('date ecriture') or f.get('date of writing'))[:120],
            'interlanguages_parameter_present':bool(str(f.get('interlanguages','')).strip())}


def smw_rows(client,lang,query,limit=8):
    data=client.api(API[lang],action='ask',query=query+'|?MIA URL|limit='+str(limit))
    raw=data['query']['results']
    if not isinstance(raw,(dict,list)): raise ValueError('invalid_smw_results')
    rows=[]
    for r in raw.values() if isinstance(raw,dict) else raw:
        if not isinstance(r,dict) or r.get('namespace')!=0 or r.get('exists') is False: continue
        title=r.get('fulltext')
        if not safe_title(title): continue
        printouts=r.get('printouts',{})
        values=printouts.get('MIA URL',printouts.get('MIA_URL',[]))
        if not isinstance(values,list): raise ValueError('invalid_smw_url_printout')
        urls=sorted({u for v in values if isinstance(v,str) and (u:=canonical_mia(v))})
        rows.append({'title':title,'mia_urls':urls})
    return {'rows':sorted(rows,key=lambda r:r['title']),
            'truncated':bool(data.get('query-continue-offset')) or len(raw)>=limit}


def reverse_lookup(client,url):
    canonical=canonical_mia(url)
    if not canonical: raise ValueError('invalid_mia_url')
    p=urlsplit(canonical)
    alternatives=[scheme+'://'+host+p.path for scheme in ('https','http') for host in ('www.marxists.org','marxists.org')]
    r=smw_rows(client,'fr','[[MIA URL::'+'||'.join(alternatives)+']]')
    r['matches']=[x for x in r['rows'] if canonical in x['mia_urls']]
    return r


def title_lookup(client,title):
    if not safe_title(title): return {'method':'invalid_title','titles':[]}
    p=client.api(API['fr'],action='query',titles=title,redirects=1,prop='info')['query']['pages']
    hits=[x['title'] for x in p if x.get('ns')==0 and 'missing' not in x and 'invalid' not in x and safe_title(x.get('title'))]
    if hits: return {'method':'exact_api_title_or_redirect','titles':sorted(set(hits))}
    r=client.api(API['fr'],action='query',list='search',srsearch=title,srnamespace=0,srlimit=20)
    hits=sorted({x['title'] for x in r['query']['search'] if x.get('ns')==0 and safe_title(x.get('title')) and norm(x['title'])==norm(title)})
    return {'method':'normalized_title_search','titles':hits,'search_truncated':bool(r.get('continue'))}


def error_kind(exc):
    # No raw wikitext, source prose, or server error bodies in public reports.
    if isinstance(exc,ValueError) and re.fullmatch(r'[a-z_]+(?:=\d+)?',str(exc)): return str(exc)
    return type(exc).__name__


def wr_meta(client,lang,title):
    result={'status':'metadata_unavailable','mia_urls':[],'authors':''}
    try:
        actual,rev,raw=raw_parse(client,API[lang],title=title)
        if actual.replace('_',' ')!=title.replace('_',' '): raise ValueError('metadata_title_mismatch')
        result.update(text_metadata(raw),revision=rev,raw_sha256=sha(raw.encode()))
        result['raw_mia_urls']=result['mia_urls'][:]
    except Exception as exc: result['raw_error']=error_kind(exc)
    if not result['mia_urls'] and safe_title(title):
        try:
            r=smw_rows(client,lang,'[['+title+']]',limit=3)
            hits=[x for x in r['rows'] if x['title'].replace('_',' ')==title.replace('_',' ')]
            result['smw_by_title']={'matches':len(hits),'truncated':r['truncated']}
            if len(hits)==1 and not r['truncated']:
                result['mia_urls']=hits[0]['mia_urls']; result['mia_url_method']='semantic_property_by_title'
        except Exception as exc: result['smw_error']=error_kind(exc)
    elif result['mia_urls']: result['mia_url_method']='raw_text_template'
    return result


def reciprocal_roots(client,title):
    # A semantic URL may identify a chapter. Follow only the explicit work
    # hierarchy; never infer that chapter numbers agree across translations.
    root=title.split('/',1)[0]
    body,fr=client.get(html_url(WR+'fr/',root))
    ft=wr_title(fr,'fr')
    if ft is None or '/' in ft: raise ValueError('unexpected_french_root')
    targets=wr_interlanguage(soup(body),fr,'en')
    if len(targets)!=1: raise ValueError('missing_or_ambiguous_english_root')
    body,en=client.get(targets[0]); et=wr_title(en,'en')
    if et is None or '/' in et: raise ValueError('unexpected_english_root')
    back={wr_title(u,'fr') for u in wr_interlanguage(soup(body),en,'fr')}
    if back!={ft}: raise ValueError('nonreciprocal_work_langlink')
    return {'fr_title':ft,'en_title':et,'fr_url':fr,'en_url':en,
            'reciprocal':True,'scope':'work_identity_candidate_not_section_or_quote_identity'}


def author_check(citation,work):
    def words(s): return set(norm(s).split())-{'auteur','auteurs','author','authors','et','and'}
    a,b=words(citation),words(work)
    if not a or not b: return 'metadata_missing'
    if a==b: return 'same_normalized_author_tokens'
    if a<=b or b<=a: return 'partial_author_overlap'
    return 'author_tokens_differ'


def discover_case(client,case):
    row={'id':case['id'],'auto_publish':False,'rights':'unreviewed','status':'unresolved','attempts':[]}
    try:
        title,rev,raw=raw_parse(client,WD_API,title=case['wiki_title'])
        metadata=extract_citation(raw,case['selector'])
        row['input']={**metadata,'wiki_title':title,'revision':rev,'raw_sha256':sha(raw.encode())}
    except Exception as exc:
        row.update(status='input_unavailable',error=error_kind(exc)); return row
    selected=None
    if len(metadata['mia_urls'])==1:
        try:
            r=reverse_lookup(client,metadata['mia_urls'][0]); row['attempts'].append({'method':'semantic_mia_url_reverse_lookup',**r})
            if len(r['matches'])==1 and not r['truncated']:
                selected=r['matches'][0]['title']; row['route_method']='semantic_exact_mia_url'
            elif len(r['matches'])>1 or r['truncated']:
                row['status']='ambiguous_semantic_url'; return row
        except Exception as exc: row['attempts'].append({'method':'semantic_mia_url_reverse_lookup','error':error_kind(exc)})
    if selected is None:
        # Only metadata from the original Citation is used, not work_fr or
        # expected_en_mia from the benchmark catalog.
        for field in ('work','article'):
            candidate=metadata[field]
            if not candidate: continue
            try:
                r=title_lookup(client,candidate); row['attempts'].append({'field':field,**r})
                if len(r['titles'])>1 or r.get('search_truncated'):
                    row['status']='ambiguous_title_search'; return row
                if len(r['titles'])==1:
                    selected=r['titles'][0]; row['route_method']='citation_'+field+'_'+r['method']; break
            except Exception as exc: row['attempts'].append({'field':field,'error':error_kind(exc)})
    if selected is None:
        row['status']='work_not_resolved'; return row
    row['selected_fr_title']=selected
    try:
        route=reciprocal_roots(client,selected); row['route']=route
        fr=wr_meta(client,'fr',route['fr_title']); en=wr_meta(client,'en',route['en_title'])
        row['work_metadata']={'fr':fr,'en':en}
        row['author_check']=author_check(metadata['authors'],fr['authors'])
        row['citation_mia_url_in_root_metadata']=bool(set(metadata['mia_urls'])&set(fr['mia_urls']))
        row['status']='wikirouge_route_candidate'
        if len(en['mia_urls'])==1:
            requested=en['mia_urls'][0]
            try:
                body,final=client.get(requested)
                if canonical_mia(final) is None: raise ValueError('unexpected_mia_final_host')
                row['mia_target']={'requested_url':requested,'final_url':final,'sha256':sha(body),
                                   'url_accessible':True,'scope':'metadata_link_only_not_quote_match'}
                row['status']='mia_route_candidate'
            except Exception as exc: row['mia_target']={'requested_url':requested,'url_accessible':False,'error':error_kind(exc)}
        elif len(en['mia_urls'])>1: row['mia_target']={'status':'multiple_english_mia_urls'}
    except Exception as exc: row.update(status='work_link_unconfirmed',error=error_kind(exc))
    return row


def evaluate_route(row,case):
    """Gold fields are read only after discover_case returned."""
    expected=case.get('work_fr','')
    known=case.get('expected_en_mia')
    fr=row.get('route',{}).get('fr_title','')
    actual=row.get('mia_target',{}).get('final_url')
    return {'known_work_title_matches':bool(fr and expected and norm(fr)==norm(expected)),
            'expected_mia_url_available':bool(known),
            'target_exact_url_match':bool(actual and known and canonical_mia(actual)==canonical_mia(known)),
            'target_same_directory_only':bool(actual and known and canonical_mia(actual) and canonical_mia(known)
                                             and urlsplit(actual).path.rsplit('/',1)[0]==urlsplit(known).path.rsplit('/',1)[0])}


def run_navigation(client,cases):
    probes={}
    for lang in ('fr','en'):
        try:
            p=smw_rows(client,lang,'[[MIA URL::+]]',limit=3)
            probes[lang]={'status':'available',**p}
        except Exception as exc: probes[lang]={'status':'unavailable','error':error_kind(exc)}
    rows=[]
    for case in cases:
        row=discover_case(client,case)
        row['evaluation']=evaluate_route(row,case)
        rows.append(row)
    counts={status:sum(r['status']==status for r in rows) for status in sorted({r['status'] for r in rows})}
    return {'schema':'wikidebia-metadata-navigation-0.1','offline':client.offline,'auto_publish':False,
            'smw_property_probes':probes,'cases':rows,'counts':counts,'requests':client.requests,
            'cache_hits':client.hits,'http_events':client.events,
            'scope':'bibliographic routes only; no candidate quotation approved'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path,default=ROOT/'.pilot-cache')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'navigation.json')
    p.add_argument('--case',action='append',default=[])
    p.add_argument('--budget',type=int,default=150)
    p.add_argument('--replay',action='store_true')
    a=p.parse_args()
    if a.budget<1: p.error('positive budget required')
    cases=json.loads((ROOT/'cases-v02.json').read_text())['cases']
    cases=[c for c in cases if not a.case or c['id'] in a.case]
    if not cases or set(a.case)-{c['id'] for c in cases}: p.error('unknown or empty case')
    c=PilotClient(a.cache,budget=a.budget); report=run_navigation(c,cases)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={k:v for k,v in report.items() if k!='http_events'}
    rc=int(not any(r['status'] in ('mia_route_candidate','wikirouge_route_candidate') for r in report['cases']))
    if a.replay:
        expected={}
        for e in c.events:
            old=expected.get(e['request_url'])
            if old and any(old.get(k)!=e.get(k) for k in ('status','sha256','final_url')):
                raise RuntimeError('inconsistent_live_response')
            expected[e['request_url']]=e
        replay_client=PilotClient(a.cache,budget=a.budget,offline=True,expected_events=expected)
        replay=run_navigation(replay_client,cases)
        same=all(report[k]==replay[k] for k in ('smw_property_probes','cases','counts','auto_publish'))
        mismatches=replay_mismatches(expected,replay_client.events)
        integrity={'results_match':same,'response_mismatches':mismatches,'network_requests':replay_client.requests}
        replay['replay_integrity']=integrity
        a.out.with_name(a.out.stem+'-replay.json').write_text(json.dumps(replay,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        summary['replay_integrity']=integrity
        rc=max(rc,int(not same or bool(mismatches) or replay_client.requests!=0))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return rc


if __name__=='__main__': raise SystemExit(main())
