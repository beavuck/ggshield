"""An enabled plugin that fails to load is recorded, not silently swallowed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ggshield.core.config.enterprise_config import EnterpriseConfig
from ggshield.core.plugin.loader import DiscoveredPlugin, PluginLoader


def _enabled(name: str = "machine_scan") -> DiscoveredPlugin:
    return DiscoveredPlugin(
        name=name,
        entry_point=MagicMock(),
        wheel_path=None,
        is_installed=True,
        is_enabled=True,
        version="0.59.0",
    )


def test_load_failure_is_recorded_and_commands_not_registered():
    loader = PluginLoader(EnterpriseConfig())
    with patch.object(PluginLoader, "discover_plugins", return_value=[_enabled()]):
        with patch.object(
            PluginLoader,
            "_load_plugin",
            side_effect=ImportError(
                "Error relocating …: gnu_get_libc_version: symbol not found"
            ),
        ):
            registry = loader.load_enabled_plugins()

    failures = registry.get_load_failures()
    assert "machine_scan" in failures
    assert "symbol not found" in failures["machine_scan"]
    assert registry.get_commands() == []


def test_one_plugin_failing_does_not_block_the_others():
    good, bad = _enabled("good"), _enabled("bad")
    good_plugin = MagicMock()
    good_plugin.metadata.name = "good"
    good_plugin.metadata.version = "1.0.0"

    def load(discovered: DiscoveredPlugin) -> MagicMock:
        if discovered.name == "bad":
            raise RuntimeError("boom")
        return good_plugin

    loader = PluginLoader(EnterpriseConfig())
    # bad is first: a loop that aborted on failure would never reach good.
    with patch.object(PluginLoader, "discover_plugins", return_value=[bad, good]):
        with patch.object(PluginLoader, "_load_plugin", side_effect=load):
            with patch.object(
                PluginLoader, "_check_version_compatibility", return_value=True
            ):
                registry = loader.load_enabled_plugins()

    assert registry.get_load_failures() == {"bad": "boom"}
    assert registry.get_plugin("good") is good_plugin


def test_wheel_import_failure_records_real_reason(tmp_path):
    discovered = DiscoveredPlugin(
        name="machine_scan",
        entry_point=None,
        wheel_path=tmp_path / "machine_scan.whl",
        is_installed=True,
        is_enabled=True,
        version="0.59.0",
    )
    loader = PluginLoader(EnterpriseConfig())
    with patch.object(PluginLoader, "discover_plugins", return_value=[discovered]):
        with patch.object(
            PluginLoader,
            "_load_from_wheel",
            side_effect=ImportError("Error relocating libsatori.so: symbol not found"),
        ):
            registry = loader.load_enabled_plugins()

    assert "symbol not found" in registry.get_load_failures()["machine_scan"]


def test_none_load_result_is_recorded_as_failure():
    loader = PluginLoader(EnterpriseConfig())
    with patch.object(PluginLoader, "discover_plugins", return_value=[_enabled()]):
        with patch.object(PluginLoader, "_load_plugin", return_value=None):
            registry = loader.load_enabled_plugins()

    assert registry.get_load_failures()["machine_scan"] == "could not be loaded"
    assert registry.get_commands() == []


def test_version_incompatible_plugin_is_recorded_as_failure():
    plugin = MagicMock()
    plugin.metadata.name = "machine_scan"
    plugin.metadata.min_ggshield_version = "99.0.0"

    loader = PluginLoader(EnterpriseConfig())
    with patch.object(PluginLoader, "discover_plugins", return_value=[_enabled()]):
        with patch.object(PluginLoader, "_load_plugin", return_value=plugin):
            with patch.object(
                PluginLoader, "_check_version_compatibility", return_value=False
            ):
                registry = loader.load_enabled_plugins()

    assert "requires ggshield >= 99.0.0" in registry.get_load_failures()["machine_scan"]
    assert registry.get_commands() == []


def test_disabled_plugin_is_not_a_load_failure():
    disabled = DiscoveredPlugin(
        name="machine_scan",
        entry_point=MagicMock(),
        wheel_path=None,
        is_installed=True,
        is_enabled=False,
        version="0.59.0",
    )
    loader = PluginLoader(EnterpriseConfig())
    with patch.object(PluginLoader, "discover_plugins", return_value=[disabled]):
        registry = loader.load_enabled_plugins()

    assert registry.get_load_failures() == {}
