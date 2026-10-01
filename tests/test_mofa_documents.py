import unittest, tempfile, copy
from pathlib import Path
from datetime import datetime, timezone
from src.fetch import Fetched, sha256
from src.mofa_sources import DocumentLink
from src.mofa_documents import *

class DocumentsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.addCleanup(self.tmp.cleanup)
  self.now=datetime(2026,10,1,tzinfo=timezone.utc)
  self.link=DocumentLink('current_un','current_pdf','https://www.mofa.go.jp/a.pdf','UN',baseline=True)
  self.state,self.events,self.queue=empty_bundle()
 def observe(self,b):
  f=Fetched(self.link.url,body=b,sha256=sha256(b),http_status=200)
  f.raw_path=save_raw(self.root,f,self.link.role)
  return observe_document(self.state,self.events,self.queue,link=self.link,fetched=f,when=self.now)
 def test_a_b_a_is_three_events_but_recheck_is_not(self):
  a=self.observe(b'A'); self.assertIsNone(self.observe(b'A')); b=self.observe(b'B'); c=self.observe(b'A')
  self.assertEqual(len({a,b,c}),3); self.assertTrue(all(e['diff_level']=='document' and e['applied']=='false' for e in self.events))
 def test_baseline_is_not_new_designation(self):
  self.observe(b'A'); self.assertEqual(self.events[0]['event_type'],'BASELINED')
 def test_url_change_same_hash(self):
  self.observe(b'A'); self.link.url='https://www.mofa.go.jp/b.pdf'; self.observe(b'A')
  self.assertEqual(self.events[-1]['event_type'],'LINK_CHANGED_SAME_CONTENT')
 def test_same_pdf_keeps_both_notice_relations(self):
  self.observe(b'A'); self.link.key='notice2'; self.link.notice_url='https://www.mofa.go.jp/n2.html'; self.observe(b'A')
  self.assertEqual(len(self.queue),2); self.assertEqual(self.queue[0]['raw_path'],self.queue[1]['raw_path'])
 def test_review_is_hash_bound(self):
  event=self.observe(b'A'); save_bundle(self.root,self.state,self.events,self.queue)
  for h,r,t in [('0'*64,'Ken',self.now),(sha256(b'A'),'',self.now),(sha256(b'A'),'Ken',self.now.replace(tzinfo=None))]:
   with self.assertRaises(DocumentStateError): review_document(self.root,event_id=event,source_hash=h,reviewer=r,when=t,note='checked')
 def test_cancelled_same_document_stays_cancelled(self):
  event=self.observe(b'A'); save_bundle(self.root,self.state,self.events,self.queue)
  review_document(self.root,event_id=event,source_hash=sha256(b'A'),reviewer='Ken',when=self.now,note='irrelevant',cancel=True)
  s,e,q=load_bundle(self.root); self.assertEqual(q[0]['review_status'],'CANCELLED_DOCUMENT')
  self.assertIsNone(observe_document(s,e,q,link=self.link,fetched=Fetched(self.link.url,body=b'A',sha256=sha256(b'A')),when=self.now))
 def test_queue_conflicting_event_id_fails_without_rewrite(self):
  self.observe(b'A'); save_bundle(self.root,self.state,self.events,self.queue)
  before=(self.root/'data/mofa/state.json').read_bytes(); q=copy.deepcopy(self.queue); q.append(dict(q[0],source_hash='0'*64))
  with self.assertRaises(DocumentStateError): save_bundle(self.root,self.state,self.events,q)
  self.assertEqual(before,(self.root/'data/mofa/state.json').read_bytes())
 def test_missing_then_reappeared_is_new_observation(self):
  self.observe(b'A'); mark_unavailable(self.state,self.events,self.queue,link=self.link,when=self.now,reason='missing'); self.observe(b'A')
  self.assertEqual(self.events[-1]['event_type'],'REAPPEARED')
 def test_current_and_unresolved_raw_are_retained(self):
  self.observe(b'A'); self.observe(b'B'); self.assertTrue(all((self.root/q['raw_path']).exists() for q in self.queue))
 def test_header_only_queue_is_corruption(self):
  self.observe(b'A'); save_bundle(self.root,self.state,self.events,self.queue)
  p=self.root/QUEUE_PATH; p.write_text(','.join(QUEUE_COLS)+'\n'); before=p.read_bytes()
  with self.assertRaises(DocumentStateError): load_bundle(self.root)
  self.assertEqual(p.read_bytes(),before)
