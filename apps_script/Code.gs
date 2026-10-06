const VERSION = '0.4.3';
const DEFAULT_TZ = 'Asia/Tokyo';
const LOCK_WAIT_MS = 15000;
const MAX_RETRIES = 2;

const RE_REVIEW_URL =
  'https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/main/data/dashboard/re_review.csv';

const TAB = {
  DASH: '01_監視ダッシュボード',
  HISTORY: '02_監視履歴',
  CHANGES: '03_変更履歴',
  SEARCH: '04_名簿検索',
  ANOMALY: '05_異常履歴',
  AUDIT: '06_監査証跡',
  LATEST_DIFF: '08_最新差分',
  RE_REVIEW: '09_再審査分',
  SETTINGS: '99_設定',
  MOFA: '10_外務省資料',
};

const QUEUE_CHANGE_TYPES = new Set([
  '追加',
  '変更',
  '掲載終了',
  '掲載終了候補（要確認）',
]);

const RESOLVED_QUEUE_STATUSES = new Set([
  '対応済',
  '対応済み',
  '完了',
  '処理済',
]);

const QUEUE_STATUS_OPTIONS = [
  '未確認',
  '未対応',
  '対応中',
  '保留',
  '対応済',
];

const ROW = {
  SETTINGS_HEADER: 4,
  SETTINGS_DATA: 5,
  DATA_HEADER: 4,
  DATA_START: 5,
  SEARCH_INPUT: 'B4',
  SEARCH_HEADER: 6,
  SEARCH_DATA: 7,
};

const MOFA_DOCUMENTS_URL = 'https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/main/data/dashboard/mofa_documents.csv';

const EXPECTED = {
  mofaDocuments: ['資料イベントID','検知日時','資料区分','タイトル','公表日','公表日時精度','検知理由','取得状態','レビュー状態','発表URL','資料URL','SHA256','原本'],
  status: [
    '出所',
    '状態',
    '最終チェック',
    '最終更新',
    '件数',
    '内容ハッシュ',
  ],

  changes: [
    '検知日時',
    '出所',
    '種別',
    '受取人名',
    '変更前',
    '変更後',
  ],

  heartbeat: [
    'checked_at',
    'source',
    'status',
    'content_hash',
    'source_updated',
    'record_count',
    'raw_path',
  ],

  list: [
    '受取人名',
    'リスクタイプ',
    '状態',
    'リスク度',
  ],

  reReview: [
    '再審査ID',
    '検知日時',
    '出所',
    '対象者',
    '番号',
    '国連参照番号',
    '優先度',
    '再審査理由',
    '変更項目',
    '変更概要',
    '原本',
    '一次ソースURL',
  ],
};

const SOURCE_LABEL = {
  mof: '財務省',
  meti: '経済産業省',
  ofac_sdn: 'OFAC SDN',
  ofac_cons: 'OFAC Consolidated',
  mofa_catalog: '外務省（現行リスト）',
  mofa_press: '外務省（報道発表）',
};

const STATUS_LABEL = {
  fetched: '取得あり',
  changed: '更新あり',
  no_effective_change: '元データ更新・実質変更なし',
  unchanged: '変更なし',
  updated: '更新あり（要手動確認）',
  blocked: '自動取得不可',
  error: 'エラー',
};


/* =========================================================
 * メニュー
 * ========================================================= */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('制裁監視')
    .addItem('今すぐ同期', 'syncAll')
    .addItem('名簿検索', 'searchName')
    .addSeparator()
    .addItem('運用セルフチェック', 'runSelfCheck')
    .addItem('JST設定を適用', 'applyJstSettings')
    .addSeparator()
    .addItem('初期設定（同期トリガー作成）', 'initialSetup')
    .addItem('トリガー再作成', 'installSyncTrigger')
    .addToUi();
}


/* =========================================================
 * 初期設定
 * ========================================================= */

function initialSetup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  if (!ss) {
    throw new Error(
      'アクティブなスプレッドシートがありません。'
    );
  }

  PropertiesService
    .getScriptProperties()
    .setProperty(
      'SPREADSHEET_ID',
      ss.getId()
    );

  ensureTimezone_(ss);
  ensureStructure_(ss);

  const minutes =
    installSyncTrigger_(true);

  syncAll();

  SpreadsheetApp.getUi().alert(
    `初期設定が完了しました。${minutes}分ごとにGitHubの監視結果を同期します。`
  );
}


function installSyncTrigger() {
  const minutes =
    installSyncTrigger_(true);

  SpreadsheetApp.getUi().alert(
    `同期トリガーを${minutes}分ごとで再作成しました。`
  );
}


function installSyncTrigger_(silent) {
  const settings =
    readSettings_();

  const requested =
    Number(
      settings['同期間隔(分)'] || 15
    );

  const allowed = [
    1,
    5,
    10,
    15,
    30,
  ];

  const minutes =
    allowed.includes(requested)
      ? requested
      : 15;

  /*
   * v0.3.1以前のsyncAllトリガーも削除。
   */
  ScriptApp.getProjectTriggers()
    .filter(
      t =>
        [
          'syncAll',
          'scheduledSyncAll',
        ].includes(
          t.getHandlerFunction()
        )
    )
    .forEach(
      t =>
        ScriptApp.deleteTrigger(t)
    );

  /*
   * v0.3.2以降の自動同期専用関数。
   */
  ScriptApp
    .newTrigger('scheduledSyncAll')
    .timeBased()
    .everyMinutes(minutes)
    .create();

  const ss =
    SpreadsheetApp.getActiveSpreadsheet();

  if (ss) {
    try {
      updateAutoSyncDashboard_(
        ss,
        evaluateAutoSyncHealth_(
          readSettings_()
        )
      );
    } catch (e) {
      /*
       * トリガー作成自体は完了しているため、
       * 表示更新失敗だけでは処理を中断しない。
       */
    }
  }

  if (!silent) {
    SpreadsheetApp
      .getActiveSpreadsheet()
      .toast(
        `同期トリガー: ${minutes}分ごと`,
        '制裁監視',
        5
      );
  }

  return minutes;
}


/* =========================================================
 * 同期入口
 * ========================================================= */

function syncAll(e) {
  /*
   * 手動メニューから呼ばれれば手動。
   *
   * 旧トリガーが残っている場合、
   * triggerUidがあれば自動として扱う。
   */
  const executionType =
    e && e.triggerUid
      ? '自動'
      : '手動';

  return syncAll_(
    executionType
  );
}


function scheduledSyncAll(e) {
  return syncAll_('自動');
}


/* =========================================================
 * 同期本体
 * ========================================================= */

function syncAll_(executionType) {
  const props =
    PropertiesService
      .getScriptProperties();

  const isAuto =
    executionType === '自動';

  /*
   * トリガーが実際に起動した証拠。
   */
  if (isAuto) {
    props.setProperty(
      'LAST_AUTO_ATTEMPT_AT',
      formatJst_(new Date())
    );
  }

  const lock =
    LockService.getScriptLock();

  /*
   * 多重実行防止。
   */
  if (
    !lock.tryLock(
      LOCK_WAIT_MS
    )
  ) {
    recordPendingLockSkip_();
    return;
  }

  const started =
    Date.now();

  const runId =
    Utilities.getUuid();

  let ss = null;

  let result = '成功';
  let errorText = '';
  let auditErrorText = '';

  let statusCount = 0;
  let changesCount = 0;
  let heartbeatCount = 0;

  const metrics = {
    http200: 0,
    http304: 0,
    bytes: 0,
    retries: 0,

    lockSkips:
      consumePendingLockSkips_(),

    newChanges: 0,
    retainedChanges: 0,
  };

  try {
    ss =
      getSpreadsheet_();

    ensureTimezone_(ss);
    ensureStructure_(ss);

    /*
     * 08_最新差分で人間が更新した対応状況を、
     * GitHub履歴の再同期より先に03_変更履歴へ退避する。
     * 08側を対応状況の正本とするための順序保証。
     */
    syncQueueStatusesToHistory_(
      ss
    );

    /*
     * 2026/9/4のAdvanced XML初回master反映を
     * 公式新規追加から切り分ける。
     * changes.csvが304でも既存履歴を移行する。
     */
    reclassifyLegacyOfacBackfillHistory_(
      ss
    );

    const settings =
      readSettings_();

    const payload =
      fetchSyncPayload_(
        settings,
        metrics
      );

    syncMofaDocuments_(ss, payload.mofaDocuments.notModified ? null : payload.mofaDocuments.rows);
    commitMofaHttpMeta_(payload.mofaDocuments, props);

    const status =
      payload.status.rows;

    const changes =
      payload.changes.rows;

    const heartbeat =
      payload.heartbeat;

    statusCount =
      status.length;

    changesCount =
      changes.length;

    heartbeatCount =
      heartbeat.length;

    /*
     * 制裁監視状態更新。
     */
    const health =
      updateDashboard_(
        ss,
        status,
        settings
      );

    /*
     * heartbeatに新規データがある場合のみ
     * 監視履歴へ追加。
     */
    if (
      heartbeat.length
    ) {
      appendHeartbeatHistory_(
        ss,
        heartbeat
      );
    }

    /*
     * changes.csvが200の場合だけ
     * 変更履歴を同期。
     *
     * 304なら既存5000件を維持。
     */
    if (
      !payload.changes.notModified
    ) {
      const changeStats =
        syncChangesMirror_(
          ss,
          changes,
          settings
        );

      metrics.newChanges =
        changeStats.newChanges;

      metrics.retainedChanges =
        changeStats.retainedChanges;

    } else {
      metrics.retainedChanges =
        countChangeRows_(ss);
    }

    /*
     * 全期間の未解消master差分だけを
     * 08_最新差分へ物理行として再構築する。
     */
    refreshUnresolvedChangesQueue_(
      ss
    );

    /*
     * 再審査キュー。
     */
    if (
      !payload.reReview.notModified
    ) {
      syncReReviewMirror_(
        ss,
        payload.reReview.rows
      );
    } else {
      refreshReReviewVisibility_(
        requireSheet_(
          ss,
          TAB.RE_REVIEW
        )
      );
    }

    /*
     * ダッシュボード直近変更4件。
     */
    refreshRecentChanges_(ss);

    /*
     * 自動同期成功記録。
     */
    if (isAuto) {
      props.setProperty(
        'LAST_AUTO_SUCCESS_AT',
        formatJst_(new Date())
      );

      props.deleteProperty(
        'LAST_AUTO_FAILURE_AT'
      );

      props.deleteProperty(
        'LAST_AUTO_FAILURE_MESSAGE'
      );
    }

    /*
     * Apps Script自身の死活監視。
     */
    const autoHealth =
      evaluateAutoSyncHealth_(
        settings
      );

    updateAutoSyncDashboard_(
      ss,
      autoHealth
    );

    /*
     * 制裁4ソース＋Apps Script自身を
     * 異常履歴へ反映。
     */
    updateAnomalies_(
      ss,
      health.concat([
        autoHealth,
      ])
    );

    /*
     * 以前のApps Scriptエラーが
     * 正常復旧していれば解消。
     */
    resolveScriptErrorAnomaly_(
      ss
    );

  } catch (err) {
    result =
      '失敗';

    errorText =
      err && err.stack
        ? err.stack
        : String(err);

    /*
     * 自動同期失敗情報。
     */
    if (isAuto) {
      props.setProperty(
        'LAST_AUTO_FAILURE_AT',
        formatJst_(new Date())
      );

      props.setProperty(
        'LAST_AUTO_FAILURE_MESSAGE',
        String(
          errorText
        ).slice(
          0,
          1000
        )
      );
    }

    if (ss) {
      try {
        markDashboardSyncError_(
          ss,
          errorText
        );

        recordScriptErrorAnomaly_(
          ss,
          errorText
        );

        updateAutoSyncDashboard_(
          ss,
          evaluateAutoSyncHealth_(
            readSettings_()
          )
        );

      } catch (secondaryErr) {
        errorText +=
          '\n[secondary] ' +
          (
            secondaryErr &&
            secondaryErr.stack
              ? secondaryErr.stack
              : String(
                  secondaryErr
                )
          );
      }
    }

  } finally {
    /*
     * 監査証跡失敗でも
     * ロックが残らない構造。
     */
    try {
      if (ss) {
        appendAudit_(
          ss,
          {
            runId,
            result,
            executionType,
            statusCount,
            changesCount,
            heartbeatCount,

            elapsed:
              Date.now() -
              started,

            errorText,
            metrics,
          }
        );
      }

    } catch (auditErr) {
      auditErrorText =
        auditErr &&
        auditErr.stack
          ? auditErr.stack
          : String(auditErr);
    }

    try {
      SpreadsheetApp.flush();

    } finally {
      lock.releaseLock();
    }
  }

  if (errorText) {
    throw new Error(
      errorText
    );
  }

  if (auditErrorText) {
    throw new Error(
      `監査証跡の記録に失敗しました: ${auditErrorText}`
    );
  }
}


