#!/usr/bin/env python3
# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Writes experiments.json, the dashboard's optional experiment table.

  extras/report/seed.py --data DATA_PY [--rows DIR] [--out FILE]

DATA_PY is a project data module in the format of data.example.py. Totals
come from the Pinpoint result rows that pp_rows.py caches in --rows; they
use the JetStream score formula: the geomean of the significant story
ratios, with non-significant stories counted as unchanged. Without cached
rows the totals are empty. --out defaults to experiments.json in the
configured data_dir.
"""

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shepherd import config

BOT_SHORT = {
    "linux-r350-perf": "linux",
    "mac-m1_mini_2020-perf": "m1",
    "mac-m4-mini-perf": "m4",
}


def load_data(path):
    spec = importlib.util.spec_from_file_location("project_data", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def job_result(rows_dir, job, bot_short):
    """(bot, total in percent), each None when unknown."""
    try:
        with open(os.path.join(rows_dir, job + ".json")) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, None
    bot = bot_short.get(data["configuration"], data["configuration"])
    if "jetstream" not in (data["benchmark"] or ""):
        return bot, None
    stories = [
        r
        for r in data["rows"]
        if r["name"] != "Total" and r["base_mean"] and r["exp_mean"]
    ]
    if not stories:
        return bot, None
    log_sum = sum(
        math.log(r["exp_mean"] / r["base_mean"]) for r in stories if r["significant"]
    )
    return bot, (math.exp(log_sum / len(stories)) - 1) * 100


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="project data module")
    ap.add_argument("--rows", help="pp_rows.py output directory")
    ap.add_argument("--out", default=config.load().experiments)
    args = ap.parse_args()

    data = load_data(args.data)
    bot_short = getattr(data, "BOT_SHORT", BOT_SHORT)
    bots = list(dict.fromkeys(bot_short.values()))
    headline = getattr(data, "HEADLINE", {})
    headline_jobs = getattr(data, "HEADLINE_JOBS", {})
    out = []
    for eid, title, status, desc, worktree, cl, jobs, _extra in data.E:
        # The CL field is "<number>" or "<number> (<note>)"; "-" or None
        # when there is no CL.
        head = (cl or "").split(" ", 1)
        num = int(head[0]) if head[0].isdigit() else None
        entry = {
            "id": eid,
            "title": title,
            "status": status,
            "description": desc,
            "worktree": worktree,
            "cl": num,
            "cl_note": cl if num else None,
            "headline": headline.get(eid),
            "jobs": [],
            "totals": dict.fromkeys(bots),
        }
        for job in headline_jobs.get(eid, jobs):
            bot, total = (
                job_result(args.rows, job, bot_short) if args.rows else (None, None)
            )
            entry["jobs"].append({"id": job, "bot": bot, "total": total})
            if bot in entry["totals"]:
                entry["totals"][bot] = total
        out.append(entry)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=1))
    os.replace(tmp, out_path)
    print(f"{len(out)} experiments -> {out_path}")


if __name__ == "__main__":
    main()
