import unittest
from unittest.mock import Mock,patch
from resolver import ROOT,Document
from source_pdf import load_pdf_catalog,section_documents,locate,SourcePDFs,extract_pdf


class SourcePDFTests(unittest.TestCase):
    def setUp(self):
        self.entry=load_pdf_catalog(ROOT/'source-pdf-catalog.json')[0]
        self.quote='Une phrase précise et vérifiable dans le premier chapitre.'
        self.entry={**self.entry,'skip_pages':1,'sections':[
            {'heading':'I. Premier chapitre','en_url':'https://www.marxists.org/archive/example/first.htm'},
            {'heading':'III. Deuxième chapitre','en_url':'https://www.marxists.org/archive/example/second.htm'},
            {'heading':'IV. Fin','en_url':None}]}
        self.filler=' '.join('contexte'+str(i) for i in range(80))
        self.raw='I. Premier chapitre\nIII. Deuxième chapitre\n\fI. Premier chapitre\n\n'+self.quote+'\n'+self.filler+'\n1\fIII. Deuxième\nchapitre\n\n'+self.filler+'\n2\fIV. Fin\n\nAnnexe\n3\f'

    def test_sections_are_not_toc_or_number_based(self):
        sections,meta=section_documents(self.raw,self.entry,'digest')
        self.assertEqual(len(sections),2)
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

    def test_extract_rejects_non_pdf_and_uses_no_shell(self):
        with self.assertRaisesRegex(ValueError,'invalid_source_pdf_body'):
            extract_pdf(b'<html>Not a PDF</html>')
        with patch('source_pdf.subprocess.run',return_value=Mock(stdout=b'Un document textuel')) as runner:
            self.assertEqual(extract_pdf(b'%PDF-example'),'Un document textuel')
            self.assertEqual(runner.call_args.args[0],['pdftotext','-enc','UTF-8','-','-'])
            self.assertNotIn('shell',runner.call_args.kwargs)


if __name__=='__main__':
    unittest.main()
