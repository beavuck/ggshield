from dataclasses import dataclass
from importlib import import_module
from typing import Any, List, Optional

import click
from pygitguardian.models import APITokensResponse, HealthCheckResponse

from ggshield.cmd.install import (
    get_default_global_hook_dir_path,
    get_default_system_hook_dir_path,
    get_global_hook_dir_path,
    get_system_hook_dir_path,
)
from ggshield.cmd.utils.common_options import add_common_options
from ggshield.cmd.utils.context_obj import ContextObj
from ggshield.core.client import create_client_from_config, safe_api_tokens
from ggshield.core.plugin.hooks import get_plugin_registry
from ggshield.verticals.ai.installation import ai_hook_posture


PLUGIN_NAME = "machine_scan"
# The native scanner module shipped in the machine_scan plugin wheel. Importing it
# loads the compiled (Rust) extension, which is what we want to prove.
_PLUGIN_NATIVE_MODULE = "satori_python"
_GIT_HOOK_TYPES = ("pre-commit", "pre-push")

# Scopes the configured protections need.
_SCAN_SCOPE = "scan"  # the AI and git hooks run `ggshield secret scan`
_HONEYTOKEN_SCOPE = "honeytokens:write"  # plant honeytokens (granted only to Business)
_ENDPOINT_SCOPE = "endpoints:send"  # the machine_scan plugin uploads endpoint data


@dataclass
class Check:
    """One readiness check and its outcome.

    ``fix`` is the remediation shown when the check fails — it must be specific to
    the check (hooks are fixed by `machine setup`, but scopes are fixed by getting a
    token with the right scopes, not by re-running setup).
    """

    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


@click.command(name="healthcheck")
@add_common_options()
@click.pass_context
def healthcheck_cmd(ctx: click.Context, **kwargs: Any) -> int:
    """
    Check that this machine's ggshield protections are correctly set up.

    Verifies the AI hooks and git hooks are installed, that the GitGuardian token
    is reachable and carries the scopes the configured protections need (including
    `honeytokens:write`, granted only on Business or Enterprise plans), and — when
    the `machine_scan` plugin is installed — that the token has the endpoint scope
    (also Business/Enterprise-only) and the native scanner loads.

    This is read-only: it never installs, scans, or changes anything. Each failed
    check prints how to fix it (hooks via `ggshield machine setup`, scopes via a
    token that carries them, the plugin via `ggshield plugin install`). Exits
    non-zero if any check fails, so it can gate an MDM rollout.
    """
    config = ContextObj.get(ctx).config

    scopes, auth_check = _check_auth_and_scopes(config)
    plugin_installed = _is_plugin_installed()

    checks: List[Check] = [auth_check]
    checks.append(_check_ai_hooks())
    checks.append(_check_git_hooks())
    checks.extend(_check_scopes(scopes, plugin_installed))
    if plugin_installed:
        checks.append(_check_plugin_native())

    _render(checks)
    return 0 if all(check.ok for check in checks) else 1


def _check_auth_and_scopes(config: Any) -> "tuple[Optional[List[str]], Check]":
    """Verify the token reaches GitGuardian and read its scopes (one set of calls)."""
    login = "authenticate with `ggshield auth login`"
    try:
        client = create_client_from_config(config)
        health = client.health_check()
    except (
        Exception
    ) as exc:  # noqa: BLE001 - report any auth/network failure as a check
        return None, Check("Authentication", False, str(exc), fix=login)

    if not isinstance(health, HealthCheckResponse) or health.status_code != 200:
        detail = getattr(health, "detail", "unhealthy")
        return None, Check("Authentication", False, str(detail), fix=login)

    scopes: Optional[List[str]] = None
    token = safe_api_tokens(client)
    if isinstance(token, APITokensResponse):
        scopes = token.scopes
    return scopes, Check("Authentication", True, "token reaches GitGuardian")


def _check_ai_hooks() -> Check:
    statuses = ai_hook_posture()
    if not statuses:
        return Check("AI hooks", True, "no AI coding assistants detected")
    missing = [status.display_name for status in statuses if not status.installed]
    if missing:
        return Check(
            "AI hooks",
            False,
            f"not installed for: {', '.join(missing)}",
            fix="run `ggshield machine setup`",
        )
    installed = ", ".join(status.display_name for status in statuses)
    return Check("AI hooks", True, f"installed for: {installed}")


