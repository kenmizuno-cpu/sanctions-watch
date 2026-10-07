/** ダッシュボード側に追加。救済側のOfacWatchdog.gsとは別ファイルです。 */
const WD_STATUS = Object.freeze({sheet:'11_OFAC救済監視',dashboard:'01_監視ダッシュボード',repo:'kenmizunokuro/sanctions-watch',warningMinutes:25,criticalMinutes:60});

function wdStatusTime_(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return NaN;
  const ms=Date.parse(value);
  return Number.isFinite(ms) && new Date(ms).toISOString().replace('.000Z','Z')===value ? ms : NaN;
}
function wdStatusJst_(value) {
  const ms=wdStatusTime_(value);
  return Number.isFinite(ms) ? new Date(ms+9*3600000).toISOString().slice(0,19).replace('T',' ') : '記録なし';
}

function wdStatusEvaluate_(f,now,expectedRepo) {
  const checks=[];
  const add=(name,ok,detail,severity=3)=>checks.push({name,ok,detail,severity:ok?0:severity});
  const age=value=>{const t=wdStatusTime_(value);return Number.isFinite(t)&&t<=now?(now-t)/60000:null;};
  const timeCheck=(name,value)=>{const n=age(value);add(name,n!==null,n===null?'記録なし・日時異常':wdStatusJst_(value)+' JST / 約'+Math.floor(n)+'分前');return n;};
  add('稼働記録接続',f.schemaVersion==='1','記録形式: '+(f.schemaVersion||'未接続'));
  add('監視リポジトリ',f.repo===expectedRepo,f.repo||'未接続');
  const publishAge=timeCheck('稼働記録の更新時刻',f.publishedAt);
  add('稼働記録の鮮度',publishAge!==null&&publishAge<=WD_STATUS.warningMinutes,publishAge===null?'未接続':Math.floor(publishAge)+'分前 / 許容25分',publishAge!==null&&publishAge<=WD_STATUS.criticalMinutes?2:3);
  add('OFAC救済トリガー',f.triggerCount==='1',(f.triggerCount||'未確認')+'個（救済側実行者の可視範囲）');
  add('GitHub認証情報',f.tokenConfigured==='true',f.tokenConfigured==='true'?'設定済み（有効性はAPI実行結果で確認）':'未設定・未確認');
  add('通知先設定',f.emailConfigured==='true',f.emailConfigured==='true'?'設定済み':'未設定・未確認');
  const tickAge=timeCheck('Watchdog最終実行',f.lastTickAt);
  add('Watchdog実行鮮度',tickAge!==null&&tickAge<=WD_STATUS.warningMinutes,tickAge===null?'未実行・日時異常':Math.floor(tickAge)+'分前 / 10分設定',tickAge!==null&&tickAge<=WD_STATUS.criticalMinutes?2:3);
  const completedAge=timeCheck('Watchdog最終処理終了',f.lastCompletedAt);
  add('Watchdog処理終了確認',completedAge!==null&&tickAge!==null&&completedAge<=tickAge,'開始より古い終了記録は未完了として扱います');
  const observedAge=timeCheck('GitHub監視状況の最終確認',f.lastObservationAt);
  add('GitHub確認の鮮度',observedAge!==null&&observedAge<=WD_STATUS.warningMinutes,observedAge===null?'未確認':Math.floor(observedAge)+'分前',observedAge!==null&&observedAge<=WD_STATUS.criticalMinutes?2:3);
  const upstreamAge=timeCheck('GitHub最終取得成功',f.lastSuccessAt);
  add('OFAC取得鮮度',upstreamAge!==null&&upstreamAge<90,upstreamAge===null?'両リスト成功が未確認':Math.floor(upstreamAge)+'分前 / 警告90分・重大150分',upstreamAge!==null&&upstreamAge<150?2:3);
  add('監視・救済エラー',f.consecutiveErrors==='0','連続エラー: '+(f.consecutiveErrors||'未確認')+(f.lastError?' / 直近履歴: '+f.lastError:''));
  add('通知エラー',!f.lastNotificationError,f.lastNotificationError||'未解消エラー記録なし');
  const key=f.lastObservationKey||'';
  const goodKeys=['healthy','warning','critical','failure','failure_critical'];
  add('Watchdog最終判定',goodKeys.includes(key)&&!/^failure/.test(key),key||'未実行');
  add('保存状態の整合性',f.stateInvalid==='false',f.stateInvalid==='false'?'解析済み':'状態の破損・未確認');
  const dispatchTime=wdStatusTime_(f.lastDispatchAt);
  let dispatchDetail='記録なし（救済不要なら正常です）';
  if(f.lastDispatchAt) {
    const valid=Number.isFinite(dispatchTime)&&dispatchTime<=now;
    add('救済受理日時',valid,valid?wdStatusJst_(f.lastDispatchAt)+' JST':'日時異常');
    dispatchDetail=valid&&wdStatusTime_(f.lastSuccessAt)>=dispatchTime?'受理後の取得成功を確認（因果関係は未確定）':'要求受理済み・その後の取得成功は未確認';
  }
  const severity=Math.max(0,...checks.map(c=>c.severity));
  return {fields:f,checks,severity,passed:severity===0,state:!f.schemaVersion?'未接続':severity===3?'重大':severity===2?'警告':'正常',tickAge,upstreamAge,publishAge,dispatchDetail,
    note:checks.filter(c=>!c.ok).map(c=>c.name+': '+c.detail).join(' / ')};
}

