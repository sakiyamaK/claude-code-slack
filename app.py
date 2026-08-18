"""Slack Bolt (Socket Mode) エントリ。

- DM 専用（message.im）＋チャンネルでの @メンション
- ゲート: 許可ユーザー / 許可チャンネル
- 受信即 👀 ack（生死確認）
- 起動時 catch-up（切断中の未処理 DM を拾う）

import 時の副作用は無い。組み立ては main()（テストは register_handlers / catch_up 単位で可能）。
"""
from __future__ import annotations

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from relay.config import Config
from relay.gate import is_allowed, normalize_text
from relay.orchestrator import Orchestrator


def register_handlers(app: App, cfg: Config, orch: Orchestrator) -> None:
    def _allowed(user_id: str, channel_id: str) -> bool:
        return is_allowed(user_id, channel_id,
                          cfg.allowed_user_ids, cfg.allowed_channel_ids)

    def _ack_eyes(channel: str, ts: str) -> None:
        try:
            app.client.reactions_add(channel=channel, timestamp=ts, name="eyes")
        except Exception as e:
            # ack は失敗しても処理は続けるが、沈黙させず必ずログに残す
            print(f"[relay] ⚠️ 👀 リアクション失敗: {e}", flush=True)

    def _accept(event) -> None:
        user = event.get("user", "")
        channel = event.get("channel", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts") or ts
        text = normalize_text(event.get("text", ""))
        if not _allowed(user, channel):
            return
        _ack_eyes(channel, ts)
        orch.runtime.mark_processed(ts)
        orch.handle(channel, user, thread_ts, text)

    @app.event("app_mention")
    def on_mention(event, logger):
        # チャンネルで @claude-relay とメンションされたとき（DM制限の回避経路）
        _accept(event)

    @app.event("message")
    def on_message(event, logger):
        # bot 自身・編集・システム系は無視
        if event.get("bot_id") or event.get("subtype"):
            return
        if event.get("channel_type") != "im":  # DM 専用
            return
        _accept(event)


def catch_up(app: App, cfg: Config, orch: Orchestrator) -> None:
    """起動時: 各許可ユーザーとの DM 履歴を last_processed_ts 以降で拾い直す。"""
    last = orch.runtime.last_processed_ts
    if not last:
        return
    try:
        convos = app.client.conversations_list(types="im").get("channels", [])
    except Exception:
        return
    for c in convos:
        ch = c.get("id")
        try:
            msgs = app.client.conversations_history(channel=ch, oldest=last).get("messages", [])
        except Exception:
            continue
        for m in sorted(msgs, key=lambda x: x.get("ts", "")):
            if m.get("bot_id") or m.get("subtype") or m.get("ts", "") <= last:
                continue
            user = m.get("user", "")
            if not is_allowed(user, ch, cfg.allowed_user_ids, cfg.allowed_channel_ids):
                continue
            ts = m.get("ts", "")
            thread_ts = m.get("thread_ts") or ts
            orch.post(ch, thread_ts, "⏰ 復帰しました。未処理の指示を処理します。")
            orch.runtime.mark_processed(ts)
            orch.handle(ch, user, thread_ts, normalize_text(m.get("text", "")))


def self_check(app: App) -> None:
    """起動時の疎通確認。失敗した経路を具体的に表示する。"""
    try:
        r = app.client.auth_test()
        print(f"  ✅ Slack Web API 疎通 OK（workspace: {r.get('team')} / bot: {r.get('user')}）")
    except Exception as e:
        print(f"  ❌ Slack Web API に接続できません（返信・リアクションが失敗します）: {e}")
        print("     ネットワーク/VPN/プロキシを確認して再起動してください。")


def main() -> None:
    cfg = Config.load()
    app = App(token=cfg.slack_bot_token)

    def poster(channel: str, thread_ts: str | None, text: str) -> None:
        try:
            app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
        except Exception as e:
            # Slack へ届けられない失敗は必ずコンソールに残す（無言で消さない）
            print(f"[relay] ❌ Slack への投稿失敗: {e} / text={text[:80]!r}", flush=True)
            raise

    orch = Orchestrator(cfg, poster)
    register_handlers(app, cfg, orch)

    print("[claude-code-slack] Socket Mode 起動")
    self_check(app)
    for rname, rpath in cfg.repos.items():
        mark = "*" if rname == cfg.default_repo else " "
        print(f"  repo {mark}{rname:<9} = {rpath}")
    print(f"  model/mode     = {orch.model} / {orch.permission_mode}")
    orch.start_watcher()
    catch_up(app, cfg, orch)
    SocketModeHandler(app, cfg.slack_app_token).start()


if __name__ == "__main__":
    main()
