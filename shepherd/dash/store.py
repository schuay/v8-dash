# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Loads the dashboard's data sources and joins them into one model.

Sources, each with a single writer except the append-only request log:

  <watch_dir>/cl-watch-state.json  Gerrit facts, written by the watcher
  <watch_dir>/cl-watch.log         watcher event log
  <data_dir>/experiments.json      optional experiment table (extras/report)
  <data_dir>/pinpoint.json         optional Pinpoint batches (extras/pinpoint)
  <data_dir>/cls.json              curated per-CL state (curation.py)
  <data_dir>/requests.jsonl        requests and notes (requestlog.py)
  <data_dir>/agent.json            agent heartbeat (dashctl)
"""

from datetime import UTC, datetime

from . import curation, requestlog

PINPOINT_JOB = "https://pinpoint-dot-chromeperf.appspot.com/job/"
LOG_TAIL = 15

# (key, title, hint, collapsed by default)
BUCKETS = [
    ("todo", "Your move", "open threads or a failed run", False),
    ("ping", "Ping reviewer", "green, but the reviewer has gone quiet", False),
    ("send", "Ready to send", "WIP with a green dry run", False),
    ("approved", "Approved", "all submit requirements satisfied", False),
    ("review", "Waiting on reviewers", "nothing to do", False),
    ("decide", "Keep or abandon", "needs a decision", False),
    ("untracked", "Untracked", "found on Gerrit, not curated yet", False),
    ("wip", "Work in progress", "not ready to send", True),
    ("landed", "Landed", "", True),
    ("abandoned", "Abandoned", "", True),
]
# Buckets cls.json may assign. Landed and abandoned always follow Gerrit.
CURATED_BUCKETS = {"todo", "ping", "send", "approved", "review", "decide", "wip"}


def sources(cfg):
    return (
        cfg.state,
        cfg.log,
        cfg.experiments,
        cfg.pinpoint,
        cfg.cls,
        cfg.requests,
        cfg.agent,
    )


def version(cfg):
    """Changes whenever any source file does."""
    parts = []
    for p in sources(cfg):
        try:
            st = p.stat()
            parts.append(f"{st.st_mtime_ns}.{st.st_size}")
        except FileNotFoundError:
            parts.append("-")
    return "_".join(parts)


def _mtime(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except FileNotFoundError:
        return None


def _gerrit_time(s):
    # Gerrit timestamps are UTC, "2026-11-24 14:39:43.000000000".
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _cv(g):
    """The watcher's last_cv field, "<mode> <verdict> PS<n>", as a dict."""
    text = g.get("last_cv")
    if not text:
        return None
    mode, verdict, ps = text.split()
    ps = int(ps.removeprefix("PS"))
    return {
        "mode": mode,
        "verdict": verdict,
        "ps": ps,
        "current": ps == g.get("current_revision_number"),
    }


def _derived_bucket(g, cv):
    if g["status"] == "MERGED":
        return "landed"
    if g["status"] == "ABANDONED":
        return "abandoned"
    green = cv and cv["current"] and cv["verdict"] == "PASSED"
    red = cv and cv["current"] and cv["verdict"] == "FAILED"
    if g["work_in_progress"]:
        return "send" if green else "wip"
    if g["unresolved_comment_count"] or red:
        return "todo"
    # States written by an older watcher carry no submit requirements.
    if any(v > 0 for v in g["cr_votes"].values()) and not g.get("unsatisfied"):
        return "approved"
    return "review"


def _votes(votes):
    return [
        {"who": who, "value": f"{v:+d}", "ok": v > 0}
        for who, v in sorted(votes.items())
    ]


def _pinpoint(batch, current_ps):
    """Summary of a CL's latest Pinpoint batch for the table column."""
    if batch is None:
        return None
    jobs = sorted(batch["jobs"], key=lambda j: j["bot"])
    totals = [j["total"] for j in jobs if j["total"] is not None]
    return {
        "jobs": jobs,
        "mean": sum(totals) / len(totals) if totals else None,
        "ps": batch["ps"],
        "ps_current": batch["ps"] == current_ps if batch["ps"] else None,
        "base_patch": batch["base_patch"],
        "created": batch["created"],
        "newer": batch["newer"],
    }


def _priority(bucket, r):
    """Sort key within a bucket; the row to act on next sorts first.

    A curated `prio` (lower first) wins, then rows with an open request,
    then the bucket's own order.
    """
    age = -r["updated"].timestamp()  # most recently updated first
    perf = -(
        r["pp"]["mean"] if r["pp"] and r["pp"]["mean"] is not None else float("-inf")
    )  # best measured total first, unmeasured last
    red = not (r["cv"] and r["cv"]["current"] and r["cv"]["verdict"] == "FAILED")
    if bucket == "todo":
        key = (red, -r["unresolved"], age)
    elif bucket in ("ping", "review", "decide"):
        key = (-age,)  # longest quiet first
    elif bucket in ("send", "approved", "wip"):
        key = (perf, age)
    else:
        key = (age,)
    return (
        r["prio"] if r["prio"] is not None else float("inf"),
        not r["open_requests"],
        *key,
    )


