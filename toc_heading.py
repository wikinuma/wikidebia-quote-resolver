"""Bilingual TOC matching from explicit links, never inferred chapter URLs.

A root-to-root route must be independently established by the caller.
Chapter numbers are stripped rather than treated as equivalent. Require
mutual semantic nearest neighbours, a margin and monotone support from
other headings. A returned link is a document hypothesis only.
"""
from collections import defaultdict
import re
from urllib.parse import urljoin,urlsplit
from auto_retrieve import explicit_toc,mia_toc_links,mia_doc
from navigation import canonical_mia
from pilot_full import soup
from resolver import norm,sha

GENERIC={'preface','avant propos','introduction','conclusion','appendix','appendices',
         'annexe','annexes','sommaire','contents','table des matieres','table of contents',
         'suite','next','previous','back','retour','index','chapter','chapitre','notes'}
NUMBER=re.compile(r'^\s*(?:(?:chapter|chapitre)\s+)?(?:\d{1,3}|[IVXLCDM]{1,8})(?:[.:\-–)]\s*|\s+)',re.I)


def heading(label):
    value=' '.join(label.split())
    value=NUMBER.sub('',value,count=1).strip()
    key=norm(value)
    if (not key or key in GENERIC or re.fullmatch(r'[ivxlcdm]+|\d+',key)
            or re.match(r'^(appendix|annexe|chronological table|table chronologique)\b',key)
            or not 1<=len(key.split())<=20 or len(key)<5):
        return None
    return value


def entries(body,root,lang):
    root=canonical_mia(root)
    prefix='/francais/' if lang=='fr' else '/archive/'
    if not root or not urlsplit(root).path.startswith(prefix) or not explicit_toc(body,root):
        return []
    links=mia_toc_links(body,root)
    labels=defaultdict(dict)
    allowed={r['url'] for r in links if r['route'] in ('directory_index_link','work_stem_link')
             and urlsplit(r['url']).path.startswith(prefix)}
    for a in soup(body).find_all('a',href=True):
        u=canonical_mia(urljoin(root,a['href']))
        if u not in allowed: continue
        label=heading(a.get_text(' ',strip=True))
        if label: labels[u].setdefault(norm(label),label)
    result=[]
    for position,r in enumerate(links):
        u=r['url']
        if u in allowed and len(labels[u])==1:
            result.append({'url':u,'heading':next(iter(labels[u].values())),'position':position})
    return result


def align_headings(fr,en,encoder,min_similarity=.72,min_margin=.08):
    import numpy as np
    if min(len(fr),len(en))<3 or max(len(fr),len(en))>160:
        return {'status':'insufficient_or_excessive_toc','pairs':[],'anchors':0}
    labels=[r['heading'] for r in fr+en]
    v=np.asarray(encoder.encode(labels),dtype=np.float64)
    if v.ndim!=2 or v.shape[0]!=len(labels) or not np.isfinite(v).all():
        raise ValueError('invalid_heading_embeddings')
    n=np.linalg.norm(v,axis=1)
    if np.any(n<=0): raise ValueError('zero_heading_vector')
    v=v/n[:,None]
    scores=np.clip(v[:len(fr)]@v[len(fr):].T,-1,1)
    anchors=[]
    for i in range(len(fr)):
        ordered=np.argsort(-scores[i],kind='stable')
        j=int(ordered[0])
        reverse=np.argsort(-scores[:,j],kind='stable')
        score=float(scores[i,j]);fm=score-float(scores[i,ordered[1]])
        em=score-float(scores[reverse[1],j])
        if int(reverse[0])!=i or score<min_similarity or min(fm,em)<min_margin:
            continue
        anchors.append((i,j,score,fm,em))
    pairs=[]
    for i,j,score,fm,em in anchors:
        others=[(a,b) for a,b,*_ in anchors if a!=i]
        # Need at least two additional independent anchors, with no crossing
        # relative to this proposed match. Insertions/deletions are allowed.
        support=sum((a<i and b<j) or (a>i and b>j) for a,b in others)
        crossing=len(others)-support
        if support<2 or crossing: continue
        pairs.append({'fr':fr[i],'en':en[j],'cosine':round(score,6),
                      'fr_margin':round(fm,6),'en_margin':round(em,6),
                      'monotone_support':support,'score_is_probability':False,
                      'scope':'explicit_toc_document_hypothesis','rights':'unreviewed','auto_publish':False})
    return {'status':'candidate_pairs' if pairs else 'no_supported_pair','anchors':len(anchors),'pairs':pairs}


