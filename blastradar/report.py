"""Render the blast-radius report (Markdown) and decide the merge gate."""
from __future__ import annotations

from .radius import BlastRadius

EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def render(br: BlastRadius, risk: dict, *, gate_at: str = "high",
           notify: list[str] | None = None) -> tuple[str, bool]:
    """Return (markdown, gate_passed). gate fails when risk_level >= gate_at.

    `notify` is a list of CODEOWNERS handles for the affected downstream nodes.
    """
    order = ["low", "medium", "high"]
    passed = order.index(risk["risk_level"]) < order.index(gate_at)

    lines = [
        f"## {EMOJI[risk['risk_level']]} BlastRadar — risk: **{risk['risk_level'].upper()}**",
        "",
        f"> {risk['summary']}",
        "",
        f"**Changed infra:** {', '.join(br.changed_nodes) or '—'}",
        f"**Blast radius:** {len(br.affected_services)} service(s)"
        + (f" · ⚠️ critical: {', '.join(br.critical_hit)}" if br.critical_hit else ""),
        "",
    ]
    if br.affected_services:
        lines.append("**Downstream consumers that could break:**")
        lines += [f"- `{s}`" for s in br.affected_services]
        lines.append("")
    lines += [
        f"**Recommended rollout:** {risk['recommended_rollout']}",
    ]
    if notify:
        lines.append(f"**👥 Downstream owners — please review:** {' '.join(notify)}")
    lines += [
        "",
        ("✅ **Gate: PASS** — safe to merge under policy."
         if passed else
         f"⛔ **Gate: BLOCKED** — risk ≥ `{gate_at}`. Needs senior review / staged rollout sign-off."),
        "",
        f"<sub>assessed by `{risk.get('source', 'heuristic')}` · gate threshold = `{gate_at}`</sub>",
    ]
    return "\n".join(lines), passed
