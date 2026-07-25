# claude-code-slack

Slack から、自分の Mac の Claude Code を動かす。
移動中にスマホで「作業: 〜して」と投げれば、Mac で無人処理して結果を返す。

---

## なぜこれを使う？（公式との違い）

公式にも「離れて Claude Code を動かす」手段はあります。使い分けの目安:

- **Claude Code on the web**: Anthropic のクラウドで動く。**GitHub の repo だけ**で、あなたの Mac のローカル repo・ローカルツールには触れない。
- **@Claude in Slack**: Slack ネイティブだが **Team/Enterprise＋管理者が必須（Pro 不可）**。クラウド実行でローカル環境には触れない。
- **公式 Remote Control（`claude remote-control`）**: あなたの Mac 上で動きローカル完全アクセス（Pro 可）。ただしブラウザ/モバイルアプリから **1つのセッションを対話的に操縦**する形。

**claude-code-slack はこう違う（＝こういう時に効く）**:
- **入口が Slack**。普段チームがいる場所から、**1スレッド=1タスク**で複数投げて放置 →**完了ごとに通知**が届く（1セッションを見張る必要がない）。
- **Pro でOK・管理者不要**で Slack から動かせる。
- 自分のマルチエージェント/オーケストレーターを持っているなら**それをそのまま無人駆動**（設定で差し込む）。持っていなくても `solo` でどこでも動く。

> ひとことで: 公式 Remote Control =「スマホで1セッションを操縦」。claude-code-slack =「Slack で複数タスクを投げて無人処理させる」。
> 1つのセッションを対話的にじっくり操縦したいだけなら公式 Remote Control で十分です。

---

## 導入（チーム代表者が一度だけ）

同じプロジェクトでチーム利用するなら、代表者が最初に config テンプレートを用意して配ると、各メンバーの手間が減る。個人利用だけなら**このセクションは飛ばして「セットアップと起動」へ**。

1. この repo をチームに共有する
2. `config.sample.yml` をコピーし、**プロジェクト共通の設定**を記入して配る（トークンは入れない）:
   - `paths.target_repo`（対象プロジェクト）
   - `task`（自分たちのオーケストレーターを使うなら backend 定義。solo だけなら不要）
   - `commands`（`/deploy` などプロジェクト共通コマンドがあれば）
3. 配布物 = この repo（コード＋記入済みテンプレート）。**`config.yml`（トークン入り）は配らない**

> ※ Slack アプリは共有しない。**1人1アプリ・1人1台**が原則（理由は「セットアップと起動」冒頭の注意書き）。

---

## セットアップと起動

> ⚠️ **最初に確認**: 1人1つ Slack アプリを作ります。ワークスペースがアプリ作成を制限していることがあるので、**作れるか先に確認**（ダメなら管理者に依頼）。

> 💡 **楽な道**: このフォルダを Claude Code で開いて「セットアップして」でもOK（機械的な準備は Claude が実行、Slack アプリ作成だけ手動）。
> 自分でやるなら `bash setup.sh`（venv＋依存＋config.yml）→ config.yml 記入 → `bash run.sh`。以下はその詳細。

### 0. 前提（入っているか確認するだけ）
- macOS＋Python 3
- `claude` CLI（ログイン済み） … `claude --version` が通る
- **Slack アプリを作成できること**（上の注意書き参照）

### 1. 自分の Slack アプリを作る（1人1つ）
1. https://api.slack.com/apps → **Create New App** → **From a manifest**
2. ワークスペースを選び、[manifest.yml](./manifest.yml) の中身を貼り付け → Create
3. **Install to Workspace** → 許可 → **Bot User OAuth Token（`xoxb-`）** をコピー
4. **Basic Information → App-Level Tokens → Generate** → scope `connections:write` → **`xapp-`** をコピー

> DM が「送信オフ」になる/組織で制限がある場合は [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)（チャンネルに bot を招いて `@bot名` でも動く）。

### 2. 入れる
```bash
cd claude-code-slack
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 3. 設定する
```bash
cp config.sample.yml config.yml   # 代表者配布のテンプレートがあればそれを config.yml に
```
`config.yml` を開いて必須項目を埋める:
- `paths.target_repo` … 対象プロジェクトのフルパス
- `slack.bot_token` … `xoxb-` トークン
- `slack.app_token` … `xapp-` トークン
- `security.allowed_user_ids` … 自分の Slack メンバーID（`U…`）

> 代表者配布のテンプレートがあれば `paths.target_repo` は記入済み。トークン2つと自分のIDだけ足す。

### 4. 起動する
```bash
caffeinate ./.venv/bin/python app.py
```
`⚡️ Bolt app is running!` が出れば稼働中。クラムシェルで蓋を閉じても動かすなら `sudo pmset -b disablesleep 1`。

### 5. 試す
Slack で **自分の bot に DM**:
```
こんにちは                       ← 普通に返事が来る（👀 が付けば受信OK）
作業: READMEのタイポを確認して報告して   ← Mac で無人処理して結果を返す（solo）
```

---

## 使い方

| 入力（DM） | 動作 |
|---|---|
| `作業: <自然言語>` | タスク実行（`solo` は relay が直接／自作 backend はそのオーケストレーターへ委譲） |
| ふつうの質問 | その場で回答（「何が動いてる？」で状況も見える） |
| `<タスク> やめて` | 中断 |
| `/model` `/mode` | モデル/動作モードを番号で選択 |
| `/commit` `/push` | そのスレッドのタスクを commit / push |
| ユーザー定義コマンド | `config.yml` の `commands` で自由に定義（例 `/deploy X.X.X+1`） |

---

- 困ったら → [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)
- もっと使う（別プロジェクトで動かす／自分のマルチエージェント基盤を差し込む／設定の全項目） → [SETUP.md](./SETUP.md)
