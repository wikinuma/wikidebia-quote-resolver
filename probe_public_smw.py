"""Small anonymous read-only diagnostic: no page content, no credentials."""
import json
import time
from urllib.request import Request,urlopen
from urllib.parse import urlencode

PAGES={'fr':['Manifeste du parti communiste','Anti-Dühring'],
       'en':['Manifesto of the Communist Party','Anti-Dühring','The Questions of Wendelin Thomas']}
FIELDS={'fr':['Auteur','Date écriture','Publication','MIA URL','Recueil','Indisponible'],
        'en':['Author','Date of writing','Publication','MIA URL','Collection','Nonavailable']}
# Parameter names and data, not article text. 
def get(lang,**params):
    time.sleep(2)
    u='https://wikirouge.net/texts/'+lang+'/w/api.php?'+urlencode({'format':'json','maxlag':5,**params})
    try:
        with urlopen(Request(u,headers={'User-Agent':'WikidebiaMetadataProbe/1.0 (small anonymous read-only bibliographic test)'}),timeout=25) as r:
            data=r.read(1000000)
            return json.loads(data),len(data)
    except Exception as e:
        return {'transport_error':str(e)[:200]},0

def summarize_ask(data):
    raw=data.get('query',{}).get('results',{})
    rows=raw.values() if isinstance(raw,dict) else raw
    return {'error':data.get('error',data.get('transport_error')),
            'warnings':data.get('warnings'),'count':len(raw),
            'results':[{'title':r.get('fulltext'),'properties':r.get('printouts',{})} for r in rows]}

for lang,titles in PAGES.items():
    print('LANGUAGE',lang,flush=True)
    d,n=get(lang,action='help',modules='ask')
    # Never print full help; expose only access requirement from actual API.
    help_text=str(d.get('help',{}))
    print('ASK_HELP',json.dumps({'bytes':n,'error':d.get('error',d.get('transport_error')),
        'read_rights_mentioned': 'read rights' in help_text.lower(),
        'admin_rights_mentioned': 'administrator rights' in help_text.lower()},ensure_ascii=False),flush=True)
    d,n=get(lang,action='query',prop='info|revisions|langlinks',rvprop='ids|timestamp',titles='|'.join(titles),lllang='en' if lang=='fr' else 'fr',formatversion=2)
    print('PAGEINFO',json.dumps({'bytes':n,'error':d.get('error',d.get('transport_error')),
          'pages':[{k:r.get(k) for k in ('title','pageid','lastrevid','langlinks','missing')} for r in d.get('query',{}).get('pages',[])]},ensure_ascii=False),flush=True)
    fields='|'.join('?'+name for name in FIELDS[lang])
    d,n=get(lang,action='ask',query=' OR '.join('[['+t+']]' for t in titles)+'|'+fields+'|limit='+str(len(titles)))
    print('ASK_BATCH',json.dumps({'bytes':n,**summarize_ask(d)},ensure_ascii=False),flush=True)
    for title in titles:
        d,n=get(lang,action='ask',query='[['+title+']]|'+fields+'|limit=1')
        print('ASK_SINGLE',json.dumps({'bytes':n,'requested':title,**summarize_ask(d)},ensure_ascii=False),flush=True)
        d,n=get(lang,action='browsebysubject',subject=title)
        q=d.get('query',{})
        entries=q.get('data',[])
        print('BROWSE',json.dumps({'bytes':n,'requested':title,'error':d.get('error',d.get('transport_error')),
            'properties':[{ 'name':e.get('property'),'dataitem':e.get('dataitem') if str(e.get('property','')).replace('_',' ') in FIELDS[lang] else None } for e in entries],
            'subobjects_count':len(q.get('sobj',[]))},ensure_ascii=False),flush=True)
