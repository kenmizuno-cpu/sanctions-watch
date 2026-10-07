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
function response(code, body, headers = {}, bytes = body.length) {
  return {getResponseCode(){return code;},getContentText(){return body;},
    getAllHeaders(){return headers;},getBlob(){return {getBytes(){return {length:bytes};}};}};
}
const api = 'https://api.github.com/repos/kenmizunokuro/sanctions-watch/commits/main';
const raw = 'https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/main/data/crypto/dashboard.json';
test('rate limit response identifies endpoint and reset time',()=>{
  context.UrlFetchApp={fetch(){return response(403,JSON.stringify({message:'API rate limit exceeded for 192.0.2.1.'}),
    {'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'1791338400'});}};
  assert.throws(()=>context.caGetText_(api),e=>/HTTP 403/.test(e.message)&&e.message.includes(api)&&
    /GitHub APIの回数制限/.test(e.message)&&/2026-10-07T02:00:00.000Z/.test(e.message)&&!e.message.includes('192.0.2.1'));
});
test('ordinary permission denial is not labelled a rate limit',()=>{
  context.UrlFetchApp={fetch(){return response(403,JSON.stringify({message:'Resource not accessible by integration'}),{});}};
  assert.throws(()=>context.caGetText_(api),e=>e.message.includes(api)&&/Resource not accessible/.test(e.message)&&!/回数制限/.test(e.message));
});
test('raw server refusal includes URL but does not copy HTML response',()=>{
  context.UrlFetchApp={fetch(){return response(403,'<html>untrusted response contents</html>');}};
  assert.throws(()=>context.caGetText_(raw),e=>e.message.includes(raw)&&!/untrusted response contents/.test(e.message));
});
test('secondary rate limit is identified without exposing full response',()=>{
  context.UrlFetchApp={fetch(){return response(403,JSON.stringify({message:'You have exceeded a secondary rate limit.'}));}};
  assert.throws(()=>context.caGetText_(api),/GitHub APIの回数制限/);
});
test('successful and oversized responses retain existing checks',()=>{
  context.UrlFetchApp={fetch(){return response(200,'{}');}};
  assert.equal(context.caGetText_(raw),'{}');
  context.UrlFetchApp={fetch(){return response(200,'{}',{},5*1024*1024+1);}};
  assert.throws(()=>context.caGetText_(raw),/5MB/);
});
test('HTTP failure stops before fetching or validating snapshot',()=>{
  let calls=0;
  context.UrlFetchApp={fetch(){calls++;return response(403,'{}');}};
  assert.throws(()=>context.caFetchSnapshot_({repo:'kenmizunokuro/sanctions-watch',branch:'main'}),/HTTP 403/);
  assert.equal(calls,1);
});
test('validation methods and remaining reasons are visible without official differences',()=>{
 const row={...snap.rows[0],validation:'FORMAT_ONLY',review_reason:'チェックサム情報なし',
   validation_method:'HEX20',validation_detail:'20バイトの16進形式を確認',validation_version:'2'};
 const updated={...snap,rows:[row],counts:{...snap.counts,format_review:1},validation_events:[]};
 const tables=context.caBuildTables_(updated,{warningMinutes:120,criticalMinutes:360});
 const header=tables['アドレス台帳'][0],body=tables['アドレス台帳'][1];
 assert.equal(body[header.indexOf('検証方法')],'16進形式（20バイト）');
 assert.equal(body[header.indexOf('検証詳細')],'20バイトの16進形式を確認');
 assert.equal(body[header.indexOf('検証版')],'2');
 assert.equal(tables['要確認'][1][tables['要確認'][0].indexOf('検証方法')],'16進形式（20バイト）');
 assert.equal(tables['差分'].length,1);
});
test('validation audit IDs and references must be consistent when provided',()=>{
 const audit={event_id:'c'.repeat(64),relation_id:snap.rows[0].relation_id,kind:'REVALIDATED',
   detected_at:'2026-10-07T02:00:00Z',before_validation:'FORMAT_ONLY',after_validation:'CHECKSUM_VALID'};
 assert.equal(context.caValidateSnapshot_({...snap,validation_events:[audit]}).validation_events.length,1);
 assert.throws(()=>context.caValidateSnapshot_({...snap,validation_events:[audit,audit]}),/検証履歴/);
 assert.throws(()=>context.caValidateSnapshot_({...snap,validation_events:[{...audit,relation_id:'d'.repeat(64)}]}),/検証履歴/);
 assert.throws(()=>context.caValidateSnapshot_({...snap,validation_events:{}}),/検証履歴/);
});
test('review categories distinguish inconsistencies and limitations and show candidates',()=>{
 const rows=[{...snap.rows[0],review_category:'LIMITATION',review_reason:'ネットワーク未確定',
   network_candidates:['tron-family'],network_resolution:'FAMILY_ONLY'},
   {...snap.rows[0],relation_id:'b'.repeat(64),validation:'INVALID',review_reason:'通貨記号との不整合',
   review_category:'INCONSISTENCY',network_candidates:['tron-family'],network_resolution:'FAMILY_ONLY'}];
 const tables=context.caBuildTables_({...snap,rows,counts:{...snap.counts,format_review:2}},
   {warningMinutes:120,criticalMinutes:360});
 const reviews=tables['要確認'];
 assert.equal(reviews[1][0],'不整合');assert.equal(reviews[2][0],'検証制限');
 assert.equal(reviews[1][reviews[0].indexOf('形式からの候補')],'tron-family');
 assert.equal(reviews[2][reviews[0].indexOf('ネットワーク判定範囲')],'形式の系統のみ・実ネットワーク未確定');
 assert.ok(tables['監視ダッシュボード'].some(r=>r[0]==='不整合・原文確認'&&r[1]===1));
 assert.ok(tables['監視ダッシュボード'].some(r=>r[0]==='検証上の制限'&&r[1]===1));
});
test('optional metadata and subdivided counts are validated but legacy data is accepted',()=>{
 const row={...snap.rows[0],review_category:'',network_candidates:['bitcoin'],network_resolution:'SYMBOL_AND_FORMAT'};
 const data={...snap,rows:[row],counts:{...snap.counts,inconsistency_review:0,validation_limitations:0,unsupported_review:0}};
 assert.equal(context.caValidateSnapshot_(data).rows.length,1);
 assert.throws(()=>context.caValidateSnapshot_({...data,counts:{...data.counts,inconsistency_review:1}}),/件数/);
 for(const overrides of [{network_candidates:'tron'}, {network_candidates:[1]}, {network_resolution:'CONFIRMED'},
    {review_category:'INCONSISTENCY'}, {network_candidates:[],network_resolution:'FAMILY_ONLY'}]){
   assert.throws(()=>context.caValidateSnapshot_({...data,rows:[{...row,...overrides}]}),/検証分類|候補/);
 }
 assert.equal(context.caValidateSnapshot_(snap).rows.length,1);
});
console.log(passed+' tests passed');
const chain={schema_version:1,registry_version:'2026-10-07.1',generated_at:'2026-10-07T00:00:00Z',
 source_hash:'a'.repeat(64),counts:{target_relations:1,probes:2,successful_probes:1,PARTIAL:1},
 rows:[{relation_id:snap.rows[0].relation_id,address:'123',symbol:'XBT',state:'PARTIAL',probes:[
  {chain:'bitcoin',provider:'Blockstream',endpoint:'https://blockstream.info/api',contract:'',asset:'USDT',
   status:'SUCCESS',attempted_at:'2026-10-07T00:00:00Z',last_success:{checked_at:'2026-10-07T00:00:00Z',positive:true,
   balance_raw:'900719925474099312345',decimals:8,asset_match:false,tx_count:'171',scope:'BTCのみ。Omni未確認。'}},
  {chain:'bitcoin',provider:'mempool.space',endpoint:'https://mempool.space/api',contract:'',asset:'USDT',
   status:'FAILED',attempted_at:'2026-10-07T00:00:00Z',error:'HTTPError: failed'}]}]};
test('chain overlay validates precise integers, identity, states and counts',()=>{
 assert.equal(typeof context.caValidateChain_,'function');
 assert.equal(context.caValidateChain_(chain,snap).rows.length,1);
 for(const bad of [
  {...chain,rows:[{...chain.rows[0],address:'changed'}]},
  {...chain,rows:[chain.rows[0],chain.rows[0]]},
  {...chain,counts:{...chain.counts,target_relations:0}},
  {...chain,rows:[{...chain.rows[0],probes:[{...chain.rows[0].probes[0],last_success:{...chain.rows[0].probes[0].last_success,balance_raw:9007199254740992}}]}]},
  {...chain,rows:[{...chain.rows[0],state:'POSITIVE'}]},
  {...chain,rows:[{...chain.rows[0],probes:[{...chain.rows[0].probes[0],status:'SUCCESS',last_success:null}]}]}
 ]) assert.throws(()=>context.caValidateChain_(bad,snap),/チェーン/);
});
test('chain fetch shares official SHA and failure remains visible with official data',()=>{
 const sha='b'.repeat(40);let urls=[];
 context.UrlFetchApp={fetch(url){urls.push(url);return response(200,JSON.stringify(url.includes('/commits/')?{sha}:
  url.endsWith('/dashboard.json')?snap:chain));}};
 const loaded=context.caFetchSnapshot_({repo:'kenmizunokuro/sanctions-watch',branch:'main'});
 assert.equal(loaded.chain_data.rows.length,1);
 assert.ok(urls[2].includes('/'+sha+'/data/crypto/chain_observations.json'));
 context.UrlFetchApp={fetch(url){return response(url.endsWith('/chain_observations.json')?403:200,
  JSON.stringify(url.includes('/commits/')?{sha}:snap));}};
 const failed=context.caFetchSnapshot_({repo:'kenmizunokuro/sanctions-watch',branch:'main'});
 assert.equal(failed.rows.length,1);assert.match(failed.chain_error,/HTTP 403/);
 const tables=context.caBuildTables_(failed,{warningMinutes:120,criticalMinutes:360});
 assert.ok(tables['チェーン検証'].some(r=>r.join(' ').includes('HTTP 403')));
});
test('chain view preserves reviews and official changes and exposes failed attempts and old successes',()=>{
 const tables=context.caBuildTables_({...snap,chain_data:chain},{warningMinutes:120,criticalMinutes:360},new Date('2026-10-08T00:00:00Z'));
 assert.equal(tables['差分'].length,1);assert.equal(tables['要確認'].length,1);
 assert.ok(tables['チェーン検証'].some(r=>r.includes('900719925474099312345')));
 assert.ok(tables['チェーン検証'].some(r=>r.join(' ').includes('取得失敗')));
 assert.ok(tables['チェーン検証'].some(r=>r.join(' ').includes('期限超過')));
 assert.ok(context.caSheetNames_().includes('チェーン検証'));
});
console.log('Total '+passed+' tests passed');
