"""Automatic leaf-document matching from an actual localized French passage.

Only exact MIA URL or exact Wikirouge page/revision evidence is eligible.
No inferred chapter numbers, translated titles, case-specific targets or
semantic acceptance. The index can propose a document; alignment still
has to support a candidate passage.
"""
from navigation import canonical_mia
from pilot_full import wr_title
from refinement import select_hit_metadata
from wikirouge_index import LIMIT


def is_leaf(index,lang,page):
    row=index.db.execute('SELECT title_key FROM pages WHERE lang=? AND id=?',
                         (lang,page['id'])).fetchone()
    if row is None:
        return False
    key=row[0]
    return index.db.execute(
        'SELECT 1 FROM pages WHERE lang=? AND title_key>=? AND title_key<? LIMIT 1',
        (lang,key+'/',key+'0')).fetchone() is None


def route_for_hit(index,metadata,hit):
    url=hit['url']
    mia=canonical_mia(url)
    wr=wr_title(url,'fr')
    if mia and mia.startswith('https://www.marxists.org/francais/'):
        ids=index.ids_for_urls([mia])
        lookup={'authors':metadata.get('authors',''),'work':'','article':'','mia_urls':[mia]}
    elif wr:
        ids=index.ids_for_title('fr',wr)
        lookup={'authors':metadata.get('authors',''),'work':'','article':wr,'mia_urls':[]}
    else:
        return None,'unsupported_source_url'
    if len(ids)!=1:
        return None,'source_identity_missing_or_ambiguous'
    pid=next(iter(ids))
    fr=index.page('fr',pid)
    if mia and mia not in fr['mia_urls']:
        return None,'source_url_not_exact'
    if wr and (fr['title']!=wr or type(hit.get('revision')) is not int
               or fr['revision']!=hit['revision']):
        return None,'source_revision_or_title_mismatch'
    result=index.lookup(lookup)
    if result['status']!='candidate' or len(result['candidates'])!=1:
        return None,'native_pair_'+result['status']
    c=result['candidates'][0]
    en=c['en']
    if c['scope']!='direct_native_link' or c['fr']['id']!=pid or c['source_page']['id']!=pid:
        return None,'parent_route_not_leaf_identity'
    if not is_leaf(index,'fr',fr) or not is_leaf(index,'en',en):
        return None,'not_two_leaf_pages'
    if fr['title']==fr['work_root'] and en['title']==en['work_root']:
        return None,'standalone_work_not_new_chapter_route'
    target_mia=en['mia_urls'][0] if len(en['mia_urls'])==1 else None
    if target_mia and target_mia.startswith('https://www.marxists.org/francais/'):
        return None,'target_language_conflict'
    return {
        'method':'indexed_native_leaf_route_v1','source_url':url,
        'fr_id':pid,'fr_title':fr['title'],'fr_revision':fr['revision'],
        'en_id':en['id'],'en_title':en['title'],'en_revision':en['revision'],
        'en_wr_url':en['url'],'en_mia_url':target_mia,
        'directions':c['directions'],'author_checks':c['author_checks'],
        'index_sha256':index.fingerprint,'scope':'document_hypothesis_not_equivalence',
        'rights':'unreviewed','auto_publish':False},'candidate'


def indexed_leaf_route(index,metadata,source):
    out={'method':'indexed_native_leaf_route_v1','status':'not_applicable',
         'route':None,'attempts':[],'auto_publish':False,'rights':'unreviewed'}
    urls=metadata.get('mia_urls',[])
    if not urls or any(canonical_mia(u)!=u for u in urls):
        return out
    if source.get('status') not in ('exact_in_examined_documents','variant_review_required'):
        return out
    hits=source.get('occurrences',[]) if source['status']=='exact_in_examined_documents' else source.get('variants',[])
    routes=[]
    for url in sorted({h['url'] for h in hits if h.get('url')}):
        hit,kind=select_hit_metadata(source,url)
        if hit is None:
            out['attempts'].append({'url':url,'status':'source_not_uniquely_supported'})
            continue
        route,status=route_for_hit(index,metadata,hit)
        out['attempts'].append({'url':url,'status':status})
        if route:
            routes.append({**route,'source_evidence_kind':kind})
    if len(routes)==1:
        out.update(status='candidate',route=routes[0])
    elif len(routes)>1:
        out['status']='ambiguous_leaf_routes'
    elif out['attempts']:
        out['status']='no_supported_leaf_route'
    return out
