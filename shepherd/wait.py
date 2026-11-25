# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Block until there is something for the shepherding agent to handle.

Polls Gerrit like `shepherd watch` and the dashboard queue like `shepherd
dashctl wait`, in one process. Exits 0 on the first Gerrit events or
approved/answered dashboard items and prints them as one JSON object:

  {"gerrit": ["event line", ...], "dash": [request, ...]}

Exits 124 after --max-wait seconds without anything to report, 2 if another
watcher or waiter holds the lock. Gerrit events go to cl-watch.log as well;
while waiting, the dashboard shows the agent as listening.
"""

import argparse
import json
import sys
import time

from . import config, dashctl, watch


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="shepherd wait",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--interval",
        type=int,
        help="seconds between Gerrit polls (config: watch.interval)",
    )
    ap.add_argument(
        "--max-wait",
        type=float,
        help="idle seconds before exiting 124 (config: watch.max_wait)",
    )
    ap.add_argument("--cl", type=int, action="append", default=[])
    args = ap.parse_args(argv)
    cfg = config.load()
    cfg.require_owner()
    interval = args.interval or cfg.interval
    max_wait = args.max_wait if args.max_wait is not None else cfg.max_wait

    lock_file = watch.lock(cfg)
    if lock_file is None:
        return 2

    deadline = time.monotonic() + max_wait
    next_poll = next_beat = 0
    while True:
        now = time.monotonic()
        if now >= next_beat:
            dashctl.beat(cfg, "listening")
            next_beat = now + dashctl.HEARTBEAT_S

        events = []
        if now >= next_poll:
            next_poll = now + interval
            try:
                events, _, missing = watch.poll(cfg, args.cl)
            except (OSError, ValueError) as e:
                msg = f"poll failed: {e!r}"
                print(msg, file=sys.stderr)
                watch.log(cfg.log, [msg])
            else:
                if missing:
                    print(
                        f"not found on Gerrit: {' '.join(map(str, missing))}",
                        file=sys.stderr,
                    )
                if events:
                    watch.log(cfg.log, events)

        items = dashctl.approved(cfg)
        if events or items:
            print(
                json.dumps(
                    {"gerrit": events, "dash": items}, indent=1, ensure_ascii=False
                )
            )
            return 0
        if time.monotonic() >= deadline:
            return 124
        time.sleep(dashctl.POLL_S)
