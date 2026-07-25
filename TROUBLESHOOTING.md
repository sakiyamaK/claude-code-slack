# トラブルシューティング

困った症状から引ける逆引き。詰まったらここだけ見ればOK。

---

## DM に返事が来ない / 「このアプリへのメッセージ送信はオフ」と出る
1. Slack アプリの **App Home → Show Tabs → Messages Tab** の
   **「Allow users to send Slash commands and messages…」にチェック**
   （[manifest.yml](./manifest.yml) から作れば最初から有効）
2. それでもダメなら **Slack を ⌘Q で完全終了して再起動**（クライアントのキャッシュ）
3. まだダメなら **組織がアプリへのDMを制限**している可能性 → 管理者に確認、または下の「チャンネルで使う」で回避

### チャンネルで使う（DMが使えない時の回避）
1. 適当なチャンネルで `/invite @claude-relay`
2. `@claude-relay こんにちは` のように**メンションして話しかける**（DMと同じ機能が使える）

---

## 👀 リアクションが付かない（でも返事は来る）
`reactions:write` スコープが無い。
- OAuth & Permissions → Bot Token Scopes に **`reactions:write`** を追加 → **Reinstall to Workspace**
- （[manifest.yml](./manifest.yml) から作れば最初から入っている）
- トークンが変わったら `config.yml` を更新して relay 再起動

---

## タスクモードでマネージャーが権限確認で止まる
`.claude/` 配下の編集などで確認プロンプトが出て無人運用が止まる。
- `config.yml` の `behavior.manager_permission_mode: bypassPermissions` にする（無人運用の推奨）
- マネージャーは manager.agent.md の安全ルール（`rm -rf`/`push --force` 等禁止）＋worktree隔離の下で動く

---

## `作業:` を送ってもマネージャーが動かない
- cmux（`/Applications/cmux.app`）が起動しているか。relay が自動起動を試みるが、**Mac がログイン画面だと GUI 起動できない**（ログインしておく）
- cmux 左に `relay-manager` ワークスペースが立ち、**ターミナルで claude が起動**しているか確認
- 対象プロジェクトに tcmtasks（`.claude/agents/manager.agent.md`）が入っているか

---

## 完了しても Slack に通知が来ない
- manager.agent.md に **from-thread 併記ルール**が入っているか（[SETUP.md](./SETUP.md) 参照）。無いと通知のスレッド振り分けができない
- relay を**再起動した直後**は、監視の基準が現状態になるため、再起動前に完了したものは遡って通知されない（次のタスクから正常）

---

## Bot トークンを再発行したら動かなくなった
- 再インストールで `xoxb-` が変わることがある → `config.yml` の `slack.bot_token` を更新 → relay 再起動

---

## 起動時にエラーで落ちる
- `slack.bot_token` / `slack.app_token` 未記入 → `config.yml` を記入
- `paths.target_repo が存在しません` → パスを実在するプロジェクトに
- `invalid_auth` → トークンが間違っている/失効。取り直して `config.yml` 更新

---

## Mac がスリープすると止まる
- `caffeinate ./.venv/bin/python app.py` で起動（稼働中はスリープ抑止）
- クラムシェル（蓋閉じ）でも動かすなら `sudo pmset -b disablesleep 1`

---

## 生きているか確認したい
- DM を送って **👀 が付く**＝稼働中。付かない＝止まっている
- Slack 上の claude-relay bot の**緑ドット**でも分かる（切り替わりに数十秒ラグあり）
