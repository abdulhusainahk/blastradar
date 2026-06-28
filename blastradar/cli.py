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

    g = graph.build_graph(args.root, consumers, repo_slug=args.repo)
    br = radius.compute(g, changed, args.root, repo_slug=args.repo)
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
    orgs = [o.strip() for o in (args.orgs or args.org or "").split(",") if o.strip()]
    if not orgs:
        print("error: provide --org <name> or --orgs a,b,c", file=sys.stderr)
        return 2
    data = crawl.crawl_orgs(orgs, token, max_repos=args.max_repos)
    crawl.write(data, args.out)
    print(f"Wrote {args.out}: {len(data['edges'])} edges across {len(orgs)} org(s), "
          f"{len(data['owners'])} repos with CODEOWNERS.")
    return 0


def cmd_merge(args) -> int:
    data = crawl.merge([crawl.load(f) for f in args.files])
    crawl.write(data, args.out)
    print(f"Merged {len(args.files)} graph(s) -> {args.out}: "
          f"{len(data['edges'])} edges, {len(data['owners'])} owned nodes.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blastradar")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate", help="gate a PR by its infra blast radius")
    g.add_argument("--root", default=".")
    g.add_argument("--changed", nargs="*", default=None)
    g.add_argument("--changed-from-git", metavar="BASE")
    g.add_argument("--consumers", default=None, help="org-graph.yaml / consumers.yaml")
    g.add_argument("--repo", default=None,
                   help="owner/repo of THIS repo (e.g. ${{ github.repository }}). "
                        "Enables source-URL-qualified keys — REQUIRED when using a "
                        "crawler-produced org-graph.yaml.")
    g.add_argument("--gate-at", default="high", choices=["low", "medium", "high"])
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_gate)

    c = sub.add_parser("crawl", help="build the org-wide infra graph from GitHub")
    c.add_argument("--org", default=None, help="a single org")
    c.add_argument("--orgs", default=None, help="comma-separated orgs (one token must read all)")
    c.add_argument("--out", default="org-graph.yaml")
    c.add_argument("--token", default=None, help="GitHub token (or GITHUB_TOKEN env)")
    c.add_argument("--max-repos", type=int, default=1000)
    c.set_defaults(func=cmd_crawl)

    m = sub.add_parser("merge", help="merge several org-graph.yaml files into one")
    m.add_argument("files", nargs="+", help="graph YAMLs to merge (e.g. per-org outputs)")
    m.add_argument("--out", default="org-graph.yaml")
    m.set_defaults(func=cmd_merge)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