/* =========================================================
 * 名簿検索
 * ========================================================= */

function searchName() {
  const ss =
    getSpreadsheet_();

  const sheet =
    requireSheet_(
      ss,
      TAB.SEARCH
    );

  const term =
    String(
      sheet
        .getRange(
          ROW.SEARCH_INPUT
        )
        .getDisplayValue() ||
        ''
    ).trim();

  if (
    !term ||
    term ===
      'ここに氏名・団体名を入力'
  ) {
    SpreadsheetApp
      .getUi()
      .alert(
        `検索語を${ROW.SEARCH_INPUT}へ入力してください。`
      );

    return;
  }

  const settings =
    readSettings_();

  const maxResults =
    Math.max(
      1,
      Math.min(
        500,
        Number(
          settings[
            '検索最大件数'
          ] || 200
        )
      )
    );

  const list =
    fetchCsvNow_(
      settings[
        'List URL'
      ],
      EXPECTED.list
    );

  const needle =
    normalizeName_(
      term
    );

  if (
    needle.length < 2
  ) {
    SpreadsheetApp
      .getUi()
      .alert(
        '検索語は正規化後2文字以上で入力してください。'
      );

    return;
  }

  const now =
    formatJst_(
      new Date()
    );

  const exact = [];
  const partial = [];

  for (
    let i = 0;
    i < list.rows.length;
    i++
  ) {
    const r =
      list.rows[i];

    const name =
      String(
        r[0] || ''
      );

    const hay =
      normalizeName_(
        name
      );

    if (!hay) {
      continue;
    }

    const out = [
      name,
      r[1],
      r[2],
      r[3],
      '',
      'GitHub list.csv',
      '簡易照合結果。最終判断はGitHub保存データ・一次ソースを確認',
      now,
    ];

    if (
      hay === needle
    ) {
      out[4] =
        '完全一致';

      exact.push(out);

      continue;
    }

    if (
      hay.includes(
        needle
      ) ||
      needle.includes(
        hay
      )
    ) {
      out[4] =
        '部分一致';

      partial.push(
        out
      );
    }
  }

  partial.sort(
    (a, b) =>
      String(
        a[0]
      ).length -
      String(
        b[0]
      ).length
  );

  const hits =
    exact
      .concat(
        partial
      )
      .slice(
        0,
        maxResults
      );

  const existingLast =
    sheet.getLastRow();

  /*
   * ヘッダー6行目を残して
   * 7行目以降のみ消す。
   */
  if (
    existingLast >=
    ROW.SEARCH_DATA
  ) {
    sheet
      .getRange(
        ROW.SEARCH_DATA,
        1,
        existingLast -
          ROW.SEARCH_DATA +
          1,
        8
      )
      .clearContent();
  }

  if (
    hits.length
  ) {
    ensureRows_(
      sheet,
      ROW.SEARCH_DATA +
        hits.length -
        1
    );

    sheet
      .getRange(
        ROW.SEARCH_DATA,
        1,
        hits.length,
        8
      )
      .setValues(
        hits
      );
  }

  ss.toast(
    `${hits.length}件表示（完全一致 ${exact.length}件）`,
    '名簿検索',
    5
  );
}


/* =========================================================
 * Spreadsheet取得
 * ========================================================= */

function getSpreadsheet_() {
  const id =
    PropertiesService
      .getScriptProperties()
      .getProperty(
        'SPREADSHEET_ID'
      );

  if (id) {
    return SpreadsheetApp
      .openById(id);
  }

  const ss =
    SpreadsheetApp
      .getActiveSpreadsheet();

  if (!ss) {
    throw new Error(
      'スプレッドシートIDが未設定です。初期設定を実行してください。'
    );
  }

  return ss;
}


function getSpreadsheetFallback_() {
  const id =
    PropertiesService
      .getScriptProperties()
      .getProperty(
        'SPREADSHEET_ID'
      );

  if (id) {
    return SpreadsheetApp
      .openById(id);
  }

  const ss =
    SpreadsheetApp
      .getActiveSpreadsheet();

  if (!ss) {
    throw new Error(
      'スプレッドシートを特定できません。'
    );
  }

  return ss;
}


function requireSheet_(
  ss,
  name
) {
  const sheet =
    ss.getSheetByName(
      name
    );

  if (!sheet) {
    throw new Error(
      `必要なシートがありません: ${name}`
    );
  }

  return sheet;
}


/* =========================================================
 * シート構造保証
 * ========================================================= */

function ensureStructure_(ss) {
  ensureMofaStructure_(ss);
  ensureTimezone_(ss);

  const audit =
    requireSheet_(
      ss,
      TAB.AUDIT
    );

  /*
   * A:Q = 17列。
   */
  ensureColumns_(
    audit,
    17
  );

  const expectedAuditHeaders = [
    'HTTP200',
    'HTTP304',
    '受信Bytes',
    'リトライ',
    'ロック競合スキップ',
    '新規changes',
    '保持changes',
  ];

  const auditHeaders =
    audit
      .getRange(
        'K4:Q4'
      )
      .getDisplayValues()[0];

  if (
    expectedAuditHeaders
      .some(
        (h, i) =>
          auditHeaders[i] !==
          h
      )
  ) {
    audit
      .getRange(
        'K4:Q4'
      )
      .setValues([
        expectedAuditHeaders,
      ]);
  }

  const queue =
    requireSheet_(
      ss,
      TAB.LATEST_DIFF
    );

  ensureColumns_(
    queue,
    8
  );

  queue
    .getRange('A1')
    .setValue(
      '未解消差分キュー'
    );

  queue
    .getRange('A2')
    .setValue(
      '全期間の未解消master差分を表示します。対応状況はこのシートで変更し、次回同期で「対応済」の行が消えます。'
    );

  queue
    .getRange('A4:H4')
    .setValues([[
      '検知日時',
      '出所',
      '種別',
      '対象/タイトル',
      '変更前/判定理由',
      '変更後/URL',
      '対応状況',
      'イベントキー',
    ]]);

  queue.hideColumns(8);

  ensureReReviewStructure_(
    ss
  );

  syncVersionSetting_(
    ss
  );
}



function ensureReReviewStructure_(
  ss
) {
  const settingsSheet =
    requireSheet_(
      ss,
      TAB.SETTINGS
    );

  /*
   * Re-review URL は固定設定。
   * 無ければ追加、空欄なら補完、別値なら正規値へ戻す。
   */
  const settingsLast =
    Math.max(
      settingsSheet.getLastRow(),
      ROW.SETTINGS_DATA
    );

  const settingsRows =
    settingsSheet
      .getRange(
        ROW.SETTINGS_DATA,
        1,
        settingsLast -
          ROW.SETTINGS_DATA +
          1,
        2
      )
      .getValues();

  let foundRow =
    0;

  settingsRows.forEach(
    (r, i) => {
      if (
        String(
          r[0] || ''
        ).trim() ===
        'Re-review URL'
      ) {
        foundRow =
          ROW.SETTINGS_DATA +
          i;
      }
    }
  );

  if (!foundRow) {
    foundRow =
      Math.max(
        settingsSheet.getLastRow() + 1,
        ROW.SETTINGS_DATA
      );

    ensureRows_(
      settingsSheet,
      foundRow
    );

    settingsSheet
      .getRange(
        foundRow,
        1,
        1,
        4
      )
      .setValues([
        [
          'Re-review URL',
          RE_REVIEW_URL,
          '再審査キュー',
          '固定',
        ],
      ]);

  } else {
    const current =
      String(
        settingsSheet
          .getRange(
            foundRow,
            2
          )
          .getDisplayValue() ||
        ''
      ).trim();

    if (
      current !==
      RE_REVIEW_URL
    ) {
      settingsSheet
        .getRange(
          foundRow,
          2
        )
        .setValue(
          RE_REVIEW_URL
        );
    }
  }

  let sheet =
    ss.getSheetByName(
      TAB.RE_REVIEW
    );

  if (!sheet) {
    sheet =
      ss.insertSheet(
        TAB.RE_REVIEW
      );
  }

  ensureColumns_(
    sheet,
    15
  );

  ensureRows_(
    sheet,
    100
  );

  sheet.setFrozenRows(
    4
  );

  sheet
    .getRange(
      'A1'
    )
    .setValue(
      '再審査分'
    )
    .setFontSize(
      16
    )
    .setFontWeight(
      'bold'
    );

  sheet
    .getRange(
      'A2'
    )
    .setValue(
      '一次ソースの情報改訂のうち、既存顧客・取引先の再スクリーニングが必要な対象だけを1対象者=1案件で管理します。'
    )
    .setWrap(
      true
    );

  const headers = [
    '再審査ID',
    '検知日時',
    '出所',
    '対象者',
    '番号',
    '国連参照番号',
    '優先度',
    '再審査理由',
    '変更項目',
    '変更概要',
    '原本',
    '一次ソースURL',
    '対応状況',
    '確認者/メモ',
    '対応日時',
  ];

  const currentHeaders =
    sheet
      .getRange(
        4,
        1,
        1,
        15
      )
      .getDisplayValues()[0];

  if (
    headers.some(
      (h, i) =>
        currentHeaders[i] !==
        h
    )
  ) {
    sheet
      .getRange(
        4,
        1,
        1,
        15
      )
      .setValues([
        headers,
      ]);
  }

  sheet
    .getRange(
      4,
      1,
      1,
      15
    )
    .setFontWeight(
      'bold'
    )
    .setBackground(
      '#E5E7EB'
    )
    .setWrap(
      true
    );

  const statusRule =
    SpreadsheetApp
      .newDataValidation()
      .requireValueInList(
        [
          '未対応',
          '対応中',
          '対応済',
          '保留',
        ],
        true
      )
      .setAllowInvalid(
        false
      )
      .build();

  sheet
    .getRange(
      ROW.DATA_START,
      13,
      sheet.getMaxRows() -
        ROW.DATA_START +
        1,
      1
    )
    .setDataValidation(
      statusRule
    );
}


