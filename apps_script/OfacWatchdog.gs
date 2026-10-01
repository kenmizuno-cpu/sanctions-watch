/**
 * OFAC外部監視。専用のGASプロジェクトにこのファイルだけを配置する。
 * Script Properties: OFAC_WATCHDOG_TOKEN / OFAC_WATCHDOG_EMAIL
 * 導入手順: docs/ofac-watchdog-setup.md
 */
const OFAC_WATCHDOG = Object.freeze({
  repo: 'kenmizuno-cpu/sanctions-watch',
  workflow: 'watch-ofac.yml',
  rescueMinutes: 75,
  warningMinutes: 90,
  criticalMinutes: 150,
  cooldownMinutes: 30,
  reminderMinutes: 360,
});

function ofacWatchdogConfig_() {
  const props = PropertiesService.getScriptProperties();
  const token = (props.getProperty('OFAC_WATCHDOG_TOKEN') || '').trim();
  const email = (props.getProperty('OFAC_WATCHDOG_EMAIL') || '').trim();
  const repo = (props.getProperty('OFAC_WATCHDOG_REPO') || OFAC_WATCHDOG.repo).trim();
  if (!token) throw new Error('OFAC_WATCHDOG_TOKEN をScript Propertiesに設定してください');
  const recipients = email.split(',').map(v => v.trim());
  if (!email || recipients.some(v => !/^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/.test(v))) {
    throw new Error('OFAC_WATCHDOG_EMAIL に通知先メールを設定してください');
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) throw new Error('repository名が不正です');
  return {props: props, token: token, email: recipients.join(','), recipientCount: recipients.length, repo: repo};
}

function ofacWatchdogApi_(cfg, path, method, payload, optional) {
  const options = {
    method: method || 'get',
    headers: {
      Authorization: 'Bearer ' + cfg.token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    followRedirects: false,
    muteHttpExceptions: true,
  };
  if (payload !== undefined) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }
  const response = UrlFetchApp.fetch('https://api.github.com/repos/' + cfg.repo + path, options);
  const code = response.getResponseCode();
  if (optional && code === 404) return null;
  const expected = method === 'post' ? 204 : 200;
  if (code !== expected) throw new Error('GitHub API HTTP ' + code + ': ' + path);
  return code === 204 ? null : JSON.parse(response.getContentText('UTF-8'));
}

function ofacWatchdogFile_(cfg, path) {
  const body = ofacWatchdogApi_(cfg, '/contents/' + path + '?ref=main', 'get', undefined, true);
  if (body === null) return null;
  if (body.encoding !== 'base64' || typeof body.content !== 'string') throw new Error('GitHub Contents応答が不正: ' + path);
  return Utilities.newBlob(Utilities.base64Decode(body.content.replace(/\s/g, ''))).getDataAsString('UTF-8');
}

function ofacWatchdogTime_(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return NaN;
  const ms = Date.parse(value);
  if (!Number.isFinite(ms) || new Date(ms).toISOString().replace('.000Z', 'Z') !== value) return NaN;
  return ms;
}

