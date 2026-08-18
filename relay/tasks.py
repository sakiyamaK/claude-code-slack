"""タスクのライフサイクル（1タスク=1 cmux セッション）。

新規指示のセッション割り当て（照合→リポジトリ振り分け→タブ作成）、
継続指示の注入、中断操作を担う。照合・振り分けの判定関数は注入される。
"""
from __future__ import annotations

import os
import re
import time
from typing import Callable, Protocol

from .cmux import CmuxClient, Session
from .config import Config
from .links import ThreadLinks
from .parsing import SessionRef, format_template
from .registry import (
    Task, TaskStore, MODE_TASK, STATUS_ACTIVE, now_iso,
)
from .settings import RuntimeSettings

# Slack への投稿: (channel, thread_ts, text) -> None
Poster = Callable[[str, str | None, str], None]

# 照合: (指示, 候補セッション) -> surface ref | None
PickSession = Callable[[str, list[Session]], str | None]
# 振り分け: (指示) -> リポジトリ名
PickRepo = Callable[[str], str]

_TASK_NAME_LEN = 24


class TicketFinder(Protocol):
    def __call__(self, text: str) -> str | None: ...


def active_instructed_threads(tasks: TaskStore, links: ThreadLinks) -> list[tuple[str, str]]:
    """追跡中タスク (thread_ts, 依頼内容)。依頼内容が残っているものだけ。"""
    out: list[tuple[str, str]] = []
    for t in tasks.active():
        if t.mode != MODE_TASK:
            continue
        instr = links.instruction_of(t.thread_ts)
        if instr:
            out.append((t.thread_ts, instr))
    return out


def _tab_matches(listed: str | None, expected: str | None) -> bool:
    """タブ名の照合。cmux は名前の先頭に状態アイコン（✳ ⠐ 等）を付けるため後方一致も許す。"""
    listed = (listed or "").strip()
    expected = (expected or "").strip()
    return bool(expected) and (listed == expected or listed.endswith(expected))


def resolve_surface(cmux: CmuxClient, links: ThreadLinks, thread_ts: str) -> str | None:
    """スレッドの surface を返す（生存確認＋本人確認込み）。

    surface ref は cmux 再起動で振り直される借り物の番号。同じ ref が別のタブを
    指すことがあるため、relay が命名したタブ（tab_name 記録あり）は名前で本人確認し、
    別物なら正しいタブを名前で探し直して紐付けを張り直す。
    ID 指名で合流したスレッド（workspace UUID 記録あり）は UUID から引き直す。
    どちらの記録も無い紐付け（自動照合での合流）は ref の存在確認のみ。
    """
    surface = links.surface_of(thread_ts)
    if not surface:
        return None
    workspace = links.workspace_of(thread_ts)
    if workspace:
        # UUID は cmux 再起動をまたいで不変。そこから今の ref を引き直す
        refs = cmux.workspace_surfaces(workspace)
        if surface in refs:
            return surface
        if refs:
            links.link_surface(thread_ts, refs[0])
            return refs[0]
        return None
    tab = links.tab_name_of(thread_ts)
    names = cmux.surface_names()
    listed = names.get(surface)
    if listed is not None and (not tab or _tab_matches(listed, tab)):
        return surface
    # ref が消えた／別のタブに再割り当てされていた → タブ名で再発見
    if not tab:
        return None
    taken = links.linked_surfaces(exclude=thread_ts)
    for ref, name in names.items():
        if _tab_matches(name, tab) and ref not in taken:
            links.link_surface(thread_ts, ref)
            return ref
    return None