function ensureRows_(
  sheet,
  neededLastRow
) {
  const maxRows =
    sheet.getMaxRows();

  if (
    neededLastRow <=
    maxRows
  ) {
    return;
  }

  const buffer =
    100;

  sheet.insertRowsAfter(
    maxRows,
    neededLastRow -
      maxRows +
      buffer
  );
}


function ensureColumns_(
  sheet,
  neededLastColumn
) {
  const maxColumns =
    sheet.getMaxColumns();

  if (
    neededLastColumn <=
    maxColumns
  ) {
    return;
  }

  sheet.insertColumnsAfter(
    maxColumns,
    neededLastColumn -
      maxColumns
  );
}


/* =========================================================
 * 設定取得
 * ========================================================= */

function readSettings_() {
  const ss =
    getSpreadsheetFallback_();

  const sheet =
    requireSheet_(
      ss,
      TAB.SETTINGS
    );

  const last =
    sheet.getLastRow();

  if (
    last <
    ROW.SETTINGS_DATA
  ) {
    return {};
  }

  const values =
    sheet
      .getRange(
        ROW.SETTINGS_DATA,
        1,
        last -
          ROW.SETTINGS_DATA +
          1,
        2
      )
      .getValues();

  const out = {};

  values.forEach(
    r => {
      const key =
        String(
          r[0] || ''
        ).trim();

      if (key) {
        out[key] =
          r[1];
      }
    }
  );

  return out;
}


/* =========================================================
 * GitHub同期
 * ========================================================= */

function fetchSyncPayload_(
  settings,
  metrics
) {
  const base =
    String(
      settings[
        'Heartbeat Base'
      ] || ''
    );

  if (!base) {
    throw new Error(
      'Heartbeat Base が未設定です。'
    );
  }

  const tz =
    String(
      settings[
        'Timezone'
      ] ||
      DEFAULT_TZ
    );

  const months =
    monthKeys_(
      new Date(),
      tz
    );

  const specs = [
    {key: 'mofa_documents', url: MOFA_DOCUMENTS_URL, expected: EXPECTED.mofaDocuments, optional: false, conditional: true},
    {
      key:
        'status',

      url:
        String(
          settings[
            'Status URL'
          ] || ''
        ),

      expected:
        EXPECTED.status,

      optional:
        false,

      /*
       * statusは小さいため
       * 毎回200取得。
       */
      conditional:
        false,
    },

    {
      key:
        'changes',

      url:
        String(
          settings[
            'Changes URL'
          ] || ''
        ),

      expected:
        EXPECTED.changes,

      optional:
        false,

      /*
       * 最大5000行なので
       * ETag / Last-Modified使用。
       */
      conditional:
        true,
    },

    {
      key:
        're_review',

      url:
        String(
          settings[
            'Re-review URL'
          ] ||
          RE_REVIEW_URL
        ),

      expected:
        EXPECTED.reReview,

      optional:
        false,

      conditional:
        true,
    },
  ];

  months.forEach(
    month => {
      specs.push({
        key:
          `heartbeat_${month}`,

        url:
          `${base}${month}.csv`,

        expected:
          EXPECTED.heartbeat,

        optional:
          true,

        conditional:
          true,
      });
    }
  );

  const result =
    fetchCsvBatch_(
      specs,
      metrics
    );

  let heartbeat =
    [];

  months.forEach(
    month => {
      const item =
        result[
          `heartbeat_${month}`
        ];

      if (
        item &&
        item.rows.length
      ) {
        heartbeat =
          heartbeat.concat(
            item.rows
          );
      }
    }
  );

  return {
    mofaDocuments: result.mofa_documents,

    status:
      result.status,

    changes:
      result.changes,

    reReview:
      result.re_review,

    heartbeat,
  };
}


/* =========================================================
 * 手動CSV取得
 * ========================================================= */

function fetchCsvNow_(
  url,
  expectedHeaders
) {
  const metrics = {
    http200: 0,
    http304: 0,
    bytes: 0,
    retries: 0,
  };

  const result =
    fetchCsvBatch_(
      [
        {
          key:
            'manual',

          url:
            String(
              url || ''
            ),

          expected:
            expectedHeaders,

          optional:
            false,

          conditional:
            false,
        },
      ],

      metrics
    );

  return result.manual;
}


/* =========================================================
 * HTTP並列取得
 * ========================================================= */

function fetchCsvBatch_(
  specs,
  metrics
) {
  const props =
    PropertiesService
      .getScriptProperties();

  specs.forEach(
    spec =>
      validateFetchSpec_(
        spec
      )
  );

  const requests =
    specs.map(
      spec =>
        buildFetchRequest_(
          spec,
          props
        )
    );

  const responses =
    UrlFetchApp.fetchAll(
      requests
    );

  const out =
    {};

  specs.forEach(
    (spec, i) => {
      let response =
        responses[i];

      let code =
        response
          .getResponseCode();

      if (
        isRetryableHttpCode_(
          code
        )
      ) {
        response =
          retryFetch_(
            spec,
            props,
            response,
            metrics
          );

        code =
          response
            .getResponseCode();
      }

      out[spec.key] =
        parseCsvResponse_(
          spec,
          response,
          props,
          metrics
        );
    }
  );

  return out;
}


function validateFetchSpec_(
  spec
) {
  if (
    !spec.url
  ) {
    throw new Error(
      `URL未設定: ${spec.key}`
    );
  }

  if (
    !/^https:\/\//i.test(
      spec.url
    )
  ) {
    throw new Error(
      `HTTPS以外のURLは許可しません: ${spec.url}`
    );
  }
}


function buildFetchOptions_(
  spec,
  props
) {
  const headers = {
    Accept:
      'text/csv,text/plain;q=0.9,*/*;q=0.1',
  };

  if (
    spec.conditional
  ) {
    const etag =
      props.getProperty(
        `HTTP_ETAG_${spec.key}`
      );

    const modified =
      props.getProperty(
        `HTTP_MODIFIED_${spec.key}`
      );

    if (etag) {
      headers[
        'If-None-Match'
      ] = etag;
    }

    if (modified) {
      headers[
        'If-Modified-Since'
      ] = modified;
    }
  }

  return {
    method:
      'get',

    muteHttpExceptions:
      true,

    followRedirects:
      true,

    headers,
  };
}


function buildFetchRequest_(
  spec,
  props
) {
  return Object.assign(
    {
      url:
        spec.url,
    },

    buildFetchOptions_(
      spec,
      props
    )
  );
}


/* =========================================================
 * HTTP Retry
 * ========================================================= */

function retryFetch_(
  spec,
  props,
  firstResponse,
  metrics
) {
  let response =
    firstResponse;

  for (
    let attempt = 1;
    attempt <=
      MAX_RETRIES;
    attempt++
  ) {
    const code =
      response
        .getResponseCode();

    if (
      !isRetryableHttpCode_(
        code
      )
    ) {
      return response;
    }

    metrics.retries =
      Number(
        metrics.retries ||
        0
      ) + 1;

    const waitMs =
      retryDelayMs_(
        response,
        attempt
      );

    Utilities.sleep(
      waitMs
    );

    response =
      UrlFetchApp.fetch(
        spec.url,
        buildFetchOptions_(
          spec,
          props
        )
      );
  }

  return response;
}


/* =========================================================
 * CSVレスポンス
 * ========================================================= */

function parseCsvResponse_(
  spec,
  response,
  props,
  metrics
) {
  const code =
    response
      .getResponseCode();

  /*
   * 内容変更なし。
   */
  if (
    code === 304
  ) {
    metrics.http304 +=
      1;

    return {
      header:
        spec.expected.slice(),

      rows:
        [],

      notModified:
        true,

      code,
    };
  }

  /*
   * heartbeatの存在しない月は許容。
   */
  if (
    code === 404 &&
    spec.optional
  ) {
    return {
      header:
        spec.expected.slice(),

      rows:
        [],

      notModified:
        true,

      code,
    };
  }

  if (
    code !== 200
  ) {
    throw new Error(
      `HTTP ${code}: ${spec.url}`
    );
  }

  metrics.http200 +=
    1;

  const bytes =
    response
      .getBlob()
      .getBytes();

  metrics.bytes +=
    bytes.length;

  const text =
    response
      .getContentText(
        'UTF-8'
      );

  const rows =
    Utilities.parseCsv(
      text
    );

  if (
    !rows.length
  ) {
    throw new Error(
      `CSV空: ${spec.url}`
    );
  }

  const header =
    rows[0].map(
      v =>
        String(v)
          .replace(
            /^\uFEFF/,
            ''
          )
    );

  validateHeaders_(
    header,
    spec.expected,
    spec.url
  );

  if (spec.key !== 'mofa_documents') {
    saveHttpMeta_(spec, response, props);
  }

  return {
    mofaHttpMeta: spec.key === 'mofa_documents' ? response.getAllHeaders() : null,
    header,

    rows:
      rows
        .slice(1)
        .filter(
          r =>
            r.some(
              v =>
                String(v)
                  .trim() !==
                ''
            )
        ),

    notModified:
      false,

    code,
  };
}


/* =========================================================
 * ETag / Last-Modified
 * ========================================================= */

function saveHttpMeta_(
  spec,
  response,
  props
) {
  if (
    !spec.conditional
  ) {
    return;
  }

  const headers =
    response
      .getAllHeaders();

  const etag =
    getHeaderIgnoreCase_(
      headers,
      'etag'
    );

  const modified =
    getHeaderIgnoreCase_(
      headers,
      'last-modified'
    );

  const updates =
    {};

  if (etag) {
    updates[
      `HTTP_ETAG_${spec.key}`
    ] = etag;
  }

  if (modified) {
    updates[
      `HTTP_MODIFIED_${spec.key}`
    ] = modified;
  }

  if (
    Object.keys(
      updates
    ).length
  ) {
    props.setProperties(
      updates,
      false
    );
  }
}


function getHeaderIgnoreCase_(
  headers,
  name
) {
  const target =
    String(name)
      .toLowerCase();

  for (
    const key in headers
  ) {
    if (
      String(key)
        .toLowerCase() ===
      target
    ) {
      return String(
        headers[key] ||
        ''
      );
    }
  }

  return '';
}


/* =========================================================
 * CSV Schema
 * ========================================================= */

function validateHeaders_(
  actual,
  expected,
  source
) {
  if (
    actual.length !==
      expected.length ||

    expected.some(
      (h, i) =>
        String(
          actual[i]
        ) !==
        String(h)
    )
  ) {
    throw new Error(
      `スキーマ不一致: ${source}\n` +
      `expected=${expected.join('|')}\n` +
      `actual=${actual.join('|')}`
    );
  }
}


