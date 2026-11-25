#!/usr/bin/env python3
"""Read-only full-work pilot. No external model, no wiki writes, no production integration."""
from __future__ import annotations
import argparse
import base64
import json
import re
from difflib import SequenceMatcher
import time
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup,NavigableString
from resolver import API, WD_API, ROOT, Client, Document, norm, clean, sha, valid_url, MAX_BYTES
from alignment import exact_occurrences, text_and_starts, tokens_with_offsets, OMISSION
from compare import compare_methods, evaluation, page_pairs
from embedding import LocalEncoder

MIA = 'https://www.marxists.org'
WR = 'https://wikirouge.net/texts/'
WD = 'https://fr.wikidebates.org/wiki/'
FR_MAN = '/francais/marx/works/1847/00/kmfe18470000'
EN_MAN = '/archive/marx/works/1848/communist-manifesto/'
FR_ENG = '/francais/engels/works/1845/03/fe_18450315'
EN_ENG = '/archive/marx/works/1845/condition-working-class/'
PREFACES = [
    '/francais/marx/works/1872/06/kmfe18720624.htm',
    '/francais/marx/works/1882/01/kmfe18820121.htm',
    '/francais/engels/works/1883/06/fe18830628.htm',
    '/francais/engels/works/1888/01/fe18880130.htm',
    '/francais/engels/works/1890/05/fe18900501.htm',
    '/francais/engels/works/1892/02/fe18920210.htm',
    '/francais/engels/works/1893/02/fe18930201.htm',
]
# Root pages and structural scope observed in the public tables of contents.
# The test quotation's location / English anchor is not a discovery input.
WORKS = {
    'manifesto': {
        'wr_fr': 'Manifeste du parti communiste',
        'fr': {'index': MIA + FR_MAN + '.htm', 'expected': 11,
               'required': PREFACES + [FR_MAN + x + '.htm' for x in 'abcd']},
        'en': {'index': MIA + EN_MAN + 'index.htm', 'expected': 5,
               'required': [EN_MAN + x + '.htm' for x in ('preface','ch01','ch02','ch03','ch04')]},
    },
    'engels-condition': {
        'wr_fr': 'La situation de la classe laborieuse en Angleterre',
        'fr': {'index': MIA + FR_ENG + '.htm', 'expected': 19,
               'required': [FR_ENG+'_'+x+'.htm' for x in ('pref','avt','pr2','intro','src',*[str(i) for i in range(1,13)])] + ['/francais/engels/works/1887/01/fe_18870126.htm','/francais/engels/works/1892/07/fe_18920721.htm']},
        'en': {'index': MIA + EN_ENG + 'index.htm', 'expected': 20,
               'required': [EN_ENG+f'ch{i:02}.htm' for i in range(15)] + ['/archive/marx/works/1885/03/01.htm','/archive/marx/works/1886/02/25.htm','/archive/marx/works/1887/01/26.htm','/archive/marx/works/1892/01/11.htm','/archive/marx/works/1892/07/21.htm']},
    },
}
# These labels identify sections independently of any quotation or gold answer.
ENGELS_SECTION_LABELS = [
    ('Introduction','Introduction'),
    ('Le prolétariat industriel','The Industrial Proletariat'),
    ('Les grandes villes','The Great Towns'),
    ('La concurrence','Competition'),
    ('L’immigration irlandaise','Irish Immigration'),
    ('Les résultats','Results'),
    ("Les différentes branches d'industrie",'Single Branches of Industry'),
    ("Les autres branches d’industrie",'The Remaining Branches of Industry'),
    ('Mouvements ouvriers','Labour Movements'),
    ('Le prolétariat des mines','The Mining Proletariat'),
    ('Le prolétariat agricole','The Agricultural Proletariat'),
    ('L’attitude de la bourgeoisie à l’égard du prolétariat','The Attitude of the Bourgeoisie Towards the Proletariat'),
]
CASES = {'manifesto-i':'manifesto','manifesto-ii':'manifesto',
         'manifesto-preface-1883':'manifesto','engels-competition':'engels-condition'}


def canonical_url(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))


def html_url(base, title):
    return base+quote(title.replace(' ','_'),safe='/()')


