import json
from export_semantique_v2 import Client,CATS,category_batch,batch_records
c=Client(delay=2,budget=40)
try:
    for lang,names in CATS.items():
        for category in names:
            rows,obj=category_batch(c,lang,category,limit=25)
            print('CATEGORY',json.dumps({'language':lang,'category':category,'rows':len(rows),'more':bool(obj.get('continue'))},ensure_ascii=False),flush=True)
            if rows and category in ('Texte','Text','Sous-page','Subpage') and (category in ('Texte','Sous-page') if lang=='fr' else category in ('Text','Subpage')):
                records=batch_records(c,lang,ids=[r['pageid'] for r in rows])
                print('BATCH',json.dumps({'language':lang,'category':category,'pages':len(records),
                    'authors':sum(bool(r['semantic_properties'].get('author')) for r in records),
                    'dates':sum(bool(r['semantic_properties'].get('date')) for r in records),
                    'mia_links':sum(bool(r['mia_urls']) for r in records),
                    'multiple_mia_links':sum(len(r['mia_urls'])>1 for r in records),
                    'native_langlinks':sum(bool(r['native_langlinks']) for r in records),
                    'semantic_rows':sum(r['status']=='semantic_metadata' for r in records)},ensure_ascii=False),flush=True)
                if len(records)!=len(rows): raise RuntimeError('incomplete_batch')
                if category in ('Texte','Text') and not any(r['status']=='semantic_metadata' for r in records): raise RuntimeError('no_semantic_metadata_in_text_sample')
    print('COUNTS',json.dumps({'requests':c.calls,'bytes':c.bytes}),flush=True)
finally: c.close()
