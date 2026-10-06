/** データ書込みと失敗時復旧。人の設定・同期履歴は別管理。 */
function caEnsureGrid_(sheet,rows,cols) {
  if(sheet.getMaxRows()<Math.max(rows,2))sheet.insertRowsAfter(sheet.getMaxRows(),Math.max(rows,2)-sheet.getMaxRows());
  if(sheet.getMaxColumns()<cols)sheet.insertColumnsAfter(sheet.getMaxColumns(),cols-sheet.getMaxColumns());
}
function caPutMatrix_(sheet,matrix) {
  var rows=matrix.length,cols=matrix[0].length;
  caEnsureGrid_(sheet,rows,cols);
  var oldRows=Math.max(sheet.getLastRow(),rows),oldCols=Math.max(sheet.getLastColumn(),cols);
  sheet.getRange(1,1,oldRows,oldCols).clearContent();
  var safe=matrix.map(function(row){return row.map(caSafeText_);});
  sheet.getRange(1,1,rows,cols).setNumberFormat('@').setValues(safe);
}
function caWriteTables_(ss,tables) {
  var backups={},started=[];
  Object.keys(tables).forEach(function(name){
    var sheet=ss.getSheetByName(name);
    if(!sheet)throw Error('タブがありません: '+name);
    backups[name]=sheet.getDataRange().getValues();
  });
  try{
    Object.keys(tables).forEach(function(name){started.push(name);caPutMatrix_(ss.getSheetByName(name),tables[name]);});
    SpreadsheetApp.flush();
  }catch(error){
    var failures=[];
    started.reverse().forEach(function(name){try{caPutMatrix_(ss.getSheetByName(name),backups[name]);}catch(e){failures.push(name);}});
    try{SpreadsheetApp.flush();}catch(e){failures.push('flush');}
    throw Error(String(error.message||error)+(failures.length?' / 復旧失敗: '+failures.join(','):''));
  }
}