function ofacWatchdogObserve_(cfg, now) {
  const current = new Date(now);
  const previous = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() - 1, 1));
  const month = date => date.toISOString().slice(0, 7);
  const groups = {};
  const successful = ['unchanged', 'changed', 'no_effective_change', 'backfill', 'review_required'];
  [month(previous), month(current)].forEach(key => {
    const text = ofacWatchdogFile_(cfg, 'data/heartbeat/' + key + '.csv');
    if (text === null) return;
    const rows = Utilities.parseCsv(text.replace(/^\uFEFF/, ''));
    const expected = ['checked_at', 'source', 'status', 'content_hash', 'source_updated', 'record_count', 'raw_path'];
    if (!rows.length || rows[0].join('|') !== expected.join('|')) throw new Error('heartbeatの列定義が不正: ' + key);
    rows.slice(1).forEach(row => {
      const source = row[1];
      if (source !== 'ofac_sdn' && source !== 'ofac_cons') return;
      const stamp = ofacWatchdogTime_(row[0]);
      if (!Number.isFinite(stamp) || stamp > now) return;
      if (!groups[stamp]) groups[stamp] = {};
      const good = successful.indexOf(row[2]) !== -1;
      groups[stamp][source] = groups[stamp][source] === false ? false : good;
    });
  });
  const stamps = Object.keys(groups).map(Number).filter(t => groups[t].ofac_sdn === true && groups[t].ofac_cons === true);
  if (!stamps.length) throw new Error('SDN/Consolidated両方の成功heartbeatが見つかりません');
  const success = Math.max.apply(null, stamps);
  const runs = ofacWatchdogApi_(cfg, '/actions/workflows/' + OFAC_WATCHDOG.workflow + '/runs?branch=main&status=completed&per_page=1');
  if (!Array.isArray(runs.workflow_runs)) throw new Error('workflow runs応答が不正');
  const completed = runs.workflow_runs[0];
  const attemptText = ofacWatchdogFile_(cfg, 'data/monitoring/ofac_attempt.json');
  const attempt = attemptText === null ? null : JSON.parse(attemptText);
  let failedRunUrl = '';
  if (completed && ['failure', 'timed_out', 'cancelled', 'action_required', 'stale'].indexOf(completed.conclusion) !== -1 &&
      ofacWatchdogTime_(completed.updated_at) >= success) {
    failedRunUrl = completed.html_url || ('https://github.com/' + cfg.repo + '/actions/runs/' + completed.id);
  }
  if (attempt && attempt.outcome === 'failure' && ofacWatchdogTime_(attempt.finished_at) >= success) {
    failedRunUrl = attempt.run_url || failedRunUrl || ('https://github.com/' + cfg.repo + '/actions');
  }
  return {lastSuccessAt: new Date(success).toISOString().replace('.000Z', 'Z'), ageMinutes: (now - success) / 60000, failedRunUrl: failedRunUrl};
}

function ofacWatchdogActive_(cfg) {
  const active = [];
  // Separate filters find old queued runs as well as recent runs; no history page limit.
  ['queued', 'in_progress', 'pending', 'waiting', 'requested'].forEach(status => {
    const body = ofacWatchdogApi_(cfg, '/actions/workflows/' + OFAC_WATCHDOG.workflow + '/runs?branch=main&status=' + status + '&per_page=1');
    if (!Array.isArray(body.workflow_runs)) throw new Error('active runs応答が不正');
    body.workflow_runs.forEach(run => active.push(run));
  });
  return active;
}

function ofacWatchdogSave_(cfg, state) {
  cfg.props.setProperty('OFAC_WATCHDOG_STATE', JSON.stringify(state));
}

function ofacWatchdogMail_(cfg, subject, body) {
  if (MailApp.getRemainingDailyQuota() < cfg.recipientCount) throw new Error('メール送信の残りクォータが不足しています');
  MailApp.sendEmail({to: cfg.email, subject: subject, body: body});
}

function ofacWatchdogNotify_(cfg, state, incident, detail, now) {
  const repeat = incident.key !== 'healthy' && now - (state.lastAlertAt || 0) >= OFAC_WATCHDOG.reminderMinutes * 60000;
  const changed = state.lastAlertKey !== incident.key;
  if ((changed || repeat) && (incident.key !== 'healthy' || state.lastAlertKey)) {
    ofacWatchdogMail_(cfg, '[OFAC] ' + incident.title, detail + '\n\nhttps://github.com/' + cfg.repo + '/actions/workflows/' + OFAC_WATCHDOG.workflow);
    // Mark delivered only after MailApp succeeds. A failed delivery is retried next tick.
    state.lastAlertAt = now;
  }
  state.lastAlertKey = incident.key;
  ofacWatchdogSave_(cfg, state);
}

/** Read-only verification: neither workflow dispatch nor email. */
function checkOfacWatchdog() {
  const cfg = ofacWatchdogConfig_();
  const observed = ofacWatchdogObserve_(cfg, Date.now());
  console.log(JSON.stringify(observed));
  return observed;
}

