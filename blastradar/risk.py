"""Risk assessment for a blast radius.

Two modes:
  * Heuristic (always available, no network) — deterministic score from the
    size of the radius and whether any critical consumer is hit.
  * Claude (when ANTHROPIC_API_KEY is set) — a plain-English explanation plus a
    recommended rollout strategy, grounded in the heuristic facts.

The heuristic always runs; Claude enriches it. This keeps the gate usable in CI
without an API key, and testable offline.
"""
from __future__ import annotations

import json
import os

from .radius import BlastRadius

LEVELS = ["low", "medium", "high"]


def heuristic(br: BlastRadius, high_threshold: int = 3) -> dict:
    n = len(br.affected_services)
    if br.critical_hit or n >= high_threshold:
        level = "high"
    elif n >= 1:
        level = "medium"
    else:
        level = "low"
    canary = {"high": "5% → 25% → 50% → 100% with bake time", "medium": "10% → 50% → 100%",
              "low": "standard rolling update"}[level]
    return {
        "risk_level": level,
        "summary": (f"{n} downstream service(s) affected"
                    + (f"; CRITICAL consumers hit: {', '.join(br.critical_hit)}" if br.critical_hit else "")
                    + "." if br.affected_services else "No external consumers affected."),
        "recommended_rollout": canary,
        "owners_to_notify": br.affected_services,
        "source": "heuristic",
    }


def assess(br: BlastRadius, changed_files: list[str], high_threshold: int = 3) -> dict:
    base = heuristic(br, high_threshold)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return base
    try:
        return _claude(br, changed_files, base)
    except Exception as exc:  # never let the LLM break the gate — fall back
        base["llm_error"] = str(exc)
        return base


def _claude(br: BlastRadius, changed_files: list[str], base: dict) -> dict:
    import anthropic

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "risk_level": {"type": "string", "enum": LEVELS},
            "summary": {"type": "string"},
            "recommended_rollout": {"type": "string"},
            "owners_to_notify": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["risk_level", "summary", "recommended_rollout", "owners_to_notify"],
    }
    facts = {
        "changed_files": changed_files,
        "changed_infra_nodes": br.changed_nodes,
        "transitively_affected": br.affected,
        "affected_services": br.affected_services,
        "critical_consumers_hit": br.critical_hit,
        "heuristic": {k: base[k] for k in ("risk_level", "recommended_rollout")},
    }
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1500,
        thinking={"type": "adaptive"},
        system=(
            "You are a release-risk reviewer for infrastructure changes. Given the precomputed "
            "blast radius of a pull request, explain in 2-4 sentences what could break downstream "
            "and why, then recommend a concrete rollout strategy. Be specific about which services "
            "carry risk. Do not understate risk when critical consumers are hit. Ground every claim "
            "in the provided facts — do not invent services that aren't listed."
        ),
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": "Assess this change:\n" + json.dumps(facts, indent=2)}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    out = json.loads(text)
    out["source"] = "claude-opus-4-8"
    return out
