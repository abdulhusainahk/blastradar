"""BlastRadar CLI — compute the infra blast radius of a PR and gate the merge.

Usage:
    python -m blastradar --root . --changed a.tf charts/stream/values.yaml
    python -m blastradar --root . --changed-from-git origin/main   # diff vs base

In GitHub Actions, pass the changed files (the workflow computes them) and the
process exits non-zero when the gate blocks, which fails the check.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import graph, radius, report, risk


def _git_changed(base: str, root: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", root, "diff", "--name-only", f"{base}...HEAD"], text=True)
    return [l.strip() for l in out.splitlines() if l.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blastradar")
    ap.add_argument("--root", default=".", help="repo root to scan")
    ap.add_argument("--changed", nargs="*", default=None, help="changed file paths")
    ap.add_argument("--changed-from-git", metavar="BASE", help="diff against this git ref")
    ap.add_argument("--consumers", default=None, help="path to consumers.yaml (cross-repo edges)")
    ap.add_argument("--gate-at", default="high", choices=["low", "medium", "high"],
                    help="block the merge at this risk level or above")
    ap.add_argument("--out", default=None, help="write the markdown report to this file too")
    args = ap.parse_args(argv)

    if args.changed is not None:
        changed = args.changed
    elif args.changed_from_git:
        changed = _git_changed(args.changed_from_git, args.root)
    else:
        ap.error("provide --changed or --changed-from-git")

    consumers = args.consumers or (
        os.path.join(args.root, "consumers.yaml")
        if os.path.exists(os.path.join(args.root, "consumers.yaml")) else None)

    g = graph.build_graph(args.root, consumers)
    br = radius.compute(g, changed, args.root)
    assessment = risk.assess(br, changed, high_threshold=3)
    md, passed = report.render(br, assessment, gate_at=args.gate_at)

    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
    # Surface in the GitHub Actions job summary if available.
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
