"""進捗解釈（interpret.py）のテスト。LLM は ask を偽装。"""
from relay.interpret import interpret


def test_no_tasks_returns_empty_without_llm():
    def ask_never(*a, **k):
        raise AssertionError("呼ばれてはいけない")
    assert interpret(ask_never, "haiku", "画面", [], {}) == []


def test_events_filtered_to_known_ids_and_kinds():
    ask = lambda p, m, timeout=120: {"events": [
        {"id": "111.1", "kind": "done", "summary": "完了しました"},
        {"id": "unknown", "kind": "done", "summary": "無関係"},
        {"id": "111.1", "kind": "weird", "summary": "不正kind"},
    ]}
    events = interpret(ask, "haiku", "画面", [{"id": "111.1", "task": "直す"}], {})
    assert len(events) == 1
    assert events[0].thread_ts == "111.1" and events[0].kind == "done"
    assert events[0].summary == "完了しました"


def test_llm_failure_returns_empty():
    events = interpret(lambda *a, **k: None, "haiku", "画面",
                       [{"id": "1", "task": "t"}], {})
    assert events == []


def test_notified_passed_into_prompt():
    captured = {}
    def ask(prompt, model, timeout=120):
        captured["prompt"] = prompt
        return {"events": []}
    interpret(ask, "haiku", "画面", [{"id": "1", "task": "t"}],
              {"1": ["既に通知済みの要約"]})
    assert "既に通知済みの要約" in captured["prompt"]
