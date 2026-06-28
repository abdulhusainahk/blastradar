"""BlastRadar CLI.

Two subcommands:

  gate   Compute the infra blast radius of a PR and gate the merge.
         python -m blastradar gate --root . --changed a.tf charts/stream/values.yaml
         python -m blastradar gate --root . --changed-from-git origin/main

  crawl  Build the org-wide cross-repo infra graph from GitHub.
         GITHUB_TOKEN=... python -m blastradar crawl --org my-org --out org-graph.yaml

In GitHub Actions the gate exits non-zero when it blocks, failing the check.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import crawl, graph, radius, report, risk


def _git_changed(base: str, root: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", root, "diff", "--name-only", f"{base}...HEAD"], text=True)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def cmd_gate(args) -> int:
    if args.changed is not None:
        changed = args.changed
    elif args.changed_from_git:
        changed = _git_changed(args.changed_from_git, args.root)
    else:
        print("error: provide --changed or --changed-from-git", file=sys.stderr)
        return 2

    consumers = args.consumers or (
        os.path.join(args.root, "consumers.yaml")
        if os.path.exists(os.path.join(args.root, "consumers.yaml")) else None)

    g = graph.build_graph(args.root, consumers)
    br = radius.compute(g, changed, args.root)
    assessment = risk.assess(br, changed, high_threshold=3)
    notify = graph.owners_of(g, br.affected)
    md, passed = report.render(br, assessment, gate_at=args.gate_at, notify=notify)

    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0 if passed else 1


def cmd_crawl(args) -> int:
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("error: set --token or GITHUB_TOKEN (needs read access to org repos)", file=sys.stderr)
        return 2
    crawler = crawl.OrgCrawler(args.org, token, max_repos=args.max_repos)
    data = crawler.build()
    crawl.write(data, args.out)
    print(f"Wrote {args.out}: {len(data['edges'])} edges across the org, "
          f"{len(data['owners'])} repos with CODEOWNERS.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blastradar")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate", help="gate a PR by its infra blast radius")
    g.add_argument("--root", default=".")
    g.add_argument("--changed", nargs="*", default=None)
    g.add_argument("--changed-from-git", metavar="BASE")
    g.add_argument("--consumers", default=None, help="org-graph.yaml / consumers.yaml")
    g.add_argument("--gate-at", default="high", choices=["low", "medium", "high"])
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_gate)

    c = sub.add_parser("crawl", help="build the org-wide infra graph from GitHub")
    c.add_argument("--org", required=True)
    c.add_argument("--out", default="org-graph.yaml")
    c.add_argument("--token", default=None, help="GitHub token (or GITHUB_TOKEN env)")
    c.add_argument("--max-repos", type=int, default=1000)
    c.set_defaults(func=cmd_crawl)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
