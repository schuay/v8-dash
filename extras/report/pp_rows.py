# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Dump Pinpoint result rows as JSON, one file per job.

Runs in the v8-utils project environment so that it can use its API:

  uv run --project V8_UTILS python -I pp_rows.py OUTDIR JOB...

Writes OUTDIR/<job>.json with the job's configuration, benchmark and rows
(v8_utils.pinpoint.ResultRow fields). Jobs without rows are skipped.
"""

import dataclasses
import json
import os
import sys

from v8_utils.api import pinpoint


def main():
    out, jobs = sys.argv[1], sys.argv[2:]
    for r in pinpoint.fetch_job_results(jobs):
        if r.error or not r.rows:
            print(f"{r.job_id}: no rows ({r.error})", file=sys.stderr)
            continue
        data = {
            "configuration": r.job.configuration,
            "benchmark": r.job.benchmark,
            "rows": [dataclasses.asdict(x) for x in r.rows],
        }
        with open(os.path.join(out, r.job_id + ".json"), "w") as f:
            json.dump(data, f)


if __name__ == "__main__":
    main()
