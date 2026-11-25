# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Example project data module for seed.py and gen.py.

Copy it next to your project file and keep it in sync with the experiments
being shepherded. All values here are made up.
"""

TITLE = "Example performance experiments"

# Where the worktrees named below live, for the PDF report.
WORKTREE_ROOT = "/path/to/worktrees"

# (id, title, status, description, worktree, cl, [jobs], [(label, [jobs])])
#
# status: free text whose first word picks a color in the report, eg
#   confirmed | mixed | neutral | parked | refuted | planned | exploring
# cl: "<number>", "<number> (<note>)", or None
# jobs: Pinpoint job ids, one per bot; the extra list holds labelled
#   follow-up batches.
E = [
    (
        "exp-01",
        "Cache scope lookups",
        "confirmed",
        "Cache scope chain lookups in the parser.\nPositive on all bots.",
        "scope-cache",
        "100001 (PS3)",
        ["aaaa0001", "aaaa0002"],
        [("PS2", ["aaaa0011", "aaaa0012"])],
    ),
    (
        "exp-02",
        "Inline small string search",
        "mixed",
        "Search short strings inline.",
        None,
        "100005",
        ["aaaa0003"],
        [],
    ),
    (
        "exp-03",
        "Backtracking limit",
        "exploring",
        "Bound regexp backtracking.",
        None,
        None,
        [],
        [],
    ),
]

# Top story per experiment, curated by hand.
HEADLINE = {"exp-01": "parse-heavy +2.1%"}

# Jobs the summary uses where the main jobs are not the latest relevant
# measurement.
HEADLINE_JOBS = {"exp-01": ["aaaa0011", "aaaa0012"]}

# Pinpoint configuration -> short bot name.
BOT_SHORT = {
    "linux-r350-perf": "linux",
    "mac-m1_mini_2020-perf": "m1",
    "mac-m4-mini-perf": "m4",
}