class PilotClient(Client):
    """Audit every lookup; optional strict offline replay of the existing HTTP cache."""
    def __init__(self,cache,budget=220,delay=.5,offline=False,expected_events=None):
        super().__init__(cache,budget,delay)
        self.offline=offline
        self.events=[]
        self.failed_urls={}
        self.cache_bytes_read=0
        self.expected_events=expected_events
        if expected_events is not None and not offline: raise ValueError('replay requires offline mode')

    def get(self,url,params=None):
        if not valid_url(url): raise ValueError(f'URL not allowed: {url}')
        prepared=requests.Request('GET',url,params=sorted((params or {}).items())).prepare().url
        event={'request_url':prepared}
        self.events.append(event)
        before=self.hits
        try:
            expected=None
            if self.expected_events is not None:
                if prepared not in self.expected_events: raise RuntimeError('replay_unrecorded_request')
                expected=self.expected_events[prepared]
                if expected['status']=='error': raise RuntimeError(expected['error'])
            if prepared in self.failed_urls:
                event['memoized_failure']=True
                raise RuntimeError(self.failed_urls[prepared])
            if self.offline:
                p=self.cache/(sha(prepared.encode())+'.json')
                if not p.is_file(): raise RuntimeError('offline_cache_miss')
                c=json.loads(p.read_text())
                body=base64.b64decode(c['body'],validate=True)
                final=c['url']
                if c['request_url']!=prepared or not valid_url(final) or sha(body)!=c['sha256'] or not 0<len(body)<=MAX_BYTES:
                    raise RuntimeError('invalid_offline_cache_entry')
                if expected and (expected['sha256']!=sha(body) or expected['final_url']!=final):
                    raise RuntimeError('replay_response_mismatch')
                self.hits+=1
            else:
                body,final=super().get(url,params)
            if not 0<len(body)<=MAX_BYTES or not valid_url(final): raise RuntimeError('invalid_response')
            event.update(status='ok',final_url=final,sha256=sha(body),bytes=len(body),cache_hit=self.hits>before)
            if event['cache_hit']: self.cache_bytes_read+=len(body)
            return body,final
        except Exception as exc:
            event.update(status='error',error=str(exc))
            self.failed_urls.setdefault(prepared,str(exc))
            raise


def soup(body):
    # JSON parse output and contemporary wiki HTML are UTF-8. For legacy
    # MIA documents, fall back to the declared/detected HTML encoding.
    if isinstance(body,bytes):
        try: body=body.decode('utf-8-sig')
        except UnicodeDecodeError: pass
    return BeautifulSoup(body,'html.parser')


def body_root(s):
    return s.select_one('.mw-parser-output') or s.select_one('#mw-content-text') or s.body or s


def visible_text(node):
    # A word split by inline emphasis must not acquire a new space. BRs are
    # explicit boundaries. Whitespace normalization is recorded, not claimed
    # to reproduce an edition's printed typography.
    for b in node.find_all('br'): b.replace_with('\n')
    return clean(node.get_text('',strip=False))


def remove_nontext(root):
    for n in root.select('script,style,nav,.mw-editsection,.reference,.references,.toc,.navbox,.metadata'):
        if n.parent: n.decompose()
    # Obvious inline note markers on old MIA HTML; not arbitrary numbers.
    for n in root.find_all('sup'):
        if n.parent and n.find('a',href=True) and re.fullmatch(r'\s*[\[(]?\d+[\])]?\s*',n.get_text()):
            n.decompose()
    for n in root.find_all('a',href=True):
        if not n.parent or not n['href'].startswith('#'): continue
        if re.fullmatch(r'\s*\[\s*\d+\s*\]\s*',n.get_text()): n.decompose(); continue
        # Some legacy pages place the square brackets outside the link.
        if re.fullmatch(r'\s*\d+\s*',n.get_text()):
            left,right=n.previous_sibling,n.next_sibling
            if isinstance(left,NavigableString) and isinstance(right,NavigableString) and re.search(r'\[\s*$',str(left)) and re.match(r'^\s*\]',str(right)):
                left.replace_with(re.sub(r'\[\s*$','',str(left))); right.replace_with(re.sub(r'^\s*\]','',str(right))); n.decompose()


def extract_blocks(body,mediawiki=False):
    s=soup(body); root=body_root(s); remove_nontext(root)
    if mediawiki:
        for n in root.select('table.infobox,table.wikitable,table.metadata'):
            if n.parent: n.decompose()
    blocks=[]
    for n in root.find_all(['p','li','blockquote']):
        if n.find_parent(['p','li','blockquote']) is not None: continue
        # Preserve paragraphs within quotes rather than concatenating them.
        children=n.find_all('p') if n.name=='blockquote' else []
        for x in children or [n]:
            text=visible_text(x)
            if text: blocks.append(text)
    return blocks


def html_revision(s):
    revs=set()
    for a in s.select('li#t-permalink a[href],a.mw-permalink[href]'):
        m=re.search(r'[?&]oldid=(\d+)',a['href'])
        if m: revs.add(int(m.group(1)))
    for script in s.find_all('script'):
        m=re.search(r'"wgRevisionId"\s*:\s*(\d+)',script.get_text())
        if m and int(m.group(1)): revs.add(int(m.group(1)))
    return next(iter(revs)) if len(revs)==1 else None


