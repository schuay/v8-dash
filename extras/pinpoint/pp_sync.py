# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Writes pinpoint.json: the latest Pinpoint batch of every watched CL.

Needs v8-utils (MIT) for its Pinpoint API and the user's Pinpoint login;
runs in that project's environment:

  uv run --project V8_UTILS python -I extras/pinpoint/pp_sync.py

Paths default to the shepherd config: the watcher state is read from
watch_dir, pinpoint.json is written to data_dir. The dashboard works
without this file.

A batch is the set of jobs started together for one CL: same base patch
and benchmark, created within BATCH_WINDOW of each other, one per bot. The
batch shown is the newest one with a completed job; a newer batch that is
still running or failed is recorded under `newer`.

Each job carries its Total and the text `pp show-results --compact` prints,
rendered by the same v8-utils function. Total is the geomean of the
significant story ratios in percent, with non-significant stories counted
as unchanged (the JetStream score formula); None for other benchmarks.
"""

import argparse
import json
import math
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from v8_utils.api import pinpoint
from v8_utils.render.pinpoint import format_results_table

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shepherd import config

BATCH_WINDOW = timedelta(minutes=30)


def total(results):
    if "jetstream" not in (results.job.benchmark or ""):
        return None
    stories = [
        r for r in results.rows if r.name != "Total" and r.base_mean and r.exp_mean
    ]
    if not stories:
        return None
    log_sum = sum(math.log(r.exp_mean / r.base_mean) for r in stories if r.significant)
    return (math.exp(log_sum / len(stories)) - 1) * 100


def batches(jobs):
    """Splits one CL's jobs, newest first, into batches, newest first."""
    out = []
    for job in jobs:
        created = datetime.fromisoformat(job.created)
        key = (job.base_patch, job.benchmark)
        if (
            out
            and out[-1]["key"] == key
            and out[-1]["created"] - created <= BATCH_WINDOW
        ):
            # A bot that was retried within the batch keeps its newest job.
            out[-1]["jobs"].setdefault(job.configuration, job)
        else:
            out.append(
                {"key": key, "created": created, "jobs": {job.configuration: job}}
            )
    return out


def patchset(batch):
    for job in batch["jobs"].values():
        ps = pinpoint.parse_patch_ref(job.experiment_patch)[1]
        if ps:
            return int(ps)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfg = config.load()
    ap.add_argument("--state", default=cfg.state)
    ap.add_argument("--out", default=cfg.pinpoint)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--count", type=int, default=1000)
    args = ap.parse_args()

    with open(args.state) as f:
        watched = set(json.load(f))
    since = datetime.now(UTC) - timedelta(days=args.days)
    by_cl = {}
    for job in pinpoint.fetch_jobs_list(count=args.count, since=since):
        ref = job.experiment_patch and pinpoint.parse_patch_ref(job.experiment_patch)
        if ref and ref[0] in watched and job.created:
            by_cl.setdefault(ref[0], []).append(job)

    chosen = {}
    for cl, jobs in by_cl.items():
        jobs.sort(key=lambda j: j.created, reverse=True)
        all_batches = batches(jobs)
        for i, b in enumerate(all_batches):
            if any(j.status == "Completed" for j in b["jobs"].values()):
                chosen[cl] = (b, all_batches[0] if i else None)
                break

    wanted = [
        j.job_id
        for b, _ in chosen.values()
        for j in b["jobs"].values()
        if j.status == "Completed"
    ]
    results = {r.job_id: r for r in pinpoint.fetch_job_results(wanted)}

    out = {}
    for cl, (b, newer) in chosen.items():
        entry = {
            "created": b["created"].isoformat(),
            "ps": patchset(b),
            "base_patch": b["key"][0],
            "benchmark": b["key"][1],
            "newer": None,
            "jobs": [],
        }
        if newer:
            entry["newer"] = {
                "created": newer["created"].isoformat(),
                "ps": patchset(newer),
                "statuses": sorted(j.status for j in newer["jobs"].values()),
            }
        for job in b["jobs"].values():
            r = results.get(job.job_id)
            has_rows = r is not None and not r.error and r.rows
            entry["jobs"].append(
                {
                    "id": job.job_id,
                    "url": job.url,
                    "bot": pinpoint.short_configuration(job.configuration),
                    "status": job.status,
                    "error": r.error if r else None,
                    "total": total(r) if has_rows else None,
                    "significant": sum(x.significant for x in r.rows)
                    if has_rows
                    else None,
                    "text": format_results_table(
                        r, show_all=False, compact=True, ansi=True
                    )
                    if has_rows
                    else None,
                }
            )
        out[cl] = entry

    path = os.path.normpath(args.out)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            {
                "synced": datetime.now(UTC).isoformat(timespec="seconds"),
                "cls": out,
            },
            f,
            indent=1,
        )
    os.replace(tmp, path)
    print(f"{len(out)} CLs, {len(wanted)} jobs -> {path}")


if __name__ == "__main__":
    main()
