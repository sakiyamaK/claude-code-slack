# slack-relay 仕様書

Slack DM を入口に、ローカル Mac の Claude Code を駆動する自前中継。
このファイルは対話で確定した仕様の唯一の正。セッションが切れたらここから再開する。

- 最終更新: 2026-07-25
- 対象リポジトリ: `/Users/sakiyamak/program/work/tcm/prgm/tcm-ios`（設定で可変）
- 実装状態: **仕様確定作業中。実装は未着手**。`relay/` 配下の下書きコードは合意確定前の暫定で、SPEC 反映のため作り直す前提。

---

## 1. 目的とユースケース

- Slack DM でタスクを指示し、指示後はユーザーは別のことをする（移動中の可能性あり）
- タスクが**1件完了するごとに** Slack へ通知（2〜3並行でも都度）
- 帰宅後は同じ作業を **cmux の UI 上で直接** 続けられる
- 「いま何が動いてる？」を外出先から自然言語で確認できる

---

## 2. 全体構成（動作トポロジ）

```
スマホ/PC の Slack（人間として指示）
      │ DM
      ▼
Slack のサーバ
      │ Socket Mode（Mac→Slack へ外向きWebSocket。公開URL不要・Slack.app不要）
      ▼
┌───────────── このMac（常時起動・クラムシェル） ─────────────┐
│  relay（Python + Slack Bolt）= 常駐botメンバー              │
│   ・DM受信 → モード判定 → 振り分け                          │
│   ・普通モード: claude -p をサブプロセス起動                │
│   ・タスクモード: todo.md更新 → cmux send でマネージャーへ  │
│   ・dashboard.md を watch → タスク完了を検知 → Slack通知    │
│                                                            │
│  cmux（GUIターミナル / com.cmuxterm.app, ghosttyベース）    │
│   └ claude agent-session ペイン = tcmtasks マネージャー常駐 │
│        └ Agentツールで team-leader/programmer を worktree起動│
└────────────────────────────────────────────────────────────┘
```

- **Slackアプリ = 登録情報（トークン）だけ**。動くコードは全部 Mac 側。クラウドに分身は無い。
- Slack視点では「ワークスペースに常駐する自動応答 bot メンバーが1人増える」。生死は Mac＋relayプロセスに連動。
- **マネージャー＝cmux ペインに常駐する単一 claude セッション**。relay 再起動でも死なない。Slack と cmux は同一マネージャーの2つの顔。

---

## 3. 起動・常駐

- 起動: `caffeinate python app.py`（プロセス稼働中はスリープ抑止）
- クラムシェル蓋閉じ対策: `sudo pmset -b disablesleep 1` を手順書に併記
- 前提: **Mac がログイン済み（GUIセッション有効）** であること。cmux は GUI アプリなので、ログイン画面で止まっていると起動できない（→ §9 フォールバック）

---

## 4. 入口とスレッドモデル

- **claude-relay bot との DM 専用**
- **1スレッド = 1セッション**。最初の1通でモード確定・以降固定。返信は resume で継続
- スレッド鍵は `thread_ts`（親があればそれ、無ければ自分の `ts`）

---

## 5. モード

| | 普通モード（既定） | タスクモード |
|---|---|---|
| 起動 | `作業:` 等が付かない全て | 先頭が `作業:`/`タスク:`/`task:` 等 |
| cwd | `WORKSPACE_DIR`（既定=リポジトリ親） | （worktreeはマネージャーが作る） |
| 実行 | relay が `claude -p` を直接起動（resume） | todo.md更新 → cmux send でマネージャー |
| 用途 | 質問・調査・お試しproject作成・tcm横断・**タスク状況の確認** | tcm-ios の隔離作業（tcmtasks） |
| worktree | 作らない | マネージャーが作成 |
| cmux | 不要 | 必要（マネージャー常駐先） |

- 普通モードの claude には「**タスクの状況を聞かれたら `.claude/tcmtasks/dashboard.md` を読んで答える**」前提を持たせる。→ 決め打ちの status コマンドは作らない。

---

## 6. タスクモード判定（寛容パース）

先頭（前後の空白・改行は無視）が下記に一致 → タスクモード:

