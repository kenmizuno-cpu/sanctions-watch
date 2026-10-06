/** 配布形式の検証。検証完了前に表示データを書き換えない。 */
function caValidateSnapshot_(s) {
  if(!s||s.schema_version!==1||['SUCCESS','FAILED'].indexOf(s.status)<0)throw Error('配布形式・版の変更');
  if(!Array.isArray(s.rows)||s.rows.length>5000||!Array.isArray(s.events)||s.events.length>30000)throw Error('配布件数の上限・構造変更');
  if(!s.counts||!s.upstream||!s.source)throw Error('配布項目の欠損');
  ['generated_at','last_success'].forEach(function(k){
    if((k==='generated_at'||s[k])&&(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(s[k]||'')||!isFinite(Date.parse(s[k]))))throw Error('日時不正: '+k);
  });
  if(s.status==='SUCCESS'&&(!s.last_success||!/^[0-9a-f]{64}$/.test(s.source.sha256||'')))throw Error('正常版の原本・日時欠損');
  var ids={},eventIds={},listed=0,unique={},formatReview=0,removalReview=0;
  s.rows.forEach(function(r){
    if(!/^[0-9a-f]{64}$/.test(r.relation_id||''))throw Error('掲載関係ID不正');
    if(ids[r.relation_id])throw Error('掲載関係ID重複');ids[r.relation_id]=true;
    if(!r.party_id||!r.symbol||typeof r.address!=='string'||!r.address||typeof r.normalized_address!=='string')throw Error('アドレス行の欠損');
    if(['LISTED','REMOVAL_REVIEW'].indexOf(r.listing_status)<0)throw Error('未知の掲載状態');
    if(r.listing_status==='LISTED'){
      listed++;unique[(r.network||'symbol:'+r.symbol)+'|'+r.normalized_address]=true;
      if(r.review_reason)formatReview++;
    } else removalReview++;
  });
  if(listed!==s.counts.listed_relations||Object.keys(unique).length!==s.counts.unique_addresses||
    formatReview!==s.counts.format_review||removalReview!==s.counts.removal_review)throw Error('配布件数の整合性エラー');
  s.events.forEach(function(e){
    if(!/^[0-9a-f]{64}$/.test(e.event_id||'')||eventIds[e.event_id])throw Error('イベントID不正・重複');
    eventIds[e.event_id]=true;
    if(!ids[e.relation_id]||['BASELINED','BACKFILLED','ADDED','CHANGED','RELISTED','REMOVAL_CANDIDATE'].indexOf(e.kind)<0)throw Error('イベント参照・種別不正');
    if(!isFinite(Date.parse(e.detected_at)))throw Error('イベント日時不正');
  });
  return s;
}
function caSafeText_(value) {
  var s=value===null||value===undefined?'':String(value);
  return /^\s*[=+@-]/.test(s)?"'"+s:s;
}