class TaskService:
    def __init__(self, cfg: Config, cmux: CmuxClient, tasks: TaskStore,
                 links: ThreadLinks, runtime: RuntimeSettings, poster: Poster,
                 pick_session: PickSession, pick_repo: PickRepo,
                 find_ticket: TicketFinder) -> None:
        self.cfg = cfg
        self.cmux = cmux
        self.tasks = tasks
        self.links = links
        self.runtime = runtime
        self.post = poster
        self._pick_session = pick_session
        self._pick_repo = pick_repo
        self._find_ticket = find_ticket

    # ── 新規指示 ────────────────────────────────────────
    def new_task(self, channel: str, user: str, thread_ts: str, body: str) -> None:
        """既存セッションに該当作業があれば連携、無ければタブを増やす。"""
        self.links.set_instruction(thread_ts, body)
        self.ensure_row(channel, user, thread_ts)
        self.cmux.ensure_running()
        prompt = format_template(self.cfg.cmux_prompt, {"task": body, "id": thread_ts})
        # 既存セッションとの照合（他スレッドに紐付き済み・relay 自身のタブは候補外）
        # 画面も読む: いま何の話をしているかが照合の一番強い手掛かりになる
        taken = self.links.linked_surfaces(exclude=thread_ts)
        taken.add(self.cmux.own_surface() or "")
        candidates = [s for s in self.cmux.list_sessions(with_screens=True)
                      if s.surface not in taken and s.agent]
        picked_ref = self._pick_session(body, candidates)
        if picked_ref:
            picked = next(s for s in candidates if s.surface == picked_ref)
            self.links.link_surface(thread_ts, picked_ref)
            # タブ名は記録しない: 手動タブは cmux が話題で自動改名するため
            # 名前による本人確認・再発見は relay 命名タブに限る
            self._record_repo_from_cwd(thread_ts, picked.cwd)
            self.cmux.send(picked_ref, prompt)
            label = picked.title or picked.name
            self.post(channel, thread_ts,
                      f"🔗 進行中のセッション「{label}」にこの指示を連携しました。"
                      "以降このスレッドがそのセッションの窓口になります。")
            return
        # 新規タブ（作業先リポジトリのルーティング: 名前の明示 > AI 判定 > 既定）
        name = self._task_name(body)
        repo = self._pick_repo(body)
        self.links.set_repo(thread_ts, repo)
        label = f"（{repo}）" if len(self.cfg.repos) > 1 else ""
        self.post(channel, thread_ts,
                  f"🚀 新しいセッション「{name}」{label}を cmux に作って開始します…")
        surface = self.create_session(thread_ts, name, self.cfg.repos[repo])
        self.cmux.send(surface, prompt)
        self.post(channel, thread_ts,
                  "タブを作成しました（cmux の UI からも確認できます）。進捗はこのスレッドに通知します。")

    # ── ID 指名での合流 ──────────────────────────────────
    def attach_session(self, channel: str, user: str, thread_ts: str,
                       ref: SessionRef) -> bool:
        """cmux の workspace_id / surface_id を貼られたら、そのタブを窓口にする。

        すでに cmux で進めている作業に Slack から合流するための入口。
        照合（AI 判定）を通さず名指しするので、新しいタブは作らない。
        紐付けだけを行い、添えられた指示の扱いは呼び側（router）に任せる。
        紐付けできなければ False（呼び側が通常処理に回すか案内するかを決める）。
        """
        self.cmux.ensure_running()
        session = (self.cmux.find_surface(ref.session_id) if ref.is_surface
                   else self.cmux.workspace_session(ref.session_id))
        if session is None:
            # workspace_id / surface_id の取り違えを救う（どちらも `identify` に並ぶ）
            session = (self.cmux.workspace_session(ref.session_id) if ref.is_surface
                       else self.cmux.find_surface(ref.session_id))
        if session is None:
            if ref.explicit:  # 目印付きの指名だけ案内する（裸の UUID は指示文の一部かもしれない）
                self.post(channel, thread_ts,
                          f"⚠️ ID `{ref.session_id}` のセッションが cmux に見つかりません。"
                          "合流したいタブで `cmux identify --id-format both` を実行し、"
                          "`workspace_id` の値を貼ってください。")
            return False
        self.ensure_row(channel, user, thread_ts)
        # 1セッション=1窓口。同じタブを見ていた別スレッドの紐付けは外す（二重通知を防ぐ）
        stolen = self.links.threads_for_surface(session.surface, exclude=thread_ts)
        for other in stolen:
            self.links.unlink_surface(other)
        self.links.unlink_surface(thread_ts)  # 別タブに紐付いていた場合の付け替え
        self.links.link_surface(thread_ts, session.surface)
        self.links.set_workspace(thread_ts, session.workspace_id or session.workspace)
        self._record_repo_from_cwd(thread_ts, session.cwd)
        label = session.name or session.title or session.surface
        if ref.rest:
            self.links.set_instruction(thread_ts, ref.rest)
        elif not self.links.instruction_of(thread_ts):
            # 監視ループは依頼内容のあるスレッドだけ追跡するため、最低限の記録を残す
            self.links.set_instruction(thread_ts, f"cmux セッション「{label}」への合流")
        note = "（このタブを見ていた別スレッドの紐付けは解除しました）" if stolen else ""
        self.post(channel, thread_ts,
                  f"🔗 cmux のセッション「{label}」に接続しました{note}。"
                  "以降このスレッドがそのセッションの窓口になります（進捗もここに届きます）。")
        return True

    # ── 継続指示 ────────────────────────────────────────
    def follow_up(self, channel: str, user: str, thread_ts: str, text: str) -> None:
        """タスクスレッドの2通目以降。紐付きセッションへ注入する。"""
        # done 後の追加指示も追跡に戻す（監視ループが再び拾う）
        self.tasks.update(thread_ts, status=STATUS_ACTIVE)
        self.cmux.ensure_running()
        surface = self.resolve_surface(thread_ts)
        if surface:
            if self.cmux.surface_has_process(surface):
                # 返答は監視ループが AI 解釈してこのスレッドに返す（受領 ack は 👀 で済んでいる）
                self.cmux.send(surface, text)
                return
            # claude が終了している → シェルに指示を打ち込まず、同じタブ・同じ会話で復活を試みる
            self.post(channel, thread_ts,
                      "♻️ セッションの claude が終了していたため、同じタブで会話を引き継いで再開します…")
            if self.cmux.revive_session(surface, self.cfg.claude_bin,
                                        self.runtime.model, self.runtime.permission_mode):
                self.cmux.send(surface, text)
                return
            # 復活できなかった → タブ消失時と同じ作り直しフローへ
        # タブが閉じられていた → 記録済みのリポジトリで作り直して文脈を添える
        instr = self.links.instruction_of(thread_ts)
        self.post(channel, thread_ts,
                  "⚠️ 連携していたセッションが見つかりません。新しいセッションを作って続けます…")
        repo = self.links.repo_of(thread_ts) or ""
        cwd = self.cfg.repos.get(repo)
        surface = self.create_session(thread_ts, self._task_name(instr or text), cwd)
        ctx = f"（元の依頼「{instr[:120]}」の続き）" if instr else ""
        self.cmux.send(surface, f"{ctx}{text}")

    # ── 中断操作 ────────────────────────────────────────
    def operation(self, channel: str, thread_ts: str, text: str) -> None:
        """「やめて」等。紐付いたセッションに割り込んで中断を指示する。"""
        self.cmux.ensure_running()
        surface = self.resolve_surface(thread_ts)
        if not surface:
            self.post(channel, thread_ts,
                      "このスレッドに紐付いたセッションがありません。"
                      "中断したいタスクのスレッドで指示してください。")
            return
        if not self.cmux.surface_has_process(surface):
            self.post(channel, thread_ts,
                      "セッションの claude は既に終了しています（作業は止まっています）。")
            return
        self.cmux.interrupt(surface)  # 実行中なら ESC で止めてから指示を渡す
        time.sleep(0.5)
        self.cmux.send(surface, f"（Slackからの指示）作業を中断してください: {text}")
        self.post(channel, thread_ts, "🛑 セッションに中断を指示しました。")

    # ── 共有ヘルパ ──────────────────────────────────────
    def ensure_row(self, channel: str, user: str, thread_ts: str) -> None:
        existing = self.tasks.get(thread_ts)
        if existing is None:
            self.tasks.create(Task(
                thread_ts=thread_ts, channel_id=channel, user_id=user, mode=MODE_TASK,
                branch=None, session_id=None, anchor_ts=None, status=STATUS_ACTIVE,
                created_at=now_iso(), updated_at=now_iso()))
        elif existing.mode != MODE_TASK or existing.status != STATUS_ACTIVE:
            self.tasks.update(thread_ts, mode=MODE_TASK, status=STATUS_ACTIVE)

    def create_session(self, thread_ts: str, name: str, cwd: str | None = None) -> str:
        """タスク用の cmux タブを作って紐付ける（タブ名は再発見用の目印として記録）。"""
        surface = self.cmux.new_session(
            name, cwd or self.cfg.target_repo, self.cfg.claude_bin,
            self.runtime.model, self.runtime.permission_mode)
        self.links.link_surface(thread_ts, surface)
        self.links.set_tab_name(thread_ts, name)
        return surface

    def resolve_surface(self, thread_ts: str) -> str | None:
        return resolve_surface(self.cmux, self.links, thread_ts)

    def has_active(self) -> bool:
        return bool(active_instructed_threads(self.tasks, self.links))

    def _record_repo_from_cwd(self, thread_ts: str, cwd: str | None) -> None:
        """タブ再作成に備えて、cwd がどの repo 配下かを記録する。"""
        for rn, rp in self.cfg.repos.items():
            if cwd and (cwd == rp or cwd.startswith(rp + os.sep)):
                self.links.set_repo(thread_ts, rn)
                break

    def _task_name(self, body: str) -> str:
        """新規タブの名前。チケットIDがあればそれ、無ければ指示の先頭。"""
        ticket = self._find_ticket(body)
        if ticket:
            return ticket
        name = re.sub(r"\s+", " ", (body or "").strip())
        return name[:_TASK_NAME_LEN] or "slack-task"
