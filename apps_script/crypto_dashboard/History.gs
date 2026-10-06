/** 同期履歴とエラー表示。配布側の原本確認日時は変更しない。 */
function caRecordSync_(ss,status,sha,detail) {
  var sheet=ss.getSheetByName('同期履歴');
  if(!sheet)throw Error('同期履歴タブがありません');
  if(!sheet.getLastRow())caPutMatrix_(sheet,[['同期日時（JST）','結果','GitHub版','詳細']]);
  var row=sheet.getLastRow()+1;
  caEnsureGrid_(sheet,row,4);
  sheet.getRange(row,1,1,4).setNumberFormat('@').setValues([[caTime_(new Date().toISOString()),status,sha||'',detail||''].map(caSafeText_)]);
}
function caShowSyncFailure_(ss,message) {
  var sheet=ss.getSheetByName('監視ダッシュボード');
  if(sheet)sheet.getRange(4,1,1,3).setNumberFormat('@').setValues([['スプレッドシート同期','失敗・前回表示を保持',caSafeText_(message)]]);
}
