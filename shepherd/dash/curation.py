# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Curated per-CL state in cls.json, written by the agent.

  {"<cl>": {"bucket", "next", "note", "exp", "prio", "basis"}}

`basis` records the Gerrit facts the entry was written against. Once any of
them changes the entry is stale: the dashboard stops applying its bucket
and flags its text until the agent curates the CL again.
"""

import json
import os

BASIS_FIELDS = (
    "status",
    "work_in_progress",
    "current_revision_number",
    "unresolved_comment_count",
    "cr_votes",
    "last_cv",
)
FIELDS = ("bucket", "next", "note", "exp", "prio")


def basis(gerrit):
    return {k: gerrit.get(k) for k in BASIS_FIELDS}


def changed_fields(entry, gerrit):
    """The basis fields that differ from the CL's current state.

    Empty for an entry without a basis: it was never tied to a state.
    """
    old = entry.get("basis")
    if old is None:
        return []
    return [k for k in BASIS_FIELDS if old.get(k) != gerrit.get(k)]


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def curate(cls_path, state_path, cl, fields, clear=False):
    """Merges `fields` into the CL's entry and stamps it with the current
    Gerrit state. Returns the new entry."""
    state = read_json(state_path, {})
    if cl not in state:
        raise KeyError(f"{cl} is not in the watcher state")
    cls = read_json(cls_path, {})
    entry = {} if clear else cls.get(cl, {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    entry["basis"] = basis(state[cl])
    cls[cl] = entry
    write_json(cls_path, cls)
    return entry
