/** 日本語表示とレイアウト。数値の取得・判定ロジックとは分離。 */
function caTime_(value) {
  if(!value)return '';
  var date=new Date(value);
  if(!isFinite(date.getTime()))return String(value);
  return new Date(date.getTime()+9*3600000).toISOString().slice(0,19).replace('T',' ')+' JST';
}
function caBuildTables_(s,cfg,now) {
  now=now||new Date();
  var last=Date.parse(s.upstream.last_checked||''),age=isFinite(last)?(now.getTime()-last)/60000:null;
  var health=s.upstream.outcome==='failure'?'異常：上流OFAC取得失敗':
    age===null?'未確認：上流確認日時なし':age>cfg.criticalMinutes?'重大：上流確認が遅延':
    age>cfg.warningMinutes?'警告：上流確認が遅延':'確認間隔内';
  var overview=[['暗号資産アドレス監視','値','説明'],
    ['台帳収録',s.status==='SUCCESS'?'収録成功':'収録失敗・旧台帳保持',s.error||'保存済み公式原本から収録'],
    ['上流監視',health,'台帳収録成功と、OFAC取得成功は別'],
    ['スプレッドシート同期',caTime_(now.toISOString()),'この時刻はOFAC確認時刻ではありません'],
    ['OFAC最終確認',caTime_(s.upstream.last_checked),'上流の監視記録'],
    ['台帳最終成功',caTime_(s.last_success),'アドレス収録の最終成功'],
    ['台帳実行日時',caTime_(s.generated_at),'最新の台帳収録処理'],
    ['公式掲載の掲載関係数',s.counts.listed_relations,'同一アドレス・別対象者は別関係'],
    ['ユニークアドレス数',s.counts.unique_addresses,'ネットワーク不明は通貨記号ごとに集計'],
    ['形式・ネットワーク要確認',s.counts.format_review,'公式掲載の原文を保持。形式の検証未対応も含む'],
    ['掲載終了レビュー',s.counts.removal_review,'自動削除・解除を行いません'],
    ['初回収録・補完を除く差分数',s.events.filter(function(e){return e.kind!=='BASELINED'&&e.kind!=='BACKFILLED';}).length,'履歴全体の件数'],
    ['HTTP状態',s.upstream.http_status||'未記録','上流Advanced XMLの最終取得監査'],
    ['ETag',s.source.etag||'','原本取得のHTTP識別子'],
    ['HTTP更新日時',s.source.last_modified||'','公表日・発効日とは区別'],
    ['原本SHA256',s.source.sha256||'','取得済み原本の内容ハッシュ'],
    ['OFAC原本URL',s.source.url||'','現在の取得先。収録時の原本は次行'],
    ['保存原本',s.source.raw_path||'','GitHubリポジトリ内の原本パス'],
    ['GitHub取得版',s.commit_sha||'初期スナップショット','この版に固定して同期'],
    ['判断の範囲','公式掲載の事実','日本での法的判断・社内制限は別工程'],
    ['通貨記号','掲載関係数','公式の記号をそのまま表示']];
  Object.keys(s.counts.by_symbol||{}).sort().forEach(function(symbol){overview.push([symbol,s.counts.by_symbol[symbol],'']);});
  var names={ADDED:'追加',CHANGED:'変更',RELISTED:'再掲載',REMOVAL_CANDIDATE:'掲載終了候補'};
  var changes=[['検知日時','種別','通貨','ネットワーク','アドレス','対象者ID','対象者名','原本SHA256','イベントID']];
  s.events.slice().reverse().filter(function(e){return names[e.kind];}).forEach(function(e){changes.push([
    caTime_(e.detected_at),names[e.kind],e.symbol,e.network||'未確定',e.address,e.party_id,e.entity_name,e.source_hash,e.event_id]);});
  var master=[['通貨','ネットワーク','公式原文アドレス','正規化値','対象者ID','対象者名','プログラム','掲載状態','形式検証','要確認理由','初回収録','最終確認','原本SHA256','原本内位置','掲載関係ID']];
  var reviews=[['分類','理由','通貨','ネットワーク','アドレス','対象者ID','対象者名','掲載状態','原本内位置','掲載関係ID']];
  var statuses={LISTED:'公式掲載',REMOVAL_REVIEW:'掲載終了候補'};
  var validations={CHECKSUM_VALID:'チェックサム検証済',FORMAT_ONLY:'形式確認・チェックサム未検証',INVALID:'形式不正・要確認',UNSUPPORTED:'検証未対応'};
  s.rows.forEach(function(r){
    master.push([r.symbol,r.network||'未確定',r.address,r.normalized_address,r.party_id,r.entity_name,r.program||'',statuses[r.listing_status],
      validations[r.validation]||r.validation,r.review_reason||'',caTime_(r.first_seen),caTime_(r.last_seen),r.source_hash||'',r.evidence_locator||'',r.relation_id]);
    if(r.review_reason||r.listing_status==='REMOVAL_REVIEW')reviews.push([r.listing_status==='REMOVAL_REVIEW'?'掲載終了候補':'形式・ネットワーク',
      r.listing_status==='REMOVAL_REVIEW'?'原本から消失。対象者指定の解除とは別':r.review_reason,r.symbol,r.network||'未確定',r.address,r.party_id,r.entity_name,statuses[r.listing_status],r.evidence_locator||'',r.relation_id]);
  });
  return {'監視ダッシュボード':overview,'差分':changes,'アドレス台帳':master,'要確認':reviews};
}
function caFormatSheets_(ss) {
  caSheetNames_().forEach(function(name){
    var sheet=ss.getSheetByName(name),rows=Math.max(sheet.getLastRow(),2),cols=Math.max(sheet.getLastColumn(),3);
    caEnsureGrid_(sheet,rows,cols);
    sheet.setFrozenRows(1);
    sheet.getRange(1,1,rows,cols).setFontFamily('Arial').setFontSize(10).setVerticalAlignment('middle').setWrap(true);
    sheet.getRange(1,1,1,cols).setBackground('#17324d').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
    sheet.setRowHeight(1,36);sheet.setColumnWidths(1,cols,170);
    if(name==='監視ダッシュボード'){sheet.setColumnWidth(1,240);sheet.setColumnWidth(2,400);sheet.setColumnWidth(3,440);}
    if(name==='アドレス台帳'){sheet.setColumnWidth(3,390);sheet.setColumnWidth(4,390);sheet.setColumnWidth(6,280);sheet.setColumnWidth(10,280);}
    if(name==='使い方'||name==='設定'){sheet.setColumnWidth(2,420);sheet.setColumnWidth(3,440);}
  });
}
