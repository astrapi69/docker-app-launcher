"""Background update check against the GitHub Releases API.

Fails completely silently on any error (no network, GitHub down, rate
limit, malformed response). The launcher must never be blocked or
interrupted by the update check; the user sees a notification only when a
strictly newer release than :attr:`LauncherConfig.app_version` is actually
available for :attr:`LauncherConfig.repo_url`.

Stdlib only (``urllib``, ``json``, ``re``, ``threading``).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from collections.abc import Callable

from docker_app_launcher.config import LauncherConfig

logger = logging.getLogger("docker_app_launcher.update_check")

TIMEOUT_SECONDS = 5.0

# Matches an owner/repo out of a github.com web URL (with or without a
# trailing path such as ``/releases/latest`` or a ``.git`` suffix).
_GITHUB_REPO_RE = re.compile(r"github\.com[/:]([^/]+)/([^/.]+)")


def github_api_releases_url(repo_url: str) -> str | None:
    """Convert a GitHub repo/web URL into its ``releases/latest`` API URL.

    Returns ``None`` when ``repo_url`` is empty or not a GitHub URL.
    """
    if not repo_url:
        return None
    match = _GITHUB_REPO_RE.search(repo_url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def is_newer(current: str, latest: str) -> bool:
    """True if ``latest`` is a strictly greater version than ``current``.

    Both accept an optional leading ``v``. Components compare as integers;
    any parse error returns False so a malformed tag never flags a bogus
    update.
    """

    def parse(value: str) -> tuple[int, ...]:
        return tuple(int(x) for x in value.lstrip("v").split("."))

    try:
        return parse(latest) > parse(current)
    except (ValueError, AttributeError):
        return False


def fetch_latest_release(api_url: str, *, user_agent: str = "docker-app-launcher") -> tuple[str, str] | None:
    """Return ``(tag_name, html_url)`` of the latest release, or ``None``.

    Silent on any failure: network error, timeout, rate limit, JSON parse
    error, missing fields. The caller treats ``None`` as "no update info,
    proceed as usual".
    """
    try:
        req = urllib.request.Request(
            api_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": user_agent},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name")
        url = data.get("html_url")
        if not tag or not url:
            return None
        return tag, url
    except Exception as exc:  # noqa: BLE001 - genuinely want to swallow everything
        logger.info("update check failed silently: %s", exc)
        return None


def check_for_update_async(
    config: LauncherConfig,
    on_update_available: Callable[[str, str], None],
) -> threading.Thread | None:
    """Run the version check in a background daemon thread.

    Invokes ``on_update_available(tag, html_url)`` on the worker thread only
    when a strictly newer release is found. The callback marshals any UI
    update back to the main thread itself (this module never touches Tk).

    Returns the started thread, or ``None`` when the check is disabled or not
    enough config is present (no ``repo_url``/``app_version``, or
    ``update_check_enabled`` is False).
    """
    if not config.update_check_enabled or not config.app_version:
        return None
    api_url = github_api_releases_url(config.releases_url or config.repo_url)
    if api_url is None:
        return None

    current = config.app_version
    user_agent = config.app_slug or "docker-app-launcher"

    def _run() -> None:
        result = fetch_latest_release(api_url, user_agent=user_agent)
        if result is None:
            return
        tag, url = result
        if is_newer(current, tag):
            try:
                on_update_available(tag, url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("update notification callback raised: %s", exc)

    thread = threading.Thread(target=_run, daemon=True, name="docker-app-launcher-update-check")
    thread.start()
    return thread
