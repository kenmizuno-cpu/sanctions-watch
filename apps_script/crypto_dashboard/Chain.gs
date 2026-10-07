/** 公開チェーン照会のJSON検証と日本語表示。公式の形式判定を変更しない。 */
function caChainState_(probes) {
  var ok=probes.filter(function(p){return p.status==='SUCCESS';}),by={};
  ok.forEach(function(p){var values=by[p.chain]||(by[p.chain]={});values[String(p.last_success.positive)]=true;});
  if(Object.keys(by).some(function(k){return Object.keys(by[k]).length>1;}))return 'CONFLICT';
  if(ok.length<probes.length||ok.some(function(p){return !!p.last_success.history_error;}))return ok.length?'PARTIAL':'FAILED';
  return ok.some(function(p){return p.last_success.positive;})?'POSITIVE':'NO_EVIDENCE';
}
function caValidateChain_(d,s) {
  function fail(){throw Error('チェーン照会JSON不正・件数または掲載関係不一致');}
  function time(v){if(typeof v!=='string'||!isFinite(Date.parse(v)))fail();}
  function str(v,max){if(typeof v!=='string'||v.length>max)fail();}
  if(!d||d.schema_version!==1||!Array.isArray(d.rows)||d.rows.length>5000||!d.counts)fail();
  str(d.registry_version,64);time(d.generated_at);
  if(!/^[0-9a-f]{64}$/.test(d.source_hash||''))fail();
  var known={},seen={},counts={},total=0,success=0;
  s.rows.forEach(function(r){known[r.relation_id]=r;});
  d.rows.forEach(function(r){
    var official=known[r.relation_id];
    if(seen[r.relation_id]||!official||official.address!==r.address||official.symbol!==r.symbol||
      !Array.isArray(r.probes)||!r.probes.length||r.probes.length>16)fail();
    seen[r.relation_id]=true;var keys={};
    r.probes.forEach(function(p){
      ['chain','provider','contract','asset'].forEach(function(k){str(p[k]||'',160);});
      str(p.endpoint,300);if(!/^https:\/\//.test(p.endpoint))fail();
      var key=p.chain+'|'+p.provider+'|'+(p.contract||'');if(keys[key])fail();keys[key]=true;
      if(['SUCCESS','FAILED','DEFERRED'].indexOf(p.status)<0)fail();time(p.attempted_at);total++;
      if(p.status==='SUCCESS'){success++;if(!p.last_success)fail();}
      if(p.error!==undefined)str(p.error,300);
      var v=p.last_success;
      if(v){
        time(v.checked_at);if(typeof v.positive!=='boolean')fail();
        if(v.asset_match!==undefined&&typeof v.asset_match!=='boolean')fail();
        ['balance_raw','token_balance_raw','nonce','tx_count','block_height','block_timestamp','slot','history_from_block','recent_token_tx_count'].forEach(function(k){
          if(v[k]!==undefined&&(typeof v[k]!=='string'||!/^\d{1,78}$/.test(v[k])))fail();
        });
        ['decimals','token_decimals'].forEach(function(k){if(v[k]!==undefined&&(!Number.isInteger(v[k])||v[k]<0||v[k]>18))fail();});
        ['scope','history_error','confirmation'].forEach(function(k){if(v[k]!==undefined)str(v[k],1000);});
        if(v.token_tx_sample!==undefined){if(!Array.isArray(v.token_tx_sample)||v.token_tx_sample.length>3)fail();v.token_tx_sample.forEach(function(tx){if(!/^0x[0-9a-f]{64}$/.test(tx))fail();});}
        if(v.tx_sample!==undefined){if(!Array.isArray(v.tx_sample)||v.tx_sample.length>3)fail();v.tx_sample.forEach(function(tx){str(tx.signature,100);if(!/^\d{1,78}$/.test(tx.slot||''))fail();});}
      }
    });
    if(r.state!==caChainState_(r.probes))fail();counts[r.state]=(counts[r.state]||0)+1;
  });
  if(d.counts.target_relations!==d.rows.length||d.counts.probes!==total||d.counts.successful_probes!==success)fail();
  ['POSITIVE','NO_EVIDENCE','CONFLICT','PARTIAL','FAILED'].forEach(function(k){if((d.counts[k]||0)!==(counts[k]||0))fail();});
  return d;
}
function caChainLabels_() {
  return {POSITIVE:'記録・残高を観測',NO_EVIDENCE:'照会範囲で未観測',CONFLICT:'取得先で観測結果が異なる',PARTIAL:'一部取得失敗・範囲不足',FAILED:'取得失敗・未照会'};
}
function caChainTables_(tables,s,now) {
  var d=s.chain_data,labels=caChainLabels_(),lookup={},names={};
  s.rows.forEach(function(r){names[r.relation_id]=r;});
  var header=['全体判定','通貨','公式アドレス','対象者名','チェーン','取得先','今回取得','今回照会日時',
    '最終成功日時','鮮度','観測有無','資産照合','発行元コントラクト','発行元資料','残高（最小単位）','桁数',
    'トークン残高（最小単位）','トークン桁数','nonce（総取引数ではない）','確認済みBTC取引数',
    'ブロック高さ/slot','ブロック識別子','確認状態','取引参照例','照会範囲・注意','取得エラー','掲載関係ID','照会元原本SHA256'];
  var rows=[header];
  if(d){
    d.rows.forEach(function(r){
      lookup[r.relation_id]=r;
      r.probes.forEach(function(p){
        var v=p.last_success||{},age=(now.getTime()-Date.parse(v.checked_at||''))/3600000;
        var freshness=!v.checked_at?'成功記録なし':age>6?'期限超過・旧成功値':p.status!=='SUCCESS'?'旧成功値（今回失敗）':'6時間以内';
        var asset=p.asset==='USDT'&&p.chain==='bitcoin'?'USDT未検証（BTCのみ）':!p.contract?'トークン対象外':
          v.asset_match?'発行元資産を観測':'残高・直近範囲で未観測';
        var txs=(v.token_tx_sample||[]).concat((v.tx_sample||[]).map(function(t){return t.signature+' / slot '+t.slot;})).join(' / ');
        rows.push([labels[r.state],r.symbol,r.address,names[r.relation_id].entity_name,p.chain,p.provider,
          p.status==='SUCCESS'?'取得成功':p.status==='DEFERRED'?'未照会・上限/取得先停止':'取得失敗',
          caTime_(p.attempted_at),caTime_(v.checked_at),freshness,
          v.checked_at?(v.positive?'観測あり（所有者は未確認）':'未観測（無効とは判定しない）'):'未確認',
          v.checked_at?asset:'未確認',p.contract||'',p.issuer_source||'',v.balance_raw||'',
          v.decimals===undefined?'':v.decimals,v.token_balance_raw||'',v.token_decimals===undefined?'':v.token_decimals,
          v.nonce||'',v.tx_count||'',v.block_height||v.slot||'',v.block_hash||'',v.confirmation||'',txs,
          v.scope||'前回成功なし',p.error||v.history_error||'',r.relation_id,d.source_hash]);
      });
    });
    tables['監視ダッシュボード'].splice(4,0,
      ['チェーン追加照会',d.counts.target_relations+'関係 / 成功 '+d.counts.successful_probes+' / '+d.counts.probes+'照会先','詳細はチェーン検証タブ。要確認の形式判定は保持'],
      ['チェーン照会の配布日時',caTime_(d.generated_at),'各値の鮮度は取得先別の最終成功日時で確認'],
      ['チェーンで記録・残高を観測',d.counts.POSITIVE||0,'公式の実ネットワーク・所有者の確定ではない'],
      ['チェーン取得先の結果差異',d.counts.CONFLICT||0,'履歴収録範囲や照会ブロックの違いもあり得る'],
      ['チェーン一部/全部取得失敗',(d.counts.PARTIAL||0)+(d.counts.FAILED||0),'期限超過・前回成功・未照会を詳細で確認']);
  }else{
    var message=s.chain_error||'チェーン照会データ未取得（旧版）';
    rows.push(['照会JSON未取得','','','','','','取得失敗/未設定','','','','','','','','','','','','','','','','','',message,message,'','']);
    tables['監視ダッシュボード'].splice(4,0,['チェーン追加照会','未取得',message]);
  }
  [['アドレス台帳','掲載関係ID'],['要確認','掲載関係ID']].forEach(function(pair){
    var matrix=tables[pair[0]],index=matrix[0].indexOf(pair[1]);
    matrix[0].push('チェーン追加照会','照会の最終成功日時');
    matrix.slice(1).forEach(function(r){var match=lookup[r[index]],times=match?match.probes.map(function(p){return p.last_success?p.last_success.checked_at:'';}).filter(Boolean).sort():[];
      r.push(match?labels[match.state]:'対象外・未照会',times.length?caTime_(times[times.length-1]):'');});
  });
  tables['チェーン検証']=rows;
  return tables;
}
