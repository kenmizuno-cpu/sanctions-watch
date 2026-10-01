import unittest,tempfile,gzip,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from src.mofa_sources import *
from src.mofa_documents import *
from src.mofa_watch import run,fetch_document
from tests.test_mofa_transport import StreamResponse
from tests.test_mofa_sources import html
from tests.test_fetch_meti_policy import FakeSession

class RoutingSession:
 def __init__(self):
  self.calls=[]; self.responses={}
 def get(self,url,**kwargs):
  self.calls.append((url,kwargs)); r=self.responses[url]
  if isinstance(r,Exception): raise r
  return StreamResponse(url=url,status_code=r[0],body=r[1],headers=r[2] if len(r)>2 else {})

class WatchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
  self.now=datetime(2026,10,1,1,tzinfo=timezone.utc); self.s=RoutingSession()
  self.un='https://www.mofa.go.jp/un.pdf'; self.other='https://www.mofa.go.jp/1373.pdf'
  self.s.responses={CATALOG_URL:(200,html('<li>資産凍結措置対象リスト<a href="/un.pdf">1267号</a><a href="/1373.pdf">1373号</a></li>')),self.un:(200,b'%PDF-1.6\nUN'),self.other:(200,b'%PDF-1.6\n1373'),PRESS_URL:(200,html('<h1>報道発表 2026年10月</h1><ul class="release-list"></ul><a href="/mofaj/press/release/2026/9.html">2026年9月</a>')),'https://www.mofa.go.jp/mofaj/press/release/2026/9.html':(200,html('<h1>報道発表 2026年9月</h1><ul class="release-list"></ul><a href="/mofaj/press/release/2026/10.html">2026年10月</a>'))}
 def call(self): return run(self.root,now=self.now,session=self.s)
 def test_catalog_304_still_checks_both_pdfs(self):
  self.assertEqual(self.call(),0); self.s.calls=[]; self.s.responses[CATALOG_URL]=(304,b''); self.s.responses[self.un]=(304,b''); self.s.responses[self.other]=(200,b'%PDF-1.6\nchanged')
  self.assertEqual(self.call(),0); self.assertIn(self.un,[u for u,k in self.s.calls]); self.assertEqual(len(load_bundle(self.root)[2]),3)
 def test_bad_304_cache_retries_once(self):
  self.call(); r=load_bundle(self.root)[0]['resources']['current_un']; (self.root/r['raw_path']).unlink()
  s=FakeSession([StreamResponse(url=self.un,status_code=304),StreamResponse(url=self.un,status_code=200,body=b'%PDF-1.6\nrestored')])
  f=fetch_document(self.root,link=DocumentLink('current_un','current_pdf',self.un,'UN'),previous=r,session=s)
  self.assertEqual(len(s.calls),2); self.assertNotIn('If-None-Match',s.calls[1][1]['headers']); self.assertTrue(f.body)
 def test_failed_fetch_keeps_last_valid_cache(self):
  self.call(); before=load_bundle(self.root)[0]['resources']['current_un']; self.s.responses[self.un]=(404,b'')
  self.assertEqual(self.call(),1); after=load_bundle(self.root)[0]['resources']['current_un']; self.assertEqual(after['sha256'],before['sha256']); self.assertEqual(after['last_success_at'],before['last_success_at'])
  self.s.responses[self.un]=(304,b''); self.assertEqual(self.call(),0)
 def test_empty_or_html_pdf_is_error(self):
  for b in [b'',b'<html>error</html>']:
   self.s.responses[self.un]=(200,b); self.assertEqual(self.call(),1)
 def test_failure_in_press_does_not_hide_catalog_result(self):
  self.s.responses[PRESS_URL]=TimeoutError('timeout'); self.assertEqual(self.call(),1)
  s=load_bundle(self.root)[0]; self.assertEqual(s['families']['mofa_press']['status'],'error'); self.assertTrue(s['families']['mofa_catalog']['last_success_at'])
 def test_backlog_31_processes_30(self):
  links=''.join(f'<li><a href="/mofaj/press/release/pressit_{i:06}.html">会談</a></li>' for i in range(31))
  self.s.responses[PRESS_URL]=(200,html('<h1>報道発表</h1><h2>10月1日</h2><ul>'+links+'</ul><a href="/mofaj/press/release/2026/9.html">2026年9月</a>'))
  for i in range(31): self.s.responses[f'https://www.mofa.go.jp/mofaj/press/release/pressit_{i:06}.html']=(200,html('<h1>会談</h1><p>会談</p>'))
  self.assertEqual(self.call(),0); s=load_bundle(self.root)[0]; self.assertEqual(len(s['pending_notices']),1); self.assertTrue(s['pending_notices'][0]['baseline']); self.assertFalse(s['baseline_complete'])
  self.assertEqual(self.call(),0); self.assertTrue(load_bundle(self.root)[0]['baseline_complete'])
 def test_coverage_gap_is_not_normal(self):
  self.call(); s,e,q=load_bundle(self.root); s['last_complete_scan_at']='2026-07-01T00:00:00Z'; save_bundle(self.root,s,e,q)
  self.call(); self.assertEqual(load_bundle(self.root)[0]['families']['mofa_press']['status'],'COVERAGE_GAP')
 def test_other_master_outputs_are_byte_identical(self):
  paths=['data/master.csv','data/dashboard/changes.csv','data/ofac_index.json','dist/internal_import_latest.xlsx']
  for v in paths: p=self.root/v; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'unchanged')
  self.call(); self.assertTrue(all((self.root/v).read_bytes()==b'unchanged' for v in paths))
 def test_persistence_failure_rolls_back_bundle(self):
  self.call(); before=(self.root/STATE_PATH).read_bytes()
  with patch('src.mofa_documents.atomic_replace_many',side_effect=OSError('fail')):
   self.assertEqual(self.call(),1)
  self.assertEqual(before,(self.root/STATE_PATH).read_bytes()); self.assertTrue((self.root/'data/mofa/last_failure.json').exists())
 def test_dry_run_no_formal_data_change(self):
  self.assertEqual(run(self.root,now=self.now,session=self.s,dry_run=True),0); self.assertFalse((self.root/'data').exists())
 def test_initial_out_of_scope_notice_does_not_become_new(self):
  self.now=datetime(2026,10,20,tzinfo=timezone.utc)
  u='https://www.mofa.go.jp/mofaj/press/release/pressit_999999.html'
  self.s.responses['https://www.mofa.go.jp/mofaj/press/release/2026/9.html']=(200,html('<h1>報道発表 2026年9月</h1><h2>9月1日</h2><ul><li><a href="'+u+'">制裁</a></li></ul><a href="/mofaj/press/release/2026/10.html">10月</a>'))
  self.s.responses[u]=(200,html('<h1>制裁</h1><p>資産凍結</p>'))
  self.assertEqual(self.call(),0); self.s.calls=[]; self.assertEqual(self.call(),0)
  self.assertNotIn(u,[url for url,kw in self.s.calls]); self.assertFalse(any(q['url']==u for q in load_bundle(self.root)[2]))
 def test_known_notice_keyword_removal_keeps_unresolved_attachment_monitored(self):
  u='https://www.mofa.go.jp/mofaj/press/release/pressit_999999.html'; pdf='https://www.mofa.go.jp/attachment.pdf'
  self.s.responses[PRESS_URL]=(200,html('<h1>報道発表 2026年10月</h1><h2>10月1日</h2><ul><li><a href="'+u+'">会議</a></li></ul><a href="/mofaj/press/release/2026/9.html">9月</a>'))
  self.s.responses[u]=(200,html('<h1>会議</h1><p>資産凍結</p><a href="/attachment.pdf">別添</a>')); self.s.responses[pdf]=(200,b'%PDF-1.6\nattachment')
  self.call(); s,e,q=load_bundle(self.root); n=next(x for x in q if x['url']==u)
  review_document(self.root,event_id=n['event_id'],source_hash=n['source_hash'],reviewer='Ken',when=self.now,note='checked')
  self.s.responses[u]=(200,html('<h1>会議</h1><p>更新</p><a href="/attachment.pdf">別添</a>'))
  self.call(); self.s.calls=[]; self.s.responses[pdf]=(200,b'%PDF-1.6\nchanged'); self.call()
  self.assertIn(pdf,[url for url,kw in self.s.calls]); self.assertTrue(any(q['source_hash']==sha256(b'%PDF-1.6\nchanged') for q in load_bundle(self.root)[2]))