```
^\s*(作業|タスク|task)\s*[:：]\s*([\s\S]*)$     # IGNORECASE, 本文は改行含む
```

- キーワード: `作業`/`タスク`/`task`（設定で増減可）
- 区切り: `:` または `：`（半角/全角）。キーワードと区切りの間のスペース任意
- 区切り後のスペース・改行は読み飛ばし、**残り全部**がプロンプト（改行OK）
- 境界: 区切りなし（`作業について` 等）→ 普通モード。本文が空（`作業:` だけ）→ **聞き返す**（空worktree誤爆防止）

例（全てタスクモード、本文は右）: `作業:aaa`→aaa / `作業 ： aaa`→aaa / `task:\naaa`→aaa

---

## 7. タスクモードの委譲（tcmtasks）

- relay は **worktree を作らない**。**todo.md（`.claude/tcmtasks/todo.md`）を更新してマネージャーに処理させるだけ**
- **ブランチ名決定・worktree作成・並行（4〜5）・進捗管理は全部マネージャーの仕事**（`manager.agent.md`）
- ブランチ名は**マネージャーが自然名を決める**。relay は決めない。**`relay-XXX` のような機構前提の名前は厳禁**。タスク内容に応じた自然名（`feature/fix-login` 等）
- 指示は**自然言語でOK**。`作業(branch):` のような固定構文は不要。ブランチ指定があれば従い、既存ブランチもマネージャーが再利用
- relay→マネージャーへの受け渡し: relay が todo.md にエントリ追記 → `cmux send` で「todo更新した、処理して」を注入

### relay の並行キューは不要
タスクモードの並行はマネージャーが担う。relay 側の MAX_CONCURRENT は普通モードにのみ関係（普通モードの同時 claude -p 制御。既定2、超過は待機）。

### commit / push / PR（tcmtasks 継承・relay 独自実装なし）
- `git add`/`git commit`: マネージャーが実行。ただし**社長（ユーザー）の承認後のみ**。承認は §14 の自然言語ルーティングでマネージャーへ伝わる（例「レビューOKならコミットして」）
- `git push`: **ユーザーが手動**。マネージャーはしない
- PR: tcmtasks スコープ外（自動化しない）
- → relay は承認NLを中継するだけ。commit/PR ポリシーは持たない

---

## 8. cmux 連携

- cmux CLI: `/Applications/cmux.app/Contents/Resources/bin/cmux`（Unix socket 制御）
- 使う機能:
  - `cmux send [--surface <id>] <text>` … マネージャーペインへ文字列注入（tmux send-keys 相当）
  - `cmux send-key ... <key>` … Enter 等
  - `cmux read-screen` / `capture-pane` … ペイン出力読取（catch-up・状態）
  - `cmux new-surface --type agent-session --provider claude` … マネージャーペイン起動
  - `cmux events [--after <seq>] [--name <event>]` … イベント購読（将来の完了検知強化に）
  - `cmux notify` / `set-status` / `set-progress` … UI通知
- **マネージャーへの流し込み方式（候補A）で確定**。tmux/pty ではなく cmux socket CLI を使う。

---

## 9. cmux オンデマンド起動とフォールバック

タスクモード指示が来たら relay が冪等にチェック:

```
作業: 受信 → 👀即ack
  → cmux 起動してる？ 無ければ relay が起動
  → マネージャーペインある？ 無ければ new-surface --provider claude で作成
  → todo.md更新 → cmux send で処理指示
  → 「🚀 cmuxを起動してタスクに着手しました」通知
```

- 外出先で cmux 起動を忘れていても relay が自動起動 → ユーザーは気にしなくてよい
- **Mac がログイン画面で止まっている場合のみ** GUI起動不可 → 「⚠️ cmuxを起動できません（Macがロック/ログイン画面の可能性）」と通知し、**普通モードのみ受付**にフォールバック

---

## 10. スリープ対策と取りこぼし防止（catch-up）

- 対策方針: **Macを寝かせない（caffeinate/disablesleep）＋catch-up 保険**
- Socket Mode は**切断中に届いた DM を再配信しない**ため、relay 起動/再接続時に **DM履歴（`im:history`）を遡り、最後に処理した ts 以降の未処理DMを拾って実行**
- 「最後に処理した ts」はレジストリに保持（再起動から復元可）
- 復帰時に「⏰ 復帰しました。未処理の指示を処理します」と通知