def wiki_input(client,case,api_ok):
    attempts=[]; title=case['wiki_title']; selector=norm(case['selector'])
    if not selector: raise ValueError('empty selector')
    for method in (['api_parse','public_html'] if api_ok else ['public_html']):
        try:
            if method=='api_parse':
                data=client.api(WD_API,action='parse',page=title,redirects=1,prop='text|revid')['parse']
                body=data['text']; body=body['*'] if isinstance(body,dict) else body
                body=body.encode('utf-8'); rev=data.get('revid'); url=html_url(WD,data.get('title',title))
            else:
                body,url=client.get(html_url(WD,title)); rev=html_revision(soup(body))
                if urlsplit(url).hostname!='fr.wikidebates.org': raise ValueError('unexpected final wiki host')
            s=soup(body); root=body_root(s); remove_nontext(root); matches=[]
            for n in root.find_all('blockquote'):
                if n.find_parent('blockquote'): continue
                text=clean(' '.join(visible_text(p) for p in n.find_all('p')) if n.find('p') else visible_text(n)).strip('«»“” ')
                if ' '+selector+' ' in ' '+norm(text)+' ': matches.append(text)
            if len(matches)!=1: raise ValueError(f'quote_selector_matches={len(matches)}')
            text=matches[0]
            return text,{'method':method,'url':url,'revision':rev,'response_sha256':sha(body),
                         'quote_sha256':sha(text.encode()),'quote_words':len(norm(text).split()),
                         'attempts':attempts,'source_kind':'rendered_blockquote_not_raw_template','text':text}
        except Exception as exc:
            attempts.append({'method':method,'error':str(exc)})
    raise RuntimeError(json.dumps(attempts,ensure_ascii=False))


def index_links(body,base,spec):
    """Select the bounded work-specific TOC scope, including cross-directory prefaces."""
    required=set(spec['required']); urls={}
    for a in soup(body).find_all('a',href=True):
        u=canonical_url(urljoin(base,a['href'])); p=urlsplit(u)
        if not valid_url(u) or p.hostname not in ('marxists.org','www.marxists.org') or p.query: continue
        path=unquote(p.path)
        if path not in required and not (spec.get('pattern') and re.fullmatch(spec['pattern'],path)): continue
        if path==urlsplit(spec['index']).path: continue
        urls.setdefault(path,{'url':MIA+path,'label':clean(a.get_text(' ',strip=True))})
    missing=sorted(required-set(urls)); issues=[]
    if missing: issues.append({'kind':'required_toc_links_missing','paths':missing})
    if len(urls)!=spec['expected']:
        issues.append({'kind':'toc_count_changed','expected':spec['expected'],'actual':len(urls)})
    return list(urls.values()),issues


def mia_work(client,work,max_pages):
    docs={'fr':[],'en':[]}; info={'scope':'reviewed_toc_rules_not_arbitrary_site_crawl','sides':{},'issues':[]}
    for lang in ('fr','en'):
        spec=work[lang]; side={'index':spec['index'],'expected_unique_pages':spec['expected'],'pages':[]}; info['sides'][lang]=side
        try:
            body,final=client.get(spec['index']); side['index_sha256']=sha(body)
            links,issues=index_links(body,final,spec); info['issues'] += [dict(x,lang=lang) for x in issues]
            side['discovered']=links
            if len(links)>max_pages:
                info['issues'].append({'kind':'page_limit','lang':lang,'count':len(links),'limit':max_pages})
            for row in links[:max_pages]:
                try:
                    data,url=client.get(row['url']); s=soup(data)
                    if urlsplit(url).hostname not in ('marxists.org','www.marxists.org') or unquote(urlsplit(url).path)!=unquote(urlsplit(row['url']).path): raise ValueError('redirect outside expected MIA document')
                    blocks=extract_blocks(data)
                    if not blocks: raise ValueError('no_extracted_blocks')
                    d=Document(url,blocks,sha(data),clean(s.title.get_text()) if s.title else '')
                    docs[lang].append(d)
                    side['pages'].append({'requested_url':row['url'],'url':url,'sha256':d.digest,'blocks':len(blocks),'words':sum(len(norm(b).split()) for b in blocks)})
                except Exception as exc:
                    info['issues'].append({'kind':'fetch_or_extract_error','lang':lang,'url':row['url'],'error':str(exc)})
        except Exception as exc:
            info['issues'].append({'kind':'index_error','lang':lang,'error':str(exc)})
    info['scope_complete']=bool(docs['fr'] and docs['en'] and not info['issues'])
    return docs,info


def mia_pairs(wid,docs,info):
    # Work-level section mappings, not case-specific expected URLs.
    pairs=[]
    def get(lang,path):
        hits=[d for d in docs[lang] if unquote(urlsplit(d.url).path)==path]
        return hits[0] if len(hits)==1 else None
    if wid=='manifesto':
        candidates=[(FR_MAN+x+'.htm',EN_MAN+f'ch{i:02}.htm') for i,x in enumerate('abcd',1)]
    else:
        def find(lang,label):
            rows=info['sides'].get(lang,{}).get('discovered',[])
            matches=[unquote(urlsplit(r['url']).path) for r in rows if norm(r['label'])==norm(label)]
            return matches[0] if len(matches)==1 else None
        candidates=[(find('fr',f),find('en',e)) for f,e in ENGELS_SECTION_LABELS]
    for f,e in candidates:
        fd=get('fr',f); ed=get('en',e)
        if fd and ed: pairs.append((fd,ed,'work_level_section_mapping_not_gold_passage'))
    return pairs


