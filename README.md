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

## Set it up for a GitHub **organization** (v0.2)

At org scale you don't hand-write `consumers.yaml` — the crawler builds it from every repo.

**1. Central config repo — crawl the org nightly.** Create `<org>/blastradar-config` and add
[`examples/org-setup/01-crawl-nightly.yml`](examples/org-setup/01-crawl-nightly.yml). It runs:

```bash
GITHUB_TOKEN=... python -m blastradar crawl --org <org> --out org-graph.yaml
```

The crawler lists every repo, parses its IaC (Terraform module sources incl. cross-repo
`git::` refs, Helm deps, container bases), and reads each repo's **CODEOWNERS**, producing an
`org-graph.yaml` of `repo:<x> → producer` edges + owners. It commits that file back.

**2. Per-repo gate.** Add [`examples/org-setup/02-gate-in-each-repo.yml`](examples/org-setup/02-gate-in-each-repo.yml)
to each repo you want gated. On every PR it pulls `org-graph.yaml`, computes the blast radius,
comments the report, **@-mentions the downstream owners**, and **fails the check on HIGH**.

```yaml
- uses: abdulhusainahk/blastradar@v0
  with: { root: ".", consumers: ".blastradar/org-graph.yaml", gate-at: "high" }
  env:  { ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }} }   # optional narrative
```

**3. Secrets/permissions:** an org-read token (or GitHub App install token) as
`BLASTRADAR_ORG_READ_TOKEN`, the gate job needs `pull-requests: write`, and optionally
`ANTHROPIC_API_KEY` for Claude's narrative. Make the check **required** in branch protection
to actually block merges.

### Multiple organizations

```bash
# One token that can read all the orgs:
python -m blastradar crawl --orgs org-a,org-b,org-c --out org-graph.yaml

# Or per-org tokens (run each separately), then merge the outputs:
python -m blastradar crawl --org org-a --out a.yaml   # token A
python -m blastradar crawl --org org-b --out b.yaml   # token B
python -m blastradar merge a.yaml b.yaml --out org-graph.yaml
```

A change in `org-b/tf-modules` then surfaces consumers in `org-a` *and* `org-c`, and @-mentions
each repo's CODEOWNERS. The only hard requirement is **read access to every org** (one token, or
per-org tokens + `merge`).

### How keys avoid collisions (source-URL keying)

Cross-repo Terraform identities are **repo+path-qualified**, not name-only — a module referenced
as `git::https://github.com/orgB/tf-modules//modules/network` becomes
`tfmodule:orgB/tf-modules//modules/network`. So two different modules both named `network` in
different repos/orgs never get confused. This requires the gate to know its own repo: the
[Action](action.yml) passes `--repo ${{ github.repository }}` automatically. (The local
hand-written demo runs in simpler name-mode — see below.)

### Validate it on GitHub
1. Open a PR in a gated repo that edits a **shared** artifact (a Terraform module others use).
2. The `blastradar` check runs → a comment appears: risk level, the exact downstream services,
   recommended rollout, and `@owner` mentions.
3. On HIGH the check is **red** → with branch protection on, **Merge is blocked** until a senior
   review / staged-rollout sign-off.
4. Open a second PR touching only a **leaf** file → green check, merge allowed. That contrast is
   the demo.

This repo's own [`.github/workflows/blastradar.yml`](.github/workflows/blastradar.yml) dogfoods
the gate against the demo monorepo, so the first PR you open here shows it live.

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

- **v0.1** — Terraform/Helm/K8s/Docker parsing, blast-radius gate, heuristic + Claude, GitHub Action
- **v0.2** — ✅ org-wide crawler + ✅ CODEOWNERS @-mentions + ✅ **multi-org crawl/merge** + ✅ **source-URL-qualified keys** *(this release)*
- **v0.3** — GitHub App auth (no PATs), webhook-triggered incremental crawl + caching, downstream-repo issue/check creation
- **v0.4** — live runtime-impact fusion (weight risk by current traffic / error-budget burn) + MCP tool wrapper + risk-trend dashboard

## License

MIT — see [LICENSE](LICENSE).

---

Built by **Abdulhussain Kanchwala** · [Portfolio](https://abdulhussaink.netlify.app) ·
[GitHub](https://github.com/abdulhusainahk)
