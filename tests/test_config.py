"""Config 読み込みのテスト（新書式・旧書式エラー・必須チェック）。"""
import os

import pytest
import yaml

from relay.config import Config


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RELAY_CONFIG", raising=False)
    return tmp_path


def write_config(tmp_path, data):
    with open(tmp_path / "config.yml", "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)


def base_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {
        "slack": {"bot_token": "xoxb-t", "app_token": "xapp-t"},
        "security": {"allowed_user_ids": ["U1"]},
        "repos": {"main": {"path": str(repo), "description": "本体"}},
    }


class TestLoad:
    def test_minimal(self, in_tmp):
        write_config(in_tmp, base_config(in_tmp))
        c = Config.load()
        assert c.default_repo == "main"
        assert c.target_repo == c.repos["main"]
        assert c.default_model == "fable"
        assert c.match_model == "sonnet"
        assert c.task_permission_mode == "acceptEdits"

    def test_repo_string_form(self, in_tmp):
        data = base_config(in_tmp)
        data["repos"] = {"main": data["repos"]["main"]["path"]}
        write_config(in_tmp, data)
        assert Config.load().repos["main"]

    def test_default_repo_explicit(self, in_tmp):
        data = base_config(in_tmp)
        second = in_tmp / "repo2"
        second.mkdir()
        data["repos"]["sub"] = str(second)
        data["default_repo"] = "sub"
        write_config(in_tmp, data)
        assert Config.load().default_repo == "sub"

    def test_missing_repos_exits(self, in_tmp):
        data = base_config(in_tmp)
        del data["repos"]
        write_config(in_tmp, data)
        with pytest.raises(SystemExit, match="repos が未設定"):
            Config.load()

    def test_nonexistent_repo_path_exits(self, in_tmp):
        data = base_config(in_tmp)
        data["repos"]["main"] = "/nonexistent/path"
        write_config(in_tmp, data)
        with pytest.raises(SystemExit, match="存在しません"):
            Config.load()

    def test_unknown_default_repo_exits(self, in_tmp):
        data = base_config(in_tmp)
        data["default_repo"] = "nazo"
        write_config(in_tmp, data)
        with pytest.raises(SystemExit, match="default_repo"):
            Config.load()

    def test_missing_tokens_exit(self, in_tmp):
        data = base_config(in_tmp)
        data["slack"]["bot_token"] = ""
        write_config(in_tmp, data)
        with pytest.raises(SystemExit, match="未設定"):
            Config.load()

    def test_legacy_keys_rejected_with_guidance(self, in_tmp):
        data = base_config(in_tmp)
        data["paths"] = {"target_repo": "/tmp"}
        data["behavior"] = {"default_model": "opus"}
        write_config(in_tmp, data)
        with pytest.raises(SystemExit, match="旧書式"):
            Config.load()

    def test_env_override(self, in_tmp, monkeypatch):
        write_config(in_tmp, base_config(in_tmp))
        monkeypatch.setenv("MODELS_TASK", "opus")
        assert Config.load().default_model == "opus"
