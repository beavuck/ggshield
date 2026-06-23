from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from pygitguardian.models import (
    AgentInfo,
    AIDiscovery,
    MCPConfiguration,
    MCPPromptInfo,
    MCPResourceInfo,
    MCPToolInfo,
    UserInfo,
)

from ggshield.verticals.ai.cache import (
    _server_has_capabilities_unknown_to,
    has_changed_from,
    load_discovery_cache,
    save_discovery_cache,
)
from ggshield.verticals.ai.models import MCPServer, Scope, Transport


def _user(**kwargs: Any) -> UserInfo:
    defaults = dict(
        hostname="host", username="user", machine_id="mid", user_email="u@e.com"
    )
    return UserInfo(**(defaults | kwargs))


def _server(
    name: str = "srv",
    tools: Optional[List[MCPToolInfo]] = None,
    resources: Optional[List[MCPResourceInfo]] = None,
    prompts: Optional[List[MCPPromptInfo]] = None,
    configurations: Optional[List[MCPConfiguration]] = None,
) -> MCPServer:
    return MCPServer(
        name=name,
        tools=tools or [],
        resources=resources or [],
        prompts=prompts or [],
        configurations=configurations or [],
    )


def _cfg(
    name: str = "srv",
    agent: str = "cursor",
    scope: Scope = Scope.USER,
    project: Optional[str] = None,
) -> MCPConfiguration:
    return MCPConfiguration(
        name=name,
        agent=agent,
        scope=scope,
        transport=Transport.STDIO,
        project=project,
    )


# ---------------------------------------------------------------------------
# MCPServer.has_capabilities_unknown_to
# ---------------------------------------------------------------------------


class TestMCPServerHasCapabilitiesUnknownTo:
    @pytest.mark.parametrize(
        "self_kwargs, other_kwargs, expected",
        [
            pytest.param(
                {"tools": [MCPToolInfo(name="t1")]},
                {"tools": [MCPToolInfo(name="t1")]},
                False,
                id="same_tools",
            ),
            pytest.param(
                {"tools": [MCPToolInfo(name="t1"), MCPToolInfo(name="t2")]},
                {"tools": [MCPToolInfo(name="t1")]},
                True,
                id="extra_tool",
            ),
            pytest.param(
                {"resources": [MCPResourceInfo(uri="r1"), MCPResourceInfo(uri="r2")]},
                {"resources": [MCPResourceInfo(uri="r1")]},
                True,
                id="extra_resource",
            ),
            pytest.param(
                {"prompts": [MCPPromptInfo(name="p1"), MCPPromptInfo(name="p2")]},
                {"prompts": [MCPPromptInfo(name="p1")]},
                True,
                id="extra_prompt",
            ),
            pytest.param(
                {"tools": [MCPToolInfo(name="t1")]},
                {"tools": [MCPToolInfo(name="t1"), MCPToolInfo(name="t2")]},
                False,
                id="subset_of_other",
            ),
            pytest.param({}, {}, False, id="both_empty"),
        ],
    )
    def test_has_capabilities_unknown_to(
        self, self_kwargs: Dict[str, Any], other_kwargs: Dict[str, Any], expected: bool
    ):
        assert (
            _server_has_capabilities_unknown_to(
                _server(**self_kwargs), _server(**other_kwargs)
            )
            is expected
        )


# ---------------------------------------------------------------------------
# AIDiscovery.has_changed_from
# ---------------------------------------------------------------------------


class TestAIDiscoveryHasChangedFrom:
    def test_identical_returns_false(self):
        cfg = _cfg()
        a = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            discovery_duration=0.2,
        )
        assert has_changed_from(a, b) is False

    def test_different_user_returns_true(self):
        cfg = _cfg()
        a = AIDiscovery(
            user=_user(hostname="a"),
            servers=[_server(configurations=[cfg])],
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(hostname="b"),
            servers=[_server(configurations=[cfg])],
            discovery_duration=0.1,
        )
        assert has_changed_from(a, b) is True

    def test_different_configuration_keys_returns_true(self):
        a = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[_cfg(name="x")])],
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[_cfg(name="y")])],
            discovery_duration=0.1,
        )
        assert has_changed_from(a, b) is True

    def test_new_capabilities_unknown_to_all_candidates_returns_true(self):
        cfg = _cfg()
        a = AIDiscovery(
            user=_user(),
            servers=[
                _server(
                    configurations=[cfg],
                    tools=[MCPToolInfo(name="new_tool")],
                )
            ],
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            discovery_duration=0.1,
        )
        assert has_changed_from(a, b) is True

    def test_capabilities_known_to_one_candidate_returns_false(self):
        cfg = _cfg()
        tool = MCPToolInfo(name="known")
        a = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg], tools=[tool])],
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg], tools=[tool])],
            discovery_duration=0.1,
        )
        assert has_changed_from(a, b) is False

    def test_empty_servers_returns_false(self):
        a = AIDiscovery(user=_user(), servers=[], discovery_duration=0.1)
        b = AIDiscovery(user=_user(), servers=[], discovery_duration=0.1)
        assert has_changed_from(a, b) is False

    def test_changed_hook_installation_returns_true(self):
        cfg = _cfg()
        a = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            agents=[AgentInfo(name="cursor", hooks_installed=True)],
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            agents=[AgentInfo(name="cursor", hooks_installed=False)],
            discovery_duration=0.1,
        )
        assert has_changed_from(a, b) is True

    def test_same_hook_installation_returns_false(self):
        cfg = _cfg()
        agents = [AgentInfo(name="cursor", hooks_installed=True)]
        a = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            agents=agents,
            discovery_duration=0.1,
        )
        b = AIDiscovery(
            user=_user(),
            servers=[_server(configurations=[cfg])],
            agents=list(agents),
            discovery_duration=0.2,
        )
        assert has_changed_from(a, b) is False


# ---------------------------------------------------------------------------
# load / save discovery cache
# ---------------------------------------------------------------------------


class TestLoadSaveDiscoveryCache:
    def test_round_trip(self, tmp_path: Path):
        discovery = AIDiscovery(user=_user(), servers=[], discovery_duration=0.1)
        with patch("ggshield.verticals.ai.cache.get_cache_dir", return_value=tmp_path):
            save_discovery_cache(discovery)
            loaded = load_discovery_cache()
        assert loaded is not None
        assert loaded.user == discovery.user

    def test_load_returns_none_when_missing(self, tmp_path: Path):
        with patch("ggshield.verticals.ai.cache.get_cache_dir", return_value=tmp_path):
            assert load_discovery_cache() is None

    def test_load_returns_none_on_valid_json_bad_schema(self, tmp_path: Path):
        """Valid JSON that doesn't match AIDiscovery schema."""
        cache_file = tmp_path / "ai_discovery.json"
        cache_file.write_text('{"foo": "bar"}')
        with patch("ggshield.verticals.ai.cache.get_cache_dir", return_value=tmp_path):
            assert load_discovery_cache() is None

    def test_load_returns_none_on_invalid_json(self, tmp_path: Path):
        """Valid JSON that doesn't match AIDiscovery schema."""
        cache_file = tmp_path / "ai_discovery.json"
        cache_file.write_text("not json")
        with patch("ggshield.verticals.ai.cache.get_cache_dir", return_value=tmp_path):
            assert load_discovery_cache() is None

    def test_load_returns_none_on_oserror(self, tmp_path: Path):
        with patch("ggshield.verticals.ai.cache.get_cache_dir", return_value=tmp_path):
            cache_file = tmp_path / "ai_discovery.json"
            cache_file.mkdir()  # directory instead of file triggers OSError
            assert load_discovery_cache() is None