def wr_title(url,lang):
    p=urlsplit(url); prefix='/texts/'+lang+'/'
    if p.hostname!='wikirouge.net' or p.query or not p.path.startswith(prefix): return None
    title=unquote(p.path[len(prefix):]).replace('_',' ')
    if not title or title.startswith('w/') or ':' in title.split('/')[0]: return None
    return title


def wr_interlanguage(s,base,lang):
    out=set()
    for a in s.select('a.interlanguage-link-target[href],.interlanguage-link a[href],a[hreflang][href]'):
        u=canonical_url(urljoin(base,a['href']))
        if valid_url(u) and wr_title(u,lang): out.add(u)
    return sorted(out)


def wr_public_work(client,work,max_pages):
    docs={'fr':[],'en':[]}; info={'scope':'public_html_visible_subpage_links_only','scope_complete':False,'issues':[],'routes':[],'sides':{}}
    interlinks={}; root_fr=html_url(WR+'fr/',work['wr_fr']); roots={'fr':root_fr}
    for lang in ('fr','en'):
        if lang not in roots: break
        queue=[roots[lang]]; seen=set(); root_title=None; records=[]; info['sides'][lang]=records
        while queue and len(seen)<max_pages:
            url=queue.pop(0)
            if url in seen: continue
            seen.add(url)
            try:
                data,final=client.get(url); title=wr_title(final,lang)
                if title is None: raise ValueError('unexpected Wikirouge final URL')
                if root_title is None: root_title=title
                if title!=root_title and not title.startswith(root_title+'/'): raise ValueError('redirect outside work')
                s=soup(data); body=body_root(s); blocks=extract_blocks(data,mediawiki=True)
                d=Document(final,blocks,sha(data),title,html_revision(s)); docs[lang].append(d)
                targets=wr_interlanguage(s,final,'en' if lang=='fr' else 'fr'); interlinks[final]=targets
                records.append({'url':final,'title':title,'revision':d.revision,'sha256':d.digest,'blocks':len(blocks),'words':sum(len(norm(b).split()) for b in blocks),'interlanguage':targets})
                if lang=='fr' and len(seen)==1:
                    if len(targets)==1:
                        roots['en']=targets[0]; info['routes'].append({'fr':final,'en':targets[0],'method':'public_html_langlink'})
                    else: info['issues'].append({'kind':'missing_or_ambiguous_root_langlink','count':len(targets)})
                for a in body.find_all('a',href=True):
                    u=canonical_url(urljoin(final,a['href'])); t=wr_title(u,lang)
                    if valid_url(u) and t and t.startswith(root_title+'/') and u not in seen and u not in queue: queue.append(u)
            except Exception as exc: info['issues'].append({'kind':'fetch_error','url':url,'error':str(exc)})
        if queue: info['issues'].append({'kind':'page_limit','lang':lang,'remaining':len(queue)})
    # HTML alone cannot prove that no unlinked subpage exists.
    return docs,info,interlinks


def wr_api_work(client,work,max_pages):
    # Use the existing API implementation for true prefix enumeration. Fetch
    # text with the same extraction policy as the other provider.
    from resolver import work_title,subpages
    docs={'fr':[],'en':[]}; info={'scope':'api_prefix_enumeration','issues':[],'routes':[],'sides':{}}
    try:
        fr,targets=work_title(client,work['wr_fr'])
        info['native_langlink']={'fr_title':fr,'en_titles':targets}
        if not fr or len(targets)!=1: raise ValueError('missing_or_ambiguous_work_langlink')
        info['routes']=[{'fr_title':fr,'en_title':targets[0],'method':'api_langlinks'}]
        for lang,title in [('fr',fr),('en',targets[0])]:
            rows=[]; info['sides'][lang]=rows
            names,truncated=subpages(client,lang,title,max_pages)
            if truncated: info['issues'].append({'kind':'page_limit','lang':lang})
            for name in names:
                try:
                    data=client.api(API[lang],action='parse',page=name,redirects=1,prop='text|revid')['parse']
                    raw=data['text']; raw=raw['*'] if isinstance(raw,dict) else raw; body=raw.encode('utf-8')
                    actual=data['title']
                    if actual!=title and not actual.startswith(title+'/'): raise ValueError('redirect outside work')
                    d=Document(html_url(WR+lang+'/',actual),extract_blocks(body,True),sha(body),actual,data.get('revid'))
                    docs[lang].append(d); rows.append({'url':d.url,'title':actual,'sha256':d.digest,'revision':d.revision,'blocks':len(d.blocks),'words':sum(len(norm(b).split()) for b in d.blocks)})
                except Exception as exc: info['issues'].append({'kind':'fetch_error','lang':lang,'title':name,'error':str(exc)})
    except Exception as exc: info['issues'].append({'kind':'api_work_error','error':str(exc)})
    # A TOC-only root is allowed, but both languages must have actual text.
    for lang in ('fr','en'):
        if not any(d.blocks for d in docs[lang]): info['issues'].append({'kind':'no_extracted_text','lang':lang})
        titles=[d.title for d in docs[lang]]
        if len(titles)!=len(set(titles)): info['issues'].append({'kind':'duplicate_resolved_title','lang':lang})
    info['scope_complete']=bool(docs['fr'] and docs['en'] and not info['issues'])
    return docs,info,{}



