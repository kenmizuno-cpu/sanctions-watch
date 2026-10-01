import unittest,tempfile
from pathlib import Path
from src.dashboard import build_status_rows,build_mofa_document_rows,MOFA_DOCUMENT_COLS
from src.mofa_documents import empty_bundle,save_bundle
class DashboardTests(unittest.TestCase):
 def test_source_rows_survive_other_source_runs(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d); s,e,q=empty_bundle(); s['families']['mofa_catalog']={'last_success_at':'2026-10-01T00:00:00Z','status':'COVERAGE_GAP'}; save_bundle(p,s,e,q)
   rows=build_status_rows(p,[],{})
   self.assertEqual(len(rows),6); r=next(r for r in rows if r[0]=='外務省（現行リスト）'); self.assertEqual(r[4],''); self.assertEqual(r[1],'未確認期間あり')
 def test_resolved_rows_not_projected_and_no_limit(self):
  row={k:'' for k in ['event_id','checked_at','role','title','publication_date','publication_precision','reason','fetch_status','review_status','notice_url','url','source_hash','raw_path']}; row['review_status']='REVIEW_REQUIRED_DOCUMENT'
  self.assertEqual(len(build_mofa_document_rows([row]*6001)),6001)
  row['review_status']='CANCELLED_DOCUMENT'; self.assertEqual(build_mofa_document_rows([row]),[]); self.assertEqual(len(MOFA_DOCUMENT_COLS),13)
