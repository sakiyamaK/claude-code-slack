"""CommandService のテスト。"""
import dataclasses

import pytest

from relay.commands import CommandService
from relay.match import find_ticket
from relay.tasks import TaskService
from tests.conftest import FakeCmux, make_session


@pytest.fixture
def build(cfg, task_store, links, runtime, poster):
    def _make(fake_cmux, cfg_override=None, shell=lambda cmd, cwd: "v1.2.3"):
        c = cfg_override or cfg
        tasks = TaskService(c, fake_cmux, task_store, links, runtime, poster,
                            pick_session=lambda b, cands: None,
                            pick_repo=lambda b: "ios", find_ticket=find_ticket)
        return CommandService(c, fake_cmux, tasks, task_store, links,
                              runtime, poster, shell_runner=shell)
    return _make


class TestPicker:
    def test_model_picker_flow(self, build, runtime, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "111.1", "model", "")
        assert svc.has_pending_pick("111.1")
        assert "Fable 5" in posts[-1][2]
        svc.apply_pick("C1", "111.1", 2)                 # Opus 4.8
        assert runtime.model == "opus"
        assert not svc.has_pending_pick("111.1")

    def test_mode_picker_without_bypass(self, build, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "111.1", "mode", "")
        assert "yolo" not in posts[-1][2]                # allow_bypass=False

    def test_mode_picker_with_bypass(self, build, cfg, posts):
        cfg2 = dataclasses.replace(cfg, allow_bypass=True)
        svc = build(FakeCmux(), cfg_override=cfg2)
        svc.handle("C1", "U1", "111.1", "mode", "")
        assert "yolo" in posts[-1][2]

    def test_out_of_range(self, build, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "111.1", "model", "")
        svc.apply_pick("C1", "111.1", 99)
        assert "範囲外" in posts[-1][2]


class TestGit:
    def test_commit_needs_link(self, build, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "111.1", "commit", "")
        assert "紐付いていません" in posts[-1][2]

    def test_commit_sends_to_session(self, build, links, cfg, posts):
        cmux = FakeCmux([make_session(cwd=cfg.repos["ios"])])
        svc = build(cmux)
        links.link_surface("111.1", "surface:10")
        svc.handle("C1", "U1", "111.1", "commit", "")
        assert any("コミット" in t for s, t in cmux.sent if s == "surface:10")


class TestCustom:
    def test_unknown_command(self, build, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "111.1", "nazo", "")
        assert "未知のコマンド" in posts[-1][2]

    def test_custom_with_version_creates_tab_for_unbound_thread(
            self, build, cfg, links, posts):
        cfg2 = dataclasses.replace(cfg, commands={
            "deploy": {"version": True,
                       "confirm": "v{version} でデプロイします。",
                       "prompt": "v{version} をデプロイして。"}})
        cmux = FakeCmux()
        svc = build(cmux, cfg_override=cfg2)
        svc.handle("C1", "U1", "111.1", "deploy", "X.X.X+1")
        assert any("v1.2.4 でデプロイします" in p[2] for p in posts)
        assert cmux.created and cmux.created[0][0] == "/deploy"
        assert ("v1.2.4 をデプロイして。" == cmux.sent[-1][1])
        assert links.surface_of("111.1")

    def test_task_placeholder_requires_link(self, build, cfg, posts):
        cfg2 = dataclasses.replace(
            cfg, commands={"report": {"prompt": "「{task}」を報告して"}})
        svc = build(FakeCmux(), cfg_override=cfg2)
        svc.handle("C1", "U1", "111.1", "report", "")
        assert "紐付いていません" in posts[-1][2]

    def test_current_version_parses(self, build):
        svc = build(FakeCmux(), shell=lambda cmd, cwd: "release v3.14.15 (stable)")
        assert svc.current_version() == (3, 14, 15)

    def test_current_version_failure_is_zero(self, build):
        def boom(cmd, cwd):
            raise RuntimeError("no git")
        svc = build(FakeCmux(), shell=boom)
        assert svc.current_version() == (0, 0, 0)


class TestDirectArgs:
    def test_model_direct(self, build, runtime, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "1.1", "model", "fable")
        assert runtime.model == "fable"
        assert "✅" in posts[-1][2]

    def test_model_invalid_lists_choices(self, build, runtime, posts):
        svc = build(FakeCmux())
        before = runtime.model
        svc.handle("C1", "U1", "1.1", "model", "gpt")
        assert runtime.model == before
        assert "指定できるモデル" in posts[-1][2]

    def test_mode_alias_yolo_requires_allow_bypass(self, build, runtime, posts):
        svc = build(FakeCmux())                          # allow_bypass=False
        svc.handle("C1", "U1", "1.1", "mode", "yolo")
        assert runtime.permission_mode != "bypassPermissions"
        assert "指定できるモード" in posts[-1][2]

    def test_mode_auto_alias(self, build, runtime):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "1.1", "mode", "auto")
        assert runtime.permission_mode == "acceptEdits"

    def test_cancel_pick(self, build):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "1.1", "model", "")
        assert svc.has_pending_pick("1.1")
        svc.cancel_pick("1.1")
        assert not svc.has_pending_pick("1.1")


class TestStatusUnlink:
    def test_status_linked(self, build, cfg, links, posts):
        cmux = FakeCmux([make_session(cwd=cfg.repos["ios"])])
        svc = build(cmux)
        links.link_surface("1.1", "surface:10")
        links.set_tab_name("1.1", "A機能の実装")
        links.set_repo("1.1", "ios")
        links.set_instruction("1.1", "Aを直して")
        svc.handle("C1", "U1", "1.1", "status", "")
        body = posts[-1][2]
        assert "surface:10" in body and "A機能の実装" in body
        assert "ios" in body and "Aを直して" in body and "稼働中" in body

    def test_status_unlinked_lists_active(self, build, task_store, links, posts):
        svc = build(FakeCmux())
        svc.tasks.ensure_row("C1", "U1", "9.9")
        links.set_instruction("9.9", "別の作業")
        svc.handle("C1", "U1", "1.1", "status", "")
        body = posts[-1][2]
        assert "紐付いていません" in body and "別の作業" in body

    def test_unlink_frees_surface_for_matching(self, build, cfg, links, posts):
        cmux = FakeCmux([make_session(cwd=cfg.repos["ios"])])
        svc = build(cmux)
        links.link_surface("1.1", "surface:10")
        links.set_tab_name("1.1", "A機能の実装")
        svc.handle("C1", "U1", "1.1", "unlink", "")
        assert links.surface_of("1.1") is None
        assert links.linked_surfaces() == set()          # 照合候補に戻る
        assert "🔓" in posts[-1][2]

    def test_unlink_without_link(self, build, posts):
        svc = build(FakeCmux())
        svc.handle("C1", "U1", "1.1", "unlink", "")
        assert "紐付いていません" in posts[-1][2]
