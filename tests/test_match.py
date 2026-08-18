"""照合・リポジトリ振り分け（match.py）のテスト。LLM は ask を偽装。"""
from relay.match import (
    pick_session, pick_repo, find_ticket, describe_session, screen_blocks, screen_digest,
)
from tests.conftest import make_session


def ask_never(prompt, model, timeout=60):
    raise AssertionError("決定的経路で LLM が呼ばれてはいけない")


# 実際の cmux 画面の縮小版（枠線・ステータス行・ツール出力・折り返しを含む）
SCREEN = """\
⏺ 就活モーダルの SwiftUI 化を進めます。まずは既存の実装を読みます。
  ⎿  $ cat Sources/JobHuntingStatusModal.swift | head -100
⏺ テストは24件すべて通りました。TestRail の項目も
  25件すべて緑になっています。
✻ Worked for 3m 7s
※ recap: NAPP-22290 の就活モーダル SwiftUI 化はコミット済みです。
  次は PR を作るかどうかのご判断をお願いします。 (disable recaps in /config)
──────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────
  ● 📁@develop(develop) | 🤖 Opus 5 | 🧠 36%
  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents
"""


class TestScreenDigest:
    def test_blocks_keep_speech_and_join_wraps(self):
        blocks = screen_blocks(SCREEN)
        assert blocks[0].startswith("就活モーダルの SwiftUI 化を進めます")
        # 折り返された発話は1ブロックに繋がる
        assert "テストは24件すべて通りました。TestRail の項目も 25件すべて緑になっています。" in blocks

    def test_blocks_drop_noise(self):
        joined = " ".join(screen_blocks(SCREEN))
        assert "cat Sources" not in joined          # ツール出力
        assert "Worked for" not in joined           # スピナー
        assert "auto mode on" not in joined         # ステータス行
        assert "──" not in joined                   # 枠線
        assert "disable recaps" not in joined       # 常駐の案内文

    def test_digest_puts_recap_first(self):
        digest = screen_digest(SCREEN)
        assert digest.startswith("recap: NAPP-22290")
        assert "次は PR を作るかどうかのご判断をお願いします。" in digest

    def test_digest_respects_budget(self):
        assert len(screen_digest(SCREEN, 80)) <= 80

    def test_empty_screen_is_empty_digest(self):
        assert screen_digest("") == ""
        assert "画面:" not in describe_session(make_session())


class TestPasteMatch:
    def test_pasted_screen_text_is_decisive_without_llm(self):
        target = make_session(surface="surface:20", name="NAPP-22290", screen=SCREEN)
        other = make_session(surface="surface:11", name="スライド作り",
                             screen="⏺ スライドの原稿を書いています。")
        # 画面からコピーした一節（端末の折り返しで空白が入っていても一致する）
        pasted = "テストは24件すべて通りました。TestRail の項目も25件すべて緑になっています。"
        assert pick_session(ask_never, "sonnet", pasted, [target, other]) == "surface:20"

    def test_ambiguous_paste_falls_to_ai(self):
        s1 = make_session(surface="surface:1", screen="⏺ 同じ話題の長い一節がここにある")
        s2 = make_session(surface="surface:2", screen="⏺ 同じ話題の長い一節がここにある")
        assert pick_session(lambda *a, **k: {"surface": None}, "sonnet",
                            "同じ話題の長い一節がここにある", [s1, s2]) is None

    def test_short_fragment_never_matches(self):
        s = make_session(screen="⏺ はい、やります。")
        assert pick_session(lambda *a, **k: {"surface": None}, "sonnet", "はい", [s]) is None


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
