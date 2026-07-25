# SETUP（設定・バックエンド・チーム配布）

README で動かせた後の、少し踏み込んだ設定。

---

## タスクの実行方式（backend）

`作業:` を送ったときの処理方式を `config.yml` の `task.backend` で選ぶ。

relay はタスクを **start → watch** のライフサイクルで回す。各フックを config で差す。

### solo（既定・どこでも動く）
`backend` 未指定なら solo。**タスクごとに worktree を切って** `claude` を回し、結果をスレッドに返す。
worktree 隔離により**複数タスクを安全に並行**できる。追加ツール不要。

```yaml
task:
  solo:
    worktree: true     # タスクごとに worktree（既定 true）
    base: ""           # ベース ref（空=現在ブランチ）
```

### 自作 backend（自分のマルチエージェント/オーケストレーターを使う）
自前の「タスクを受けて処理する仕組み」を持っているなら差し込める。relay は共通部分
（cmux 上のマネージャー常駐・スレッド紐付け・進捗の AI 解釈・通知）を担う。

```yaml
task:
  backend: my-agents
  backends:
    my-agents:
      start:                          # タスク開始時のフック（順に実行）
        - kind: inject                #   inject: マネージャー(claude)にプロンプト注入
          prompt: "新規タスク {id}: {task}"
        # - kind: file                #   file: ファイルにタスクを書く
        #   path: ".agents/todo.md"
        #   template: "{task}\n"
        # - kind: command             #   command: 任意のシェルコマンド
        #   command: "my-agents add '{task}'"
      watch: ".agents/progress.md"    # relay が監視。AI が中身を読んで完了/停滞/要判断を通知
```

**契約はこれだけ**: 「start を受けて動き、進捗をどこかのファイルに書く」。
進捗ファイルの**書式は自由**で、relay は変化のたびにその中身と追跡中タスクを AI に渡し、
「どのタスクが 完了 / 停滞 / 要判断 か」を解釈させてスレッドへ通知する。
**決まった書式も相関タグも不要**（AI が内容でスレッドに紐付ける）。

- プレースホルダ: `{task}`=依頼内容, `{id}`=スレッド識別子
- コスト: 進捗ファイルが変わるたびに解釈 LLM（安価なモデル）を1回。変化検知で無駄打ちは抑制

---

## 別プロジェクトで使う
`config.yml` の `paths.target_repo` を対象プロジェクトのパスに変えるだけ。
複数プロジェクトを同時に動かすなら、**プロジェクトごとに relay を1つ**起動する
（別々の `config.yml`。`RELAY_CONFIG=/path/to/other.yml ./.venv/bin/python app.py` で切替可能）。

---

## チームに配る
1. この `claude-code-slack/` 一式を配る（`config.yml` は**配らない** = トークンを含む。`config.sample.yml` を配る）
2. 各自が [manifest.yml](./manifest.yml) から**自分の Slack アプリ**を作る（1人1つ）
3. 各自が `config.yml` を作り、自分のトークン・自分のメンバーIDを入れる
4. 各自が自分の Mac で起動する

> **共有しない**: 1台の relay を複数人で使うのは不可（Slack の Socket Mode が配信先を1つに絞るため混線する）。
> **1人1アプリ・1人1relay** が原則。1人が複数台 Mac を使う場合も、台ごとに別アプリにする。

---

## config.yml の項目

| キー | 説明 | 既定 |
|---|---|---|
| `slack.bot_token` / `slack.app_token` | Slack トークン（必須） | - |
| `security.allowed_user_ids` | 起動を許可するユーザー（本人のみ推奨。空=全員で危険） | - |
| `security.allowed_channel_ids` | 反応する DM/チャンネル（空=全部） | 空 |
| `security.allow_bypass` | `/mode` で yolo を選べるように | false |
| `paths.target_repo` | 対象プロジェクト（必須） | - |
| `paths.worktree_parent` / `workspace_dir` | worktree 親 / 普通モードの cwd（空=target_repo の親） | 親 |
| `task.backend` | タスク実行方式（`solo` or `backends` のキー） | solo |
| `task.solo.worktree` | solo でタスクごとに worktree を切る | true |
| `task.backends` | 自作 backend 定義（`start` フック＋`watch` 進捗ファイル） | 無 |
| `behavior.manager_permission_mode` | 自作 backend マネージャーの権限（無人運用は `bypassPermissions`） | acceptEdits |
| `behavior.task_keywords` | タスク発火語 | 作業,タスク,task |
| `behavior.max_concurrent` | 普通モード/solo の同時実行 | 2 |
| `behavior.default_model` / `default_permission_mode` | 起動時の既定（`/model`/`/mode` で変更可） | opus / acceptEdits |
| `commands` | ユーザー定義コマンド（`/<name>`） | 無 |
| `bin.claude` / `bin.cmux` | バイナリのパス | claude / cmux |
| `registry_db` | 状態 DB の保存先 | ./relay_registry.sqlite3 |

---

## 安全について
- `allowed_user_ids` を本人だけに絞る（空にすると誰でもあなたの Mac を操作できる）
- `bypassPermissions` は無人運用のための全許可。あなたのオーケストレーターの安全策と
  worktree 隔離が歯止め。理解した上で使う
- `config.yml` はトークンを含むので**コミット・共有しない**（`.gitignore` 済み）

---

## 補足: 自作 backend は cmux を使う
自作 backend のマネージャーは、常駐対話セッションとして cmux（GUI ターミナル）上で動かす。
relay が自動で cmux を起動しマネージャーのターミナルを用意する（cmux 未インストールなら solo を使う）。
