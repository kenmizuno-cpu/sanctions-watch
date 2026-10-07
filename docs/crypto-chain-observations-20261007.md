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
- 成功値は6時間キャッシュ。失敗・未照会・Transferの部分失敗は1時間後に再試行。毎回最大360秒・1200 HTTP要求、応答512KiB、照会JSON2MiB。同じ取得先の3連続HTTP/API失敗でその実行中の照会を停止する。公開APIの可用性やレート制限により複数実行に分かれることがある。
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
- https://llamarpc.com/eth
- https://docs.coregeth.com/etc-cooperative-transition/