/* =========================================================
 * heartbeat対象月
 * ========================================================= */

function monthKeys_(
  now,
  tz
) {
  const current =
    Utilities.formatDate(
      now,
      tz,
      'yyyy-MM'
    );

  const y =
    Number(
      Utilities.formatDate(
        now,
        tz,
        'yyyy'
      )
    );

  const m =
    Number(
      Utilities.formatDate(
        now,
        tz,
        'M'
      )
    );

  const prevDate =
    new Date(
      Date.UTC(
        y,
        m - 2,
        15,
        12,
        0,
        0
      )
    );

  const previous =
    Utilities.formatDate(
      prevDate,
      tz,
      'yyyy-MM'
    );

  return [
    ...new Set([
      previous,
      current,
    ]),
  ];
}


/* =========================================================
 * ダッシュボード更新
 * ========================================================= */

function updateDashboard_(
  ss,
  statusRows,
  settings
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.DASH
    );

  const order = [
    '財務省',
    '経済産業省',
    'OFAC SDN',
    'OFAC Consolidated',
    '外務省（現行リスト）',
    '外務省（報道発表）',
  ];

  const bySource =
    {};

  statusRows.forEach(
    r => {
      bySource[
        String(r[0])
      ] = r;
    }
  );

  const healthRows =
    order.map(
      source => {
        const r =
          bySource[source] ||
          [
            source,
            '未確認',
            '',
            '',
            '',
            '',
          ];

        const health =
          freshness_(
            source,
            r[1],
            r[2],
            settings
          );

        const note =
          health.note ||
          (
            source ===
              '経済産業省' &&
            r[1] ===
              '自動取得不可'
              ? 'WAF等により自動取得不可。手動確認対象'
              : ''
          );

        return {
          source,

          state:
            r[1] ||
            '未確認',

          lastCheck:
            r[2] ||
            '',

          sourceUpdated:
            r[3] ||
            '',

          count:
            r[4] ||
            '',

          hash:
            r[5] ||
            '',

          freshness:
            health.label,

          note,

          severity:
            health.severity,

          anomalyType:
            health.anomalyType,
        };
      }
    );

  sheet
    .getRange(
      9,
      1,
      healthRows.length,
      8
    )
    .setValues(
      healthRows.map(
        x => [
          x.source,
          x.state,
          x.lastCheck,
          x.sourceUpdated,
          x.count,
          x.hash,
          x.freshness,
          x.note,
        ]
      )
    );

  const hasCritical =
    healthRows.some(
      x =>
        x.severity ===
        3
    );

  const hasWarning =
    healthRows.some(
      x =>
        x.severity ===
          2 ||
        x.severity ===
          1
    );

  const overall =
    hasCritical
      ? '異常'
      : hasWarning
        ? '要確認'
        : '正常';

  const normalCount =
    healthRows.filter(
      x =>
        x.severity ===
        0
    ).length;

  sheet
    .getRange(
      'A5:F5'
    )
    .setValues([
      [
        overall,

        formatJst_(
          new Date()
        ),

        '',

        order.length,

        normalCount,

        order.length -
          normalCount,
      ],
    ]);

  return healthRows;
}


/* =========================================================
 * 直近変更
 * ========================================================= */

function refreshRecentChanges_(ss) {
  const source =
    requireSheet_(
      ss,
      TAB.CHANGES
    );

  const dash =
    requireSheet_(
      ss,
      TAB.DASH
    );

  const last =
    source.getLastRow();

  dash
    .getRange(
      'A16:F19'
    )
    .clearContent();

  if (
    last <
    ROW.DATA_START
  ) {
    return;
  }

  const count =
    Math.min(
      4,
      last -
        ROW.DATA_START +
        1
    );

  const values =
    source
      .getRange(
        ROW.DATA_START,
        1,
        count,
        6
      )
      .getValues();

  dash
    .getRange(
      16,
      1,
      count,
      6
    )
    .setValues(
      values
    );
}


/* =========================================================
 * 制裁監視鮮度
 * ========================================================= */

function freshness_(
  source,
  state,
  lastCheckText,
  settings
) {
  if (['エラー', '構造異常', '未確認期間あり', '手動取込エラー'].includes(state)) {
    return {
      label:
        '重大',

      severity:
        3,

      anomalyType:
        '取得エラー',

      note:
        state === '手動取込エラー' ? '手動取込が失敗。原本・理由を確認' : 'GitHub側で取得エラーまたは未確認期間あり',
    };
  }

  if (source.indexOf('外務省') === 0) {
    const checked = state === '手動確認済み' && !!lastCheckText;
    return {
      label: checked ? '手動確認' : '要確認',
      severity: 1,
      anomalyType: '手動監視',
      note: checked ? '最終手動確認: ' + lastCheckText : state + '。公式サイトをブラウザで確認',
    };
  }

  if (
    state ===
    '自動取得不可'
  ) {
    return {
      label:
        '要確認',

      severity:
        1,

      anomalyType:
        '自動取得不可',

      note:
        '手動確認対象',
    };
  }

  if (
    !lastCheckText
  ) {
    return {
      label:
        '重大',

      severity:
        3,

      anomalyType:
        '監視未確認',

      note:
        '最終チェック時刻なし',
    };
  }

  let warn = 0;
  let critical = 0;

  if (
    source.indexOf(
      'OFAC'
    ) === 0
  ) {
    warn =
      Number(
        settings[
          'OFAC警告(分)'
        ] || 90
      );

    critical =
      Number(
        settings[
          'OFAC重大(分)'
        ] || 150
      );

  } else if (
    source ===
    '財務省'
  ) {
    warn =
      Number(
        settings[
          'MOF警告(分)'
        ] || 420
      );

    critical =
      Number(
        settings[
          'MOF重大(分)'
        ] || 600
      );

  } else {
    return {
      label:
        '正常',

      severity:
        0,

      anomalyType:
        '',

      note:
        '',
    };
  }

  const d =
    parseJst_(
      lastCheckText
    );

  if (!d) {
    return {
      label:
        '重大',

      severity:
        3,

      anomalyType:
        '日時解析エラー',

      note:
        `日時を解析できません: ${lastCheckText}`,
    };
  }

  const age =
    (
      Date.now() -
      d.getTime()
    ) /
    60000;

  if (
    age >
    critical
  ) {
    return {
      label:
        '重大',

      severity:
        3,

      anomalyType:
        '監視遅延',

      note:
        `最終チェックから約${Math.floor(age)}分`,
    };
  }

  if (
    age >
    warn
  ) {
    return {
      label:
        '警告',

      severity:
        2,

      anomalyType:
        '監視遅延',

      note:
        `最終チェックから約${Math.floor(age)}分`,
    };
  }

  return {
    label:
      '正常',

    severity:
      0,

    anomalyType:
      '',

    note:
      '',
  };
}


/* =========================================================
 * heartbeat履歴
 * ========================================================= */

function appendHeartbeatHistory_(
  ss,
  heartbeatRows
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.HISTORY
    );

  const existing =
    existingKeys_(
      sheet,
      9
    );

  const now =
    formatJst_(
      new Date()
    );

  const parsed =
    heartbeatRows
      .map(
        r => {
          const source =
            SOURCE_LABEL[
              r[1]
            ] ||
            r[1];

          const status =
            STATUS_LABEL[
              r[2]
            ] ||
            r[2];

          const key = [
            r[0],
            r[1],
            r[2],
            r[3],
          ].join('|');

          return {
            checkedAt:
              r[0],

            key,

            row: [
              now,

              source,

              status,

              isoUtcToJst_(
                r[0]
              ),

              sourceUpdatedDisplay_(
                r[4]
              ),

              r[5] ||
                '',

              String(
                r[3] || ''
              ).slice(
                0,
                12
              ),

              'GitHub heartbeat',

              key,
            ],
          };
        }
      )
      .filter(
        x =>
          !existing.has(
            x.key
          )
      );

  parsed.sort(
    (a, b) =>
      String(
        a.checkedAt
      ).localeCompare(
        String(
          b.checkedAt
        )
      )
  );

  if (
    !parsed.length
  ) {
    return;
  }

  const start =
    Math.max(
      sheet.getLastRow() +
        1,

      ROW.DATA_START
    );

  ensureRows_(
    sheet,
    start +
      parsed.length -
      1
  );

  sheet
    .getRange(
      start,
      1,
      parsed.length,
      9
    )
    .setValues(
      parsed.map(
        x =>
          x.row
      )
    );
}


/* =========================================================
 * changes同期
 * ========================================================= */

function syncChangesMirror_(
  ss,
  changeRows,
  settings
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.CHANGES
    );

  const keep =
    Math.max(
      100,
      Number(
        settings[
          'Changes保持件数'
        ] || 5000
      )
    );

  const last =
    sheet.getLastRow();

  const existingRows =
    last >=
      ROW.DATA_START
      ? sheet
          .getRange(
            ROW.DATA_START,
            1,
            last -
              ROW.DATA_START +
              1,
            9
          )
          .getValues()

      : [];

  const existingKeys =
    new Set();

  const byKey =
    new Map();

  /*
   * 既存履歴と人間レビュー情報を保持。
   */
  existingRows.forEach(
    (r, index) => {
      const key =
        String(
          r[8] ||
          buildChangeKey_(
            r.slice(
              0,
              6
            )
          )
        );

      if (
        !key ||
        byKey.has(
          key
        )
      ) {
        return;
      }

      existingKeys.add(
        key
      );

      byKey.set(
        key,
        {
          ts:
            r[0],

          order:
            index,

          row: [
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],

            r[6] ||
              '未確認',

            r[7] ||
              '',

            key,
          ],
        }
      );
    }
  );

  const incomingSeen =
    new Set();

  let newChanges =
    0;

  changeRows.forEach(
    (r, index) => {
      const base = normalizeLegacyOfacBackfillRow_([
        r[0] || '',
        r[1] || '',
        r[2] || '',
        r[3] || '',
        r[4] || '',
        r[5] || '',
      ]);

      const key =
        buildChangeKey_(
          base
        );

      if (
        !key ||
        incomingSeen.has(
          key
        )
      ) {
        return;
      }

      incomingSeen.add(
        key
      );

      const previous =
        byKey.get(
          key
        );

      if (
        !existingKeys.has(
          key
        )
      ) {
        newChanges +=
          1;
      }

      byKey.set(
        key,
        {
          ts:
            base[0],

          order:
            index,

          row: [
            base[0],
            base[1],
            base[2],
            base[3],
            base[4],
            base[5],

            previous
              ? previous.row[6]
              : '未確認',

            previous
              ? previous.row[7]
              : '',

            key,
          ],
        }
      );
    }
  );

  const merged =
    Array.from(
      byKey.values()
    );

  /*
   * SheetsでDate型へ変換済みでも
   * 正しい時系列順にする。
   */
  merged.sort(
    (a, b) => {
      const byTime =
        changeTimestampMs_(
          b.ts
        ) -
        changeTimestampMs_(
          a.ts
        );

      if (
        byTime !== 0
      ) {
        return byTime;
      }

      return (
        a.order -
        b.order
      );
    }
  );

  const finalRows =
    merged
      .slice(
        0,
        keep
      )
      .map(
        x =>
          x.row
      );

  const previousCount =
    existingRows.length;

  /*
   * まとめて1回で書き込み。
   */
  if (
    finalRows.length
  ) {
    ensureRows_(
      sheet,
      ROW.DATA_START +
        finalRows.length -
        1
    );

    sheet
      .getRange(
        ROW.DATA_START,
        1,
        finalRows.length,
        9
      )
      .setValues(
        finalRows
      );
  }

  /*
   * 前回より件数が減った場合だけ
   * 下側をクリア。
   */
  if (
    previousCount >
    finalRows.length
  ) {
    sheet
      .getRange(
        ROW.DATA_START +
          finalRows.length,
        1,
        previousCount -
          finalRows.length,
        9
      )
      .clearContent();
  }

  return {
    newChanges,

    retainedChanges:
      finalRows.length,
  };
}


