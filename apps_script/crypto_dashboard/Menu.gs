/** 操作メニュー。同期の中身をここへ書かない。 */
function onOpen() {
  SpreadsheetApp.getUi().createMenu('アドレス監視')
    .addItem('初期設定・定期同期を開始','setupCryptoDashboard')
    .addItem('今すぐ同期','syncCryptoDashboard')
    .addItem('定期同期を停止','stopCryptoDashboardSync').addToUi();
}
