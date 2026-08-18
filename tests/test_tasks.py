"""TaskService のテスト（1スレッド=1surface の仕様を回帰テスト化）。"""
from dataclasses import replace

import pytest

from relay.parsing import parse_session_ref
from relay.registry import STATUS_ACTIVE, STATUS_DONE
from relay.tasks import TaskService
from relay.match import find_ticket
from tests.conftest import FakeCmux, make_session


@pytest.fixture
def service_factory(cfg, task_store, links, runtime, poster):
    def _make(fake_cmux, pick_session=lambda body, cands: None,
              pick_repo=lambda body: "ios"):
        return TaskService(cfg, fake_cmux, task_store, links, runtime, poster,
                           pick_session=pick_session, pick_repo=pick_repo,
                           find_ticket=find_ticket)
    return _make


class TestNewTask:
    def test_creates_new_tab_when_no_match(self, service_factory, cfg, links, posts):
        cmux = FakeCmux()
        svc = service_factory(cmux, pick_repo=lambda body: "docs")
        svc.new_task("C1", "U1", "111.1", "エージェント定義を直して")
        # docs リポジトリでタブが作られ、指示が注入される
        assert cmux.created == [("エージェント定義を直して", cfg.repos["docs"])]
        assert links.surface_of("111.1") == cmux.sent[0][0]
        assert cmux.sent[0][1] == "エージェント定義を直して"
        assert links.repo_of("111.1") == "docs"
        assert any("🚀" in p[2] and "（docs）" in p[2] for p in posts)

    def test_joins_unbound_running_session(self, service_factory, cfg, links, posts):
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        svc = service_factory(
            cmux,
            pick_session=lambda body, cands:
                "surface:10" if any(s.surface == "surface:10" for s in cands) else None)
        svc.new_task("C1", "U1", "111.1", "A機能の続きをやって")
        assert links.surface_of("111.1") == "surface:10"
        assert cmux.created == []                       # 新規タブは作らない
        assert ("surface:10", "A機能の続きをやって") in cmux.sent
        assert links.repo_of("111.1") == "ios"           # cwd から逆引きで記録
        assert any("🔗" in p[2] for p in posts)

    def test_bound_surface_never_stolen(self, service_factory, cfg, links):
        """1スレッド=1surface: 紐付き済み surface は照合候補に出ない。"""
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        links.link_surface("999.9", "surface:10")        # 別スレッドが窓口
        seen = {}
        def pick(body, cands):
            seen["candidates"] = [s.surface for s in cands]
            return "surface:10" if any(s.surface == "surface:10" for s in cands) else None
        svc = service_factory(cmux, pick_session=pick)
        svc.new_task("C1", "U1", "111.1", "Aについて教えて")
        assert seen["candidates"] == []                  # 除外されている
        assert links.surface_of("999.9") == "surface:10"  # 元の紐付けは無傷
        new_surface = links.surface_of("111.1")
        assert new_surface and new_surface != "surface:10"
        assert not any(s == "surface:10" for s, _ in cmux.sent)

    def test_done_thread_surface_still_reserved(self, service_factory, cfg,
                                                task_store, links):
        """done になったスレッドの surface も他スレッドに取られない。"""
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        svc = service_factory(cmux)
        links.link_surface("999.9", "surface:10")
        svc.ensure_row("C1", "U1", "999.9")
        task_store.update("999.9", status=STATUS_DONE)
        seen = {}
        svc2 = service_factory(cmux, pick_session=lambda b, c: seen.setdefault(
            "candidates", [s.surface for s in c]) and None)
        svc2.new_task("C1", "U1", "111.1", "何かやって")
        assert "surface:10" not in (seen.get("candidates") or [])

    def test_own_tab_excluded_from_candidates(self, service_factory, cfg):
        """relay 自身のタブは候補外（受信ログに指示文が写るため誤照合しやすい）。"""
        cmux = FakeCmux([make_session(surface="surface:10", cwd=cfg.repos["ios"])])
        cmux.own = "surface:10"
        seen = {}
        def pick(body, cands):
            seen["candidates"] = [s.surface for s in cands]
            return None
        service_factory(cmux, pick_session=pick).new_task("C1", "U1", "111.1", "何かやって")
        assert seen["candidates"] == []

    def test_non_agent_surface_excluded_from_candidates(self, service_factory, cfg):
        """素のシェルやエディタのサーフェスに指示を打ち込まない。"""
        cmux = FakeCmux([
            make_session(surface="surface:10", cwd=cfg.repos["ios"]),
            make_session(surface="surface:19", name="メモ", cwd=None),
        ])
        cmux.sessions[1] = replace(cmux.sessions[1], agent=False)
        seen = {}
        def pick(body, cands):
            seen["candidates"] = [s.surface for s in cands]
            return None
        service_factory(cmux, pick_session=pick).new_task("C1", "U1", "111.1", "何かやって")
        assert seen["candidates"] == ["surface:10"]

    def test_task_name_prefers_ticket(self, service_factory):
        cmux = FakeCmux()
        svc = service_factory(cmux)
        svc.new_task("C1", "U1", "111.1", "NAPP-22262 のバグを直して")
        assert cmux.created[0][0] == "NAPP-22262"


