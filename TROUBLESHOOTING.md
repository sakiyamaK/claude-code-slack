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

## 自作 backend のマネージャーが権限確認で止まる
無人運用中に確認プロンプトが出て止まる。
- `config.yml` の `behavior.manager_permission_mode: bypassPermissions` にする（無人運用の推奨）
- 全許可はあなたのオーケストレーターの安全策＋worktree 隔離が歯止めになる前提

---

## `作業:` を送っても自作 backend が動かない
- cmux（`/Applications/cmux.app`）が起動しているか。relay が自動起動を試みるが、**Mac がログイン画面だと GUI 起動できない**（ログインしておく）
- cmux に `relay-manager` ワークスペースが立ち、**ターミナルで claude が起動**しているか確認
- `task.backend` と `backends`（start / watch）が config.yml に正しく定義されているか
- 迷ったら `task.backend` を外して **solo** で試す（追加ツール不要で動く）

---

## 完了しても Slack に通知が来ない（自作 backend）
- `watch` に指定した進捗ファイルにオーケストレーターが状況を書いているか（そこを AI が読む）
- 進捗ファイルに、そのタスクが「完了した」と読み取れる記述があるか（AI が判断できる粒度で書く）
- relay を**再起動した直後**は監視の基準が現状態になるため、再起動前に完了したものは遡って通知されない（次のタスクから正常）

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
