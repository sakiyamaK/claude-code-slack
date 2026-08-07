"""コマンド処理（/model /mode /commit /push＋config 定義のカスタムコマンド）。

/model /mode は番号ピッカーで RuntimeSettings を更新する。
カスタムコマンドはスレッドのセッションへ指示を注入する（未紐付けならタブを作る）。
"""
from __future__ import annotations

import subprocess
import re
from typing import Callable

from .config import Config
from .cmux import CmuxClient
from .links import ThreadLinks
from .parsing import format_template, parse_version_spec
from .registry import TaskStore, MODE_TASK
from .settings import RuntimeSettings
from .tasks import TaskService, Poster, active_instructed_threads

MODEL_CHOICES = [("fable", "Fable 5"), ("opus", "Opus 4.8"),
                 ("sonnet", "Sonnet 5"), ("haiku", "Haiku 4.5")]
MODE_CHOICES = [
    ("default", "default(都度確認)"),
    ("acceptEdits", "auto(編集自動許可)"),
    ("plan", "plan(計画のみ)"),
]
BYPASS_CHOICE = ("bypassPermissions", "yolo(全許可)")

_VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _default_shell_runner(command: str, cwd: str) -> str:
    return subprocess.run(command, shell=True, cwd=cwd,
                          capture_output=True, text=True, timeout=15).stdout


