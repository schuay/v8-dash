#!/usr/bin/env python3
# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Render a project's experiment tracker as a PDF.

One section per experiment in the data module (see data.example.py):
description, worktree, CL with its live Gerrit state, and the colored
`pp s --compact` output of every Pinpoint job.

  V8_UTILS=DIR uv run --with reportlab gen.py --data DATA_PY [-o out.pdf]
      [--refresh]

Needs reportlab (BSD) and a v8-utils (MIT) checkout in $V8_UTILS, whose
`pp` and pp_rows.py run in that project's environment. Pinpoint output is
cached in $REPORT_CACHE (default: report/ in the shepherd state_dir);
completed jobs don't change, --refresh re-fetches them. Gerrit state is
always fetched.
"""

import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import seed

from shepherd import config

CFG = config.load()
CACHE_ROOT = os.environ.get("REPORT_CACHE") or str(CFG.state_dir / "report")
CACHE = os.path.join(CACHE_ROOT, "pp")
ROWS = os.path.join(CACHE_ROOT, "rows")
V8_UTILS = os.environ.get("V8_UTILS", "v8-utils")
UV_V8_UTILS = ["uv", "run", "-q", "--project", V8_UTILS]
# Keeps v8-utils' Pinpoint cache DB next to the report cache.
V8_UTILS_ENV = {**os.environ, "XDG_DATA_HOME": os.path.join(CACHE_ROOT, "xdg-data")}
GERRIT = CFG.gerrit
# Set from the data module in main().
E, HEADLINE, HEADLINE_JOBS, BOT_SHORT, BOTS, WORKTREE_ROOT = [], {}, {}, {}, [], ""

# Menlo where macOS has it, else the built-in Courier.
MENLO = "/System/Library/Fonts/Menlo.ttc"
if os.path.exists(MENLO):
    pdfmetrics.registerFont(TTFont("Menlo", MENLO, subfontIndex=0))
    pdfmetrics.registerFont(TTFont("Menlo-Bold", MENLO, subfontIndex=1))
    MONO, MONO_BOLD = "Menlo", "Menlo-Bold"
else:
    MONO, MONO_BOLD = "Courier", "Courier-Bold"

FS = 6.6
LH = FS * 1.25
CW = pdfmetrics.stringWidth("0", MONO, FS)
FG = {
    31: colors.HexColor("#c62828"),
    32: colors.HexColor("#1b7f2a"),
    36: colors.HexColor("#00727f"),
}
DIM = colors.HexColor("#808080")
BG = colors.HexColor("#f6f6f4")

STATUS_COLOR = {
    "confirmed": "#1b7f2a",
    "mixed": "#c77700",
    "neutral": "#6d6d6d",
    "parked": "#6d6d6d",
    "refuted": "#c62828",
    "planned": "#3b5bab",
    "exploring": "#7b3fa0",
    "m1.5": "#7b3fa0",
    "m2": "#7b3fa0",
    # Promising in one shell, not yet confirmed in the browser.
    "promising": "#d4006a",
}
CL_COLOR = {
    "landed": "#1b7f2a",
    "approved": "#1b7f2a",
    "under review": "#c77700",
    "not sent": "#3b5bab",
    "wip": "#6d6d6d",
    "abandoned": "#c62828",
}

SGR = re.compile(r"\x1b\[([0-9;]*)m")


# Pinpoint ------------------------------------------------------------------


def fetch_pp(job, refresh):
    path = os.path.join(CACHE, job + ".ans")
    if refresh or not os.path.exists(path) or os.path.getsize(path) == 0:
        # pp only colors its output on a tty; script(1) provides one and
        # records what it writes.
        pp = " ".join([*UV_V8_UTILS, "pp"])
        subprocess.run(
            [
                "script",
                "-q",
                path,
                "sh",
                "-c",
                f"stty cols 140; {pp} s --compact {job} 2>/dev/null",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            env=V8_UTILS_ENV,
            check=True,
        )
    return path


def fetch_rows(jobs, refresh):
    """Result rows by job, from pp_rows.py's JSON cache."""
    missing = [
        j
        for j in jobs
        if refresh or not os.path.exists(os.path.join(ROWS, j + ".json"))
    ]
    if missing:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pp_rows.py")
        subprocess.run(
            [*UV_V8_UTILS, "python", "-I", script, ROWS, *missing],
            env=V8_UTILS_ENV,
            check=True,
        )
    out = {}
    for j in jobs:
        path = os.path.join(ROWS, j + ".json")
        if os.path.exists(path):
            with open(path) as f:
                out[j] = json.load(f)
    return out


