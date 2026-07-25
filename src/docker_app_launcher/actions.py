"""FACADE - the stable public entry point for all launcher actions.

The implementation lives in modules whose names say what they do:

- :mod:`docker_detection`  - is Docker usable here, and why not
- :mod:`docker_lifecycle`  - install / start / stop / uninstall / health
- :mod:`docker_cleanup`    - find and remove leftovers of old installs
- :mod:`launcher_settings` - launcher.json + .env persistence (ports, locale, geometry)
- :mod:`install_manifest`  - what we installed, for precise cleanup
- :mod:`docker_cli`        - the shared subprocess/streaming layer

This module only re-exports their public API so ``actions.install`` etc.
keep working unchanged (the SemVer contract of the package).
"""

from __future__ import annotations

from docker_app_launcher.docker.cleanup import (
    _human_size as _human_size,
)
from docker_app_launcher.docker.cleanup import (
    _remove_config_path as _remove_config_path,
)
from docker_app_launcher.docker.cleanup import (
    _searched_config_dirs as _searched_config_dirs,
)
from docker_app_launcher.docker.cleanup import (
    _stale_config_dirs as _stale_config_dirs,
)
from docker_app_launcher.docker.cleanup import (
    cleanup_offer_lines as cleanup_offer_lines,
)
from docker_app_launcher.docker.cleanup import (
    cleanup_stale as cleanup_stale,
)
from docker_app_launcher.docker.cleanup import (
    find_stale_artifacts as find_stale_artifacts,
)
from docker_app_launcher.docker.cleanup import (
    has_stale_artifacts as has_stale_artifacts,
)
from docker_app_launcher.docker.command_runner import (
    DockerBuildProgress as DockerBuildProgress,
)
from docker_app_launcher.docker.command_runner import (
    OutputFn as OutputFn,
)
from docker_app_launcher.docker.command_runner import (
    ProgressFn as ProgressFn,
)
from docker_app_launcher.docker.command_runner import (
    ProgressPctFn as ProgressPctFn,
)
from docker_app_launcher.docker.command_runner import (
    _docker_op as _docker_op,
)
from docker_app_launcher.docker.command_runner import (
    _first_line as _first_line,
)
from docker_app_launcher.docker.command_runner import (
    _notify as _notify,
)
from docker_app_launcher.docker.command_runner import (
    _progress as _progress,
)
from docker_app_launcher.docker.command_runner import (
    _reset_docker_host_override as _reset_docker_host_override,
)
from docker_app_launcher.docker.command_runner import (
    _run as _run,
)
from docker_app_launcher.docker.command_runner import (
    _step_label as _step_label,
)
from docker_app_launcher.docker.command_runner import (
    _stream_command as _stream_command,
)
from docker_app_launcher.docker.command_runner import (
    _t as _t,
)
from docker_app_launcher.docker.command_runner import (
    docker_host_override as docker_host_override,
)
from docker_app_launcher.docker.detection import (
    _DOCKER_INSTALL_URLS as _DOCKER_INSTALL_URLS,
)
from docker_app_launcher.docker.detection import (
    _active_context as _active_context,
)
from docker_app_launcher.docker.detection import (
    _docker_contexts as _docker_contexts,
)
from docker_app_launcher.docker.detection import (
    _docker_info_rc as _docker_info_rc,
)
from docker_app_launcher.docker.detection import (
    _probe_unix_socket as _probe_unix_socket,
)
from docker_app_launcher.docker.detection import (
    _sweep_other_contexts as _sweep_other_contexts,
)
from docker_app_launcher.docker.detection import (
    add_user_to_docker_group as add_user_to_docker_group,
)
from docker_app_launcher.docker.detection import (
    check_docker as check_docker,
)
from docker_app_launcher.docker.detection import (
    check_docker_detailed as check_docker_detailed,
)
from docker_app_launcher.docker.detection import (
    docker_installed as docker_installed,
)
from docker_app_launcher.docker.detection import (
    start_docker_daemon as start_docker_daemon,
)
from docker_app_launcher.docker.detection import (
    start_docker_desktop as start_docker_desktop,
)
from docker_app_launcher.docker.detection import (
    wait_for_docker as wait_for_docker,
)
from docker_app_launcher.docker.inventory import (
    _docker_names as _docker_names,
)
from docker_app_launcher.docker.inventory import (
    _image_refs as _image_refs,
)
from docker_app_launcher.docker.inventory import (
    _image_size_bytes as _image_size_bytes,
)
from docker_app_launcher.docker.inventory import (
    _name_filter_args as _name_filter_args,
)
from docker_app_launcher.docker.inventory import (
    _project_container_ids as _project_container_ids,
)
from docker_app_launcher.docker.inventory import (
    _project_containers as _project_containers,
)
from docker_app_launcher.docker.inventory import (
    _project_images as _project_images,
)
from docker_app_launcher.docker.inventory import (
    _project_volumes as _project_volumes,
)
from docker_app_launcher.docker.inventory import (
    _running_container_names as _running_container_names,
)
from docker_app_launcher.docker.lifecycle import (
    _call as _call,
)
from docker_app_launcher.docker.lifecycle import (
    _compose_args as _compose_args,
)
from docker_app_launcher.docker.lifecycle import (
    _health_payload as _health_payload,
)
from docker_app_launcher.docker.lifecycle import (
    _health_probe as _health_probe,
)
from docker_app_launcher.docker.lifecycle import (
    _stream_build_with_progress as _stream_build_with_progress,
)
from docker_app_launcher.docker.lifecycle import (
    _stream_compose as _stream_compose,
)
from docker_app_launcher.docker.lifecycle import (
    _uninstall_images as _uninstall_images,
)
from docker_app_launcher.docker.lifecycle import (
    app_logs as app_logs,
)
from docker_app_launcher.docker.lifecycle import (
    change_internal_port as change_internal_port,
)
from docker_app_launcher.docker.lifecycle import (
    change_port as change_port,
)
from docker_app_launcher.docker.lifecycle import (
    ensure_installed as ensure_installed,
)
from docker_app_launcher.docker.lifecycle import (
    get_app_version as get_app_version,
)
from docker_app_launcher.docker.lifecycle import (
    get_state as get_state,
)
from docker_app_launcher.docker.lifecycle import (
    get_version as get_version,
)
from docker_app_launcher.docker.lifecycle import (
    health_check as health_check,
)
from docker_app_launcher.docker.lifecycle import (
    install as install,
)
from docker_app_launcher.docker.lifecycle import (
    is_healthy as is_healthy,
)
from docker_app_launcher.docker.lifecycle import (
    open_browser as open_browser,
)
from docker_app_launcher.docker.lifecycle import (
    open_url as open_url,
)
from docker_app_launcher.docker.lifecycle import (
    start as start,
)
from docker_app_launcher.docker.lifecycle import (
    stop as stop,
)
from docker_app_launcher.docker.lifecycle import (
    stream_app_logs as stream_app_logs,
)
from docker_app_launcher.docker.lifecycle import (
    uninstall as uninstall,
)
from docker_app_launcher.install_manifest import (
    _now as _now,
)
from docker_app_launcher.install_manifest import (
    _record_manifest as _record_manifest,
)
from docker_app_launcher.install_manifest import (
    _write_manifest as _write_manifest,
)
from docker_app_launcher.install_manifest import (
    append_history as append_history,
)
from docker_app_launcher.install_manifest import (
    collect_installed_artifacts as collect_installed_artifacts,
)
from docker_app_launcher.install_manifest import (
    manifest_artifacts as manifest_artifacts,
)
from docker_app_launcher.install_manifest import (
    mark_uninstalled as mark_uninstalled,
)
from docker_app_launcher.install_manifest import (
    read_manifest as read_manifest,
)
from docker_app_launcher.install_manifest import (
    write_manifest as write_manifest,
)
from docker_app_launcher.launcher_settings import (
    _GEOMETRY_RE as _GEOMETRY_RE,
)
from docker_app_launcher.launcher_settings import (
    MAX_PORT as MAX_PORT,
)
from docker_app_launcher.launcher_settings import (
    MIN_INTERNAL_PORT as MIN_INTERNAL_PORT,
)
from docker_app_launcher.launcher_settings import (
    MIN_PORT as MIN_PORT,
)
from docker_app_launcher.launcher_settings import (
    _compose_cwd as _compose_cwd,
)
from docker_app_launcher.launcher_settings import (
    _env_path as _env_path,
)
from docker_app_launcher.launcher_settings import (
    _env_port_updates as _env_port_updates,
)
from docker_app_launcher.launcher_settings import (
    _upsert_env_line as _upsert_env_line,
)
from docker_app_launcher.launcher_settings import (
    _validate_internal_port as _validate_internal_port,
)
from docker_app_launcher.launcher_settings import (
    _validate_port as _validate_port,
)
from docker_app_launcher.launcher_settings import (
    _write_env as _write_env,
)
from docker_app_launcher.launcher_settings import (
    _write_env_port as _write_env_port,
)
from docker_app_launcher.launcher_settings import (
    _write_env_ports as _write_env_ports,
)
from docker_app_launcher.launcher_settings import (
    check_port as check_port,
)
from docker_app_launcher.launcher_settings import (
    find_free_port as find_free_port,
)
from docker_app_launcher.launcher_settings import (
    load_config as load_config,
)
from docker_app_launcher.launcher_settings import (
    resolve_internal_port as resolve_internal_port,
)
from docker_app_launcher.launcher_settings import (
    resolve_locale as resolve_locale,
)
from docker_app_launcher.launcher_settings import (
    resolve_port as resolve_port,
)
from docker_app_launcher.launcher_settings import (
    resolve_window_geometry as resolve_window_geometry,
)
from docker_app_launcher.launcher_settings import (
    save_config as save_config,
)
from docker_app_launcher.launcher_settings import (
    set_internal_port as set_internal_port,
)
from docker_app_launcher.launcher_settings import (
    set_locale as set_locale,
)
from docker_app_launcher.launcher_settings import (
    set_port as set_port,
)
from docker_app_launcher.launcher_settings import (
    set_window_geometry as set_window_geometry,
)
