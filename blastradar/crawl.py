"""Org-wide crawler — build the cross-repo infra graph from every repo in a
GitHub organization, so you don't hand-maintain `consumers.yaml`.

For each repo it lists the tree, fetches IaC files, and records what the repo
*consumes* (Terraform module sources incl. cross-repo `git::` refs, Helm chart
deps, internal container base images), emitting `repo:<name> -> <producer>`
edges. It also reads each repo's CODEOWNERS to map `repo:<name>` -> owners, so
the gate can @-mention the right teams when their service is in the blast radius.

Output is an `org-graph.yaml` the gate loads via `--consumers`.

Usage:
    GITHUB_TOKEN=ghp_... python -m blastradar crawl --org my-org --out org-graph.yaml
"""
from __future__ import annotations

import base64
import os

import yaml

from .graph import DOCKER_FROM, MODULE_SOURCE

API = "https://api.github.com"
IAC_SUFFIXES = (".tf",)
IAC_NAMES = ("Dockerfile", "Chart.yaml", "Chart.yml")
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def _session(token: str | None):
    import requests

    s = requests.Session()
    s.headers.update({"Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def _module_name(source: str) -> str:
    """Normalize a Terraform module source to a producer name.

    Handles local paths and cross-repo git sources with optional `//subdir` and
    `?ref=`: e.g. git::https://github.com/org/tf-modules//network?ref=v1 -> network
    """
    src = source.split("?", 1)[0]
    if "//" in src and not src.startswith(("http://", "https://", "git::")):
        pass  # plain local path with no scheme
    if "//" in src.split("::")[-1].split("github.com/", 1)[-1]:
        src = src.split("//")[-1]            # take the subdir after //
    return src.rstrip("/").split("/")[-1]


class OrgCrawler:
    def __init__(self, org: str, token: str | None, max_repos: int = 1000):
        self.org = org
        self.s = _session(token)
        self.max_repos = max_repos

    def _get(self, url, params=None):
        r = self.s.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r

    def repos(self):
        page, seen = 1, 0
        while seen < self.max_repos:
            data = self._get(f"{API}/orgs/{self.org}/repos",
                             {"per_page": 100, "page": page, "type": "all"}).json()
            if not data:
                return
            for repo in data:
                if repo.get("archived"):
                    continue
                yield repo["name"], repo.get("default_branch", "main")
                seen += 1
                if seen >= self.max_repos:
                    return
            page += 1

    def tree(self, repo: str, branch: str) -> list[str]:
        try:
            data = self._get(f"{API}/repos/{self.org}/{repo}/git/trees/{branch}",
                             {"recursive": "1"}).json()
        except Exception:
            return []
        return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"]

    def file(self, repo: str, path: str, branch: str) -> str:
        try:
            data = self._get(f"{API}/repos/{self.org}/{repo}/contents/{path}",
                             {"ref": branch}).json()
            if data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", "replace")
        except Exception:
            pass
        return ""

    def codeowners(self, repo: str, branch: str) -> list[str]:
        for path in CODEOWNERS_PATHS:
            text = self.file(repo, path, branch)
            if not text:
                continue
            star, fallback = [], []
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                handles = [p for p in parts[1:] if p.startswith("@")]
                if parts[0] == "*":
                    star = handles
                fallback = fallback or handles
            return star or fallback
        return []

    def build(self) -> dict:
        edges: list[dict] = []
        owners: dict[str, list[str]] = {}
        seen_edges: set[tuple[str, str]] = set()

        for repo, branch in self.repos():
            node = f"repo:{repo}"
            paths = self.tree(repo, branch)
            iac = [p for p in paths
                   if p.endswith(IAC_SUFFIXES) or os.path.basename(p) in IAC_NAMES]
            for path in iac:
                text = self.file(repo, path, branch)
                if not text:
                    continue
                for producer in _producers_consumed(path, text):
                    key = (node, producer)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({"consumer": node, "depends_on": producer})
            handles = self.codeowners(repo, branch)
            if handles:
                owners[node] = handles
        return {"edges": edges, "owners": owners, "critical": []}


def _producers_consumed(path: str, text: str) -> list[str]:
    out: list[str] = []
    base = os.path.basename(path)
    if path.endswith(".tf"):
        out += [f"tfmodule:{_module_name(s)}" for s in MODULE_SOURCE.findall(text)]
    elif base == "Dockerfile":
        for b in DOCKER_FROM.findall(text):
            b = b.split(":")[0]
            if "/" in b and not b.startswith(("library/", "docker.io/library/")):
                out.append(f"image:{b.split('/')[-1]}")
    elif base in ("Chart.yaml", "Chart.yml"):
        try:
            data = yaml.safe_load(text) or {}
            out += [f"helmchart:{d['name']}" for d in data.get("dependencies", []) or []
                    if isinstance(d, dict) and d.get("name")]
        except yaml.YAMLError:
            pass
    return out


def write(data: dict, out_path: str) -> None:
    header = ("# Auto-generated by `blastradar crawl` — do not edit by hand.\n"
              "# Cross-repo infra dependency graph + CODEOWNERS for the org.\n")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
