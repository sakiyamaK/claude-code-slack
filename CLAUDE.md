# claude-code-slack — Claude 向けガイド

このフォルダを開いた利用者が「セットアップして」「動かして」と頼んだら、以下に従って支援する。
（このツールは Slack から自分の Mac の Claude Code を動かす中継。詳細は README.md / SETUP.md）

## セットアップを頼まれたとき

1. **前提確認**: `python3 --version` と `claude --version` が通るか確認。
2. **機械的な準備を実行**: `bash setup.sh` を実行（venv 作成・依存インストール・config.yml 生成）。
3. **Slack アプリ作成を案内**（※ここは人が手でやる。Claude はブラウザを操作しない）:
   - api.slack.com/apps → Create New App → From a manifest → `manifest.yml` の内容を貼る
   - Install to Workspace → `xoxb-` トークン取得
   - Basic Information → App-Level Tokens（scope `connections:write`）→ `xapp-` トークン取得
   - ※ ワークスペースがアプリ作成を制限している場合があるので、作れるか先に確認させる
4. **config.yml の記入を案内**:
   - `paths.target_repo`（動かす対象プロジェクトのフルパス）は Claude が記入してよい
   - `slack.bot_token` / `slack.app_token` / `security.allowed_user_ids` は**利用者本人が config.yml に直接記入**する
   - ⚠️ **トークンや member ID をチャットに貼らせない**。必ず利用者が config.yml を編集する
5. **起動**: `bash run.sh` を実行（`⚡️ Bolt app is running!` が出れば成功）。
   - 常駐させたいなら、利用者自身のターミナルで `bash run.sh` を実行するよう案内（Claude セッションに紐づけると閉じたとき止まる）。

## 動かし方・使い方を聞かれたとき
- 起動: `bash run.sh`
- 使い方（DM コマンド等）: README.md の「使い方」
- 詰まったら: TROUBLESHOOTING.md（DM送信オフ・reactions:write・cmux 等）
- 別プロジェクト/自作 backend/全設定: SETUP.md

## 禁止
- トークン・シークレットをチャットに出力/記録しない（config.yml はコミットもしない＝.gitignore 済み）
