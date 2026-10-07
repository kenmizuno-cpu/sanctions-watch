# 暗号資産の追加検証（2026-10-07）

第1段階のBitcoin/Ethereum検証後に残った187件について、未対応通貨とトークンの形式検証を追加する。会社のシートには利用者が設置し、このWorkで共通ソースを更新する。

## 追加した検証器

| ファイル | 検証範囲 |
| --- | --- |
| cashaddr.py | BCH CashAddrの40ビットchecksum、hash長、padding、大小文字、mainnet接頭辞（省略を含む）。legacy Base58Check |
| monero.py | 標準/サブ/統合アドレスの固有ブロックBase58、mainnet接頭辞、69/77バイト、Keccak-256先頭4バイト |
| solana.py | 32バイトBase58。曲線上でないPDAも排除しない。文字列checksumなしとして制限を明記 |
| xrp.py | classicの専用Base58文字表、account ID長、接頭辞、二重SHA256 |
| utxo.py | LTC SegWit、BSV/BTG/XVGのBase58Check、ZEC透明アドレスの2バイト接頭辞とchecksum |
| binance.py | 旧Beacon Chainのbnb/20バイト/Bech32。稼働状態は別 |
| tokens.py | USDT/USDCのEVM、TRON、Bitcoin、Solana系候補。実ネットワークは未確定 |
| identity.py | 旧network/normalized_address基準を維持。候補推定による掲載関係ID変更を防止 |

ETC/BSC/ARBも既存ethereum.pyの20バイト形式・EIP-55で検証する。単一大小文字をchecksum検証済みと扱わない。新しいライブラリ依存は追加しない。

## 判定と監査

- `review_category`: `INCONSISTENCY`（不整合）、`LIMITATION`（検証上の制限）、`UNSUPPORTED`（検証未対応）、空欄（要確認理由なし）。
- `network_candidates`: 形式と公式記号の対応による候補。`network_resolution` は `SYMBOL_AND_FORMAT`、`FAMILY_ONLY`、`UNRESOLVED`。実ネットワーク・所有者・活動有無の確定ではない。
- 古いIDの構成要素は固定。公式原文を自動補正せず、通貨記号を書き換えない。新規収録も同じID基準を使う。
- validator版は3。分類・候補の変更もREVALIDATEDとして監査し、公式eventsとは別のvalidation_eventsへ記録する。before/afterの候補・判定範囲・分類も保持。
- schema_version=1、旧format_review総数を維持。追加集計は任意項目として旧JSONと互換。GASでは候補構造・分類と集計の一致を検証する。
- 未対応のZEC shielded/unified/TEX、LTC MWEB、XRP X-address、CashAddr追加種別等はUNSUPPORTEDを残す。誤記と断定しない。

## 同一の保存済み公式原本による検証

原本 `data/raw/ofac_sdn_advanced/20261005T224323Z__sdn_advanced.xml.gz`、SHA256 `1919d7ccdad5a6a4542ca196b746613ae7cdf218b94192ab77cf04f06b9793dd`。

| 項目 | 第1段階後 | 追加後 |
| --- | ---: | ---: |
| 掲載関係 | 1065 | 1065 |
| ユニークアドレス（従来の識別基準） | 1063 | 1063 |
| 要確認合計 | 187 | 160 |
| 不整合 | 分類なし | 2 |
| 検証制限 | 分類なし | 158 |
| 検証未対応 | 分類なし | 0 |

27件で要確認理由を解消。158件はUSDT94、ETH55、SOL4、USDC2、BSC/ARB/ETC各1。USDT候補はTRON系79、EVM系8、Bitcoin系7。全候補で実ネットワークは未確定。

不整合2件は以下。原因・誤記・所有者を自動断定しない。

| 公式記号 | 対象者ID | 公式原文 | 結果 |
| --- | --- | --- | --- |
| XBT | 45404 | TUCsTq7TofTCJRRoHk6RvhMoS2mJLm5Yzq | Bitcoin形式と不整合。TRON系の接頭辞・checksumに一致するが原文/記号は保持 |
| XMR | 29585 | 5be5543ff73456ab9f2d207887e2af87322c651ea1a873c5b25b7ffae456c320 | 通常の95/106文字アドレス形式に不一致。別種の情報かどうかは公式原本確認 |

全1065件の掲載関係ID、first_seen、last_event_id、原文、旧network/normalized_address、原本内位置を照合し、変更なし。公式events全体も一致。validator版更新による検証専用履歴1065件、公式新規イベント0。再実行時は両方0。配布JSONは約4.10MBで既存5MiB上限未満。これは保存済み原本の再検証であり、公式サイトの新規取得完了を意味しない。

## 検証と会社側の更新

Python 407件、GAS 48件（暗号資産17・外務省9・OFAC watchdog22）。実JSONのGAS入力検証・表示行列も確認。独立レビューで検出した、Bitcoinに似たSolanaトークン形式の誤分類とZEC TEX/LTC MWEB/32バイトhexトークン種別の扱いも回帰テストで修正した。トークンはBitcoinテストネットの形式も系統候補として検証し、XBT mainnet判定とは分ける。独立レビュー・CI・本番収録の結果は公開後に追記する。

会社側は同じコミットの `apps_script/crypto_dashboard/Validate.gs` と `View.gs` を全文差し替え、保存後 `setupCryptoDashboard` を再実行する。設定値を保持し、新列の書式と15分トリガーを設定する。HTTP403が続く場合はFetch.gsのエラー全文で原因を切り分ける。本作業で403解消や会社側同期成功を未確認のまま主張しない。

## 一次仕様

- [CashAddr仕様と固定例](https://github.com/bitcoincashorg/bitcoincash.org/blob/master/spec/cashaddr.md)
- [Monero標準アドレス](https://docs.getmonero.org/public-address/standard-address/)、[統合アドレス](https://docs.getmonero.org/public-address/integrated-address/)、[接頭辞](https://github.com/monero-project/monero/blob/master/src/cryptonote_config.h)
- [Solanaアカウント構造](https://solana.com/docs/core/accounts/account-structure)
- [XRPLアドレス](https://xrpl.org/docs/concepts/accounts/addresses)、[文字表](https://github.com/XRPLF/xrpl-py/blob/main/xrpl/core/addresscodec/utils.py)
- [TRONエンコード](https://developers.tron.network/docs/encoding)
- [Litecoin](https://github.com/litecoin-project/litecoin/blob/master/src/chainparams.cpp)、[Bitcoin Gold](https://github.com/BTCGPU/BTCGPU/blob/master/src/chainparams.cpp)、[Verge](https://github.com/vergecurrency/verge/blob/master/src/chainparams.cpp)、[Zcash](https://github.com/zcash/zcash/blob/master/src/chainparams.cpp)
- [BNB SDK](https://github.com/bnb-chain/javascript-sdk/blob/master/src/crypto/index.ts)、[BSV SDK](https://bsv-blockchain.github.io/ts-sdk/reference/primitives/)
- [EIP-55](https://eips.ethereum.org/EIPS/eip-55)

- [Zcash TEX（ZIP320）](https://zips.z.cash/zip-0320)
- [Suiの32バイトアドレス](https://github.com/MystenLabs/sui/blob/main/docs/content/references/sui-api.mdx)（トークンはhex32系候補・チェーン別検証未対応として保持）