class TestAttachSession:
    """Slack にワークスペースIDを貼っての合流（新規タブを作らない）。"""

    WS = "1A010C53-2190-4135-8EFF-AA18350D2FA0"

    def _cmux(self, cfg):
        return FakeCmux([make_session(cwd=cfg.repos["ios"], workspace_id=self.WS)])

    def test_attaches_named_tab(self, service_factory, cfg, links, task_store, posts):
        cmux = self._cmux(cfg)
        svc = service_factory(cmux)
        assert svc.attach_session("C1", "U1", "111.1", parse_session_ref(
            f"workspace_id={self.WS} テストも直して"))
        assert cmux.created == []                          # 新しいタブは作らない
        assert links.surface_of("111.1") == "surface:10"
        assert links.workspace_of("111.1") == self.WS       # UUID で再解決できるよう記録
        assert links.repo_of("111.1") == "ios"             # cwd から逆引き
        assert links.instruction_of("111.1") == "テストも直して"
        assert task_store.get("111.1").status == STATUS_ACTIVE
        assert any("🔗" in p[2] for p in posts)
        assert cmux.sent == []                             # 指示の投入は router 経由

    def test_id_only_records_instruction_for_tracking(self, service_factory, cfg, links):
        cmux = self._cmux(cfg)
        svc = service_factory(cmux)
        svc.attach_session("C1", "U1", "111.1",
                           parse_session_ref(f"cmux://workspace/{self.WS}"))
        assert links.surface_of("111.1") == "surface:10"
        assert links.instruction_of("111.1")               # 監視対象になるよう記録は残す

    def test_unknown_id_guides_without_creating_tab(self, service_factory, links, posts):
        cmux = FakeCmux()
        svc = service_factory(cmux)
        assert not svc.attach_session("C1", "U1", "111.1",
                                      parse_session_ref(f"workspace_id={self.WS}"))
        assert cmux.created == [] and links.surface_of("111.1") is None
        assert any("見つかりません" in p[2] for p in posts)

    def test_takes_over_from_other_thread(self, service_factory, cfg, links, posts):
        cmux = self._cmux(cfg)
        svc = service_factory(cmux)
        links.link_surface("999.9", "surface:10")           # 別スレッドが窓口だった
        svc.attach_session("C1", "U1", "111.1",
                           parse_session_ref(f"workspace_id={self.WS}"))
        assert links.surface_of("111.1") == "surface:10"
        assert links.surface_of("999.9") is None           # 二重通知にならないよう解除
        assert any("別スレッドの紐付けは解除" in p[2] for p in posts)

    def test_rebinds_by_uuid_after_ref_change(self, service_factory, cfg, links):
        """cmux 再起動で ref が変わっても UUID から引き直す。"""
        cmux = self._cmux(cfg)
        svc = service_factory(cmux)
        svc.attach_session("C1", "U1", "111.1",
                           parse_session_ref(f"workspace_id={self.WS}"))
        cmux.sessions[:] = [make_session(surface="surface:77", workspace="workspace:77",
                                        cwd=cfg.repos["ios"], workspace_id=self.WS)]
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert links.surface_of("111.1") == "surface:77"
        assert ("surface:77", "続きをやって") in cmux.sent
        assert cmux.created == []


