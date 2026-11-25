# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Agent-side access to the dashboard's data.

Talking to the user:
  shepherd dashctl request --cl N --kind KIND "summary" [--detail TEXT]
  shepherd dashctl ask [--cl N] "question" [--detail TEXT]
  shepherd dashctl note [--cl N] "text"
  shepherd dashctl wait [--timeout SECONDS]
  shepherd dashctl done ID "result"
  shepherd dashctl list [--status pending|approved|rejected|done]
  shepherd dashctl heartbeat "what the agent is doing"

Curating CLs:
  shepherd dashctl curate --cl N [--bucket B] [--next TEXT] [--note TEXT]
                          [--exp ID] [--prio N] [--clear]
  shepherd dashctl stale

`request` files a proposal the user approves or rejects in the dashboard,
`ask` a question the user answers; both print the new id. `note` posts a
status line that needs no decision. `wait` blocks until an approved request
or an answered question is open, prints the open ones as JSON (the user's
text is in `reply`) and exits 0; it exits 124 on timeout. While it runs the
dashboard shows the agent as listening. `done` closes an approved request
with a result line. `heartbeat` marks the agent as working.

`curate` merges the given fields into the CL's cls.json entry and stamps it
with the CL's current Gerrit state; the entry goes stale when that state
changes. With no fields it only re-stamps. `stale` lists the stale entries
with the fields that changed.

All output is JSON.
"""

import argparse
import json
import sys
import time

from . import config
from .dash import curation, requestlog, store

POLL_S = 2
# The dashboard shows a listening agent as gone after 90 s without a beat.
HEARTBEAT_S = 30


def beat(cfg, state, status=""):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    curation.write_json(
        cfg.agent, {"ts": requestlog.now(), "state": state, "status": status}
    )


def approved(cfg):
    """Approved requests and answered questions not yet closed."""
    return [
        r
        for r in requestlog.load(cfg.requests).requests.values()
        if r["status"] == "approved"
    ]


def out(value):
    print(json.dumps(value, indent=1, ensure_ascii=False))


def wait(cfg, timeout):
    deadline = timeout and time.monotonic() + timeout
    next_beat = 0
    while True:
        if time.monotonic() >= next_beat:
            beat(cfg, "listening")
            next_beat = time.monotonic() + HEARTBEAT_S
        items = approved(cfg)
        if items:
            out(items)
            return 0
        if deadline and time.monotonic() >= deadline:
            return 124
        time.sleep(POLL_S)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="shepherd dashctl",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("request")
    p.add_argument("--cl")
    p.add_argument("--kind", required=True, choices=requestlog.USER_KINDS)
    p.add_argument("--detail", default="")
    p.add_argument("summary")
    p = sub.add_parser("ask")
    p.add_argument("--cl")
    p.add_argument("--detail", default="")
    p.add_argument("question")
    p = sub.add_parser("note")
    p.add_argument("--cl")
    p.add_argument("text")
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float)
    p = sub.add_parser("done")
    p.add_argument("id")
    p.add_argument("result")
    p = sub.add_parser("list")
    p.add_argument("--status", choices=("pending", "approved", "rejected", "done"))
    p = sub.add_parser("heartbeat")
    p.add_argument("status")
    p = sub.add_parser("curate")
    p.add_argument("--cl", required=True)
    p.add_argument("--bucket", choices=sorted(store.CURATED_BUCKETS))
    p.add_argument("--next")
    p.add_argument("--note")
    p.add_argument("--exp")
    p.add_argument("--prio", type=int)
    p.add_argument(
        "--clear", action="store_true", help="drop the entry's existing fields first"
    )
    sub.add_parser("stale")
    args = ap.parse_args(argv)
    cfg = config.load()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    log = cfg.requests
    try:
        if args.cmd == "request":
            out(
                {
                    "id": requestlog.new_request(
                        log, args.cl, args.kind, args.summary, args.detail
                    )
                }
            )
        elif args.cmd == "ask":
            out(
                {
                    "id": requestlog.new_request(
                        log, args.cl, "question", args.question, args.detail
                    )
                }
            )
        elif args.cmd == "note":
            out({"id": requestlog.new_note(log, args.cl, args.text)})
        elif args.cmd == "done":
            requestlog.done(log, args.id, args.result)
            out({"ok": True})
        elif args.cmd == "list":
            out(
                [
                    r
                    for r in requestlog.load(log).requests.values()
                    if not args.status or r["status"] == args.status
                ]
            )
        elif args.cmd == "heartbeat":
            beat(cfg, "working", args.status)
            out({"ok": True})
        elif args.cmd == "curate":
            fields = {k: getattr(args, k) for k in curation.FIELDS}
            out(curation.curate(cfg.cls, cfg.state, args.cl, fields, args.clear))
        elif args.cmd == "stale":
            state = curation.read_json(cfg.state, {})
            cls = curation.read_json(cfg.cls, {})
            out(
                [
                    {"cl": cl, "changed": changed, "entry": entry}
                    for cl, entry in cls.items()
                    if cl in state
                    and state[cl]["status"] == "NEW"
                    and (changed := curation.changed_fields(entry, state[cl]))
                ]
            )
        elif args.cmd == "wait":
            return wait(cfg, args.timeout)
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