def est_total(data):
    """JetStream3 Total change in percent, from the significant story changes.

    The JetStream score is the geometric mean of the story scores, so the
    Total ratio is the geometric mean of the story ratios; non-significant
    stories count as unchanged. Returns None for other benchmarks.
    """
    if "jetstream" not in (data["benchmark"] or ""):
        return None
    stories = [
        r
        for r in data["rows"]
        if r["name"] != "Total" and r["base_mean"] and r["exp_mean"]
    ]
    log_sum = sum(
        math.log(r["exp_mean"] / r["base_mean"]) for r in stories if r["significant"]
    )
    return (math.exp(log_sum / len(stories)) - 1) * 100


def parse_ansi(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    # script(1) echoes the EOF it sends to the child as "^D\b\b".
    raw = re.sub(r"^(\^D|\x04)[\x04\x08]*", "", raw.replace("\r", ""))
    lines = []
    for line in raw.split("\n"):
        spans, bold, dim, fg, pos = [], False, False, None, 0
        for m in SGR.finditer(line):
            if m.start() > pos:
                spans.append((line[pos : m.start()], bold, dim, fg))
            for c in [int(x) for x in (m.group(1) or "0").split(";")]:
                if c == 0:
                    bold, dim, fg = False, False, None
                elif c == 1:
                    bold = True
                elif c == 2:
                    dim = True
                elif c in FG:
                    fg = c
            pos = m.end()
        if pos < len(line):
            spans.append((line[pos:], bold, dim, fg))
        lines.append(spans)

    def blank(spans):
        return not "".join(s[0] for s in spans).strip()

    lines = [
        l for i, l in enumerate(lines) if not (blank(l) and i and blank(lines[i - 1]))
    ]
    while lines and blank(lines[-1]):
        lines.pop()
    while lines and blank(lines[0]):
        lines.pop(0)
    return lines


def bot_of(lines):
    m = re.search(r"bot:\s*(\S+)", "".join(s[0] for s in lines[0])) if lines else None
    return m.group(1) if m else "?"


# Gerrit --------------------------------------------------------------------


def fetch_cl(num):
    url = (
        f"{GERRIT}/changes/?q=change:{num}"
        "&o=LABELS&o=DETAILED_ACCOUNTS&o=CURRENT_REVISION"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read().decode()
    # Gerrit prefixes JSON responses with )]}' to defeat XSSI.
    changes = json.loads(body[body.index("\n") + 1 :])
    return changes[0] if changes else None


def cl_state(c):
    owner = c["owner"]["_account_id"]
    votes = [
        v
        for v in c.get("labels", {}).get("Code-Review", {}).get("all", [])
        if v.get("_account_id") != owner and v.get("value")
    ]
    reviewers = [
        r
        for r in c.get("reviewers", {}).get("REVIEWER", [])
        if r.get("_account_id") != owner and "SERVICE_USER" not in r.get("tags", [])
    ]
    if c["status"] == "MERGED":
        state = "landed"
    elif c["status"] == "ABANDONED":
        state = "abandoned"
    elif c.get("work_in_progress"):
        state = "wip"
    elif any(v["value"] > 0 for v in votes):
        state = "approved"
    elif reviewers:
        state = "under review"
    else:
        state = "not sent"
    return {
        "state": state,
        "reviewers": ", ".join(r.get("email", "?").split("@")[0] for r in reviewers),
        "ps": c.get("current_revision_number"),
        "size": f"+{c.get('insertions', 0)}/-{c.get('deletions', 0)}",
        "unresolved": c.get("unresolved_comment_count", 0),
    }


def cl_number(cl):
    m = re.match(r"\s*(\d+)", cl or "")
    return m.group(1) if m else None


# Rendering -----------------------------------------------------------------


def wrap_cols(lines, cols):
    out = []
    for spans in lines:
        cur, n = [], 0
        for span, *attr in spans:
            text = span
            while text:
                take = cols - n
                if take <= 0:
                    out.append(cur)
                    cur, n = [], 0
                    continue
                cur.append((text[:take], *attr))
                n += len(text[:take])
                text = text[take:]
        out.append(cur)
    return out


class Term(Flowable):
    def __init__(self, lines, width):
        super().__init__()
        self.pad = 4
        self.lines = wrap_cols(lines, int((width - 2 * self.pad) / CW))
        self.width = width
        self.height = len(self.lines) * LH + 2 * self.pad

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(BG)
        c.roundRect(0, 0, self.width, self.height, 2, stroke=0, fill=1)
        y = self.height - self.pad - FS
        for spans in self.lines:
            x = self.pad
            for text, bold, dim, fg in spans:
                c.setFont(MONO_BOLD if bold else MONO, FS)
                c.setFillColor(FG[fg] if fg else (DIM if dim else colors.black))
                c.drawString(x, y, text)
                x += len(text) * CW
            y -= LH


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def badge(status):
    color = STATUS_COLOR.get(status.split()[0].lower().rstrip(";:,"), "#000000")
    return f'<font color="{color}"><b>{esc(status.upper())}</b></font>'


def cl_badge(state):
    return f'<font color="{CL_COLOR[state]}"><b>{state.upper()}</b></font>'


def cl_link(num):
    return f'<a href="{CFG.change_url(num)}" color="#00727f">{num}</a>'


def cl_detail(num, info):
    if not info:
        return cl_link(num)
    parts = [cl_link(num), cl_badge(info["state"]), f"PS{info['ps']}", info["size"]]
    if info["reviewers"]:
        parts.append("reviewers: " + esc(info["reviewers"]))
    if info["unresolved"]:
        parts.append(f"{info['unresolved']} unresolved")
    return " &nbsp;".join(parts)


H1 = ParagraphStyle(
    "h1", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceAfter=6
)
H2 = ParagraphStyle(
    "h2",
    fontName="Helvetica-Bold",
    fontSize=11.5,
    leading=14,
    spaceBefore=4,
    spaceAfter=2,
)
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=8.5, leading=11)
META = ParagraphStyle(
    "meta",
    fontName="Helvetica",
    fontSize=7.8,
    leading=10,
    textColor=colors.HexColor("#444444"),
)
SUB = ParagraphStyle(
    "sub",
    fontName="Helvetica-Oblique",
    fontSize=7.8,
    leading=10,
    spaceBefore=3,
    spaceAfter=2,
)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.5, leading=9)
GROUP = ParagraphStyle("group", fontName="Helvetica-Bold", fontSize=8.5, leading=11)
GROUP_H = ParagraphStyle(
    "grouph",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=17,
    spaceBefore=6,
    spaceAfter=4,
)

