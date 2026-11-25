# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Append-only log shared by the dashboard and the agents.

One JSON object per line:

  {"type": "request", "id", "cl", "kind", "summary", "detail", "by", "ts"}
  {"type": "decision", "id", "decision": "approved" | "rejected", "reply", "ts"}
  {"type": "done", "id", "result", "ts"}
  {"type": "note", "id", "cl", "text", "ts"}
  {"type": "dismiss", "id", "ts"}

A request from the agent starts as pending and needs the user's decision,
which may carry a reply. Kind "question" is a request whose decision is the
answer: it is always approved and the reply is required. Requests the user
files from the dashboard are approved on creation. The agent closes an
approved request with a done record. A note is a status line from the agent
that needs no decision; the user can dismiss it.

Writers only ever append a single line, so the dashboard and the agents
need no lock.
"""

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

USER_KINDS = ("subagent", "ping", "send", "pinpoint", "abandon", "other")
KINDS = (*USER_KINDS, "question")
DECISIONS = ("approved", "rejected")


@dataclass
class Log:
    requests: dict = field(default_factory=dict)  # by id, in creation order
    notes: list = field(default_factory=list)


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def append(path, record):
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def new_request(path, cl, kind, summary, detail="", by="agent"):
    if by not in ("agent", "user"):
        raise ValueError(f"unknown author {by!r}")
    if kind not in (USER_KINDS if by == "user" else KINDS):
        raise ValueError(f"unknown kind {kind!r}")
    rid = "r" + secrets.token_hex(4)
    ts = now()
    append(
        path,
        {
            "type": "request",
            "id": rid,
            "cl": str(cl) if cl else None,
            "kind": kind,
            "summary": summary,
            "detail": detail,
            "by": by,
            "ts": ts,
        },
    )
    if by == "user":
        append(
            path,
            {
                "type": "decision",
                "id": rid,
                "decision": "approved",
                "reply": "",
                "ts": ts,
            },
        )
    return rid


def decide(path, rid, decision, reply=""):
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")
    req = load(path).requests.get(rid)
    if req is None:
        raise KeyError(rid)
    if req["status"] != "pending":
        raise ValueError(f"{rid} is already {req['status']}")
    if req["kind"] == "question" and (decision != "approved" or not reply):
        raise ValueError("a question needs an answer")
    append(
        path,
        {
            "type": "decision",
            "id": rid,
            "decision": decision,
            "reply": reply,
            "ts": now(),
        },
    )


def done(path, rid, result):
    req = load(path).requests.get(rid)
    if req is None:
        raise KeyError(rid)
    if req["status"] != "approved":
        raise ValueError(f"{rid} is {req['status']}, not approved")
    append(path, {"type": "done", "id": rid, "result": result, "ts": now()})


def new_note(path, cl, text):
    nid = "n" + secrets.token_hex(4)
    append(
        path,
        {
            "type": "note",
            "id": nid,
            "cl": str(cl) if cl else None,
            "text": text,
            "ts": now(),
        },
    )
    return nid


def dismiss(path, nid):
    if not any(n["id"] == nid for n in load(path).notes):
        raise KeyError(nid)
    append(path, {"type": "dismiss", "id": nid, "ts": now()})


def load(path):
    """Folds the log into its current requests and notes."""
    log = Log()
    notes = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return log
    for line in lines:
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec["type"]
        if kind == "request":
            log.requests[rec["id"]] = {
                "id": rec["id"],
                "cl": rec.get("cl"),
                "kind": rec["kind"],
                "summary": rec["summary"],
                "detail": rec.get("detail", ""),
                "by": rec.get("by", "agent"),
                "ts": rec["ts"],
                "status": "pending",
                "reply": "",
                "decided_ts": None,
                "result": None,
                "done_ts": None,
            }
        elif kind == "note":
            notes[rec["id"]] = {
                "id": rec["id"],
                "cl": rec.get("cl"),
                "text": rec["text"],
                "ts": rec["ts"],
                "dismissed": False,
            }
        elif kind == "dismiss":
            if rec["id"] in notes:
                notes[rec["id"]]["dismissed"] = True
        elif kind == "decision":
            req = log.requests.get(rec["id"])
            if req and req["status"] == "pending":
                req["status"] = rec["decision"]
                req["reply"] = rec.get("reply", "")
                req["decided_ts"] = rec["ts"]
        elif kind == "done":
            req = log.requests.get(rec["id"])
            if req and req["status"] == "approved":
                req["status"] = "done"
                req["result"] = rec.get("result", "")
                req["done_ts"] = rec["ts"]
    log.notes = list(notes.values())
    return log
