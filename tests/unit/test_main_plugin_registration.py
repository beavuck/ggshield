"""Tests for plugin command registration in __main__.py."""

from unittest import mock

import click
import pytest


def test_register_plugin_commands_skips_conflicts(monkeypatch) -> None:
    """Plugin commands must not override built-in commands."""
    import ggshield.__main__ as main_module

    plugin_conflicting_cmd = click.Command("auth")
    plugin_new_cmd = click.Command("plugin-extra")

    mock_registry = mock.MagicMock()
    mock_registry.get_commands.return_value = [plugin_conflicting_cmd, plugin_new_cmd]

    mock_cli = mock.MagicMock()
    mock_cli.commands = {
        "auth": click.Command("auth"),
        "config": click.Command("config"),
    }

    deferred_warnings: list[str] = []

    monkeypatch.setattr(main_module, "cli", mock_cli)
    monkeypatch.setattr(main_module, "_deferred_warnings", deferred_warnings)
    monkeypatch.setattr(main_module, "_load_plugins", lambda: mock_registry)

    main_module._register_plugin_commands()

    mock_cli.add_command.assert_called_once_with(plugin_new_cmd)
    assert any("conflicts with an existing command" in msg for msg in deferred_warnings)


def test_add_or_merge_adds_new_top_level_command() -> None:
    """A plugin command with a fresh name is added as-is."""
    import ggshield.__main__ as main_module

    root = click.Group("cli", commands={"auth": click.Command("auth")})
    plugin_cmd = click.Command("brand-new")
    warnings: list[str] = []

    main_module._add_or_merge_plugin_command(root, plugin_cmd, warnings)

    assert root.commands["brand-new"] is plugin_cmd
    assert warnings == []


def test_add_or_merge_merges_plugin_group_into_builtin_group() -> None:
    """A plugin group colliding with a built-in group merges its subcommands in."""
    import ggshield.__main__ as main_module

    builtin_machine = click.Group("machine", commands={"setup": click.Command("setup")})
    root = click.Group("cli", commands={"machine": builtin_machine})
    plugin_machine = click.Group(
        "machine",
        commands={
            "scan": click.Command("scan"),
            "inventory": click.Command("inventory"),
            "setup": click.Command("setup"),  # conflicts with built-in
        },
    )
    warnings: list[str] = []

    main_module._add_or_merge_plugin_command(root, plugin_machine, warnings)

    # Plugin subcommands are merged into the SAME built-in group object.
    assert root.commands["machine"] is builtin_machine
    assert "scan" in builtin_machine.commands
    assert "inventory" in builtin_machine.commands
    # The built-in `setup` wins; the plugin's conflicting one is skipped.
    assert builtin_machine.commands["setup"].name == "setup"
    assert any("machine setup" in msg for msg in warnings)


def test_add_or_merge_skips_non_group_conflict() -> None:
    """A plain command colliding with a built-in command is skipped, not merged."""
    import ggshield.__main__ as main_module

    root = click.Group("cli", commands={"auth": click.Command("auth")})
    plugin_cmd = click.Command("auth")
    warnings: list[str] = []

    main_module._add_or_merge_plugin_command(root, plugin_cmd, warnings)

    assert root.commands["auth"] is not plugin_cmd
    assert any("conflicts with an existing command" in msg for msg in warnings)


def test_warn_about_failed_plugins_emits_one_warning_per_failure(monkeypatch) -> None:
    import ggshield.__main__ as main_module
    from ggshield.core.plugin.registry import PluginRegistry

    registry = PluginRegistry()
    registry.record_load_failure("machine_scan", "error relocating libsatori.so")
    monkeypatch.setattr(main_module, "_load_plugins", lambda: registry)

    warnings: list[str] = []
    monkeypatch.setattr(main_module.ui, "display_warning", warnings.append)

    main_module._warn_about_failed_plugins()

    assert len(warnings) == 1
    assert "machine_scan" in warnings[0]
    assert "failed to load" in warnings[0].lower()


def test_warn_about_failed_plugins_silent_when_none(monkeypatch) -> None:
    import ggshield.__main__ as main_module
    from ggshield.core.plugin.registry import PluginRegistry

    monkeypatch.setattr(main_module, "_load_plugins", lambda: PluginRegistry())

    warnings: list[str] = []
    monkeypatch.setattr(main_module.ui, "display_warning", warnings.append)

    main_module._warn_about_failed_plugins()

    assert warnings == []


def test_main_warns_then_click_rejects_unknown_plugin_command(
    monkeypatch, capsys
) -> None:
    """End to end: the warning prints AND Click still rejects the missing command."""
    import ggshield.__main__ as main_module
    from ggshield.core.plugin.registry import PluginRegistry

    registry = PluginRegistry()
    registry.record_load_failure("machine_scan", "error relocating libsatori.so")
    monkeypatch.setattr(main_module, "_load_plugins", lambda: registry)
    monkeypatch.setattr(main_module, "_register_plugin_commands", lambda: None)
    monkeypatch.setattr(main_module, "force_utf8_output", lambda: None)
    monkeypatch.setattr(main_module, "setup_truststore", lambda: None)

    with pytest.raises(SystemExit):
        main_module.main(["machine", "scan"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "machine_scan" in combined
    assert "No such command" in combined
