/** 設定のみ。既存の人物名ダッシュボードとは別プロジェクトへ設置する。 */
function caSheetNames_() {
  return ['監視ダッシュボード','差分','アドレス台帳','要確認','チェーン検証','トークン履歴検証','同期履歴','設定','使い方'];
}
function caDefaults_() {
  return [['項目','値','説明'],
    ['GitHubリポジトリ','kenmizunokuro/sanctions-watch','公開リポジトリ。顧客データを置かない'],
    ['参照ブランチ','main','正式運用はmain。導入前の確認はPRのブランチを指定できる'],
    ['遅延警告（分）','120','OFACの最終確認日時から計算'],
    ['遅延重大（分）','360','スプレッドシート同期日時からは計算しない']];
}
function caSpreadsheet_() {
  var id=PropertiesService.getScriptProperties().getProperty('CA_SPREADSHEET_ID');
  var ss=id?SpreadsheetApp.openById(id):SpreadsheetApp.getActiveSpreadsheet();
  if(!ss)throw Error('このスプレッドシートに紐付けたApps Scriptへ設置してください');
  return ss;
}
function caReadConfig_(ss) {
  var sheet=ss.getSheetByName('設定');
  if(!sheet)throw Error('先にsetupCryptoDashboardを実行してください');
  var values=sheet.getDataRange().getDisplayValues(), map={};
  values.slice(1).forEach(function(r){map[r[0]]=r[1];});
  var cfg={repo:map['GitHubリポジトリ'],branch:map['参照ブランチ'],
    warningMinutes:Number(map['遅延警告（分）']),criticalMinutes:Number(map['遅延重大（分）'])};
  if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(cfg.repo||''))throw Error('リポジトリ設定不正');
  if(!/^[A-Za-z0-9_./-]+$/.test(cfg.branch||'')||cfg.branch.indexOf('..')>=0)throw Error('ブランチ設定不正');
  if(!(cfg.warningMinutes>0&&cfg.criticalMinutes>cfg.warningMinutes))throw Error('遅延閾値の設定不正');
  return cfg;
}
