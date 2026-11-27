#!/usr/bin/env python3
"""Read-only FR -> EN quotation candidate retrieval. No wiki writes or generated quotes."""
from __future__ import annotations
import argparse, base64, hashlib, json, math, re, sys, time, unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
API = {lang: f'https://wikirouge.net/texts/{lang}/w/api.php' for lang in ('fr', 'en')}
WD_API = 'https://fr.wikidebates.org/w/api.php'
HOSTS = {'wikirouge.net', 'www.marxists.org', 'marxists.org', 'fr.wikidebates.org',
         'en.wikidebates.org'}
MAX_BYTES = 5_000_000


def sha(b):
    return hashlib.sha256(b).hexdigest()


def clean(s):
    return re.sub(r'\s+', ' ', s).strip()


def norm(s):
    s = s.replace('\u00ad', '').replace('\u200b', '').replace('œ', 'oe').replace('Œ', 'OE')
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in unicodedata.normalize('NFKD', s.casefold()) if not unicodedata.combining(c)), re.UNICODE))


def valid_url(url):
    p = urlsplit(url)
    try:
        return p.scheme == 'https' and p.hostname in HOSTS and not p.username and not p.password and p.port in (None, 443)
    except ValueError:
        return False


class RequestBudgetExceeded(RuntimeError):
    """A local request limit, not evidence of source unavailability."""


class Client:
    """Bounded public GETs, disk cache, no cookies or credentials from caller."""
    def __init__(self, cache, budget=70, delay=.5, ttl=30*86400):
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.budget = budget; self.delay = delay; self.ttl = ttl
        self.requests = 0; self.hits = 0; self.last = 0.0
        self.network_body_bytes = 0  # decoded body bytes read, including partial failures

    def get(self, url, params=None):
        if not valid_url(url): raise ValueError(f'URL not allowed: {url}')
        url = requests.Request('GET', url, params=sorted((params or {}).items())).prepare().url
        key = self.cache / (sha(url.encode()) + '.json')
        if key.exists():
            try:
                c = json.loads(key.read_text()); body = base64.b64decode(c['body'], validate=True)
                if c['request_url'] == url and valid_url(c['url']) and c['sha256'] == sha(body) and 0 <= time.time()-c['at'] <= self.ttl:
                    self.hits += 1; return body, c['url']
            except (ValueError, KeyError, TypeError, OSError): pass
        request_url = url
        for _ in range(5):
            if not valid_url(url): raise ValueError(f'Redirect not allowed: {url}')
            if self.requests >= self.budget: raise RequestBudgetExceeded('request budget exhausted')
            time.sleep(max(0, self.delay - (time.monotonic()-self.last)))
            self.requests += 1; self.last = time.monotonic()
            with requests.Session() as session:
                session.trust_env = False
                with session.get(url, headers={'User-Agent':'WikidebiaQuoteResolver/0.1 (public research; no writes)'}, timeout=(5, 15), allow_redirects=False, stream=True) as r:
                    if r.status_code in (301,302,303,307,308):
                        url = urljoin(url, r.headers['Location']); continue
                    r.raise_for_status()
                    if r.status_code != 200: raise RuntimeError(f'Unexpected status {r.status_code}')
                    chunks=[]; size=0
                    for chunk in r.iter_content(65536):
                        size += len(chunk)
                        self.network_body_bytes += len(chunk)
                        if size > MAX_BYTES: raise RuntimeError('response too large')
                        chunks.append(chunk)
                    body=b''.join(chunks)
            if not body: raise RuntimeError('empty response')
            data={'request_url':request_url,'url':url,'at':time.time(),'sha256':sha(body),'body':base64.b64encode(body).decode('ascii')}
            tmp=key.with_suffix('.tmp'); tmp.write_text(json.dumps(data)); tmp.replace(key)
            return body, url
        raise RuntimeError('too many redirects')

    def api(self, endpoint, **params):
        body, _ = self.get(endpoint, {'format':'json','formatversion':2,**params})
        result=json.loads(body)
        if 'error' in result: raise RuntimeError(str(result['error']))
        return result