def _build(cfg):
    state = curation.read_json(cfg.state, {})
    experiments = curation.read_json(cfg.experiments, [])
    curated = curation.read_json(cfg.cls, {})
    pinpoint = curation.read_json(cfg.pinpoint, {"cls": {}})
    log = requestlog.load(cfg.requests)
    requests = list(log.requests.values())

    for e in experiments:
        e.setdefault("totals", {})
        for job in e.setdefault("jobs", []):
            job["url"] = PINPOINT_JOB + job["id"]
    exp_by_id = {e["id"]: e for e in experiments}
    exp_by_cl = {}
    for e in experiments:
        if e.get("cl"):
            exp_by_cl.setdefault(str(e["cl"]), e)

    reqs_by_cl = {}
    for r in requests:
        reqs_by_cl.setdefault(r["cl"], []).append(r)

    rows = {key: [] for key, *_ in BUCKETS}
    subjects = {}
    stale_count = 0
    for num, g in state.items():
        cur = curated.get(num, {})
        cv = _cv(g)
        derived = _derived_bucket(g, cv)
        bucket = derived
        closed = derived in ("landed", "abandoned")
        stale = [] if closed else curation.changed_fields(cur, g)
        if not closed and not stale and cur.get("bucket") in CURATED_BUCKETS:
            bucket = cur["bucket"]
        elif not closed and g.get("auto") and num not in curated:
            bucket = "untracked"
        stale_count += bool(stale)
        subjects[num] = g["subject"]
        reqs = reqs_by_cl.get(num, [])
        row = {
            "num": num,
            "url": cfg.change_url(num),
            "subject": g["subject"],
            "ps": g.get("current_revision_number"),
            "wip": g["work_in_progress"],
            "status": g["status"],
            "cv": cv,
            "cr": _votes(g["cr_votes"]),
            "cr_sum": sum(g["cr_votes"].values()),
            # Open threads have their own column.
            "needs": [
                n for n in g.get("unsatisfied", []) if n != "No-Unresolved-Comments"
            ],
            "cq": _votes(g["cq_votes"]),
            "unresolved": g["unresolved_comment_count"],
            "comments": g["total_comment_count"],
            "reviewers": g["reviewers"],
            "updated": _gerrit_time(g["updated"]),
            "curated": bucket != derived,
            "stale": stale,
            "next": cur.get("next", ""),
            "note": cur.get("note", ""),
            "prio": cur.get("prio"),
            "exp": exp_by_id.get(cur.get("exp")) or exp_by_cl.get(num),
            "pp": _pinpoint(pinpoint["cls"].get(num), g.get("current_revision_number")),
            "requests": reqs,
            "open_requests": [
                q for q in reqs if q["status"] in ("pending", "approved")
            ],
        }
        rows[bucket].append(row)
    for key, bucket_rows in rows.items():
        bucket_rows.sort(key=lambda r, key=key: _priority(key, r))

    def link(item):
        item["subject"] = subjects.get(item["cl"], "")
        item["url"] = cfg.change_url(item["cl"]) if item["cl"] else None

    for r in requests:
        link(r)
    notes = [n for n in log.notes if not n["dismissed"]]
    for n in notes:
        link(n)
    by_status = {
        s: [r for r in requests if r["status"] == s]
        for s in ("pending", "approved", "rejected", "done")
    }
    closed = sorted(
        by_status["rejected"] + by_status["done"],
        key=lambda r: r["done_ts"] or r["decided_ts"],
        reverse=True,
    )

    try:
        tail = cfg.log.read_text(encoding="utf-8").splitlines()[-LOG_TAIL:][::-1]
    except FileNotFoundError:
        tail = []

    return {
        "bots": sorted({b for e in experiments for b in e["totals"]}),
        "buckets": [
            {"key": k, "title": t, "hint": h, "collapsed": c, "rows": rows[k]}
            for k, t, h, c in BUCKETS
        ],
        "open_count": sum(
            len(rows[k]) for k, *_ in BUCKETS if k not in ("landed", "abandoned")
        ),
        "stale_count": stale_count,
        "pending": by_status["pending"],
        "approved": by_status["approved"],
        "closed": closed[:10],
        "notes": notes[::-1],
        "experiments": experiments,
        "pinpoint": pinpoint["cls"],
        "pinpoint_synced": pinpoint.get("synced"),
        "log": tail,
        "polled": _mtime(cfg.state),
        "agent": curation.read_json(cfg.agent, None),
        "kinds": requestlog.USER_KINDS,
    }


_cache = (None, None)


def load(cfg):
    """(version, model); the model is rebuilt only when a source changed."""
    global _cache
    key = (cfg, version(cfg))
    if _cache[0] != key:
        _cache = (key, _build(cfg))
    return key[1], _cache[1]


def age_s(t, now=None):
    """Seconds since a datetime or an ISO timestamp."""
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - t).total_seconds()


def ago(t):
    """Compact relative time for a datetime or an ISO timestamp."""
    if t is None:
        return "never"
    s = int(age_s(t))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"