# Experiments with a CL are grouped by its Gerrit state, the rest by their
# local status.
GROUP_ORDER = [
    "landed",
    "approved",
    "under review",
    "wip",
    "exploring",
    "neutral",
    "rest",
]
GROUP_OF_STATE = {
    "landed": "landed",
    "approved": "approved",
    "under review": "under review",
    "not sent": "wip",
    "wip": "wip",
    "abandoned": "rest",
    "exploring": "exploring",
    "m1.5": "exploring",
    "m2": "exploring",
    "neutral": "neutral",
    "parked": "neutral",
}
GROUP_COLOR = {
    "exploring": STATUS_COLOR["exploring"],
    "neutral": STATUS_COLOR["neutral"],
    "rest": "#444444",
}


def group_of(e, gerrit):
    info = gerrit.get(cl_number(e[5]))
    state = info["state"] if info else e[2].split()[0].lower()
    return GROUP_OF_STATE.get(state, "rest")


def group_title(g):
    if g in CL_COLOR:
        return f"CL {cl_badge(g)}"
    return f'<font color="{GROUP_COLOR[g]}"><b>{g.upper()}</b></font>'


def total_cell(jobs, rows):
    by_bot = {}
    for j in jobs:
        d = rows.get(j)
        t = est_total(d) if d else None
        if t is not None:
            by_bot[BOT_SHORT.get(d["configuration"], "?")] = t
    if not by_bot:
        return "-"
    return "/".join(f"{by_bot[b]:+.2f}" if b in by_bot else "-" for b in BOTS)


