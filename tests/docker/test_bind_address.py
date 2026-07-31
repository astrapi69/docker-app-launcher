"""The published port binds to LOCALHOST unless someone opens it (#111).

Security finding, measured at the running container before this existed:

    $ docker inspect -f '{{json .NetworkSettings.Ports}}' dal-bind-image
    {"80/tcp":[{"HostIp":"0.0.0.0","HostPort":"18231"},
               {"HostIp":"::","HostPort":"18231"}]}

Every interface, IPv4 and IPv6, in both modes the launcher publishes
itself - while the documentation promised localhost. The app carries no
authentication and holds the user's provider keys, so reachability from
the network is not a cosmetic difference.

Cause: docker-py's bare-int port form (``ports={"80/tcp": 8080}``) means
"all interfaces". The tuple form pins the interface.

After the fix, measured the same way:

    {"80/tcp":[{"HostIp":"127.0.0.1","HostPort":"18231"}]}

These tests pin the DECISION so it cannot silently reopen; the real-daemon
counterpart in tests/integration/test_lifecycle_matrix_real.py pins the
EFFECT at a running container.
"""

from __future__ import annotations

import logging

import pytest

from docker_app_launcher.config import LauncherConfig
from docker_app_launcher.docker.dockerfile_backend import LOCALHOST_BIND, OPEN_BINDS, port_binding


def _cfg(**kw: object) -> LauncherConfig:
    return LauncherConfig(
        app_name="Bind",
        container_name="bind",
        image_name="bind:test",
        compose_file="docker-compose.yml",
        locale="en",
        **kw,  # type: ignore[arg-type]
    )


class TestTheDefaultIsLocalhost:
    def test_an_untouched_config_binds_to_localhost(self) -> None:
        assert _cfg().bind_address == LOCALHOST_BIND

    def test_the_publish_form_is_the_interface_pinning_tuple(self) -> None:
        """A bare int would mean 0.0.0.0 - that was the finding."""
        binding = port_binding(_cfg(), 8080)
        assert binding == ("127.0.0.1", 8080)
        assert not isinstance(binding, int), "a bare int publishes on EVERY interface in docker-py - that is #111"

    def test_an_empty_bind_address_falls_back_to_localhost(self) -> None:
        """A config that leaves the field blank must not open the machine up."""
        assert port_binding(_cfg(bind_address=""), 8080) == ("127.0.0.1", 8080)

    def test_whitespace_is_not_an_accidental_opening(self) -> None:
        assert port_binding(_cfg(bind_address="  127.0.0.1  "), 8080) == ("127.0.0.1", 8080)


class TestOpeningIsDeliberateAndLoud:
    @pytest.mark.parametrize("address", OPEN_BINDS)
    def test_every_open_form_warns_at_the_moment_of_opening(self, address: str, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.dockerfile_backend"):
            binding = port_binding(_cfg(bind_address=address), 8080)
        assert binding == (address, 8080)
        assert caplog.records, f"opening on {address!r} passed silently"
        text = caplog.text
        assert "reachable from EVERY network" in text
        assert "127.0.0.1" in text, "the warning must name the way back, not just the danger"

    def test_localhost_does_not_warn(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="docker_app_launcher.docker.dockerfile_backend"):
            port_binding(_cfg(), 8080)
        assert not caplog.records, "the safe default must not train users to ignore the warning"

    def test_a_specific_lan_address_is_honoured_without_the_open_warning(self) -> None:
        """Binding to one interface on purpose is a legitimate middle ground."""
        assert port_binding(_cfg(bind_address="192.168.1.10"), 8080) == ("192.168.1.10", 8080)


class TestBothApiModesUseTheSameRule:
    """The rule must not drift apart between the two modes that publish."""

    @pytest.mark.parametrize(
        "module_name",
        ["docker_app_launcher.docker.image_backend", "docker_app_launcher.docker.dockerfile_backend"],
    )
    def test_the_mode_publishes_through_the_shared_helper(self, module_name: str) -> None:
        import inspect

        module = __import__(module_name, fromlist=["*"])
        source = inspect.getsource(module)
        assert "port_binding(config, host_port)" in source, (
            f"{module_name} builds its own port mapping - the binding rule would drift (#111)"
        )
        assert 'ports={f"{container_port}/tcp": host_port}' not in source, (
            f"{module_name} still uses the bare-int form, which publishes on every interface"
        )
