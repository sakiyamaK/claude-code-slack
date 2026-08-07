"""SessionWatcher のテスト（デバウンス・重複排除・done 反映・タブ消失）。"""
import pytest

from relay.interpret import InterpEvent
from relay.registry import STATUS_ACTIVE, STATUS_DONE
from relay.tasks import TaskService
from relay.match import find_ticket
from relay.watcher import SessionWatcher
from tests.conftest import FakeCmux, make_session


@pytest.fixture
def setup(cfg, task_store, links, runtime, poster):
    """追跡中スレッド 111.1（surface:10）を用意する。"""
    cmux = FakeCmux([make_session(cwd=cfg.repos["ios"])])
    tasks = TaskService(cfg, cmux, task_store, links, runtime, poster,
                        pick_session=lambda b, c: None,
                        pick_repo=lambda b: "ios", find_ticket=find_ticket)
    tasks.ensure_row("C1", "U1", "111.1")
    links.link_surface("111.1", "surface:10")
    links.set_instruction("111.1", "Aを直して")
    return cmux, task_store, links


def make_watcher(cmux, task_store, links, poster, events_per_call):
    calls = {"n": 0}
    def interpreter(content, tasks, notified):
        calls["n"] += 1
        return events_per_call
    w = SessionWatcher(cmux, task_store, links, interpreter, poster)
    return w, calls


class TestPoll:
    def test_notifies_and_marks_done(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.screens["surface:10"] = "作業完了！"
        w, _ = make_watcher(cmux, store, links, poster,
                            [InterpEvent("111.1", "done", "直しました")])
        w.poll_once()
        assert any("✅ 直しました" == p[2] for p in posts)
        assert store.get("111.1").status == STATUS_DONE

    def test_debounce_same_screen(self, setup, poster):
        cmux, store, links = setup
        cmux.screens["surface:10"] = "同じ画面"
        w, calls = make_watcher(cmux, store, links, poster, [])
        w.poll_once()
        w.poll_once()                                   # 画面が変わらない
        assert calls["n"] == 1                          # LLM は1回だけ

    def test_duplicate_summary_not_reposted(self, setup, poster, posts):
        cmux, store, links = setup
        w, _ = make_watcher(cmux, store, links, poster,
                            [InterpEvent("111.1", "progress", "節目です")])
        cmux.screens["surface:10"] = "画面1"
        w.poll_once()
        cmux.screens["surface:10"] = "画面2"            # 画面は変化、要約は同じ
        store.update("111.1", status=STATUS_ACTIVE)
        w.poll_once()
        assert sum(1 for p in posts if "節目です" in p[2]) == 1

    def test_closed_tab_ends_tracking(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.sessions.clear()                           # タブが閉じられた
        w, calls = make_watcher(cmux, store, links, poster, [])
        w.poll_once()
        assert any("閉じられたため追跡を終了" in p[2] for p in posts)
        assert store.get("111.1").status == STATUS_DONE
        assert calls["n"] == 0                          # LLM は呼ばない

    def test_cmux_down_is_noop(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.running = False                            # cmux 停止中
        w, _ = make_watcher(cmux, store, links, poster, [])
        w.poll_once()
        assert posts == []                              # 誤判定しない
        assert store.get("111.1").status == STATUS_ACTIVE

    def test_empty_screen_skipped(self, setup, poster):
        cmux, store, links = setup
        cmux.screens["surface:10"] = "   "
        w, calls = make_watcher(cmux, store, links, poster, [])
        w.poll_once()
        assert calls["n"] == 0


class TestDeadClaude:
    def test_dead_claude_notified_and_tracking_ends(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.dead_claude.add("surface:10")
        w, calls = make_watcher(cmux, store, links, poster, [])
        w.poll_once()
        assert any("claude が終了しています" in p[2] for p in posts)
        assert store.get("111.1").status == STATUS_DONE
        assert calls["n"] == 0                          # LLM は呼ばない


class TestTruncation:
    def test_long_summary_capped(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.screens["surface:10"] = "画面"
        w, _ = make_watcher(cmux, store, links, poster,
                            [InterpEvent("111.1", "progress", "あ" * 5000)])
        w.poll_once()
        body = next(p[2] for p in posts if p[2].startswith("🔨"))
        assert len(body) <= 3510                        # アイコン+3500字


class TestSurfaceRebind:
    def test_watcher_rebinds_instead_of_ending(self, setup, poster, posts):
        cmux, store, links = setup
        links.set_tab_name("111.1", "A機能の実装")   # 目印あり
        cmux.sessions.clear()                        # 旧 ref は失効
        from tests.conftest import make_session
        cmux.sessions.append(make_session(surface="surface:44", name="A機能の実装",
                                          title="A機能の実装", cwd="/tmp"))
        cmux.screens["surface:44"] = "作業中"
        w, calls = make_watcher(cmux, store, links, poster, [])
        w.poll_once()
        assert links.surface_of("111.1") == "surface:44"   # 張り直し
        assert not any("閉じられた" in p[2] for p in posts)  # 誤終了しない
        assert calls["n"] == 1