class CommandService:
    def __init__(self, cfg: Config, cmux: CmuxClient, tasks: TaskService,
                 task_store: TaskStore, links: ThreadLinks,
                 runtime: RuntimeSettings, poster: Poster,
                 shell_runner: Callable[[str, str], str] | None = None) -> None:
        self.cfg = cfg
        self.cmux = cmux
        self.tasks = tasks
        self.task_store = task_store
        self.links = links
        self.runtime = runtime
        self.post = poster
        self._shell = shell_runner or _default_shell_runner
        self._pending: dict[str, str] = {}  # thread_ts -> "model" | "mode"

    # ── 入口 ────────────────────────────────────────────
    def handle(self, channel: str, user: str, thread_ts: str, name: str, arg: str) -> None:
        if name in ("model", "mode"):
            if arg.strip():
                self._set_direct(channel, thread_ts, name, arg.strip())
            else:
                self._show_picker(channel, thread_ts, name)
        elif name in ("commit", "push"):
            self._git_command(channel, thread_ts, name)
        elif name == "status":
            self._status(channel, thread_ts)
        elif name == "unlink":
            self._unlink(channel, thread_ts)
        elif name in self.cfg.commands:
            self._custom_command(channel, user, thread_ts, name, arg)
        else:
            self.post(channel, thread_ts,
                      f"未知のコマンド: /{name}。config.yml の commands で定義できます。")

    # ── /model /mode（引数で直接指定 or 番号ピッカー） ──
    _MODE_ALIASES = {
        "default": "default", "auto": "acceptEdits", "acceptedits": "acceptEdits",
        "plan": "plan", "yolo": "bypassPermissions", "bypasspermissions": "bypassPermissions",
    }

    def _set_direct(self, channel: str, thread_ts: str, kind: str, arg: str) -> None:
        """`/model fable` `/mode yolo` のような直接指定。"""
        if kind == "model":
            valid = [k for k, _ in MODEL_CHOICES]
            value = arg.lower()
            if value not in valid:
                self.post(channel, thread_ts,
                          f"指定できるモデル: {' / '.join(valid)}")
                return
            self.runtime.set_model(value)
        else:
            value = self._MODE_ALIASES.get(arg.lower())
            if value is None or (value == "bypassPermissions" and not self.cfg.allow_bypass):
                names = ["default", "auto", "plan"] + (["yolo"] if self.cfg.allow_bypass else [])
                self.post(channel, thread_ts, f"指定できるモード: {' / '.join(names)}")
                return
            self.runtime.set_permission_mode(value)
        self.post(channel, thread_ts,
                  f"✅ {('モデル' if kind == 'model' else '動作モード')}を「{value}」にしました"
                  "（以降の新しいセッションから適用）。")

    def has_pending_pick(self, thread_ts: str) -> bool:
        return thread_ts in self._pending

    def cancel_pick(self, thread_ts: str) -> None:
        """番号以外の発言が来たらピッカーを破棄する（あとから数字を送っても誤発動しない）。"""
        self._pending.pop(thread_ts, None)

    def _choices(self, kind: str) -> list[tuple[str, str]]:
        if kind == "model":
            return MODEL_CHOICES
        opts = list(MODE_CHOICES)
        if self.cfg.allow_bypass:
            opts.append(BYPASS_CHOICE)
        return opts

    def _show_picker(self, channel: str, thread_ts: str, kind: str) -> None:
        cur = self.runtime.model if kind == "model" else self.runtime.permission_mode
        label = "モデル" if kind == "model" else "動作モード"
        lines = [f"現在: {cur}。{label}を選んでください（番号を返信）:"]
        for i, (_, disp) in enumerate(self._choices(kind), 1):
            lines.append(f" {i} {disp}")
        self._pending[thread_ts] = kind
        self.post(channel, thread_ts, "\n".join(lines))

    def apply_pick(self, channel: str, thread_ts: str, num: int) -> None:
        kind = self._pending.pop(thread_ts)
        opts = self._choices(kind)
        if not (1 <= num <= len(opts)):
            self.post(channel, thread_ts, "範囲外の番号です。もう一度コマンドを打ってください。")
            return
        value, disp = opts[num - 1]
        if kind == "model":
            self.runtime.set_model(value)
        else:
            self.runtime.set_permission_mode(value)
        self.post(channel, thread_ts,
                  f"✅ {('モデル' if kind == 'model' else '動作モード')}を「{disp}」にしました"
                  "（以降の新しいセッションから適用）。")

    # ── /status /unlink（紐付けの可視化と修正） ─────────
    def _status(self, channel: str, thread_ts: str) -> None:
        surface = self.tasks.resolve_surface(thread_ts)
        if surface:
            t = self.task_store.get(thread_ts)
            lines = ["このスレッドの紐付け:"]
            lines.append(f"- セッション: {surface}"
                         f"（タブ名: {self.links.tab_name_of(thread_ts) or '不明'}）")
            repo = self.links.repo_of(thread_ts)
            if repo:
                lines.append(f"- 作業先: {repo}（{self.cfg.repos.get(repo, '?')}）")
            instr = self.links.instruction_of(thread_ts)
            if instr:
                lines.append(f"- 依頼内容: {instr[:120]}")
            if t:
                lines.append(f"- 状態: {t.status}")
            alive = self.cmux.surface_has_process(surface)
            lines.append(f"- claude: {'稼働中' if alive else '終了している（続きを指示すれば再開）'}")
        else:
            active = active_instructed_threads(self.task_store, self.links)
            lines = ["このスレッドはどのセッションにも紐付いていません。"]
            if active:
                lines.append("追跡中のタスク:")
                lines += [f"- {instr[:60]}" for _, instr in active]
            lines.append(f"モデル: {self.runtime.model} / モード: {self.runtime.permission_mode}")
        self.post(channel, thread_ts, "\n".join(lines))

    def _unlink(self, channel: str, thread_ts: str) -> None:
        if not self.links.surface_of(thread_ts):
            self.post(channel, thread_ts, "このスレッドは紐付いていません。")
            return
        self.links.unlink_surface(thread_ts)
        self.post(channel, thread_ts,
                  "🔓 このスレッドとセッションの紐付けを解除しました。"
                  "セッション自体は残っています（他のスレッドから合流可能に戻ります）。"
                  "このスレッドへの次の指示は新しいタスクとして扱われます。")

    # ── /commit /push ───────────────────────────────────
    def _git_command(self, channel: str, thread_ts: str, name: str) -> None:
        self.cmux.ensure_running()
        surface = self.tasks.resolve_surface(thread_ts)
        if not surface:
            self.post(channel, thread_ts, "このスレッドはタスクに紐付いていません。どのタスクですか？")
            return
        if name == "commit":
            self.cmux.send(surface, "承認します。この作業の成果をコミットしてください。")
            self.post(channel, thread_ts, "📝 コミットをセッションに指示しました。")
        else:
            self.cmux.send(surface, "この作業のブランチを origin に push してください。")
            self.post(channel, thread_ts, "⬆️ push をセッションに指示しました。")

    # ── カスタムコマンド ─────────────────────────────────
    def _task_ref(self, thread_ts: str) -> str | None:
        """このスレッドのタスクを指す参照（依頼内容）。{task} プレースホルダに使う。"""
        t = self.task_store.get(thread_ts)
        if not t or t.mode != MODE_TASK:
            return None
        return self.links.instruction_of(thread_ts)[:120] or None

    def _custom_command(self, channel: str, user: str, thread_ts: str,
                        name: str, arg: str) -> None:
        spec = self.cfg.commands[name] or {}
        ref = self._task_ref(thread_ts)
        prompt_tpl = spec.get("prompt", "")
        if not ref and ("{task}" in prompt_tpl or "{branch}" in prompt_tpl):
            self.post(channel, thread_ts, "このスレッドはタスクに紐付いていません。")
            return
        ctx = {"task": ref or "", "branch": ref or "", "arg": arg}
        # バージョン記法を使うコマンド（配信等）
        if spec.get("version"):
            cur = self.current_version()
            try:
                vr = parse_version_spec(arg or None, cur)
            except ValueError as e:
                self.post(channel, thread_ts, f"❌ {e}")
                return
            ctx["version"] = ".".join(map(str, vr.version))
            if vr.note:
                self.post(channel, thread_ts, f"（{vr.note}）")
        confirm = spec.get("confirm")
        if confirm:
            self.post(channel, thread_ts, format_template(confirm, ctx))
        # セッションへ指示投入（未紐付けスレッドならコマンド用のタブを作る）
        prompt = format_template(spec.get("prompt", ""), ctx)
        self.cmux.ensure_running()
        surface = self.tasks.resolve_surface(thread_ts)
        if not surface:
            self.tasks.ensure_row(channel, user, thread_ts)
            if not self.links.instruction_of(thread_ts):
                self.links.set_instruction(thread_ts, prompt)
            surface = self.tasks.create_session(thread_ts, f"/{name}")
        self.cmux.send(surface, prompt)

    def current_version(self) -> tuple[int, int, int]:
        """config の version_source（シェルコマンド）を実行し、出力から X.Y.Z を拾う。"""
        try:
            out = self._shell(self.cfg.version_source, self.cfg.target_repo)
            m = _VER_RE.search(out or "")
            if m:
                return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
        return (0, 0, 0)
