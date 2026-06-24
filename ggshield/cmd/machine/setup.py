from typing import Any, Tuple

import click

from ggshield.cmd.honeytoken.plant import plant_cmd
from ggshield.cmd.install import (
    get_default_global_hook_dir_path,
    get_global_hook_dir_path,
    install_global,
)
from ggshield.cmd.utils.common_options import add_common_options
from ggshield.cmd.utils.context_obj import ContextObj
from ggshield.core import ui
from ggshield.verticals.ai.agents import AGENTS
from ggshield.verticals.ai.installation import (
    check_ai_hook_authentication,
    install_all_agent_hooks,
)


_GIT_HOOK_TYPES = ("pre-commit", "pre-push")


@click.command()
@click.option(
    "--no-ai-hooks",
    is_flag=True,
    help="Do not configure the AI assistant hooks.",
)
@click.option(
    "--no-git-hooks",
    is_flag=True,
    help="Do not install the global git hooks.",
)
@click.option(
    "--no-honeytokens",
    is_flag=True,
    help="Do not plant a honeytoken on this machine.",
)
@click.option(
    "--agent",
    "agents",
    type=click.Choice(sorted(AGENTS.keys())),
    multiple=True,
    metavar="ASSISTANT",
    help="Only configure the AI hook for these assistants (repeatable). "
    "Defaults to every assistant detected on this machine.",
)
@click.option(
    "--exclude-agent",
    "exclude_agents",
    type=click.Choice(sorted(AGENTS.keys())),
    multiple=True,
    metavar="ASSISTANT",
    help="Skip these assistants when configuring the AI hook (repeatable).",
)
@add_common_options()
@click.pass_context
def setup_cmd(
    ctx: click.Context,
    no_ai_hooks: bool,
    no_git_hooks: bool,
    no_honeytokens: bool,
    agents: Tuple[str, ...],
    exclude_agents: Tuple[str, ...],
    **kwargs: Any,
) -> int:
    """
    Set up ggshield protection on this machine.

    Configures every protection in one idempotent run: the ggshield AI hook for
    each detected AI coding assistant, the global git pre-commit/pre-push hooks,
    and a honeytoken to detect endpoint intrusion. Safe to re-run — it adds what
    is missing and leaves existing entries untouched.

    Each protection is on by default; drop one with `--no-ai-hooks`,
    `--no-git-hooks`, or `--no-honeytokens`. `--agent` / `--exclude-agent`
    narrow which assistants get the AI hook.
    """
    if agents and exclude_agents:
        raise click.UsageError("--agent and --exclude-agent cannot be used together.")

    failed = False

    if not no_ai_hooks:
        if not _setup_ai_hooks(ctx, agents, exclude_agents):
            failed = True

    if not no_git_hooks:
        if not _setup_git_hooks():
            failed = True

    if not no_honeytokens:
        if not _setup_honeytokens(ctx):
            failed = True

    return 1 if failed else 0


def _setup_ai_hooks(
    ctx: click.Context,
    agents: Tuple[str, ...],
    exclude_agents: Tuple[str, ...],
) -> bool:
    """Configure the AI hook for the selected assistants. Returns False on failure.

    Additive/idempotent: adds a ggshield hook entry where one is missing and
    leaves any existing entry untouched.
    """
    click.echo(click.style("AI hooks", bold=True))
    summary = install_all_agent_hooks(only=agents, exclude=exclude_agents)
    # Run the auth preflight once, and only if we actually configured something.
    if summary.configured and not summary.failed:
        check_ai_hook_authentication(ContextObj.get(ctx).config)
    return summary.failed == 0


def _setup_git_hooks() -> bool:
    """Install the global git pre-commit/pre-push hooks, idempotently.

    A hook already wired to ggshield is left as-is; a foreign hook is never
    overwritten. Returns False if any hook failed to install.
    """
    click.echo(click.style("Git hooks", bold=True))
    hook_dir = get_global_hook_dir_path() or get_default_global_hook_dir_path()
    ok = True
    for hook_type in _GIT_HOOK_TYPES:
        hook_path = hook_dir / hook_type
        if hook_path.is_file():
            if "ggshield secret scan" in hook_path.read_text(errors="ignore"):
                click.echo(f"  global {hook_type} hook already configured")
            else:
                ui.display_warning(
                    f"  a non-ggshield global {hook_type} hook is present; "
                    "left untouched"
                )
            continue
        try:
            install_global(hook_type=hook_type, force=False, append=False)
        except Exception as exc:  # one hook failure must not abort the whole setup
            ui.display_warning(f"  could not install global {hook_type} hook: {exc}")
            ok = False
    return ok


def _setup_honeytokens(ctx: click.Context) -> bool:
    """Plant a honeytoken on this machine (idempotent reconcile via `plant`)."""
    click.echo(click.style("Honeytoken", bold=True))
    return ctx.invoke(plant_cmd) == 0
