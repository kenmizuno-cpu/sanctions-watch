# OFAC外部監視の導入

GitHubのscheduleが起動しない遅延に備え、**専用のGoogle Apps Scriptプロジェクト**からOFACの成功時刻を確認し、必要時に同じActionsを起動する。取得・名簿反映の処理と削除承認ルールは従来どおりである。

## 有効化（GAS所有者による一度の設定）

1. この変更をmainへ反映する。`watch-ofac`を手動で一度実行し、成功heartbeatを確認する。初回の `data/monitoring/ofac_attempt.json` がまだ存在しなくても外部監視は動く。
2. GitHubの [fine-grained personal access token設定](https://github.com/settings/personal-access-tokens/new) で対象を `kenmizunokuro/sanctions-watch` だけに限定する。Repository permissionsは **Actions: Read and write、Contents: Read-only**。トークンに期限を設定し、更新予定を管理する。トークンはチャット、ソースコード、シートのセルに貼らない。
3. [Apps Script](https://script.google.com/) で「OFAC外部監視」という新規プロジェクトを作成する。既存ダッシュボードのプロジェクトとは分ける。初期のCode.gsを `apps_script/OfacWatchdog.gs` の全文に置き換える。このファイルだけで動き、既存のCode.gs/Mofa.gsは不要。
4. プロジェクトの設定 → スクリプトプロパティに以下を追加する。

   | プロパティ | 値 |
   | --- | --- |
   | `OFAC_WATCHDOG_TOKEN` | 手順2のトークン |
   | `OFAC_WATCHDOG_EMAIL` | 運用担当者の受信できるメールアドレス。複数はカンマ区切り |
   | `OFAC_WATCHDOG_REPO` | 通常は不要。別repoへの導入時だけ `owner/repo` を指定 |

5. 関数 `checkOfacWatchdog` を実行し、Googleの認証を許可する。実行ログで `lastSuccessAt`（UTC）と `ageMinutes` を確認する。この関数は読み取りだけで、再起動もメール送信も行わない。
6. `installOfacWatchdog` を実行する。API読み取りとテストメールの送信受付後に、10分間隔の `ofacWatchdogTick` トリガーを作成する。メールの到着を受信箱で確認する。API、認証、メールクォータが失敗すれば新しいトリガーは作成されない。
7. `ofacWatchdogTick` を一度実行する。成功から75分以上経過し、稼働・待機中のOFAC実行がなければmainに再実行を要求する。GitHubで `workflow_dispatch` の実行と新しいheartbeatを確認する。監視が正常なら追加起動しない。
8. GASのトリガー画面で `ofacWatchdogTick` が1個だけ存在することを確認する。失敗通知設定を「今すぐ通知」にして、GAS自体の停止にも備える。

トークンの発行・GASの認証許可は所有者のアカウントで行う。このリポジトリの更新だけでは外部タイマーは有効にならない。

## 動作

| 条件 | 動作 |
| --- | --- |
| SDNとConsolidatedが同一時刻に成功 | その時刻を最終成功として採用 |
| 304・変更なし・削除承認待ち | 取得確認は成功。承認待ちのParty削除は保留のまま |
| エラー、片側のみ、将来時刻 | 成功時刻を更新しない |
| 最終成功から75分以上 | 稼働・待機中がなければ再実行要求 |
| 再実行要求から30分未満 | 追加要求を抑制。HTTP 204の受理後にだけ記録 |
| 90分以上／150分以上 | 警告／重大メール |
| 新しい取得・workflow失敗 | 実行失敗メール。150分で重大へエスカレーション |
| GitHub API・トークン・dispatch障害 | 外部監視エラーメール。読み取り失敗時に再実行を要求しない |
| 復旧 | 復旧メール |
| 同じ障害が継続 | 同じ通知は6時間間隔。通知受付失敗なら次回再試行 |

専用GASはOFACの6ファイルを直接取得せず、GitHubの監視結果を確認する。cronの `17 * * * *` とGASのダッシュボード警告閾値90/150分は継続する。メール先・トークンはGASプロパティにのみ保存する。

`OFAC_WATCHDOG_STATE` に最終tick、成功時刻、dispatch受理時刻、通知状態を保存する。実行ログにも同じ診断値を出す。トークンの値は記録しない。`lastDispatchAt` はミリ秒のUTC epochで、受理は取得成功を意味しない。後続のheartbeatで成功を確認する。

## 取得失敗の保存とHTTP再試行

`src.monitor_attempt` が `src.watch` の終了コードを `data/monitoring/ofac_attempt.json` に記録する。成功時は通常のdataコミットに含める。失敗時はArtifactを残した後、`src.persist_monitor_failure` が最新mainの別worktreeから**この1ファイルだけ**をコミットする。途中のmaster/state/heartbeat等を失敗時にまとめてpushしない。新しいattemptを古い失敗記録で上書きしない。

依存関係・自己テストの失敗やjob timeoutでattemptが未作成でも、外部監視はGitHub Actionsの最新完了runの失敗とheartbeatの古さを確認する。GitHubへのpushも停止している場合はattempt保存はできないため、Actions側の失敗と独立メールで検知する。

HTTP 429/502/503/504と接続・読取timeoutは初回を含め3回まで、通常2秒→4秒で再試行する。Retry-Afterが30秒以内なら尊重し、それより長ければ今回の取得を失敗として終了する。403/404、証明書エラー、schema異常、名簿検証失敗は再試行で通過させない。METI/MOFAの自動通信禁止は維持する。

Slackは別途 `SLACK_WEBHOOK_URL` の設定が必要。未設定はActions警告と `notification_status=skipped`、受付成功は `sent`、受付失敗は `failed` と出力する。GASのメールは遅延・実行失敗を知らせるための経路で、**個別の名簿差分通知は代替しない**。

## 導入後の確認

### 稼働状況をスプレッドシートで確認する（2026-10-08追加）

救済監視GASはダッシュボードGASと別プロジェクトである。ダッシュボードの「同期トリガーあり」は救済トリガーの存在を証明しない。救済側で観測した稼働記録を同じスプレッドシートへ保存する。

1. **ダッシュボード側**のApps Scriptに `apps_script/OfacWatchdogStatus.gs` を新しいファイルとして追加し、全文を貼る。会社側で設置済みの `Code.gs` / `Mofa.gs` はそのまま使える。`setupOfacWatchdogStatus` を一度実行し、認証を許可する。表示更新10分トリガーと専用メニューの表示トリガーだけを作成し、既存同期トリガーは保持する。
2. **専用の救済監視側**の `OfacWatchdog.gs` を今回の全文に更新する。スクリプトプロパティに `OFAC_WATCHDOG_SPREADSHEET_ID` を追加し、ダッシュボードのURL `/spreadsheets/d/【ここ】/edit` にあるIDを設定する。救済側GASの実行者にはそのスプレッドシートの編集権限が必要。トークンと通知先はこれまでどおり救済側のみに設定する。
3. 救済側で `publishOfacWatchdogStatus` を一度実行し、追加のGoogle認証を許可する。保存済みの稼働証跡だけを書き出す関数であり、GitHubの再実行要求やメール送信はしない。これだけでは新しいtickの証跡は作られない。
4. ダッシュボード側で `runOfacWatchdogSelfCheck` を実行する。スプレッドシート再読込後は **OFAC救済監視 → 救済監視セルフチェック** からも実行できる。
5. 救済トリガーが0個なら、救済側で従来の `checkOfacWatchdog` → `installOfacWatchdog` → `ofacWatchdogTick` の導入手順を行う。`installOfacWatchdog` はテストメールを送信し、`ofacWatchdogTick` は条件に合えば再実行要求・障害メールを送る。

`01_監視ダッシュボード` の22〜25行に救済の状態を追加する。既存の制裁ソース件数や直近差分を上書きしない。`11_OFAC救済監視` に最終実行、処理終了、GitHub最終取得成功、救済要求の試行・受理時刻、HTTP結果、今回見送った理由、連続エラー、直近エラー履歴、通知エラー、直近20件の救済受理・エラー履歴を保存する。専用シートは機械更新領域なので手編集しない。

| 表示・判定 | 意味 |
| --- | --- |
| 未接続 | 救済側の記録なし。正常とは扱わない |
| トリガー0個／2個以上 | 未設定／重複。重大 |
| 記録・最終tick・GitHub状況確認が25分超 | 警告。60分超で重大 |
| 最新tickの終了記録がない | 未完了として重大 |
| API・救済要求・通知エラー | 重大。前回の取得成功日時が新しくても隠さない |
| 要求受理 HTTP 204 | 起動要求だけ受理。後続の取得成功とは別 |
| 最終救済なし | 新しい取得が続いている場合、正常。発火しなかっただけでは失敗にしない |
| 直近エラー履歴 | 復旧後も履歴は保持。連続エラー0なら過去エラーだけで異常にしない |

トークン値と通知先アドレスは保存しない。「トークン設定済み」は値の存在確認であり、Actions書込み権限を保証するものではない。救済要求の実際のHTTP結果を確認する。トリガー数は救済側の実行者が参照できる範囲である。複数所有者が別々に導入したトリガーは各所有者の画面でも確認する。

GitHub更新だけでは会社側GASへ自動配布されない。既存v0.4.4を維持する場合は上の追加ファイル方式で導入する。リポジトリの `Code.gs` にも任意の連携フックを用意しており、最新版を使う設置では従来の「運用セルフチェック」と同期完了時にも救済状態を反映する。

救済側のシート書込みが失敗しても救済要求の受理証跡は保持し、`OFAC_WATCHDOG_STATE.lastPublishError` と救済側ログへ記録する。ダッシュボードは古い記録を検知する。GAS実行が強制終了して最終処理が走らない場合も、古い記録・未完了tickを正常扱いしない。両GASが停止するとシート表示も更新されないため、表示判定時刻自体を確認する。

- まず24時間、続いて72時間で、90分超の滞留割合と最大成功間隔を導入前と比較する。
- 再実行要求の受理から成功heartbeatまでを確認する。受理だけで成功扱いにしない。
- 外部トリガーの実行ログ、警告・重大・復旧メール、GitHubのrun URLを対応させる。
- 稼働・待機中のrunが長く止まる場合は通知を見てGitHubのqueue/concurrencyを調査する。外部監視は既存runを自動キャンセルしない。
- MailAppの成功は送信受付である。実際の到着とメールフィルタを初回・設定変更時に確認する。

GASにも時刻・可用性の保証はなく、10分は設定間隔である。GitHub API/Actionsの全面停止中は取得を復旧できないが、GASのメール経路は独立している。共有GitHub concurrencyとGASのlock/cooldownで通常の重複を抑制するが、scheduleとdispatchが同時に作成される競合を完全には排除しない。

## 参照と検証

- [GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [GitHub workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs)
- [GAS ClockTriggerBuilder](https://developers.google.com/apps-script/reference/script/clock-trigger-builder)
- [GAS MailApp](https://developers.google.com/apps-script/reference/mail/mail-app)

```bash
python -m unittest
python -m tests.test_offline
python -m tests.test_e2e
python -m tests.test_mof_record_diff
python -m tests.test_mof_re_review
node --test tests/test_mofa_gas.cjs tests/test_ofac_watchdog.cjs tests/test_ofac_watchdog_status.cjs
git diff --check
```
