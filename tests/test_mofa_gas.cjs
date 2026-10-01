const {test}=require('node:test'); const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
function ctx(){const c=vm.createContext({console,Date});vm.runInContext(fs.readFileSync('apps_script/Code.gs','utf8')+'\n'+fs.readFileSync('apps_script/Mofa.gs','utf8'),c);return c;}
const row=(id='a',hash='1'.repeat(64))=>[id,'2026-10-01 10:00:00','notice','=HYPERLINK("evil")','2026-10-01','date','制裁','DOCUMENT_VALIDATED','REVIEW_REQUIRED_DOCUMENT','https://www.mofa.go.jp/n','https://www.mofa.go.jp/a.pdf',hash,'data/raw/mofa/x'];
test('formula-like title stays text',()=>{const c=ctx();const r=c.mofaRowsForSheet_([row()],[]);assert.equal(r[0][3][0],"'");assert.equal(r[0].length,14);});
test('duplicate event with conflicting hash fails',()=>{assert.throws(()=>ctx().mofaRowsForSheet_([row(),row('a','2'.repeat(64))],[]));});
test('memo preserved by event and new document does not inherit old review',()=>{const c=ctx();const r=c.mofaRowsForSheet_([row('b'),row()],[row().concat('memo')]);assert.equal(r[0][13],'');assert.equal(r[1][13],'memo');assert.equal(r[0][8],'REVIEW_REQUIRED_DOCUMENT');});
test('304 keeps document rows and memo',()=>{ctx().syncMofaDocuments_({getSheetByName(){throw Error('must not access sheet');}},null);});
test('MOFA manual status never becomes automatic normal or a polling timeout', () => {
  const c = ctx();
  const now = Date.UTC(2026, 9, 1);
  c.Date = class extends Date { static now() { return now; } };
  for (const age of [0, 30, 60, 1440, 10080]) {
    c.parseJst_ = () => new Date(now - age * 60000);
    assert.equal(c.freshness_('外務省（現行リスト）', '手動確認済み', 'x', {}).label, '手動確認');
    assert.equal(c.freshness_('外務省（報道発表）', '変更なし', 'x', {}).label, '要確認');
  }
  for (const state of ['自動取得不可・手動確認待ち', '手動取得待ち', '資料更新・要レビュー（手動取得）']) {
    assert.equal(c.freshness_('外務省（現行リスト）', state, '', {}).label, '要確認');
  }
  for (const state of ['手動取込エラー', 'エラー', '構造異常', '未確認期間あり']) {
    assert.equal(c.freshness_('外務省（現行リスト）', state, '', {}).label, '重大');
  }
});
test('six source rows fit before recent changes and unknown count blank',()=>{const c=ctx();let sourceRange,sourceValues;const sheet={getRange(...a){return {setValues(v){if(a.length===4&&a[0]===9){sourceRange=a;sourceValues=v;}return this;},setValue(){return this;}};}};c.requireSheet_=()=>sheet;c.formatJst_=()=>'';c.freshness_=()=>({label:'正常',severity:0});c.appendAnomalies_=()=>{};c.appendAnomaly_=()=>{};c.updateDashboard_({},[['外務省（現行リスト）','変更なし','','','','']],{});assert.deepEqual(sourceRange,[9,1,6,8]);assert.equal(sourceValues[4][4],'');});
test('payload requires MOFA CSV and sync failure cannot update success',()=>{const c=ctx();let specs;c.monthKeys_=()=>[];c.fetchCsvBatch_=(s)=>{specs=s;throw Error('404');};assert.throws(()=>c.fetchSyncPayload_({'Heartbeat Base':'https://x/'},{}));const m=specs.find(x=>x.key==='mofa_documents');assert.equal(m.optional,false);assert.equal(m.conditional,true);assert.equal(m.expected.length,13);});
test('existing OFAC backfill classification preserved',()=>{const c=ctx();const r=['2026-09-04 10:26:50','OFAC','追加','AL-FADHLI','','制裁リスト（OFAC：SDN）','未確認','','2026-09-04 10:26:50|OFAC|追加|AL-FADHLI||制裁リスト（OFAC：SDN）'];assert.equal(c.normalizeLegacyOfacBackfillRow_(r)[2],'初回同期（Advanced XML）');assert.equal(c.reconcileUnresolvedQueueRows_([c.normalizeLegacyOfacBackfillRow_(r)],[]).queueRows.length,0);});
test('MOFA transport metadata is deferred until its rows are applied',()=>{const c=ctx();const values={};const props={setProperties(x){Object.assign(values,x);},setProperty(k,v){values[k]=v;},getProperty(k){return values[k]||null;},deleteProperty(k){delete values[k];}};c.Utilities={parseCsv:()=>[vm.runInContext('EXPECTED.mofaDocuments',c),row()]};const response={getResponseCode:()=>200,getBlob:()=>({getBytes:()=>[1]}),getContentText:()=>'',getAllHeaders:()=>({ETag:'v1'})};const spec={key:'mofa_documents',conditional:true,url:'https://x',expected:vm.runInContext('EXPECTED.mofaDocuments',c)};const item=c.parseCsvResponse_(spec,response,props,{http200:0,http304:0,bytes:0});assert.equal(values.HTTP_ETAG_mofa_documents,undefined);c.commitMofaHttpMeta_(item,props);assert.equal(values.HTTP_ETAG_mofa_documents,'v1');});
