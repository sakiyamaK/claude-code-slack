"""cmux アダプタのテスト。runner を偽装して CLI 出力のパースを検証。"""
from subprocess import CompletedProcess

from relay.cmux import CmuxClient


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
            ("workspace:4", "タスクA"), ("workspace:1", "iOS"), ("workspace:3", "作業中")]

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
            ("surface:4", "メイン"), ("surface:7", "サブ")]

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
