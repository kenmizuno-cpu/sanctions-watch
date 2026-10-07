const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const NOW = Date.parse('2026-10-01T09:00:00Z');
const HEADER = 'checked_at,source,status,content_hash,source_updated,record_count,raw_path\n';
const iso = age => new Date(NOW - age * 60000).toISOString().replace('.000Z', 'Z');
const pair = (age, status = 'unchanged') => ['ofac_sdn', 'ofac_cons'].map(s => `${iso(age)},${s},${status},abc,,1,`).join('\n') + '\n';

function environment(options = {}) {
  const values = {OFAC_WATCHDOG_TOKEN: 'test-token', OFAC_WATCHDOG_EMAIL: 'owner@example.com', ...options.properties};
  const requests = [], mail = [], triggers = [], cells = {};
  let stateWrites=0;
  const range = key => ({setValue(v) {cells[key] = v; return this;}, setValues(v) {cells[key] = v; return this;}, setNumberFormat() {return this;}, setBackground() {return this;}, setFontWeight() {return this;}, setWrap() {return this;}});
  const sheet = {getRange: (...a) => range(a.join(',')), setFrozenRows() {}, setColumnWidth() {}, getMaxRows: () => 100, getMaxColumns: () => 26};
  const csv = options.csv === undefined ? HEADER + pair(10) : options.csv;
  const previousCsv = options.previousCsv;
  const response = (status, body = '') => ({getResponseCode: () => status, getContentText: () => typeof body === 'string' ? body : JSON.stringify(body)});
  const completed = options.completed || {id: 7, status: 'completed', conclusion: 'success', updated_at: iso(9), html_url: 'https://github.com/kenmizunokuro/sanctions-watch/actions/runs/7'};
  const c = vm.createContext({
    console: {log() {}, error() {}},
    Date: class extends Date {constructor(...args) {super(...(args.length ? args : [options.now ?? NOW]));} static now() {return options.now ?? NOW;}},
    PropertiesService: {getScriptProperties: () => ({getProperty: k => values[k] ?? null, setProperty: (k, v) => {if(k==='OFAC_WATCHDOG_STATE'){stateWrites++;if(options.failFirstStateSave||stateWrites===options.failStateSaveAt){options.failFirstStateSave=false;throw Error('state save unavailable');}} values[k] = v;}})},
    LockService: {getScriptLock: () => ({tryLock: () => options.lock !== false, releaseLock() {}})},
    Utilities: {parseCsv: text => text.trim().split(/\r?\n/).map(r => r.split(',')), base64Decode: text => Buffer.from(text, 'base64'), newBlob: data => ({getDataAsString: () => Buffer.from(data).toString('utf8')})},
    UrlFetchApp: {fetch: (url, config) => {
      requests.push({url, ...config});
      if (options.apiError) return response(options.apiError, {message: 'Bad credentials'});
      if (url.includes('/contents/data/heartbeat/')) {
        const text = url.includes('2026-09.csv') ? previousCsv : csv;
        return text === undefined || text === null ? response(404) : response(200, {encoding: 'base64', content: Buffer.from(text).toString('base64')});
      }
      if (url.includes('/contents/data/monitoring/ofac_attempt.json')) return options.attempt ? response(200, {encoding: 'base64', content: Buffer.from(JSON.stringify(options.attempt)).toString('base64')}) : response(404);
      if (url.includes('/dispatches')) return response(options.dispatchStatus || 204);
      if (url.includes('status=completed')) return response(200, {total_count: 1, workflow_runs: [completed]});
      if (url.includes('/runs?')) {
        const status = new URL(url).searchParams.get('status');
        const runs = options.active?.status === status ? [options.active] : [];
        return response(200, {total_count: runs.length, workflow_runs: runs});
      }
      throw Error('unexpected API request: ' + url);
    }},
    MailApp: {getRemainingDailyQuota: () => options.quota === undefined ? 100 : options.quota, sendEmail: message => {if (options.mailError) throw Error(typeof options.mailError==='string'?options.mailError:'mail unavailable'); mail.push(message);}},
    SpreadsheetApp: {openById(id) {if (options.sheetError) throw Error('sheet unavailable'); assert.equal(id, 'dashboard-id'); return {getSheetByName: () => sheet, insertSheet: () => sheet};}},
    ScriptApp: {getProjectTriggers: () => options.existingTriggers || [], deleteTrigger: trigger => {triggers.push({deleted: trigger.getHandlerFunction()});}, newTrigger: handler => ({timeBased() {return this;}, everyMinutes(minutes) {this.minutes = minutes; return this;}, create() {triggers.push({handler, minutes: this.minutes});}})},
  });
  const path = 'apps_script/OfacWatchdog.gs';
  if (fs.existsSync(path)) vm.runInContext(fs.readFileSync(path, 'utf8'), c);
  return {c, values, requests, mail, triggers, cells, state: () => JSON.parse(values.OFAC_WATCHDOG_STATE || '{}'), dispatches: () => requests.filter(r => r.url.includes('/dispatches'))};
}

