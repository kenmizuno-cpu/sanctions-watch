# 過去のトークン送受信の追加検証

## 検証内容

対象は公開OFACアドレスのTRON USDT・Ethereum USDT/USDC。既存原本では89掲載関係。現在残高がゼロでも、過去に発行元トークンの送受信があった場合、その取引を補足証拠として記録する。

| 段階 | 内容 | 上限・制限 |
| --- | --- | --- |
| 履歴候補 | TronGridの確認済み履歴／Ethereum Blockscout | 先頭20／50件、次ページURLは追わない |
| 取引結果 | 主ネットワークを確認した公開RPCの成功receipt | 最大3候補、最初の1証拠。取得先失敗時は別取得先で照会 |
| ブロック | TRON固化済み／Ethereum finalized高さ以下 | 正規ブロックの取引収録、識別子・高さを照合 |
| トークン | 正確な発行元契約のTransferイベント | 対象住所が送信者または受信者、金額は正の整数 |

トークン名・第三者の所有者ラベルは根拠にしない。今回の範囲では、Omni USDT、Avalanche/Arbitrum/Baseの過去トークン索引は対象外。既存の残高・直近Transfer照会は継続する。ETCの指定ブロック状態のHTTP400も別の既存照会として失敗を保持する。

## 判定と障害

| 表示 | 意味 |
| --- | --- |
| 発行元の送受信を検証済み | 成功した確定取引・ブロック収録・対象契約と住所を照合 |
| 取得範囲で証拠なし | 取得できた有限範囲で証拠が得られなかった。無効な住所とは判断しない |
| 候補あり・取引結果の取得不足 | 索引候補はあるがRPC照会を完了できない |
| 取得失敗 | 履歴索引・ネットワーク確認を取得できない |
| 未照会・上限/取得先停止 | 時間/要求上限や停止回路により未照会 |

現在の履歴照会結果はlast_success、過去の検証済み取引証拠はlast_verifiedとして別々に保持する。現在の照会が取得不足・空の有限履歴になっても過去の取引証拠は消さず、旧証拠表示と証拠を検証した日時を併記する。今回失敗・期限超過・最終成功日時を併記する。成功は6時間、失敗/取得不足は1時間後に再照会。300秒/600要求、HTTP本文512KiB、JSON2MiB。TRON索引は1秒以上間隔、Blockscoutも1秒以上間隔、既存RPCの停止回路を継承する。時間・要求上限で未処理となった対象は次の実行ですぐ再開し（API停止回路・通信失敗の1時間待ちと区別）、成功キャッシュの対象に再要求しない。

## ファイル構成

| ファイル | 責任 |
| --- | --- |
| src/crypto_history/registry.py | 対象・索引・取引結果取得先の固定リスト |
| src/crypto_history/transport.py | 有限予算・非リダイレクト通信の拡張 |
| src/crypto_history/events.py | 厳密Transfer照合・候補数制限 |
| src/crypto_history/tron.py | TRON索引・固化済み取引照合 |
| src/crypto_history/evm.py | Ethereum索引・finalized取引照合 |
| src/crypto_history/runner.py | キャッシュ・今回失敗・旧成功保持・件数 |
| src/crypto_token_history_watch.py | 独立JSONの原子的保存 |
| apps_script/crypto_dashboard/TokenHistory.gs | JSON検証と専用タブ・補足欄の日本語表示 |

GitHub Actionsが収録成功後に実行し、`data/crypto/token_history.json`を保存する。公式台帳・チェーン残高JSONを変更しない。SheetsはGitHubの同一SHAに固定した公式JSON・チェーンJSON・履歴JSONだけを読む。GASからTronGrid・Blockscout・RPCを呼ばない。

## 会社側の更新

既存10ファイルを設置済みなら、以下5ファイルの全文を同じコミット版で適用する。

| ファイル | 操作 |
| --- | --- |
| TokenHistory.gs | 新規追加（Apps Scriptの名前はTokenHistory） |
| Config.gs | 全文置換 |
| Fetch.gs | 全文置換 |
| View.gs | 全文置換 |
| Triggers.gs | 全文置換 |

保存後、`setupCryptoDashboard`を一度実行。「トークン履歴検証」タブ、同期履歴、取引IDと正確な金額・取得範囲・最終成功日時を確認する。最新構成は11個の.gsと既存appsscript.json。第2/3段階をまだ設置していない場合は同版の11ファイル全部を使用する。暗号資産プロジェクトの外にあるCode.gs/OfacWatchdog.gs/OfacWatchdogStatus.gsは別の人物名・OFAC救済監視用。

機能が増えても公式の「要確認」の形式制限を解除しない。送受信の実在は住所の所有者や実ネットワークの一意確定ではない。会社のSheetsの設置・権限承認・コードの適用は利用者が行う。

## 一次資料

