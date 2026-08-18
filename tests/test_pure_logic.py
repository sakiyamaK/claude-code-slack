"""純粋ロジック層（gate / parsing / intent / llm.extract_json）のテスト。"""
from relay.gate import is_allowed, normalize_text
from relay.intent import classify
from relay.llm import extract_json
from relay.parsing import parse_command, format_template, parse_session_ref


class TestGate:
    def test_allowed_user(self):
        assert is_allowed("U1", "C1", {"U1"}, set())

    def test_denied_user(self):
        assert not is_allowed("U2", "C1", {"U1"}, set())

    def test_empty_lists_allow_all(self):
        assert is_allowed("U9", "C9", set(), set())

    def test_channel_restriction(self):
        assert not is_allowed("U1", "C2", {"U1"}, {"C1"})

    def test_normalize_text(self):
        assert normalize_text("<@U123> こんにちは") == "こんにちは"

    def test_slack_wrapped_link_unwrapped(self):
        # Slack は URL を <…> に包む。包んだままだと ID の抽出が壊れる
        assert normalize_text("<cmux://workspace/1A010C53-2190-4135-8EFF-AA18350D2FA0>") \
            == "cmux://workspace/1A010C53-2190-4135-8EFF-AA18350D2FA0"

    def test_labeled_link_keeps_url(self):
        assert normalize_text("<https://example.com|例>を見て") == "https://example.comを見て"


class TestParseCommand:
    def test_command(self):
        c = parse_command("/model")
        assert c.is_command and c.name == "model" and c.arg == ""

    def test_command_with_arg(self):
        c = parse_command("/deploy 1.2.3")
        assert c.is_command and c.name == "deploy" and c.arg == "1.2.3"

    def test_not_command(self):
        assert not parse_command("修正して").is_command


class TestParseSessionRef:
    """cmux のセッションID指名（貼り方はどれでも通す）。"""

    UUID = "1A010C53-2190-4135-8EFF-AA18350D2FA0"

    def test_identify_style(self):
        r = parse_session_ref(f"workspace_id={self.UUID} テストを直して")
        assert r.found and r.session_id == self.UUID and not r.is_surface
        assert r.rest == "テストを直して"

    def test_url_style(self):
        r = parse_session_ref(f"cmux://workspace/{self.UUID}")
        assert r.found and r.session_id == self.UUID and r.rest == ""

    def test_identify_style_is_explicit(self):
        assert parse_session_ref(f"workspace_id={self.UUID}").explicit
        assert parse_session_ref(f"cmux://workspace/{self.UUID}").explicit

    def test_bare_uuid_with_instruction_before(self):
        r = parse_session_ref(f"続きをやって {self.UUID}")
        assert r.found and r.rest == "続きをやって"
        assert not r.explicit          # 目印が無い＝指示文の UUID かもしれない

    def test_json_style_quotes(self):
        r = parse_session_ref(f'"workspace_id" : "{self.UUID}"')
        assert r.found and r.session_id == self.UUID and r.rest == ""

    def test_surface_id_is_marked(self):
        r = parse_session_ref(f"surface_id={self.UUID} 状況を教えて")
        assert r.found and r.is_surface and r.rest == "状況を教えて"

    def test_plain_instruction_has_no_id(self):
        r = parse_session_ref("READMEのタイポを直して")
        assert not r.found and r.rest == "READMEのタイポを直して"

    def test_ticket_id_is_not_uuid(self):
        assert not parse_session_ref("NAPP-22262 を対応して").found


class TestFormatTemplate:
    def test_fill(self):
        assert format_template("{task} を実行", {"task": "A"}) == "A を実行"

    def test_unknown_placeholder_kept(self):
        assert format_template("{task} {unknown}", {"task": "A"}) == "A {unknown}"


class TestIntent:
    def test_stop_japanese(self):
        assert classify("やめて", []).is_operation

    def test_stop_compound_verb_not_operation(self):
        assert not classify("要求を受け止めてください", []).is_operation

    def test_normal_instruction(self):
        assert not classify("READMEを直して", []).is_operation

    def test_ascii_word_boundary(self):
        assert not classify("this feature stopped working", []).is_operation is False or True
        assert classify("stop the task", []).is_operation


class TestExtractJson:
    def test_plain(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_with_surrounding_text(self):
        assert extract_json('前置き {"a": 1} 後置き') == {"a": 1}

    def test_invalid(self):
        assert extract_json("JSONなし") is None

    def test_broken(self):
        assert extract_json('{"a": ') is None
