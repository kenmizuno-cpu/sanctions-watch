import unittest
from src.mofa_sources import *
U='https://www.mofa.go.jp/mofaj/press/release/index.html'
def html(s): return ('<main id="contents"><div id="maincontents">'+s+'</div></main><footer>制裁</footer>').encode()
class SourcesTests(unittest.TestCase):
 def test_catalog_selects_current_lists_not_resolution_text(self):
  b=html('<li>決議1373<a href="/text.pdf">原文</a></li><li>資産凍結措置対象リスト<a href="/un.pdf">1267号に基づくもの</a><a href="/1373.pdf">1373号に基づくもの</a></li>')
  self.assertEqual({x.key for x in parse_catalog(b,url=U)},{'current_un','current_1373'})
 def test_missing_or_duplicate_current_role(self):
  for s in ['資産凍結措置対象リスト<a href="/a.pdf">1267号</a>','資産凍結措置対象リスト<a href="/a.pdf">1267号</a><a href="/b.pdf">1267号</a><a href="/c.pdf">1373号</a>']:
   with self.assertRaises(MofaSchemaError): parse_catalog(html('<li>'+s+'</li>'),url=U)
 def test_body_only_keyword_and_no_pdf(self):
  x=parse_release_body(html('<h1>お知らせ</h1><p>資産凍結の措置</p>'),link=DocumentLink('n','notice',U,'お知らせ'))
  self.assertTrue(x.reasons); self.assertFalse(x.attachments)
 def test_navigation_keyword_is_ignored(self):
  self.assertFalse(parse_release_body(html('<h1>会談</h1><p>会談しました</p>'),link=DocumentLink('n','notice',U,'会談')).reasons)
 def test_january_preserves_december_year(self):
  b=html('<h1>報道発表 2025年12月</h1><h2>12月31日</h2><ul><li><a href="/mofaj/press/release/pressit_000001.html">発表</a></li></ul><a href="/mofaj/press/release/2025/11.html">2025年11月</a>')
  x=parse_release_listing(b,url=U,month='2025-12'); self.assertEqual(x.notices[0].publication_date,'2025-12-31')
 def test_current_month_empty_is_verified(self):
  b=html('<h1>報道発表 2026年10月</h1><ul class="release-list"></ul><a href="/mofaj/press/release/2026/9.html">2026年9月</a>')
  self.assertTrue(parse_release_listing(b,url=U,month='2026-10').verified_empty)
  with self.assertRaises(MofaSchemaError): parse_release_listing(html('<h1>報道発表</h1>'),url=U,month='2026-10')
 def test_external_pdf_keeps_link_without_fetching(self):
  x=parse_release_body(html('<h1>制裁</h1><a href="https://other.test/a.pdf">別添</a>'),link=DocumentLink('n','notice',U,'制裁'))
  self.assertEqual(x.external_links,['https://other.test/a.pdf']); self.assertFalse(x.attachments)
 def test_non_release_path_notice_is_body_screened(self):
  b=html('<h1>報道発表 2026年10月</h1><div id="pressrelease"><dl><dt>10月1日付</dt><dd><ul><li><a href="/mofaj/fp/unp/pageit_000001_03220.html">会議</a></li></ul></dd></dl></div><a href="/mofaj/press/release/8_09_index.html">9月</a>')
  self.assertEqual(len(parse_release_listing(b,url=U,month='2026-10').notices),1)
 def test_wrong_month_archive_is_not_verified_empty(self):
  b=html('<h1>報道発表</h1><h2>過去の記録（令和8年9月）</h2><div id="pressrelease"><dl><dt>9月30日付</dt><dd><ul><li><a href="/mofaj/fp/unp/pageit_1.html">会議</a></li></ul></dd></dl></div><a href="/mofaj/press/release/8_09_index.html">9月</a>')
  with self.assertRaises(MofaSchemaError): parse_release_listing(b,url='https://www.mofa.go.jp/mofaj/press/release/8_10_index.html',month='2026-10')
 def test_unknown_link_structure_is_not_verified_empty(self):
  b=html('<h1>報道発表 2026年10月</h1><div id="pressrelease"><dl><dt>10月1日付</dt><dd><ul><li><a href="/unknown-new-format">発表</a></li></ul></dd></dl></div><a href="/mofaj/press/release/8_09_index.html">9月</a>')
  with self.assertRaises(MofaSchemaError): parse_release_listing(b,url=U,month='2026-10')
 def test_body_only_add_remove_release_terms(self):
  for word in ['追加','削除','解除']:
   x=parse_release_body(html('<h1>お知らせ</h1><p>指定を'+word+'しました</p>'),link=DocumentLink('n','notice',U,'お知らせ'))
   self.assertIn(word,x.reasons)
 def test_real_listing_all_dated_entries_discovered(self):
  from pathlib import Path
  b=Path('tests/fixtures/mofa/release_index.html').read_bytes()
  tree=Tree(b).root; listing=next(n for n in tree.walk() if n.attrs.get('id')=='pressrelease')
  expected=[]; current_month=''
  for n in listing.walk():
   if n.tag=='dt': current_month=n.text().strip().split('月')[0]
   if n.tag=='a' and n.attrs.get('href','').endswith('.html') and current_month=='9': expected.append(n)
  self.assertEqual(len(parse_release_listing(b,url=U,month='2026-09').notices),len(expected))