function wdStatusRead_(ss) {
  const sheet=ss.getSheetByName(WD_STATUS.sheet);
  if(!sheet||sheet.getLastRow()<5)return {};
  const rows=sheet.getRange(5,1,Math.min(100,sheet.getLastRow()-4),2).getDisplayValues();
  const fields={};
  rows.forEach(row=>{
    const key=String(row[0]||'').trim();if(!key)return;
    if(Object.prototype.hasOwnProperty.call(fields,key))throw new Error('OFAC救済監視の項目重複: '+key);
    fields[key]=String(row[1]||'');
  });
  return fields;
}
function wdStatusHealth_(ss,now) {
  const props=PropertiesService.getScriptProperties();
  const expected=(props.getProperty('OFAC_WATCHDOG_REPO')||WD_STATUS.repo).trim();
  try{return wdStatusEvaluate_(wdStatusRead_(ss),now,expected);}
  catch(error){const h=wdStatusEvaluate_({},now,expected);h.note=String(error.message||error);h.checks.unshift({name:'稼働記録読込',ok:false,severity:3,detail:h.note});return h;}
}

function wdStatusRender_(ss,h,now) {
  const sheet=ss.getSheetByName(WD_STATUS.dashboard);
  if(!sheet)throw new Error('01_監視ダッシュボードがありません');
  if(sheet.getMaxRows()<26)sheet.insertRowsAfter(sheet.getMaxRows(),26-sheet.getMaxRows());
  const f=h.fields;
  const decisions={not_needed:'救済不要',active_run:'実行・待機中のため見送り',cooldown:'再試行待ち（30分）',accepted:'再実行要求を受理',dispatching:'再実行要求中',error:'再実行要求失敗',observation_error:'監視状況の取得失敗'};
  const rows=[
    ['OFAC救済監視',h.state,'トリガー',(f.triggerCount||'未確認')+'個','最終実行',wdStatusJst_(f.lastTickAt),'経過',h.tickAge===null?'未確認':Math.floor(h.tickAge)+'分'],
    ['GitHub最終取得',wdStatusJst_(f.lastSuccessAt),'経過',h.upstreamAge===null?'未確認':Math.floor(h.upstreamAge)+'分','稼働記録更新',wdStatusJst_(f.publishedAt),'表示判定時刻',new Date(now+9*3600000).toISOString().slice(0,19).replace('T',' ')],
    ['最終救済受理',wdStatusJst_(f.lastDispatchAt),'HTTP',f.lastDispatchHttp||'なし','今回の判断',decisions[f.dispatchDecision]||'未確認','取得成功確認',h.dispatchDetail],
    ['連続エラー',f.consecutiveErrors||'未確認','直近エラー履歴',f.lastError||'なし','通知エラー',f.lastNotificationError||'なし','判定理由',h.note||'救済側の稼働記録は許容時間内'],
  ];
  const safe=v=>/^[\s]*[=+@-]/.test(String(v))?"'"+v:String(v);
  sheet.getRange('A21').setValue('OFAC救済監視（詳細・救済履歴は11_OFAC救済監視。表示時刻はJST）');
  sheet.getRange(22,1,4,8).setValues(rows.map(r=>r.map(safe))).setWrap(true).setVerticalAlignment('middle');
  sheet.getRange('B22').setBackground(h.severity===3?'#FEE2E2':h.severity?'#FEF3C7':'#DCFCE7').setFontWeight('bold');
  const overall=sheet.getRange('A5');
  if(h.severity===3)overall.setValue('異常');
  else if(h.severity>0&&overall.getDisplayValue()==='正常')overall.setValue('要確認');
}

