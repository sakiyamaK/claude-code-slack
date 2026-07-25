# SETUP（プロジェクト設定・チーム配布）

README で動かせた後の、少し踏み込んだ設定。

---

## プロジェクトに必要なもの（タスクモードを使う場合）

タスクモード（`作業:`）は対象プロジェクトの tcmtasks（`.claude/agents/manager.agent.md` 等）を使う。
完了通知を正しいスレッドへ振り分けるため、**manager.agent.md に次の1行ルールを追加**しておく:

> todo エントリの body に `<!-- from-thread: <ts> -->` タグがあれば、dashboard.md の該当行
> （実行中/完了テーブル・🚨・報告のみ結果セクション）にそのタグを併記する。
> タスク処理・ブランチ命名には影響させない。行末に置く。

（tcm-ios には設定済み。他プロジェクトへ tcmtasks を移植する際はこの1行も忘れずに）

---

## 別プロジェクトで使う

`config.yml` の `paths.target_repo` を対象プロジェクトのパスに変えるだけ。
複数プロジェクトを同時に動かしたいなら、**プロジェクトごとに relay を1つ**起動する
（それぞれ別の `config.yml`。`RELAY_CONFIG=/path/to/other.yml ./.venv/bin/python app.py` で切替可能）。

---

## チームに配る

1. この `slack-relay/` 一式を配る（`config.yml` は**配らない** = トークンを含む。`config.sample.yml` を配る）
2. 各自が [manifest.yml](./manifest.yml) から**自分の Slack アプリ**を作る（1人1つ）
3. 各自が `config.yml` を作り、自分のトークン・自分のメンバーIDを入れる
4. 各自が自分の Mac で起動する

> **共有しない**: 1台の relay を複数人で使うのは不可（Slack の Socket Mode が配信先を1つに絞るため混線する）。
> **1人1アプリ・1人1relay** が原則。1人が複数台Macを使う場合も、台ごとに別アプリにする。

---

## config.yml の項目

| キー | 説明 | 既定 |
|---|---|---|
| `slack.bot_token` / `slack.app_token` | Slack トークン（必須） | - |
| `security.allowed_user_ids` | 起動を許可するユーザー（本人のみ推奨。空=全員で危険） | - |
| `security.allowed_channel_ids` | 反応するDM/チャンネル（空=全部） | 空 |
| `security.allow_bypass` | `/mode` で yolo を選べるように | false |
| `paths.target_repo` | 対象プロジェクト（必須） | - |
| `paths.worktree_parent` / `workspace_dir` | worktree親 / 普通モードのcwd（空=target_repoの親） | 親 |
| `behavior.manager_permission_mode` | マネージャーの権限（無人運用は `bypassPermissions`） | acceptEdits |
| `behavior.task_keywords` | タスク発火語 | 作業,タスク,task |
| `behavior.max_concurrent` | 普通モードの同時実行 | 2 |
| `behavior.default_model` / `default_permission_mode` | 起動時の既定（`/model`/`/mode`で変更可） | opus / acceptEdits |
| `bin.claude` / `bin.cmux` | バイナリのパス | claude / cmux |
| `registry_db` | 状態DBの保存先 | ./relay_registry.sqlite3 |

---

## 安全について
- `allowed_user_ids` を本人だけに絞る（空にすると誰でもあなたの Mac を操作できる）
- `manager_permission_mode: bypassPermissions` は無人運用のための全許可。manager.agent.md の
  破壊的操作禁止ルール＋worktree隔離が歯止め。理解した上で使う
- `config.yml` はトークンを含むので**コミット・共有しない**（`.gitignore` 済み）