def root_key(u):
    u=canonical_mia(u)
    if not u: return None
    for suffix in ('index.htm','index.html'):
        if u.endswith('/'+suffix): return u[:-len(suffix)]
    return u


class TocMatcher:
    def __init__(self,client,encoder):
        self.client=client;self.encoder=encoder;self.tocs={};self.matches={};self.docs={};self.content_probes={}

    def toc(self,root,lang):
        key=(root,lang)
        if key not in self.tocs:
            body,final=self.client.get(root)
            if root_key(final)!=root_key(root):
                raise ValueError('toc_redirect_outside_root')
            self.tocs[key]=(entries(body,final,lang),
                            {'requested_url':root,'url':final,'sha256':sha(body)})
        return self.tocs[key]

    def match(self,fr_root,en_root):
        key=(fr_root,en_root)
        if key not in self.matches:
            fr,fr_receipt=self.toc(fr_root,'fr');en,en_receipt=self.toc(en_root,'en')
            result=align_headings(fr,en,self.encoder)
            self.matches[key]={**result,'fr_count':len(fr),'en_count':len(en),
                               'fr_toc':fr_receipt,'en_toc':en_receipt,'auto_publish':False}
        return self.matches[key]

    def match_for_source(self,fr_root,en_root,source_url):
        """Return the hypothesis set for one explicit French chapter."""
        return self.match(fr_root,en_root)

    def document(self,url):
        if url not in self.docs:
            d,body=mia_doc(self.client,url)
            if canonical_mia(d.url)!=url or not d.blocks or explicit_toc(body,d.url):
                raise ValueError('invalid_chapter_document')
            self.docs[url]=d
        return self.docs[url]

    def content_probe(self,pair):
        """Reuse corroboration only for the same retrieved document digests."""
        fr=self.document(pair['fr']['url']);en=self.document(pair['en']['url'])
        key=(fr.url,fr.digest,en.url,en.digest)
        if key not in self.content_probes:
            self.content_probes[key]=self._content_probe(fr,en)
        return self.content_probes[key]

    def _content_probe(self,fr,en):
        """Independent sampled text agreement, not bilingual equivalence."""
        import numpy as np
        fw=' '.join(fr.blocks).split();ew=' '.join(en.blocks).split()
        base={'fr_document_sha256':fr.digest,'en_document_sha256':en.digest,
              'fr_words':len(fw),'en_words':len(ew),'scope':'sampled_content_hypothesis',
              'auto_publish':False}
        if min(len(fw),len(ew))<300 or not .4<=len(ew)/len(fw)<=2.5:
            return {**base,'status':'incompatible_lengths'}
        def sample(words,pos):
            i=max(0,min(len(words)-100,int(len(words)*pos)-50))
            return ' '.join(words[i:i+100])
        fp=(.2,.5,.8);ep=tuple(i/10 for i in range(1,10))
        texts=[sample(fw,p) for p in fp]+[sample(ew,p) for p in ep]
        v=np.asarray(self.encoder.encode(texts),dtype=np.float64)
        n=np.linalg.norm(v,axis=1)
        if not np.isfinite(v).all() or np.any(n<=0):
            raise ValueError('invalid_sample_embeddings')
        v=v/n[:,None];scores=np.clip(v[:3]@v[3:].T,-1,1)
        rows=[]
        for i,p in enumerate(fp):
            j=max((j for j,x in enumerate(ep) if abs(p-x)<=.151),key=lambda j:(scores[i,j],-j))
            rows.append({'fr_position':p,'en_position':ep[j],'cosine':round(float(scores[i,j]),6),
                         'fr_sample_sha256':sha(texts[i].encode()),'en_sample_sha256':sha(texts[3+j].encode())})
        ordered=all(a['en_position']<b['en_position'] for a,b in zip(rows,rows[1:]))
        supported=ordered and sum(r['cosine']>=.65 for r in rows)>=2 and min(r['cosine'] for r in rows)>=.50
        return {**base,'samples':rows,'monotone':ordered,
                'status':'sampled_content_support' if supported else 'sampled_content_inconclusive'}