def wr_hybrid_work(client,work,max_pages,native_attempt):
    """Use reciprocally linked public roots, then verify the prefix inventory.

    Reuse the already downloaded public HTML (including cache). Only fetch
    missing inventory pages by API. A native langlink failure is diagnostic,
    not fatal when independent root links and inventory are both confirmed.
    This certifies a bounded prefix inventory, not textual equivalence.
    """
    docs,public,links=wr_public_work(client,work,max_pages)
    public_issues=list(public['issues'])
    info={**public,'scope':'html_roots_api_prefix_enumeration','scope_complete':False,
          'issues':[],'html_attempt_issues':public_issues,'api_attempt':native_attempt}
    if len(public['routes'])!=1:
        info['issues'].append({'kind':'unconfirmed_html_root_pair'})
        return docs,info,links
    route=public['routes'][0]
    roots={l:wr_title(route[l],l) for l in ('fr','en')}
    # Compare canonical decoded titles, not literal URL encodings.
    en_roots=[d for d in docs['en'] if d.title==roots['en']]
    fr_roots=[d for d in docs['fr'] if d.title==roots['fr']]
    if (not all(roots.values()) or len(fr_roots)!=1 or len(en_roots)!=1 or
        {wr_title(u,'fr') for u in links.get(en_roots[0].url,[])}!={roots['fr']}):
        info['issues'].append({'kind':'missing_or_ambiguous_reciprocal_root_link'})
        return docs,info,links
    native=native_attempt.get('native_langlink',{})
    if ((native.get('fr_title') and native['fr_title']!=roots['fr']) or
        (native.get('en_titles') and set(native['en_titles'])!={roots['en']})):
        info['issues'].append({'kind':'native_and_html_root_conflict'})
        return docs,info,links
    info['routes']=[{**route,'method':'reciprocal_html_root_langlinks','reciprocal':True}]
    info['prefix_inventory']={}
    from resolver import subpages
    for lang in ('fr','en'):
        title=roots[lang]
        try:
            names,truncated=subpages(client,lang,title,max_pages)
            info['prefix_inventory'][lang]={'titles':names,'truncated':truncated}
            if truncated: info['issues'].append({'kind':'page_limit','lang':lang})
            existing={d.title:d for d in docs[lang]}
            if len(existing)!=len(docs[lang]):
                info['issues'].append({'kind':'duplicate_resolved_title','lang':lang})
            extras=sorted(set(existing)-set(names))
            if extras:
                info['issues'].append({'kind':'html_titles_outside_api_inventory','lang':lang,'titles':extras})
            for name in names:
                if name in existing and existing[name].blocks: continue
                try:
                    data=client.api(API[lang],action='parse',page=name,redirects=1,prop='text|revid')['parse']
                    raw=data['text']; raw=raw['*'] if isinstance(raw,dict) else raw; body=raw.encode('utf-8')
                    if data['title']!=name: raise ValueError('inventory_title_redirect_or_mismatch')
                    rev=data.get('revid'); blocks=extract_blocks(body,True)
                    if not isinstance(rev,int) or rev<=0: raise ValueError('missing_revision')
                    d=Document(html_url(WR+lang+'/',name),blocks,sha(body),name,rev)
                    existing[name]=d
                    info['sides'][lang]=[r for r in info['sides'][lang] if r['title']!=name]
                    info['sides'][lang].append({'url':d.url,'title':name,'revision':rev,'sha256':d.digest,'blocks':len(blocks),'words':sum(len(norm(b).split()) for b in blocks),'retrieval':'api_parse'})
                except Exception as exc:
                    info['issues'].append({'kind':'inventory_fetch_error','lang':lang,'title':name,'error':str(exc)})
            # Keep public traversal order so ties in the baseline are stable.
            docs[lang]=list(existing.values())
            missing=sorted(set(names)-set(existing))
            if missing: info['issues'].append({'kind':'missing_inventory_titles','lang':lang,'titles':missing})
            if any(not isinstance(d.revision,int) or d.revision<=0 for d in docs[lang]):
                info['issues'].append({'kind':'missing_document_revision','lang':lang})
            if not any(d.blocks for d in docs[lang]): info['issues'].append({'kind':'no_extracted_text','lang':lang})
            if any(not d.blocks for d in docs[lang] if d.title!=title):
                info['issues'].append({'kind':'empty_subpage_text','lang':lang})
        except Exception as exc: info['issues'].append({'kind':'api_inventory_error','lang':lang,'error':str(exc)})
    info['scope_complete']=bool(docs['fr'] and docs['en'] and not info['issues'])
    return docs,info,links