test('healthy paired check does not dispatch or notify', () => {
  const e = environment(); assert.equal(typeof e.c.ofacWatchdogTick, 'function');
  e.c.ofacWatchdogTick(); assert.equal(e.dispatches().length, 0); assert.equal(e.mail.length, 0);
  assert.equal(e.state().lastSuccessAt, iso(10));
});
test('75 minute threshold rescues main through workflow_dispatch once within cooldown', () => {
  const e = environment({csv: HEADER + pair(76)}); e.c.ofacWatchdogTick(); e.c.ofacWatchdogTick();
  assert.equal(e.dispatches().length, 1); assert.equal(e.dispatches()[0].method, 'post');
  assert.deepEqual(JSON.parse(e.dispatches()[0].payload), {ref: 'main'}); assert.equal(e.mail.length, 0);
});
test('pending and running OFAC runs suppress a duplicate dispatch', () => {
  for (const status of ['queued', 'in_progress', 'pending', 'waiting', 'requested']) {
    const e = environment({csv: HEADER + pair(100), active: {id: 20, status, created_at: iso(40), html_url: 'https://github.com/kenmizunokuro/sanctions-watch/actions/runs/20'}});
    e.c.ofacWatchdogTick(); assert.equal(e.dispatches().length, 0, status); assert.equal(e.mail.length, 1, status);
  }
});
test('SDN only, failures and future checks cannot hide an old paired success across month boundary', () => {
  const e = environment({previousCsv: HEADER + pair(720), csv: HEADER + `${iso(5)},ofac_sdn,unchanged,abc,,1,\n` + pair(3, 'error') + pair(-60)});
  e.c.ofacWatchdogTick(); assert.equal(e.state().lastSuccessAt, iso(720)); assert.equal(e.dispatches().length, 1);
  assert.match(e.mail[0].subject, /重大/);
});
test('304 and pending deletion review are successful observations for both lists', () => {
  const e = environment({csv: HEADER + pair(2, 'review_required')}); e.c.ofacWatchdogTick();
  assert.equal(e.state().lastSuccessAt, iso(2)); assert.equal(e.dispatches().length, 0);
});
test('new month missing CSV falls back to previous month', () => {
  const e = environment({csv: null, previousCsv: HEADER + pair(720)}); e.c.ofacWatchdogTick();
  assert.equal(e.state().lastSuccessAt, iso(720)); assert.equal(e.dispatches().length, 1);
});
test('90 and 150 minute alerts escalate, deduplicate and notify recovery', () => {
  const e = environment({csv: HEADER + pair(95)}); e.c.ofacWatchdogTick(); e.c.ofacWatchdogTick();
  assert.equal(e.mail.length, 1); assert.match(e.mail[0].subject, /警告/);
  const later = environment({csv: HEADER + pair(160), properties: e.values}); later.c.ofacWatchdogTick();
  assert.equal(later.mail.length, 1); assert.match(later.mail[0].subject, /重大/);
  const healthy = environment({properties: later.values}); healthy.c.ofacWatchdogTick();
  assert.equal(healthy.mail.length, 1); assert.match(healthy.mail[0].subject, /復旧/);
});
test('a failed workflow reports an incident even when preceding heartbeat is fresh', () => {
  const e = environment({completed: {id: 8, status: 'completed', conclusion: 'failure', updated_at: iso(1), html_url: 'https://github.com/kenmizunokuro/sanctions-watch/actions/runs/8'}});
  e.c.ofacWatchdogTick(); assert.equal(e.mail.length, 1); assert.match(e.mail[0].subject, /実行失敗/);
  assert.match(e.mail[0].body, /\/runs\/8/);
});
test('a newer persisted failed attempt remains visible while its workflow is unfinished', () => {
  const e = environment({attempt: {outcome: 'failure', finished_at: iso(1), run_url: 'https://github.com/kenmizunokuro/sanctions-watch/actions/runs/8'}});
  e.c.ofacWatchdogTick(); assert.match(e.mail[0].subject, /実行失敗/);
});
test('API auth failure alerts without blindly dispatching or exposing token', () => {
  const e = environment({apiError: 401}); e.c.ofacWatchdogTick();
  assert.equal(e.dispatches().length, 0); assert.equal(e.mail.length, 1);
  assert.match(e.mail[0].subject, /外部監視エラー/); assert.doesNotMatch(JSON.stringify(e.mail) + e.values.OFAC_WATCHDOG_STATE, /test-token/);
});
test('rejected dispatch is not saved as accepted and alerts the operator', () => {
  const e = environment({csv: HEADER + pair(76), dispatchStatus: 403}); e.c.ofacWatchdogTick();
  assert.equal(e.state().lastDispatchAt, undefined); assert.match(e.mail[0].subject, /外部監視エラー/);
});
test('notification failure preserves accepted dispatch but retries the alert next tick', () => {
  const e = environment({csv: HEADER + pair(100), mailError: true}); assert.throws(() => e.c.ofacWatchdogTick(), /mail unavailable/);
  assert.ok(e.state().lastDispatchAt); assert.notEqual(e.state().lastAlertKey, 'warning');
  const retry = environment({csv: HEADER + pair(100), properties: e.values}); retry.c.ofacWatchdogTick();
  assert.equal(retry.dispatches().length, 0); assert.equal(retry.mail.length, 1);
});
test('no complete success data sends an error and cannot become healthy', () => {
  const e = environment({csv: HEADER}); e.c.ofacWatchdogTick();
  assert.equal(e.dispatches().length, 0); assert.match(e.mail[0].subject, /外部監視エラー/);
});
test('dry check observes state without dispatching or emailing', () => {
  const e = environment({csv: HEADER + pair(200)}); const result = e.c.checkOfacWatchdog();
  assert.equal(result.ageMinutes, 200); assert.equal(e.dispatches().length, 0); assert.equal(e.mail.length, 0);
});
test('setup verifies read and notification before creating one dedicated 10 minute trigger', () => {
  const e = environment({existingTriggers: [{getHandlerFunction: () => 'ofacWatchdogTick'}, {getHandlerFunction: () => 'scheduledSyncAll'}]}); e.c.installOfacWatchdog();
  assert.equal(e.mail.length, 1); assert.deepEqual(e.triggers, [{deleted: 'ofacWatchdogTick'}, {handler: 'ofacWatchdogTick', minutes: 10}]);
  assert.equal(e.dispatches().length, 0);
});
test('setup stops before installing a trigger if token, email, read or notification is unavailable', () => {
  for (const options of [{properties: {OFAC_WATCHDOG_TOKEN: ''}}, {properties: {OFAC_WATCHDOG_EMAIL: ''}}, {apiError: 401}, {quota: 0}, {mailError: true}]) {
    const e = environment(options); assert.equal(typeof e.c.installOfacWatchdog, 'function'); assert.throws(() => e.c.installOfacWatchdog()); assert.equal(e.triggers.length, 0);
  }
});
test('workflow failure still escalates when check age reaches 150 minutes', () => {
  const failed = {id: 8, status: 'completed', conclusion: 'failure', updated_at: iso(1), html_url: 'https://github.com/kenmizunokuro/sanctions-watch/actions/runs/8'};
  const e = environment({completed: failed}); e.c.ofacWatchdogTick();
  const worse = environment({completed: failed, csv: HEADER + pair(160), properties: e.values}); worse.c.ofacWatchdogTick();
  assert.equal(worse.mail.length, 1); assert.match(worse.mail[0].subject, /重大/);
});
test('continuing incidents repeat after six hours and a fresh check resets the incident', () => {
  const e = environment({csv: HEADER + pair(100), properties: {OFAC_WATCHDOG_STATE: JSON.stringify({lastAlertKey: 'warning', lastAlertAt: NOW - 360 * 60000})}});
  e.c.ofacWatchdogTick(); assert.equal(e.mail.length, 1);
});
test('a duplicate error row prevents same-time successful rows from claiming freshness', () => {
  const e = environment({previousCsv: HEADER + pair(720), csv: HEADER + pair(1) + `${iso(1)},ofac_cons,error,abc,,1,\n`});
  e.c.ofacWatchdogTick(); assert.equal(e.state().lastSuccessAt, iso(720));
});
test('malformed heartbeat and locked parallel tick cannot dispatch', () => {
  const e = environment({csv: 'bad,columns\n'}); e.c.ofacWatchdogTick(); assert.equal(e.dispatches().length, 0); assert.equal(e.mail.length, 1);
  const locked = environment({lock: false}); locked.c.ofacWatchdogTick(); assert.equal(locked.requests.length, 0); assert.equal(locked.mail.length, 0);
});
test('persistent dispatch failure cannot suppress 90 and 150 minute escalation', () => {
  const first = environment({csv: HEADER + pair(76), dispatchStatus: 403}); first.c.ofacWatchdogTick();
  const warning = environment({csv: HEADER + pair(95), dispatchStatus: 403, properties: first.values}); warning.c.ofacWatchdogTick();
  assert.equal(warning.mail.length, 1); assert.match(warning.mail[0].subject, /警告/);
  const critical = environment({csv: HEADER + pair(160), dispatchStatus: 403, properties: warning.values}); critical.c.ofacWatchdogTick();
  assert.equal(critical.mail.length, 1); assert.match(critical.mail[0].subject, /重大/);
});
test('read failure escalates using last observed paired success instead of hiding staleness', () => {
  const e = environment({apiError: 401, properties: {OFAC_WATCHDOG_STATE: JSON.stringify({lastSuccessAt: iso(160), lastAlertKey: 'watchdog_error', lastAlertAt: NOW - 10 * 60000})}});
  e.c.ofacWatchdogTick(); assert.equal(e.mail.length, 1); assert.match(e.mail[0].subject, /重大/); assert.match(e.mail[0].body, /160/);
});

