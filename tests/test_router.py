"""MessageRouter のテスト。サービスは MagicMock で振り分け先だけ検証。"""
from unittest.mock import MagicMock

import pytest

from relay.router import MessageRouter


@pytest.fixture
def parts(links, poster):
    commands = MagicMock()
    commands.has_pending_pick.return_value = False
    tasks = MagicMock()
    tasks.has_active.return_value = False
    return commands, tasks, links


def make_router(commands, tasks, links, poster, is_operation=False):
    classify = MagicMock(return_value=MagicMock(is_operation=is_operation))
    return MessageRouter(commands, tasks, links, poster, classify=classify)


class TestRoute:
    def test_empty_text_prompts(self, parts, poster, posts):
        commands, tasks, links = parts
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", "   ")
        assert "内容を教えてください" in posts[-1][2]
        tasks.new_task.assert_not_called()

    def test_pending_pick_digit(self, parts, poster):
        commands, tasks, links = parts
        commands.has_pending_pick.return_value = True
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", " 2 ")
        commands.apply_pick.assert_called_once_with("C1", "U1", "1.1", 2)

    def test_command(self, parts, poster):
        commands, tasks, links = parts
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", "/model")
        commands.handle.assert_called_once_with("C1", "U1", "1.1", "model", "")

    def test_new_thread_becomes_task(self, parts, poster):
        commands, tasks, links = parts
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", "直して")
        tasks.new_task.assert_called_once_with("C1", "U1", "1.1", "直して")

    def test_linked_thread_follow_up(self, parts, poster):
        commands, tasks, links = parts
        links.link_surface("1.1", "surface:10")
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", "続けて")
        tasks.follow_up.assert_called_once_with("C1", "U1", "1.1", "続けて")
        tasks.new_task.assert_not_called()

    def test_linked_thread_operation(self, parts, poster):
        commands, tasks, links = parts
        links.link_surface("1.1", "surface:10")
        make_router(commands, tasks, links, poster, is_operation=True) \
            .route("C1", "U1", "1.1", "やめて")
        tasks.operation.assert_called_once_with("C1", "1.1", "やめて")

    def test_new_thread_operation_with_active_guides(self, parts, poster):
        commands, tasks, links = parts
        tasks.has_active.return_value = True
        make_router(commands, tasks, links, poster, is_operation=True) \
            .route("C1", "U1", "9.9", "やめて")
        tasks.operation.assert_called_once()
        tasks.new_task.assert_not_called()

    def test_new_thread_operation_without_active_is_task(self, parts, poster):
        commands, tasks, links = parts
        tasks.has_active.return_value = False
        make_router(commands, tasks, links, poster, is_operation=True) \
            .route("C1", "U1", "9.9", "止め方を調べて")
        tasks.new_task.assert_called_once()


class TestSessionIdRouting:
    UUID = "1A010C53-2190-4135-8EFF-AA18350D2FA0"

    def test_id_attaches_then_instructs(self, parts, poster):
        commands, tasks, links = parts
        make_router(commands, tasks, links, poster) \
            .route("C1", "U1", "1.1", f"workspace_id={self.UUID} 続きをやって")
        ref = tasks.attach_session.call_args[0][3]
        assert ref.session_id == self.UUID and ref.rest == "続きをやって"
        tasks.follow_up.assert_called_once_with("C1", "U1", "1.1", "続きをやって")
        tasks.new_task.assert_not_called()

    def test_id_only_just_attaches(self, parts, poster):
        commands, tasks, links = parts
        make_router(commands, tasks, links, poster) \
            .route("C1", "U1", "1.1", f"cmux://workspace/{self.UUID}")
        tasks.attach_session.assert_called_once()
        tasks.follow_up.assert_not_called()
        tasks.new_task.assert_not_called()

    def test_id_with_stop_interrupts(self, parts, poster):
        commands, tasks, links = parts
        make_router(commands, tasks, links, poster, is_operation=True) \
            .route("C1", "U1", "1.1", f"workspace_id={self.UUID} やめて")
        tasks.operation.assert_called_once_with("C1", "1.1", "やめて")

    def test_failed_explicit_attach_does_not_instruct(self, parts, poster):
        commands, tasks, links = parts
        tasks.attach_session.return_value = False           # ID のタブが無かった
        make_router(commands, tasks, links, poster) \
            .route("C1", "U1", "1.1", f"workspace_id={self.UUID} 続きをやって")
        tasks.follow_up.assert_not_called()
        tasks.new_task.assert_not_called()                  # 勝手に新規タブは作らない

    def test_bare_uuid_without_tab_is_normal_task(self, parts, poster):
        """目印の無い UUID は指示文の一部かもしれない → 該当タブが無ければ通常処理。"""
        commands, tasks, links = parts
        tasks.attach_session.return_value = False
        text = f"{self.UUID} のレコードを消して"
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", text)
        tasks.new_task.assert_called_once_with("C1", "U1", "1.1", text)


class TestPickerCancel:
    def test_non_digit_cancels_pick_and_routes_normally(self, parts, poster):
        commands, tasks, links = parts
        commands.has_pending_pick.return_value = True
        make_router(commands, tasks, links, poster).route("C1", "U1", "1.1", "別の指示")
        commands.cancel_pick.assert_called_once_with("1.1")
        commands.apply_pick.assert_not_called()
        tasks.new_task.assert_called_once()             # ピッカー破棄後は通常処理