function wdStatusSpreadsheet_() {
  const props=PropertiesService.getScriptProperties();
  const id=props.getProperty('OFAC_WATCHDOG_STATUS_SPREADSHEET_ID')||props.getProperty('SPREADSHEET_ID');
  if(id)return SpreadsheetApp.openById(id);
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  if(!ss)throw new Error('ダッシュボード側でsetupOfacWatchdogStatusを実行してください');
  return ss;
}

/** 表示更新のみ。GitHubへの再実行要求・メール送信はしません。 */
function refreshOfacWatchdogStatus() {
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return;
  try {const ss=wdStatusSpreadsheet_();const now=Date.now();const h=wdStatusHealth_(ss,now);wdStatusRender_(ss,h,now);return h;}
  finally{lock.releaseLock();}
}
function runOfacWatchdogSelfCheck() {
  const h=refreshOfacWatchdogStatus();
  if(!h)throw new Error('同期処理と競合しました。少し後で再実行してください');
  const message='OFAC救済監視: '+h.state+'\n\n'+h.checks.map(c=>(c.ok?'PASS':'FAIL')+' '+c.name+': '+c.detail).join('\n')+
    '\nINFO 最終救済: '+wdStatusJst_(h.fields.lastDispatchAt)+' / '+h.dispatchDetail+'\nINFO 詳細・履歴: 11_OFAC救済監視';
  console.log(message);SpreadsheetApp.getUi().alert(message);return h;
}
function ofacWatchdogStatusMenu() {
  SpreadsheetApp.getUi().createMenu('OFAC救済監視').addItem('救済監視セルフチェック','runOfacWatchdogSelfCheck').addItem('稼働表示を更新','refreshOfacWatchdogStatus').addToUi();
}
/** 既存の同期トリガーは保持し、表示更新10分と専用メニューだけを設定。 */
function setupOfacWatchdogStatus() {
  const ss=wdStatusSpreadsheet_();
  PropertiesService.getScriptProperties().setProperty('OFAC_WATCHDOG_STATUS_SPREADSHEET_ID',ss.getId());
  ScriptApp.getProjectTriggers().filter(t=>['refreshOfacWatchdogStatus','ofacWatchdogStatusMenu'].includes(t.getHandlerFunction())).forEach(t=>ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('refreshOfacWatchdogStatus').timeBased().everyMinutes(10).create();
  ScriptApp.newTrigger('ofacWatchdogStatusMenu').forSpreadsheet(ss).onOpen().create();
  refreshOfacWatchdogStatus();ofacWatchdogStatusMenu();
}
