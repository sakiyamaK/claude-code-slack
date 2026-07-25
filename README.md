# slack-relay

Slack から、自分の Mac の Claude Code / tcmtasks を動かす。
移動中にスマホで「作業: 〜して」と投げれば、Mac で無人処理して結果を返す。

---

## なぜこれを使う？（公式との違い）

公式にも「離れて Claude Code を動かす」手段はあります。使い分けの目安:

- **Claude Code on the web**: Anthropic のクラウドで動く。**GitHub の repo だけ**で、あなたの Mac のローカル repo・cmux・独自ツールには触れない。
- **@Claude in Slack（Claude Tag）**: Slack ネイティブだが **Team/Enterprise＋管理者が必須（Pro 不可）**。クラウド実行でローカル環境には触れない。
- **公式 Remote Control（`claude remote-control`）**: あなたの Mac 上で動きローカル完全アクセス（Pro 可）。ただしブラウザ/モバイルアプリから **1つのセッションを対話的に操縦**する形。

**slack-relay はこう違う（＝こういう時に効く）**:
- **入口が Slack**。普段チームがいる場所から、**1スレッド=1タスク**で複数投げて放置 →**完了ごとに通知**が届く（1セッションを見張る必要がない）。
- あなたの **tcmtasks マルチエージェント（worktree 並列・dashboard）をそのまま無人駆動**。「操縦」ではなく「投げて並列で回す」。
- **Pro でOK・管理者不要**で Slack から動かせる（@Claude の制約を回避）。
- cmux・配信コマンドなど**自分の環境に合わせて拡張**できる。

> ひとことで: 公式 Remote Control =「スマホで1セッションを操縦」。slack-relay =「Slack で複数タスクを投げ、自分のマルチエージェント環境に無人・並列で処理させる」。
> 逆に、1つのセッションを対話的にじっくり操縦したいだけなら公式 Remote Control で十分です。

---

## 5分で動かす

### 0. 前提（入っているか確認するだけ）
- macOS＋Python 3
- `claude` CLI（ログイン済み） … `claude --version` が通る
- cmux（`/Applications/cmux.app`）… タスクモードで使う
- 対象プロジェクトに tcmtasks が入っていること（`.claude/agents/manager.agent.md` 等）
  - ※ 完了通知には manager.agent.md に1行ルールが必要。→ [SETUP.md](./SETUP.md)

### 1. Slack アプリを作る（1人1つ）
1. https://api.slack.com/apps → **Create New App** → **From a manifest**
2. ワークスペースを選び、[manifest.yml](./manifest.yml) の中身を貼り付け → Create
3. **Install to Workspace** → 許可 → **Bot User OAuth Token（`xoxb-`）** をコピー
4. **Basic Information → App-Level Tokens → Generate** → scope `connections:write` → **`xapp-`** をコピー

> DM が「送信オフ」になる/組織で制限がある場合は [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) を見る（チャンネルにbotを招いて `@claude-relay` でも動く）。

### 2. 入れる
```bash
cd slack-relay
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 3. 設定する
```bash
cp config.sample.yml config.yml
```
`config.yml` を開いて **3か所だけ** 埋める:
- `slack.bot_token`（`xoxb-`）
- `slack.app_token`（`xapp-`）
- `security.allowed_user_ids`（自分のメンバーID。Slackの自分のプロフィール →「メンバーIDをコピー」）

`paths.target_repo` を動かしたいプロジェクトのパスにする（既定は tcm-ios）。

### 4. 起動する
```bash
caffeinate ./.venv/bin/python app.py
```
`⚡️ Bolt app is running!` が出れば稼働中。クラムシェルで蓋を閉じても動かすなら `sudo pmset -b disablesleep 1`。

### 5. 試す
Slack で **claude-relay に DM**:
```
こんにちは           ← 普通に返事が来る（👀 が付けば受信OK）
作業: READMEのタイポを確認して報告して   ← Mac で無人処理して結果を返す
```

---

## 使い方

| 入力（DM） | 動作 |
|---|---|
| `作業: <自然言語>` | タスク実行（cmux のマネージャーが無人処理） |
| ふつうの質問 | その場で回答（「何が動いてる？」で状況も見える） |
| `<タスク> やめて` | 中断 |
| `/model` `/mode` | モデル/動作モードを番号で選択 |
| `/commit` `/push` | そのスレッドのタスクを commit / push |
| プロジェクト固有コマンド | `config.yml` の `commands` で定義（iOS 例: `/firebase X.X.X+1` `/testflight X.X.X+1`） |

---

## 別プロジェクトで使う / チームに配る
- **別プロジェクト**: `config.yml` の `paths.target_repo` を変える（プロジェクトごとに relay を1つ起動）。
- **チーム配布**: この `slack-relay/` 一式と `config.sample.yml` を配る。各自が自分の Slack アプリを作り、自分の `config.yml` を書く（`config.yml` は共有しない＝トークンを含む）。

詳しくは [SETUP.md](./SETUP.md)。

---

- 困ったら → [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
- 仕組み・仕様 → [SPEC.md](./SPEC.md)
