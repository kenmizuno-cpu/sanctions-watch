/** 取得だけを担当。コミットSHAに固定して世代の混在を防ぐ。 */
function caSnapshotUrl_(cfg,sha) {
  if(!/^[0-9a-f]{40}$/.test(sha||''))throw Error('GitHubコミットSHA不正');
  return 'https://raw.githubusercontent.com/'+cfg.repo+'/'+sha+'/data/crypto/dashboard.json';
}
function caGetText_(url) {
  var response=UrlFetchApp.fetch(url,{muteHttpExceptions:true,followRedirects:false,
    headers:{Accept:'application/json','User-Agent':'sanctions-watch-crypto-dashboard'}});
  if(response.getResponseCode()!==200)throw Error('取得失敗 HTTP '+response.getResponseCode());
  if(response.getBlob().getBytes().length>5*1024*1024)throw Error('取得サイズ上限5MBを超過');
  var text=response.getContentText('UTF-8');
  if(!text)throw Error('空レスポンス');
  return text;
}
function caFetchSnapshot_(cfg) {
  var commit=JSON.parse(caGetText_('https://api.github.com/repos/'+cfg.repo+'/commits/'+encodeURIComponent(cfg.branch)));
  var url=caSnapshotUrl_(cfg,commit.sha);
  var snapshot=caValidateSnapshot_(JSON.parse(caGetText_(url)));
  snapshot.commit_sha=commit.sha;
  snapshot.snapshot_url=url;
  return snapshot;
}