@dataclass
class Document:
    url: str
    blocks: list[str]
    digest: str
    title: str = ''
    revision: int | None = None
    headings: tuple[str, ...] = ()


def soup_from(body):
    return BeautifulSoup(body, 'html.parser')


def blocks_from(body):
    soup=soup_from(body)
    root=soup.select_one('.mw-parser-output') or soup.body or soup
    for node in root.select('script,style,nav,table,.mw-editsection,.reference,.references,.toc,.navbox,.metadata'):
        if node.parent: node.decompose()
    out=[]
    for node in root.find_all(['p','blockquote','li']):
        if node.find_parent(['p','blockquote','li']) is not None: continue
        # For a blockquote with nested paragraphs retain internal separation.
        s=clean(node.get_text(' ',strip=False))
        if s: out.append(s)
    return out


def html_document(client,url):
    body, final=client.get(url)
    soup=soup_from(body)
    title=clean(soup.title.get_text()) if soup.title else ''
    return Document(final,blocks_from(body),sha(body),title)


def mw_document(client,lang,title):
    data=client.api(API[lang],action='parse',page=title,redirects=1,prop='text|revid')['parse']
    raw=data['text']; raw=raw['*'] if isinstance(raw,dict) else raw
    body=raw.encode('utf-8')
    actual=data['title']
    from urllib.parse import quote
    url=f'https://wikirouge.net/texts/{lang}/'+quote(actual.replace(' ','_'),safe='/()')
    return Document(url,blocks_from(body),sha(body),actual,data.get('revid'))


def work_title(client,title):
    data=client.api(API['fr'],action='query',titles=title,redirects=1,prop='langlinks',lllang='en',llprop='url')
    p=data['query']['pages'][0]
    if p.get('missing') is not True and 'missing' not in p:
        return p['title'], [x['title'] if 'title' in x else x['*'] for x in p.get('langlinks',[]) if x.get('lang')=='en']
    return None, []


def search_titles(client,query,limit=5):
    data=client.api(API['fr'],action='query',list='search',srsearch=query,srlimit=limit,srnamespace=0)
    return [p['title'] for p in data['query']['search']]


def subpages(client,lang,title,max_pages):
    """Canonical prefix enumeration, bounded; reports truncation explicitly."""
    names=[title]; cursor={}; seen_cursors=set()
    if max_pages <= 1: return names, True
    while len(names)<max_pages:
        data=client.api(API[lang],action='query',list='allpages',apprefix=title+'/',apnamespace=0,aplimit=min(50,max_pages-len(names)),**cursor)
        for p in data['query']['allpages']:
            name=p['title']
            if not name.startswith(title+'/') or name in names:
                raise ValueError('invalid_or_duplicate_prefix_title')
            if len(names)>=max_pages:
                raise ValueError('api_page_limit_exceeded')
            names.append(name)
        cursor=data.get('continue',{})
        if not cursor: return names,False
        cursor_key=json.dumps(cursor,sort_keys=True)
        if cursor_key in seen_cursors:
            raise ValueError('repeated_api_continuation')
        seen_cursors.add(cursor_key)
    return names,bool(cursor)


def wikirouge(client,work,quote,max_pages=16):
    """Return separate FR/EN document sets. The interlanguage link proves only work identity."""
    issues=[]; french=[]; english=[]; routes=[]
    fr, targets=work_title(client,work)
    if fr is None:
        # Search both title and distinctive source phrase; never assume first hit is correct.
        phrase=' '.join(norm(quote).split()[:12]); hits=search_titles(client,work)
        if phrase: hits+=search_titles(client,'"'+phrase+'"')
        issues.append({'kind':'title_not_resolved','candidates':list(dict.fromkeys(hits))})
        return french,english,routes,issues
    routes.append({'fr_title':fr,'en_titles':targets,'method':'langlinks'})
    for lang,titles,docs in [('fr',[fr],french),('en',targets,english)]:
        for title in titles[:1]:
            names,truncated=subpages(client,lang,title,max_pages)
            if truncated: issues.append({'kind':'subpages_truncated','lang':lang,'title':title})
            for name in names:
                try: docs.append(mw_document(client,lang,name))
                except Exception as exc: issues.append({'kind':'fetch_error','title':name,'error':str(exc)})
    if not targets: issues.append({'kind':'no_english_langlink','title':fr})
    return french,english,routes,issues


