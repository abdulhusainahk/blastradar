"""Build a cross-repo *infrastructure* dependency graph.

Nodes are infra artifacts; edges point consumer -> producer ("depends on").
The blast radius of a changed producer is therefore every node that can reach
it — i.e. its transitive consumers.

Parsed sources (v0, regex/YAML — intentionally dependency-light):
  * Terraform   `module "x" { source = "../modules/network" }`
  * Dockerfile  `FROM <internal-base>`
  * Kubernetes  Deployments that reference a ConfigMap/Secret
  * Helm        Chart.yaml `dependencies:` (subcharts)
  * consumers.yaml  declarative CROSS-REPO edges parsing can't see

Node id scheme:  tfmodule:<name> | service:<name> | image:<name> |
                 helmchart:<name> | configmap:<name> | secret:<name> | repo:<name>
"""
from __future__ import annotations

import os
import re
from collections import defaultdict, deque

import yaml

MODULE_SOURCE = re.compile(r'module\s+"[^"]+"\s*{[^}]*?source\s*=\s*"([^"]+)"', re.DOTALL)
DOCKER_FROM = re.compile(r'^\s*FROM\s+([^\s]+)', re.MULTILINE | re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Source-URL keying — repo+path-qualified Terraform module identities so that  #
# two different modules both named "network" never collide across repos/orgs. #
# --------------------------------------------------------------------------- #
def _norm_subdir(p: str) -> str:
    """Normalize a path, resolving '.'/'..' without touching the filesystem."""
    parts: list[str] = []
    for seg in p.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def parse_git_source(source: str):
    """A cross-repo Terraform git source -> (owner/repo, subdir), else None.

    e.g. git::https://github.com/orgB/tf-modules//modules/network?ref=v1
         -> ("orgB/tf-modules", "modules/network")
    """
    if "github.com" not in source:
        return None
    after = source.split("github.com", 1)[1].lstrip(":/").split("?", 1)[0]
    repo_part, subdir = (after.split("//", 1) + [""])[:2]
    owner_repo = repo_part[:-4] if repo_part.endswith(".git") else repo_part
    return owner_repo.strip("/"), subdir.strip("/")


def tfmodule_key(owner_repo: str, subdir: str) -> str:
    return f"tfmodule:{owner_repo}//{subdir}" if subdir else f"tfmodule:{owner_repo}"


def local_subdir(consumer_file_rel: str, source: str) -> str:
    """Resolve a local module source to a repo-root-relative subdir."""
    base = os.path.dirname(consumer_file_rel.replace("\\", "/"))
    return _norm_subdir(f"{base}/{source}")


def tf_producer(source: str, consumer_file_rel: str, repo_slug: str | None) -> str:
    """The producer node a Terraform module `source` points to.

    Qualified by repo+subdir when we know the org/repo context (cross-repo via
    the git URL, or intra-repo via `repo_slug`); name-only as a fallback.
    """
    git = parse_git_source(source)
    if git:
        return tfmodule_key(*git)
    if source.startswith((".", "/")) and repo_slug:
        return tfmodule_key(repo_slug, local_subdir(consumer_file_rel, source))
    return f"tfmodule:{source.rstrip('/').split('/')[-1]}"


class InfraGraph:
    def __init__(self) -> None:
        self.deps: dict[str, set[str]] = defaultdict(set)   # consumer -> {producers}
        self.nodes: set[str] = set()
        self.meta: dict[str, dict] = defaultdict(dict)      # node -> metadata (e.g. critical)

    def add_edge(self, consumer: str, producer: str) -> None:
        if consumer == producer:
            return
        self.nodes.update((consumer, producer))
        self.deps[consumer].add(producer)

    def add_node(self, node: str, **meta) -> None:
        self.nodes.add(node)
        if meta:
            self.meta[node].update(meta)

    def consumers_of(self, producer: str) -> set[str]:
        """All nodes that transitively depend on `producer` (reverse reachability)."""
        rev: dict[str, set[str]] = defaultdict(set)
        for consumer, producers in self.deps.items():
            for p in producers:
                rev[p].add(consumer)
        seen, queue = set(), deque([producer])
        while queue:
            cur = queue.popleft()
            for c in rev.get(cur, ()):
                if c not in seen:
                    seen.add(c)
                    queue.append(c)
        return seen


# --------------------------------------------------------------------------- #
# Path → node mapping                                                          #
# --------------------------------------------------------------------------- #
def _seg_after(path: str, key: str) -> str | None:
    parts = path.replace("\\", "/").split("/")
    if key in parts:
        i = parts.index(key)
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def nodes_for_path(path: str, root: str = ".", repo_slug: str | None = None) -> list[str]:
    """Best-effort: which graph node(s) does a changed file belong to."""
    p = path.replace("\\", "/")
    # Qualified mode: a changed .tf file IS the module at its directory.
    if repo_slug and p.endswith(".tf"):
        return [tfmodule_key(repo_slug, _norm_subdir(os.path.dirname(p)))]
    if (m := _seg_after(p, "modules")):
        return [f"tfmodule:{m}"]
    if (c := _seg_after(p, "charts")):
        return [f"helmchart:{c}"]
    if p.endswith("Dockerfile") and (s := _seg_after(p, "services")):
        return [f"image:{s}"]
    if (s := _seg_after(p, "services")):
        return [f"service:{s}"]
    # Kubernetes manifest: resolve by parsing kind/name.
    if p.endswith((".yaml", ".yml")):
        full = os.path.join(root, path)
        for kind, name in _k8s_objects(full):
            if kind in ("ConfigMap", "Secret"):
                return [f"{kind.lower()}:{name}"]
            if kind in ("Deployment", "StatefulSet", "DaemonSet"):
                return [f"service:{name}"]
    return [f"file:{p}"]


def _k8s_objects(full_path: str):
    try:
        with open(full_path, encoding="utf-8") as fh:
            for doc in yaml.safe_load_all(fh):
                if isinstance(doc, dict) and doc.get("kind"):
                    yield doc["kind"], (doc.get("metadata") or {}).get("name", "?")
    except (OSError, yaml.YAMLError):
        return


# --------------------------------------------------------------------------- #
# Graph construction                                                          #
# --------------------------------------------------------------------------- #
def build_graph(root: str, consumers_file: str | None = None,
                repo_slug: str | None = None) -> InfraGraph:
    g = InfraGraph()
    for dirpath, _dirs, files in os.walk(root):
        if ".git" in dirpath.split(os.sep):
            continue
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if fn.endswith(".tf"):
                _parse_terraform(g, full, rel, repo_slug)
            elif fn == "Dockerfile":
                _parse_dockerfile(g, full, rel)
            elif fn in ("Chart.yaml", "Chart.yml"):
                _parse_helm_chart(g, full, rel)
            elif fn.endswith((".yaml", ".yml")):
                _parse_k8s(g, full)
    if consumers_file and os.path.exists(consumers_file):
        _apply_consumers(g, consumers_file)
    return g


def _parse_terraform(g: InfraGraph, full: str, rel: str, repo_slug: str | None = None) -> None:
    consumer = _owning_node(rel)
    g.add_node(consumer)
    try:
        text = open(full, encoding="utf-8").read()
    except OSError:
        return
    for source in MODULE_SOURCE.findall(text):
        if parse_git_source(source) or source.startswith((".", "/")):
            g.add_edge(consumer, tf_producer(source, rel, repo_slug))


def _parse_dockerfile(g: InfraGraph, full: str, rel: str) -> None:
    svc = _seg_after(rel, "services")
    if not svc:
        return
    img = f"image:{svc}"
    g.add_node(img)
    try:
        text = open(full, encoding="utf-8").read()
    except OSError:
        return
    for base in DOCKER_FROM.findall(text):
        base = base.split(":")[0]
        if "/" in base and not base.startswith(("library/", "docker.io/library/")):
            g.add_edge(img, f"image:{base.split('/')[-1]}")


def _parse_helm_chart(g: InfraGraph, full: str, rel: str) -> None:
    chart = _seg_after(rel, "charts") or os.path.basename(os.path.dirname(full))
    node = f"helmchart:{chart}"
    g.add_node(node)
    try:
        data = yaml.safe_load(open(full, encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    for dep in data.get("dependencies", []) or []:
        if isinstance(dep, dict) and dep.get("name"):
            g.add_edge(node, f"helmchart:{dep['name']}")


def _parse_k8s(g: InfraGraph, full: str) -> None:
    for kind, name, refs in _k8s_workloads(full):
        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            svc = f"service:{name}"
            g.add_node(svc)
            for ref_kind, ref_name in refs:
                g.add_edge(svc, f"{ref_kind}:{ref_name}")


def _k8s_workloads(full: str):
    try:
        docs = list(yaml.safe_load_all(open(full, encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        return
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name", "?")
        refs: list[tuple[str, str]] = []
        spec = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
        for ctr in spec.get("containers", []) or []:
            for ef in ctr.get("envFrom", []) or []:
                if "configMapRef" in ef:
                    refs.append(("configmap", ef["configMapRef"]["name"]))
                if "secretRef" in ef:
                    refs.append(("secret", ef["secretRef"]["name"]))
            for env in ctr.get("env", []) or []:
                vf = (env.get("valueFrom") or {})
                if "configMapKeyRef" in vf:
                    refs.append(("configmap", vf["configMapKeyRef"]["name"]))
                if "secretKeyRef" in vf:
                    refs.append(("secret", vf["secretKeyRef"]["name"]))
        for vol in spec.get("volumes", []) or []:
            if "configMap" in vol:
                refs.append(("configmap", vol["configMap"]["name"]))
            if "secret" in vol:
                refs.append(("secret", vol["secret"]["secretName"]))
        yield kind, name, refs


def _apply_consumers(g: InfraGraph, consumers_file: str) -> None:
    """consumers.yaml / org-graph.yaml: cross-repo edges, criticality, owners.

    edges:
      - consumer: repo:mobile-bff
        depends_on: tfmodule:network
    critical: [service:payments, repo:mobile-bff]
    owners:
      repo:mobile-bff: ["@org/mobile-team"]
    """
    data = yaml.safe_load(open(consumers_file, encoding="utf-8")) or {}
    for e in data.get("edges", []) or []:
        g.add_edge(e["consumer"], e["depends_on"])
    for node in data.get("critical", []) or []:
        g.add_node(node, critical=True)
    for node, owners in (data.get("owners", {}) or {}).items():
        g.add_node(node, owners=list(owners))


def owners_of(graph: InfraGraph, nodes) -> list[str]:
    """Unique CODEOWNERS handles to notify for a set of affected nodes."""
    handles: list[str] = []
    for n in nodes:
        for h in graph.meta.get(n, {}).get("owners", []) or []:
            if h not in handles:
                handles.append(h)
    return handles


def _owning_node(rel: str) -> str:
    if (s := _seg_after(rel, "services")):
        return f"service:{s}"
    if (m := _seg_after(rel, "modules")):
        return f"tfmodule:{m}"
    return f"dir:{os.path.dirname(rel).replace(os.sep, '/') or '.'}"
