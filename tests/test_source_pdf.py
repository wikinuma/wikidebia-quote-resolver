import unittest
from unittest.mock import Mock,patch
from resolver import ROOT,Document
from source_pdf import load_pdf_catalog,section_documents,locate,SourcePDFs,extract_pdf


class SourcePDFTests(unittest.TestCase):
    def setUp(self):
        self.entry=load_pdf_catalog(ROOT/'source-pdf-catalog.json')[0]
        self.assertTrue(self.entry['sections'][0]['en_url'].endswith('/intro.htm'))
        self.quote='Une phrase précise et vérifiable dans le premier chapitre.'
        self.entry.pop('expected_pdf_pages')
        self.entry={**self.entry,'skip_pages':1,'sections':[
            {'heading':'I. Premier chapitre','en_url':'https://www.marxists.org/archive/example/first.htm'},
            {'heading':'III. Deuxième chapitre','en_url':'https://www.marxists.org/archive/example/second.htm'},
            {'heading':'IV. Fin','en_url':None}]}
        self.filler=' '.join('contexte'+str(i) for i in range(80))
        self.raw='I. Premier chapitre\nIII. Deuxième chapitre\n\fI. Premier chapitre\n\n'+self.quote+'\n'+self.filler+'\n1\fIII. Deuxième\nchapitre\n\n'+self.filler+'\n2\fIV. Fin\n\nAnnexe\n3\f'

    def test_sections_are_not_toc_or_number_based(self):
        sections,meta=section_documents(self.raw,self.entry,'digest')
        self.assertEqual(len(sections),2)
        punctuated=self.raw.replace('IV. Fin','***\nIV. Fin')
        self.assertEqual(len(section_documents(punctuated,self.entry,'digest')[0]),2)
        self.assertEqual(meta['removed_numbered_footers'],3)
        h,status=locate(self.quote,sections)
        self.assertEqual(status,'exact')
        self.assertEqual(h[0]['en_url'],self.entry['sections'][0]['en_url'])
        self.assertEqual(h[2]['pdf_page_start'],2)
        self.assertEqual(h[2]['coordinate_scope'],'extracted_section_blocks_v1')
        self.assertNotIn('\n1',h[1].blocks)
        with self.assertRaisesRegex(ValueError,'source_pdf_heading_missing_or_ambiguous'):
            section_documents(self.raw.replace('IV. Fin','Autre fin'),self.entry,'digest')
        with self.assertRaisesRegex(ValueError,'source_pdf_heading_missing_or_ambiguous'):
            section_documents(self.raw.replace('IV. Fin','III. Deuxième chapitre\nIV. Fin'),self.entry,'digest')

    def test_page_anchors_accept_wrapped_heading_and_exact_page_number(self):
        entry={**self.entry,'expected_pdf_pages':4,
               'sections':[{**s,'pdf_page':i+2} for i,s in enumerate(self.entry['sections'])]}
        # Heading fragments may be split over physical lines, and a page
        # number may precede a heading on the same line. The body is kept.
        raw=self.raw.replace('I. Premier chapitre\n\n'+self.quote,
                             '1 I.\n\nPremier\nchapitre '+self.quote)
        # Avoid a duplicated synthetic printed number on this page.
        raw=raw.replace(self.filler+'\n1\f',self.filler+'\f')
        raw=raw.replace('III. Deuxième\nchapitre','2\nIII.\n\nDeuxième\nchapitre')
        raw=raw.replace(self.filler+'\n2\f',self.filler+'\f')
        sections,meta=section_documents(raw,entry,'digest')
        self.assertEqual(meta['verified_heading_pages'],[2,3,4])
        self.assertEqual(locate(self.quote,sections)[1],'exact')
        self.assertTrue(sections[0][1].blocks[0].startswith(self.quote))
        self.assertEqual(sections[0][2]['pdf_page_start'],2)
        self.assertEqual(sections[0][2]['pdf_page_end'],2)
        with self.assertRaisesRegex(ValueError,'source_pdf_heading_mismatch: page 3'):
            section_documents(raw.replace('Deuxième\nchapitre','Autre\nchapitre'),entry,'digest')
        with self.assertRaisesRegex(ValueError,'source_pdf_page_count_mismatch'):
            section_documents(raw+'Autre page\f',entry,'digest')
        with self.assertRaisesRegex(ValueError,'invalid_source_pdf_page_anchors'):
            section_documents(raw,{**entry,'sections':[entry['sections'][0],self.entry['sections'][1],entry['sections'][2]]},'digest')

    def test_ambiguous_exact_source_abstains(self):
        sections,_=section_documents(self.raw.replace('chapitre\n\n'+self.filler,
                'chapitre\n\n'+self.quote+'\n'+self.filler),self.entry,'digest')
        self.assertEqual(locate(self.quote,sections),(None,'source_pdf_ambiguous'))

    def test_review_candidate_keeps_target_html_and_source_scope(self):
        client=Mock()
        client.get.return_value=(b'%PDF-fixture',self.entry['pdf_url'])
        engine=SourcePDFs(client,[self.entry])
        row={'navigation':{'input':{'mia_urls':[self.entry['source_url']],
             'authors':self.entry['authors'],'work':self.entry['source_title']}},'candidates':[]}
        en=Document(self.entry['sections'][0]['en_url'],['A precise sentence in the first chapter.'],'en')
        align=Mock()
        align.candidate.return_value={'status':'review_required','candidates':[{'url':en.url,'text':en.blocks[0]}]}
        fetch=Mock(return_value=en)
        with patch('source_pdf.extract_pdf',return_value=self.raw), patch('source_pdf.rank',return_value=[]), patch('source_pdf.rank_short_windows',return_value={'candidates':[]}):
            self.assertEqual(engine.retrieve(dict(row),self.quote,align,fetch)['status'],'source_pdf_candidate_review_required')
            got=engine.retrieve(dict(row),self.quote,align,fetch)
        client.get.assert_called_once_with(self.entry['pdf_url'])
        self.assertFalse(got['auto_publish'])
        self.assertEqual(got['rights'],'unreviewed')
        self.assertEqual(got['target_attempts'][0]['pages'][0]['url'],en.url)
        self.assertEqual(got['source']['occurrences'][0]['url'],self.entry['pdf_url'])
        bad={'navigation':{'input':{**row['navigation']['input'],'authors':'Wrong author'}}}
        self.assertIsNone(engine.retrieve(bad,self.quote,align,fetch))

    def test_failed_preflight_is_not_a_negative_lookup_or_cached_failure(self):
        client=Mock()
        client.get.return_value=(b'%PDF-fixture',self.entry['pdf_url'])
        engine=SourcePDFs(client,[self.entry])
        with patch('source_pdf.extract_pdf',side_effect=[self.raw.replace('IV. Fin','Autre fin'),self.raw]):
            with self.assertRaisesRegex(ValueError,'source_pdf_heading_missing_or_ambiguous'):
                engine.prepare(self.entry)
            self.assertEqual(engine.cache,{})
            self.assertEqual(engine.prepare(self.entry)['sections'],2)
        self.assertEqual(client.get.call_count,2)

    def test_extract_rejects_non_pdf_and_uses_no_shell(self):
        with self.assertRaisesRegex(ValueError,'invalid_source_pdf_body'):
            extract_pdf(b'<html>Not a PDF</html>')
        with patch('source_pdf.subprocess.run',return_value=Mock(stdout=b'Un document textuel')) as runner:
            self.assertEqual(extract_pdf(b'%PDF-example'),'Un document textuel')
            self.assertEqual(runner.call_args.args[0],['pdftotext','-enc','UTF-8','-','-'])
            self.assertNotIn('shell',runner.call_args.kwargs)


if __name__=='__main__':
    unittest.main()