---

## 11. 生死確認

- **① 受信即ack**: DM受信で relay が 1〜2秒以内に 👀 を付与。「👀 付かない＝停止中」がスマホ判定ルール（実装する）
- **② bot 在席ドット**: Slack標準（準備不要・コード不要）。緑=稼働/グレー=停止。切替に数十秒〜数分ラグ、モバイルUIは控えめ
- 決め打ちの `ping` コマンドは作らない（①②で足りる）

---

## 12. タスク完了通知（都度通知）

### 衝突と解決
- マネージャーは「**全完了時に1回だけ報告**」設計（都度報告しない）。ユーザー要件「1タスク完了ごとに通知」と衝突。
- 解決: **relay が独立して dashboard.md を watch**。タスクが「実行中」→「完了タスク」へ移った瞬間に Slack 通知。マネージャー無改造で都度通知を実現。

### 通知ルーティング（相関タグ方式）
- relay が todo エントリ本文に**不可視の相関タグ** `<!-- from-thread: <thread_ts> -->` を入れる（ブランチ名には干渉しない別レーン）
- **manager.agent.md に一行ルール追加**:「todoエントリに `from-thread` タグがあれば dashboard の該当タスク行に併記する（relay通知用）」
- relay は dashboard で **branch ↔ thread** を解決し、正しいスレッドへ完了通知

### 通知する節目（D＝中間案で確定）
relay は dashboard のステータス変化を監視し、**下記の節目だけ**該当スレッドへ通知する（途中のこまごま＝ビルド開始・調査中等は流さない）:

| タイミング | 通知例 | 区分 |
|---|---|---|
| 🚀 着手 | 「着手しました」 | 開始 |
| 🔨 実装・テスト完了→レビュー入り | 「<task> 実装・テスト完了、レビューへ」 | 中間の節目（1点） |
| 🚨 社長の確認が必要な事項 | 「<task> 要判断: テストコードなし」等 | **要判断（必ず通知）** |
| ⚠️ 停滞（30分） | 「<task> 停滞しています」 | 異常 |
| ✅ 完了 | 「<task> 完了」 | 完了 |

- **🚨（社長の確認が必要）は進捗の饒舌さと別軸で必ず通知**（判断しないとタスクが前に進まないため）
- どのステータス文字列を各節目とみなすかの**マッピングは設定可能**（dashboard 表記変更に追従）
- うるさければ「🔨実装完了」通知だけ設定でオフ → 最小案（開始・要判断・停滞・完了）に落とせる

### 停滞警告も同じ仕組みで通知（G＝案1で確定）
- **定期ハートビートは作らない**（順調な時は静か）
- relay の dashboard 監視対象に**完了だけでなく停滞警告(🚨/[警告])も含める**
- マネージャーは stuck を自前で検知（manager.agent.md 警告ルール: 同一ステップ30分以上未更新→警告）し dashboard に書く。マネージャーは Slack を知らない。**relay が dashboard を watch して拾い、相関タグで解決した該当スレッドへ「⚠️ <task> が停滞しています」を通知**
- 「順調なら静か、詰まったら relay が拾って Slack に流す」

---

## 13. モデル/モード切替（コマンド）

- `/model`：引数なしで打つと relay が番号付き選択肢を提示 → **番号返信**で選択
  - 選択肢: `1 Opus 4.8` / `2 Sonnet 5` / `3 Haiku 4.5`
- `/mode`：同様に番号返信
  - 選択肢: `1 default(都度確認)` / `2 auto(acceptEdits=編集自動許可)` / `3 plan(計画のみ)` /（`ALLOW_BYPASS=true` の人だけ）`4 yolo(bypassPermissions=全許可)`
- **グローバル1設定**（入口=マネージャーの設定なので全スレッド共通）。設定ストア1レコードに保存、再起動保持。以降の全 `claude -p`／マネージャーに適用
- その場指定（`(sonnet)` 等）は無し。切替は `/model` `/mode` に一本化

---

## 13.5 デリバリ / git コマンド（スレッドスコープ）

