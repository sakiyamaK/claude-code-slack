"""dashboard.md の監視（SPEC §12）。

マネージャーが書く dashboard.md を relay がポーリングし、状態遷移から
通知イベント（着手/実装完了/要判断🚨/停滞⚠️/完了✅）を生成する。

dashboard 表記 → 節目のマッピングは設定可能（既定値は下記）。実装細部は運用調整（SPEC §22）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# 既定マッピング（status 文字列に含まれれば該当節目とみなす）
REVIEW_MARKERS = ["レビュー"]            # 🔨 実装・テスト完了→レビュー入り
STALL_MARKERS = ["警告", "停滞", "[警告]"]  # ⚠️ 停滞

# 行内の from-thread 併記（manager.agent.md 追加ルール, SPEC §19）
_FROM_THREAD = re.compile(r"from-thread:\s*([0-9.]+)")
_FROM_THREAD_COMMENT = re.compile(r"\s*<!--\s*from-thread:[^>]*-->")


def strip_tag(s: str) -> str:
    """表示用に from-thread コメントを除去する。"""
    return _FROM_THREAD_COMMENT.sub("", s).strip()


@dataclass
class TaskRow:
    name: str
    status: str
    thread_ts: str | None
    section: str  # "running" | "done"


@dataclass
class Alert:
    text: str
    thread_ts: str | None


@dataclass
class Report:
    name: str            # セクション見出し
    thread_ts: str | None
    body: str            # セクション本文（報告内容）


@dataclass
class DashboardState:
    tasks: dict[str, TaskRow] = field(default_factory=dict)   # name -> TaskRow
    alerts: list[Alert] = field(default_factory=list)          # 🚨 セクション
    reports: dict[str, Report] = field(default_factory=dict)   # 報告のみタスクの結果セクション


def _thread_of(line: str) -> str | None:
    m = _FROM_THREAD.search(line)
    return m.group(1) if m else None


def _split_sections(text: str) -> dict[str, str]:
    """"## 見出し" ごとに本文を分割する。"""
    sections: dict[str, str] = {}
    cur = "_head"
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            sections[cur] = "\n".join(buf)
            cur = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    sections[cur] = "\n".join(buf)
    return sections


def _parse_table_rows(body: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # 区切り行（---）やヘッダ行は除外
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if cells and cells[0] in ("タスク", "task", ""):
            continue
        rows.append(cells)
    return rows


def parse_dashboard(text: str) -> DashboardState:
    state = DashboardState()
    sections = _split_sections(text)

    for name, body in sections.items():
        if name.startswith("実行中"):
            for cells in _parse_table_rows(body):
                if not cells:
                    continue
                state.tasks[cells[0]] = TaskRow(
                    name=cells[0],
                    status=cells[1] if len(cells) > 1 else "",
                    thread_ts=_thread_of(" ".join(cells)),
                    section="running",
                )
        elif name.startswith("完了タスク"):
            for cells in _parse_table_rows(body):
                if not cells:
                    continue
                state.tasks.setdefault(cells[0], TaskRow(
                    name=cells[0], status="完了",
                    thread_ts=_thread_of(" ".join(cells)), section="done",
                ))
                state.tasks[cells[0]].section = "done"
        elif "確認が必要" in name or name.startswith("🚨"):
            for line in body.splitlines():
                s = line.strip().lstrip("-・ ").strip()
                if not s or s in ("（なし）", "(なし)"):
                    continue
                state.alerts.append(Alert(text=strip_tag(s), thread_ts=_thread_of(s)))
        else:
            # 上記以外のセクションで、見出し or 本文に from-thread タグがあれば
            # 「報告のみタスク」の結果セクションとみなす（manager.agent.md 準拠）
            tt = _thread_of(name) or _thread_of(body)
            if tt:
                state.reports[strip_tag(name)] = Report(
                    name=strip_tag(name), thread_ts=tt, body=strip_tag(body).strip())
    return state


# ── 差分 → 通知イベント ──────────────────────────────────
@dataclass
class Event:
    kind: str          # "review" | "done" | "alert" | "stall"
    task: str
    thread_ts: str | None
    detail: str = ""


def _has(markers: list[str], s: str) -> bool:
    return any(m in s for m in markers)


def diff_events(prev: DashboardState | None, cur: DashboardState) -> list[Event]:
    """前回状態との差分から通知すべきイベントを列挙する。"""
    events: list[Event] = []
    prev_tasks = prev.tasks if prev else {}

    for name, row in cur.tasks.items():
        old = prev_tasks.get(name)
        # 完了へ遷移
        if row.section == "done" and (old is None or old.section != "done"):
            events.append(Event("done", name, row.thread_ts))
            continue
        if row.section != "running":
            continue
        old_status = old.status if old else ""
        # 停滞（新たに警告が付いた）
        if _has(STALL_MARKERS, row.status) and not _has(STALL_MARKERS, old_status):
            events.append(Event("stall", name, row.thread_ts, row.status))
        # 実装・テスト完了→レビュー入り（レビュー系へ遷移）
        elif _has(REVIEW_MARKERS, row.status) and not _has(REVIEW_MARKERS, old_status):
            events.append(Event("review", name, row.thread_ts, row.status))

    # 🚨 新規アラート
    prev_alerts = {a.text for a in (prev.alerts if prev else [])}
    for a in cur.alerts:
        if a.text not in prev_alerts:
            events.append(Event("alert", "", a.thread_ts, a.text))

    # 報告のみタスクの結果セクション（新規出現）
    prev_reports = prev.reports if prev else {}
    for name, rep in cur.reports.items():
        if name not in prev_reports:
            events.append(Event("report", name, rep.thread_ts, rep.body))
    return events