class TestFollowUp:
    def test_injects_into_linked_surface(self, service_factory, cfg, links, task_store):
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        svc = service_factory(cmux)
        links.link_surface("111.1", "surface:10")
        svc.ensure_row("C1", "U1", "111.1")
        task_store.update("111.1", status=STATUS_DONE)   # done 後の追加指示
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert ("surface:10", "続きをやって") in cmux.sent
        assert task_store.get("111.1").status == STATUS_ACTIVE  # 追跡に戻る

    def test_recreates_in_recorded_repo_when_tab_closed(
            self, service_factory, cfg, links, posts):
        cmux = FakeCmux()                                # タブは存在しない
        svc = service_factory(cmux)
        links.link_surface("111.1", "surface:10")        # 消えた surface
        links.set_repo("111.1", "docs")
        links.set_instruction("111.1", "元の依頼")
        svc.ensure_row("C1", "U1", "111.1")
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert cmux.created[0][1] == cfg.repos["docs"]   # 記録済みリポジトリで復元
        assert "元の依頼" in cmux.sent[0][1] and "続きをやって" in cmux.sent[0][1]
        assert any("⚠️" in p[2] for p in posts)


class TestOperation:
    def test_interrupts_linked_session(self, service_factory, cfg, links, posts):
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        svc = service_factory(cmux)
        links.link_surface("111.1", "surface:10")
        svc.operation("C1", "111.1", "やめて")
        assert cmux.interrupted == ["surface:10"]
        assert any("中断してください" in t for _, t in cmux.sent)
        assert any("🛑" in p[2] for p in posts)

    def test_guides_when_no_link(self, service_factory, posts):
        svc = service_factory(FakeCmux())
        svc.operation("C1", "111.1", "やめて")
        assert any("紐付いたセッションがありません" in p[2] for p in posts)


