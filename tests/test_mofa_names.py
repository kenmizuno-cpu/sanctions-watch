import unittest
from src import mofa_names as N

class NameEvidenceTests(unittest.TestCase):
    def parse(self, pages, key='current_un'):
        return N.parse_pages(pages, document_key=key, source_hash='a'*64,
                             source_url='https://www.mofa.go.jp/test.pdf', raw_path='data/raw/mofa/test.pdf.gz')

    def test_numbered_reference_in_narrative_is_not_a_party(self):
        result = self.parse(['１．甲\nALPHA\n生年月日：１９８０年\nその他の情報：\n２．に指定した個人）と関係がある。\n２．乙\nBETA\n所在地：不明'])
        self.assertEqual([r['document_number'] for r in result['records']], ['1', '2'])
        self.assertEqual([r['party_type'] for r in result['records']], ['person', 'entity'])
        self.assertNotIn('に指定', ' '.join(n['name'] for n in result['names']))

    def test_cross_page_names_and_explicit_aliases_keep_page_evidence(self):
        result = self.parse(['１．アル・ラ\n', 'シード信託（別称：（a）別称甲（b）別称乙）\nAL RASHID TRUST (a.k.a.: (a)Alias A (b)Alias B)\n所在地：不明'])
        names = result['names']
        primary = next(n for n in names if n['name_kind']=='primary' and n['language']=='ja')
        self.assertEqual(primary['name'], 'アル・ラシード信託')
        self.assertEqual(primary['pages'], '1;2')
        self.assertEqual(primary['source_page_url'], 'https://www.mofa.go.jp/test.pdf#page=1')
        self.assertEqual({n['name'] for n in names if n['name_kind']=='alias'}, {'別称甲','別称乙','Alias A','Alias B'})
        self.assertTrue(all(n['source_hash']=='a'*64 and n['evidence_text'] for n in names))

    def test_deleted_entries_do_not_become_active_names(self):
        result = self.parse(['１．※２０１１年１１月１８日解除\n２．乙\nBETA\n生年月日：不明'])
        self.assertEqual(result['records'][0]['current'], 'false')
        self.assertEqual({n['document_number'] for n in result['names']}, {'2'})

    def test_weak_aliases_never_become_strong(self):
        result = self.parse(['１．甲\nALPHA\n生年月日：不明\n確定に十分でない別名：甲太郎；甲次郎\nA Taro; A Jiro\n国籍：不明'])
        weak = [n for n in result['names'] if n['name_kind']=='weak_alias']
        self.assertEqual({n['name'] for n in weak}, {'甲太郎','甲次郎','A Taro','A Jiro'})
        self.assertTrue(all(n['screening_eligible']=='false' for n in weak))

    def test_1373_repeated_numbers_are_scoped_to_section(self):
        result = self.parse(['Ⅰ 平成１４年外務省告示\n１．甲\nALPHA\n生年月日：不明\nⅡ 平成１４年外務省告示\n１．乙\nBETA\n所在地：不明'], key='current_1373')
        self.assertEqual(len({r['source_record_id'] for r in result['records']}), 2)

    def test_zero_text_and_unknown_schema_are_blocked(self):
        for pages in [[''], ['別の資料\n名前：甲']]:
            with self.subTest(pages=pages):
                self.assertEqual(self.parse(pages)['status'], 'BLOCKED')

    def test_unknown_type_and_annotated_alias_require_review(self):
        result = self.parse(['１．甲\nALPHA\n別名：乙（１９８０年生まれ）\n国籍：不明'])
        self.assertEqual(result['records'][0]['party_type'], 'unknown')
        annotated = next(n for n in result['names'] if n['name_kind']=='alias')
        self.assertEqual(annotated['parse_status'], 'REVIEW_REQUIRED')
        self.assertEqual(annotated['screening_eligible'], 'false')
        self.assertTrue(result['issues'])

    def test_original_script_keeps_original_unicode(self):
        result = self.parse(['１．甲\nALPHA\n(original script: محمد)\n国連参照番号：QDi.001\n生年月日：不明'])
        n = next(n for n in result['names'] if n['name_kind']=='original_script')
        self.assertEqual(n['name'], 'محمد')
        self.assertEqual(result['records'][0]['source_external_id'], 'QDi.001')

    def test_aka_without_colon_and_quoted_primary(self):
        result = self.parse(["１．甲（別名：乙）\n‘ALPHA (a.k.a. (a)Beta (b)Gamma)\n生年月日：不明"])
        self.assertIn('‘ALPHA', {n['name'] for n in result['names'] if n['name_kind']=='primary'})
        self.assertEqual({n['name'] for n in result['names'] if n['name_kind']=='alias'}, {'乙','Beta','Gamma'})

    def test_japanese_alias_with_latin_initial_keeps_language(self):
        result = self.parse(['１．甲\nALPHA\n生年月日：不明\n別名：A・カビール; 乙\nA. Kabir; Beta\n国籍：不明'])
        self.assertEqual({n['name'] for n in result['names'] if n['name_kind']=='alias' and n['language']=='ja'}, {'A・カビール','乙'})

    def test_language_annotation_does_not_truncate_record(self):
        result = self.parse(['１．甲（別称：乙\n【アラビア語】）\nALPHA\n所在地：不明'])
        self.assertEqual(result['records'][0]['party_type'], 'entity')

    def test_official_entity_notice_link_is_type_evidence(self):
        result = self.parse(['１．甲\nALPHA\n住所：不明\nその他の情報：\nhttps://www.interpol.int/en/How-we-work/Notices/View-UN-Notices-Entities'])
        self.assertEqual(result['records'][0]['party_type'], 'entity')

    def test_inline_english_primary_and_latin_japanese_alias_boundary(self):
        for text in ['１．甲（別名：乙）ALPHA\n生年月日：不明',
                     '１．甲（別名：乙、\nPFLP-GC）\nALPHA\n生年月日：不明']:
            result=self.parse([text])
            self.assertEqual([n['name'] for n in result['names'] if n['name_kind']=='primary' and n['language']=='en'], ['ALPHA'])

    def test_alias_marker_without_colon_and_former_name(self):
        result=self.parse(['１．甲（別名 乙）\nALPHA (f.k.a. Beta)\n生年月日：不明'])
        self.assertEqual({n['name'] for n in result['names'] if n['name_kind']=='primary'}, {'甲','ALPHA'})
        self.assertEqual({n['name'] for n in result['names'] if n['name_kind']=='former_name'}, {'Beta'})

    def test_alternative_original_script_syntax(self):
        result=self.parse(['１．甲\nALPHA (original script): Альфа\n生年月日：不明'])
        self.assertEqual({n['name'] for n in result['names'] if n['name_kind']=='primary'}, {'甲','ALPHA'})
        self.assertIn('Альфа', {n['name'] for n in result['names'] if n['name_kind']=='original_script'})

    def test_image_page_and_number_gap_are_blocked(self):
        result=self.parse(['１．甲\nALPHA\n生年月日：不明', '', '３．乙\nBETA\n所在地：不明'])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('IMAGE_OR_EMPTY_PAGE', {i['code'] for i in result['issues']})

    def test_rtl_script_is_flagged_for_visual_review(self):
        result=self.parse(['１．甲\nALPHA\n(original script: محمد)\n生年月日：不明'])
        self.assertIn('RTL_ORDER_REVIEW', {i['code'] for i in result['issues']})

    def test_comma_alias_list_is_retained_as_ambiguous_not_guessed(self):
        result=self.parse(['１．甲（別名：乙、丙）\nALPHA (a.k.a.: Beta, Gamma)\n生年月日：不明'])
        aliases=[n for n in result['names'] if n['name_kind']=='alias']
        self.assertTrue(all(n['parse_status']=='REVIEW_REQUIRED' for n in aliases))

    def test_alias_labels_inside_annotations_are_not_new_names(self):
        result=self.parse(['１．甲（別名：（a）乙（備考：（a）旧称；（b）通称）（b）丙）\nALPHA\n生年月日：不明'])
        self.assertEqual(len([n for n in result['names'] if n['name_kind']=='alias']),2)


    def test_wrapped_alias_label_and_missing_close_keep_english_primary(self):
        for heading in ['１．甲（別\n名：乙）', '１．甲（別名：乙']:
            result=self.parse([heading+'\nALPHA\n(original script: محمد)\n生年月日：不明'])
            self.assertEqual({n['name'] for n in result['names'] if n['name_kind']=='primary'}, {'甲','ALPHA'})

    def test_missing_alias_close_preserves_parenthesized_english_primary(self):
        result=self.parse(['１．甲（別名：乙\nALPHA (ABC)\n(a.k.a.: Beta)\n所在地：不明'])
        self.assertIn('ALPHA (ABC)', {n['name'] for n in result['names'] if n['name_kind']=='primary'})

    def test_multiple_original_scripts_on_same_line_are_distinct(self):
        result=self.parse(['１．甲\nALPHA (original script: Альфа) (a.k.a.: Beta (original script: Бета))\n生年月日：不明'])
        self.assertEqual([n['name'] for n in result['names'] if n['name_kind']=='original_script'], ['Альфа','Бета'])

    def test_wrapped_alternative_original_script_keeps_surname(self):
        result=self.parse(['１．甲\nALPHA (original script): Тархан\nИсмаилович Газиев (a.k.a.: Beta)\n生年月日：不明'])
        self.assertEqual([n['name'] for n in result['names'] if n['name_kind']=='original_script'], ['Тархан Исмаилович Газиев'])

    def test_inline_metadata_does_not_become_a_weak_alias(self):
        result=self.parse(['１．甲\nALPHA\n生年月日：不明\n確定に十分でない別名：アブ・ガイス Abo Ghaith 国籍：２００２年に国籍を\n剥奪された。'])
        weak=[n for n in result['names'] if n['name_kind']=='weak_alias']
        self.assertEqual(len(weak),1)
        self.assertNotIn('国籍',weak[0]['name'])
        self.assertEqual(weak[0]['parse_status'],'REVIEW_REQUIRED')

class ExtractionSafetyTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_missing_documents_are_reported_and_stale_projection_is_cleared(self):
        import json
        out=self.root/N.OUTPUT;out.mkdir(parents=True)
        (out/'names.csv').write_text('STALE')
        result=N.extract(self.root)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['name_count'],0)
        self.assertNotIn('STALE',(out/'names.csv').read_text())
        self.assertEqual(json.loads((out/'report.json').read_text())['applied'],False)

    def test_corrupt_original_is_blocked_and_phase3a_and_master_are_unchanged(self):
        import csv
        from datetime import datetime,timezone
        from src import mofa_manual as M
        from tests.test_mofa_manual import pdf
        f=self.root/'input.pdf';f.write_bytes(pdf())
        M.import_file(self.root,file=f,role='current_un',source_url='https://www.mofa.go.jp/un.pdf',
                      operator='Ken',when=datetime(2026,10,2,tzinfo=timezone.utc),note='official')
        state,_,_=N.D.load_bundle(self.root)
        master=self.root/'data/master.csv';master.write_bytes(b'name\nKEEP\n')
        before={p:p.read_bytes() for p in [self.root/N.D.STATE_PATH,self.root/N.D.EVENT_PATH,self.root/N.D.QUEUE_PATH,master]}
        (self.root/state['resources']['current_un']['raw_path']).write_bytes(b'not-gzip')
        result=N.extract(self.root)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['name_count'],0)
        self.assertTrue(all(p.read_bytes()==body for p,body in before.items()))
        with (self.root/N.OUTPUT/'issues.csv').open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))),2)

    def test_same_input_produces_identical_projection(self):
        out=self.root/N.OUTPUT
        N.extract(self.root)
        before={p.name:p.read_bytes() for p in out.iterdir()}
        N.extract(self.root)
        self.assertEqual(before,{p.name:p.read_bytes() for p in out.iterdir()})


if __name__ == '__main__': unittest.main()
