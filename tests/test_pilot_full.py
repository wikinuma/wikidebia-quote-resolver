import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import requests
from pilot_full import *


def html_links(urls):
    return ('<html><body>'+''.join(f'<a href="{url}">{label}</a>' for url,label in urls)+'</body></html>').encode()


def cache_put(directory,url,body,final=None,params=None):
    prepared=requests.Request('GET',url,params=sorted((params or {}).items())).prepare().url
    path=Path(directory)/(sha(prepared.encode())+'.json')
    path.write_text(json.dumps({'request_url':prepared,'url':final or prepared,'at':1,'sha256':sha(body),'body':base64.b64encode(body).decode()}))
    return path,prepared


class PilotTests(unittest.TestCase):
    def test_manifesto_all_prefaces_cross_directory(self):
        spec=WORKS['manifesto']['fr']; body=html_links([(p,'chapter') for p in spec['required']]+[(FR_MAN+'a.htm#preamble','duplicate'),('/francais/marx/index.htm','unrelated'),('https://example.com'+FR_MAN+'a.htm','wrong host')]); rows,issues=index_links(body,spec['index'],spec)
        self.assertEqual(len(rows),11); self.assertEqual(issues,[]); self.assertEqual(len({x['url'] for x in rows}),11)

    def test_manifesto_en_preamble_same_page(self):
        spec=WORKS['manifesto']['en']; body=html_links([(p,'chapter') for p in spec['required']]+[('ch01.htm#preamble','preamble'),('preface.htm#appendix','appendix'),('/archive/marx/works/1847/11/prin-com.htm','related work')]); rows,issues=index_links(body,spec['index'],spec)
        self.assertEqual(len(rows),5); self.assertFalse(issues)

    def test_missing_toc_link_and_changed_count(self):
        spec=WORKS['manifesto']['en']; rows,issues=index_links(html_links([(spec['required'][0],'preface')]),spec['index'],spec)
        self.assertEqual({x['kind'] for x in issues},{'required_toc_links_missing','toc_count_changed'})

    def test_layout_table_and_inline_text(self):
        body=b'<table><tr><td><p>Co<em>operation</em> is necessary<sup><a href="#1">(1)</a></sup>.</p><blockquote><p>First paragraph.</p><p>Second paragraph.</p></blockquote></td></tr></table>'
        self.assertEqual(extract_blocks(body),['Cooperation is necessary.','First paragraph.','Second paragraph.'])

    def test_engels_toc_external_prefaces_and_appendices(self):
        for lang,count in [('fr',19),('en',20)]:
            spec=WORKS['engels-condition'][lang]
            body=html_links([(p,'section') for p in spec['required']]+[(spec['index'],'home'),('/archive/marx/works/1900/index.htm','unrelated')])
            rows,issues=index_links(body,spec['index'],spec)
            self.assertEqual(len(rows),count); self.assertFalse(issues)

    def test_legacy_note_marker_and_encoding(self):
        body='<p>Une révolution [<a href="#n1">1</a>] sociale en 1848.</p>'.encode('cp1252')
        self.assertEqual(norm(extract_blocks(body)[0]),'une revolution sociale en 1848')
        self.assertIn('révolution',extract_blocks(body)[0])

    def test_replay_detects_missing_or_changed_response(self):
        a={'request_url':MIA+'/a','status':'ok','sha256':'a','final_url':MIA+'/a'}
        b={'request_url':MIA+'/b','status':'error','error':'blocked'}
        self.assertEqual(replay_mismatches({a['request_url']:a,b['request_url']:b},[a,b]),[])
        self.assertEqual(replay_mismatches({a['request_url']:a,b['request_url']:b},[{**a,'sha256':'b'}]),[a['request_url'],b['request_url']])

    def test_api_unavailable_html_quote_fallback(self):
        c=Mock(); c.api.side_effect=RuntimeError('api blocked'); c.get.return_value=(b'<div class="mw-parser-output"><blockquote><p>Une <em>citation</em> assez longue.</p><p>La fin.</p></blockquote></div>','https://fr.wikidebates.org/wiki/Test')
        text,prov=wiki_input(c,{'wiki_title':'Test','selector':'citation assez longue'},True)
        self.assertEqual(text,'Une citation assez longue. La fin.'); self.assertEqual(prov['method'],'public_html'); self.assertEqual(prov['attempts'][0]['method'],'api_parse')

    def test_html_quote_ambiguous_refuses(self):
        c=Mock(); c.get.return_value=(b'<blockquote>Une citation assez longue.</blockquote><blockquote>Une citation assez longue.</blockquote>','https://fr.wikidebates.org/wiki/Test')
        with self.assertRaisesRegex(RuntimeError,'quote_selector_matches=2'): wiki_input(c,{'wiki_title':'Test','selector':'citation assez longue'},False)

    def test_work_level_pairing_independent_of_gold(self):
        f=Document(MIA+FR_ENG+'_3.htm',['source'], 'f'); e=Document(MIA+EN_ENG+'ch05.htm',['target'],'e')
        info={'sides':{'fr':{'discovered':[{'label':'La concurrence','url':f.url}]},'en':{'discovered':[{'label':'Competition','url':e.url}]}}}
        self.assertEqual(mia_pairs('engels-condition',{'fr':[f],'en':[e]},info),[(f,e,'work_level_section_mapping_not_gold_passage')])
        info['sides']['fr']['discovered'].append({'label':'La concurrence','url':MIA+FR_ENG+'_9.htm'})
        self.assertEqual(mia_pairs('engels-condition',{'fr':[f],'en':[e]},info),[])

    def test_wr_html_root_langlink_and_chapters(self):
        fr=WR+'fr/Oeuvre'; en=WR+'en/Work'; sub=en+'/05/Chapter'
        data={fr:(b'<div class="mw-parser-output"><p>French prose.</p></div><a class="interlanguage-link-target" href="/texts/en/Work">EN</a>',fr),en:(b'<div class="mw-parser-output"><a href="/texts/en/Work/05/Chapter">Chapter</a><a href="/texts/en/Other">Other</a></div>',en),sub:(b'<p>English prose.</p>',sub)}
        c=Mock(); c.get.side_effect=lambda u:data[u]
        docs,info,_=wr_public_work(c,{'wr_fr':'Oeuvre'},10)
        self.assertEqual([d.url for d in docs['en']],[en,sub]); self.assertFalse(info['scope_complete']); self.assertEqual(info['issues'],[]); self.assertEqual(c.get.call_count,3)
        docs,info,_=wr_public_work(c,{'wr_fr':'Oeuvre'},1)
        self.assertIn('page_limit',[x['kind'] for x in info['issues']])

    def test_wr_ambiguous_langlink_does_not_pick_first(self):
        fr=WR+'fr/Oeuvre'; c=Mock(); c.get.return_value=(b'<p>French prose.</p><a hreflang="en" href="/texts/en/One">EN</a><a hreflang="en" href="/texts/en/Two">EN</a>',fr)
        docs,info,_=wr_public_work(c,{'wr_fr':'Oeuvre'},10)
        self.assertFalse(docs['en']); self.assertEqual(info['issues'][0]['kind'],'missing_or_ambiguous_root_langlink')

    def test_offline_never_uses_network_and_detects_corruption(self):
        url=MIA+'/test.htm'; body=b'<p>Text.</p>'
        with tempfile.TemporaryDirectory() as tmp:
            p,key=cache_put(tmp,url,body); c=PilotClient(tmp,offline=True)
            with patch('resolver.requests.Session',side_effect=AssertionError('network forbidden')):
                self.assertEqual(c.get(url),(body,url)); self.assertEqual(c.requests,0)
                with self.assertRaisesRegex(RuntimeError,'offline_cache_miss'): c.get(MIA+'/missing.htm')
                entry=json.loads(p.read_text()); entry['sha256']='bad'; p.write_text(json.dumps(entry))
                with self.assertRaisesRegex(RuntimeError,'invalid_offline_cache_entry'): PilotClient(tmp,offline=True).get(url)

    def test_exact_replay_response_fingerprint(self):
        url=MIA+'/test.htm'; body=b'<p>Text.</p>'
        with tempfile.TemporaryDirectory() as tmp:
            _,key=cache_put(tmp,url,body); ev={key:{'status':'ok','sha256':sha(body),'final_url':url}}
            c=PilotClient(tmp,offline=True,expected_events=ev); self.assertEqual(c.get(url),(body,url))
            cache_put(tmp,url,b'<p>Other.</p>')
            with self.assertRaisesRegex(RuntimeError,'replay_response_mismatch'): PilotClient(tmp,offline=True,expected_events=ev).get(url)
            with self.assertRaisesRegex(RuntimeError,'replay_unrecorded_request'): c.get(MIA+'/new.htm')

    def test_redaction_removes_candidate_text(self):
        r=redact_text({'nested':[{'text':'Some source text','score':.9}]})
        self.assertNotIn('text',r['nested'][0]); self.assertEqual(r['nested'][0]['text_words'],3); self.assertEqual(r['nested'][0]['text_sha256'],sha(b'Some source text'))

    def test_full_pilot_offline_end_to_end_with_synthetic_pages(self):
        # Entire simulated work indices, not preselected snippets only; 11 FR
        # and 5 EN pages. These are artificial HTML documents, never claimed
        # to be the historical texts. The gold URL is used only for evaluation.
        case={'id':'manifesto-ii','wiki_title':'Test','selector':'libre développement de chacun','expected_en_mia':MIA+EN_MAN+'ch02.htm','expected_needle':'free development of each'}
        with tempfile.TemporaryDirectory() as tmp:
            cache_put(tmp,html_url(WD,'Test'),'<blockquote>Le libre développement de chacun est la condition de tous.</blockquote>'.encode())
            for lang in ('fr','en'):
                spec=WORKS['manifesto'][lang]; cache_put(tmp,spec['index'],html_links([(p,'section') for p in spec['required']]))
                for path in spec['required']:
                    body='<p>Artificial unrelated paragraph about the sea.</p>'
                    if path==FR_MAN+'b.htm': body='<p>Le libre développement de chacun est la condition de tous.</p>'
                    if path==EN_MAN+'ch02.htm': body='<p>The free development of each is the condition for all.</p>'
                    cache_put(tmp,MIA+path,body.encode())
            c=PilotClient(tmp,offline=True)
            with patch('resolver.requests.Session',side_effect=AssertionError('network forbidden')):
                r=run_pilot(c,[case],['mia'],40)
            self.assertEqual(r['status'],'review_required'); self.assertEqual(r['requests'],0); self.assertEqual(r['cases'][0]['documents'],{'fr':11,'en':5}); self.assertTrue(r['works']['manifesto:mia']['scope_complete']); self.assertEqual(r['cases'][0]['methods']['lexical']['evaluation']['rank'],1)
            self.assertFalse(r['cases'][0]['auto_publish']); self.assertEqual(r['metrics']['mia']['methods']['lexical']['anchor_top1'],1)
            changed={**case,'expected_en_mia':MIA+EN_MAN+'ch04.htm','expected_needle':'impossible anchor'}
            r2=run_pilot(PilotClient(tmp,offline=True),[changed],['mia'],40)
            self.assertEqual(r['cases'][0]['methods']['lexical']['candidates'],r2['cases'][0]['methods']['lexical']['candidates']); self.assertIsNone(r2['cases'][0]['methods']['lexical']['evaluation']['rank'])
            # Delete one unrelated page: coverage cannot be called complete.
            (Path(tmp)/(sha((MIA+EN_MAN+'ch04.htm').encode())+'.json')).unlink()
            r3=run_pilot(PilotClient(tmp,offline=True),[case],['mia'],40)
            self.assertFalse(r3['cases'][0]['scope_complete']); self.assertEqual(r3['status'],'incomplete'); self.assertEqual(r3['metrics']['mia']['methods']['lexical']['anchor_evaluable_complete_scope'],0)

    def test_failed_input_not_a_negative_result(self):
        case={'id':'manifesto-ii','wiki_title':'Test','selector':'quote'}
        with tempfile.TemporaryDirectory() as tmp:
            r=run_pilot(PilotClient(tmp,offline=True),[case],['mia','wikirouge'],40)
            self.assertEqual(r['status'],'incomplete'); self.assertFalse(r['works']); self.assertEqual([x['status'] for x in r['cases']],['input_unavailable']*2); self.assertEqual(r['metrics']['mia']['methods'],{})

if __name__=='__main__': unittest.main()
