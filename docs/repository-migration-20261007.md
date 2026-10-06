# sanctions-watchをkenmizunokuroへ移管

2026-10-07依頼。分析モデルと同じ組織へ統一する。正式リポジトリはkenmizunokuro/sanctions-watch。移管完了、Public設定、移管前と同一repository ID 1350902696、GitHub Appの対象追加を確認済み。App経由でブランチ作成に成功し、実際の書込みを確認した。

## 本人が実施したGitHubの移管操作

1. https://github.com/kenmizuno-cpu/sanctions-watch/settings を開く。
2. Generalの一番下にあるDanger ZoneでTransfer ownershipを選ぶ。
3. 新しい所有者をkenmizunokuro、リポジトリ名をsanctions-watchにする。表示された確認欄に従って確定する。公開設定は現状のPublicを維持する。公開JSONを取得するSheetsに必要。
4. 移管後のhttps://github.com/kenmizunokuro/sanctions-watch を開く。GitHub App接続は組織に設置済み・all repositories。対象として認識されることと、実際の書込み成功は別途検証する。

移管は本人のGitHub画面で実施済み。以下のGit・GAS運用更新は移管とは別工程。

## コードの準備済み変更

- READMEのActionsバッジ、tools/pull.shの既定リポジトリ。
- 既存の人物名ダッシュボードCode.gsのMOFA関連CSV URL。
- 既存OFAC自己監視OfacWatchdog.gsのAPI参照先。
- 新しい暗号資産ダッシュボードConfig.gsの既定リポジトリ。
- 導入手順と現行URLを使うテスト。過去の取得記録・操作者ID・証跡は変更しない。

## 移管後に確認・適用すること

- repository ID、所有者、Public設定、GitHub App経由の読み書き。
- ローカルoriginを新URLへ変更。
- 最新mainとの競合を解消して、準備済み暗号資産コードとURL変更をPRへ反映。
- OFAC・財務省のGitHub Actionsと公開CSV/JSONを確認。監視頻度と既存のMOFA_MONITOR_ENABLED設定を維持する。
- 既存のGASへ更新コードを設置。OFAC_WATCHDOG_REPOのScript Propertiesがある場合はkenmizunokuro/sanctions-watchに変更。APIはfollowRedirects:falseなので旧値のままでは転送応答を失敗として扱う。
- OFAC_WATCHDOG_TOKENが個人所有者向けのfine-grained tokenなら、所有者kenmizunokuro・対象sanctions-watchのトークンを作り、Apps Scriptのプロパティへ直接保存する（Actions: Read and write、Contents: Read-only）。トークン値はチャットに貼らない。
- 新規シートの設定タブのGitHubリポジトリを移管完了確認後に新URLへ変更し、Apps Scriptの設置とsetupCryptoDashboardを実行する。

Macのローカルorigin変更は移管後に実施：

```bash
cd "$HOME/Desktop/sanctions-watch"
git remote set-url origin https://github.com/kenmizunokuro/sanctions-watch.git
git remote -v
```

参考：
https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