/* =========================================================
 * 未解消差分キュー
 * ========================================================= */

function normalizeLegacyOfacBackfillRow_(row) {
  const out = row.slice();
  const legacyStamp =
    Date.parse('2026-09-04T10:26:50+09:00');
  const displayedStamp = String(out[0] || '').trim();
  const matchesLegacyStamp =
    /^2026[-/]09[-/]04[ T]10:26:50$/.test(displayedStamp) ||
    changeTimestampMs_(out[0]) === legacyStamp;

  if (
    !matchesLegacyStamp ||
    String(out[1]).trim() !== 'OFAC' ||
    ![
      '追加',
      '初回同期（Advanced XML）',
    ].includes(String(out[2]).trim())
  ) {
    return out;
  }

  out[2] = '初回同期（Advanced XML）';

  if (out.length >= 9) {
    const oldPrefix =
      '2026-09-04 10:26:50|OFAC|追加|';
    const key = String(out[8] || '');

    if (key.startsWith(oldPrefix)) {
      out[8] =
        '2026-09-04 10:26:50|OFAC|初回同期（Advanced XML）|' +
        key.slice(oldPrefix.length);
    } else {
      const base = out.slice(0, 6);
      base[0] = '2026-09-04 10:26:50';
      out[8] = buildChangeKey_(base);
    }
  }

  return out;
}


function reclassifyLegacyOfacBackfillHistory_(ss) {
  const sheet = requireSheet_(ss, TAB.CHANGES);
  const last = sheet.getLastRow();
  if (last < ROW.DATA_START) return 0;

  const count = last - ROW.DATA_START + 1;
  const range = sheet.getRange(
    ROW.DATA_START, 1, count, 9
  );
  const rows = range.getValues();
  let changed = 0;

  rows.forEach((row, index) => {
    const normalized = normalizeLegacyOfacBackfillRow_(row);
    if (
      normalized[2] === row[2] &&
      normalized[8] === row[8]
    ) return;
    rows[index] = normalized;
    changed++;
  });

  if (changed) {
    sheet.getRange(ROW.DATA_START, 3, count, 1)
      .setValues(rows.map(row => [row[2]]));
    sheet.getRange(ROW.DATA_START, 9, count, 1)
      .setValues(rows.map(row => [row[8]]));
  }
  return changed;
}


function normalizeQueueStatus_(value) {
  const status =
    String(
      value || ''
    ).trim();

  return status ||
    '未確認';
}


function isResolvedQueueStatus_(value) {
  const status =
    String(
      value || ''
    )
      .replace(
        /\s+/g,
        ''
      )
      .trim();

  return RESOLVED_QUEUE_STATUSES.has(
    status
  );
}


function isQueueEligibleChangeType_(value) {
  return QUEUE_CHANGE_TYPES.has(
    String(
      value || ''
    ).trim()
  );
}


function queueEventKey_(
  row,
  keyIndex
) {
  const explicit =
    String(
      row[keyIndex] || ''
    ).trim();

  if (explicit) {
    return explicit;
  }

  const base =
    row.slice(
      0,
      6
    );

  const hasData =
    base.some(
      value =>
        String(
          value == null
            ? ''
            : value
        ).trim() !== ''
    );

  return hasData
    ? buildChangeKey_(base)
    : '';
}


function reconcileUnresolvedQueueRows_(
  historyRows,
  queueRows
) {
  const statusByKey =
    new Map();

  (queueRows || [])
    .forEach(
      row => {
        const key =
          queueEventKey_(
            row,
            7
          );

        if (!key) return;

        statusByKey.set(
          key,
          normalizeQueueStatus_(
            row[6]
          )
        );
      }
    );

  const updatedHistoryRows =
    (historyRows || [])
      .map(
        row => {
          const out =
            row
              .slice(
                0,
                9
              );

          while (
            out.length < 9
          ) {
            out.push('');
          }

          const key =
            queueEventKey_(
              out,
              8
            );

          out[6] =
            statusByKey.has(
              key
            )
              ? statusByKey.get(
                  key
                )
              : normalizeQueueStatus_(
                  out[6]
                );

          out[8] =
            key;

          return out;
        }
      );

  const seen =
    new Set();

  const queueCandidates = [];

  updatedHistoryRows
    .forEach(
      (row, index) => {
        const key =
          String(
            row[8] || ''
          );

        if (
          !key ||
          seen.has(key) ||
          !isQueueEligibleChangeType_(
            row[2]
          ) ||
          isResolvedQueueStatus_(
            row[6]
          )
        ) {
          return;
        }

        seen.add(key);

        queueCandidates.push({
          timestamp:
            row[0],

          order:
            index,

          row: [
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            normalizeQueueStatus_(
              row[6]
            ),
            key,
          ],
        });
      }
    );

  queueCandidates.sort(
    (a, b) => {
      const byTime =
        changeTimestampMs_(
          b.timestamp
        ) -
        changeTimestampMs_(
          a.timestamp
        );

      return byTime ||
        a.order -
          b.order;
    }
  );

  return {
    historyRows:
      updatedHistoryRows,

    queueRows:
      queueCandidates.map(
        item =>
          item.row
      ),
  };
}


function syncQueueStatusesToHistory_(ss) {
  const history =
    requireSheet_(
      ss,
      TAB.CHANGES
    );

  const queue =
    requireSheet_(
      ss,
      TAB.LATEST_DIFF
    );

  const historyLast =
    history.getLastRow();

  if (
    historyLast <
      ROW.DATA_START
  ) {
    return;
  }

  const historyRows =
    history
      .getRange(
        ROW.DATA_START,
        1,
        historyLast -
          ROW.DATA_START +
          1,
        9
      )
      .getValues();

  const queueLast =
    queue.getLastRow();

  const queueRows =
    queueLast >=
      ROW.DATA_START
      ? queue
          .getRange(
            ROW.DATA_START,
            1,
            queueLast -
              ROW.DATA_START +
              1,
            8
          )
          .getValues()
      : [];

  const result =
    reconcileUnresolvedQueueRows_(
      historyRows,
      queueRows
    );

  history
    .getRange(
      ROW.DATA_START,
      7,
      result.historyRows.length,
      1
    )
    .setValues(
      result.historyRows.map(
        row => [
          row[6],
        ]
      )
    );

  history
    .getRange(
      ROW.DATA_START,
      9,
      result.historyRows.length,
      1
    )
    .setValues(
      result.historyRows.map(
        row => [
          row[8],
        ]
      )
    );
}


function refreshUnresolvedChangesQueue_(ss) {
  const history =
    requireSheet_(
      ss,
      TAB.CHANGES
    );

  const queue =
    requireSheet_(
      ss,
      TAB.LATEST_DIFF
    );

  const historyLast =
    history.getLastRow();

  const historyRows =
    historyLast >=
      ROW.DATA_START
      ? history
          .getRange(
            ROW.DATA_START,
            1,
            historyLast -
              ROW.DATA_START +
              1,
            9
          )
          .getValues()
      : [];

  const result =
    reconcileUnresolvedQueueRows_(
      historyRows,
      []
    );

  const previousRows =
    Math.max(
      1,
      queue.getLastRow() -
        ROW.DATA_START +
        1
    );

  queue
    .getRange(
      ROW.DATA_START,
      1,
      previousRows,
      8
    )
    .clearContent();

  queue
    .getRange(
      ROW.DATA_START,
      7,
      previousRows,
      1
    )
    .clearDataValidations();

  if (
    result.queueRows.length
  ) {
    ensureRows_(
      queue,
      ROW.DATA_START +
        result.queueRows.length -
        1
    );

    queue
      .getRange(
        ROW.DATA_START,
        1,
        result.queueRows.length,
        8
      )
      .setValues(
        result.queueRows
      );

    queue
      .getRange(
        ROW.DATA_START,
        1,
        result.queueRows.length,
        1
      )
      .setNumberFormat(
        'yyyy/mm/dd hh:mm:ss'
      );

    const validation =
      SpreadsheetApp
        .newDataValidation()
        .requireValueInList(
          QUEUE_STATUS_OPTIONS,
          true
        )
        .setAllowInvalid(
          true
        )
        .build();

    queue
      .getRange(
        ROW.DATA_START,
        7,
        result.queueRows.length,
        1
      )
      .setDataValidation(
        validation
      );
  }

  queue.hideColumns(8);
}


/* =========================================================
 * 互換用
 * ========================================================= */

function clearRowsAfterKeep_(
  sheet,
  keep,
  columns
) {
  const last =
    sheet.getLastRow();

  const dataRows =
    Math.max(
      0,
      last -
        ROW.DATA_START +
        1
    );

  if (
    dataRows <=
    keep
  ) {
    return;
  }

  const excess =
    dataRows -
    keep;

  sheet
    .getRange(
      ROW.DATA_START +
        keep,
      1,
      excess,
      columns
    )
    .clearContent();
}


/* =========================================================
 * 既存キー取得
 * ========================================================= */

function existingKeys_(
  sheet,
  keyColumn
) {
  const last =
    sheet.getLastRow();

  if (
    last <
    ROW.DATA_START
  ) {
    return new Set();
  }

  return new Set(
    sheet
      .getRange(
        ROW.DATA_START,
        keyColumn,
        last -
          ROW.DATA_START +
          1,
        1
      )
      .getDisplayValues()
      .flat()
      .filter(
        Boolean
      )
  );
}



/* =========================================================
 * 再審査キュー同期
 * ========================================================= */

