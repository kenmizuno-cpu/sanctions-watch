# 公開チェーンの追加検証（2026-10-07）

対象はOFACの公開アドレスのうち、既存検証で制限が残るUSDT/USDC、ETH/ETC/BSC/ARB、SOL。現在158掲載関係。照会結果は `data/crypto/chain_observations.json` に独立保存し、公式台帳・ID・初回収録・公式差分・形式判定は変更しない。

## 見る場所

`チェーン検証` タブに取得先ごとの残高、発行元コントラクト、nonce、BTC確認済み取引数、SOL取引参照、EVMトークンの直近256ブロックのTransfer参照、照会日時、最終成功日時、失敗を表示する。`アドレス台帳` と `要確認` の末尾にも追加照会の全体判定を表示する。

| 表示 | 意味・次に見る点 |
|---|---|
| 記録・残高を観測 | 対象チェーンに補足記録がある。公式の意図したチェーンや所有者は確定しない |
| 照会範囲で未観測 | 取得範囲で正の残高・記録がなかった。無効・非存在・過去の取引なしとは判定しない |
| 取得先で観測結果が異なる | 同じチェーンで観測有無が異なる。各時刻・ブロック・履歴収録範囲を確認 |
| 一部取得失敗・範囲不足 | 一部の取得先または最近のTransfer照会が失敗。成功した記録は保持 |
| 取得失敗・未照会 | 最新照会の成功なし。エラー、予算枯渇、最終成功値の鮮度を確認 |

残高は最小単位の整数文字列として表示する。桁数6なら1,000,000が1トークン。大きな整数を浮動小数点へ変換しない。EVM nonceは送信回数の指標で、入金を含む総取引数ではない。SOLは成功したfinalized取引中でのアカウント参照で、送信者・所有者を意味しない。失敗した参照記録は成功取引の証拠と数えない。

発行元資産の照合はTetherのEthereum/Avalanche/TRON USDTと、CircleのEthereum/Arbitrum/Base USDC。USDT/USDCのEVM形式はこれら複数チェーンを照会する。他の発行チェーンやブリッジ版の資産を同じものと扱わない。Bitcoin形式USDTはBTC確認済み履歴までで、Omni property31の資産検証は未対応。トークン残高ゼロと限定期間のTransferなしでは過去保有を否定できない。

## 処理・保守

- GitHub `watch-crypto` が保存済み公式原本の収録成功後に実行。会社のApps ScriptはチェーンAPIへアクセスせず、同じGitHubコミットの公式JSONと照会JSONを取得する。
- 成功値は6時間キャッシュ。失敗・未照会・Transferの部分失敗は1時間後に再試行。毎回最大360秒・1200 HTTP要求、応答512KiB、照会JSON2MiB。TronGridは要求開始間隔1秒、dRPCは0.4秒を置く。待機も時間予算に含める。同じ取得先の3連続HTTP/API失敗でその実行中の照会を停止する。公開APIの可用性やレート制限により複数実行に分かれることがある。
- 失敗時に前回成功値を保持し、今回失敗と最終成功日時を同時表示。6時間超の値は「期限超過」。配布日時を値の成功日時と誤認しない。
- 照会JSONを取得できなくても公式台帳の同期は継続し、照会タブにエラーを表示。古いチェーン表を成功として残さない。
- 結合は掲載関係IDだけでなく公式address/symbolも一致させる。照会元の原本SHA256も表示。新しい掲載関係は次のGitHub照会まで対象外・未照会。
- `src/crypto_chain/registry.py` に取得先と発行元契約をまとめる。EVM/TRON/SOL/BTC、HTTP、保存・集計を別モジュールに分割。公開先の変更は一次資料を確認してregistry_versionを更新し、古いキャッシュを無効化する。
- API鍵不要の公開エンドポイントを利用。特にTronGridは匿名照会の拒否・厳しい制限があり、失敗も記録する。鍵の導入は別途GitHub Secretsで行う拡張で、会社シートや公開リポジトリへ鍵を書かない。

## 会社側のコード更新

同一リリースの `apps_script/crypto_dashboard/` から以下を適用する。

