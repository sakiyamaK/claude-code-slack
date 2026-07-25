"""Slack Bolt (Socket Mode) エントリ（SPEC §2, §4, §10, §11, §16）。

- DM 専用（message.im）
- ゲート: 許可ユーザー / 許可チャンネル
- 受信即 👀 ack（生死確認）
- 起動時 catch-up（切断中の未処理 DM を拾う）
"""
from __future__ import annotations

import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from relay.config import Config
from relay.orchestrator import Orchestrator

cfg = Config.load()
app = App(token=cfg.slack_bot_token)

_MENTION_RE = re.compile(r"<@[^>]+>")


def _post(channel: str, thread_ts: str | None, text: str) -> None:
    app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)


orch = Orchestrator(cfg, _post)


def _strip(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _allowed(user_id: str, channel_id: str) -> bool:
    if cfg.allowed_user_ids and user_id not in cfg.allowed_user_ids:
        return False
    if cfg.allowed_channel_ids and channel_id not in cfg.allowed_channel_ids:
        return False
    return True


def _ack_eyes(channel: str, ts: str) -> None:
    try:
        app.client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        pass


@app.event("app_mention")
def on_mention(event, logger):
    # チャンネルで @claude-relay とメンションされたとき（DM制限の回避経路）
    user = event.get("user", "")
    channel = event.get("channel", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or ts
    text = _strip(event.get("text", ""))
    if not _allowed(user, channel):
        return
    _ack_eyes(channel, ts)
    orch.registry.set_setting("last_processed_ts", ts)
    orch.handle(channel, user, thread_ts, text)


@app.event("message")
def on_message(event, logger):
    # bot 自身・編集・システム系は無視
    if event.get("bot_id") or event.get("subtype"):
        return
    if event.get("channel_type") != "im":  # DM 専用
        return
    user = event.get("user", "")
    channel = event.get("channel", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or ts
    text = _strip(event.get("text", ""))

    if not _allowed(user, channel):
        return
    _ack_eyes(channel, ts)
    orch.registry.set_setting("last_processed_ts", ts)
    orch.handle(channel, user, thread_ts, text)


def _catch_up() -> None:
    """起動時: 各許可ユーザーとの DM 履歴を last_processed_ts 以降で拾い直す。"""
    last = orch.registry.get_setting("last_processed_ts")
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
            if not _allowed(user, ch):
                continue
            ts = m.get("ts", "")
            thread_ts = m.get("thread_ts") or ts
            _post(ch, thread_ts, "⏰ 復帰しました。未処理の指示を処理します。")
            orch.registry.set_setting("last_processed_ts", ts)
            orch.handle(ch, user, thread_ts, _strip(m.get("text", "")))


def main() -> None:
    print("[claude-code-slack] Socket Mode 起動")
    print(f"  target_repo    = {cfg.target_repo}")
    print(f"  workspace_dir  = {cfg.workspace_dir}")
    print(f"  max_concurrent = {cfg.max_concurrent}")
    print(f"  model/mode     = {orch.model} / {orch.permission_mode}")
    orch.start_watcher()
    _catch_up()
    SocketModeHandler(app, cfg.slack_app_token).start()


if __name__ == "__main__":
    main()
