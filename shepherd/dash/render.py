# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""HTML for the dashboard: the page shell, the board and the Pinpoint popover.

Every value from the data files goes through e(); markup is built only from
literals in this file.
"""

import html
import re

from . import store

WATCHER_STALE_S = 20 * 60
# dashctl wait refreshes the heartbeat every 30 s while it listens.
LISTENING_STALE_S = 90
WORKING_STALE_S = 30 * 60

SGR = re.compile(r"\x1b\[([0-9;]*)m")
SGR_CLASS = {"1": "b", "2": "dim", "31": "down", "32": "up", "36": "cyan"}


def e(value):
    return "" if value is None else html.escape(str(value), quote=True)


def attr(name, value):
    return f' {name}="{e(value)}"'


def ansi(text):
    """Terminal text with SGR colors as HTML spans."""
    out, classes = [], []
    pos = 0
    for m in SGR.finditer(text):
        out.append(e(text[pos : m.start()]))
        pos = m.end()
        if classes:
            out.append("</span>")
        codes = m.group(1).split(";")
        classes = (
            []
            if codes in ([""], ["0"])
            else classes + [SGR_CLASS[c] for c in codes if c in SGR_CLASS]
        )
        if classes:
            out.append(f'<span class="{" ".join(classes)}">')
    out.append(e(text[pos:]))
    if classes:
        out.append("</span>")
    return "".join(out)


def delta(v):
    if v is None:
        return '<span class="muted">-</span>'
    cls = "up" if v > 0.005 else "down" if v < -0.005 else "muted"
    return f'<span class="{cls}">{v:+.2f}</span>'


def dot(kind):
    return f'<span class="dot {kind}"></span>'


def chip(text, cls="", title=None, pre=""):
    t = attr("title", title) if title is not None else ""
    c = f"chip {cls}".strip()
    return f'<span class="{c}"{t}>{pre}{e(text)}</span>'


def link(url, text):
    return f'<a href="{e(url)}" target="_blank" rel="noopener">{e(text)}</a>'


def ago(t):
    return store.ago(t)


def ago_suffix(t):
    text = ago(t)
    return f"{text} ago" if text not in ("now", "never") else text


def agent_status(agent):
    """(label, alive) for the agent's heartbeat."""
    if not agent:
        return "no agent seen", False
    age = store.age_s(agent["ts"])
    if agent["state"] == "listening" and age < LISTENING_STALE_S:
        return "agent listening", True
    if agent["state"] == "working" and age < WORKING_STALE_S:
        return f"agent working: {agent.get('status') or '...'}", True
    return f"agent last seen {store.ago(agent['ts'])} ago", False