1. 新規 `Chain.gs` を作成し、全文を貼る。
2. `Config.gs` / `Fetch.gs` / `View.gs` / `Triggers.gs` を同版の全文で差し替える。
3. 保存して `setupCryptoDashboard` を実行。既存設定を保持し、新しい `チェーン検証` タブと列を作り、同期・書式・既存15分トリガーを更新する。
4. `監視ダッシュボード` のチェーン照会件数、`チェーン検証` の今回取得・最終成功・鮮度・エラーを確認する。

他の.gs、会社の独立したCode.gs/OfacWatchdog.gsはこの更新の対象ではない。会社側への適用と実環境の実行は利用者が行い、GitHubの更新だけでは自動反映されない。

## 一次資料（2026-10-07確認）

- https://tether.to/en/supported-protocols/
- https://developers.circle.com/stablecoins/usdc-contract-addresses
- https://ethereum.org/developers/docs/apis/json-rpc/
- https://developers.tron.network/reference/triggerconstantcontract
- https://developers.tron.network/reference/solidity-node-http-api-overview
- https://developers.tron.network/reference/rate-limits
- https://solana.com/docs/rpc/http/getsignaturesforaddress
- https://github.com/Blockstream/esplora/blob/master/API.md
- https://www.publicnode.com/
- https://drpc.org/docs/ethereum-api
- https://drpc.org/chainlist/ethereum-classic-mainnet-rpc
- https://ethereumclassic.org/network/endpoints/
- https://docs.coregeth.com/etc-cooperative-transition/

## 検証記録と差し替え用の全文

PR #18: https://github.com/kenmizunokuro/sanctions-watch/pull/18
実装main: 8d4512f87eaa6bcb80108552038c7a1018f3576c

Python全424件、GAS全52件（暗号資産21・外務省9・OFAC自己監視22）、offline213項目、e2e9項目、MOF差分/再審査に成功。独立レビューの4件は再現テストRED→GREENで修正。PRのwatch-mofa（37598302276）とwatch-meti-manual-sla（37598302192）は成功。公式台帳・履歴への変更なしを確認。

ローカルの有限実照会では158関係・340取得先のうち1取得先のBTC履歴取得が成功、1関係PARTIAL・157関係FAILED（未照会を含む）。接続拒否・タイムアウトで共有時間予算が枯渇した。別JSONは129,295バイトで、GAS検証と340行の表示を確認。この数値を本番の到達性や自動検証の解決件数と扱わない。

同一コミットの5ファイル（全文）:

- [Chain.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/8d4512f87eaa6bcb80108552038c7a1018f3576c/apps_script/crypto_dashboard/Chain.gs)
- [Config.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/8d4512f87eaa6bcb80108552038c7a1018f3576c/apps_script/crypto_dashboard/Config.gs)
- [Fetch.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/8d4512f87eaa6bcb80108552038c7a1018f3576c/apps_script/crypto_dashboard/Fetch.gs)
- [View.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/8d4512f87eaa6bcb80108552038c7a1018f3576c/apps_script/crypto_dashboard/View.gs)
- [Triggers.gs](https://raw.githubusercontent.com/kenmizunokuro/sanctions-watch/8d4512f87eaa6bcb80108552038c7a1018f3576c/apps_script/crypto_dashboard/Triggers.gs)

### 初回本番照会

watch-crypto 37598417516は全工程成功。配布コミット6a6d2041ccfb7abb1ccec8d11c7fdd4909912a79、照会開始2026-10-07 18:07:42 JST。158関係・340取得先、成功200、全体判定POSITIVE24/PARTIAL132/FAILED2。152関係で少なくとも一つの取得先から記録または残高を観測、43関係で発行元トークンを照合。残りを無効と判定しない。JSONは227,957バイト。SHA固定の公開JSONをGAS検証に通し、340行を表示できることを確認。公式1065関係/1063ユニーク・要確認160・不整合2/制限158は維持、ID/原文/初回収録/公式履歴/検証履歴は不変。

本番で判明したHTTP429に対して呼出し間隔を追加し、EthereumのHTTP525取得先とETCの停止済み取得先を差し替える。dRPCのETC短縮URLはeth_chainId=0x3d（61）を実照会確認し、ベンダーの公開チェーン設定でもEthereum Classicの提供を確認。既存の成功値は再照会せず保持し、変更された取得先だけキャッシュが無効になる。TronGridの直前の失敗は既定通り1時間後に再試行する。