function syncReReviewMirror_(
  ss,
  incomingRows
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.RE_REVIEW
    );

  const last =
    sheet.getLastRow();

  const existingRows =
    last >=
      ROW.DATA_START
      ? sheet
          .getRange(
            ROW.DATA_START,
            1,
            last -
              ROW.DATA_START +
              1,
            15
          )
          .getValues()
      : [];

  const byId =
    new Map();

  existingRows.forEach(
    (r, index) => {
      const caseId =
        String(
          r[0] || ''
        ).trim();

      if (
        !caseId ||
        byId.has(
          caseId
        )
      ) {
        return;
      }

      byId.set(
        caseId,
        {
          order:
            index,

          row: [
            r[0], r[1], r[2], r[3], r[4],
            r[5], r[6], r[7], r[8], r[9],
            r[10], r[11],
            r[12] || '未対応',
            r[13] || '',
            r[14] || '',
          ],
        }
      );
    }
  );

  incomingRows.forEach(
    (r, index) => {
      const base =
        r
          .slice(
            0,
            12
          )
          .map(
            v =>
              v == null
                ? ''
                : v
          );

      const caseId =
        String(
          base[0] || ''
        ).trim();

      if (!caseId) {
        throw new Error(
          're_review.csvに再審査ID空欄があります。'
        );
      }

      const previous =
        byId.get(
          caseId
        );

      byId.set(
        caseId,
        {
          order:
            index,

          row: [
            ...base,
            previous
              ? previous.row[12] || '未対応'
              : '未対応',
            previous
              ? previous.row[13] || ''
              : '',
            previous
              ? previous.row[14] || ''
              : '',
          ],
        }
      );
    }
  );

  const finalRows =
    Array.from(
      byId.values()
    );

  finalRows.sort(
    (a, b) => {
      const byTime =
        changeTimestampMs_(
          b.row[1]
        ) -
        changeTimestampMs_(
          a.row[1]
        );

      if (byTime !== 0) {
        return byTime;
      }

      return a.order - b.order;
    }
  );

  if (finalRows.length) {
    ensureRows_(
      sheet,
      ROW.DATA_START +
        finalRows.length -
        1
    );

    sheet
      .getRange(
        ROW.DATA_START,
        1,
        finalRows.length,
        15
      )
      .setValues(
        finalRows.map(
          x => x.row
        )
      );
  }

  if (
    existingRows.length >
    finalRows.length
  ) {
    sheet
      .getRange(
        ROW.DATA_START +
          finalRows.length,
        1,
        existingRows.length -
          finalRows.length,
        15
      )
      .clearContent();
  }

  refreshReReviewVisibility_(
    sheet
  );
}


function refreshReReviewVisibility_(
  sheet
) {
  const last =
    sheet.getLastRow();

  if (
    last <
    ROW.DATA_START
  ) {
    return;
  }

  const count =
    last -
    ROW.DATA_START +
    1;

  sheet.showRows(
    ROW.DATA_START,
    count
  );

  const statuses =
    sheet
      .getRange(
        ROW.DATA_START,
        13,
        count,
        1
      )
      .getDisplayValues()
      .flat();

  let start =
    null;

  for (
    let i = 0;
    i <= statuses.length;
    i++
  ) {
    const completed =
      i < statuses.length &&
      String(
        statuses[i] || ''
      ).trim() ===
      '対応済';

    if (
      completed &&
      start === null
    ) {
      start = i;
    }

    if (
      !completed &&
      start !== null
    ) {
      sheet.hideRows(
        ROW.DATA_START +
          start,
        i - start
      );

      start = null;
    }
  }
}


function onEdit(e) {
  if (
    !e ||
    !e.range
  ) {
    return;
  }

  const sheet =
    e.range.getSheet();

  if (
    sheet.getName() !==
    TAB.RE_REVIEW
  ) {
    return;
  }

  if (
    e.range.getColumn() !==
      13 ||
    e.range.getNumColumns() !==
      1 ||
    e.range.getRow() <
      ROW.DATA_START
  ) {
    return;
  }

  const statuses =
    e.range
      .getDisplayValues()
      .flat();

  statuses.forEach(
    (value, i) => {
      const row =
        e.range.getRow() + i;

      const status =
        String(
          value || ''
        ).trim();

      const completedAt =
        sheet.getRange(
          row,
          15
        );

      if (
        status ===
        '対応済'
      ) {
        if (
          !String(
            completedAt
              .getDisplayValue() ||
            ''
          ).trim()
        ) {
          completedAt.setValue(
            formatJst_(
              new Date()
            )
          );
        }
      } else {
        completedAt.clearContent();
      }
    }
  );

  SpreadsheetApp.flush();

  refreshReReviewVisibility_(
    sheet
  );
}


/* =========================================================
 * 異常履歴
 * ========================================================= */

function updateAnomalies_(
  ss,
  healthRows
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.ANOMALY
    );

  const now =
    formatJst_(
      new Date()
    );

  const last =
    sheet.getLastRow();

  const existingRows =
    last >=
      ROW.DATA_START
      ? sheet
          .getRange(
            ROW.DATA_START,
            1,
            last -
              ROW.DATA_START +
              1,
            9
          )
          .getValues()

      : [];

  const openByKey =
    {};

  existingRows.forEach(
    (r, i) => {
      const key =
        String(
          r[8] || ''
        );

      const resolved =
        String(
          r[6] || ''
        );

      if (
        key &&
        !resolved
      ) {
        openByKey[
          key
        ] =
          i +
          ROW.DATA_START;
      }
    }
  );

  const active =
    new Set();

  const add =
    [];

  healthRows.forEach(
    x => {
      if (
        !x.anomalyType
      ) {
        return;
      }

      const key =
        `${x.source}|${x.anomalyType}`;

      active.add(
        key
      );

      if (
        openByKey[
          key
        ]
      ) {
        return;
      }

      const severity =
        x.severity ===
          3
          ? '重大'
          : x.severity ===
              2
            ? '警告'
            : '注意';

      add.push([
        now,

        x.source,

        x.anomalyType,

        severity,

        x.note ||
          x.state,

        now,

        '',

        '',

        key,
      ]);
    }
  );

  if (
    add.length
  ) {
    const start =
      Math.max(
        sheet.getLastRow() +
          1,

        ROW.DATA_START
      );

    ensureRows_(
      sheet,
      start +
        add.length -
        1
    );

    sheet
      .getRange(
        start,
        1,
        add.length,
        9
      )
      .setValues(
        add
      );
  }

  /*
   * 今回activeでない未解消異常は解消。
   */
  Object.keys(
    openByKey
  ).forEach(
    key => {
      if (
        !active.has(
          key
        )
      ) {
        sheet
          .getRange(
            openByKey[
              key
            ],
            7
          )
          .setValue(
            now
          );
      }
    }
  );
}


/* =========================================================
 * 監査証跡
 * ========================================================= */

function appendAudit_(
  ss,
  info
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.AUDIT
    );

  const metrics =
    info.metrics ||
    {};

  const row = [
    info.runId,

    formatJst_(
      new Date()
    ),

    info.result,

    `${info.executionType || '不明'} / status / changes / heartbeat`,

    info.statusCount,

    info.changesCount,

    info.heartbeatCount,

    info.elapsed,

    VERSION,

    info.errorText
      ? String(
          info.errorText
        ).slice(
          0,
          5000
        )
      : '',

    Number(
      metrics.http200 ||
      0
    ),

    Number(
      metrics.http304 ||
      0
    ),

    Number(
      metrics.bytes ||
      0
    ),

    Number(
      metrics.retries ||
      0
    ),

    Number(
      metrics.lockSkips ||
      0
    ),

    Number(
      metrics.newChanges ||
      0
    ),

    Number(
      metrics.retainedChanges ||
      0
    ),
  ];

  ensureColumns_(
    sheet,
    17
  );

  const start =
    Math.max(
      sheet.getLastRow() +
        1,

      ROW.DATA_START
    );

  ensureRows_(
    sheet,
    start
  );

  sheet
    .getRange(
      start,
      1,
      1,
      17
    )
    .setValues([
      row,
    ]);
}


/* =========================================================
 * JST設定
 * ========================================================= */

function applyJstSettings() {
  const ss =
    getSpreadsheet_();

  ensureTimezone_(
    ss
  );

  syncVersionSetting_(
    ss
  );

  SpreadsheetApp
    .getUi()
    .alert(
      `スプレッドシートのTimezoneを${DEFAULT_TZ}へ設定しました。`
    );
}


/* =========================================================
 * セルフチェック
 * ========================================================= */

function runSelfCheck() {
  const ss =
    getSpreadsheet_();

  ensureStructure_(
    ss
  );

  const settings =
    readSettings_();

  const checks =
    [];

  /*
   * 必須シート存在確認。
   */
  Object.values(
    TAB
  ).forEach(
    name => {
      const exists =
        Boolean(
          ss.getSheetByName(
            name
          )
        );

      checks.push({
        name:
          `sheet:${name}`,

        ok:
          exists,

        detail:
          exists
            ? '存在'
            : '欠落',
      });
    }
  );

  /*
   * Timezone。
   */
  checks.push({
    name:
      'timezone',

    ok:
      ss.getSpreadsheetTimeZone() ===
      DEFAULT_TZ,

    detail:
      ss.getSpreadsheetTimeZone(),
  });

  /*
   * Version。
   */
  checks.push({
    name:
      'version',

    ok:
      String(
        settings[
          'Script Version'
        ] || ''
      ) ===
      VERSION,

    detail:
      String(
        settings[
          'Script Version'
        ] || ''
      ),
  });

  /*
   * URL。
   */
  [
    'Status URL',
    'Changes URL',
    'List URL',
    'Heartbeat Base',
    'Re-review URL',
  ].forEach(
    key => {
      const value =
        String(
          settings[
            key
          ] || ''
        );

      checks.push({
        name:
          key,

        ok:
          /^https:\/\//i
            .test(
              value
            ),

        detail:
          value
            ? 'HTTPS'
            : '未設定',
      });
    }
  );

  /*
   * 自動同期トリガー。
   */
  const trigger =
    getSyncTriggerState_();

  checks.push({
    name:
      '自動同期トリガー',

    ok:
      trigger.count ===
      1,

    detail:
      trigger.detail,
  });

  /*
   * 自動同期鮮度。
   *
   * トリガー作成直後の未実行は許容。
   */
  const autoHealth =
    evaluateAutoSyncHealth_(
      settings
    );

  checks.push({
    name:
      '自動同期鮮度',

    ok:
      autoHealth.severity ===
        0 ||
      autoHealth.anomalyType ===
        '自動同期未実行',

    detail:
      autoHealth.note ||
      autoHealth.label,
  });

  /*
   * changes保持件数。
   */
  const changesSheet =
    requireSheet_(
      ss,
      TAB.CHANGES
    );

  const keep =
    Math.max(
      100,
      Number(
        settings[
          'Changes保持件数'
        ] || 5000
      )
    );

  const changeCount =
    countChangeRows_(
      ss
    );

  checks.push({
    name:
      'changes保持件数',

    ok:
      changeCount <=
      keep,

    detail:
      `${changeCount}/${keep}`,
  });

  /*
   * イベントキー重複・空欄。
   */
  const last =
    changesSheet.getLastRow();

  let duplicateCount =
    0;

  let blankKeyCount =
    0;

  if (
    last >=
    ROW.DATA_START
  ) {
    const keys =
      changesSheet
        .getRange(
          ROW.DATA_START,
          9,
          last -
            ROW.DATA_START +
            1,
          1
        )
        .getDisplayValues()
        .flat();

    const seen =
      new Set();

    keys.forEach(
      key => {
        const k =
          String(
            key || ''
          );

        if (!k) {
          blankKeyCount +=
            1;

          return;
        }

        if (
          seen.has(
            k
          )
        ) {
          duplicateCount +=
            1;
        }

        seen.add(
          k
        );
      }
    );
  }

  checks.push({
    name:
      'changesイベントキー重複',

    ok:
      duplicateCount ===
      0,

    detail:
      String(
        duplicateCount
      ),
  });

  checks.push({
    name:
      'changesイベントキー空欄',

    ok:
      blankKeyCount ===
      0,

    detail:
      String(
        blankKeyCount
      ),
  });

  /*
   * Dashboardにも現在状態を反映。
   */
  updateAutoSyncDashboard_(
    ss,
    autoHealth
  );

  const failed =
    checks.filter(
      x =>
        !x.ok
    );

  const lines =
    checks.map(
      x =>
        `${x.ok ? 'PASS' : 'FAIL'}  ${x.name}: ${x.detail}`
    );

  const message =
    failed.length
      ? `SELF CHECK: FAIL\n\n${lines.join('\n')}`
      : `ALL CHECKS: PASS\n\n${lines.join('\n')}`;

  SpreadsheetApp
    .getUi()
    .alert(
      message
    );

  if (
    failed.length
  ) {
    throw new Error(
      `セルフチェック失敗: ${failed.map(x => x.name).join(', ')}`
    );
  }
}