def index_row(e, gerrit, rows):
    id_, title, status, _desc, _wt, cl, jobs, _extra = e
    n = cl_number(cl)
    info = gerrit.get(n)
    state = badge(status)
    if info:
        state += "<br/>CL " + cl_badge(info["state"])
        if info["reviewers"]:
            state += f' <font size="6">({esc(info["reviewers"])})</font>'
    total = total_cell(HEADLINE_JOBS.get(id_, jobs), rows)
    top = HEADLINE.get(id_, "-")
    return [
        Paragraph(f'<a href="#{id_}" color="#000000"><b>{id_}</b></a>', CELL),
        Paragraph(esc(title), CELL),
        Paragraph(cl_link(n) if n else "-", CELL),
        Paragraph(esc(total), CELL),
        Paragraph(esc(top), CELL),
        Paragraph(state, CELL),
    ]


def section(e, gerrit, jobs_block):
    """Returns the head flowables (kept with the first jobs) and the rest."""
    id_, title, status, desc, wt, cl, jobs, extra = e
    n = cl_number(cl)
    njobs = len(jobs) + sum(len(x[1]) for x in extra)
    head = [
        Paragraph(
            f'<a name="{id_}"/>{id_} &nbsp;{esc(title)} &nbsp;&nbsp;'
            f'<font size="8">{badge(status)}</font>',
            H2,
        ),
        Paragraph("<br/>".join(esc(l) for l in desc.split("\n")), BODY),
        Paragraph(
            f"<b>Location:</b> <font name='{MONO}'>{esc(os.path.join(WORKTREE_ROOT, wt)) if wt else '-'}</font>"
            f" &nbsp;&nbsp; <b>Pinpoint:</b> {njobs or 'none'}",
            META,
        ),
        Paragraph(f"<b>CL:</b> {cl_detail(n, gerrit.get(n)) if n else '-'}", META),
    ]
    blocks = jobs_block(jobs, True)
    rest = blocks[2:]
    for label, xj in extra:
        rest.append(Paragraph(esc(label), SUB))
        rest += jobs_block(xj, False)
    rest.append(Spacer(1, 10))
    return head + blocks[:2], rest


