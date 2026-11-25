import unittest
from resolver import Document,sha
from section_match import heading_signature,find_section_pair,automatic_alignment,extract_headings
from alignment import exact_occurrences
from pilot_full import soup


def doc(url,headings,blocks):
    return Document(url,blocks,sha('\n'.join(blocks).encode()),headings=tuple(headings))


class SectionMatchTests(unittest.TestCase):
    def test_different_numbering_not_used_to_choose(self):
        fr=doc('fr',['IV. La concurrence'],['mot '*90])
        decoy=doc('wrong',['IV. The proletariat'],['word '*90])
        en=doc('en',['V. Competition'],['word '*90])
        target,info=find_section_pair(fr,[decoy,en])
        self.assertEqual(target,en)
        self.assertEqual(info['status'],'heading_pair_hypothesis')
        self.assertFalse(info['heading_evidence'][0]['same_explicit_number'])

    def test_number_only_and_generic_titles(self):
        fr=doc('fr',['IV.','Introduction','Un livre'],['mot '*90])
        en=doc('en',['IV.','Introduction','Un livre'],['word '*90])
        self.assertIsNone(find_section_pair(fr,[en],work_titles=('Un livre',))[0])

    def test_repeated_heading_abstains(self):
        fr=doc('fr',['Lutte de classe'],['mot '*90])
        e1=doc('en1',['Class struggle'],['word '*90])
        e2=doc('en2',['Class struggle'],['word '*90])
        self.assertEqual(find_section_pair(fr,[e1,e2])[1]['status'],'ambiguous_heading_pair')

    def test_book_title_requires_number_and_matching_words(self):
        fr=doc('fr',['V. La lutte de classe'],['mot '*90])
        en=doc('en',['Chapter 5. The Class Struggle'],['word '*90])
        other=doc('index',['The Class Struggle'],['word '*90])
        target,_=find_section_pair(fr,[en,other],work_titles=('The Class Struggle',))
        self.assertEqual(target,en)

    def test_length_guard(self):
        fr=doc('fr',['La concurrence'],['mot '*90])
        en=doc('en',['Competition'],['word '*300])
        self.assertIsNone(find_section_pair(fr,[en])[0])

    def test_headings_only_not_arbitrary_prose(self):
        root=soup('<h2>La concurrence</h2><p>Arbitrary paragraph.</p><p align="center"><b>Chapitre II</b></p>')
        self.assertEqual(extract_headings(root),('La concurrence','Chapitre II'))

    def test_alignment_refuses_variant(self):
        r=automatic_alignment('Le texte est ici',{'status':'variant_review_required'},[],[],(),())
        self.assertFalse(r['candidates'])
        self.assertFalse(r['auto_publish'])

    def test_alignment_uses_unique_exact_source(self):
        q='Les travailleurs sont unis dans cette lutte.'
        f=doc('fr',['Lutte de classe'],[q,' '.join(['Une phrase de contexte.']*15)])
        e=doc('en',['Class struggle'],['The workers are united in this struggle.',' '.join(['A sentence of context.']*15)])
        hits,_=exact_occurrences(q,f)
        r=automatic_alignment(q,{'status':'exact_in_examined_documents','occurrences':hits},[f],[e],(),())
        self.assertEqual(r['status'],'review_required')
        self.assertTrue(r['candidates'])
        self.assertIn('The workers are united',r['candidates'][0]['text'])
        self.assertFalse(r['candidates'][0]['boundary_verified'])
        self.assertFalse(r['auto_publish'])
