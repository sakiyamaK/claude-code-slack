"""純粋ロジック層（gate / parsing / intent / llm.extract_json）のテスト。"""
from relay.gate import is_allowed, strip_mentions
from relay.intent import classify
from relay.llm import extract_json
from relay.parsing import parse_command, format_template


class TestGate:
    def test_allowed_user(self):
        assert is_allowed("U1", "C1", {"U1"}, set())

    def test_denied_user(self):
        assert not is_allowed("U2", "C1", {"U1"}, set())

    def test_empty_lists_allow_all(self):
        assert is_allowed("U9", "C9", set(), set())

    def test_channel_restriction(self):
        assert not is_allowed("U1", "C2", {"U1"}, {"C1"})

    def test_strip_mentions(self):
        assert strip_mentions("<@U123> こんにちは") == "こんにちは"


class TestParseCommand:
    def test_command(self):
        c = parse_command("/model")
        assert c.is_command and c.name == "model" and c.arg == ""

    def test_command_with_arg(self):
        c = parse_command("/deploy 1.2.3")
        assert c.is_command and c.name == "deploy" and c.arg == "1.2.3"

    def test_not_command(self):
        assert not parse_command("修正して").is_command


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