def wr_section_pairs(wid,docs,links):
    """Candidate routes independent of gold quote data; no chapter-number guess."""
    pairs=[]
    def leaf(d,ds):
        return d.title and not any(x.title.startswith(d.title+'/') for x in ds if x.title!=d.title)
    fr=[d for d in docs['fr'] if d.blocks and leaf(d,docs['fr'])]
    en=[d for d in docs['en'] if d.blocks and leaf(d,docs['en'])]
    for f in fr:
        targets={wr_title(u,'en') for u in links.get(f.url,[])}
        hits=[e for e in en if e.title in targets]
        if len(targets)==1 and len(hits)==1:
            pairs.append((f,hits[0],'public_leaf_langlink_candidate'))
    if wid=='engels-condition':
        # Existing work-level mapping from MIA TOCs also matches these titles.
        for a,b in ENGELS_SECTION_LABELS:
            fs=[f for f in fr if '/' in f.title and norm(f.title.rsplit('/',1)[-1])==norm(a)]
            es=[e for e in en if '/' in e.title and norm(e.title.rsplit('/',1)[-1])==norm(b)]
            if len(fs)==len(es)==1 and not any(p[0]==fs[0] for p in pairs):
                pairs.append((fs[0],es[0],'work_level_bilingual_section_labels_candidate'))
    return pairs


def source_diagnostics(quote_text,selector,docs,limit=5):
    """Show bounded source contexts around exact selector hits, even when the
    full quote is not contiguous. Never promotes fuzzy/omitted source text.
    """
    q=norm(quote_text).split(); needle=norm(selector).split(); rows=[]
    if len(needle)<3 or not q: return {'has_ellipsis':bool(OMISSION.search(quote_text)),'selector_hits':[]}
    for doc in docs:
        text,_=text_and_starts(doc); ts=tokens_with_offsets(text)
        words=[t[0] for t in ts]
        # The selector is chosen before the test, not a gold source location.
        for i in range(len(words)-len(needle)+1):
            if words[i:i+len(needle)]!=needle: continue
            a=max(0,i-len(q)); b=min(len(ts),i+len(needle)+len(q))
            lo,hi=ts[a][1],ts[b-1][2]
            context=text[lo:hi]
            # Heuristic overlap; the context is longer than the quote.
            ratio=SequenceMatcher(None,q,words[a:b],autojunk=False).ratio()
            rows.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
                         'char_start':lo,'char_end':hi,'context_similarity':round(ratio,4),
                         'text':context,'boundary_verified':False})
    rows.sort(key=lambda r:(-r['context_similarity'],r['url'],r['char_start']))
    return {'has_ellipsis':bool(OMISSION.search(quote_text)),'selector_hits':rows[:limit],
            'selector_hit_count':len(rows),'selector_hits_truncated':len(rows)>limit}

def probe_api(client,endpoint):
    try:
        r=client.api(endpoint,action='query',meta='siteinfo',siprop='general')
        g=r['query']['general']
        return {'status':'available','sitename':g.get('sitename'),'generator':g.get('generator'),'lang':g.get('lang'),'server':g.get('server')}
    except Exception as exc: return {'status':'unavailable','error':str(exc)}


def redact_text(obj):
    """Default report contains fingerprints and coordinates, not long source extracts."""
    if isinstance(obj,list): return [redact_text(x) for x in obj]
    if not isinstance(obj,dict): return obj
    out={}
    for k,v in obj.items():
        if k=='text' and isinstance(v,str):
            out['text_sha256']=sha(v.encode()); out['text_words']=len(norm(v).split())
        else: out[k]=redact_text(v)
    return out


