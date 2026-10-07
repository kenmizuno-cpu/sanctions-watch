# Crypto-1：Bitcoin・Ethereum自動検証の更新

## 採用した範囲

会社のスプレッドシートで監視し、共通コードはこのWorkとGitHubで更新する方針を継続する。GitHubの収録処理へBitcoin・Ethereumのアドレス検証を追加し、検証方法・結果・残る確認理由を表示する。外部のアドレス照会サービスは使用せず、保存済み公式原本から検証する。取引履歴・所有者・送金関連性の判定は今回の範囲に含めない。

## 機能別ファイル

| ファイル | 担当 |
| --- | --- |
| src/crypto_validation/__init__.py | 通貨別の振り分けと未対応通貨の保持 |
| src/crypto_validation/common.py | 結果形式・検証版・従来のBase58Check |
| src/crypto_validation/bitcoin.py | Bitcoin mainnetのSegWit検証と既存形式 |
| src/crypto_validation/bech32.py | BIP-173／350の公開参照実装。MIT許諾を保持 |
| src/crypto_validation/ethereum.py | Ethereum EIP-55とチェックサム情報の有無 |
| src/crypto_ledger.py | 掲載関係の履歴と検証更新の分類 |
| src/crypto_watch.py | 公式掲載差分と検証更新履歴の分離・永続化 |
| apps_script/crypto_dashboard/Validate.gs | 新しい検証履歴の整合性確認、旧配布版との互換性 |
| apps_script/crypto_dashboard/View.gs | 日本語の検証方法・詳細・版の表示 |

Bitcoinの検証は接頭辞・文字・大小文字・長さ・余剰ビット・witness版・チェックサム方式を確認する。既存の掲載関係IDを維持するため、原文の大小文字を変更しない。Ethereumは既存の小文字正規化を維持し、原文のチェックサムを検証する。全小文字・全大文字・数字だけの原文には誤記検出の大小文字情報がないため、形式確認のみとする。

Ethereumのハッシュ処理はPyCryptodome 3.23.0のKeccak-256を使用する。依存はrequirements-crypto.txtへ固定し、収録workflowと通常のrequirements.txtから導入する。検証依存が利用できないときは収録を失敗表示にして旧台帳を保持する。SHA3-256で代用しない。

## 履歴と互換性

公式の名前・プログラム・原文アドレスが変わった場合は従来の変更履歴へ記録する。検証結果や確認理由だけが変わった場合はREVALIDATEDとして分類し、配布JSONのvalidation_eventsへ記録する。公式掲載のeventsには入れず、公式差分の件数へ加算しない。検証履歴は変更前後の結果・理由、検証方法・版、原本ハッシュ、掲載関係IDを保持する。

既存の公式イベント鎖last_event_idは検証更新で変更せず、last_validation_event_idを別に管理する。同じ原本と検証器の再実行で履歴を重複させない。新しいJSON項目は追加形式であり、旧Apps Scriptも従来の掲載差分を取得できる。新しい表示・検証履歴確認には今回の2ファイル更新が必要。

## 保存済み原本での確認

原本SHA256：1919d7ccdad5a6a4542ca196b746613ae7cdf218b94192ab77cf04f06b9793dd

| 項目 | 更新前 | 更新後 |
| --- | ---: | ---: |
| 公式掲載関係 | 1065 | 1065 |
| ユニークアドレス | 1063 | 1063 |
| 形式・ネットワーク要確認 | 398 | 187 |
| Bitcoin SegWitのチェックサム未検証 | 146 | 0 |
| Ethereumのチェックサム検証済み | 0 | 65 |
| Ethereumのチェックサム情報なし | 未区分 | 55 |

要確認から211件をチェックサム検証済みへ変更した。Ethereum55件とネットワーク未確定・未対応等は残す。Bitcoinの従来形式のチェックサム不正1件も残す。267件の検証結果・理由更新を専用履歴へ記録し、新しい公式差分は0件。掲載関係IDと初回収録日時は全件保持した。再実行の新公式差分・新検証更新はともに0件。

原本を再取得した件数や会社のシートを実査した結果ではない。会社側の設置・差し替え・動作確認は利用者が行う。

## 会社側の更新

1. 同じ版のValidate.gsとView.gsを全文差し替える。他の7つの.gsとマニフェストは変更不要。
2. 保存してsetupCryptoDashboardを再実行する。初回同期、新しい列の書式設定、15分トリガーの設定を行う。既存の設定タブの値は保持する。以後は「アドレス監視 → 今すぐ同期」でも更新できる。
3. 「アドレス台帳」「要確認」に検証方法・詳細・版が増え、同期履歴が成功となることを確認する。
4. この同一原本なら要確認187件となる。将来公式原本が変わる場合は件数を固定せず、その版の検証結果で確認する。

## 検証結果とGit反映

- ローカルのPython全390テスト、Apps Script全46テストが成功。
- 独立コードレビューでCritical／Important／Minorの指摘なし。検証依存欠損時の旧台帳・両履歴・最終成功日時保持も確認。
- GitHub CI：watch-mofa run 37563396718とwatch-meti-manual-sla run 37563396717が成功。
- [PR #16](https://github.com/kenmizunokuro/sanctions-watch/pull/16)をmainへ反映。コード反映SHA：b306b5e84300a200183a506a81358b34253e2a8e。

- 本番watch-crypto run 37563486454が成功。収録・台帳保存まで成功。
- 本番配布JSONの実行日時2026-10-07T02:45:28Z（11:45:28 JST）。要確認187、掲載関係1065、ユニーク1063、新公式差分0、新検証履歴267。原本ハッシュ、掲載関係ID、初回収録日時、既存公式履歴を更新前と照合して保持を確認。

## 根拠

- [BIP-173](https://github.com/bitcoin/bips/blob/master/bip-0173.mediawiki)
- [BIP-350](https://github.com/bitcoin/bips/blob/master/bip-0350.mediawiki)
- [公開参照実装と固定テスト](https://github.com/sipa/bech32/tree/master/ref/python)：著作権・MIT許諾をコードとテストへ保持。実行時にコードをダウンロードしない。
- [EIP-55](https://eips.ethereum.org/EIPS/eip-55)：公式の混在文字固定例と、誤った大小文字をテストする。
- [PyCryptodome Keccak](https://pycryptodome.readthedocs.io/en/stable/src/hash/keccak.html)
