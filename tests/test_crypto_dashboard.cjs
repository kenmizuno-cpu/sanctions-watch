const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({console, Date, JSON, Math});
const dir=path.join(__dirname,'../apps_script/crypto_dashboard');
if(fs.existsSync(dir)) for(const f of fs.readdirSync(dir).filter(f=>f.endsWith('.gs')))
  vm.runInContext(fs.readFileSync(path.join(dir,f),'utf8'),context,{filename:f});
let passed=0;
function test(name,fn){try{fn();passed++; console.log('PASS '+name)}catch(e){console.error('FAIL '+name);throw e}}
const snap={schema_version:1,status:'SUCCESS',generated_at:'2026-10-07T00:00:00Z',last_success:'2026-10-07T00:00:00Z',
 upstream:{last_checked:'2026-10-06T23:59:00Z',status:'unchanged',outcome:'success'},source:{sha256:'a'.repeat(64),url:'https://example.test'},
 counts:{listed_relations:1,unique_addresses:1,format_review:0,removal_review:0,by_symbol:{XBT:1}},
 rows:[{relation_id:'a'.repeat(64),party_id:'42',symbol:'XBT',network:'bitcoin',address:'123',normalized_address:'123',listing_status:'LISTED',entity_name:'EXAMPLE',validation:'CHECKSUM_VALID',review_reason:''}],events:[],new_event_count:0,report:{raw_count:1}};
test('snapshot validation checks duplicate relation IDs',()=>{
 assert.equal(typeof context.caValidateSnapshot_,'function');
 assert.equal(context.caValidateSnapshot_(snap).rows.length,1);
 assert.throws(()=>context.caValidateSnapshot_({...snap,rows:[...snap.rows,...snap.rows]}),/重複/);
});
test('snapshot count mismatch and bad timestamps fail',()=>{
 assert.throws(()=>context.caValidateSnapshot_({...snap,counts:{...snap.counts,listed_relations:0}}),/件数/);
 assert.throws(()=>context.caValidateSnapshot_({...snap,generated_at:'bad'}),/日時/);
});
test('data beginning with equals is escaped before Sheets writes',()=>{
 assert.equal(context.caSafeText_('=IMPORTXML("evil")'),"'=IMPORTXML(\"evil\")");
 assert.equal(context.caSafeText_('001234'),'001234');
});
test('pin raw resource to validated commit SHA',()=>{
 assert.equal(context.caSnapshotUrl_({repo:'kenmizunokuro/sanctions-watch'},'b'.repeat(40)),
  'https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/'+ 'b'.repeat(40) +'/data/crypto/dashboard.json');
 assert.throws(()=>context.caSnapshotUrl_({repo:'x/y'},'main'),/SHA/);
});
test('upstream staleness does not become normal after a new extraction',()=>{
 const tables=context.caBuildTables_(snap,{warningMinutes:120,criticalMinutes:360},new Date('2026-10-07T08:00:00Z'));
 assert.ok(tables['監視ダッシュボード'].some(r=>r.includes('重大：上流確認が遅延')));
 assert.equal(tables['差分'].length,1);
});
test('partial write restores already changed data including failing sheet',()=>{
 const contents={A:[['oldA']],B:[['oldB']]};let fail=true;
 const ss={getSheetByName(name){return {getDataRange(){return {getValues(){return contents[name]}}},
  getMaxRows(){return 10},getMaxColumns(){return 10},getLastRow(){return contents[name].length},getLastColumn(){return 1},
  getRange(){return {setNumberFormat(){return this},clearContent(){contents[name]=[];return this},
    setValues(values){contents[name]=values;if(name==='B'&&fail){fail=false;throw Error('write failed')}return this}}}}}};
 context.SpreadsheetApp={flush(){}};
 assert.throws(()=>context.caWriteTables_(ss,{A:[['newA']],B:[['newB']]}),/write failed/);
 assert.equal(contents.A[0][0],'oldA'); assert.equal(contents.B[0][0],'oldB');
});
test('trigger setup replaces only own sync handler',()=>{
 let removed=[],created=[];
 context.ScriptApp={getProjectTriggers(){return [{getHandlerFunction(){return 'syncCryptoDashboard'}},{getHandlerFunction(){return 'other'}}]},
 deleteTrigger(t){removed.push(t.getHandlerFunction())},newTrigger(name){created.push(name);return {timeBased(){return this},everyMinutes(n){assert.equal(n,15);return this},create(){}}}};
 context.caInstallTrigger_();assert.deepEqual(removed,['syncCryptoDashboard']);assert.deepEqual(created,['syncCryptoDashboard']);
});
console.log(passed+' tests passed');
