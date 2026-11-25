"""Check whether native external-link metadata recovers known MIA URLs."""
import json
import time
from urllib.request import Request,urlopen
from urllib.parse import urlencode,urlsplit
PAGES={'fr':{'Manifeste du parti communiste':'/francais/marx/works/1847/00/kmfe18470000.htm','Anti-Dühring':'/francais/engels/works/1878/06/fe18780611.htm'},
'en':{'Manifesto of the Communist Party':'/archive/marx/works/1848/communist-manifesto/','Anti-Dühring':'/archive/marx/works/1877/anti-duhring/index.htm','The Questions of Wendelin Thomas':'/archive/trotsky/1937/07/thomas.htm'}}
for lang,expected in PAGES.items():
    cont={}; pages={}; seen=set()
    for _ in range(5):
        time.sleep(2)
        u='https://wikirouge.net/texts/'+lang+'/w/api.php?'+urlencode({'format':'json','formatversion':2,'maxlag':5,'action':'query','prop':'extlinks','ellimit':500,'titles':'|'.join(expected),**cont})
        with urlopen(Request(u,headers={'User-Agent':'WikidebiaMetadataProbe/1.0 (small anonymous external link metadata test)'}),timeout=25) as r:
            d=json.loads(r.read(2000000))
        if 'error' in d or 'warnings' in d:
            print('API_ERROR',lang,json.dumps({'error':d.get('error'),'warnings':d.get('warnings')}),flush=True)
            break
        for row in d.get('query',{}).get('pages',[]):
            links=pages.setdefault(row['title'],set())
            for e in row.get('extlinks',[]):
                link=e.get('url',e.get('*',''))
                if urlsplit(link).hostname in ('marxists.org','www.marxists.org','marxists.info','www.marxists.info'):
                    links.add(link)
        cont=d.get('continue',{})
        if not cont: break
        marker=json.dumps(cont,sort_keys=True)
        if marker in seen: break
        seen.add(marker)
    for title,path in expected.items():
        links=sorted(pages.get(title,set()))
        print('EXTLINKS',json.dumps({'language':lang,'title':title,'known_mia_url_found':any(urlsplit(x).path==path for x in links),'mia_links':links,'continuation_remaining':bool(cont)},ensure_ascii=False),flush=True)
