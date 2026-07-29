"""#81 condition 1: one presentation layer, three renderers, drift impossible.

Every frontend must declare ASSISTANT_WIDGET_BUILDERS covering EXACTLY
ui_model.ASSISTANT_ELEMENTS, and (when its toolkit is importable) implement
every builder method. A new element added to the presentation layer breaks
all three frontends until each renders it - forgetting is structurally
impossible, not procedurally forbidden.
"""

from __future__ import annotations

import importlib

import pytest

from docker_app_launcher import ui_model

_FRONTENDS = [
    ("tk_window", "LauncherApp", None),
    ("ctk_window", "CtkLauncherApp", "HAS_CTK"),
    ("qt_window", "QtLauncherApp", "HAS_QT"),
]


class TestAssistantParityAcrossFrontends:
    @pytest.mark.parametrize(("module_name", "class_name", "guard"), _FRONTENDS)
    def test_frontend_declares_and_implements_every_element(
        self, module_name: str, class_name: str, guard: str | None
    ) -> None:
        module = importlib.import_module(f"docker_app_launcher.frontends.{module_name}")
        builders = getattr(module, "ASSISTANT_WIDGET_BUILDERS", None)
        assert builders is not None, f"{module_name} renders no assistant at all"
        assert set(builders) == set(ui_model.ASSISTANT_ELEMENTS), (
            f"{module_name} diverges from the presentation layer: "
            f"missing {set(ui_model.ASSISTANT_ELEMENTS) - set(builders)}, "
            f"extra {set(builders) - set(ui_model.ASSISTANT_ELEMENTS)}"
        )
        if guard is not None and not getattr(module, guard):
            pytest.skip(f"{module_name}: toolkit not installed - builder methods unverifiable here")
        window_class = getattr(module, class_name)
        missing = [m for m in builders.values() if not callable(getattr(window_class, m, None))]
        assert not missing, f"{module_name}.{class_name} lacks builder method(s): {missing}"

    def test_builder_method_names_agree_across_frontends(self) -> None:
        # Same element, same builder name everywhere - reading one frontend
        # must teach you all three.
        dicts = [
            getattr(importlib.import_module(f"docker_app_launcher.frontends.{m}"), "ASSISTANT_WIDGET_BUILDERS", {})
            for m, _, _ in _FRONTENDS
        ]
        assert dicts[0] == dicts[1] == dicts[2], f"builder maps diverge: {dicts}"