def wiki_quote(client,title,selector):
    data=client.api(WD_API,action='parse',page=title,redirects=1,prop='text|revid')['parse']
    raw=data['text']; raw=raw['*'] if isinstance(raw,dict) else raw
    soup=soup_from(raw)
    for node in soup.select('.reference,.mw-editsection'):
        node.decompose()
    s=norm(selector); matches=[]
    for b in soup.find_all('blockquote'):
        if b.find_parent('blockquote'): continue
        text=clean(b.get_text(' ',strip=False)).strip('«»“” ')
        if s and (' '+s+' ') in (' '+norm(text)+' '): matches.append(text)
    if len(matches)!=1: raise RuntimeError(f'expected exactly one quote matching selector, found {len(matches)}')
    return matches[0],data.get('revid')


def source_windows(doc,max_span=4):
    for i in range(len(doc.blocks)):
        for n in range(1,min(max_span,len(doc.blocks)-i)+1):
            yield i,n,'\n\n'.join(doc.blocks[i:i+n])


def locate(quote,docs):
    """Exact normalized span, then fuzzy windows; omissions never declared exact."""
    q=norm(quote); words=q.split(); results=[]
    if len(words)<4: return []
    qset=set(words)
    for doc in docs:
        local=[]
        for i,n,text in source_windows(doc):
            t=norm(text); tw=t.split()
            if not tw: continue
            exact=(' '+q+' ') in (' '+t+' ')
            if exact: score=1.0
            else:
                overlap=len(qset & set(tw))/len(qset)
                if overlap<.65: continue
                # avoid very large windows obscuring a short quotation
                if len(tw)>3*len(words) or len(tw)<.6*len(words): continue
                score=SequenceMatcher(None,words,tw,autojunk=False).ratio()
                if score<.65: continue
            local.append((score,exact,i,n,text))
        # retain smallest covering window at each occurrence; don't duplicate overlapping windows
        local.sort(key=lambda x:(-x[0],len(norm(x[4]).split()),x[3],x[2]))
        used=set()
        for score,exact,i,n,text in local:
            span=set(range(i,i+n))
            if span & used: continue
            used |= span
            results.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,'block':i,'span':n,'match':'normalized_exact' if exact else 'fuzzy','score':round(score,4),'text':text})
    return sorted(results,key=lambda r:(-r['score'],len(r['text'])))[:10]


# Small baseline only: high-information bilingual anchors and cognates. Not a translation model.
ANCHORS=(('libre','free'),('developpement','development'),('chacun','each'),('tous','all'),('societ','societ'),('bourgeois','bourgeois'),('classe','class'),('revolution','revolution'),('reaction','reaction'),('proletar','proletar'),('lumpenproletar','lumpenproletar'),('pourriture','rotting'),('exploit','exploit'),('opprim','oppress'),('trahir','betray'),('trahit','betray'),('traitre','traitor'),('lache','coward'),('pecher','fish'),('trouble','troubled'),('venal','venal'),('allie','allies'),('lie','scum'),('pouvoir','power'),('richesse','wealth'),('mouvement','movement'),('servage','serfdom'),('monarchie','monarchy'),('ecuries','stables'),('propriete','property'),('ancien','old'),('condition','condition'),('couche','layer'),('femme','women'),('religion','religion'),('nationalit','national'),('milieu','environment'),('emancipat','emancipat'),('liber','free'),('paysan','peasant'),('travail','labor'),('travail','labour'))


def anchors(text,side):
    ws=norm(text).split()
    return {i for i,p in enumerate(ANCHORS) if any(w.startswith(p[side]) for w in ws)}


