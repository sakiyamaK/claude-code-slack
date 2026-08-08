"""ハッピーパスのシナリオテスト（E2E）。

実物の Orchestrator（本物の SQLite 永続化・本物の各サービスの組み立て）に
偽の cmux / LLM を注入し、主要ユースケース2本を最初から最後まで通す:

  シナリオ1: Slack で開始 → 進捗通知 → 完了 → 追加指示 → 帰宅後 cmux で手動継続
  シナリオ2: cmux で手動開始 → Slack から合流 → 進捗が Slack に届く
"""
from __future__ import annotations

import pytest

from relay.orchestrator import Orchestrator
from relay.registry import STATUS_ACTIVE
from tests.conftest import FakeCmux, make_session


class FakeLlm:
    """LlmClient 互換。プロンプトの種類を見て台本どおりに答える。"""

    def __init__(self):
        self.session_pick: str | None = None   # 照合の答え
        self.repo_pick: str = "ios"            # 振り分けの答え

    def ask_json(self, prompt: str, model: str, timeout: int = 60):
        if "セッション照合器" in prompt:
            return {"surface": self.session_pick}
        if "ルーティング器" in prompt:
            return {"repo": self.repo_pick}
        raise AssertionError(f"未知のプロンプト: {prompt[:40]}")


@pytest.fixture
def world(cfg, posts, poster):
    cmux = FakeCmux()
    llm = FakeLlm()
    orch = Orchestrator(cfg, poster, cmux=cmux, llm=llm)
    return orch, cmux, llm


def thread_posts(posts, thread_ts):
    return [text for ch, ts, text in posts if ts == thread_ts]


class TestScenarioSlackToCmux:
    """Slack で開始 → 通知 → 追加指示 → 帰宅後 cmux で手動継続。"""

    def test_full_journey(self, world, cfg, posts):
        orch, cmux, llm = world
        thread = "100.1"

        # ① Slack から新規指示 → ios にタブが立ち、指示が注入される
        orch.router.route("C1", "U1", thread, "ログイン画面のバグを直して")
        assert cmux.created == [("ログイン画面のバグを直して", cfg.repos["ios"])]
        surface = orch.links.surface_of(thread)
        assert surface is not None
        assert (surface, "ログイン画面のバグを直して") in cmux.sent
        assert any("🚀" in p for p in thread_posts(posts, thread))

        # ② セッションが進む → 画面が落ち着いたら本文がそのままスレッドへ
        cmux.screens[surface] = "原因を調査中… NullPointer の可能性"
        orch.watcher.poll_once()   # 変化を検知（出力中は送らない）
        orch.watcher.poll_once()   # 安定 → 原文を転送
        assert any("原因を調査中… NullPointer の可能性" in p
                   for p in thread_posts(posts, thread))

        # ③ さらに進む → 増えた分だけが原文のまま届く
        cmux.screens[surface] = ("原因を調査中… NullPointer の可能性\n"
                                 "修正完了。テストも通りました")
        orch.watcher.poll_once(); orch.watcher.poll_once()
        assert any("修正完了。テストも通りました" in p for p in thread_posts(posts, thread))

        # ④ 同じスレッドに追加指示 → 同じセッションへ届く
        orch.router.route("C1", "U1", thread, "ではコミットしてください")
        assert (surface, "ではコミットしてください") in cmux.sent
        assert orch.tasks_store.get(thread).status == STATUS_ACTIVE
        assert orch.links.surface_of(thread) == surface  # 窓口は不変

        # ⑤ 帰宅後、cmux のタブを直接手で操作して作業が進む
        #    → relay は関与していないのに、画面変化が同じスレッドに通知される
        cmux.screens[surface] = "$ git commit -m 'fix: login NPE' … コミットしました"
        orch.watcher.poll_once(); orch.watcher.poll_once()
        assert any("コミットしました" in p for p in thread_posts(posts, thread))

    def test_repo_routing_to_docs(self, world, cfg, posts):
        orch, cmux, llm = world
        llm.repo_pick = "docs"
        orch.router.route("C1", "U1", "200.1", "エージェント定義の説明を最新化して")
        assert cmux.created[0][1] == cfg.repos["docs"]
        assert orch.links.repo_of("200.1") == "docs"
        assert any("（docs）" in p for p in thread_posts(posts, "200.1"))


class TestScenarioCmuxToSlack:
    """cmux で手動開始 → Slack から合流 → 進捗が Slack に届く。"""

    def test_full_journey(self, world, cfg, posts):
        orch, cmux, llm = world
        thread = "300.1"
        # ① cmux で手動で始めた作業（relay は関与していない）
        manual = make_session(surface="surface:10", name="NAPP-100 ログイン改修",
                              title="ログイン改修中", cwd=cfg.repos["ios"])
        cmux.sessions.append(manual)

        # ② Slack からその作業について指示 → チケットIDの決定的一致で合流（LLM 照合なし）
        orch.router.route("C1", "U1", thread, "NAPP-100 いまどうなってますか？")
        assert orch.links.surface_of(thread) == "surface:10"
        assert cmux.created == []                     # 新規タブは作らない
        assert ("surface:10", "NAPP-100 いまどうなってますか？") in cmux.sent
        assert any("🔗" in p for p in thread_posts(posts, thread))
        assert orch.links.repo_of(thread) == "ios"    # cwd から作業先を記録

        # ③ セッションが答える → 監視が原文をスレッドへ届ける
        cmux.screens["surface:10"] = "現状: 画面実装は完了、テスト作成中です"
        orch.watcher.poll_once(); orch.watcher.poll_once()
        assert any("現状: 画面実装は完了、テスト作成中です" in p
                   for p in thread_posts(posts, thread))

    def test_second_thread_gets_own_tab(self, world, cfg):
        """合流済みセッションは占有される: 別スレッドは自分のタブを持つ。"""
        orch, cmux, llm = world
        manual = make_session(surface="surface:10", name="NAPP-100 ログイン改修",
                              title="ログイン改修中", cwd=cfg.repos["ios"])
        cmux.sessions.append(manual)
        orch.router.route("C1", "U1", "300.1", "NAPP-100 いまどうなってますか？")
        assert orch.links.surface_of("300.1") == "surface:10"

        # 同じ作業に言及する2本目のスレッド → surface:10 は候補から外れ、新規タブ
        llm.session_pick = "surface:10"   # AI が選ぼうとしても
        orch.router.route("C1", "U1", "400.1", "NAPP-100 について教えて")
        s2 = orch.links.surface_of("400.1")
        assert s2 and s2 != "surface:10"
        assert orch.links.surface_of("300.1") == "surface:10"  # 元の窓口は無傷
