"""Inspect only public technical configuration and templates, not article texts."""
import json
import re
import time
from urllib.request import Request,urlopen
from urllib.parse import urlencode

def get(lang,**params):
    time.sleep(2)
    try:
        url='https://wikirouge.net/texts/'+lang+'/w/api.php?'+urlencode({'format':'json','formatversion':2,'maxlag':5,**params})
        with urlopen(Request(url,headers={'User-Agent':'WikidebiaMetadataProbe/1.0 (read-only template and property diagnostic)'}),timeout=20) as r:
            return json.loads(r.read(2000000))
    except Exception as e: return {'error':str(e)[:200]}

for lang in ('fr','en'):
    d=get(lang,action='query',meta='siteinfo',siprop='extensions|general')
    print('EXTENSIONS',lang,json.dumps({'error':d.get('error'),'names':[x.get('name') for x in d.get('query',{}).get('extensions',[])]},ensure_ascii=False),flush=True)
    # Only source of technical templates; no text-page contents requested.
    titles='Template:Text|Template:Text/internal|Template:Texte|Template:Texte/internal|Form:Text|Property:MIA URL|Property:MIA-URL'
    d=get(lang,action='query',prop='revisions',rvprop='ids|content',rvslots='main',titles=titles)
    for page in d.get('query',{}).get('pages',[]):
        raw=page.get('revisions',[{}])[0].get('slots',{}).get('main',{}).get('content','')
        snippets=[]
        for m in re.finditer(r'(?i)MIA[ _-]+URL|#(?:set|subobject|dpl|invoke|vardefine|external_value)\s*:',raw):
            s=raw[max(0,m.start()-90):min(len(raw),m.end()+150)].replace('\n',' ')
            if s not in snippets: snippets.append(s)
        print('TEMPLATE',lang,json.dumps({'title':page.get('title'),'missing':page.get('missing',False),'revision':page.get('revisions',[{}])[0].get('revid'),'bytes':len(raw.encode()),'matching_contexts':snippets[:35]},ensure_ascii=False),flush=True)
    if 'error' in d: print('TEMPLATE_ERROR',lang,d['error'],flush=True)
    title='Manifeste du parti communiste' if lang=='fr' else 'Manifesto of the Communist Party'
    fields=['MIA URL','MIA-URL','URL MIA','MIA','Publication']
    d=get(lang,action='ask',query='[['+title+']]|'+'|'.join('?'+p for p in fields)+'|limit=1')
    rows=d.get('query',{}).get('results',{})
    print('PROPERTY_VARIANTS',lang,json.dumps({'results':rows,'error':d.get('error')},ensure_ascii=False),flush=True)