def rank(quote,docs,hint=None,top=5):
    qa=anchors(quote,0); qn=len(norm(quote).split()); qnums=set(re.findall(r'\b\d+\b',quote)); rows=[]
    for doc in docs:
        local=[]
        for i,n,text in source_windows(doc,3):
            tw=norm(text).split(); count=len(tw)
            if not count or count>max(30,4*qn) or count<max(3,qn*.25): continue
            ta=anchors(text,1); shared=qa&ta
            length=math.exp(-abs(math.log(count/max(1,qn))))
            nums=set(re.findall(r'\b\d+\b',text)); num=len(qnums&nums)/len(qnums) if qnums else 0
            coverage=len(shared)/max(1,len(qa)); precision=len(shared)/max(1,len(ta))
            if hint:
                ht=norm(hint).split(); hs=set(ht); ts=set(tw)
                # A supplied rough translation is search input only, never emitted as the quote.
                lexical=2*len(hs&ts)/max(1,len(hs)+len(ts)); sequence=SequenceMatcher(None,ht,tw,autojunk=False).ratio()
                score=.45*lexical+.35*sequence+.12*coverage+.08*length
            else:
                if not shared and not num: continue
                score=.57*coverage+.23*precision+.15*length+.05*num
            local.append({'url':doc.url,'sha256':doc.digest,'revision':doc.revision,'block':i,'span':n,'score':round(score,4),'shared_anchors':len(shared),'text':text,'boundary_verified':False,'rights':'unreviewed'})
        used=set()
        for row in sorted(local,key=lambda r:(-r['score'],r['span'],r['block'])):
            span=set(range(row['block'],row['block']+row['span']))
            if span&used: continue
            used|=span; rows.append(row)
            if len(used)>top*3: break
    return sorted(rows,key=lambda r:(-r['score'],r['span'],r['url'],r['block']))[:top]


def resolve(quote,fr_docs,en_docs,hint=None):
    fr=locate(quote,fr_docs); en=rank(quote,en_docs,hint)
    return {'status':'review_required' if fr and en else 'source_fr_unconfirmed' if not fr else 'no_candidate', 'fr_matches':fr,'en_candidates':en,'auto_publish':False,'method':'lexical_anchors_v1' if not hint else 'english_hint_v1'}


def pilot(client,casefile,provider,max_pages):
    data=json.loads(Path(casefile).read_text()); out=[]
    for case in data['cases']:
        started=time.monotonic(); before=(client.requests,client.hits)
        row={'id':case['id'],'provider':provider,'issues':[],'routes':[]}; out.append(row)
        try:
            q, rev=wiki_quote(client,case['wiki_title'],case['selector']); row['wiki_revision']=rev; row['quote_sha256']=sha(q.encode())
        except Exception as exc:
            row.update(status='input_unavailable',error=str(exc),elapsed_seconds=round(time.monotonic()-started,3),requests=client.requests-before[0],cache_hits=client.hits-before[1]); continue
        fr=[]; en=[]
        if provider=='mia':
            for lang,docs in [('fr',fr),('en',en)]:
                for url in case.get('mia_'+lang,[]):
                    try: docs.append(html_document(client,url))
                    except Exception as exc: row['issues'].append({'url':url,'error':str(exc)})
            row['routes'].append({'method':'seeded_work_urls_not_automatic_discovery'})
        else:
            try: fr,en,row['routes'],row['issues']=wikirouge(client,case['work_fr'],q,max_pages)
            except Exception as exc: row['issues'].append({'error':str(exc)})
        row.update(resolve(q,fr,en)); row['documents']={'fr':len(fr),'en':len(en)}; row['search_complete']=not row['issues']
        if row['issues'] and not row['en_candidates']: row['status']='incomplete_search'
        expected_url=case.get('expected_en_mia' if provider=='mia' else 'expected_en_wr')
        needle=norm(case.get('expected_needle',''))
        # Ground truth is consulted AFTER ranking. A needle is a location proxy, not a verified boundary.
        ranks=[i for i,c in enumerate(row['en_candidates'],1) if needle and (' '+needle+' ') in (' '+norm(c['text'])+' ') and (not expected_url or c['url']==expected_url)]
        row.update(evaluation={'expected_url':expected_url,'expected_anchor_rank':ranks[0] if ranks else None,'scope':'short anchor, not full citation'},elapsed_seconds=round(time.monotonic()-started,3),requests=client.requests-before[0],cache_hits=client.hits-before[1])
    return {'schema':'wikidebia-quote-experiment-0.1','provider':provider,'requests':client.requests,'cache_hits':client.hits,'cases':out}


