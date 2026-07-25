"""Everything Docker: one sub-package, one concern per module.

- :mod:`.detection` - is Docker usable here, and why not
- :mod:`.lifecycle` - install / start / stop / uninstall / health / get_state
- :mod:`.cleanup`   - find and remove leftovers of previous installs
- :mod:`.inventory` - which docker objects belong to this app (read-only)
- :mod:`.cli`       - the shared subprocess/streaming layer underneath

The public API is re-exported by :mod:`docker_app_launcher.actions`.
"""
