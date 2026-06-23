# Test Plan

All tests run **without Docker and without a display**. Docker is mocked at
`actions._run` (or the higher-level helpers); ports use real sockets bound to
ephemeral numbers; config and manifest writes are redirected to `tmp_path`; the
health check mocks `urllib.request.urlopen`. `HOME` is isolated per test
(`conftest.isolate_home`) so no real user config dir is touched.

Run everything with:

```bash
poetry run pytest -v
make ci   # also runs ruff + ruff format --check + mypy
```

## Coverage by area

| File                | What is verified                                                                 |
|---------------------|----------------------------------------------------------------------------------|
| `test_config.py`    | Slug derivation, `resolve()` defaults + idempotence, computed paths, name/image/cleanup filters, JSON round-trip, unknown-key tolerance. |
| `test_i18n.py`      | EN/DE lookup, English fallback, `{app}`/kwarg interpolation, custom-string overrides, EN↔DE key parity. |
| `test_actions.py`   | `check_docker` / `docker_installed`, `get_state` (4 states), port probing + `find_free_port`, port persistence (`set_port`/`resolve_port`/`.env`), `install` (success + every guard), `start`, `stop`, `uninstall` (verbose + partial), health probing, browser opening, install manifest, stale-artifact cleanup, human-readable sizes. |
| `test_gui_helpers.py` | Pure helpers `port_editable`, `buttons_for_state`, `dispatch_action`, `should_minimize_to_tray` — no Tk window created. |
| `test_tray.py`      | `tray_available`, menu ids + localized labels, icon loading guards, `TrayController.start()` graceful failure without an icon, handler adaptation. |
| `test_cli.py`       | Argument parsing, `--version`, every action flag routes through `actions` (CLI↔GUI parity), `--port` validation/persistence, GUI fallback when no action flag. |

## Conventions

- **Minimum 5 tests per non-trivial action.**
- **Verify, don't assume:** lifecycle tests assert the post-condition the action
  itself checks (e.g. uninstall reporting a remaining container ⇒ failure).
- **No hidden Docker calls:** any test that reaches the install manifest also
  mocks `actions._run` so `collect_installed_artifacts` cannot shell out.

## Manual smoke (with Docker + a display)

```bash
echo '{"app_name":"Test","container_name":"test"}' > /tmp/test.json
docker-app-launcher --config /tmp/test.json --check
docker-app-launcher --config /tmp/test.json        # opens the window
```