高リスクで離散的な操作は明示コマンド。**スレッドに紐付いたタスクのブランチ/worktree** に対して実行する（紐付いていないスレッドで打たれたら relay が「どのタスク？」と聞き返す）。

| コマンド | 動作 | 実行主体 |
|---|---|---|
| `/commit` | そのブランチを commit（プログラマー提案メッセージ使用） | マネージャー |
| `/push` | そのブランチを origin へ push（GitHubで成果物確認・PR可能に）。**push のみ、PR自動化なし** | マネージャー |
| `/firebase [ver]` | `deliver` スキルで **Firebase App Distribution** 配信 | deliver（worktree内） |
| `/testflight [ver]` | `deliver` スキルで **TestFlight** 配信 | deliver（worktree内） |

### バージョン指定記法（`/firebase` `/testflight` の引数 `ver`）
現在の番号を知らずに「どこを上げるか」だけ書ける。3桁 = `メジャー.マイナー.パッチ`、各桁は:
- `X` … 現状維持 / `+N` … +N / 数字 … その値に固定

**桁上げ時は下位桁を0リセット（慣習・案B）を常に適用する。**
- 引数なし → **バージョン据え置きで配信**（ビルド番号だけ deliver が自動インクリメント）
- 例（現在 `10.2.4`）: `X.X.X+1`→`10.2.5` / `X.X+1.0`→`10.3.0` / `X+1.0.0`→`11.0.0`
- ユーザーが慣習と食い違う指定（例 `X.X+1.X`=パッチ据え置き）をしたら、relay が「慣習に合わせて `10.3.0` にします」と伝えてから慣習の番号で実行
- マーケティング版（3桁）とは別に**ビルド番号（例 356）は deliver が毎回インクリメント**。だから据え置き再配信もTestFlightが受け付ける。deliver skill の実採番は実装時に整合させる

### 安全確認
`/push` `/firebase` `/testflight` は外向き操作。実行前に relay が「これから branch X を vY.Y.Z で <配信先> に上げます」と一言出してから走らせる（誤爆防止・バージョン補正の告知もここで）。

---

## 14. 確認・操作の自然言語ルーティング

`作業:`・`/model`・`/mode` 以外の自然言語は、**軽い意図判定を1枚かます**:

```
自然言語メッセージ
  ├─ running タスクへの操作意図（止めて/中断/やめて/キャンセル 等）
  │     → マネージャーへ送る → 該当タスクのエージェント停止（TaskStop/kill）→ dashboard更新
  │     → relay「🛑 <task> を止めました」
  └─ それ以外（状況確認・質問・お試し・雑談）
        → 普通モードの claude（dashboard を読んで答える等）
```

- 「どうなってる？」＝質問→普通モード。「やめて」＝操作→マネージャー。言い回しは自由。
- **中断は自然言語**でやりたい（確定）。将来「最優先で」「ブランチ変えて」等の操作も判定対象に含めうる（保留）。

---

## 15. スレッド運用（案Y：relay がタスクごとのメッセージを出す）

Slack はスレッドを開くのに親メッセージが要る。ユーザーが毎回 `作業: <ref>` を書くのを避けるため:

- 「何が動いてる？」に対し、relay は dashboard の各タスクを**振り分けて**返す:
  - **スレッド未作成のタスク** → 🧵 新規スレッド（anchorメッセージ）を作り状況を書く。relay が `anchor_ts ↔ branch` を記録
  - **既にスレッドがあるタスク** → **その既存スレッドに状況を返信**（新スレッドを乱立させない）
  - 聞いたトップレベルには**全体要約＋各スレッドへの導線**
- ユーザーは**触りたいタスクのメッセージにスレッド返信するだけ**で、そのタスク専用スレッドになる（`作業:`・番号・タスク名を書かなくてよい）
- 新規タスクを増やす時だけ `作業: 〜` をトップレベルで書く（当然の手間）

### 使い分け早見
| やりたいこと | 打ち方 |
|---|---|
| 全体をざっと見る | 「何が動いてる？」（自然言語・1スレッドでよい） |
| 特定タスクの状況だけ | 「<task> どう？」（自然言語） |
| 特定タスクを触る/専用窓口化 | そのタスクのメッセージにスレッド返信、または `作業: <ref> 〜` |
| 止める | 「<task> やめて」（自然言語） |

