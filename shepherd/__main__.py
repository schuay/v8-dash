# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""shepherd: watch Gerrit CLs and coordinate a coding agent through a
local dashboard.

  shepherd [--config FILE] COMMAND [ARGS]

Commands:
  watch     poll Gerrit, print and log events
  wait      block until Gerrit events or dashboard items arrive
  cq        summarize failed CQ / dry-run builds
  dashctl   agent side of the dashboard: requests, notes, curation
  dash      serve the dashboard
  drafts    read comment threads, write draft replies (never publishes)

`shepherd COMMAND --help` describes each command.
"""

import importlib
import os
import sys

from . import config

COMMANDS = ("watch", "wait", "cq", "dashctl", "dash", "drafts")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "--config":
        os.environ["SHEPHERD_CONFIG"] = argv[1]
        argv = argv[2:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        print(f"unknown command {cmd!r}; one of {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    module = importlib.import_module(
        ".dash.server" if cmd == "dash" else f".{cmd}", __package__
    )
    try:
        return module.main(rest)
    except config.ConfigError as e:
        print(f"config: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
