# SETUP（設定・仕組み・チーム配布）

README で動かせた後の、少し踏み込んだ設定。

---

## タスクの実行の仕組み（1タスク=1 cmux セッション）

DM のメッセージはそのまま作業指示（プレフィックス不要）。タスクごとに cmux のセッション（workspace タブ）を1つ割り当て、
Slack スレッドと同期する（**cmux 必須**）。relay は次の順で動く:

1. **既存セッションとの照合**: いま cmux で動いているセッション（手で開いた窓も含む）を列挙し、
   指示がそのどれかの作業そのものなら**そのセッションへ連携（注入）**する。
   照合はチケットID等の決定的一致 → 決まらなければ AI がタブ名・タイトル・作業ディレクトリで判定
   （曖昧なら連携しない＝誤注入より新規作成を選ぶ）。
2. **新規ならタブを増やす**: 該当が無ければ作業先リポジトリを振り分け（後述）、cmux に
   **名前付きタブ**を作って対話 claude を起動し、指示を注入する。
   **cmux の UI からいつでも作業の様子を確認できる**（タブは作成時に最前面だったウィンドウに入る）。
3. **スレッド ⇔ セッションの永続紐付け（1スレッド=1surface）**: 以降そのスレッドの発言
   （追加指示・質問）は同じセッションへ届き、「やめて」は ESC 割り込み＋中断指示になる。
   一度紐付いた surface は他のスレッドに取られない。relay を再起動しても紐付けは残る（SQLite）。
4. **進捗通知**: relay が各セッションの画面を監視し（出力が落ち着くまでデバウンス）、増えた本文をそのまま
   ✅完了 / 🚨要判断 / 🔨節目 をスレッドへ返す。**決まった書式も相関タグも不要**。

- タブを手で閉じると relay は追跡を終了する（スレッドに続きを指示すれば新しいセッションで再開）
- コスト: 照合・振り分けの LLM 呼び出しが新規タスク時に最大1回（進捗転送は LLM を使わない）

---

## リポジトリの振り分け（repos）

`repos` に名前付きでリポジトリを列挙する（1つでも複数でもこの形式）:

```yaml
repos:
  ios:
    path: "/path/to/ios-app"
    description: "iOSアプリ本体の開発"
  docs:
    path: "/path/to/dev-docs"
    description: "開発ドキュメント・エージェント定義の管理"
default_repo: "ios"
```

- 新規タスクの作業先は **指示にリポジトリ名の明示（「docsの〜」等） > AI 判定（description が手掛かり） > default_repo** の順
- スレッドごとに作業先が記録され、タブを閉じて再作成するときも同じリポジトリで復元される
- `/commit` `/push` 等の git 操作はスレッドに紐付いたセッション（＝そのリポジトリ）への指示になる

これとは別に、relay 自体を分けたい場合は**設定ごとに relay を1つ**起動する
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
| `repos` | 名前付きリポジトリ一覧（`path` + `description`。必須） | - |
| `default_repo` | `repos` のうち既定の名前 | 最初のエントリ |
| `models.task` | タスク実装モデル（`/model` で変更可） | fable |
| `models.match` | セッション照合・リポジトリ振り分け（誤照合は注入事故になる） | sonnet |
| `permission_mode` | タスクセッションの権限の初期値（`/mode` で変更可。無人運用は `bypassPermissions`） | acceptEdits |
| `prompt` | セッションに注入するテンプレ（`{task}` `{id}`） | `{task}` |
| `commands` | ユーザー定義コマンド（`/<name>`） | 無 |
| `version_source` | `version: true` コマンドの現在バージョン取得コマンド | git describe |
| `bin.claude` / `bin.cmux` | バイナリのパス | claude / cmux |
| `registry_db` | 状態 DB の保存先 | ./relay_registry.sqlite3 |

> 旧書式（`paths.*` / `task.*` / `behavior.*`、solo backend）は廃止。旧 config で起動すると
> 移行案内のエラーが出る。

---

## 安全について
- `allowed_user_ids` を本人だけに絞る（空にすると誰でもあなたの Mac を操作できる）
- `permission_mode: "bypassPermissions"` は無人運用のための全許可。プロジェクト側の安全策
  （hooks 等）が歯止め。理解した上で使う
- `config.yml` はトークンを含むので**コミット・共有しない**（`.gitignore` 済み）

---

## 補足: cmux の前提
タスクセッションは cmux（GUI ターミナル）のタブとして動かす。relay が自動で cmux を
起動してタブを用意する（Mac がログイン画面だと GUI 起動できないためログインしておく）。
セッションは cmux の UI からそのまま操作できる。手で開いた窓に Slack から連携することも、
Slack で始めたタスクを途中から手で引き継ぐこともできる。

---

## 開発・テスト

モジュール構成（1モジュール=1責務。依存はすべてコンストラクタ注入）:

| モジュール | 責務 |
|---|---|
| `app.py` | Slack Bolt の組み立てとハンドラ登録（import 副作用なし） |
| `relay/orchestrator.py` | composition root（依存の組み立て・スレッド単位の直列化・エラー報告） |
| `relay/router.py` | メッセージ種別の振り分けのみ |
| `relay/tasks.py` | タスクのライフサイクル（新規/継続/中断） |
| `relay/commands.py` | `/model` `/mode` `/commit` `/push`・カスタムコマンド |
| `relay/watcher.py` | セッション監視・通知（デバウンス・重複排除） |
| `relay/match.py` | 照合・振り分け（LLM 実行は注入） |
| `relay/llm.py` | claude -p 実行の唯一の窓口 |
| `relay/cmux.py` | cmux CLI アダプタ（runner 注入可） |
| `relay/registry.py` / `links.py` / `settings.py` | SQLite 永続化 / スレッド紐付け / 実行時設定 |
| `relay/gate.py` / `parsing.py` / `intent.py` | 純粋ロジック（許可判定・パース・意図判定） |

テストの実行:

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -q
```

LLM・Slack・cmux はすべて偽物を注入してテストする（実プロセスは起動しない）。
