"""永続化層（TaskStore / SettingsStore / ThreadLinks / RuntimeSettings）のテスト。"""
from relay.registry import Task, MODE_TASK, STATUS_ACTIVE, STATUS_DONE, now_iso
from relay.settings import RuntimeSettings


def _task(thread_ts="111.1", status=STATUS_ACTIVE) -> Task:
    return Task(thread_ts=thread_ts, channel_id="C1", user_id="U1", mode=MODE_TASK,
                branch=None, session_id=None, anchor_ts=None, status=status,
                created_at=now_iso(), updated_at=now_iso())


class TestTaskStore:
    def test_create_get(self, task_store):
        task_store.create(_task())
        t = task_store.get("111.1")
        assert t and t.channel_id == "C1" and t.status == STATUS_ACTIVE

    def test_update(self, task_store):
        task_store.create(_task())
        task_store.update("111.1", status=STATUS_DONE)
        assert task_store.get("111.1").status == STATUS_DONE

    def test_active_filters_done(self, task_store):
        task_store.create(_task("111.1"))
        task_store.create(_task("222.2", status=STATUS_DONE))
        assert [t.thread_ts for t in task_store.active()] == ["111.1"]


class TestSettingsStore:
    def test_get_set(self, settings_store):
        settings_store.set("k", "v")
        assert settings_store.get("k") == "v"
        assert settings_store.get("missing", "d") == "d"

    def test_with_prefix(self, settings_store):
        settings_store.set("surface:1", "surface:10")
        settings_store.set("surface:2", "surface:20")
        settings_store.set("instr:1", "x")
        assert settings_store.with_prefix("surface:") == {
            "surface:1": "surface:10", "surface:2": "surface:20"}

    def test_prefix_escapes_like_wildcards(self, settings_store):
        settings_store.set("a_b:1", "v")
        settings_store.set("axb:1", "w")
        assert settings_store.with_prefix("a_b:") == {"a_b:1": "v"}


class TestThreadLinks:
    def test_surface_roundtrip(self, links):
        links.link_surface("111.1", "surface:10")
        assert links.surface_of("111.1") == "surface:10"

    def test_linked_surfaces_excludes_self(self, links):
        links.link_surface("111.1", "surface:10")
        links.link_surface("222.2", "surface:20")
        assert links.linked_surfaces(exclude="111.1") == {"surface:20"}
        assert links.linked_surfaces() == {"surface:10", "surface:20"}

    def test_repo_and_instruction(self, links):
        links.set_repo("111.1", "docs")
        links.set_instruction("111.1", "  直して  ")
        assert links.repo_of("111.1") == "docs"
        assert links.instruction_of("111.1") == "直して"


class TestRuntimeSettings:
    def test_seeds_defaults(self, settings_store):
        rt = RuntimeSettings(settings_store, "fable", "acceptEdits")
        assert rt.model == "fable" and rt.permission_mode == "acceptEdits"

    def test_stored_value_wins_over_default(self, settings_store):
        RuntimeSettings(settings_store, "fable", "acceptEdits").set_model("opus")
        rt = RuntimeSettings(settings_store, "haiku", "plan")
        assert rt.model == "opus"          # 既存値が守られる
        assert rt.permission_mode == "acceptEdits"

    def test_last_processed_ts(self, settings_store):
        rt = RuntimeSettings(settings_store, "fable", "acceptEdits")
        assert rt.last_processed_ts is None
        rt.mark_processed("123.456")
        assert rt.last_processed_ts == "123.456"
