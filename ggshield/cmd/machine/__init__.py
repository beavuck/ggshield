from typing import Any

import click

from ggshield.cmd.machine.setup import setup_cmd
from ggshield.cmd.utils.common_options import add_common_options


@click.group(commands={"setup": setup_cmd})
@add_common_options()
def machine_group(**kwargs: Any) -> None:
    """
    Scan and protect this machine.

    `setup` sets up all of this machine's protections (AI hooks, git hooks, and
    a honeytoken) in one idempotent command. Secret and endpoint scanning
    (`scan`) is provided by the `machine_scan` plugin and merges into this group
    when it is installed.
    """