/** Verify API read and mail delivery before installing the external timer. */
function installOfacWatchdog() {
  const cfg = ofacWatchdogConfig_();
  const observed = ofacWatchdogObserve_(cfg, Date.now());
  ofacWatchdogMail_(cfg, '[OFAC] 外部監視の通知テスト', '10分間隔の外部監視を設定します。\n最終成功: ' + observed.lastSuccessAt + '\n経過: ' + Math.floor(observed.ageMinutes) + '分');
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'ofacWatchdogTick').forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('ofacWatchdogTick').timeBased().everyMinutes(10).create();
  console.log('OFAC外部監視を設定しました。通知テストの到着を確認し、ofacWatchdogTickを一度実行してください。');
}

function ofacWatchdogTick() {
  const cfg = ofacWatchdogConfig_();
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const now = Date.now();
    const state = JSON.parse(cfg.props.getProperty('OFAC_WATCHDOG_STATE') || '{}');
    state.lastTickAt = new Date(now).toISOString().replace('.000Z', 'Z');
    let incident = {key: 'healthy', title: '監視復旧'};
    let detail = '';
    try {
      const observed = ofacWatchdogObserve_(cfg, now);
      state.lastSuccessAt = observed.lastSuccessAt;
      detail = '最終成功: ' + observed.lastSuccessAt + ' (UTC)\n経過: ' + Math.floor(observed.ageMinutes) + '分';
      if (observed.ageMinutes >= OFAC_WATCHDOG.criticalMinutes) incident = {key: 'critical', title: '重大: 監視遅延'};
      else if (observed.ageMinutes >= OFAC_WATCHDOG.warningMinutes) incident = {key: 'warning', title: '警告: 監視遅延'};
      if (observed.failedRunUrl) {
        const critical = observed.ageMinutes >= OFAC_WATCHDOG.criticalMinutes;
        incident = {key: critical ? 'failure_critical' : 'failure', title: '実行失敗' + (critical ? '・重大な監視遅延' : '')};
        detail += '\n失敗実行: ' + observed.failedRunUrl;
      }
      if (observed.ageMinutes >= OFAC_WATCHDOG.rescueMinutes) {
        const active = ofacWatchdogActive_(cfg);
        const sinceDispatch = now - (state.lastDispatchAt || 0);
        if (active.length) detail += '\n稼働・待機中のOFAC実行あり: ' + active.map(r => r.html_url || r.id).join(', ');
        else if (sinceDispatch >= 0 && sinceDispatch < OFAC_WATCHDOG.cooldownMinutes * 60000) detail += '\n再起動の30分クールダウン中';
        else {
          ofacWatchdogApi_(cfg, '/actions/workflows/' + OFAC_WATCHDOG.workflow + '/dispatches', 'post', {ref: 'main'});
          state.lastDispatchAt = now;
          ofacWatchdogSave_(cfg, state);
          detail += '\n外部タイマーから再実行要求を受理（成功確認は次回以降のheartbeat）';
        }
      }
    } catch (error) {
      incident = {key: 'watchdog_error', title: '外部監視エラー'};
      // API errors must not hide freshness escalation. On a read outage use
      // the last confirmed paired success; its age continues to increase.
      const knownSuccess = ofacWatchdogTime_(state.lastSuccessAt);
      if (Number.isFinite(knownSuccess) && knownSuccess <= now) {
        const knownAge = (now - knownSuccess) / 60000;
        detail += '\n最後に確認できた成功: ' + state.lastSuccessAt + ' (UTC)\n現在の経過: ' + Math.floor(knownAge) + '分';
        if (knownAge >= OFAC_WATCHDOG.criticalMinutes) incident = {key: 'watchdog_error_critical', title: '外部監視エラー・重大な監視遅延'};
        else if (knownAge >= OFAC_WATCHDOG.warningMinutes) incident = {key: 'watchdog_error_warning', title: '外部監視エラー・警告: 監視遅延'};
      }
      detail += '\n' + String(error.message || error);
    }
    state.lastObservation = {key: incident.key, detail: detail};
    ofacWatchdogSave_(cfg, state);
    ofacWatchdogNotify_(cfg, state, incident, detail, now);
    console.log(JSON.stringify(state));
  } finally {
    lock.releaseLock();
  }
}
