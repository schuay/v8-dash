# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Triage failed CQ / dry-run builds via Buildbucket and ResultDB.

Anonymous access works for public try builds; no login needed.

  shepherd cq LABEL=BUILD_ID ...
      per build: builder, status, the CL patchset it tested, unique failing
      tests (variant suffix stripped) and a variant histogram
  shepherd cq --output LABEL=BUILD_ID=TEST_SUBSTRING ...
      the command line and output artifacts of the first failing test whose
      id contains TEST_SUBSTRING (head and tail of long outputs)

LABEL is free text that tags the output, usually the CL number. Build IDs
are in the links of the CV failure message.
"""

import argparse
import collections
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BUILDBUCKET = "cr-buildbucket.appspot.com"
RESULTDB = "results.api.cr.dev"
UNEXPECTED = {"expectancy": "VARIANTS_WITH_ONLY_UNEXPECTED_RESULTS"}
MAX_ARTIFACT = 200_000


def rpc(host, service, body):
    req = urllib.request.Request(
        f"https://{host}/prpc/{service}",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as f:
        # pRPC prefixes JSON responses with the )]}' guard.
        return json.loads(f.read().decode()[4:])


def unexpected_results(invocation, page_size=1000):
    out, token = [], None
    while True:
        body = {
            "invocations": [invocation],
            "predicate": UNEXPECTED,
            "pageSize": page_size,
        }
        if token:
            body["pageToken"] = token
        r = rpc(RESULTDB, "luci.resultdb.v1.ResultDB/QueryTestResults", body)
        out += r.get("testResults", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def summarize(label, build_id):
    b = rpc(
        BUILDBUCKET,
        "buildbucket.v2.Builds/GetBuild",
        {
            "id": build_id,
            "mask": {
                "fields": "builder,status,infra.resultdb.invocation,input.gerritChanges"
            },
        },
    )
    results = unexpected_results(b["infra"]["resultdb"]["invocation"])
    changes = b.get("input", {}).get("gerritChanges", [])
    tested = ", ".join(f"{c['change']} PS{c['patchset']}" for c in changes)
    lines = [
        (
            f"== {label} {b['builder']['builder']} {b['status']} "
            f"(tested {tested or 'no CL'}): "
            f"{len({r['testId'].rsplit('//', 1)[0] for r in results})} "
            f"unique failing tests, {len(results)} results"
        )
    ]
    tests = collections.Counter(r["testId"].rsplit("//", 1)[0] for r in results)
    variants = collections.Counter(r["testId"].rsplit("//", 1)[-1] for r in results)
    for t, n in tests.most_common(12):
        lines.append(f"   {n:3} {t}")
    lines.append(f"   variants: {dict(variants.most_common(6))}")
    lines.append(
        f"   status: {dict(collections.Counter(r['status'] for r in results))}"
    )
    return "\n".join(lines)


def output(label, build_id, want):
    results = unexpected_results(f"invocations/build-{build_id}", 200)
    tr = next((t for t in results if want in t["testId"]), None)
    if tr is None:
        return f"===== {label}: no unexpected result matches {want!r}"
    lines = [f"===== {label} {tr['testId']} {tr['status']}"]
    arts = rpc(
        RESULTDB,
        "luci.resultdb.v1.ResultDB/ListArtifacts",
        {"parent": tr["name"], "pageSize": 20},
    ).get("artifacts", [])
    for art in arts:
        lines.append(
            f"-- artifact {art['artifactId']} {art.get('contentType')} "
            f"{art.get('sizeBytes')}"
        )
        if art.get("fetchUrl") and int(art.get("sizeBytes", 0)) < MAX_ARTIFACT:
            with urllib.request.urlopen(art["fetchUrl"], timeout=60) as f:
                text = f.read().decode(errors="replace").splitlines()
            if len(text) > 60:
                text = [*text[:25], "   ...", *text[-25:]]
            lines += text
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="shepherd cq",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--output",
        action="store_true",
        help="print a failing test's command and output",
    )
    ap.add_argument("builds", nargs="+", metavar="LABEL=BUILD_ID[=TEST]")
    args = ap.parse_args(argv)
    try:
        specs = [tuple(b.split("=")) for b in args.builds]
        if any(len(s) != (3 if args.output else 2) for s in specs):
            raise ValueError
    except ValueError:
        ap.error("expected LABEL=BUILD_ID" + ("=TEST" if args.output else ""))
    fn = output if args.output else summarize
    with ThreadPoolExecutor(8) as ex:
        for text in ex.map(lambda s: fn(*s), specs):
            print(text)
    sys.stdout.flush()
    return 0
