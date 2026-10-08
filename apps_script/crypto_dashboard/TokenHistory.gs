/** 過去のトークン送受信の証拠検証・表示。残高判定と公式情報を変更しない。 */
function caValidateTokenHistory_(d,s) {
  function fail(){throw Error('トークン履歴JSON不正・証拠または掲載関係不一致');}
  function time(v){if(typeof v!=='string'||!isFinite(Date.parse(v)))fail();}
  function str(v,max){if(typeof v!=='string'||v.length>max)fail();}
  function integer(v){if(typeof v!=='string'||!/^\d{1,78}$/.test(v))fail();}
  function count(v,max){integer(v);if(!/^\d{1,3}$/.test(v)||Number(v)>max)fail();return Number(v);}
  if(!d||d.schema_version!==1||!Array.isArray(d.rows)||d.rows.length>5000||!d.counts)fail();
  time(d.generated_at);str(d.registry_version,64);
  if(!/^[0-9a-f]{64}$/.test(d.source_hash||'')||d.source_hash!==s.source.sha256)fail();
  var known={},seen={},counts={};s.rows.forEach(function(r){known[r.relation_id]=r;});
  d.rows.forEach(function(r){
    var official=known[r.relation_id],tron=r.chain==='tron';
    if(seen[r.relation_id]||!official||official.address!==r.address||official.symbol!==r.symbol||official.listing_status!=='LISTED')fail();
    seen[r.relation_id]=true;
    var expected=tron&&r.symbol==='USDT'?'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t':
      r.chain==='ethereum'&&r.symbol==='USDT'?'0xdac17f958d2ee523a2206206994597c13d831ec7':
      r.chain==='ethereum'&&r.symbol==='USDC'?'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48':'';
    var index=tron?'https://api.trongrid.io':'https://eth.blockscout.com';
    if(!expected||r.contract!==expected||r.index_endpoint!==index||r.index_provider!==(tron?'TronGrid':'Blockscout'))fail();
    if(r.issuer_source!==(r.symbol==='USDT'?'https://tether.to/en/supported-protocols/':'https://developers.circle.com/stablecoins/usdc-contract-addresses'))fail();
    function address(v){if(typeof v!=='string'||!(tron?/^T[1-9A-HJ-NP-Za-km-z]{33}$/:/^0x[0-9a-f]{40}$/).test(v))fail();}
    address(tron?r.address:r.address.toLowerCase());time(r.attempted_at);
    if(['SUCCESS','FAILED','DEFERRED'].indexOf(r.status)<0)fail();
    if(r.error!==undefined)str(r.error,300);
    var v=r.last_success;
    if(r.status==='SUCCESS'&&!v)fail();
    if(v){
      time(v.checked_at);if(Date.parse(v.checked_at)>Date.parse(d.generated_at)+60000)fail();
      str(v.scope,1000);if(v.error!==undefined)str(v.error,300);
      if(typeof v.has_more!=='boolean'||!Array.isArray(v.proofs)||v.proofs.length>1)fail();
      var candidates=count(v.candidates_count,tron?20:50),checked=count(v.checked_candidates,3),
        unavailable=count(v.unavailable_candidates,3),rejected=count(v.rejected_candidates,3);
      if(checked>candidates||unavailable+rejected+v.proofs.length!==checked)fail();
      var state=v.proofs.length?'VERIFIED':unavailable?'PARTIAL':'NO_PROOF';if(v.state!==state)fail();
      v.proofs.forEach(function(p){
        var hash=tron?/^[0-9a-f]{64}$/:/^0x[0-9a-f]{64}$/;
        if(!hash.test(p.tx_hash||'')||!hash.test(p.block_hash||''))fail();
        ['block_height','block_timestamp','log_index','amount_raw'].forEach(function(k){integer(p[k]);});
        if(/^0+$/.test(p.amount_raw))fail();address(p.from_address);address(p.to_address);
        var subject=tron?r.address:r.address.toLowerCase();if(subject!==p.from_address&&subject!==p.to_address)fail();
        if(p.confirmation!==(tron?'solidified':'finalized'))fail();
        var providers=tron?{'PublicNode':'https://tron-solidity-rpc.publicnode.com','TronGrid':'https://api.trongrid.io'}:
          {'PublicNode':'https://ethereum-rpc.publicnode.com','dRPC':'https://eth.drpc.org'};
        if(providers[p.receipt_provider]!==p.receipt_endpoint)fail();
      });
    }
    var label=r.status==='SUCCESS'?v.state:r.status;counts[label]=(counts[label]||0)+1;
  });
  if(d.counts.target_relations!==d.rows.length)fail();
  ['VERIFIED','NO_PROOF','PARTIAL','FAILED','DEFERRED'].forEach(function(k){if((d.counts[k]||0)!==(counts[k]||0))fail();});
  return d;
}
function caTokenHistoryTables_(tables,s,now) {
  var d=s.token_history_data,lookup={},names={},labels={VERIFIED:'発行元の送受信を検証済み',NO_PROOF:'取得範囲で証拠なし',PARTIAL:'候補あり・取引結果の取得不足',FAILED:'取得失敗',DEFERRED:'未照会・上限/取得先停止'};
  s.rows.forEach(function(r){names[r.relation_id]=r.entity_name;});
  var rows=[['判定','通貨','公式アドレス','対象者名','チェーン','今回取得','今回照会日時','最終成功日時','鮮度',
    '履歴索引','取引結果取得先','発行元コントラクト','検証済み取引ID','ブロック高さ','ブロック識別子','確認状態',
    '送受信額（最小単位）','送信アドレス','受信アドレス','候補数/照会数','次ページの有無','検証範囲・注意','取得エラー','掲載関係ID','照会元原本SHA256']];
  function label(r){return r.status==='SUCCESS'?labels[r.last_success.state]:labels[r.status]+(r.last_success&&r.last_success.proofs.length?'（旧証拠あり）':'');}
  if(d){
    d.rows.forEach(function(r){
      lookup[r.relation_id]=r;var v=r.last_success||{},age=(now.getTime()-Date.parse(v.checked_at||''))/3600000;
      var fresh=!v.checked_at?'成功記録なし':age<0?'時刻要確認':age>6?'期限超過・旧成功値':r.status!=='SUCCESS'?'旧成功値（今回失敗）':'6時間以内';
      var p=(v.proofs||[])[0]||{};
      rows.push([label(r),r.symbol,r.address,names[r.relation_id],r.chain,r.status==='SUCCESS'?'履歴取得成功':labels[r.status],
        caTime_(r.attempted_at),caTime_(v.checked_at),fresh,r.index_provider,p.receipt_provider||'',r.contract,p.tx_hash||'',
        p.block_height||'',p.block_hash||'',p.confirmation||'',p.amount_raw||'',p.from_address||'',p.to_address||'',
        v.checked_at?v.candidates_count+' / '+v.checked_candidates:'',v.checked_at?(v.has_more?'あり（未取得）':'索引の次ページなし'):'未確認',
        v.scope||'前回成功なし。全履歴・所有者は未確認。',r.error||v.error||'',r.relation_id,d.source_hash]);
    });
    tables['監視ダッシュボード'].splice(4,0,
      ['過去のトークン送受信の証拠',d.counts.VERIFIED||0,'TRON USDT・Ethereum USDT/USDC。成功取引とブロック収録まで照合。形式判定・所有者は未確定'],
      ['トークン履歴の検証対象',d.counts.target_relations,'全履歴の網羅ではない。詳細はトークン履歴検証タブ'],
      ['履歴検証の取得不足・失敗',(d.counts.PARTIAL||0)+(d.counts.FAILED||0)+(d.counts.DEFERRED||0),'旧成功値・取得範囲・照会日時を確認']);
  }else{
    var error=s.token_history_error||'トークン履歴データ未取得（旧版）';
    rows.push(['履歴JSON未取得','','','','','取得失敗/未設定','','','','','','','','','','','','','','','',error,error,'','']);
    tables['監視ダッシュボード'].splice(4,0,['過去のトークン送受信の証拠','未取得',error]);
  }
  [['アドレス台帳','掲載関係ID'],['要確認','掲載関係ID']].forEach(function(pair){
    var matrix=tables[pair[0]],index=matrix[0].indexOf(pair[1]);matrix[0].push('過去のトークン履歴検証','履歴照会の最終成功日時');
    matrix.slice(1).forEach(function(row){var r=lookup[row[index]];row.push(r?label(r):'対象外・未照会',r&&r.last_success?caTime_(r.last_success.checked_at):'');});
  });
  tables['トークン履歴検証']=rows;return tables;
}
