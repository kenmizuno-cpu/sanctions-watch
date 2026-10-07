# sanctions-watch

[![watch-ofac](https://github.com/kenmizunokuro/sanctions-watch/actions/workflows/watch-ofac.yml/badge.svg)](https://github.com/kenmizunokuro/sanctions-watch/actions/workflows/watch-ofac.yml)
[![watch-jp](https://github.com/kenmizunokuro/sanctions-watch/actions/workflows/watch-jp.yml/badge.svg)](https://github.com/kenmizunokuro/sanctions-watch/actions/workflows/watch-jp.yml)

財務省・OFACは定期的に自動監視し、差分を抽出してマスターを更新する。
経産省は公式通知を人が確認し、通常のブラウザで取得した公式PDFを検証・レビューして
反映する手動優先（manual-first）運用とする。

真のマスターは `data/master/master.csv`。Excel は毎回そこから生成し直す派生物で、
Google Drive に上書きアップロードされる。Excel をマスターにしないのは、バイナリだと
git 差分が効かず「何がどう変わったか」を後から追えなくなるため。

## 構成

| ワークフロー | cron | 対象 |
| --- | --- | --- |
| `watch-ofac.yml` | `17 * * * *`（毎時） | OFAC SDN / Consolidated |
| `watch-jp.yml` | `37 0,6,12,18 * * *`（6時間ごと） | 財務省 |
| `watch-meti-manual-sla.yml` | `5,20,35,50 * * * *`（15分ごと） | 経産省の手動取込状態のみ（外部通信なし） |
| `bootstrap.yml` | 手動 | 初回マスター作成 |

分を 17 分・37 分にずらしてあるのは、毎時ちょうどが GitHub 側で最も混んで
スケジュール遅延・スキップが起きやすいため。共通処理は
`.github/actions/run-watch` にまとめてあるので実行ロジックは1箇所。
同じ concurrency グループに入れてあるので同時 push で競合しない。

OFACはGitHub scheduleの起動遅延に備え、専用GASによる10分間隔の外部監視を
追加できる。成功から75分で再実行要求、90/150分で独立メール通知する。
取得失敗の状態は `data/monitoring/ofac_attempt.json` に保存し、失敗時には
部分更新したmaster/stateをpushしない。**外部タイマーはGAS所有者による設定が必要**。
[導入手順](docs/ofac-watchdog-setup.md) と `apps_script/OfacWatchdog.gs` を参照。

Public リポジトリ前提。Actions の実行時間が無制限なので毎時でも枠を気にしなくていい。
元データが全て公開情報なので、マスターを Public に置くこと自体は問題にならないはず。

## セットアップ

```bash
pip install -r requirements.txt
python -m tests.test_offline    # 正規化ロジックの自己テスト（125項目）
python -m tests.test_e2e        # 取得をモックした通しテスト（9項目）
```

### 初回マスター作成

**初回は Actions ではなくローカルで実行すること。** いきなり自動で回すと、
パース結果がおかしくてもそのままマスターとして確定してしまう。

```bash
python -m src.import_legacy --src path/to/black_receiver_name_all.xlsx
python -m src.export_excel
head -3 data/master/master.csv    # 列ズレの確認
wc -l data/master/master.csv      # 件数の確認
```

問題なければコミット。以降は Actions が回る。

### Secrets

| Secret | 用途 | 未設定時 |
| --- | --- | --- |
| `SLACK_WEBHOOK_URL` | 差分検出時・取得失敗時の通知 | スキップ |
| `GOOGLE_SERVICE_ACCOUNT` | サービスアカウントJSONをそのまま貼る | スキップ |
| `DRIVE_FOLDER_ID` | 配布先フォルダのID | スキップ |

未設定ならそのステップは飛ぶので、まず差分検出だけ動かして後から足せる。

**Drive の注意点。** サービスアカウントのマイドライブ直下は容量エラーになることがある。
共有ドライブにフォルダを作ってサービスアカウントを編集者で招待し、そのフォルダIDを
`DRIVE_FOLDER_ID` に設定するのが確実。同じファイルIDを更新し続けるので、Mac の Drive
デスクトップアプリが同名ファイルを上書き同期する。体感は「勝手に最新版が手元にある」。

## 設計で効いているところ

**ハッシュ2段構え + 条件付きGET。** ETag / Last-Modified を送り、更新が無ければ 304 が
返ってダウンロードもパースも差分計算もスキップされる。SDN.CSV は数十MBあるので毎時
取りに行く以上これは必須。ファイルが差し替わっても正規化後の内容が同じなら
`no_effective_change` として区別するので、「更新されたのに実は何も変わっていない」も判別できる。

**突合は必ずソース単位。** マスターには自動再取得できない行が含まれる。

| 出所 | 件数 | 再取得 |
| --- | --- | --- |
| OFAC | 57,782 | 可 |
| 財務省 | 14,515 | 可 |
| 経産省 | 4,431 | **不可**（PDFのみ） |
| 出所表記なし（令和5年12月外為法） | 617 | 不可 |
| 外務省告示第61号 | 253 | 不可 |
| UK FCDO | 8 | 不可 |

全件を入れ替える実装にすると、この 5,309 行が毎回消える。OFAC を取得したときは
OFAC 由来の行だけを照合し、他ソースの行には一切触れない。

**行は絶対に削除しない。** 掲載が終了した対象も `有効なのか` を `無効` に倒して残す。
消すと登録時間が失われ、「去年の送金時点ではこの人は制裁対象だった」を説明できなくなる。
再掲載されたら自動で `有効` に戻る。

**壊れたら止まる。** 列構成の変化で `SchemaError` を投げる。提供元がフォーマットを
変えたときに、壊れたデータで静かにマスターを上書きするのが一番怖い。同じ理由で、
取得失敗時はワークフロー自体を失敗させる。

**区分番号は持たない。** 財務省は新カテゴリを途中に挿入するたび以降を繰り下げる
（ハイチ共和国が `39.` → `40.`、ロシア連邦(特定銀行) が `30.` → `31.` など実データで確認）。
番号を G列に入れると再採番のたびに数千行が偽の「変更」判定になる。

**last_checked は state.json に置かない。** 毎時実行だと内容が変わらなくても git 差分が
出て月720回のコミットが積み上がる。代わりに `data/heartbeat/YYYY-MM.csv` に追記する。
副産物として「毎時チェックしていて、その間に更新は無かった」を証明できる形になる。

```bash
# ハートビート以外の意味のある履歴だけ追う
git log --oneline --grep='chore(heartbeat)' --invert-grep
# マスターの変更履歴が実質の監査ログ
git log -p data/master/master.csv
```

**生ファイルを必ず残す。** `data/raw/mof/20260828T202601Z__shisantouketsu20260828.csv`
の形で日時付き保存。外為検査で「いつ時点のリストで照合したか」を聞かれたときに出せる。
直近30件で自動的に古いものを消す。

## 名前の扱い

照合キーは名前そのもの。`match_key()` が NFKC・大文字小文字・クォート記号・
全角半角・空白を吸収するので、提供元が表記を変えただけで偽の「削除＋追加」は出ない。

財務省の告示文にある `「株式会社SRIEMI」` や `“JSC SRIEMI”` の外側引用符は、
別名を示す書式であって名称の一部ではない。完全一致照合で取りこぼさないよう、
配布用の表示名では引用符を除去し、元表記はマスターの `variants` にだけ残す。
また旧処理が `(original script：不明)` から作った `NAME不明` 形式は名称ではないため、
取込・マスター読書き・配布出力の各段階で除外する。

別名が1セルに詰め込まれている行は分解して個別の名前にする。無効化ではなく分解なのは、
中に本物の制裁対象が入っているため。

```
（別称、ヨッフェ研究所） Ioffe Institute   -> ヨッフェ研究所 / Ioffe Institute
Igor Chayka (Chaika; a.k.a. IFYAU9)        -> Igor Chayka / Chaika / IFYAU9
MS ANGIA (a.k.a. GATHER VIEW) (T7AX8) ...  -> MS ANGIA / GATHER VIEW
```

**分解はマーカーを検出した行だけに限定している。** `a.k.a.` をドット無しで書くと
`Abdifatah Abu[baka]r Abdi` の語中に誤爆して3分割される。`Makara`、`Barakallah`、
`Junjuaka` も同様。マーカーが無ければ読点があっても割らない
（`"ズベイル、アブ"` は1名として維持）。テストで固定してある。

自動無効化は「どう解釈しても制裁対象名になりえない」ものだけ。数字のみ、Excel の
日付シリアル値、空、記号のみ。3文字以下の短い名前（`ADF` `M23` `张伟` など）は
誤検知の温床だが、取りこぼしのほうが重大なので有効のまま残す。該当行には
`review_flag` が立つので、必要なら `data/master/master.csv` 側で拾える
（配布用 Excel には専用シートを出さない。件数だけ「ビルド情報」に出る）。

## 財務省CSVの実データで踏んだ罠

実物 (shisantouketsu20260828.csv, 32列2866行) に合わせて実装してある。

**別名の区切りは全角の `；`。しかも括弧の内側にも出る。**

```
ムハマド・イブラヒム・マッカウィ(生年月日1960/4/11； 1963/4/11、出生地エジプト、国籍エジプト)
```

素朴に `；` で割ると名前が真っ二つになる。`split_top_level()` は括弧の深さ0でだけ切る。

**別名に説明文が付く。** `（生年月日…、出生地…、国籍…）` は名前ではないので
`strip_descriptor()` で落とす。これを残すと `1963/4/11、出生地エジプト、国籍エジプト)`
のような断片が照合対象に紛れ込む。

**称号と役職は名前ではない。** `称号（ムラー; ハッジ）` `役職（閣僚評議会第一副議長）`
の列は意図的に除外している。敬称や肩書きを名前として登録すると照合が壊れる。

**`区分` は番号だけでカテゴリ名が無い。** しかも財務省は新カテゴリを挿入するたび
以降を繰り下げる。実データで確認した例:

| カテゴリ | 旧番号 | 現番号 |
| --- | --- | --- |
| 中央アフリカ共和国(個人) | 34 | 37 |
| イエメン共和国 | 36 | 39 |
| 南スーダン | 37 | 40 |
| ハイチ共和国 | 39 | 42 |

対応表は `data/kubun_map.json` で管理する。財務省は公開していないので、
既存マスターとの多数決で導出した。**推測で名前を埋めていない** — 票が無い区分は
空にしてある。誤ったカテゴリ名が静かに数千行に広がるより、空のほうが安全。

`mof.detect_drift()` が毎回、マスターの既存カテゴリと突き合わせて繰り下がりを
検出する。2回目以降の実行では検出時に `SchemaError` で止まるので、
`data/kubun_map.json` を人が更新する。

## OFACの実データで踏んだ罠

**名前が「姓, 名」形式。** 既存マスターや財務省は自然順で持っている。

```
OFAC の CSV : HANIYAH, Ismail Abdul Salah
既存マスター : Ismail Abdul Salah Haniyah
```

揃えないと同一人物が「削除」と「追加」の両方に出る。`swap_surname_first()` が
キー生成時にだけ語順を入れ替える (表示名は原文のまま)。ただし団体名にも読点は
出るので (`7 MAKARA PHARY CO., LTD.`)、読点の後ろが法人格の接尾辞なら入れ替えない。
この修正で既存マスターの二重登録が7,353組統合された。

**SDN と Consolidated は必ず1回でマージする。** どちらも出所は `OFAC` なので、
別々に `merge()` を呼ぶと「SDN に無い OFAC 行」を消した直後に
「Consolidated に無い行」を消すことになり、互いの分を削除し合う。
実データで Consolidated の削除件数がマスターの OFAC 全件を超えて発覚した。

**304 のときも突合対象には含める。** 片方だけ更新された回にもう片方を
取り直さないと、そのリストの全件が掲載終了と判定される。304 のときは
`data/raw/` の生ファイルから読み戻す。

### OFAC Party削除の承認手順

Advanced XMLから既存の `DistinctParty.FixedRef` が消えた場合は、名称差分より重大なため
`data/review/ofac_party_removal_queue.csv` に `PENDING_REVIEW` として隔離する。
取得・構造検証・追加/変更・state・Party/Alias履歴の更新は継続し、対象名は承認まで
master上で有効のまま維持する。ダッシュボードには
`掲載終了候補・要レビュー` と表示され、承認待ちだけを理由にworkflowは失敗しない。

候補ごとの対象名・初回検知日時・FixedRef・検知snapshot SHA256・event_idは
`data/dashboard/changes.csv` に `掲載終了候補（要確認）` として出力する。
既存の `08_最新差分` シートでも確認でき、304・財務省だけの実行でも保存済みの
承認待ちを表示する。同一イベントの表示キーは再確認で変えず、履歴上限からも
現在の候補を保護する。財務省の後処理・METIの個別workflowで差分を追加しても、
共通writerで同じ保護を行う。Sheetsの「対応済」は表示上の対応状況であり、削除承認とは
別の管理である。承認・再掲載で解消したイベントを新たな候補としては出力しないが、
過去の候補行は差分履歴として保持する。

OFAC公式の削除発表を照合したうえで、
`data/review/ofac_party_removal_approvals.csv` に次の情報を追加した場合だけ
対象Partyの非掲載を適用する。

- 対象リストと、取得したAdvanced XMLそのもののSHA256
- そのsnapshotで消えたFixedRefの完全な集合（不足・余分はどちらも失敗）
- 履歴上の対象名、承認者、timezone付き承認日時、Treasury公式URL

承認はsnapshot hashに固定される。同じFixedRefでも別のsnapshotには流用されない。
将来の削除イベントでは必ず新しい行を追加し、過去の承認行は編集しない。
承認行が無い、またはhashが違う場合は保留を継続する。同じhashに一部だけ、または
余分なFixedRefを承認した場合は安全のためtransaction全体を公開しない。

承認追加後にOFACが `304 Not Modified` を返した場合も、保存済みキューとParty履歴から
承認を再評価して非掲載を適用できる。承認前にPartyが再掲載された場合は元イベントを
`CANCELLED_REAPPEARED` とし、同時検知した他Partyがまだ不在なら現在snapshotで
新しい完全一致イベントに再隔離する。

承認後もmaster行とParty/Alias履歴は削除しない。対象名からOFAC出所だけを外し、
他出所または別の現役Strong Partyがあれば有効のまま維持する。適用した名称ごとの差分、
承認情報、適用前後のmaster/index hashは
`data/review/ofac_party_removal_audit.csv` に原子的に追記される。
取得失敗、構造変更、Classic/Advanced coverage不一致、承認台帳やキューの破損は
従来どおりworkflowを失敗させ、formal outputsを公開しない。

## 初回同期の扱い

既存マスターは別のパイプラインで作られているため、名前の粒度が今のパーサと違う。
そのまま突合すると、表記が壊れていただけの行が大量に「掲載終了」と判定される。
実データでは2,547件が該当し、その内訳は次のとおりで**制裁解除は1件も含まれていなかった**。

| 件数 | 内訳 |
| --- | --- |
| 2,100 | `（シェイク）ムーサ・ヒラール（(Sheikh) Musa Hilal）` のように和英が1セルに同居 |
| 158 | `(Ansar al Charia in Libya` など括弧が閉じていない断片 |
| 107 | OFACにも載るため有効のまま維持 |
| 86 | `..., born 9 May 1986 in Egypt` 説明文の混入 |
| 76 | 60文字超の連結 |
| 20 | `（a)Bilal （b)Adel （c)Fodhil` 旧形式の列挙 |

そのため**ソースごとの初回同期では無効化を行わない**。`state.json` の
`baseline_synced` で判定し、初回は掲載終了候補として報告するだけで行は有効のまま残す。
2回目以降は通常どおり無効化する。手動で止めたい場合は `--no-delist` を付ける。

初回同期を反映した直後にもう一度回すと差分ゼロになることを確認済み
（追加0 / 削除0 / 変更0）。繰り返し実行しても暴れない。

Advanced XML の初回 master 反映で、既にOFACのParty/Alias索引に存在した
現役Strong名称が新たにmasterへ入る場合は、差分の種別を
`初回同期（Advanced XML）` とする。同じ実行で索引に新しく現れた名称は
通常の `追加` として区別する。索引そのものの初回baselineでは、比較元が
無いためmaster追加分を初回同期とする。初回同期は名簿と社内取込には残すが、
OFAC公式の新規指定件数やダッシュボードの未解消差分キューには数えない。

2026-09-04 10:26:50 JSTの初回反映4,721行は、
`data/dashboard/changes.csv` 内で種別のみを初回同期に訂正した。
名称・出所・検知日時・master行は維持し、旧種別はGit履歴で追跡できる。
Google Sheets側では種別を含むイベントキーを使うため、移行時は先に
Apps Script v0.4.1 を稼働シートへ適用して同期し、既存履歴の種別とキーを
更新してから、このCSV変更を公開する。逆順だと旧スクリプトが同じ4,721件を
別イベントとして取り込み、保持上限5,000行から他の履歴を押し出しうる。

## 既知の運用上の注意

**初回の OFAC 取得で大量の「変更」が出る。** 既存マスターの OFAC 由来 57,782 行は
`制裁リスト（OFAC）` だが、実際に取得すると SDN / Consolidated が判別できるので
`制裁リスト（OFAC：SDN）` に変わる。一度きりの情報量の増加で、実害はない。
cron を有効にする前に手動で1回流して、差分レポートがこれだけであることを確認するとよい。

## 経産省（METI）の手動監視

経産省サイトへの自動クロールは行わない。ブラウザ互換User-Agentによるアクセス、
ヘッドレスブラウザ、プロキシやIPローテーションなど、機械アクセス制限の回避も
サポートしない。公式通知を人が確認し、経産省の公式PDFを通常のブラウザで取得する。
過去のraw・監査台帳・heartbeat・dashboard履歴は削除せず、監査証跡として保持する。

人的統制として、少なくとも1名の明示された運用責任者が経産省の公式通知チャネルを
継続購読し、不在時のカバレッジを維持する。

| 統制項目 | 現在の設定 |
| --- | --- |
| 運用責任者 | `kenmizuno-cpu` |
| 検知経路 | 経産省の公式通知チャネルを人が確認 |
| 不在時対応 | 代理担当者が同じ公式通知を確認 |

メールボックスの購読・受信確認・担当者カバレッジはリポジトリ外の人的統制である。
リポジトリが計測する60分の処理SLAは、経産省の公開時刻やメール受信時刻ではなく、
`meti_manual_event detect` が `DETECTED` を記録した時点から始まる。

### 更新時の手順

1. 経産省の公式通知を受信・確認する。
2. 通知を検知イベントとして記録し、出力された `DETECTION_ID` を控える。

   ```bash
   python -m src.meti_manual_event detect \
     --operator "kenmizuno-cpu" \
     --notice-url "https://www.meti.go.jp/..." \
     --title "外国ユーザーリスト更新"
   ```

3. 通常のブラウザで通知先を開き、経産省の公式PDFをダウンロードする。
4. `DETECTION_ID` と公式URL、日付、想定件数を指定してPDFを検証・取込する。

   ```bash
   python -m src.meti_manual_import \
     /path/to/official-meti-list.pdf \
     --source-url "https://www.meti.go.jp/.../official-list.pdf" \
     --publication-date "YYYY-MM-DD" \
     --effective-date "YYYY-MM-DD" \
     --expected-count 1234 \
     --detection-id "$DETECTION_ID"
   ```

   成功時に表示される `SHA256` を以降の `$SOURCE_HASH` として使用する。この時点では
   `REVIEW_REQUIRED` になり、マスターにはまだ反映されない。

   `BLOCKED` になった場合、PDFや入力値を修正して同じ `DETECTION_ID` でこのコマンドを
   再実行する。再試行回数は監査イベントへ記録される。誤検知などで取込を中止する場合は、
   適用済みスナップショットを維持したまま検知を明示的に取消す。

   ```bash
   python -m src.meti_manual_event cancel \
     --operator "kenmizuno-cpu" \
     --detection-id "$DETECTION_ID" \
     --note "取消理由"
   ```

5. スナップショットを確認し、承認または却下する。

   ```bash
   python -m src.meti_review status --hash "$SOURCE_HASH"

   python -m src.meti_review approve \
     --hash "$SOURCE_HASH" \
     --reviewer "kenmizuno-cpu" \
     --note "公式PDF・件数・差分を確認"

   # 却下する場合
   python -m src.meti_review reject \
     --hash "$SOURCE_HASH" \
     --reviewer "kenmizuno-cpu" \
     --note "却下理由"
   ```

6. `APPROVED` を確認した場合だけ既存の安全な反映計画を生成・検証し、表示された
   plan/masterのSHA256を目視確認してから適用する。却下時は生成・適用しない。

   ```bash
   python -m src.meti_apply_plan --hash "$SOURCE_HASH"
   python -m src.meti_apply verify \
     --summary "$SUMMARY_PATH" \
     --source-hash "$SOURCE_HASH"
   python -m src.meti_apply apply \
     --summary "$SUMMARY_PATH" \
     --source-hash "$SOURCE_HASH" \
     --operator "kenmizuno-cpu" \
     --confirm-plan-sha256 "$PLAN_SHA256" \
     --confirm-master-before-sha256 "$MASTER_BEFORE_SHA256" \
     --confirm-master-after-sha256 "$MASTER_AFTER_SHA256"
   ```

## 外務省：Phase 3A 手動取得・資料レビュー

ブラウザでは開ける一方、Macのrequests/curlとGitHub ActionsではAkamaiの403を確認したため、外務省は人が公式サイトを確認して取得する。自動通信は共通取得層で外務省・経産省の全ホストとリダイレクト先について停止する。`watch-mofa.yml`は読取権限のオフライン検証のみ。`MOFA_MONITOR_ENABLED=false`を維持し、旧`src.mofa_watch`は通信・状態更新せず、手動取込の案内と終了コード1を返す。

3Aの範囲は資料の原本保存・SHA-256比較・レビュー待ち作成まで。対象者件数は**未解析（空欄）**。`diff_level=document`、`applied=false`を維持する。資料確認は対象者審査・制裁解除・名簿反映の承認とは別。既存master・OFAC索引・changes・取込名簿を変更しない。

### 手動取得と取込

[現行リスト入口](https://www.mofa.go.jp/mofaj/gaiko/terro/kyoryoku_05.html)の国連決議1267号等と1373号の二PDF、[報道発表](https://www.mofa.go.jp/mofaj/press/release/index.html)の関連本文と別添PDFをブラウザで確認・保存する。HTMLはSafariの「Webアーカイブ」ではなく、HTMLのソースを保存する。表示ページのPDF印刷は公式原本PDFの代用にしない。

各操作には担当者、タイムゾーン付きの日時、理由が必要。入力URLは公式HTTPS URLに限定し、取込コマンドは通信しない。ブラウザから保存されたファイルの取得方法は担当者の申告として記録する。SHA-256は保存後の同一性を確認する値で、発行者の電子署名検証ではない。

```bash
cd "$HOME/Desktop/sanctions-watch"
# 初期化: 空の表示CSVを作る。サイトを確認したことにはならない。
.venv/bin/python -m src.mofa_manual init \
  --operator '健 水野' --at '2026-10-01T15:00:00+09:00' \
  --note '外務省を手動取得運用へ切り替え'

# 実際に確認した日時と確認範囲を記録する。更新未取得なら --result pending。
.venv/bin/python -m src.mofa_manual check --family catalog --result checked \
  --operator '健 水野' --at '2026-10-01T15:05:00+09:00' \
  --note '現行リスト入口の1267号等と1373号のリンクを確認'
.venv/bin/python -m src.mofa_manual check --family press --result checked \
  --operator '健 水野' --at '2026-10-01T15:06:00+09:00' \
  --note '当月・前月の報道発表を確認。対象発表と別添の有無を確認'

# 実ファイル名・実際のPDF URL・実際の取得日時に置き換える。
.venv/bin/python -m src.mofa_manual import "$HOME/Downloads/current-un.pdf" \
  --role current_un --source-url 'https://www.mofa.go.jp/実際のPDFパス.pdf' \
  --operator '健 水野' --at '2026-10-01T15:10:00+09:00' \
  --note '公式1267号等PDFをブラウザで保存' --dry-run
# 結果の原本ハッシュと候補を確認後、同じコマンドから --dry-run を外して保存する。
```

現行二PDFは`--role current_un` / `current_1373`。本文HTMLは`--role notice`、別添PDFは`--role attachment_pdf --notice-url '元の報道発表URL'`。`--title`と`--publication-date YYYY-MM-DD`は確認した情報だけを指定する。過去の既存発表は`--baseline`を指定すれば初回資料として記録する。現行二PDFの最初の取込は常に初回資料として扱う。`--expected-sha256`は別途控えたハッシュとの照合に使える。

別添は親の発表URLごとに記録する。本文から見つかった公式別添は自動取得せず「手動取得待ち」にする。本文を再取得したときはその日時以降に別添も再取得して確認する。URL変更を同じ資料の差し替えとして比較したい本文・別添は、直前の`document_key`を`--document-key`で指定する。親発表や資料種別が異なるキーは使えない。

### 保存・状態・レビュー

原本は`data/raw/mofa/`へハッシュ名のgzipとして保存し、削除しない。PDFは20MiB、本文HTMLは5MiBを上限とし、PDFのヘッダー・終端・ページ構造、HTMLの本文領域を確認する。破損ファイル・ハッシュ不一致・取込失敗は「手動取込エラー」とし、有効な直前原本を保持する。担当者・日時・URL・ハッシュ・理由・成否は`data/mofa/manual_operations.csv`、取得情報は月別`data/source_audit/`、資料履歴は`data/mofa/events.csv`、レビュー待ちは`data/review/mofa_document_queue.csv`に残る。手動取得にはHTTP 200/304やETagを作らない。

表示CSVの最終チェックは明示したサイトの手動確認日時。ファイル取込はこの日時を更新しない。現行二PDFが揃わない間は手動取得待ち、未解消候補があれば要レビュー、破損取込はエラーを維持する。サイト確認だけでは取込エラーを消さない。エラーは同じ資料の有効な再取込で解消する。担当者が`check --result pending`で立てた取得待ちは、未取得資料を揃えた上で明示的に再確認して`check --result checked`で解消する。

初期化・取込・確認だけで自動監視の`baseline_complete`や`last_complete_scan_at`を進めない。旧運用の未確認期間は保持し、公式アーカイブを人が確認してから理由付きの`coverage-ack`で判断を記録する。公表日の日付精度から分単位の検知遅延を算出しない。

```bash
.venv/bin/python -m src.mofa_documents review --event-id ID --source-hash SHA256 \
  --reviewer '確認者名' --at '2026-10-01T15:20:00+09:00' --note '資料内容を確認した理由'
# 確認対象外なら review ではなく cancel を使い、理由を残す。
.venv/bin/python -m src.mofa_documents coverage-ack \
  --reviewer '確認者名' --at '2026-10-01T15:20:00+09:00' \
  --note '対象期間の公式アーカイブを手動確認した範囲と判断'
```

レビューはイベントIDと原本ハッシュに固定する。同じ原本の再取込は候補を増やさず、A→B→Aの差し替えは別イベント。以前のレビューを新ハッシュへ引き継がない。状態・操作記録・監査・キュー・表示CSVは同一世代で保存し、捕捉可能なI/O例外でrollbackする。新しい原本だけが残る場合はあるが、旧原本を破棄しない。SIGKILLや電源断の完全原子性、同時の取込・レビュー操作は保証しないので、一人ずつ順に実行する。

### 導入の順序

1. コードのPRを検証・レビューしてmainへ統合する。`MOFA_MONITOR_ENABLED=false`を維持する。
2. 最新mainからデータ用の別ブランチを切り、上記`init`を実行する。公式資料はブラウザで取得し、確認・取込・資料レビューを行う。
3. データの変更は次の許可パスだけをPRにする。`data/mofa/`、`data/review/mofa_document_queue.csv`、`data/raw/mofa/`、`data/source_audit/`、`data/heartbeat/`、`data/dashboard/status.csv`、`data/dashboard/mofa_documents.csv`。他の監視の最新mainを取り込み、通常のPRで統合する。
4. mainの`data/dashboard/mofa_documents.csv`が取得可能になった後、稼働GASのバージョンとコードを退避する。`apps_script/Code.gs`に置換し、`Mofa.gs`を追加する。実装は0.4.3。既存onOpen/syncAll/scheduledSyncAllを重複させない。
5. 手動同期で六系統と`10_外務省資料`、既存`08_最新差分`・`09_再審査分`を確認する。外務省は「手動確認／要確認」とし、自動監視の30/60分閾値を使わない。GASのメモはイベントIDで保持し、公式レビューは上記CLIで行う。
6. OFAC・財務省の定期監視とSheets定期同期は既存の運用を続ける。外務省の公式サイト確認・資料保存は担当者が行う。
# 暗号資産アドレス監視の追加

OFAC SDN Advanced XMLの明示アドレスを、既存の人物・団体名マスターと別の台帳へ収録します。独立した監視スプレッドシートのApps Scriptは、後から修正できるよう10ファイルへ機能別に分割しています。

- [導入・運用](docs/crypto-addresses.md)
- [更新ロードマップ・引き継ぎ](docs/roadmap-20261006.md)
- [機能別Apps Script](apps_script/crypto_dashboard/)
- [専用workflow](.github/workflows/watch-crypto.yml)

公式掲載の事実と社内対応判断を分離します。初回収録を新規追加に数えず、掲載終了候補は原文と証跡を保持します。
