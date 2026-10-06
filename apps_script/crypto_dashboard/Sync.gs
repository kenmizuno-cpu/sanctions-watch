/** 取得→検証→全タブ書込み→成功記録の順序だけを統括。 */
function syncCryptoDashboard() {
  var lock=LockService.getScriptLock();
  if(!lock.tryLock(5000))throw Error('別の同期処理が実行中');
  var ss=null;
  try{
    ss=caSpreadsheet_();
    var cfg=caReadConfig_(ss),snapshot=caFetchSnapshot_(cfg);
    caWriteTables_(ss,caBuildTables_(snapshot,cfg));
    caRecordSync_(ss,'同期成功',snapshot.commit_sha,snapshot.status==='SUCCESS'?'台帳収録成功':'台帳収録失敗：'+snapshot.error);
    SpreadsheetApp.flush();
    PropertiesService.getScriptProperties().setProperty('CA_LAST_SYNC_SHA',snapshot.commit_sha);
    return snapshot;
  }catch(error){
    if(ss){
      try{caShowSyncFailure_(ss,String(error.message||error));caRecordSync_(ss,'同期失敗','',String(error.message||error));}catch(loggingError){console.error(loggingError);}
    }
    throw error;
  }finally{lock.releaseLock();}
}
