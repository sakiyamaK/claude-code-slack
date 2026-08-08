"""照合・リポジトリ振り分け（match.py）のテスト。LLM は ask を偽装。"""
from relay.match import pick_session, pick_repo, find_ticket
from tests.conftest import make_session


def ask_never(prompt, model, timeout=60):
    raise AssertionError("決定的経路で LLM が呼ばれてはいけない")


class TestPickSession:
    def test_empty_candidates_returns_none_without_llm(self):
        assert pick_session(ask_never, "sonnet", "何か", []) is None

    def test_ticket_id_decisive(self):
        s = make_session(name="NAPP-1234")
        assert pick_session(ask_never, "sonnet", "NAPP-1234 の続き", [s]) == "surface:10"

    def test_ticket_ambiguous_falls_to_ai(self):
        s1 = make_session(surface="surface:1", name="NAPP-1")
        s2 = make_session(surface="surface:2", name="NAPP-1 sub")
        # 2件ヒット→決定的一致にならず AI へ。AI が null なら None
        assert pick_session(lambda *a, **k: {"surface": None}, "sonnet",
                            "NAPP-1 やって", [s1, s2]) is None

    def test_ai_pick_valid(self):
        s = make_session()
        ask = lambda p, m, timeout=60: {"surface": "surface:10"}
        assert pick_session(ask, "sonnet", "A機能の続き", [s]) == "surface:10"

    def test_ai_pick_invalid_ref_rejected(self):
        s = make_session()
        ask = lambda p, m, timeout=60: {"surface": "surface:999"}
        assert pick_session(ask, "sonnet", "A機能の続き", [s]) is None

    def test_ai_failure_returns_none(self):
        s = make_session()
        assert pick_session(lambda *a, **k: None, "sonnet", "何か", [s]) is None


class TestPickRepo:
    REPOS = {"ios": "/r/ios", "docs": "/r/docs"}
    DESCS = {"ios": "アプリ", "docs": "文書"}

    def test_single_repo_short_circuits(self):
        assert pick_repo(ask_never, "sonnet", "何か", {"main": "/r"}, {}, "main") == "main"

    def test_name_mention_decisive(self):
        assert pick_repo(ask_never, "sonnet", "docsのエージェント定義を直して",
                         self.REPOS, self.DESCS, "ios") == "docs"

    def test_name_must_be_word_boundary(self):
        # "iosdocs" のような連結は名前の明示とみなさない → AI 判定へ
        ask = lambda p, m, timeout=60: {"repo": "ios"}
        assert pick_repo(ask, "sonnet", "iosdocsについて",
                         self.REPOS, self.DESCS, "ios") == "ios"

    def test_ai_pick(self):
        ask = lambda p, m, timeout=60: {"repo": "docs"}
        assert pick_repo(ask, "sonnet", "エージェント定義を直して",
                         self.REPOS, self.DESCS, "ios") == "docs"

    def test_ai_invalid_falls_back_to_default(self):
        ask = lambda p, m, timeout=60: {"repo": "unknown"}
        assert pick_repo(ask, "sonnet", "何か", self.REPOS, self.DESCS, "ios") == "ios"

    def test_ai_failure_falls_back_to_default(self):
        assert pick_repo(lambda *a, **k: None, "sonnet", "何か",
                         self.REPOS, self.DESCS, "ios") == "ios"


class TestFindTicket:
    def test_found(self):
        assert find_ticket("napp-22262 を直して") == "NAPP-22262"

    def test_not_found(self):
        assert find_ticket("何か直して") is None
