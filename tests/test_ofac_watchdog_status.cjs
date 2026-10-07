const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const NOW = Date.parse('2026-10-01T09:00:00Z');
const stamp = age => new Date(NOW-age*60000).toISOString().replace('.000Z','Z');
const fresh = overrides => ({schemaVersion:'1',repo:'kenmizunokuro/sanctions-watch',publishedAt:stamp(2),triggerCount:'1',tokenConfigured:'true',emailConfigured:'true',lastTickAt:stamp(2),lastCompletedAt:stamp(2),lastObservationAt:stamp(2),lastSuccessAt:stamp(10),consecutiveErrors:'0',lastObservationKey:'healthy',stateInvalid:'false',version:'1.1.0',dispatchDecision:'not_needed',...overrides});
function context() {
  const c=vm.createContext({Date,console});
  const path='apps_script/OfacWatchdogStatus.gs';
  if(fs.existsSync(path)) vm.runInContext(fs.readFileSync(path,'utf8'),c);
  assert.equal(typeof c.wdStatusEvaluate_,'function');
  return c;
}
const evaluate = fields => context().wdStatusEvaluate_(fields,NOW,'kenmizunokuro/sanctions-watch');
test('fresh paired success and an actual tick pass while no rescue needed is informational',()=>{
  const h=evaluate(fresh()); assert.equal(h.severity,0); assert.equal(h.passed,true); assert.equal(h.tickAge,2);
});
test('newly published snapshots cannot make a stopped or never executed timer healthy',()=>{
  for(const overrides of [{lastTickAt:stamp(400),lastCompletedAt:stamp(400)},{lastTickAt:'',lastCompletedAt:''},{triggerCount:'0'},{triggerCount:'2'}]){
    const h=evaluate(fresh(overrides)); assert.equal(h.passed,false); assert.equal(h.severity,3);
  }
});
test('stale snapshot is warning after 25 minutes and critical after 60',()=>{
  for(const [age,want] of [[25,0],[26,2],[61,3]]) assert.equal(evaluate(fresh({publishedAt:stamp(age)})).severity,want);
});
test('an unfinished tick and invalid future date must never pass',()=>{
  for(const overrides of [{lastTickAt:stamp(0),lastCompletedAt:stamp(10)},{lastTickAt:stamp(-1)},{lastSuccessAt:stamp(-1)},{schemaVersion:'2'},{repo:'other/repo'},{consecutiveErrors:'bad'},{lastObservationKey:''}]) assert.equal(evaluate(fresh(overrides)).passed,false);
});
test('latest API/notification errors fail while historical recovered errors stay informational',()=>{
  assert.equal(evaluate(fresh({lastObservationKey:'watchdog_error',consecutiveErrors:'1',lastError:'HTTP 401'})).severity,3);
  assert.equal(evaluate(fresh({lastNotificationError:'quota'})).severity,3);
  assert.equal(evaluate(fresh({lastError:'old HTTP 403',lastDispatchHttp:'403'})).severity,0);
});
test('upstream staleness is recomputed now even when saved observation says healthy',()=>{
  assert.equal(evaluate(fresh({lastSuccessAt:stamp(100)})).severity,2);
  assert.equal(evaluate(fresh({lastSuccessAt:stamp(400)})).severity,3);
});
test('HTTP 204 acceptance alone does not claim a successful OFAC fetch',()=>{
  const h=evaluate(fresh({lastSuccessAt:stamp(100),lastDispatchAt:stamp(2),lastDispatchHttp:'204',dispatchDecision:'accepted'}));
  assert.equal(h.passed,false); assert.match(h.dispatchDetail,/未確認/);
});
test('missing snapshot is explicitly unconnected and critical',()=>{
  const h=evaluate({}); assert.equal(h.passed,false); assert.equal(h.severity,3); assert.match(h.state,/未接続/);
});
test('reader rejects duplicated keys instead of choosing a reassuring value',()=>{
  const c=context();const ss={getSheetByName:()=>({getLastRow:()=>6,getRange:()=>({getDisplayValues:()=>[['schemaVersion','1'],['schemaVersion','1']]})})};
  assert.throws(()=>c.wdStatusRead_(ss),/重複/);
});
test('dashboard rendering reflects stopped watchdog in overall health without changing source counts',()=>{
  const c=context(); const writes={};const sheet={getRange(...args){const key=args.join(',');return {setValues(v){writes[key]=v;return this;},setValue(v){writes[key]=v;return this;},getDisplayValue:()=> '正常',setBackground(){return this;},setFontColor(){return this;},setFontWeight(){return this;},setWrap(){return this;},setVerticalAlignment(){return this;}};},getMaxRows:()=>100,setRowHeight(){}};
  const health=c.wdStatusEvaluate_(fresh({lastTickAt:stamp(400),lastCompletedAt:stamp(400)}),NOW,'kenmizunokuro/sanctions-watch');
  c.wdStatusRender_({getSheetByName:()=>sheet},health,NOW);
  assert.equal(writes.A5,'異常'); assert.ok(writes['22,1,4,8']); assert.equal(writes['A5:F5'],undefined);
});