---

## 16. 安全弁

- `ALLOWED_USER_IDS`：起動を許可する Slack ユーザーID（本人のみ）
- DM 限定（`message.im`）。チャンネルの拾い読みはしない
- permission モード経由で危険操作を停止。既定 `acceptEdits`
- `yolo`（bypassPermissions）は `ALLOW_BYPASS=true` の人のみ。横展開の既定は禁止
- 破壊的操作は manager.agent.md の Tier ルールに従う（マネージャー配下）

---

## 17. 横展開（チーム配布）

- **1人1Slackアプリ（別bot・別トークン）**が基本形。同一アプリを複数台で起動すると Socket Mode がどれか1接続にだけ不定配信し混線するのでNG
- 複数台使う人は台ごとに別アプリ → DM相手の bot で台を選ぶ
- 配布物: コード一式＋`env.sample`。各自 `cp env.sample .env` してトークン等を記入
- 各自が Slackアプリを **From a manifest** で作成（2026-07-25 に PoC 済みの手順）。必要scope: bot `app_mentions:read`/`chat:write`/`channels:history`/`im:history`、event `app_mention`/`message.im`、`socket_mode_enabled: true`、App Home の Messages Tab で送信許可
- **絶対パスのハードコード禁止**。既定値は `TARGET_REPO` から相対導出（例 `WORKSPACE_DIR` 未指定なら `TARGET_REPO` の親）

---

## 18. 設定（env.sample、全て .env）

- `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN`
- `ALLOWED_USER_IDS` / `ALLOWED_CHANNEL_IDS`
- タスク判定キーワード（既定 `作業,タスク,task`）
- `TARGET_REPO` / `WORKTREE_PARENT` / `WORKSPACE_DIR`
- `MAX_CONCURRENT`（普通モード用・既定2）
- モデル/モードのグローバル既定、`ALLOW_BYPASS`
- `CLAUDE_BIN`、cmux CLI パス、レジストリDBパス

---

## 19. manager.agent.md への変更（最小）

- 追加ルール1つ:「**todoエントリに `<!-- from-thread: <ts> -->` タグがあれば、dashboard.md の該当タスク行にその ts を併記する（relayの完了通知ルーティング用）**」
- それ以外のマネージャー挙動は不可侵。

---

## 20. レジストリ（永続・再起動復元）

- thread_ts → { branch, session_id, status, mode, anchor_ts, created/updated }
- branch ↔ thread_ts（完了通知ルーティング）
- 「最後に処理した DM ts」（catch-up 用）
- モデル/モードのグローバル設定
- 保存: SQLite（`relay/registry.py` の下書きあり）

---

## 21. 確定サマリ

- 言語: Python + Slack Bolt
- 入口: DM専用、1スレッド=1セッション、最初の1通でモード固定
- 普通モード（既定・cmux不要）／タスクモード（`作業:`・tcmtasks委譲・cmux常駐マネージャー）
- モデル/モードは `/model` `/mode` 番号返信のグローバル設定
- 並行: タスクはマネージャー、普通は relay の MAX_CONCURRENT
- スリープ: caffeinate＋catch-up、生死は👀ack＋在席ドット
- 完了通知: dashboard監視＋相関タグで都度・該当スレッドへ
- 確認/中断は自然言語（意図判定1枚）、スレッドは案Y（既存優先）
- 横展開: 1人1アプリ、全て.env、絶対パス禁止

---

## 22. 保留（実装時に詰める細目のみ。大枠の設計判断は完了）

- 普通モードの cwd 運用細部（お試しproject の置き場所ルール）
- 意図判定の対象拡張（「最優先で」「ブランチ変えて」等の操作の追加）
- 既存タスク参照時の branch 特定ロジックの実装細部
- deliver skill の実採番挙動との整合（ビルド番号・バージョン）
- dashboard ステータス文字列 → 節目マッピングの初期値

---

## 23-LIVE. 実機で完全動作（2026-07-25 検証済み）

DM/メンション受信 → 👀ack → タスクモード（cmuxマネージャー無人処理）→ 結果を該当スレッドへ自動通知、まで **end-to-end で実機動作を確認済み**。この過程で判明した重要事実（実装に反映済み）:

