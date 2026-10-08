# 過去のトークン送受信の追加検証

ユーザー承認：2026-10-08「よしやってくれ」。無料の過去履歴照会と取引結果の裏取りを実装し、GitHubで継続運用する。会社のSheetsへの設置はユーザーが行う。

- 対象：既存の追加照会対象のうちTRON USDT・Ethereum USDT/USDC（89掲載関係）。他チェーン・Omni・所有者判定・形式制限の解除は含めない。
- 履歴索引：TronGrid（only_confirmed=true、最新20件）とEthereum Blockscout（最初の最大50件）。次ページURLは追わない。全履歴の網羅を主張しない。
- 索引は候補に限定する。最大3取引を確認し、最初の1件の証拠を保存。候補0件と通信失敗を区別する。
- TRONはmainnet genesisを確認したSolidityNodeの成功receiptと固化ブロックへの収録を確認。EthereumはchainId=1、成功receipt、finalized高さ以下、正規ブロックhash・取引収録を確認。
- Transferイベントの発行元コントラクト・送受信アドレス・正の金額を厳密照合。トークン名や第三者ラベルを根拠にしない。
- 別JSON token_history.jsonへ保存。公式情報・チェーン残高JSON・掲載関係ID・差分・形式検証記録を変更しない。失敗時は旧成功値と今回失敗を併記。
- 成功6時間、失敗/検証不足1時間の再照会待ち。API 300秒/600要求、本文512KiB、配布JSON2MiB、既存の停止回路・匿名回数制限を継承。
- 機能毎のPythonファイルと独立GAS TokenHistory.gsを使用。取得/検証/表示を分離し、専用「トークン履歴検証」タブを追加する。GASからチェーンAPIを呼ばない。
- 同一GitHub SHAの公式JSONと履歴JSONを読み、住所・通貨・掲載関係と件数を検証する。旧成功・期限超過・取得失敗を明示する。

一次資料：TRON Solidity receipt https://developers.tron.network/reference/gettransactioninfobyid-1 、履歴 https://developers.tron.network/reference/get-trc20-transaction-info-by-account-address 、Ethereum JSON RPC https://ethereum.org/developers/docs/apis/json-rpc/ 、Blockscout REST https://docs.blockscout.com/devs/apis/rest 。発行元契約は既存の一次資料確認済みallowlistを利用。
