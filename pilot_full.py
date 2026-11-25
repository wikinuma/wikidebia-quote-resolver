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
