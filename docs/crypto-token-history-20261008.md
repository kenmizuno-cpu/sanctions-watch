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

前回の成功記録は消さず、今回失敗・期限超過・最終成功日時を併記する。成功は6時間、失敗/取得不足は1時間後に再照会。180秒/600要求、HTTP本文512KiB、JSON2MiB。TRON索引は1秒以上間隔、Blockscoutも1秒以上間隔、既存RPCの停止回路を継承する。時間上限で未処理となった対象は次の実行で追い、成功キャッシュの対象に再要求しない。

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

実装前の失敗テストから、偽契約・別住所・不正ABI・ゼロ金額・失敗receipt・未確定高さ・ブロックhash差異・取引未収録・API欠損・予算枯渇・キャッシュ失効・旧成功保持・公式JSONの保護を確認。GASは件数・原本SHA・掲載関係・契約・状態・証拠・正確な整数を検証する。全体テスト、独立レビュー、GitHub CI、本番JSONは最終確認後に追記する。
