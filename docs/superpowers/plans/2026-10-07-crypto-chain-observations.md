# 公開チェーン照会の実装計画（承認済みの追加検証）

ユーザーの「よし頼む」で、直前に提示した公開アドレスの追加照会を実装する。会社のシート設置はユーザーが行う。GitHub側で照会し、GASはSHA固定の公開JSONを表示する。

## 設計・制約
- 公式情報・掲載関係ID・初回収録・形式検証・公式差分は変更しない。チェックサム欠落は照会しても解消しない。
- 既存の検証制限のうちUSDT/USDC、ETH/ETC/BSC/ARB、SOLを対象にする。現在158関係。記号から一意に決められないEVMトークンは複数チェーンを照会する。
- モジュール分割：registry（発行元契約とチェーン）、transport（上限・HTTP/RPC）、evm/tron/bitcoin/solana（応答検証）、runner（キャッシュ・状態・保存）。GASの追加表示と照会JSON検証はChain.gsに分離。
- 発行元一次資料で確認した契約のみ照合。USDTはEthereum/Avalanche/TRON、USDCはEthereum/Arbitrum/Base。他チェーン・Omniの資産自体の検証は範囲外として明記。BTC活動をUSDTの活動とは呼ばない。
- 各取得先の時刻・ブロック/slot・確認範囲・残高（整数文字列）・nonce/最近の取引参照・失敗を保存。EVM nonceは総取引数ではない。SOLはアカウント参照記録であり送信者/所有者を意味しない。
- 取得先は複数、運営者が同じ場合独立根拠と数えない。結果差異はCONFLICT、取得漏れはPARTIAL/FAILED/DEFERRED。ゼロはNO_EVIDENCE、非存在・無効・所有者を断定しない。
- 最新値のみの別JSON（最大2MiB）。旧成功値は失敗時に最終成功として保持。6時間成功キャッシュ、失敗は1時間後再試行。有限HTTP回数/時間予算、provider連続失敗で停止。固定公開エンドポイント以外へアクセスせず、リダイレクトを拒否。鍵・顧客データ不要。
- GASは同一コミットの2 JSONを取得。照会JSONの取得失敗でも公式情報は更新し、照会失敗を新しいタブへ表示。原本hashが変わってもID+公式address+symbolが一致する関係だけ結合し、照会元hashも残す。

## Task 1: 照会と保存
Interfaces: runnerが schema_version=1 / rows[].relation_id,address,symbol / probes[].chain,provider,status,attempted_at,last_success を公開する。GASはこの形式を検証する。
1. 整数精度、偽契約排除、ゼロ・RPC失敗・プロバイダー差異、キャッシュ失敗時の旧成功保持、公式情報不変、上限をテストとして書く。
2. `python -m unittest tests.test_crypto_chain` → Expected: 未実装の失敗を確認。
3. 機能別モジュールを実装し同コマンド → Expected: 全成功。
4. watch-cryptoへ有限予算の照会工程を追加（抽出成功時のみ）。

## Task 2: GAS表示
Interfaces: Task 1のschema。公式snapshotに chain_data / chain_error を付け表示のみで利用。
1. SHA一致、壊れたJSON・関係不一致拒否、失敗を隠さない、旧版互換、要確認/公式差分不変をGASテストへ追加。
2. `node tests/test_crypto_dashboard.cjs` → Expected: 未実装の失敗。
3. Chain.gs追加、Fetch/Config/View/Triggersに配線。チェーン検証タブへ取得先別の結果・未確認の意味を日本語で表示。
4. 同コマンド → Expected: 全成功。

## Task 3: 検証と引継ぎ
1. 実API照会（有限予算）。現在の陽性・未観測・差異・失敗を数え、公式履歴不変とファイルサイズを確認。
2. Python全件、offline/e2e/MOF、全GAS、diff check → Expected: 全成功。
3. 独立レビュー、重要指摘は再現テストRED→GREENで修正。
4. PR/CI成功を確認して統合。本番workflowと公開JSONを確認。手順・ロードマップ更新、同一コミットの変更GAS全文リンクを渡す。

## Review Focus
空応答・HTTP200内エラー・不正整数・偽コントラクト・違うチェーン・一時失敗・古い最終成功・provider間差異・過大JSON・未知関係・複数チェーン・予算枯渇。公式掲載と活動・所有者を混同する表示がないか。

## 完了記録

Task 1/2/3完了。Python427、GAS52、offline213、e2e9、MOF検証成功。独立レビュー指摘を再現テストで修正。PR18/19のCI、main統合、2回の本番workflow成功を確認。最終340取得先中265成功、152関係で記録/残高、43関係で発行元資産照合。公式情報/履歴不変、公開JSONのGAS全340行を検証。会社側の全文リンク5ファイルとsetup手順をdocs/crypto-chain-observations-20261007.mdへ引き継ぐ。残る未観測・API失敗は無効とせず、取得先別に表示する。