class TestDeadClaude:
    """claude プロセスだけが死んだタブへの対応（穴2）。"""

    def test_follow_up_revives_in_same_tab(self, service_factory, cfg, links, posts):
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        cmux.dead_claude.add("surface:10")
        svc = service_factory(cmux)
        links.link_surface("111.1", "surface:10")
        svc.ensure_row("C1", "U1", "111.1")
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert cmux.revived == ["surface:10"]            # --continue で復活を試みた
        assert ("surface:10", "続きをやって") in cmux.sent  # 復活後に指示が届く
        assert cmux.created == []                        # 作り直していない
        assert any("♻️" in p[2] for p in posts)

    def test_follow_up_recreates_when_revive_fails(self, service_factory, cfg, links):
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        cmux.dead_claude.add("surface:10")
        cmux.revive_ok = False
        svc = service_factory(cmux)
        links.link_surface("111.1", "surface:10")
        links.set_repo("111.1", "ios")
        links.set_instruction("111.1", "元の依頼")
        svc.ensure_row("C1", "U1", "111.1")
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert cmux.revived == ["surface:10"]
        assert cmux.created and cmux.created[0][1] == cfg.repos["ios"]  # 作り直し
        assert not any(s == "surface:10" for s, _ in cmux.sent)  # 死んだシェルには打たない

    def test_operation_on_dead_claude_reports_stopped(self, service_factory, cfg,
                                                      links, posts):
        session = make_session(cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        cmux.dead_claude.add("surface:10")
        svc = service_factory(cmux)
        links.link_surface("111.1", "surface:10")
        svc.operation("C1", "111.1", "やめて")
        assert cmux.interrupted == []
        assert any("既に終了しています" in p[2] for p in posts)


class TestSurfaceRebind:
    """cmux 再起動で surface ref が変わっても、タブ名で再発見して張り直す（穴5）。"""

    def test_follow_up_rebinds_by_tab_name(self, service_factory, cfg, links):
        cmux = FakeCmux()
        svc = service_factory(cmux)
        # タブを作って紐付け（surface:101, タブ名記録）
        svc.new_task("C1", "U1", "111.1", "ログインを直して")
        old_surface = links.surface_of("111.1")
        tab = links.tab_name_of("111.1")
        assert tab == "ログインを直して"
        # cmux 再起動: 同じ名前のタブが別 ref で復元された想定
        cmux.sessions.clear()
        cmux.sessions.append(make_session(
            surface="surface:33", workspace="workspace:33",
            name=tab, title=tab, cwd=cfg.repos["ios"]))
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert links.surface_of("111.1") == "surface:33"   # 張り直された
        assert ("surface:33", "続きをやって") in cmux.sent
        assert cmux.created == [("ログインを直して", cfg.repos["ios"])]  # 作り直していない
        assert old_surface != "surface:33"

    def test_rebind_skips_surfaces_taken_by_others(self, service_factory, cfg, links):
        cmux = FakeCmux()
        svc = service_factory(cmux)
        svc.new_task("C1", "U1", "111.1", "ログインを直して")
        tab = links.tab_name_of("111.1")
        cmux.sessions.clear()
        # 同名タブがあるが、別スレッドが既にその surface の窓口
        cmux.sessions.append(make_session(surface="surface:33", name=tab,
                                          title=tab, cwd=cfg.repos["ios"]))
        links.link_surface("999.9", "surface:33")
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert links.surface_of("111.1") != "surface:33"   # 奪わない → 作り直し
        assert len(cmux.created) == 2

    def test_join_does_not_record_tab_name(self, service_factory, cfg, links):
        """手動タブは cmux が自動改名するため、合流時は名前を記録しない。"""
        session = make_session(name="NAPP-100 ログイン改修", cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        svc = service_factory(
            cmux, pick_session=lambda b, c: "surface:10" if c else None)
        svc.new_task("C1", "U1", "111.1", "NAPP-100 の続き")
        assert links.tab_name_of("111.1") is None
        assert links.surface_of("111.1") == "surface:10"


class TestSurfaceIdentity:
    """surface ref が別のタブに再割り当てされたときの本人確認（誤送信防止）。"""

    def test_reused_ref_detected_and_rebound(self, service_factory, cfg, links):
        cmux = FakeCmux()
        svc = service_factory(cmux)
        svc.new_task("C1", "U1", "111.1", "ログインを直して")
        ref = links.surface_of("111.1")
        # cmux 再起動: 同じ ref が「別のタブ」に割り当てられ、
        # 本来のタブは別 ref（状態アイコン付きの名前）で存在する
        cmux.sessions.clear()
        cmux.sessions.append(make_session(surface=ref, name="全く別の作業",
                                          title="別作業", cwd=cfg.repos["docs"]))
        cmux.sessions.append(make_session(surface="surface:77",
                                          name="⠐ ログインを直して",
                                          title="ログインを直して", cwd=cfg.repos["ios"]))
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert links.surface_of("111.1") == "surface:77"     # 本人確認→張り直し
        assert ("surface:77", "続きをやって") in cmux.sent
        assert (ref, "続きをやって") not in cmux.sent         # 別作業のタブには送らない

    def test_reused_ref_without_original_recreates(self, service_factory, cfg, links):
        cmux = FakeCmux()
        svc = service_factory(cmux)
        svc.new_task("C1", "U1", "111.1", "ログインを直して")
        ref = links.surface_of("111.1")
        cmux.sessions.clear()
        cmux.sessions.append(make_session(surface=ref, name="全く別の作業",
                                          title="別作業", cwd=cfg.repos["docs"]))
        n_created = len(cmux.created)
        svc.follow_up("C1", "U1", "111.1", "続きをやって")
        assert len(cmux.created) == n_created + 1            # 作り直し
        assert not any(s == ref and "続き" in t for s, t in cmux.sent)

    def test_joined_manual_tab_trusts_ref(self, service_factory, cfg, links):
        """合流タブ（タブ名記録なし）は従来どおり ref の存在確認のみ。"""
        session = make_session(name="✳ 手動の作業", cwd=cfg.repos["ios"])
        cmux = FakeCmux([session])
        svc = service_factory(
            cmux, pick_session=lambda b, c: "surface:10" if c else None)
        svc.new_task("C1", "U1", "111.1", "手動の作業の続き")
        svc.follow_up("C1", "U1", "111.1", "さらに続き")
        assert ("surface:10", "さらに続き") in cmux.sent