/* =========================================================
 * Timezone
 * ========================================================= */

function ensureTimezone_(ss) {
  if (
    ss.getSpreadsheetTimeZone() !==
    DEFAULT_TZ
  ) {
    ss.setSpreadsheetTimeZone(
      DEFAULT_TZ
    );
  }
}


/* =========================================================
 * Version同期
 * ========================================================= */

function syncVersionSetting_(ss) {
  const sheet =
    requireSheet_(
      ss,
      TAB.SETTINGS
    );

  const last =
    sheet.getLastRow();

  if (
    last <
    ROW.SETTINGS_DATA
  ) {
    return;
  }

  const keys =
    sheet
      .getRange(
        ROW.SETTINGS_DATA,
        1,
        last -
          ROW.SETTINGS_DATA +
          1,
        1
      )
      .getDisplayValues()
      .flat();

  const index =
    keys.findIndex(
      v =>
        String(v)
          .trim() ===
        'Script Version'
    );

  if (
    index < 0
  ) {
    return;
  }

  const row =
    ROW.SETTINGS_DATA +
    index;

  if (
    String(
      sheet
        .getRange(
          row,
          2
        )
        .getDisplayValue()
    ) !==
    VERSION
  ) {
    sheet
      .getRange(
        row,
        2
      )
      .setValue(
        VERSION
      );
  }
}


/* =========================================================
 * Lock競合記録
 * ========================================================= */

function recordPendingLockSkip_() {
  const props =
    PropertiesService
      .getScriptProperties();

  const current =
    Number(
      props.getProperty(
        'PENDING_LOCK_SKIPS'
      ) || 0
    );

  props.setProperty(
    'PENDING_LOCK_SKIPS',
    String(
      current + 1
    )
  );

  props.setProperty(
    'LAST_LOCK_SKIP_AT',
    formatJst_(
      new Date()
    )
  );
}


function consumePendingLockSkips_() {
  const props =
    PropertiesService
      .getScriptProperties();

  const count =
    Number(
      props.getProperty(
        'PENDING_LOCK_SKIPS'
      ) || 0
    );

  if (
    count
  ) {
    props.deleteProperty(
      'PENDING_LOCK_SKIPS'
    );
  }

  return count;
}


/* =========================================================
 * HTTP Retry判定
 * ========================================================= */

function isRetryableHttpCode_(code) {
  return (
    code === 408 ||
    code === 429 ||
    code >= 500
  );
}


/* =========================================================
 * Retry Delay
 * ========================================================= */

function retryDelayMs_(
  response,
  attempt
) {
  const headers =
    response
      .getAllHeaders();

  const raw =
    getHeaderIgnoreCase_(
      headers,
      'retry-after'
    );

  if (raw) {
    /*
     * Retry-After: 秒。
     */
    const seconds =
      Number(raw);

    if (
      Number.isFinite(
        seconds
      ) &&
      seconds >=
        0
    ) {
      return Math.min(
        30000,
        Math.max(
          250,
          seconds *
            1000
        )
      );
    }

    /*
     * Retry-After: HTTP-date。
     */
    const when =
      new Date(
        raw
      );

    if (
      !isNaN(
        when.getTime()
      )
    ) {
      return Math.min(
        30000,
        Math.max(
          250,
          when.getTime() -
            Date.now()
        )
      );
    }
  }

  return (
    500 *
    Math.pow(
      2,
      attempt -
        1
    )
  );
}


/* =========================================================
 * changes timestamp
 * ========================================================= */

function changeTimestampMs_(
  value
) {
  if (
    value instanceof Date &&
    !isNaN(
      value.getTime()
    )
  ) {
    return value.getTime();
  }

  const s =
    String(
      value || ''
    ).trim();

  if (!s) {
    return 0;
  }

  const jst =
    parseJst_(
      s
    );

  if (jst) {
    return jst.getTime();
  }

  const d =
    new Date(
      s
    );

  return isNaN(
    d.getTime()
  )
    ? 0
    : d.getTime();
}


/* =========================================================
 * changes key
 * ========================================================= */

function buildChangeKey_(row) {
  return row
    .slice(
      0,
      6
    )
    .map(
      v =>
        String(
          v == null
            ? ''
            : v
        )
    )
    .join('|');
}


/* =========================================================
 * changes件数
 * ========================================================= */

function countChangeRows_(ss) {
  const sheet =
    requireSheet_(
      ss,
      TAB.CHANGES
    );

  const last =
    sheet.getLastRow();

  return Math.max(
    0,
    last -
      ROW.DATA_START +
      1
  );
}


/* =========================================================
 * Dashboard実行エラー
 * ========================================================= */

function markDashboardSyncError_(
  ss,
  errorText
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.DASH
    );

  sheet
    .getRange(
      'A5'
    )
    .setValue(
      '異常'
    );

  sheet
    .getRange(
      'B5'
    )
    .setValue(
      formatJst_(
        new Date()
      )
    );
}


/* =========================================================
 * Apps Script Error anomaly
 * ========================================================= */

function recordScriptErrorAnomaly_(
  ss,
  errorText
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.ANOMALY
    );

  const key =
    'Google Sheets同期|Apps Script実行エラー';

  const last =
    sheet.getLastRow();

  if (
    last >=
    ROW.DATA_START
  ) {
    const rows =
      sheet
        .getRange(
          ROW.DATA_START,
          1,
          last -
            ROW.DATA_START +
            1,
          9
        )
        .getValues();

    const open =
      rows.some(
        r =>
          String(
            r[8] || ''
          ) ===
            key &&
          !String(
            r[6] || ''
          )
      );

    if (
      open
    ) {
      return;
    }
  }

  const now =
    formatJst_(
      new Date()
    );

  const start =
    Math.max(
      sheet.getLastRow() +
        1,
      ROW.DATA_START
    );

  ensureRows_(
    sheet,
    start
  );

  sheet
    .getRange(
      start,
      1,
      1,
      9
    )
    .setValues([
      [
        now,

        'Google Sheets同期',

        'Apps Script実行エラー',

        '重大',

        String(
          errorText || ''
        )
          .split('\n')[0]
          .slice(
            0,
            1000
          ),

        now,

        '',

        '',

        key,
      ],
    ]);
}


/* =========================================================
 * Trigger状態
 * ========================================================= */

function getSyncTriggerState_() {
  const triggers =
    ScriptApp
      .getProjectTriggers()
      .filter(
        t =>
          [
            'scheduledSyncAll',
            'syncAll',
          ].includes(
            t.getHandlerFunction()
          )
      );

  const handlers =
    triggers.map(
      t =>
        t.getHandlerFunction()
    );

  let detail =
    '未設定';

  if (
    triggers.length ===
    1
  ) {
    detail =
      handlers[0] ===
        'scheduledSyncAll'
        ? '設定済み'
        : '設定済み（旧式syncAll。再作成推奨）';

  } else if (
    triggers.length >
    1
  ) {
    detail =
      `重複 ${triggers.length}件: ${handlers.join(', ')}`;
  }

  return {
    count:
      triggers.length,

    handlers,

    detail,

    exists:
      triggers.length >=
      1,

    duplicated:
      triggers.length >
      1,
  };
}


/* =========================================================
 * 自動同期死活監視
 * ========================================================= */

function evaluateAutoSyncHealth_(
  settings
) {
  const trigger =
    getSyncTriggerState_();

  const props =
    PropertiesService
      .getScriptProperties();

  const interval =
    normalizedSyncInterval_(
      settings
    );

  /*
   * 15分設定なら35分。
   */
  const staleMinutes =
    Math.max(
      30,
      interval *
        2 +
        5
    );

  const lastSuccess =
    String(
      props.getProperty(
        'LAST_AUTO_SUCCESS_AT'
      ) || ''
    );

  const lastAttempt =
    String(
      props.getProperty(
        'LAST_AUTO_ATTEMPT_AT'
      ) || ''
    );

  const lastFailure =
    String(
      props.getProperty(
        'LAST_AUTO_FAILURE_AT'
      ) || ''
    );

  const failureMessage =
    String(
      props.getProperty(
        'LAST_AUTO_FAILURE_MESSAGE'
      ) || ''
    );

  const parsedLastSuccess =
    lastSuccess
      ? parseJst_(
          lastSuccess
        )
      : null;

  const nextExpected =
    parsedLastSuccess
      ? formatJst_(
          new Date(
            parsedLastSuccess
              .getTime() +
            interval *
              60000
          )
        )
      : '';

  const base = {
    source:
      'Google Sheets自動同期',

    state:
      '',

    lastCheck:
      lastSuccess,

    sourceUpdated:
      '',

    count:
      '',

    hash:
      '',

    freshness:
      '',

    note:
      '',

    severity:
      0,

    anomalyType:
      '',

    trigger,

    lastSuccess,

    lastAttempt,

    lastFailure,

    nextExpected,

    interval,

    staleMinutes,
  };

  /*
   * トリガーなし。
   */
  if (
    !trigger.exists
  ) {
    return Object.assign(
      base,
      {
        state:
          'トリガー未設定',

        freshness:
          '要確認',

        note:
          '自動同期トリガーがありません。初期設定またはトリガー再作成を実行してください。',

        severity:
          2,

        anomalyType:
          '同期トリガー未設定',
      }
    );
  }

  /*
   * 重複。
   */
  if (
    trigger.duplicated
  ) {
    return Object.assign(
      base,
      {
        state:
          'トリガー重複',

        freshness:
          '要確認',

        note:
          trigger.detail,

        severity:
          2,

        anomalyType:
          '同期トリガー重複',
      }
    );
  }

  /*
   * 作成直後。
   */
  if (
    !lastSuccess
  ) {
    return Object.assign(
      base,
      {
        state:
          '未実行',

        freshness:
          '要確認',

        note:
          'トリガーは存在しますが、自動同期の成功履歴がまだありません。',

        severity:
          1,

        anomalyType:
          '自動同期未実行',
      }
    );
  }

  /*
   * 成功より新しい失敗がある。
   */
  if (
    lastFailure &&
    changeTimestampMs_(
      lastFailure
    ) >
    changeTimestampMs_(
      lastSuccess
    )
  ) {
    return Object.assign(
      base,
      {
        state:
          '直近失敗',

        freshness:
          '重大',

        note:
          `最終自動失敗: ${lastFailure}` +
          (
            failureMessage
              ? ` / ${failureMessage}`
              : ''
          ),

        severity:
          3,

        anomalyType:
          '自動同期失敗',
      }
    );
  }

  const successDate =
    parseJst_(
      lastSuccess
    );

  if (
    !successDate
  ) {
    return Object.assign(
      base,
      {
        state:
          '日時異常',

        freshness:
          '重大',

        note:
          `最終自動同期日時を解析できません: ${lastSuccess}`,

        severity:
          3,

        anomalyType:
          '自動同期日時解析エラー',
      }
    );
  }

  const ageMinutes =
    (
      Date.now() -
      successDate.getTime()
    ) /
    60000;

  /*
   * 自動同期停止・遅延。
   */
  if (
    ageMinutes >
    staleMinutes
  ) {
    return Object.assign(
      base,
      {
        state:
          '自動同期遅延',

        freshness:
          '重大',

        note:
          `最終成功から約${Math.floor(ageMinutes)}分。許容${staleMinutes}分を超過。`,

        severity:
          3,

        anomalyType:
          '自動同期遅延',
      }
    );
  }

  return Object.assign(
    base,
    {
      state:
        '正常',

      freshness:
        '正常',

      note:
        `最終成功 ${lastSuccess} / ${interval}分間隔`,

      severity:
        0,

      anomalyType:
        '',
    }
  );
}


