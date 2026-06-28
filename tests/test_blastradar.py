"""Engine tests — run offline (heuristic mode, no API key needed)."""
import os

from blastradar import crawl, graph, radius, report, risk

ROOT = os.path.join(os.path.dirname(__file__), "..", "examples", "demo-monorepo")
CONSUMERS = os.path.join(ROOT, "consumers.yaml")


def _graph():
    return graph.build_graph(ROOT, CONSUMERS)


def test_shared_module_has_all_consumers():
    g = _graph()
    consumers = g.consumers_of("tfmodule:network")
    assert {"service:checkout", "service:stream", "service:payments", "repo:mobile-bff"} <= consumers


def test_changing_shared_module_is_high_risk():
    g = _graph()
    br = radius.compute(g, ["modules/network/main.tf"], ROOT)
    assert "service:payments" in br.affected_services
    assert br.critical_hit                       # payments + mobile-bff are flagged critical
    r = risk.heuristic(br)
    assert r["risk_level"] == "high"
    md, passed = report.render(br, r, gate_at="high")
    assert passed is False and "BLOCKED" in md


def test_leaf_service_change_is_low_risk():
    g = _graph()
    br = radius.compute(g, ["services/checkout/main.tf"], ROOT)  # nobody depends on checkout
    assert br.affected_services == []
    r = risk.heuristic(br)
    assert r["risk_level"] == "low"
    _, passed = report.render(br, r, gate_at="high")
    assert passed is True


def test_configmap_change_hits_its_deployment():
    g = _graph()
    br = radius.compute(g, ["k8s/stream-config.yaml"], ROOT)
    assert "service:stream-web" in br.affected_services
    assert risk.heuristic(br)["risk_level"] == "medium"


def test_owner_notification_for_affected_nodes():
    g = _graph()
    br = radius.compute(g, ["modules/network/main.tf"], ROOT)
    handles = graph.owners_of(g, br.affected)
    assert "@demo-org/mobile-team" in handles      # mobile-bff is in the blast radius
    _, _ = report.render(br, risk.heuristic(br), gate_at="high", notify=handles)


def test_git_source_keying_is_repo_qualified():
    # Cross-repo git source -> (owner/repo, subdir); key never collides on bare name.
    assert graph.parse_git_source("git::https://github.com/orgB/tf-modules//modules/network?ref=v1") \
        == ("orgB/tf-modules", "modules/network")
    assert graph.tf_producer("git::https://github.com/orgB/tf-modules//modules/network?ref=v1",
                             "services/x/main.tf", "orgA/web") == "tfmodule:orgB/tf-modules//modules/network"
    # Two different "network" modules in different repos get distinct keys.
    a = graph.tf_producer("../../modules/network", "services/x/main.tf", "orgA/web")
    b = graph.tf_producer("../../modules/network", "services/x/main.tf", "orgB/api")
    assert a == "tfmodule:orgA/web//modules/network" and b == "tfmodule:orgB/api//modules/network" and a != b


def test_crawler_parses_consumed_producers():
    tf = 'module "n" {\n  source = "git::https://github.com/org/mods//vpc?ref=v1"\n}'
    assert crawl._producers_consumed("services/x/main.tf", tf, "org/web") == ["tfmodule:org/mods//vpc"]
    chart = "apiVersion: v2\nname: x\ndependencies:\n  - name: common\n    version: 1.0.0\n"
    assert crawl._producers_consumed("charts/x/Chart.yaml", chart, "org/web") == ["helmchart:common"]


def test_qualified_gate_finds_cross_repo_consumer():
    # An org-graph edge: orgA/web consumes orgB/tf-modules//modules/network.
    g = graph.InfraGraph()
    g.add_edge("repo:orgA/web", "tfmodule:orgB/tf-modules//modules/network")
    g.add_node("repo:orgA/web", owners=["@orgA/web-team"])
    # A PR in orgB/tf-modules edits that module -> the cross-repo consumer is in the radius.
    br = radius.compute(g, ["modules/network/main.tf"], ".", repo_slug="orgB/tf-modules")
    assert "repo:orgA/web" in br.affected
    assert graph.owners_of(g, br.affected) == ["@orgA/web-team"]


def test_merge_unions_graphs():
    merged = crawl.merge([
        {"edges": [{"consumer": "repo:a", "depends_on": "tfmodule:x"}], "owners": {"repo:a": ["@a"]}},
        {"edges": [{"consumer": "repo:a", "depends_on": "tfmodule:x"},      # dup -> deduped
                   {"consumer": "repo:b", "depends_on": "tfmodule:y"}],
         "owners": {"repo:a": ["@a", "@a2"], "repo:b": ["@b"]}, "critical": ["repo:b"]},
    ])
    assert len(merged["edges"]) == 2
    assert merged["owners"]["repo:a"] == ["@a", "@a2"]
    assert merged["critical"] == ["repo:b"]
