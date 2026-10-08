/** 取得だけを担当。コミットSHAに固定して世代の混在を防ぐ。 */
function caSnapshotUrl_(cfg,sha) {
  if(!/^[0-9a-f]{40}$/.test(sha||''))throw Error('GitHubコミットSHA不正');
  return 'https://raw.githubusercontent.com/'+cfg.repo+'/'+sha+'/data/crypto/dashboard.json';
}
function caGetText_(url,maxBytes) {
  var response=UrlFetchApp.fetch(url,{muteHttpExceptions:true,followRedirects:false,
    headers:{Accept:'application/json','User-Agent':'sanctions-watch-crypto-dashboard'}});
  if(response.getResponseCode()!==200)throw caHttpError_(url,response);
  if(response.getBlob().getBytes().length>(maxBytes||5*1024*1024))throw Error(maxBytes?'追加照会の取得サイズ上限2MBを超過':'取得サイズ上限5MBを超過');
  var text=response.getContentText('UTF-8');
  if(!text)throw Error('空レスポンス');
  return text;
}
/** 応答本文全体は記録せず、取得先と判定に必要な情報だけを残す。 */
function caHttpError_(url,response) {
  var code=response.getResponseCode(),isApi=url.indexOf('https://api.github.com/')===0;
  var headers={},reason='',details=[];
  Object.keys(response.getAllHeaders()||{}).forEach(function(k){
    headers[k.toLowerCase()]=String(response.getAllHeaders()[k]);
  });
  if(isApi){
    try{var body=JSON.parse(response.getContentText('UTF-8'));
      if(typeof body.message==='string')reason=body.message;
    }catch(ignore){}
    if((code===403||code===429)&&(headers['x-ratelimit-remaining']==='0'||/rate limit/i.test(reason))){
      details.push('GitHub APIの回数制限');
      var reset=Number(headers['x-ratelimit-reset']);
      if(reset>0&&reset<253402300800)details.push('再開目安(UTC): '+new Date(reset*1000).toISOString());
    }else if(reason){
      details.push(reason.replace(/[\r\n\t]+/g,' ').replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g,'[IP非表示]').slice(0,180));
    }
  }
  return Error('取得失敗 HTTP '+code+' ['+(isApi?'GitHub版確認':'配布JSON取得')+'] '+url+
    (details.length?' / '+details.join(' / '):''));
}
function caFetchSnapshot_(cfg) {
  var commit=JSON.parse(caGetText_('https://api.github.com/repos/'+cfg.repo+'/commits/'+encodeURIComponent(cfg.branch)));
  var url=caSnapshotUrl_(cfg,commit.sha);
  var snapshot=caValidateSnapshot_(JSON.parse(caGetText_(url)));
  snapshot.commit_sha=commit.sha;
  snapshot.snapshot_url=url;
  try{
    var chainUrl=url.replace('/dashboard.json','/chain_observations.json');
    snapshot.chain_data=caValidateChain_(JSON.parse(caGetText_(chainUrl,2*1024*1024)),snapshot);
  }catch(error){snapshot.chain_error=String(error.message||error).slice(0,500);}
  try{
    var historyUrl=url.replace('/dashboard.json','/token_history.json');
    snapshot.token_history_data=caValidateTokenHistory_(JSON.parse(caGetText_(historyUrl,2*1024*1024)),snapshot);
  }catch(error){snapshot.token_history_error=String(error.message||error).slice(0,500);}
  return snapshot;
}
