"""Engine tests — run offline (heuristic mode, no API key needed)."""
import os

from blastradar import graph, radius, report, risk

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
