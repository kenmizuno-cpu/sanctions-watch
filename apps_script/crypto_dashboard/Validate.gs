/** 配布形式の検証。検証完了前に表示データを書き換えない。 */
function caReviewCategory_(r) {
  if(!r.review_reason)return '';
  return r.validation==='INVALID'?'INCONSISTENCY':r.validation==='UNSUPPORTED'?'UNSUPPORTED':'LIMITATION';
}
function caValidateSnapshot_(s) {
  if(!s||s.schema_version!==1||['SUCCESS','FAILED'].indexOf(s.status)<0)throw Error('配布形式・版の変更');
  if(!Array.isArray(s.rows)||s.rows.length>5000||!Array.isArray(s.events)||s.events.length>30000)throw Error('配布件数の上限・構造変更');
  if(!s.counts||!s.upstream||!s.source)throw Error('配布項目の欠損');
  ['generated_at','last_success'].forEach(function(k){
    if((k==='generated_at'||s[k])&&(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(s[k]||'')||!isFinite(Date.parse(s[k]))))throw Error('日時不正: '+k);
  });
  if(s.status==='SUCCESS'&&(!s.last_success||!/^[0-9a-f]{64}$/.test(s.source.sha256||'')))throw Error('正常版の原本・日時欠損');
  var ids={},eventIds={},listed=0,unique={},formatReview=0,removalReview=0;
  var categories={INCONSISTENCY:0,LIMITATION:0,UNSUPPORTED:0};
  s.rows.forEach(function(r){
    if(!/^[0-9a-f]{64}$/.test(r.relation_id||''))throw Error('掲載関係ID不正');
    if(ids[r.relation_id])throw Error('掲載関係ID重複');ids[r.relation_id]=true;
    if(!r.party_id||!r.symbol||typeof r.address!=='string'||!r.address||typeof r.normalized_address!=='string')throw Error('アドレス行の欠損');
    if(['LISTED','REMOVAL_REVIEW'].indexOf(r.listing_status)<0)throw Error('未知の掲載状態');
    if(['CHECKSUM_VALID','FORMAT_ONLY','INVALID','UNSUPPORTED'].indexOf(r.validation)<0)throw Error('未知の検証結果');
    if(r.review_category!==undefined&&r.review_category!==caReviewCategory_(r))throw Error('検証分類の不整合');
    if(r.network_candidates!==undefined||r.network_resolution!==undefined){
      if(!Array.isArray(r.network_candidates)||r.network_candidates.length>8||
        r.network_candidates.some(function(c){return typeof c!=='string'||!c||c.length>64;})||
        ['UNRESOLVED','FAMILY_ONLY','SYMBOL_AND_FORMAT'].indexOf(r.network_resolution)<0||
        (r.network_resolution==='UNRESOLVED')!==(r.network_candidates.length===0))throw Error('ネットワーク候補の構造・判定範囲不正');
    }
    if(r.listing_status==='LISTED'){
      listed++;unique[(r.network||'symbol:'+r.symbol)+'|'+r.normalized_address]=true;
      if(r.review_reason){formatReview++;categories[caReviewCategory_(r)]++;}
    } else removalReview++;
  });
  if(listed!==s.counts.listed_relations||Object.keys(unique).length!==s.counts.unique_addresses||
    formatReview!==s.counts.format_review||removalReview!==s.counts.removal_review)throw Error('配布件数の整合性エラー');
  [['inconsistency_review','INCONSISTENCY'],['validation_limitations','LIMITATION'],['unsupported_review','UNSUPPORTED']].forEach(function(pair){
    if(s.counts[pair[0]]!==undefined&&s.counts[pair[0]]!==categories[pair[1]])throw Error('検証分類の配布件数不整合');
  });
  s.events.forEach(function(e){
    if(!/^[0-9a-f]{64}$/.test(e.event_id||'')||eventIds[e.event_id])throw Error('イベントID不正・重複');
    eventIds[e.event_id]=true;
    if(!ids[e.relation_id]||['BASELINED','BACKFILLED','ADDED','CHANGED','RELISTED','REMOVAL_CANDIDATE'].indexOf(e.kind)<0)throw Error('イベント参照・種別不正');
    if(!isFinite(Date.parse(e.detected_at)))throw Error('イベント日時不正');
  });
  // 検証器の更新履歴は公式掲載差分と別。旧配布版では項目省略を許す。
  if(s.validation_events!==undefined){
    if(!Array.isArray(s.validation_events)||s.validation_events.length>30000)throw Error('検証履歴の上限・構造不正');
    s.validation_events.forEach(function(e){
      if(!/^[0-9a-f]{64}$/.test(e.event_id||'')||eventIds[e.event_id]||!ids[e.relation_id]||
        e.kind!=='REVALIDATED'||!isFinite(Date.parse(e.detected_at))||
        ['CHECKSUM_VALID','FORMAT_ONLY','INVALID','UNSUPPORTED'].indexOf(e.before_validation)<0||
        ['CHECKSUM_VALID','FORMAT_ONLY','INVALID','UNSUPPORTED'].indexOf(e.after_validation)<0)throw Error('検証履歴のID・参照・結果不正');
      eventIds[e.event_id]=true;
    });
  }
  return s;
}
function caSafeText_(value) {
  var s=value===null||value===undefined?'':String(value);
  return /^\s*[=+@-]/.test(s)?"'"+s:s;
}
