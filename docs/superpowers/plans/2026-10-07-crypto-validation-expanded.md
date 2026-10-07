# 暗号資産の追加検証 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 未対応通貨・USDT/USDCの形式を自動検証し、不整合と検証制限を区別する。

**Architecture:** 通貨ごとの検証器を追加し、元の掲載関係IDを作るnetwork/normalized_addressは旧仕様のまま保持する。検証で得た候補はnetwork_candidates/network_resolutionとして別に保存する。検証変更は既存の検証専用履歴で監査する。

**Tech Stack:** Python標準ライブラリ、既存PyCryptodome Keccak、既存Bech32参照実装、Google Apps Script。

**Spec:** ユーザー承認済み「残りの通貨の検証器とトークンの形式検証、不整合と検証制限の表示分離」。

## Global Constraints
- 会社のスプシにはユーザー自身が設置する。ここで同じコードを更新する。
- 機能ごとにファイルを分ける。既存9個のGAS構成を維持する。
- 公式原文、通貨記号、掲載関係ID、first_seen、公式掲載履歴を保持する。
- ネットワーク候補から実ネットワーク、所有者、活動有無は断定しない。
- 配布schema_version=1、旧format_review集計と5MB上限を維持する。

## Review Focus
- チェックサム正常でも通貨記号と不整合なら自動的に通貨を修正しない。
- SolanaのPDA・チェックサムなし形式と単一大小文字EVMを検証済みと誤表示しない。
- ネットワーク推定で既存IDや公式差分を増やさない。
- 既知通貨の未実装アドレス種別を誤ってINVALIDにしない。
- 旧JSON互換性と候補/集計の異常値の拒否を維持する。

### Task 1: 通貨別検証と安定した識別
**Files:** Create src/crypto_validation/{cashaddr,monero,solana,xrp,utxo,binance,tokens,identity}.py; Modify __init__.py/common.py; Test tests/test_crypto_validation_expanded.py and test_crypto_validation.py.
**Interfaces:** normalize_address(symbol,value)->dict。network_candidates:list[str], network_resolution:str, review_category:strを追加。
- [x] 固定仕様例、文字変異、ネットワーク・長さ・接頭辞・大小文字・チェックサムの失敗テストを書く。
- [x] テスト失敗を確認する。
- [x] 通貨別検証器と旧IDの保持を実装する。
- [x] テスト成功と実原本1065掲載関係のID維持を確認する。

### Task 2: 分類と監査・表示
**Files:** Modify src/crypto_watch.py, apps_script/crypto_dashboard/{View,Validate}.gs; Test tests/test_crypto_watch.py/test_crypto_dashboard.cjs.
**Interfaces:** counts.inconsistency_review/validation_limitations/unsupported_reviewはformat_reviewを分割する任意追加項目。
- [x] 分類の集計、候補表示、旧配布版・不正メタデータのテストを書く。
- [x] 失敗後に分類と入力検証を実装し、全体テストを行う。
- [x] 実原本再検証と再実行ゼロ差分、5MB未満を確認する。

### Task 3: 公開と引き継ぎ
**Files:** Update docs/crypto-addresses.md, docs/roadmap-20261006.md; Create docs/crypto-validation-expanded-20261007.md.
- [ ] 全体テスト後に独立した読み取り専用レビューを受ける。
- [ ] GitHubにPRを作成し、CI成功・レビュー確認後にマージする。
- [ ] 本番watch-cryptoの成功とID・履歴維持を検証する。
- [ ] 更新GASの完全版リンクとsetupCryptoDashboard再実行手順を案内する。
