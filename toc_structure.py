"""Experimental full-file TOC parsing and conservative bracketed hypotheses.

Fragments of a linked file are not alternative chapter titles. Preserve
ordinary collector positions separately from the new explicit file order.
Never invent a missing URL. Bracketed pairs require equal, gap-free runs
between strong anchors plus sampled content corroboration before routing.
"""
from collections import defaultdict
import posixpath
from urllib.parse import urljoin,urlsplit
from auto_retrieve import explicit_toc,mia_toc_links
from navigation import canonical_mia
from pilot_full import soup
from resolver import norm,sha
from toc_heading import TocMatcher,align_headings,heading,root_key

MARKERS={'sommaire','table des matieres','table of contents','contents'}


def entries_v2(body,root,lang):
    root=canonical_mia(root)
    prefix='/francais/' if lang=='fr' else '/archive/'
    if not root or not urlsplit(root).path.startswith(prefix):
        return []
    tree=soup(body)
    if not explicit_toc(body,root):
        # Older tables often use an isolated TD or SPAN for the marker.
        if not any(norm(n.get_text(' ',strip=True)) in MARKERS
                   for n in tree.find_all(['td','th','span','div','li','caption'])):
            return []
    path=urlsplit(root).path
    directory=path if path.endswith('/') else posixpath.dirname(path)+'/'
    ordinary={r['url']:i for i,r in enumerate(mia_toc_links(body,root))}
    labels=defaultdict(dict);positions={}
    for a in tree.find_all('a',href=True):
        full=urljoin(root,a['href'])
        parsed=urlsplit(full)
        if parsed.fragment or parsed.query:
            continue
        u=canonical_mia(full)
        if not u or root_key(u)==root_key(root):
            continue
        p=urlsplit(u).path
        if not p.startswith(prefix) or posixpath.dirname(p)+'/'!=directory or not p.lower().endswith(('.htm','.html')):
            continue
        if u not in positions: positions[u]=len(positions)
        label=heading(a.get_text(' ',strip=True))
        if label: labels[u].setdefault(norm(label),label)
    return [{'url':u,'heading':next(iter(labels[u].values())),'position':position,
             'ordinary_position':ordinary.get(u)}
            for u,position in positions.items() if len(labels[u])==1]


def bracketed_pairs(fr,en,anchors,encoder):
    """Only equal, short contiguous gaps bounded by strong pairs.
    A weak heading is never enough to establish a chapter correspondence.
    """
    import numpy as np
    anchors=sorted(anchors,key=lambda p:p['fr']['position'])
    f={p['position']:p for p in fr};e={p['position']:p for p in en}
    pending=[]
    for left,right in zip(anchors,anchors[1:]):
        a,b=left['fr']['position'],left['en']['position']
        c,d=right['fr']['position'],right['en']['position']
        if c-a!=d-b or not 2<=c-a<=5: continue
        if not all(a+i in f and b+i in e for i in range(1,c-a)): continue
        for i in range(1,c-a):
            pending.append((f[a+i],e[b+i],left,right))
    if not pending:return []
    texts=[x['heading'] for p in pending for x in p[:2]]
    v=np.asarray(encoder.encode(texts),dtype=np.float64)
    if v.ndim!=2 or v.shape[0]!=len(texts) or not np.isfinite(v).all():
        raise ValueError('invalid_heading_embeddings')
    n=np.linalg.norm(v,axis=1)
    if np.any(n<=0):raise ValueError('zero_heading_vector')
    v=v/n[:,None]
    out=[]
    for i,(frow,erow,left,right) in enumerate(pending):
        cos=float(np.clip(v[2*i]@v[2*i+1],-1,1))
        if cos<.48:continue
        out.append({'fr':frow,'en':erow,'cosine':round(cos,6),
                    'brackets':[[p['fr']['url'],p['en']['url']] for p in (left,right)],
                    'method':'equal_contiguous_bracketed_gap','requires_content_probe':True,
                    'scope':'explicit_toc_document_hypothesis','score_is_probability':False,
                    'rights':'unreviewed','auto_publish':False})
    return out


class StructureMatcher(TocMatcher):
    def __init__(self,client,encoder,interpolate=True):
        super().__init__(client,encoder)
        self.interpolate=interpolate

    def toc(self,root,lang):
        key=(root,lang)
        if key not in self.tocs:
            body,final=self.client.get(root)
            if root_key(final)!=root_key(root):raise ValueError('toc_redirect_outside_root')
            self.tocs[key]=(entries_v2(body,final,lang),
                            {'requested_url':root,'url':final,'sha256':sha(body)})
        return self.tocs[key]

    def match(self,fr_root,en_root):
        key=(fr_root,en_root)
        if key not in self.matches:
            fr,fr_receipt=self.toc(fr_root,'fr');en,en_receipt=self.toc(en_root,'en')
            result=align_headings(fr,en,self.encoder)
            extra=bracketed_pairs(fr,en,result['pairs'],self.encoder) if self.interpolate else []
            self.matches[key]={**result,'pairs':result['pairs']+extra,
                               'bracketed_hypotheses':len(extra),
                               'fr_count':len(fr),'en_count':len(en),
                               'fr_toc':fr_receipt,'en_toc':en_receipt,'auto_publish':False}
        return self.matches[key]