def run_pilot(client,cases,providers,max_pages,encoder=None,max_cells=250000,max_windows=6000,include_text=False,review_cases=()):
    started=time.monotonic()
    probes={'wikidebats':probe_api(client,WD_API)}
    if 'wikirouge' in providers:
        for lang in ('fr','en'): probes['wikirouge_'+lang]=probe_api(client,API[lang])
    report={'schema':'wikidebia-full-work-pilot-0.4','offline':client.offline,'api_probes':probes,
            'auto_publish':False,'works':{},'cases':[],'evaluation_contract':'short manually verified anchors only; not full quote fidelity; no automatic acceptance'}
    inputs={}
    for c in cases:
        try: inputs[c['id']]=wiki_input(client,c,probes['wikidebats']['status']=='available')
        except Exception as exc: inputs[c['id']]=(None,{'error':str(exc)})
    # Do not crawl entire works if the input wiki is unavailable. Explicitly
    # distinguish input failure from a negative match or a tested work.
    work_cache={}
    for c in cases:
        q,prov=inputs[c['id']]; wid=CASES[c['id']]
        for provider in providers:
            row={'id':c['id'],'work':wid,'provider':provider,'input':prov,'auto_publish':False,'rights':'unreviewed'}; report['cases'].append(row)
            if q is None: row['status']='input_unavailable'; continue
            key=(wid,provider)
            if key not in work_cache:
                if provider=='mia': docs,info=mia_work(client,WORKS[wid],max_pages); links={}
                else:
                    if all(probes['wikirouge_'+l]['status']=='available' for l in ('fr','en')):
                        docs,info,links=wr_api_work(client,WORKS[wid],max_pages)
                        if not info['scope_complete']:
                            docs,info,links=wr_hybrid_work(client,WORKS[wid],max_pages,info)
                    else: docs,info,links=wr_public_work(client,WORKS[wid],max_pages)
                work_cache[key]=docs,info,links; report['works'][wid+':'+provider]=info
            docs,info,links=work_cache[key]; fr,en=docs['fr'],docs['en']; pairs=[]; pair_issues=[]
            if provider=='mia':
                pairs=mia_pairs(wid,docs,info)
            elif info['scope']=='api_prefix_enumeration' and info['scope_complete']:
                found={d.url for d in fr if exact_occurrences(q,d)[0]}
                pairs=page_pairs(client,fr,en,found,pair_issues)
            elif info['scope']=='html_roots_api_prefix_enumeration' and info['scope_complete']:
                pairs=wr_section_pairs(wid,docs,links)
            # HTML discovery alone is useful for retrieval, not proof of a
            # complete parallel section. No alignment from an unverified TOC.
            start=time.monotonic()
            try:
                result=compare_methods(q,fr,en,pairs,encoder,max_cells,max_windows)
                if result['source_status']=='unconfirmed':
                    result['source_diagnostics']=source_diagnostics(q,c['selector'],fr)
                for m in result['methods'].values():
                    if m.get('status') in ('not_requested','not_applicable_or_unresolved','error','embedding_window_budget_exceeded') or not en:
                        m['evaluation']={'state':'not_executed_or_unresolved'}
                    else: m['evaluation']=evaluation(m['candidates'],c,provider)
                candidate_count=sum(len(m['candidates']) for m in result['methods'].values())
                row.update(result,documents={l:len(docs[l]) for l in docs},scope_complete=info['scope_complete'],pair_issues=pair_issues,
                           status='review_required' if info['scope_complete'] and result['source_status']=='normalized_contiguous' and en and candidate_count and not pair_issues else 'incomplete_or_unconfirmed',ranking_seconds=round(time.monotonic()-start,4))
            except Exception as exc: row.update(status='ranking_error',error=str(exc))
    report.update(status='review_required' if report['cases'] and all(r['status']=='review_required' for r in report['cases']) else 'incomplete',
                  requests=client.requests,cache_hits=client.hits,cache_bytes_read=client.cache_bytes_read,
                  elapsed_seconds=round(time.monotonic()-started,3),http_events=client.events)
    if encoder: report['embedding']={'model_id':encoder.model_id,**encoder.stats}
    report['metrics']=metrics(report['cases'])
    if include_text: return report
    redacted=redact_text(report)
    if review_cases:
        for i,row in enumerate(report['cases']):
            if row['id'] in review_cases: redacted['cases'][i]=row
    return redacted


def metrics(rows):
    out={}
    for row in rows:
        provider=row['provider']
        group=out.setdefault(provider,{'cases':0,'input_unavailable':0,'complete_toc_scope':0,'source_contiguous_unique':0,'methods':{}})
        group['cases']+=1
        group['input_unavailable']+=int(row['status']=='input_unavailable')
        group['complete_toc_scope']+=int(bool(row.get('scope_complete')))
        group['source_contiguous_unique']+=int(row.get('source_status')=='normalized_contiguous')
        for method,m in row.get('methods',{}).items():
            counts=group['methods'].setdefault(method,{'anchor_evaluable_complete_scope':0,'anchor_top1':0,'anchor_top5':0,'anchor_evaluable_partial_scope':0,'partial_scope_top1':0,'partial_scope_top5':0,'candidate_words':0})
            counts['candidate_words']+=sum(len(norm(x['text']).split()) for x in m['candidates'])
            e=m.get('evaluation',{})
            if e.get('state')!='short_anchor_only': continue
            rank=e['rank']; complete=row.get('scope_complete') and row.get('source_status')=='normalized_contiguous'
            if complete:
                counts['anchor_evaluable_complete_scope']+=1; counts['anchor_top1']+=int(rank==1); counts['anchor_top5']+=int(rank is not None and rank<=5)
            else:
                counts['anchor_evaluable_partial_scope']+=1; counts['partial_scope_top1']+=int(rank==1); counts['partial_scope_top5']+=int(rank is not None and rank<=5)
    return out