1. **cmux の agent-session(React UI)はNG**: `new-surface --type agent-session --provider claude` しても claude プロセスが起動せず（`agent.resolve_delivery_target` が surface_id:null）、`workspace.prompt_submit` してもキューされるだけで届かない。
   → **正解: ターミナルで対話 claude を起動し（`workspace create --command "claude --permission-mode <pm> --model <m>"`）、`cmux send`+`send-key enter` でプロンプト注入**。端末なら send が効く。初回は claude 起動完了を待つ（`read-screen` で "accept edits"/"❯" を検出、~40s）。
2. **マネージャーは bypassPermissions 必須**: acceptEdits だと `.claude/` 配下編集などで確認プロンプトが出て無人運用が止まる。config `behavior.manager_permission_mode: bypassPermissions`（コード既定は acceptEdits・ユーザー明示オン）。manager.agent.md の安全ルール＋worktree隔離が担保。
3. **Slack scope `reactions:write` が必要**（👀ack）。無いと返信は来るがリアクションだけ静かに失敗。
4. **報告のみタスクは完了テーブルでなく専用セクション**（`## 〇〇調査結果 <!-- from-thread: ts -->`）に出る。dashboard.py はテーブルに加えこの結果セクションも検出して通知（report イベント）。
5. マネージャーへの投入は relay が `/tcmtasks -t` を send → todo.md を読ませて処理。
6. Slackアプリは message.im＋App Home Messages Tab の送信許可＋（組織のアプリDM制限に注意）。回避策として app_mention（チャンネル＋メンション）も実装済み。
7. 注意: relay 再起動時は dashboard watcher の baseline が現状態になるため、再起動前に完了した通知は遡らない。

起動: `cd slack-relay && PYTHONUNBUFFERED=1 ./.venv/bin/python app.py`（実運用は caffeinate 付き）。設定は config.yml。

## 23. 実装状態（2026-07-25 初回実装完了）

### 実装済み・検証済み（純ロジックはローカルテスト合格）
- `relay/parsing.py`：タスク判定（寛容パース）・コマンド判定・バージョン記法（`X.X.X+1` 等・慣習リセット補正）。**単体テスト合格**
- `relay/config.py`：env 読込。絶対パス既定は TARGET_REPO 相対導出
- `relay/registry.py`：SQLite。tasks（branch↔thread・anchor）＋settings（model/mode・last_processed_ts）
- `relay/todo.py`：todo.md 追記＋`from-thread` タグ。**ブランチ名は発明せず**、明示ブランチ検出のみ（無ければマネージャー命名に委ねる）
- `relay/dashboard.py`：dashboard.md パース＋差分→通知イベント（done/review/stall/alert）。**テスト合格**
- `relay/intent.py`：操作意図の軽い判定（stop系ヒューリスティック）
- `relay/runner.py`：普通モード `claude -p`（model/mode 反映・resume・dashboard 読取ヒント）
- `relay/cmux.py`：cmux CLI ラッパ（ensure/send/read）
- `relay/orchestrator.py`：全ルーティング・普通/タスク/操作/コマンド・dashboard 監視通知。**モック統合テスト合格**（/model ピッカー→番号、作業:→todo＋cmux、普通、中断、/testflight のバージョン補正、完了/🚨のスレッド振り分け）
- `app.py`：Slack Bolt（DM専用ゲート・👀ack・catch-up・watcher起動）
- `env.sample` `requirements.txt` `README.md`

### 実機で要調整（この環境では検証不可）
- `slack_bolt` 実行時依存（`pip install -r requirements.txt`）
- **cmux の実出力形式**：`new_manager_surface` の surface ref 抽出は暫定（実機の cmux 出力に合わせる）
- `claude -p --output-format stream-json` の実フィールド名（session_id / result）確認
- **manager.agent.md への §19 追加ルール（from-thread 併記）は未適用**。tcm-ios の committed ファイルのため、ユーザー承認後に編集する

### 保留の実装細部（SPEC §22）
- todo 見出し未確定時の運用・既存タスク参照時の branch 特定・意図判定の LLM 化・節目マッピング初期値
