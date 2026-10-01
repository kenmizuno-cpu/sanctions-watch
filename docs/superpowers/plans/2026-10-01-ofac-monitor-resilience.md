# OFAC遅延対策の実装計画

設計: `docs/superpowers/specs/2026-10-01-ofac-monitor-resilience-design.md`。

1. 最新mainから独立worktreeを作成し、既存Python/Nodeテストを確認する。
2. 専用GASの実行可能テストを先に作成する。鮮度判定、月またぎ、エラー、重複抑制、dispatch失敗、通知・復旧・セットアップを検証し、REDを確認して実装する。
3. OFAC attempt記録と失敗時の限定コミット処理をテスト先行で実装する。ローカルbare gitを用いて、失敗時にもmaster/stateを保存しないことを確認する。
4. 一時HTTP障害の再試行とSlackの送信結果をテスト先行で実装する。
5. 導入手順とCIを更新し、全Pythonテスト、既存・新規GASテスト、diffチェックを実行する。
6. GitHubに変更とPRを作成する。検証結果・有効化手順・未完了のGAS所有者操作を明記する。
