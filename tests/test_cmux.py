"""cmux アダプタのテスト。runner を偽装して CLI 出力のパースを検証。"""
from subprocess import CompletedProcess

from relay.cmux import CmuxClient, is_agent_session


def make_client(outputs: dict[tuple, str]) -> CmuxClient:
    """argv（bin 以降）の先頭一致で stdout を返す偽 runner を持つ client。"""
    def runner(argv, timeout):
        key = tuple(argv[1:])
        for k, v in outputs.items():
            if key[:len(k)] == k:
                return CompletedProcess(argv, 0, stdout=v, stderr="")
        return CompletedProcess(argv, 0, stdout="", stderr="")
    return CmuxClient("/fake/cmux", runner=runner)


WINDOWS = (
    "  0: F26B55DB-1E3B-4E97-9161-7689E786215F selected_workspace=B5053155 workspaces=1\n"
    "* 1: C160910B-EF5C-4571-AE2C-E9EFAF1E3AE1 selected_workspace=CE68EAF5 workspaces=2\n"
)


class TestListing:
    def test_windows_parsed(self):
        c = make_client({("list-windows",): WINDOWS})
        assert c._windows() == [
            "F26B55DB-1E3B-4E97-9161-7689E786215F",
            "C160910B-EF5C-4571-AE2C-E9EFAF1E3AE1",
        ]

    def test_workspaces_crosses_windows(self):
        c = make_client({
            ("list-windows",): WINDOWS,
            ("workspace", "list", "--window", "F26B55DB-1E3B-4E97-9161-7689E786215F"):
                "* workspace:4  タスクA  [selected]\n",
            ("workspace", "list", "--window", "C160910B-EF5C-4571-AE2C-E9EFAF1E3AE1"):
                "  workspace:1  iOS\n* workspace:3  作業中  [selected]\n",
        })
        assert c._list_workspaces() == [
            ("workspace:4", "", "タスクA"), ("workspace:1", "", "iOS"),
            ("workspace:3", "", "作業中")]

    def test_workspaces_with_uuid(self):
        # --id-format both のとき ref のあとに UUID が入る
        c = make_client({
            ("list-windows",): WINDOWS,
            ("workspace", "list", "--window", "F26B55DB-1E3B-4E97-9161-7689E786215F"):
                "* workspace:4  1A010C53-2190-4135-8EFF-AA18350D2FA0  タスクA  [selected]\n",
            ("workspace", "list", "--window", "C160910B-EF5C-4571-AE2C-E9EFAF1E3AE1"): "",
        })
        assert c._list_workspaces() == [
            ("workspace:4", "1A010C53-2190-4135-8EFF-AA18350D2FA0", "タスクA")]

    def test_surfaces_crosses_panes(self):
        c = make_client({
            ("list-panes", "--workspace", "workspace:3"):
                "* pane:4  [1 surface]  [focused]\n  pane:7  [1 surface]\n",
            ("list-pane-surfaces", "--workspace", "workspace:3", "--pane", "pane:4"):
                "* surface:4  メイン  [selected]\n",
            ("list-pane-surfaces", "--workspace", "workspace:3", "--pane", "pane:7"):
                "* surface:7  サブ  [selected]\n",
        })
        assert c._workspace_surfaces("workspace:3") == [
            ("surface:4", "", "メイン"), ("surface:7", "", "サブ")]

    def test_surface_exists_across_windows(self):
        c = make_client({
            ("list-windows",): WINDOWS,
            ("workspace", "list", "--window", "F26B55DB-1E3B-4E97-9161-7689E786215F"):
                "* workspace:4  タスクA  [selected]\n",
            ("workspace", "list", "--window", "C160910B-EF5C-4571-AE2C-E9EFAF1E3AE1"): "",
            ("list-panes", "--workspace", "workspace:4"): "* pane:5  [1 surface]  [focused]\n",
            ("list-pane-surfaces", "--workspace", "workspace:4", "--pane", "pane:5"):
                "* surface:5  タスクA  [selected]\n",
        })
        assert c.surface_exists("surface:5")
        assert not c.surface_exists("surface:999")