test('every tick publishes only safe evidence to the separately configured dashboard', () => {
  const e = environment({properties: {OFAC_WATCHDOG_SPREADSHEET_ID: 'dashboard-id'}});
  e.c.ofacWatchdogTick();
  assert.ok(e.state().lastCompletedAt);
  const rows = e.cells['5,1,24,3'];
  assert.ok(rows, 'status snapshot must be written');
  const fields = Object.fromEntries(rows.map(r => [r[0],r[1]]));
  assert.equal(fields.lastTickAt, '2026-10-01T09:00:00Z');
  assert.equal(fields.triggerCount, '0');
  assert.equal(fields.lastSuccessAt, '2026-10-01T08:50:00Z');
  assert.doesNotMatch(JSON.stringify(e.cells), /test-token|owner@example.com/);
});
test('rejected dispatch records attempt HTTP and error while never claiming acceptance', () => {
  const e = environment({csv: HEADER + pair(100), dispatchStatus:403}); e.c.ofacWatchdogTick();
  assert.equal(e.state().lastDispatchHttp, 403);
  assert.equal(e.state().dispatchDecision, 'error');
  assert.ok(e.state().lastDispatchAttemptAt);
  assert.equal(e.state().consecutiveErrors, 1);
  assert.match(e.state().lastError, /HTTP 403/);
});
test('configuration failure is recorded and published before the tick fails', () => {
  const e = environment({properties: {OFAC_WATCHDOG_TOKEN:'',OFAC_WATCHDOG_SPREADSHEET_ID:'dashboard-id'}});
  assert.throws(() => e.c.ofacWatchdogTick());
  assert.ok(e.state().lastTickAt);
  assert.ok(e.state().lastCompletedAt);
  assert.match(e.state().lastError, /OFAC_WATCHDOG_TOKEN/);
  assert.ok(e.cells['5,1,24,3']);
});
test('mail failure and status publishing failure do not erase accepted rescue evidence', () => {
  const e = environment({csv: HEADER + pair(100),mailError:true, sheetError:true, properties:{OFAC_WATCHDOG_SPREADSHEET_ID:'dashboard-id'}});
  assert.throws(() => e.c.ofacWatchdogTick(), /mail unavailable/);
  assert.equal(e.dispatches().length,1);
  assert.ok(e.state().lastDispatchAt);
  assert.match(e.state().lastNotificationError,/mail unavailable/);
  assert.match(e.state().lastPublishError,/sheet unavailable/);
});
test('publishing existing status cannot manufacture a new tick or force workflow/email', () => {
  const e = environment({properties:{OFAC_WATCHDOG_SPREADSHEET_ID:'dashboard-id',OFAC_WATCHDOG_STATE:JSON.stringify({lastTickAt:iso(400)})}});
  assert.equal(typeof e.c.publishOfacWatchdogStatus, 'function');
  e.c.publishOfacWatchdogStatus();
  assert.equal(e.state().lastTickAt,iso(400));
  assert.equal(e.requests.length,0); assert.equal(e.mail.length,0);
});
test('real clock milliseconds produce usable second precision evidence', () => {
  const e=environment({now:NOW+237,properties:{OFAC_WATCHDOG_SPREADSHEET_ID:'dashboard-id'}});e.c.ofacWatchdogTick();
  const fields=Object.fromEntries(e.cells['5,1,24,3'].map(r=>[r[0],r[1]]));
  for(const k of ['lastTickAt','lastCompletedAt','lastObservationAt','publishedAt']) assert.equal(fields[k],'2026-10-01T09:00:00Z',k);
});
test('mail errors redact actual recipient addresses before persistence and publication',()=>{
  const e=environment({csv:HEADER+pair(100),mailError:'Invalid email: owner@example.com',properties:{OFAC_WATCHDOG_SPREADSHEET_ID:'dashboard-id'}});
  assert.throws(()=>e.c.ofacWatchdogTick());
  assert.doesNotMatch(e.values.OFAC_WATCHDOG_STATE+JSON.stringify(e.cells),/owner@example.com/);
});
test('a failed start-state write cannot publish the previous healthy observation as new success',()=>{
  const e=environment({failFirstStateSave:true,properties:{OFAC_WATCHDOG_STATE:JSON.stringify({lastSuccessAt:iso(10),lastObservationAt:iso(2),lastObservation:{key:'healthy'},consecutiveErrors:0})}});
  assert.throws(()=>e.c.ofacWatchdogTick());
  assert.equal(e.requests.length,0);
  assert.match(e.state().lastObservation.key,/watchdog_error/);
  assert.equal(e.state().consecutiveErrors,1);
});
test('a failed observation-state write cannot conceal a skipped recovery notification',()=>{
  const e=environment({failStateSaveAt:2,properties:{OFAC_WATCHDOG_STATE:JSON.stringify({lastAlertKey:'warning'})}});
  assert.throws(()=>e.c.ofacWatchdogTick());
  assert.equal(e.mail.length,0);
  assert.equal(e.state().lastObservation.key,'watchdog_error');
  assert.match(e.state().lastError,/state save unavailable/);
});