def page(version, board_html, experiments=False):
    hint = "CL, title, reviewer" + (", experiment" if experiments else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shepherd</title>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/dash.js" defer></script>
</head>
<body>
<header class="top">
  <h1>shepherd</h1>
  <input id="filter" type="search" placeholder="Filter: {hint}" autocomplete="off">
</header>
<main id="board" data-version="{e(version)}">
{board_html}
</main>
<div id="pop" hidden></div>
</body>
</html>
"""


# Board ---------------------------------------------------------------------


def _cl_ref(r):
    if not r.get("cl"):
        return ""
    return (
        f'{link(r["url"], r["cl"])}\n  <span class="subject">{e(r["subject"])}</span>'
    )


def _status_label(q):
    if q["kind"] == "question" and q["status"] == "approved":
        return "answered"
    return q["status"]


def _status_bar(m, agent, agent_alive):
    polled = m["polled"]
    watcher_stale = polled is None or store.age_s(polled) > WATCHER_STALE_S
    parts = [
        f"<span>{m['open_count']} open CLs</span>",
        f"<span>{len(m['pending'])} pending requests</span>",
    ]
    if m["stale_count"]:
        parts.append(
            '<span title="CLs whose Gerrit state changed after they '
            f'were curated">{dot("warning")}{m["stale_count"]} '
            "stale curation</span>"
        )
    parts.append(
        f'<span class="{"alert" if watcher_stale else ""}" '
        'title="Age of cl-watch-state.json">'
        f"{dot('critical' if watcher_stale else 'good')}"
        f"{'watcher stale, ' if watcher_stale else ''}last poll "
        f"{ago_suffix(polled)}</span>"
    )
    parts.append(
        f'<span class="{"" if agent_alive else "alert"}" '
        'title="Heartbeat written by shepherd dashctl">'
        f"{dot('good' if agent_alive else 'critical')}{e(agent)}</span>"
    )
    if m["pinpoint_synced"]:
        parts.append(
            '<span title="Age of pinpoint.json">Pinpoint synced '
            f"{ago_suffix(m['pinpoint_synced'])}</span>"
        )
    return '<p class="status">\n  ' + "\n  ".join(parts) + "\n</p>\n"


def _search(*parts):
    return attr("data-search", " ".join(str(p) for p in parts if p))


def _requests(m):
    if not (m["pending"] or m["approved"] or m["notes"]):
        return ""
    out = ['<section class="requests">', "  <h2>Agent</h2>"]
    for r in m["pending"]:
        if r["kind"] == "question":
            actions = (
                '<input name="reply" type="text" placeholder="Answer" '
                'required maxlength="2000">\n'
                '      <button class="primary" value="approved">'
                "Answer</button>"
            )
        else:
            actions = (
                '<input name="reply" type="text" placeholder="Reply '
                '(optional)" maxlength="2000">\n'
                '      <button class="primary" value="approved">'
                "Approve</button>\n"
                '      <button value="rejected">Reject</button>'
            )
        detail = (
            f'<div class="detail-text">{e(r["detail"])}</div>' if r["detail"] else ""
        )
        out.append(f"""  <form class="request pending"{attr("data-rid", r["id"])}{attr("data-kind", r["kind"])}
       {_search(r["cl"], r["subject"], r["summary"])}>
    <div class="request-body">
      <div>{chip(r["kind"])} {_cl_ref(r)} <span class="muted">{ago(r["ts"])}</span></div>
      <div class="summary">{e(r["summary"])}</div>
      {detail}
    </div>
    <div class="actions">
      {actions}
    </div>
  </form>""")
    for r in m["approved"]:
        reply = (
            f'<div class="detail-text">You: {e(r["reply"])}</div>' if r["reply"] else ""
        )
        waiting = chip(f"{_status_label(r)}, waiting for agent", pre=dot("warning"))
        out.append(f"""  <div class="request"{_search(r["cl"], r["subject"], r["summary"])}>
    <div class="request-body">
      <div>{chip(r["kind"])} {_cl_ref(r)}
        {waiting}
        <span class="muted">{ago(r["decided_ts"])}</span></div>
      <div class="summary">{e(r["summary"])}</div>
      {reply}
    </div>
  </div>""")
    for n in m["notes"]:
        out.append(f"""  <div class="request note"{_search(n["cl"], n["subject"], n["text"])}>
    <div class="request-body">
      <div>{chip("note")} {_cl_ref(n)} <span class="muted">{ago(n["ts"])}</span></div>
      <div class="summary">{e(n["text"])}</div>
    </div>
    <div class="actions"><button{attr("data-dismiss", n["id"])}>Dismiss</button></div>
  </div>""")
    out.append("</section>")
    return "\n".join(out) + "\n"


def _jobs(jobs):
    return " ".join(
        f'<a class="job" href="{e(j["url"])}" target="_blank" '
        f'rel="noopener">{e(j.get("bot") or "job")}</a> '
        f"{delta(j.get('total'))}"
        for j in jobs
    )


def _pp_cell(r):
    pp = r["pp"]
    if not pp:
        return '<td class="pp" data-v=""><span class="muted">none</span></td>'
    totals = []
    for j in pp["jobs"]:
        val = (
            delta(j["total"])
            if j["status"] == "Completed"
            else f'<span class="muted">{e(j["status"].lower())}</span>'
        )
        totals.append(
            f'<span class="pp-t"><span class="muted">{e(j["bot"])}</span> {val}</span>'
        )
    out = [
        f'<td class="pp"{attr("data-v", pp["mean"])}>',
        (
            f'<button type="button" class="pp-sum"{attr("data-pp", r["num"])} '
            f'aria-haspopup="dialog">{"".join(totals)}</button>'
        ),
    ]
    if pp["ps_current"] is False:
        out.append(
            chip(
                f"PS{pp['ps']}",
                "old",
                f"Measured on PS{pp['ps']}, current is PS{r['ps']}",
            )
        )
    if pp["ps_current"] is None:
        started = (pp["created"] or "")[:16].replace("T", " ")
        out.append(
            chip(
                "PS?", "old", f"The jobs don't record a patchset; started {started} UTC"
            )
        )
    if pp["base_patch"]:
        out.append(chip("vs patch", title=f"Base carries {pp['base_patch']}"))
    if pp["newer"]:
        out.append(
            '<span class="dot warning" title="A newer batch has no '
            'completed job"></span>'
        )
    return "".join(out) + "</td>"


def _cl_row(b, r, kinds, show_pp):
    exp = r["exp"]
    title_chips = []
    if exp:
        title_chips.append(chip(exp["id"], title=exp.get("title", "")))
    if r["wip"] and b["key"] != "wip":
        title_chips.append(chip("WIP"))
    if r["curated"]:
        title_chips.append(
            chip(
                "curated",
                title="Bucket set in cls.json; "
                "Gerrit state alone would file it elsewhere",
            )
        )
    if r["open_requests"]:
        title_chips.append(chip("request", pre=dot("warning")))

    cv = r["cv"]
    cv_html = ""
    if cv:
        title = f"{cv['mode']} {cv['verdict']} on PS{cv['ps']}" + (
            "" if cv["current"] else ", not the current patchset"
        )
        label = f"{cv['mode']} {cv['verdict'].lower()}" + (
            "" if cv["current"] else f" PS{cv['ps']}"
        )
        cv_html = chip(
            label,
            "" if cv["current"] else "old",
            title,
            dot("good" if cv["verdict"] == "PASSED" else "critical"),
        )

    review = "".join(
        chip(
            f"{v['who']} {v['value']}",
            title="Code-Review",
            pre=dot("good" if v["ok"] else "critical"),
        )
        for v in r["cr"]
    )
    if r["needs"] and r["status"] == "NEW" and not r["wip"]:
        review += (
            '<span class="needs" title="Unsatisfied submit requirements">'
            f"needs {e(', '.join(r['needs']))}</span>"
        )

    unresolved = (
        f"<strong>{r['unresolved']}</strong>"
        if r["unresolved"]
        else '<span class="muted">0</span>'
    )
    stale_chip = ""
    if r["stale"]:
        stale_chip = chip(
            "stale",
            title="Gerrit state changed since this was "
            f"written: {', '.join(r['stale'])}",
            pre=dot("warning"),
        )
    updated = r["updated"]
    updated_utc = updated.strftime("%Y-%m-%d %H:%M UTC")

    row = f"""    <tr class="row"{attr("data-key", "cl-" + r["num"])} tabindex="0"
        {_search(r["num"], r["subject"], " ".join(r["reviewers"]), exp and exp["id"], r["next"])}>
      <td class="num">{link(r["url"], r["num"])}</td>
      <td class="title"{attr("data-v", r["subject"])}>
        <span class="subject">{e(r["subject"])}</span>
        {"".join(title_chips)}
      </td>
      <td class="num">{e(r["ps"])}</td>
      <td{attr("data-v", cv and cv["verdict"])}>{cv_html}</td>
      <td{attr("data-v", r["cr_sum"] if r["cr"] else "")}>{review}</td>
      <td class="num"{attr("data-v", r["unresolved"])}>{unresolved}</td>
      {_pp_cell(r) if show_pp else ""}
      <td class="reviewers">{e(", ".join(r["reviewers"]))}</td>
      <td class="num"{attr("data-v", int(updated.timestamp()))}{attr("title", updated_utc)}>{ago(updated)}</td>
      <td class="next {"stale" if r["stale"] else ""}">{stale_chip}{e(r["next"])}</td>
    </tr>
"""
    stale_mark = " (stale)" if r["stale"] else ""
    left = []
    if r["next"]:
        left.append(f"<h3>Next{stale_mark}</h3><p>{e(r['next'])}</p>")
    if r["note"]:
        left.append(f"<h3>Note{stale_mark}</h3><p>{e(r['note'])}</p>")
    if r["stale"]:
        left.append(
            f'<p class="muted">Changed since curation: {e(", ".join(r["stale"]))}.</p>'
        )
    cq = (
        ", ".join(f"{e(v['who'])} {e(v['value'])}" for v in r["cq"])
        or '<span class="muted">none</span>'
    )
    needs = (
        f"<dt>Needs</dt><dd>{e(', '.join(r['needs']) or 'nothing')}</dd>"
        if r["status"] == "NEW"
        else ""
    )
    left.append(f"""<h3>Gerrit</h3>
          <dl>
            <dt>State</dt><dd>{e(r["status"].lower())}{", WIP" if r["wip"] else ""}, PS{e(r["ps"])}</dd>
            {needs}
            <dt>Comments</dt><dd>{r["unresolved"]} unresolved of {r["comments"]}</dd>
            <dt>Commit-Queue</dt><dd>{cq}</dd>
            <dt>Updated</dt><dd>{updated_utc}</dd>
          </dl>""")
    if r["requests"]:
        items = []
        for q in reversed(r["requests"]):
            extra = ""
            if q["reply"]:
                extra += f'<div class="detail-text">You: {e(q["reply"])}</div>'
            if q["result"]:
                extra += f'<div class="detail-text">Agent: {e(q["result"])}</div>'
            items.append(
                f"<li>{chip(q['kind'])} {chip(_status_label(q))}\n"
                f'              {e(q["summary"])} <span class="muted">'
                f"{e(q['by'])}, {ago(q['ts'])}</span>{extra}</li>"
            )
        left.append(
            '<h3>Requests</h3>\n          <ul class="history">\n'
            + "\n".join(items)
            + "\n          </ul>"
        )
    options = "".join(f"<option>{e(k)}</option>" for k in kinds)
    left.append(f"""<form class="ask"{attr("data-cl", r["num"])}>
            <h3>Ask the agent</h3>
            <div class="ask-row">
              <select name="kind">{options}</select>
              <input name="summary" type="text" placeholder="What should happen with this CL?" required maxlength="2000">
              <button class="primary">Queue</button>
            </div>
          </form>""")

    right = ""
    if exp:
        dl = [f"<dt>Status</dt><dd>{e(exp.get('status'))}</dd>"]
        if exp.get("headline"):
            dl.append(f"<dt>Top story</dt><dd>{e(exp['headline'])}</dd>")
        if exp.get("worktree"):
            dl.append(f"<dt>Worktree</dt><dd><code>{e(exp['worktree'])}</code></dd>")
        if exp["jobs"]:
            dl.append(
                '<dt title="Report jobs. Geomean of the significant '
                'story ratios, in percent">Report total</dt>'
                f"<dd>{_jobs(exp['jobs'])}</dd>"
            )
        right = f"""
        <div class="col">
          <h3>{e(exp["id"])}: {e(exp.get("title"))}</h3>
          <p class="pre">{e(exp.get("description"))}</p>
          <dl>{"".join(dl)}</dl>
        </div>"""

    detail = f"""    <tr class="detail"{attr("data-for", "cl-" + r["num"])} hidden><td colspan="{10 if show_pp else 9}">
      <div class="panel">
        <div class="col">
          {"".join(left)}
        </div>{right}
      </div>
    </td></tr>
"""
    return row + detail


PP_HEAD = (
    '<th class="c-pp" data-sort="num" title="Latest Pinpoint batch: Total per '
    "bot, the geomean of the significant story ratios in percent. Click the "
    'totals for the full results.">Pinpoint</th>'
)


def _cl_head(show_pp):
    """The CL table header; the Pinpoint column only with Pinpoint data."""
    return f"""    <thead><tr>
      <th class="c-cl num" data-sort="num">CL</th>
      <th class="c-title" data-sort>Title</th>
      <th class="c-ps num" data-sort="num">PS</th>
      <th class="c-cv" data-sort>CV</th>
      <th class="c-votes" data-sort="num" title="Code-Review votes and unsatisfied submit requirements">Review</th>
      <th class="c-open num" data-sort="num" title="Unresolved comment threads">Open</th>
      {PP_HEAD if show_pp else ""}
      <th class="c-rev" data-sort>Reviewers</th>
      <th class="c-upd num" data-sort="num">Updated</th>
      <th class="c-next" data-sort>Next</th>
    </tr></thead>
"""


def _bucket(b, kinds, show_pp):
    rows = "".join(_cl_row(b, r, kinds, show_pp) for r in b["rows"])
    return f"""<details class="bucket"{attr("data-key", "bucket-" + b["key"])} {"" if b["collapsed"] else "open"}>
  <summary><h2>{e(b["title"])}</h2><span class="count">{len(b["rows"])}</span>
    <span class="muted">{e(b["hint"])}</span></summary>
  <table class="cls"{attr("data-key", "sort-" + b["key"])}>
{_cl_head(show_pp)}    <tbody>
{rows}    </tbody>
  </table>
</details>
"""


def _experiments(m, cfg):
    exps = m["experiments"]
    if not exps:
        return ""
    bots = m["bots"]
    heads = "".join(f'<th class="num" data-sort="num">{e(b)}</th>' for b in bots)
    rows = []
    for x in exps:
        cl = x.get("cl")
        totals = "".join(
            f'<td class="num"{attr("data-v", x["totals"].get(b))}>'
            f"{delta(x['totals'].get(b))}</td>"
            for b in bots
        )
        dl = []
        if x.get("worktree"):
            dl.append(f"<dt>Worktree</dt><dd><code>{e(x['worktree'])}</code></dd>")
        if x.get("cl_note"):
            dl.append(f"<dt>CL</dt><dd>{e(x['cl_note'])}</dd>")
        if x["jobs"]:
            dl.append(f"<dt>Pinpoint</dt><dd>{_jobs(x['jobs'])}</dd>")
        rows.append(f"""    <tr class="row"{attr("data-key", "exp-" + str(x["id"]))} tabindex="0"
        {_search(x["id"], x.get("title"), x.get("status"), cl)}>
      <td>{e(x["id"])}</td>
      <td class="title"><span class="subject">{e(x.get("title"))}</span></td>
      <td>{e(x.get("status"))}</td>
      <td class="num"{attr("data-v", cl)}>{link(cfg.change_url(cl), cl) if cl else ""}</td>
      {totals}
      <td>{e(x.get("headline"))}</td>
    </tr>
    <tr class="detail"{attr("data-for", "exp-" + str(x["id"]))} hidden><td colspan="{5 + len(bots)}">
      <div class="panel"><div class="col">
        <p class="pre">{e(x.get("description"))}</p>
        <dl>{"".join(dl)}</dl>
      </div></div>
    </td></tr>
""")
    return f"""<details class="bucket" data-key="experiments">
  <summary><h2>Experiments</h2><span class="count">{len(exps)}</span>
    <span class="muted">Total = geomean of the significant story ratios, %</span></summary>
  <table class="exps" data-key="sort-experiments">
    <thead><tr>
      <th class="c-id" data-sort>ID</th><th class="c-title" data-sort>Title</th>
      <th class="c-status" data-sort>Status</th><th class="c-cl num" data-sort="num">CL</th>
      {heads}<th data-sort>Top story</th>
    </tr></thead>
    <tbody>
{"".join(rows)}    </tbody>
  </table>
</details>
"""


def _closed(m):
    if not m["closed"]:
        return ""
    items = []
    for r in m["closed"]:
        extra = ""
        if r["reply"]:
            extra += f'<div class="detail-text">You: {e(r["reply"])}</div>'
        if r["result"]:
            extra += f'<div class="detail-text">Agent: {e(r["result"])}</div>'
        items.append(
            f"    <li>{chip(r['kind'])} {_cl_ref(r)} "
            f"{chip(r['status'])} {e(r['summary'])}\n"
            f'      <span class="muted">'
            f"{ago(r['done_ts'] or r['decided_ts'])}</span>{extra}</li>"
        )
    return f"""<details class="bucket" data-key="closed-requests">
  <summary><h2>Closed requests</h2><span class="count">{len(m["closed"])}</span></summary>
  <ul class="history padded">
{chr(10).join(items)}
  </ul>
</details>
"""


def _log(m):
    if not m["log"]:
        return ""
    lines = "".join(e(line) + "\n" for line in m["log"])
    return f"""<details class="bucket" data-key="log">
  <summary><h2>Watcher log</h2><span class="muted">latest first, local time of the watcher</span></summary>
  <pre class="log">{lines}</pre>
</details>
"""


def board(m, cfg):
    agent, alive = agent_status(m["agent"])
    parts = [_status_bar(m, agent, alive), _requests(m)]
    show_pp = bool(m["pinpoint"])
    parts += [_bucket(b, m["kinds"], show_pp) for b in m["buckets"] if b["rows"]]
    parts += [_experiments(m, cfg), _closed(m), _log(m)]
    return "\n".join(p for p in parts if p)


def pinpoint(batch):
    out = []
    newer = batch.get("newer")
    if newer:
        ps = f", PS{newer['ps']}" if newer.get("ps") else ""
        out.append(
            f'<p class="pp-warn">{dot("warning")}A newer batch '
            f"({e(newer['created'][:16].replace('T', ' '))}{ps}) has "
            f"no completed job: "
            f"{e(', '.join(newer['statuses']).lower())}.</p>"
        )
    for j in sorted(batch["jobs"], key=lambda j: j["bot"]):
        head = [link(j["url"], j["bot"])]
        if j.get("total") is not None:
            t = j["total"]
            cls = "up" if t > 0.005 else "down" if t < -0.005 else "muted"
            head.append(f'<span class="{cls}">Total {t:+.2f}%</span>')
        if j["status"] != "Completed":
            head.append(chip(j["status"].lower(), pre=dot("critical")))
        if j.get("text"):
            body = f"<pre>{ansi(j['text'])}</pre>"
        elif j.get("error"):
            body = f"<pre>{e(j['error'])}</pre>"
        elif j["status"] == "Completed":
            body = '<pre class="muted">(no results)</pre>'
        else:
            body = ""
        out.append(
            '<div class="pp-job">\n  <div class="pp-head">\n    '
            + "\n    ".join(head)
            + f"\n  </div>\n  {body}\n</div>"
        )
    return "\n".join(out)
