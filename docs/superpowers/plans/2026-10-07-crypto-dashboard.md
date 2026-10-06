# 暗号資産アドレス監視 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** OFAC明示アドレスの収集と別シートの監視を機能別コードで提供する。
**Architecture:** 保存済み原本 → 抽出 → 掲載関係台帳 → 単一版JSON → 専用Sheets。
**Tech Stack:** Python標準ライブラリ、既存atomic_replace_many、Apps Script、Node VMテスト。
**Spec:** docs/superpowers/specs/2026-10-07-crypto-dashboard-design.md

## Global Constraints

- 既存の名前監視・監視頻度・外務省／経産省通信禁止を維持する。
- 公式掲載と社内対応判断を分離。自動凍結・解除なし。
- 原本ハッシュ不一致と20%以上の急減で前台帳を保持する。
- 同期上限5MB・5000台帳行。警告120分、重大360分。

## Review Focus

- エラー表示の新しさが原本確認日時を進めてしまわないこと。
- 部分書込みが成功版として残らないこと。
- 通貨記号から不明ネットワークを決めつけないこと。
- 再実行や再掲載で差分が失われたり増殖しないこと。
- 共通concurrencyと最新mainへの更新が既存監視出力を上書きしないこと。

### Task 1: 抽出・台帳・配布

Files: src/crypto_addresses.py, src/crypto_ledger.py, src/crypto_watch.py, tests/test_crypto_addresses.py, tests/test_crypto_ledger.py, tests/test_crypto_watch.py。
Interfaces: extract(stream) -> (rows, report); reconcile(previous, incoming, now, source_hash, parser_version) -> (rows, events); run(root, now) -> dashboard dict。
- [x] 抽出と版比較の失敗テストを書く。参照表のIDを変えても原文とFixedRefを正しく抽出すること、初回・消失・再出現のイベント数を固定。
- [x] `python -m unittest tests.test_crypto_addresses tests.test_crypto_ledger tests.test_crypto_watch`でRED確認。
- [x] 3モジュールを実装、同コマンドでGREEN確認。
- [x] 実原本で行数・通貨・要確認・原本ハッシュを監査し、同原本の再実行でイベント数が変わらないことを確認。

### Task 2: 機能別GAS

Files: apps_script/crypto_dashboard/{Config,Fetch,Validate,Store,View,History,Sync,Triggers,Menu}.gs, appsscript.json, tests/test_crypto_dashboard.cjs。
Interfaces: caValidateSnapshot_(json) -> snapshot; caBuildTables_(snapshot) -> {sheetName: matrix}; syncCryptoDashboard(); setupCryptoDashboard()。
- [x] 不正JSON／重複ID／SHA固定／数式無害化／途中書込み失敗のREDテストを書く。
- [x] NodeのテストでRED確認後、9ファイルを実装。
- [x] 同コマンドでGREEN確認。手動設定と同期管理領域を分離。

### Task 3: 接続・文書・公開

Files: .github/workflows/watch-crypto.yml, docs/crypto-addresses.md, docs/roadmap-20261006.md, README.md。
- [x] 独立workflow・日本語手順・更新ロードマップを追加。
- [x] Python全テストと既存／追加GASテストを実行。
- [x] 新規Google Sheetsに実原本スナップショットを書込み、件数と状態を読戻し確認。
- [ ] Gitブランチ・PRを公開。独立レビューと指摘修正は完了。移管後のApp経由ブランチ作成に成功、PR公開を続行。

## 実施結果

Python 372件、Node 38件が成功。実原本の初回抽出と再実行、Sheets全7タブの件数と表示状態のAPI読戻し、独立コードレビューと2件の修正を実施。実際のApps Script設置・Google権限承認・定期トリガー有効化は未実施で、利用者の初回セットアップとして手順に記載。

接続障害はkenmizunokuro組織への移管により解消。repository ID/Public設定とAppの対象追加、実際のブランチ作成成功を確認。PR公開とmain統合後のworkflow確認を続行。
