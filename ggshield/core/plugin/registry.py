"""
Plugin registry - central registry for all loaded plugins and their capabilities.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import click

from ggshield.core.plugin.base import GGShieldPlugin


@dataclass
class PluginRegistry:
    """Central registry for all loaded plugins and their capabilities."""

    _plugins: Dict[str, GGShieldPlugin] = field(default_factory=dict)
    _commands: List[click.Command] = field(default_factory=list)
    _load_failures: Dict[str, str] = field(default_factory=dict)

    def register_plugin(self, plugin: GGShieldPlugin) -> None:
        """Register a loaded plugin."""
        self._plugins[plugin.metadata.name] = plugin

    def record_load_failure(self, name: str, reason: str) -> None:
        """Record that an enabled plugin failed to load (commands unavailable)."""
        self._load_failures[name] = reason

    def get_load_failures(self) -> Dict[str, str]:
        """Map of enabled-but-failed plugin name -> failure reason."""
        return self._load_failures.copy()

    def register_command(self, command: click.Command) -> None:
        """Register a CLI command provided by a plugin."""
        self._commands.append(command)

    def get_plugin(self, name: str) -> Optional[GGShieldPlugin]:
        """Get a loaded plugin by name."""
        return self._plugins.get(name)

    def get_all_plugins(self) -> Dict[str, GGShieldPlugin]:
        """Get all loaded plugins."""
        return self._plugins.copy()

    def get_commands(self) -> List[click.Command]:
        """Get all plugin-provided commands."""
        return self._commands.copy()
