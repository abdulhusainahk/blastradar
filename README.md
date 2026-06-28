# 💥 BlastRadar

**An infra-aware, pre-merge change-risk gate.** It tells you what an
infrastructure PR will actually *break downstream* — before it merges.

> AI coding tools pushed **change-failure rates up ~30%** and **incidents per PR up 23.5%**
> ([Cortex 2026 Benchmark](https://riftmap.dev/blog/ai-doesnt-understand-blast-radius/)).
> The reason: AI (and humans) optimize for *local* correctness in one repo but can't see the
> *cross-repo* dependency graph. Green CI ≠ safe. BlastRadar is the missing guardrail.

Most "AI for reliability" tooling is **reactive** — it triages and heals incidents *after*
deploy. BlastRadar is **proactive**: it computes the blast radius of a change *before* merge,
focused on the **infrastructure** graph (Terraform / Helm / Kubernetes / containers) that
code-centric tools miss.

---

## What it does

On a pull request that touches IaC, BlastRadar:

1. **Builds a cross-repo infra dependency graph** — Terraform module consumers, Helm chart
   deps, K8s ConfigMap/Secret → workload references, container base images, plus declarative
   cross-repo edges from `consumers.yaml`.
2. **Computes the real blast radius** of the diff — every downstream service/repo that
   transitively depends on what changed.
3. **Assesses risk** — a deterministic heuristic always runs; with `ANTHROPIC_API_KEY` set,
   **Claude** (`claude-opus-4-8`) writes a plain-English explanation + a recommended rollout.
4. **Gates the merge** — posts a PR comment + check, and **fails** above your risk threshold.

```
PR diff ─▶ detect IaC ─▶ build infra graph ─▶ reverse-reachability ─▶ risk + rollout ─▶ gate
                                              (who consumes what?)     (heuristic|Claude)  (PR check)
```

## Try it locally (no API key needed)

```bash
pip install -r requirements.txt

# HIGH: a shared Terraform module → 3 services + a cross-repo consumer
python -m blastradar --root examples/demo-monorepo \
  --consumers examples/demo-monorepo/consumers.yaml \
  --changed modules/network/main.tf

# LOW: a leaf service nobody depends on → safe to merge
python -m blastradar --root examples/demo-monorepo --changed services/checkout/main.tf
```

Set `ANTHROPIC_API_KEY` to get the Claude-written narrative instead of the bare heuristic.

## Use it as a GitHub Action

```yaml
- uses: abdulhusainahk/blastradar@v0
  with:
    root: "."
    consumers: "consumers.yaml"
    gate-at: "high"     # block merge at this risk level or above
```

It diffs against the PR base, posts the report as a PR comment, and fails the check when the
gate blocks. See [`.github/workflows/blastradar.yml`](.github/workflows/blastradar.yml) (which
dogfoods this repo's demo monorepo).

## `consumers.yaml` — the cross-repo edges parsing can't see

```yaml
edges:
  - consumer: repo:mobile-bff       # another repo that imports the shared module
    depends_on: tfmodule:network
critical:
  - service:payments                # hitting these escalates risk to HIGH
```

This declarative registry is what closes the gap that lets AI-authored PRs ship "high blast
radius" changes — it makes out-of-repo consumers visible to the gate.

## Roadmap

- **v0.1** — Terraform/Helm/K8s/Docker parsing, blast-radius gate, heuristic + Claude, GitHub Action *(this release)*
- **v0.2** — auto-crawl an org's repos to build `consumers.yaml` instead of hand-maintaining it
- **v0.3** — downstream-owner auto-notification + CODEOWNERS-aware reviewer escalation
- **v0.4** — MCP tool wrapper ("what's the blast radius of bumping module X?") + risk trend dashboard

## License

MIT — see [LICENSE](LICENSE).

---

Built by **Abdulhussain Kanchwala** · [Portfolio](https://abdulhussaink.netlify.app) ·
[GitHub](https://github.com/abdulhusainahk)
