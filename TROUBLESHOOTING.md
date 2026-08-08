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

## タスクセッションが権限確認で止まる
無人運用中に確認プロンプトが出て止まる。
- `config.yml` の `permission_mode: "bypassPermissions"` にする（無人運用の推奨）
- 全許可はプロジェクト側の安全策（hooks 等）＋worktree 隔離が歯止めになる前提

---

## 指示を送ってもタスクが動かない
- cmux（`/Applications/cmux.app`）が起動しているか。relay が自動起動を試みるが、**Mac がログイン画面だと GUI 起動できない**（ログインしておく）
- cmux に新しいタブ（workspace）が立ち、**ターミナルで claude が起動**しているか確認
- タブは「作成時に最前面だった cmux ウィンドウ」に作られる。見当たらなければ**別の cmux ウィンドウ**を確認（Cmd+\` で切替）

---

## 意図しないセッションに連携された / 新規タブになってほしかった
- 照合はチケットID等の一致 → AI 判定の順。指示に**チケットIDやセッション名を明記**すると確実
- 逆に既存セッションへ流したいのに新規タブが立つ場合も、指示にチケットID・作業名を含める
- 誤って連携されたら `/unlink` で紐付けを解除して、新しいスレッドで指示を投げ直す
- いま何に紐付いているかは `/status` で確認できる

---

## 完了しても Slack に通知が来ない
- relay がセッション画面を AI 解釈して通知する。セッションが結果を**端末に書き終えているか**確認
- relay を**再起動した直後**は監視の基準が現状態になるため、再起動前に完了したものは遡って通知されない（次のタスクから正常）
- タブを手で閉じると追跡が終わる（スレッドに続きを指示すれば新セッションで再開）

---

## Bot トークンを再発行したら動かなくなった
- 再インストールで `xoxb-` が変わることがある → `config.yml` の `slack.bot_token` を更新 → relay 再起動

---

## 起動時にエラーで落ちる
- `slack.bot_token` / `slack.app_token` 未記入 → `config.yml` を記入
- `repos.<名前> が存在しません` → パスを実在するプロジェクトに
- `config.yml が旧書式です` → 表示される案内に従い `repos` / `models` 等の新書式へ移行（config.sample.yml 参照）
- `invalid_auth` → トークンが間違っている/失効。取り直して `config.yml` 更新

---

## Mac がスリープすると止まる
- `caffeinate ./.venv/bin/python app.py` で起動（稼働中はスリープ抑止）
- クラムシェル（蓋閉じ）でも動かすなら `sudo pmset -b disablesleep 1`

---

## 生きているか確認したい
- DM を送って **👀 が付く**＝稼働中。付かない＝止まっている
- Slack 上の claude-relay bot の**緑ドット**でも分かる（切り替わりに数十秒ラグあり）