/* =========================================================
 * 自動同期Dashboard表示
 * ========================================================= */

function updateAutoSyncDashboard_(
  ss,
  health
) {
  const sheet =
    requireSheet_(
      ss,
      TAB.DASH
    );

  const triggerText =
    health.trigger.exists
      ? (
          health.trigger.duplicated
            ? `重複${health.trigger.count}件`
            : '設定済み'
        )
      : '未設定';

  sheet
    .getRange(
      'A3:H3'
    )
    .setValues([
      [
        'Google Sheets自動同期',

        health.freshness ||
          '未確認',

        'トリガー',

        triggerText,

        '最終自動',

        health.lastSuccess ||
          '未実行',

        '次回目安',

        health.nextExpected ||
          '-',
      ],
    ]);

  sheet
    .getRange(
      'A3:H3'
    )
    .setBackground(
      '#F8FAFC'
    )
    .setFontColor(
      '#334155'
    )
    .setVerticalAlignment(
      'middle'
    );

  /*
   * v0.3.3 FIX
   *
   * getRange('A3,C3,E3,G3') は
   * 非連続範囲として無効。
   *
   * RangeListへ修正。
   */
  sheet
    .getRangeList([
      'A3',
      'C3',
      'E3',
      'G3',
    ])
    .setFontWeight(
      'bold'
    );

  sheet.setRowHeight(
    3,
    24
  );

  const statusColor =
    health.severity ===
      3
      ? '#FEE2E2'
      : health.severity >
          0
        ? '#FEF3C7'
        : '#DCFCE7';

  const statusFont =
    health.severity ===
      3
      ? '#991B1B'
      : health.severity >
          0
        ? '#92400E'
        : '#166534';

  sheet
    .getRange(
      'B3'
    )
    .setBackground(
      statusColor
    )
    .setFontColor(
      statusFont
    )
    .setFontWeight(
      'bold'
    );

  sheet
    .getRange(
      'D3'
    )
    .setBackground(
      health.trigger.exists &&
      !health.trigger.duplicated
        ? '#DCFCE7'
        : '#FEE2E2'
    )
    .setFontColor(
      health.trigger.exists &&
      !health.trigger.duplicated
        ? '#166534'
        : '#991B1B'
    )
    .setFontWeight(
      'bold'
    );

  /*
   * 制裁ソースの件数KPIは触らず、
   * 全体状態だけ自己監視結果を反映。
   */
  const currentOverall =
    String(
      sheet
        .getRange(
          'A5'
        )
        .getDisplayValue() ||
      '正常'
    );

  if (
    health.severity ===
    3
  ) {
    sheet
      .getRange(
        'A5'
      )
      .setValue(
        '異常'
      );

  } else if (
    health.severity >
      0 &&
    currentOverall ===
      '正常'
  ) {
    sheet
      .getRange(
        'A5'
      )
      .setValue(
        '要確認'
      );
  }
}


/* =========================================================
 * Sync interval
 * ========================================================= */

function normalizedSyncInterval_(
  settings
) {
  const requested =
    Number(
      settings[
        '同期間隔(分)'
      ] || 15
    );

  return [
    1,
    5,
    10,
    15,
    30,
  ].includes(
    requested
  )
    ? requested
    : 15;
}


/* =========================================================
 * Script error解消
 * ========================================================= */

function resolveScriptErrorAnomaly_(ss) {
  const sheet =
    requireSheet_(
      ss,
      TAB.ANOMALY
    );

  const key =
    'Google Sheets同期|Apps Script実行エラー';

  const last =
    sheet.getLastRow();

  if (
    last <
    ROW.DATA_START
  ) {
    return;
  }

  const rows =
    sheet
      .getRange(
        ROW.DATA_START,
        1,
        last -
          ROW.DATA_START +
          1,
        9
      )
      .getValues();

  const now =
    formatJst_(
      new Date()
    );

  rows.forEach(
    (r, i) => {
      if (
        String(
          r[8] || ''
        ) ===
          key &&
        !String(
          r[6] || ''
        )
      ) {
        sheet
          .getRange(
            ROW.DATA_START +
              i,
            7
          )
          .setValue(
            now
          );
      }
    }
  );
}


/* =========================================================
 * 名称正規化
 * ========================================================= */

function normalizeName_(value) {
  let s =
    String(
      value || ''
    )
      .normalize(
        'NFKC'
      )
      .replace(
        /[‘’]/g,
        "'"
      )
      .replace(
        /[“”]/g,
        '"'
      )
      .replace(
        /[‐-‒–—―ー－]/g,
        '-'
      )
      .replace(
        /[´`]/g,
        "'"
      )
      .replace(
        /\u3000/g,
        ' '
      )
      .replace(
        /[\u200B\uFEFF]/g,
        ''
      )
      .trim();

  s =
    swapSurnameFirst_(
      s
    );

  s =
    s.replace(
      /\s+/g,
      ''
    );

  const quotePairs = {
    '「': '」',
    '『': '』',
    '“': '”',
    '‘': '’',
    '«': '»',
    '"': '"',
    "'": "'",
    '＂': '＂',
    '`': '`',
  };

  while (
    s.length >=
      2 &&
    quotePairs[
      s[0]
    ] ===
      s[
        s.length -
        1
      ]
  ) {
    const inner =
      s
        .slice(
          1,
          -1
        )
        .trim();

    if (!inner) {
      break;
    }

    s =
      inner;
  }

  return s.toLowerCase();
}


/* =========================================================
 * OFAC surname-first
 * ========================================================= */

function swapSurnameFirst_(name) {
  const m =
    String(
      name || ''
    ).match(
      /^([^,]{2,60}),\s+([^,]{2,60})$/
    );

  if (!m) {
    return name;
  }

  const head =
    m[1].trim();

  const tail =
    m[2].trim();

  /*
   * 法人名称のカンマは
   * 姓名反転しない。
   */
  const orgSuffix =
    /^(?:LTD|LTD\.|INC|INC\.|LLC|L\.L\.C\.|CORP|CORP\.|CO|CO\.|PLC|GMBH|S\.A\.|S\.A\.S\.|S\.R\.L\.|SA|SARL|AG|NV|BV|AB|AS|OY|PTE|PTY|S\. DE R\.L\.|DE C\.V\.|LLP|LP|JSC|OAO|OOO|PAO|ZAO|A\.S\.|D\.O\.O\.)/i;

  if (
    orgSuffix.test(
      tail
    )
  ) {
    return name;
  }

  return (
    `${tail} ${head}`
  );
}


/* =========================================================
 * JST parse
 * ========================================================= */

function parseJst_(text) {
  const m =
    String(
      text || ''
    ).match(
      /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/
    );

  if (!m) {
    return null;
  }

  return new Date(
    `${m[1]}-${m[2]}-${m[3]}` +
    `T${m[4]}:${m[5]}:${m[6]}` +
    `+09:00`
  );
}


/* =========================================================
 * UTC -> JST
 * ========================================================= */

function isoUtcToJst_(text) {
  const d =
    new Date(
      String(
        text || ''
      )
    );

  if (
    isNaN(
      d.getTime()
    )
  ) {
    return String(
      text || ''
    );
  }

  return formatJst_(
    d
  );
}


/* =========================================================
 * source_updated表示
 * ========================================================= */

function sourceUpdatedDisplay_(value) {
  const s =
    String(
      value || ''
    );

  if (!s) {
    return '';
  }

  const d =
    new Date(
      s
    );

  if (
    !isNaN(
      d.getTime()
    ) &&
    /GMT|T\d{2}:\d{2}:\d{2}Z/
      .test(
        s
      )
  ) {
    return formatJst_(
      d
    );
  }

  return s;
}


/* =========================================================
 * JST表示
 * ========================================================= */

function formatJst_(date) {
  return Utilities.formatDate(
    date,
    DEFAULT_TZ,
    'yyyy-MM-dd HH:mm:ss'
  );
}

function testMetiAccess() {
  const targets = [
    {
      name: '安全保障貿易管理トップ',
      url: 'https://www.meti.go.jp/policy/anpo/law09.html'
    },
    {
      name: 'METIニュースリリースRSS',
      url: 'https://www.meti.go.jp/ml_index_release_atom.xml'
    }
  ];

  targets.forEach(target => {
    console.log('================================');
    console.log('TEST: ' + target.name);
    console.log('URL: ' + target.url);

    try {
      const response = UrlFetchApp.fetch(target.url, {
        muteHttpExceptions: true,
        followRedirects: true,
        headers: {
          'Cache-Control': 'no-cache'
        }
      });

      const code = response.getResponseCode();
      const body = response.getContentText('UTF-8');

      console.log('HTTP: ' + code);
      console.log('Bytes: ' + body.length);

      if (code === 200) {
        console.log('ACCESS: OK');

        if (target.name === '安全保障貿易管理トップ') {
          console.log(
            '外国ユーザーリスト文言: ' +
            (body.includes('外国ユーザーリスト') ? 'FOUND' : 'NOT FOUND')
          );

          console.log(
            '新着情報文言: ' +
            (body.includes('新着情報') ? 'FOUND' : 'NOT FOUND')
          );
        }

        if (target.name === 'METIニュースリリースRSS') {
          const xmlDetected =
            body.includes('<feed') ||
            body.includes('<rss');

          console.log(
            'RSS/XML構造: ' +
            (xmlDetected ? 'FOUND' : 'NOT FOUND')
          );
        }

        console.log(
          'HEAD: ' +
          body.substring(0, 300).replace(/\s+/g, ' ')
        );

      } else {
        console.log('ACCESS: FAILED');
      }

    } catch (e) {
      console.log('ERROR: ' + e);
    }
  });

  console.log('================================');
  console.log('METI ACCESS TEST FINISHED');
}
