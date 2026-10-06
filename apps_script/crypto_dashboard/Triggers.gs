/** 定期実行だけを管理。他の処理のトリガーは削除しない。 */
function caInstallTrigger_() {
  ScriptApp.getProjectTriggers().forEach(function(trigger){if(trigger.getHandlerFunction()==='syncCryptoDashboard')ScriptApp.deleteTrigger(trigger);});
  ScriptApp.newTrigger('syncCryptoDashboard').timeBased().everyMinutes(15).create();
}
function stopCryptoDashboardSync() {
  ScriptApp.getProjectTriggers().forEach(function(trigger){if(trigger.getHandlerFunction()==='syncCryptoDashboard')ScriptApp.deleteTrigger(trigger);});
}
function setupCryptoDashboard() {
  var ss=caSpreadsheet_();
  PropertiesService.getScriptProperties().setProperty('CA_SPREADSHEET_ID',ss.getId());
  ss.setSpreadsheetTimeZone('Asia/Tokyo');
  caSheetNames_().forEach(function(name){if(!ss.getSheetByName(name))ss.insertSheet(name);});
  var settings=ss.getSheetByName('設定');
  if(!settings.getLastRow())caPutMatrix_(settings,caDefaults_());
  var help=[['項目','操作・意味','補足'],
    ['初期設定','setupCryptoDashboardを一度実行','必要な権限の承認後、初回同期が成功すると15分トリガーを作成'],
    ['手動同期','syncCryptoDashboardを実行','メニューからも操作可'],
    ['停止','stopCryptoDashboardSyncを実行','このダッシュボードの同期だけを停止'],
    ['差分','初回収録と補完を除く意味のある変更を表示','同一イベントは配布履歴内に一度だけ保持'],
    ['要確認','形式未対応・ネットワーク未確定・掲載終了候補','確認記録や社内承認の書込み機能は今回対象外'],
    ['編集場所','設定タブ以外の管理表は自動更新','手書きメモは別タブへ'],
    ['監視間隔','Sheets同期15分／OFAC取得は既存の毎時','15分以内の公式更新検知を保証しない'],
    ['情報の範囲','公式掲載の事実を表示','自動凍結・社内制限・解除は行わない']];
  caPutMatrix_(ss.getSheetByName('使い方'),help);
  syncCryptoDashboard();caFormatSheets_(ss);caInstallTrigger_();
}
