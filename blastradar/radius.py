"""Compute the blast radius of a set of changed files against the infra graph."""
from __future__ import annotations

from dataclasses import dataclass, field

from .graph import InfraGraph, nodes_for_path


@dataclass
class BlastRadius:
    changed_nodes: list[str] = field(default_factory=list)   # the producers that changed
    affected: list[str] = field(default_factory=list)        # transitive consumers
    critical_hit: list[str] = field(default_factory=list)    # affected nodes flagged critical

    @property
    def affected_services(self) -> list[str]:
        return sorted(n for n in self.affected if n.startswith(("service:", "repo:", "image:")))

    @property
    def size(self) -> int:
        return len(self.affected)


def compute(graph: InfraGraph, changed_files: list[str], root: str = ".",
            repo_slug: str | None = None) -> BlastRadius:
    changed_nodes: set[str] = set()
    for path in changed_files:
        changed_nodes.update(nodes_for_path(path, root, repo_slug))

    affected: set[str] = set()
    for node in changed_nodes:
        affected |= graph.consumers_of(node)
    affected -= changed_nodes  # report consumers separately from the changed thing itself

    critical = sorted(n for n in affected if graph.meta.get(n, {}).get("critical"))
    return BlastRadius(
        changed_nodes=sorted(changed_nodes),
        affected=sorted(affected),
        critical_hit=critical,
    )
