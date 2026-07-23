"""Tests for the GitHub Releases update check."""

from __future__ import annotations

import io
import json
import urllib.request

import pytest

from docker_app_launcher import update_check
from docker_app_launcher.config import LauncherConfig


class TestGithubApiUrl:
    def test_web_url(self) -> None:
        assert (
            update_check.github_api_releases_url("https://github.com/astrapi69/adaptive-learner")
            == "https://api.github.com/repos/astrapi69/adaptive-learner/releases/latest"
        )

    def test_releases_latest_url(self) -> None:
        assert (
            update_check.github_api_releases_url("https://github.com/foo/bar/releases/latest")
            == "https://api.github.com/repos/foo/bar/releases/latest"
        )

    def test_empty_is_none(self) -> None:
        assert update_check.github_api_releases_url("") is None

    def test_non_github_is_none(self) -> None:
        assert update_check.github_api_releases_url("https://gitlab.com/foo/bar") is None


class TestIsNewer:
    @pytest.mark.parametrize(
        ("current", "latest", "expected"),
        [
            ("1.0.0", "1.0.1", True),
            ("v1.0.0", "v1.1.0", True),
            ("1.2.0", "1.2.0", False),
            ("2.0.0", "1.9.9", False),
            ("1.95.0", "v1.96.0", True),
        ],
    )
    def test_compare(self, current: str, latest: str, expected: bool) -> None:
        assert update_check.is_newer(current, latest) is expected

    def test_malformed_tag_is_not_newer(self) -> None:
        assert update_check.is_newer("1.0.0", "not-a-version") is False


def _fake_urlopen(payload: dict[str, object]):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    return lambda req, timeout=0: _Resp(json.dumps(payload).encode())


class TestFetchLatest:
    def test_ok(self, monkeypatch) -> None:
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            _fake_urlopen({"tag_name": "v1.2.0", "html_url": "https://example/r"}),
        )
        assert update_check.fetch_latest_release("https://api/x") == ("v1.2.0", "https://example/r")

    def test_missing_fields_is_none(self, monkeypatch) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"tag_name": "v1.2.0"}))
        assert update_check.fetch_latest_release("https://api/x") is None

    def test_network_error_is_none(self, monkeypatch) -> None:
        def boom(req, timeout=0):
            raise OSError("no network")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        assert update_check.fetch_latest_release("https://api/x") is None


class TestCheckForUpdateAsync:
    def _config(self, **kw) -> LauncherConfig:
        cfg = LauncherConfig(
            app_name="Test App",
            repo_url="https://github.com/foo/bar",
            app_version="1.0.0",
            **kw,
        )
        return cfg.resolve()

    def test_disabled_returns_none(self) -> None:
        cfg = self._config(update_check_enabled=False)
        assert update_check.check_for_update_async(cfg, lambda t, u: None) is None

    def test_no_app_version_returns_none(self) -> None:
        cfg = LauncherConfig(app_name="X", repo_url="https://github.com/foo/bar").resolve()
        assert update_check.check_for_update_async(cfg, lambda t, u: None) is None

    def test_no_repo_returns_none(self) -> None:
        cfg = LauncherConfig(app_name="X", app_version="1.0.0").resolve()
        assert update_check.check_for_update_async(cfg, lambda t, u: None) is None

    def test_newer_invokes_callback(self, monkeypatch) -> None:
        cfg = self._config()
        monkeypatch.setattr(update_check, "fetch_latest_release", lambda url, user_agent="": ("v2.0.0", "u"))
        seen: list[tuple[str, str]] = []
        thread = update_check.check_for_update_async(cfg, lambda t, u: seen.append((t, u)))
        assert thread is not None
        thread.join(timeout=2)
        assert seen == [("v2.0.0", "u")]

    def test_not_newer_skips_callback(self, monkeypatch) -> None:
        cfg = self._config()
        monkeypatch.setattr(update_check, "fetch_latest_release", lambda url, user_agent="": ("v1.0.0", "u"))
        seen: list[tuple[str, str]] = []
        thread = update_check.check_for_update_async(cfg, lambda t, u: seen.append((t, u)))
        assert thread is not None
        thread.join(timeout=2)
        assert seen == []


class TestCallbackSafety:
    def test_broken_notification_callback_is_swallowed(self, monkeypatch) -> None:
        cfg = LauncherConfig(
            app_name="X", app_version="1.0.0", repo_url="https://github.com/owner/repo", update_check_enabled=True
        ).resolve()
        monkeypatch.setattr(update_check, "fetch_latest_release", lambda url, user_agent: ("v9.9.9", "https://rel"))

        def broken(tag: str, url: str) -> None:
            raise RuntimeError("UI is gone")

        thread = update_check.check_for_update_async(cfg, broken)
        assert thread is not None
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    def test_no_release_found_is_silent(self, monkeypatch) -> None:
        cfg = LauncherConfig(
            app_name="X", app_version="1.0.0", repo_url="https://github.com/owner/repo", update_check_enabled=True
        ).resolve()
        monkeypatch.setattr(update_check, "fetch_latest_release", lambda url, user_agent: None)
        called: list[str] = []
        thread = update_check.check_for_update_async(cfg, lambda tag, url: called.append(tag))
        assert thread is not None
        thread.join(timeout=5.0)
        assert called == []
