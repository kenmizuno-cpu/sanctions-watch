# Crypto Token History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 残高ゼロでも過去の発行元トークン送受信を無料APIと確定取引結果で検証する。
**Architecture:** 履歴索引から取得した候補をmainnet RPCのreceiptとブロックで裏取りし、独立したtoken_history.jsonへ保存する。既存GASへ独立表示モジュールを追加する。
**Tech Stack:** Python標準ライブラリ、既存のBase58/Transport、GAS、unittest/Node。
**Spec:** docs/superpowers/specs/2026-10-08-crypto-token-history.md

## Global Constraints
- 無料・公開OFACアドレスのみ、秘密鍵・APIキー不要、取引送信なし。
- TRON USDT・Ethereum USDT/USDC、最大3候補/1証拠、300秒/600要求、配布JSON2MiB。
- 公式判定・形式検証・ID・監査履歴は保持。索引0件とAPI失敗は区別。

## Review Focus
- 偽同名トークン、別住所、ゼロ金額、失敗receiptは証拠にしない。
- 別ネットワーク、未確定、ブロックhash違い・取引未収録を拒否。
- 索引やRPC欠損と停止回路を「証拠なし」にしない。
- 再照会失敗や設定変更による古い証拠の誤表示を防ぐ。
- GASはJSONの件数・住所・通貨・契約・状態・証拠整合性を検証する。

### Task 1: 索引と取引証拠
**Files:** Create src/crypto_history/{__init__,registry,transport,events,tron,evm}.py; tests/test_crypto_token_history.py.
**Interfaces:** observe(target,address,http) -> observation(state,candidates_count,checked_candidates,proofs,has_more,scope)。証拠は厳密なTransfer・確定ブロック・receiptを含む。
- [x] 失敗テスト：偽契約/住所/金額・不正receipt/ブロック・欠損・停止回路・候補上限。
- [x] python -m unittest tests.test_crypto_token_history → 欠落実装でFAIL確認。
- [x] 固定API・厳密デコーダ・TRON/Ethereum照合を実装。
- [x] 同コマンド→PASS、既存chainテスト→PASS。
- [x] commit feat(crypto): verify historical issuer token transfers。

### Task 2: 独立配布と自動実行
**Files:** Create src/crypto_history/runner.py, src/crypto_token_history_watch.py; Modify .github/workflows/watch-crypto.yml.
**Interfaces:** collect(snapshot,previous,now=None,http=None,observe=None) -> schema1 JSON。CLI run(root,seconds=300,max_calls=600)は成功原本だけで原子的保存。
- [x] 失敗テスト：6時間キャッシュ・1時間再試行・旧成功保持・住所/契約変更・上流失敗・原子的保存・有限要求。
- [x] 対象テスト→FAIL確認。
- [x] キャッシュ・独立JSON・workflowの収録後照会を実装、job timeout=15分。
- [x] 対象テスト＋crypto全テスト→PASS。
- [x] commit feat(crypto): publish bounded token history evidence。

### Task 3: GAS表示・引継ぎ
**Files:** Create apps_script/crypto_dashboard/TokenHistory.gs; Modify Config.gs, Fetch.gs, View.gs, Triggers.gs, tests/test_crypto_dashboard.cjs, docs/crypto-addresses.md, docs/roadmap-20261006.md; Create docs/crypto-token-history-20261008.md.
**Interfaces:** caValidateTokenHistory_(d,s) -> validated JSON; caTokenHistoryTables_(tables,s,now) -> tables。snapshot.token_history_data / token_history_error。
- [x] 失敗テスト：正しい証拠の表示、不正契約/住所/状態/件数拒否、旧成功/期限超過/取得失敗、公式差分維持。
- [x] node tests/test_crypto_dashboard.cjs → 新機能欠落でFAIL。
- [x] SHA固定取得、独立検証・専用タブ・日本語表示、設置手順を実装。
- [x] unittest全件・GAS全件・offline/e2e・git diff --check→PASS。
- [ ] 独立レビュー→修正→PR/CI→既承認のmerge→本番JSON/GAS照合。
- [x] commit feat(crypto): display verified historical token evidence。
