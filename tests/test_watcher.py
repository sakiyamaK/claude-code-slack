"""SessionWatcher のテスト（安定化デバウンス・差分転送・タブ消失・truncation）。"""
import pytest

from relay.registry import STATUS_ACTIVE, STATUS_DONE
from relay.tasks import TaskService
from relay.match import find_ticket
from relay.watcher import SessionWatcher, new_lines
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


def make_watcher(cmux, task_store, links, poster):
    return SessionWatcher(cmux, task_store, links, poster)


class TestNewLines:
    def test_first_time_returns_all(self):
        assert new_lines([], ["a", "b"]) == ["a", "b"]

    def test_scrolled_tail_is_diff(self):
        assert new_lines(["a", "b", "c"], ["b", "c", "d", "e"]) == ["d", "e"]

    def test_fixed_input_box_not_included(self):
        # 画面下部の入力欄（共通行）は差分に出ない
        prev = ["out1", "───", "> _"]
        cur = ["out1", "out2", "───", "> _"]
        assert new_lines(prev, cur) == ["out2"]


class TestPoll:
    def test_unstable_screen_not_posted(self, setup, poster, posts):
        cmux, store, links = setup
        w = make_watcher(cmux, store, links, poster)
        cmux.screens["surface:10"] = "出力中1"
        w.poll_once()                                   # 初見 → ハッシュ記録のみ
        cmux.screens["surface:10"] = "出力中2"
        w.poll_once()                                   # まだ変化中 → 送らない
        assert posts == []

    def test_stable_screen_posted_verbatim(self, setup, poster, posts):
        cmux, store, links = setup
        w = make_watcher(cmux, store, links, poster)
        cmux.screens["surface:10"] = "回答本文です"
        w.poll_once()                                   # 変化を検知
        w.poll_once()                                   # 安定 → 全文送信
        assert any(p[2] == "```回答本文です```" for p in posts)

    def test_only_diff_posted_after_first_send(self, setup, poster, posts):
        cmux, store, links = setup
        w = make_watcher(cmux, store, links, poster)
        cmux.screens["surface:10"] = "行1\n行2"
        w.poll_once(); w.poll_once()                    # 全文送信
        cmux.screens["surface:10"] = "行1\n行2\n行3"
        w.poll_once(); w.poll_once()                    # 差分のみ
        assert posts[-1][2] == "```行3```"

    def test_no_repost_when_screen_settled(self, setup, poster, posts):
        cmux, store, links = setup
        w = make_watcher(cmux, store, links, poster)
        cmux.screens["surface:10"] = "同じ画面"
        w.poll_once(); w.poll_once()                    # 送信
        w.poll_once(); w.poll_once()                    # 変化なし → 差分ゼロ
        assert sum(1 for p in posts if "同じ画面" in p[2]) == 1

    def test_closed_tab_ends_tracking(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.sessions.clear()                           # タブが閉じられた
        w = make_watcher(cmux, store, links, poster)
        w.poll_once()
        assert any("閉じられたため追跡を終了" in p[2] for p in posts)
        assert store.get("111.1").status == STATUS_DONE

    def test_cmux_down_is_noop(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.running = False                            # cmux 停止中
        w = make_watcher(cmux, store, links, poster)
        w.poll_once()
        assert posts == []                              # 誤判定しない
        assert store.get("111.1").status == STATUS_ACTIVE

    def test_empty_screen_skipped(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.screens["surface:10"] = "   "
        w = make_watcher(cmux, store, links, poster)
        w.poll_once(); w.poll_once()
        assert posts == []


class TestDeadClaude:
    def test_dead_claude_notified_and_tracking_ends(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.dead_claude.add("surface:10")
        w = make_watcher(cmux, store, links, poster)
        w.poll_once()
        assert any("claude が終了しています" in p[2] for p in posts)
        assert store.get("111.1").status == STATUS_DONE

    def test_alert_not_repeated(self, setup, poster, posts):
        cmux, store, links = setup
        cmux.dead_claude.add("surface:10")
        w = make_watcher(cmux, store, links, poster)
        w.poll_once()
        store.update("111.1", status=STATUS_ACTIVE)     # 追跡が再開しても
        w.poll_once()
        assert sum(1 for p in posts if "claude が終了" in p[2]) == 1


class TestTruncation:
    def test_long_body_keeps_tail(self, setup, poster, posts):
        cmux, store, links = setup
        w = make_watcher(cmux, store, links, poster)
        cmux.screens["surface:10"] = "先頭マーカー\n" + "あ" * 5000 + "\n末尾マーカー"
        w.poll_once(); w.poll_once()
        body = posts[-1][2]
        assert len(body) <= 3600
        assert "末尾マーカー" in body                    # 最新の出力側を残す
        assert "先頭マーカー" not in body


class TestSurfaceRebind:
    def test_watcher_rebinds_instead_of_ending(self, setup, poster, posts):
        cmux, store, links = setup
        links.set_tab_name("111.1", "A機能の実装")   # 目印あり
        cmux.sessions.clear()                        # 旧 ref は失効
        cmux.sessions.append(make_session(surface="surface:44", name="A機能の実装",
                                          title="A機能の実装", cwd="/tmp"))
        cmux.screens["surface:44"] = "作業中"
        w = make_watcher(cmux, store, links, poster)
        w.poll_once()
        assert links.surface_of("111.1") == "surface:44"   # 張り直し
        assert not any("閉じられた" in p[2] for p in posts)  # 誤終了しない
