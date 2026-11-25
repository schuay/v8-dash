# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Read comment threads and write draft replies on Gerrit. Never publishes.

  shepherd drafts threads CL [--unresolved]
      comment threads of a CL as JSON, with the user's drafts included
      when credentials are available
  shepherd drafts list CL...
      the user's unpublished drafts per CL as JSON; drafts don't bump a
      CL's `updated` time, so the watcher can't see them
  shepherd drafts create CL [--patchset PS] < comments.json
      create drafts from a JSON list of
        {"message", "in_reply_to"?, "path"?, "line"?, "side"?,
         "unresolved"?, "is_ai"?}
      A reply that names in_reply_to and no location of its own is filed at
      its parent's path, line, side and patchset, so Gerrit shows it in the
      parent's thread. Drafts are marked is_ai unless "is_ai": false.

Published comments are read anonymously; drafts use the user's existing
Gerrit credentials (see gerrit.py), read at run time and never stored.
"""

import argparse
import json
import sys

from . import config, gerrit

PATCHSET_LEVEL = "/PATCHSET_LEVEL"


def _flatten(by_path, draft):
    out = []
    for path, comments in by_path.items():
        for c in comments:
            out.append(
                {
                    "id": c["id"],
                    "path": path,
                    "line": c.get("line"),
                    "side": c.get("side", "REVISION"),
                    "patch_set": c.get("patch_set"),
                    "in_reply_to": c.get("in_reply_to"),
                    "author": (c.get("author") or {}).get("email", "me"),
                    "message": c.get("message", ""),
                    "updated": c.get("updated", ""),
                    "unresolved": c.get("unresolved", False),
                    "draft": draft,
                }
            )
    return out


def published(cfg, cl):
    return _flatten(gerrit.get(f"{cfg.gerrit}/changes/{cl}/comments"), False)


def drafts(cfg, cl):
    return _flatten(gerrit.authed(cfg, "GET", f"changes/{cl}/drafts"), True)


def threads(comments):
    """Groups comments into threads by following in_reply_to to the root."""
    by_id = {c["id"]: c for c in comments}

    def root(c):
        seen = set()
        while c.get("in_reply_to") in by_id and c["id"] not in seen:
            seen.add(c["id"])
            c = by_id[c["in_reply_to"]]
        return c["id"]

    groups = {}
    for c in sorted(comments, key=lambda c: c["updated"]):
        groups.setdefault(root(c), []).append(c)
    out = []
    for rid, cs in groups.items():
        head = by_id[rid]
        published_last = [c for c in cs if not c["draft"]]
        out.append(
            {
                "path": head["path"],
                "line": head["line"],
                "patch_set": head["patch_set"],
                "unresolved": (published_last or cs)[-1]["unresolved"],
                "comments": cs,
            }
        )
    out.sort(key=lambda t: (t["path"], t["line"] or 0))
    return out


def comment_input(c, parent):
    """A Gerrit CommentInput for one requested draft."""
    src = parent or c
    path = src.get("path") or PATCHSET_LEVEL
    if path.strip("/").upper() == "PATCHSET_LEVEL":
        path = PATCHSET_LEVEL
    body = {
        "path": path,
        "message": c["message"],
        "unresolved": c.get("unresolved", True),
    }
    # Gerrit rejects side and line on a patchset-level comment.
    if path != PATCHSET_LEVEL:
        body["side"] = src.get("side") or "REVISION"
        if src.get("line") is not None:
            body["line"] = src["line"]
    if c.get("in_reply_to"):
        body["in_reply_to"] = c["in_reply_to"]
    if c.get("is_ai", True):
        body["is_ai"] = True
    return body


def create(cfg, cl, comments, patchset="current"):
    """Creates one draft per entry; returns one result per entry, in order."""
    parents = None
    results = []
    for c in comments:
        if not isinstance(c, dict) or not c.get("message"):
            results.append({"ok": False, "error": "missing message", "input": c})
            continue
        parent = None
        if c.get("in_reply_to") and all(c.get(k) is None for k in ("path", "line")):
            if parents is None:
                parents = {p["id"]: p for p in published(cfg, cl)}
            parent = parents.get(c["in_reply_to"])
        rev = parent["patch_set"] if parent and parent.get("patch_set") else patchset
        try:
            info = gerrit.authed(
                cfg,
                "PUT",
                f"changes/{cl}/revisions/{rev}/drafts",
                comment_input(c, parent),
            )
        except RuntimeError as e:
            results.append({"ok": False, "error": str(e), "input": c})
            continue
        results.append(
            {
                "ok": True,
                "id": info.get("id"),
                "path": info.get("path"),
                "line": info.get("line"),
                "patch_set": info.get("patch_set"),
                "in_reply_to": info.get("in_reply_to"),
            }
        )
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="shepherd drafts",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("threads")
    p.add_argument("cl", type=int)
    p.add_argument("--unresolved", action="store_true")
    p = sub.add_parser("list")
    p.add_argument("cls", type=int, nargs="+")
    p = sub.add_parser("create")
    p.add_argument("cl", type=int)
    p.add_argument("--patchset", default="current")
    args = ap.parse_args(argv)
    cfg = config.load()

    try:
        if args.cmd == "threads":
            comments = published(cfg, args.cl)
            try:
                comments += drafts(cfg, args.cl)
            except gerrit.AuthError as e:
                print(f"drafts not included: {e}", file=sys.stderr)
            out = threads(comments)
            if args.unresolved:
                out = [t for t in out if t["unresolved"]]
        elif args.cmd == "list":
            out = {str(cl): drafts(cfg, cl) for cl in args.cls}
        else:
            try:
                comments = json.load(sys.stdin)
            except ValueError as e:
                print(f"error: stdin is not JSON: {e}", file=sys.stderr)
                return 1
            if not isinstance(comments, list):
                comments = [comments]
            out = create(cfg, args.cl, comments, args.patchset)
    except gerrit.AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if args.cmd == "create" and not all(r["ok"] for r in out):
        return 1
    return 0