WS_UUID = "1A010C53-2190-4135-8EFF-AA18350D2FA0"
SURFACE_UUID = "F6A64616-3F9C-41CB-B83C-2C61B7918D89"


def make_id_client() -> CmuxClient:
    """UUID 付きで1ワークスペース1サーフェスを返す client。"""
    return make_client({
        ("list-windows",): "* 0: F26B55DB-1E3B-4E97-9161-7689E786215F workspaces=1\n",
        ("workspace", "list"): f"* workspace:12  {WS_UUID}  SwiftUI化チェック  [selected]\n",
        ("list-panes",): "* pane:14  [1 surface]  [focused]\n",
        ("list-pane-surfaces",): f"* surface:14  {SURFACE_UUID}  ✳ 調査中  [selected]\n",
    })


class TestIdLookup:
    """Slack に貼られた UUID からセッションを引く（ID 指名での合流）。"""

    def test_workspace_session_by_uuid(self):
        s = make_id_client().workspace_session(WS_UUID)
        assert s is not None
        assert (s.surface, s.workspace, s.name, s.workspace_id) == (
            "surface:14", "workspace:12", "SwiftUI化チェック", WS_UUID)

    def test_workspace_session_by_ref(self):
        s = make_id_client().workspace_session("workspace:12")
        assert s is not None and s.surface == "surface:14"

    def test_unknown_workspace_is_none(self):
        c = make_client({("list-windows",): "* 0: F26B55DB-1E3B-4E97-9161-7689E786215F\n"})
        assert c.workspace_session(WS_UUID) is None

    def test_find_surface_by_uuid(self):
        s = make_id_client().find_surface(SURFACE_UUID.lower())
        assert s is not None
        assert (s.surface, s.workspace_id) == ("surface:14", WS_UUID)

    def test_find_surface_unknown_is_none(self):
        assert make_id_client().find_surface(WS_UUID) is None

    def test_workspace_surfaces_lists_refs(self):
        assert make_id_client().workspace_surfaces(WS_UUID) == ["surface:14"]


class TestIsAgentSession:
    """合流先になり得るタブの判定（素のシェル・端末以外を候補から外す）。"""

    def test_claude_process_is_agent(self):
        # claude はプロセス名がバージョン番号で見える
        assert is_agent_session([("100", "zsh"), ("101", "2.1.232")], "")

    def test_claude_ui_on_screen_is_agent(self):
        assert is_agent_session([], "⏵⏵ auto mode on (shift+tab to cycle) · ← for agents")

    def test_plain_shell_is_not_agent(self):
        assert not is_agent_session([("100", "zsh")], "~/program ❯ ls\nREADME.md")

    def test_non_terminal_surface_is_not_agent(self):
        # 端末以外（エディタ等）は read-screen が空を返し、プロセスも見えない
        assert not is_agent_session([], "")


class TestSurfaceHasProcess:
    """claude の生死判定。claude はプロセス名がバージョン番号（例 2.1.224）で
    見えるため、名前ではなく「シェル以外の作業プロセスの有無」で判定する（実障害の再現）。"""

    @staticmethod
    def _client_with_top(rows):
        tsv = "\n".join(
            f"0.0\t0.0\t1\tprocess\t{pid}\t{surface}\t{name}"
            for pid, surface, name in rows)
        return make_client({("top",): tsv})

    def test_claude_named_as_version_number_is_alive(self):
        # 実際に観測された構成: zsh + sleep + "2.1.224"(claude)
        c = self._client_with_top([
            ("100", "surface:11", "zsh"),
            ("101", "surface:11", "sleep"),
            ("102", "surface:11", "2.1.224"),
        ])
        assert c.surface_has_process("surface:11")

    def test_shell_only_is_dead(self):
        c = self._client_with_top([("100", "surface:11", "zsh")])
        assert not c.surface_has_process("surface:11")

    def test_shell_and_sleep_is_dead(self):
        c = self._client_with_top([
            ("100", "surface:11", "zsh"),
            ("101", "surface:11", "sleep"),
        ])
        assert not c.surface_has_process("surface:11")

    def test_no_data_is_alive(self):
        # 情報が取れないときに「死んだ」と誤検知して追跡を切ってはいけない
        c = self._client_with_top([])
        assert c.surface_has_process("surface:99")
