"""Configurable desktop launcher for Docker-based applications.

Public API::

    from docker_app_launcher import LauncherConfig, launch

    launch(
        LauncherConfig(
            app_name="My App",
            container_name="my-app",
            default_port=8080,
        )
    )
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from docker_app_launcher.config import LauncherConfig

try:
    __version__ = version("docker-app-launcher")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.1.0"

__all__ = ["LauncherConfig", "__version__", "launch"]


def launch(config: LauncherConfig | None = None, **kwargs: object) -> int:
    """Launch the GUI with the given config (or one built from ``kwargs``).

    Usage::

        launch(LauncherConfig(app_name="My App", default_port=8080))
        # or
        launch(app_name="My App", default_port=8080)
    """
    if config is None:
        config = LauncherConfig(**kwargs)  # type: ignore[arg-type]
    config.resolve()

    from docker_app_launcher.gui import run

    return run(config)