- TRON確定receipt：https://developers.tron.network/reference/gettransactioninfobyid-1
- TRON履歴索引：https://developers.tron.network/reference/get-trc20-transaction-info-by-account-address
- Ethereum receipt/block JSON RPC：https://ethereum.org/developers/docs/apis/json-rpc/
- Blockscout索引・ページ範囲：https://docs.blockscout.com/devs/apis/rest
- 発行元契約：https://tether.to/en/supported-protocols/ ／ https://developers.circle.com/stablecoins/usdc-contract-addresses

## 検証記録

実装前の失敗テストから、偽契約・別住所・不正ABI・ゼロ金額・失敗receipt・未確定高さ・ブロックhash差異・取引未収録・API欠損・予算枯渇・キャッシュ失効・旧成功保持・公式JSONの保護を確認。GASは件数・原本SHA・掲載関係・契約・状態・証拠・正確な整数を検証する。独立レビューで欠損receiptの誤分類・取得不足時の証拠消失を再現し、失敗テスト→修正→成功テストで是正。GASの未照会候補を「証拠なし」と扱う不整合も拒否する。検証は公開RPCの応答を根拠とする。全体テスト445件・暗号資産GAS29件・既存GAS50件、offline213/e2e9、財務省diff/re-reviewが通過。同一コミットの本番JSONをGASで検証し、掲載関係・公式履歴の維持と全表の列数を確認済み。

## 差し替え用の全文（同一版）

共通コードのコミット：`10c6a78fade179da2739152f2f2678c14de39679`。PR #21：https://github.com/kenmizunokuro/sanctions-watch/pull/21 。

- [TokenHistory.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/10c6a78fade179da2739152f2f2678c14de39679/apps_script/crypto_dashboard/TokenHistory.gs)
- [Config.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/10c6a78fade179da2739152f2f2678c14de39679/apps_script/crypto_dashboard/Config.gs)
- [Fetch.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/10c6a78fade179da2739152f2f2678c14de39679/apps_script/crypto_dashboard/Fetch.gs)
- [View.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/10c6a78fade179da2739152f2f2678c14de39679/apps_script/crypto_dashboard/View.gs)
- [Triggers.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/10c6a78fade179da2739152f2f2678c14de39679/apps_script/crypto_dashboard/Triggers.gs)

既存の10ファイル設置済みなら、この5ファイルだけを適用し、保存後setupCryptoDashboardを実行する。追加の有料契約・APIキーは不要。会社側の設置結果は未確認。

## 初回本番収集と予算調整

PR #21をmain反映後、watch-crypto 37714927199が成功。2026-10-08 10:54:30 JSTの独立JSONは89関係中、検証済み55・取得範囲で証拠なし1・時間上限による未照会33、HTTP250要求。固化済みの成功receipt・ブロック収録・Transferまで確認できる実データを得た。同版の公式JSON・残高JSONとGAS検証/90行の表示を確認し、公式掲載関係ID・住所・first_seen・公式イベント・形式検証履歴の維持を確認。

履歴索引だけでなく2取得先へのreceipt照会と固化ブロック収録の照合にも時間を使うため、上限を180→300秒へ調整。時間/要求上限で未処理になった対象は次実行で直ちに再開し、API停止回路・通信失敗の1時間待ちと区別する。成功55件のキャッシュは保持する。PR #22（https://github.com/kenmizunokuro/sanctions-watch/pull/22）でmainへ反映し、予算と再開条件の独立レビューも指摘なしで通過した。


## 再収集と本番照合の完了

watch-crypto [37715782067](https://github.com/kenmizunokuro/sanctions-watch/actions/runs/37715782067) が成功。2026-10-08 11:03:31 JSTのJSON（データコミット `4636325eb4e2e104eacda9e60ad30a9a68718d6c`）は89掲載関係中、検証済み88（TRON 79・Ethereum 9）・取得範囲で証拠なし1。取得失敗・取得不足・未照会は0。残り33件をHTTP154要求で収集し、初回55件の成功キャッシュを保持した。次の自動実行37715805108も成功した。

現在の発行元トークン残高照会では確認できなかった44掲載関係（TRON 39・Ethereum 5）にも、過去の成功した送受信証拠を追加できた。履歴索引0件のUSDC `0x983a81ca6FB1e441266D2FbcB7D8E530AC2E05A2` は取得範囲で証拠なしと表示し、無効・未使用・所有者を断定しない。

同一データコミットの公式・残高・履歴JSONの原本SHA整合とGAS検証を確認。アドレス台帳1066行×24列、要確認161行×20列、トークン履歴検証90行×26列（いずれも見出し含む）、公式差分は見出しのみ。掲載関係1065・ユニーク住所1063・形式要確認160（不整合2、検証制限158）は維持。掲載関係ID・住所・通貨・初回収録日時・公式イベント・検証履歴が初回本番データから変わっていないことを照合済み。

無料公開RPCが応答する範囲の補足証拠であり、全履歴の網羅や住所所有者を証明しない。会社側の設置・権限・実シート表示は利用者の適用後に確認する。