def _check_git_hooks() -> Check:
    """The hooks are OK if pre-commit and pre-push are wired to ggshield in the global
    or system scope (a machine may use either, e.g. MDM installs system-wide)."""
    hook_dirs = [
        get_global_hook_dir_path() or get_default_global_hook_dir_path(),
        get_system_hook_dir_path() or get_default_system_hook_dir_path(),
    ]
    missing = [
        hook_type
        for hook_type in _GIT_HOOK_TYPES
        if not _ggshield_hook_present(hook_dirs, hook_type)
    ]
    if missing:
        return Check(
            "Git hooks",
            False,
            f"not configured: {', '.join(missing)}",
            fix="run `ggshield machine setup` (use `--system` / run as root for all users)",
        )
    return Check("Git hooks", True, "global/system pre-commit and pre-push configured")


def _ggshield_hook_present(hook_dirs: List[Any], hook_type: str) -> bool:
    for hook_dir in hook_dirs:
        hook_path = hook_dir / hook_type
        if hook_path.is_file() and "ggshield secret scan" in hook_path.read_text(
            errors="ignore"
        ):
            return True
    return False


def _check_scopes(scopes: Optional[List[str]], plugin_installed: bool) -> List[Check]:
    if scopes is None:
        return [
            Check(
                "Token scopes",
                False,
                "could not read the token's scopes",
                fix="check the token is valid (`ggshield api-status`)",
            )
        ]

    # Scopes are a property of the token, not of `machine setup` — fixing them means
    # using a token that carries them (re-auth or a service-account token), not
    # re-running setup. honeytokens:write and endpoints:send are additionally gated
    # server-side on a paid plan (Business or Enterprise).
    def _scope_fix(scope: str, *, paid_plan: bool = False) -> str:
        account = " from a Business or Enterprise account" if paid_plan else ""
        return (
            f"use a token{account} that has the `{scope}` scope "
            f"(re-run `ggshield auth login` or create a service-account token)"
        )

    checks = [
        Check(
            f"Scope `{_SCAN_SCOPE}`",
            _SCAN_SCOPE in scopes,
            "required by the AI and git hooks",
            fix=_scope_fix(_SCAN_SCOPE),
        ),
        Check(
            f"Scope `{_HONEYTOKEN_SCOPE}`",
            _HONEYTOKEN_SCOPE in scopes,
            "honeytoken protection — Business or Enterprise plans only",
            fix=_scope_fix(_HONEYTOKEN_SCOPE, paid_plan=True),
        ),
    ]
    if plugin_installed:
        checks.append(
            Check(
                f"Scope `{_ENDPOINT_SCOPE}`",
                _ENDPOINT_SCOPE in scopes,
                "machine_scan plugin — Business or Enterprise plans only",
                fix=_scope_fix(_ENDPOINT_SCOPE, paid_plan=True),
            )
        )
    return checks


def _is_plugin_installed() -> bool:
    registry = get_plugin_registry()
    return registry is not None and registry.get_plugin(PLUGIN_NAME) is not None


def _check_plugin_native() -> Check:
    """Prove the plugin's native scanner loads on this machine (signed, arch-specific
    wheel + compiled extension), without running a scan."""
    registry = get_plugin_registry()
    plugin = registry.get_plugin(PLUGIN_NAME) if registry else None
    version = ""
    if plugin is not None:
        try:
            version = f" v{plugin.metadata.version}"
        except Exception:  # noqa: BLE001 - version is cosmetic
            version = ""
    try:
        native = import_module(_PLUGIN_NATIVE_MODULE)
        native.Scanner(use_embedded=True)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - any load failure means the plugin is unusable
        return Check(
            "Plugin (machine_scan)",
            False,
            f"installed{version} but the native scanner failed to load: {exc}",
            fix="reinstall it with `ggshield plugin install machine_scan`",
        )
    return Check("Plugin (machine_scan)", True, f"native scanner loads{version}")


def _render(checks: List[Check]) -> None:
    for check in checks:
        mark = click.style("✓", fg="green") if check.ok else click.style("✗", fg="red")
        detail = f" — {check.detail}" if check.detail else ""
        click.echo(f"  {mark} {check.name}{detail}")
        if not check.ok and check.fix:
            click.echo(f"      {click.style('↳ fix:', fg='yellow')} {check.fix}")
    failed = [check for check in checks if not check.ok]
    click.echo()
    if failed:
        click.echo(
            click.style(
                f"{len(failed)} check(s) failed — see the suggested fixes above.",
                fg="red",
                bold=True,
            )
        )
    else:
        click.echo(
            click.style("This machine is correctly set up.", fg="green", bold=True)
        )
