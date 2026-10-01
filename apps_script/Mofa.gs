/* Official document review status is read-only; local memo is not approval. */
function mofaTextCell_(value) {
  const text = String(value == null ? '' : value);
  return /^[\s]*[=+@-]/.test(text) ? "'" + text : text;
}
function mofaRowsForSheet_(incomingRows, existingRows) {
  const memo = new Map(existingRows.map(r => [String(r[0]), r[13] || '']));
  const seen = new Map();
  return incomingRows.map(row => {
    if (row.length !== 13 || !row[0]) throw new Error('外務省資料CSVの列またはIDが不正');
    const id = String(row[0]);
    if (seen.has(id)) throw new Error('外務省資料イベントIDの重複・矛盾: ' + id);
    seen.set(id, row[11]);
    return row.map(mofaTextCell_).concat(mofaTextCell_(memo.get(id) || ''));
  });
}
function ensureMofaStructure_(ss) {
  const sheet = ss.getSheetByName(TAB.MOFA) || ss.insertSheet(TAB.MOFA);
  ensureColumns_(sheet, 14);
  sheet.getRange('A1').setValue('外務省資料レビュー');
  sheet.getRange('A2').setValue('対象者数は未解析。資料確認は制裁解除・名簿反映の承認とは別。');
  sheet.getRange(4, 1, 1, 14).setValues([EXPECTED.mofaDocuments.concat('メモ')]);
  sheet.setFrozenRows(4);
}
function syncMofaDocuments_(ss, incomingRows) {
  if (incomingRows === null) return; // HTTP 304 keeps all rows and local memo.
  const sheet = requireSheet_(ss, TAB.MOFA);
  const oldCount = Math.max(0, sheet.getLastRow() - 4);
  const existing = oldCount ? sheet.getRange(5, 1, oldCount, 14).getValues() : [];
  const rows = mofaRowsForSheet_(incomingRows, existing); // validate before mutation
  const count = Math.max(oldCount, rows.length);
  if (!count) return;
  ensureRows_(sheet, 4 + count);
  const padded = rows.concat(Array.from({length: count - rows.length}, () => Array(14).fill('')));
  sheet.getRange(5, 1, count, 14).setNumberFormat('@').setValues(padded);
}

function commitMofaHttpMeta_(item, props) {
  if (item.notModified || !item.mofaHttpMeta) return;
  // Called only after the sheet write succeeds; mixed-batch failures cannot acknowledge unseen rows.
  props.deleteProperty('HTTP_ETAG_mofa_documents');
  props.deleteProperty('HTTP_MODIFIED_mofa_documents');
  saveHttpMeta_({key: 'mofa_documents', conditional: true},
                {getAllHeaders: () => item.mofaHttpMeta}, props);
}