def replay_events(path):
    r=json.loads(path.read_text())
    if r.get('schema')!='wikidebia-full-work-pilot-0.4': raise ValueError('unsupported replay report')
    out={}
    for e in r['http_events']:
        key=e['request_url']
        if not valid_url(key): raise ValueError('invalid replay URL')
        old=out.get(key)
        if old and (old['status']!=e['status'] or old.get('sha256')!=e.get('sha256') or old.get('final_url')!=e.get('final_url')):
            raise ValueError('response changed during original run')
        out[key]=e
    return out

def replay_mismatches(expected,actual):
    observed={e['request_url']:e for e in actual}
    return [k for k in sorted(set(expected)|set(observed)) if k not in expected or k not in observed or any(expected[k].get(f)!=observed[k].get(f) for f in ('status','sha256','final_url'))]


def replay_results(report):
    """Compare editorial/algorithmic outputs, not execution timing or cache stats."""
    return {**{k:report.get(k) for k in ('schema','status','api_probes','works','metrics','evaluation_contract','auto_publish')},
            'cases':[{k:v for k,v in c.items() if k!='ranking_seconds'} for c in report.get('cases',[])]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--provider',choices=['both','mia','wikirouge'],default='both')
    p.add_argument('--case',choices=list(CASES),action='append')
    p.add_argument('--cache',type=Path,default=ROOT/'.pilot-cache')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'full-work.json')
    p.add_argument('--offline',action='store_true'); p.add_argument('--replay-report',type=Path); p.add_argument('--include-text',action='store_true')
    p.add_argument('--budget',type=int,default=220); p.add_argument('--max-pages',type=int,default=40)
    p.add_argument('--delay',type=float,default=.5); p.add_argument('--max-cells',type=int,default=250000); p.add_argument('--max-windows',type=int,default=6000)
    p.add_argument('--model-dir',type=Path)
    p.add_argument('--review-case',choices=list(CASES),action='append',default=[],help='Include local source/candidate texts only for selected cases; review rights before sharing')
    a=p.parse_args()
    if min(a.budget,a.max_pages,a.max_cells,a.max_windows)<1 or a.delay<.25: p.error('positive limits, delay >= .25 s')
    allcases=json.loads((ROOT/'cases-v02.json').read_text())['cases']
    cases=[c for c in allcases if c['id'] in CASES and (not a.case or c['id'] in a.case)]
    if a.replay_report and not a.offline: p.error('--replay-report requires --offline')
    if a.replay_report and a.replay_report.resolve()==a.out.resolve(): p.error('use a separate output for replay')
    client=PilotClient(a.cache,a.budget,a.delay,a.offline,replay_events(a.replay_report) if a.replay_report else None); encoder=None
    try:
        if a.model_dir: encoder=LocalEncoder(a.model_dir,a.cache/'vectors.sqlite3')
        r=run_pilot(client,cases,['mia','wikirouge'] if a.provider=='both' else [a.provider],a.max_pages,encoder,a.max_cells,a.max_windows,a.include_text,a.review_case)
        r['config']={'provider':a.provider,'cases':[c['id'] for c in cases],'budget':a.budget,'max_pages':a.max_pages,'max_cells':a.max_cells,'max_windows':a.max_windows,'embedding_requested':bool(a.model_dir),'include_text':a.include_text,'review_cases':sorted(set(a.review_case))}
        if a.replay_report:
            original=json.loads(a.replay_report.read_text())
            mismatches=replay_mismatches(client.expected_events,client.events)
            config_matches=original.get('config')==r['config']
            model_matches=original.get('embedding',{}).get('model_id')==r.get('embedding',{}).get('model_id')
            results_match=replay_results(original)==replay_results(r)
            r.update(replay_of_sha256=sha(a.replay_report.read_bytes()),replay_integrity={'config_matches':config_matches,'model_matches':model_matches,'response_mismatches':mismatches,'results_match':results_match})
            if mismatches or not config_matches or not model_matches or not results_match: r['status']='replay_mismatch'
    except Exception as exc:
        r={'schema':'wikidebia-full-work-pilot-0.4','status':'error','error':str(exc),'auto_publish':False,'http_events':client.events}
    finally:
        if encoder: encoder.close()
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(a.out),'status':r['status'],'requests':client.requests,'cache_hits':client.hits,'cases':len(r.get('cases',[]))},ensure_ascii=False))
    return int(r['status']!='review_required')

if __name__=='__main__': raise SystemExit(main())