def main():
    global E, HEADLINE, HEADLINE_JOBS, BOT_SHORT, BOTS, WORKTREE_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="project data module")
    ap.add_argument("-o", "--out", default="experiments.pdf")
    ap.add_argument("--refresh", action="store_true", help="re-fetch Pinpoint output")
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("project_data", args.data)
    data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(data)
    E, HEADLINE, HEADLINE_JOBS = data.E, data.HEADLINE, data.HEADLINE_JOBS
    BOT_SHORT = getattr(data, "BOT_SHORT", seed.BOT_SHORT)
    BOTS = list(dict.fromkeys(BOT_SHORT.values()))
    WORKTREE_ROOT = getattr(data, "WORKTREE_ROOT", "")
    title = getattr(data, "TITLE", "Experiments")
    os.makedirs(CACHE, exist_ok=True)

    all_jobs = [j for e in E for j in e[6]] + [
        j for e in E for _, xs in e[7] for j in xs
    ]
    cls = sorted({n for e in E if (n := cl_number(e[5]))})
    with ThreadPoolExecutor(8) as ex:
        pp = dict(
            zip(
                all_jobs,
                ex.map(lambda j: parse_ansi(fetch_pp(j, args.refresh)), all_jobs),
                strict=False,
            )
        )
        raw = dict(zip(cls, ex.map(fetch_cl, cls), strict=False))
    headline_jobs = sorted({j for e in E for j in HEADLINE_JOBS.get(e[0], e[6])})
    pp_rows = fetch_rows(headline_jobs, args.refresh)
    gerrit = {n: cl_state(c) for n, c in raw.items() if c}

    doc = SimpleDocTemplate(
        args.out,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )
    W = doc.width
    where = f"Worktrees are under {esc(WORKTREE_ROOT)}. " if WORKTREE_ROOT else ""
    story = [
        Paragraph(esc(title), H1),
        Paragraph(
            f"Pinpoint tables: <font name='{MONO}'>pp s --compact &lt;job&gt;</font>. "
            "CL state from Gerrit. "
            f"Generated {date.today().isoformat()}. {where}"
            "*Total: geometric mean of the significant story changes (p&lt;0.01), "
            "non-significant stories counted as unchanged; same formula as the "
            "JetStream score.",
            META,
        ),
        Spacer(1, 6),
    ]

    groups = {}
    for e in E:
        groups.setdefault(group_of(e, gerrit), []).append(e)
    grouped = [(g, groups[g]) for g in GROUP_ORDER if g in groups]
    story += [
        Paragraph(
            " &nbsp; ".join(f"{group_title(g)} {len(es)}" for g, es in grouped), BODY
        ),
        Spacer(1, 6),
    ]

    rows = [
        [
            Paragraph(f"<b>{h}</b>", CELL)
            for h in (
                "ID",
                "Experiment",
                "CL",
                f"Total {'/'.join(BOTS)} %*",
                "Top story",
                "Status",
            )
        ]
    ]
    header_rows = []
    for g, es in grouped:
        header_rows.append(len(rows))
        rows.append(
            [
                Paragraph(f"{group_title(g)} &nbsp;({len(es)})", GROUP),
                "",
                "",
                "",
                "",
                "",
            ]
        )
        rows += [index_row(e, gerrit, pp_rows) for e in es]
    t = Table(
        rows,
        colWidths=[13 * mm, W - 137 * mm, 16 * mm, 26 * mm, 46 * mm, 36 * mm],
        repeatRows=1,
    )
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    for r in header_rows:
        style += [
            ("SPAN", (0, r), (-1, r)),
            ("TOPPADDING", (0, r), (-1, r), 6),
            ("LINEBELOW", (0, r), (-1, r), 0.3, colors.HexColor("#999999")),
        ]
    t.setStyle(TableStyle(style))
    story += [t, PageBreak()]

    def jobs_block(jobs, sort):
        if sort:
            jobs = sorted(
                jobs,
                key=lambda j: (
                    BOTS.index(b) if (b := bot_of(pp[j])) in BOTS else len(BOTS)
                ),
            )
        out = []
        for j in jobs:
            out += [Spacer(1, 3), Term(pp[j], W)]
        return out

    for g, es in grouped:
        heading = Paragraph(
            f'<font size="13">{group_title(g)}</font> &nbsp;'
            f'<font size="9" color="#666666">{len(es)} experiment{"s" if len(es) != 1 else ""}</font>',
            GROUP_H,
        )
        for i, e in enumerate(es):
            head, rest = section(e, gerrit, jobs_block)
            story.append(KeepTogether(([heading] if i == 0 else []) + head))
            story += rest
    doc.build(story)
    print(
        f"wrote {args.out}: {len(E)} experiments, {len(all_jobs)} jobs, {len(gerrit)} CLs"
    )


if __name__ == "__main__":
    main()