def discover(client,limit):
    rows=[]; seen=set()
    for host in ['www.marxists.org','marxists.org']:
        for protocol in ['https','http']:
            cursor={}
            while len(rows)<limit:
                data=client.api(WD_API,action='query',list='exturlusage',euquery=host,euprotocol=protocol,eunamespace=0,eulimit=min(50,limit-len(rows)),**cursor)
                for row in data['query']['exturlusage']:
                    k=(row.get('pageid'),row.get('url'))
                    if k not in seen: seen.add(k); rows.append(row)
                cursor=data.get('continue',{})
                if not cursor: break
    return {'links':rows,'limit':limit,'note':'URL uses, not yet confirmed citations; bounded discovery, not an exhaustive census'}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--cache',default=str(ROOT/'.cache')); parser.add_argument('--budget',type=int,default=70); parser.add_argument('--out',type=Path)
    sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('pilot'); p.add_argument('--cases',default=str(ROOT/'cases.json')); p.add_argument('--provider',choices=['mia','wikirouge'],required=True); p.add_argument('--max-pages',type=int,default=16)
    p=sub.add_parser('discover'); p.add_argument('--limit',type=int,default=30)
    p=sub.add_parser('probe'); p.add_argument('--title',default='Manifeste du parti communiste')
    p=sub.add_parser('resolve'); p.add_argument('--quote',required=True); p.add_argument('--work',required=True); p.add_argument('--hint'); p.add_argument('--max-pages',type=int,default=16); p.add_argument('--fr-url',action='append',default=[]); p.add_argument('--en-url',action='append',default=[])
    args=parser.parse_args()
    if args.budget<1 or getattr(args,'max_pages',1)<1 or getattr(args,'limit',1)<1: parser.error('limits must be positive')
    client=Client(args.cache,args.budget); failed=False
    try:
        if args.cmd=='pilot': data=pilot(client,args.cases,args.provider,args.max_pages)
        elif args.cmd=='discover': data=discover(client,args.limit)
        elif args.cmd=='probe':
            data={}
            for lang in ['fr','en']:
                try: data[lang]=client.api(API[lang],action='query',meta='siteinfo',siprop='general')['query']['general']
                except Exception as exc: data[lang]={'error':str(exc)}; failed=True
            try: data['langlinks']=work_title(client,args.title)
            except Exception as exc: data['langlinks']={'error':str(exc)}; failed=True
        else:
            if args.fr_url or args.en_url:
                fr=[]; en=[]; issues=[]; routes=[{'method':'explicit_urls'}]
                for lang,urls,docs in [('fr',args.fr_url,fr),('en',args.en_url,en)]:
                    for url in urls:
                        try: docs.append(html_document(client,url))
                        except Exception as exc: issues.append({'url':url,'error':str(exc)})
            else:
                fr,en,routes,issues=wikirouge(client,args.work,args.quote,args.max_pages)
            data=resolve(args.quote,fr,en,args.hint); data.update(routes=routes,issues=issues)
            if issues and not data['en_candidates']: data['status']='incomplete_search'; failed=True
    except Exception as exc: data={'status':'error','error':str(exc)}; failed=True
    data.update(requests=client.requests,cache_hits=client.hits)
    if args.cmd=='pilot' and any(r['status'] in ('input_unavailable','incomplete_search') for r in data.get('cases',[])): failed=True
    s=json.dumps(data,ensure_ascii=False,indent=2)
    if args.out: args.out.write_text(s+'\n',encoding='utf-8')
    else: print(s)
    return int(failed)

if __name__=='__main__': sys.exit(main())
