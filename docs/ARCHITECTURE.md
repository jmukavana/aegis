# Self-Healing Ops Platform — Architecture (codename: **Aegis**)

> Status: **Draft / on-paper design.** No code yet. Foundation-first: this document exists
> to agree on shape, boundaries, and safety model *before* anything is built.
> Name "Aegis" is a placeholder.

---

## 1. Problem & goals

Modern systems fail in ways that are (a) detectable from telemetry and (b) fixable with a
small, known set of actions — but a human still has to notice, diagnose, decide, and act,
usually at 3am. Aegis closes that loop: **detect problems automatically, reason about them
with an LLM, and remediate them safely** — with a human in the loop until trust is earned.

### In scope
- Detect: unhealthy services, crash loops, memory/resource leaks, failed deploys, SLO burn.
- Diagnose: correlate signals into a single incident with a probable root cause.
- Decide: pick a remediation (restart / scale / rollback / cordon / …) with a confidence.
- Act: execute *pre-approved, parameterized* remediations with guardrails and audit.
- Verify: confirm the signal actually recovered; escalate if not.

### Explicit non-goals (v1)
- Not a general "AI runs your cluster" system. The LLM **proposes**, it never freely executes.
- Not a replacement for Prometheus/Grafana/Alertmanager — Aegis sits *on top* of them.
- Not multi-cloud/multi-cluster at first. One cluster, one environment.
- No custom ML training pipeline initially — rules + anomaly detection + LLM reasoning.

### Design principles
1. **Safety over autonomy.** Every action is dry-runnable, audited, reversible, and gated.
2. **The loop is the product.** Detection without action is a dashboard; action without
   verification is a liability. The value is the *closed* loop.
3. **The LLM advises; policy decides; the actuator acts.** Three separate responsibilities,
   never collapsed into one.
4. **Everything is an artifact.** Remediations are versioned code (runbooks), not improvised
   LLM output. Incidents, decisions, and actions are all persisted and queryable.
5. **Earn trust incrementally.** Start read-only → human-approved → auto-execute low-risk →
   widen the allowlist as confidence data accumulates.

---

## 2. Reference model: MAPE-K control loop

Aegis is a **closed-loop autonomic controller** — the industry/academic reference for
self-healing systems is IBM's **MAPE-K** loop (Monitor, Analyze, Plan, Execute over shared
Knowledge). Mapping our components onto it keeps the design honest:

```
                          ┌─────────────────────────────────────────┐
                          │              KNOWLEDGE (shared)           │
                          │  service map · runbooks · incident store  │
                          │  SLOs & error budgets · policies · RAG    │
                          └─────────────────────────────────────────┘
                               ▲        ▲          ▲         ▲
              ┌────────────────┘        │          │         └───────────────┐
              │                         │          │                         │
        ┌──────────┐            ┌──────────────┐   │                 ┌──────────────┐
        │ MONITOR  │  signals   │   ANALYZE    │ incident   plan     │   EXECUTE    │
        │ ingest   ├───────────▶│ detect +     ├────────▶ ┌────────┐ │ guarded      │
        │ telemetry│            │ correlate +  │          │  PLAN  │ │ actuation    │
        │ + k8s    │            │ diagnose     │          │ decide ├▶│ + verify     │
        └──────────┘            └──────────────┘          └────────┘ └──────┬───────┘
              ▲                                                              │
              │                        acts on cluster / rolls back         │
              └──────────────────────────────────────────────────────────────┘
                                   (verification re-enters Monitor)
```

- **Monitor** = ingestion of metrics/logs/traces/events/changes.
- **Analyze** = detection + correlation + root-cause (LLM-assisted).
- **Plan** = remediation decision (LLM suggestion + retrieval + policy + confidence).
- **Execute** = guarded actuation + post-action verification.
- **Knowledge** = the shared state every stage reads/writes.

---

## 3. Components

### 3.1 Monitor / ingestion layer
- **OTel Collector** — traces, metrics, logs over OTLP (reuse the LGTM pattern already
  proven in the `Observability` project).
- **Kubernetes state watcher** — informers on Pods/Deployments/Events/Nodes; the ground-truth
  signal independent of app instrumentation (CrashLoopBackOff, OOMKilled, Pending, etc.).
- **Alert receiver** — Alertmanager webhook → normalized internal alert events.
- **Change/deploy feed** — CI/CD + GitOps (Argo/Flux) webhooks. *Most incidents follow a
  change*; treating deploys/config/flag changes as first-class signals is high-leverage.
- **Synthetic probes** (later) — active black-box checks as ground truth.

### 3.2 Knowledge / state layer
- **Telemetry backends**: Prometheus (or Mimir) · Loki · Tempo. Long-term signal store.
- **Service dependency graph** — derived from Tempo trace spans; used for (a) human context
  and (b) **blast-radius reasoning** by the planner. Store in Postgres or a graph store.
- **Incident store** (Postgres) — incident lifecycle, timeline, every action + outcome.
- **Runbook registry** — remediation actions *as versioned, reviewed code* (not LLM
  improv). Each runbook = { trigger contract, parameters, risk tier, dry-run impl, execute
  impl, undo impl, verification check }.
- **SLO / error-budget store** — SLO definitions and live burn-rate state.
- **Policy store** — what may auto-execute, where, at what confidence (see §5).
- **Vector store for RAG** — past incidents + runbooks embedded for retrieval. Recommend
  **pgvector** to avoid a new datastore — reuse Postgres.

- **Detectors** — rule-based (thresholds, k8s state) + anomaly (rate/EWMA/seasonal) +
  SLO burn-rate. Each emits candidate *signals*, not incidents.
- **Correlation engine** — group related signals across services/time into ONE incident.
  This is the feature that decides whether on-call trusts the system ("1 root cause" vs
  "20 alerts"). Three grouping strategies, applied in order:
  1. **Fingerprint dedup** — each signal gets a stable fingerprint
     (`service + failure-class + resource`, e.g. `checkout/oom/memory`); identical
     fingerprints within a window collapse into the same incident.
  2. **Topological grouping** — use the trace-derived **service map**: if `checkout` errors
     *because* its dependency `payments` is down, they are ONE incident rooted at `payments`,
     not two. Walk the dependency edges to find the upstream-most failing node.
  3. **Change correlation** — overlay recent deploy/config/flag-change events; a signal that
     begins within minutes of a change is attributed to that change (most incidents follow a
     change). Feeds RCA directly.
- **Root-cause analysis** — hand the correlated incident + topology + change timeline to the
  LLM, which produces a causal narrative and a probable root cause (see §3.4 for the contract).
- **Incident lifecycle manager** — open → diagnosing → remediating → verifying →
  resolved/escalated, with a full timeline. Every transition is an audited event.

> **Signals vs. incidents.** A *signal* is one detector firing (cheap, noisy, high-volume).
> An *incident* is a correlated, deduplicated, human-meaningful unit of work (what gets
> reasoned about, acted on, and paged). Keeping these as two distinct model concepts — with
> a many-signals-to-one-incident relationship — is the backbone of the whole data model.

### 3.4 Plan / decision engine (AI + policy core)
- **LLM reasoning service** — explains the incident, proposes a ranked remediation plan,
  and can **generate the concrete kubectl/API commands** — but only ever as *proposals*
  mapped to existing runbooks.
- **Retrieval (RAG)** — "this resembles INC-4471; runbook `rollback-deploy` fixed it."
- **Confidence scoring** — from signal strength + retrieval similarity + historical
  success rate of that runbook for that signature.
- **Policy engine (OPA-style)** — the actual decision gate: is this action allowed to
  auto-execute, in this environment, at this confidence, for this risk tier?
- **Blast-radius estimator** — before acting, reason over the dependency graph about what
  else the action touches; feeds the policy gate.

> **Where the LLM sits — and where it does NOT.** The LLM lives in Analyze (diagnosis) and
> Plan (proposal). It **never** has a direct path to Execute. Its output is a *structured
> proposal referencing a known runbook + parameters*, which the policy engine independently
> approves or rejects. This is the single most important boundary in the system: it bounds
> the blast radius of a hallucination to "suggested a bad but pre-vetted, reversible,
> rate-limited action" rather than "ran an arbitrary command as cluster-admin."

#### The LLM tool-use contract (concrete)
The model is never asked for prose to be parsed. It is given the incident context plus the
**catalog of available runbooks as tools**, and must return a structured proposal:
`{ runbook_id, params, reasoning, confidence }`. Validation happens before anything reaches
the policy gate: an unknown `runbook_id` or params that fail the runbook's schema are
rejected outright. So the worst case of a hallucination is "proposed a valid-but-wrong,
pre-vetted, reversible, rate-limited action" — never "emitted arbitrary `kubectl`."

#### Confidence scoring (concrete)
Confidence is a composite, not a vibe:
`confidence = signal_strength × retrieval_similarity × historical_success_rate(runbook, fingerprint)`.
The last term is the **flywheel**: every verified outcome (§3.6) updates that runbook's
success rate for that fingerprint, so actions *earn* their auto-execute privilege from real
data. This is the trust ratchet (§5) expressed as a number the policy gate can threshold on.

### 3.5 Execute / actuation layer
- **Runbook executor** — runs the chosen runbook's parameterized, **idempotent** action.
- **Kubernetes actuator** — restart / scale / rollback / cordon-drain / evict, via the
  k8s API (not shelling out to kubectl).
- **Dry-run / simulation** — every runbook implements dry-run; untrusted actions run
  dry-run first and surface the *predicted* effect for approval.
- **Circuit breaker on the actuator itself** — if N actions in a window don't resolve the
  incident, STOP and escalate. Prevents remediation loops / flapping.
- **Undo** — every runbook defines its inverse where possible; failed remediations roll back.

### 3.6 Verify loop
After acting, watch the triggering signal + SLO for a recovery window. Recovered → resolve
+ record success (feeds confidence scoring). Not recovered → next plan, or escalate, or trip
the breaker. **This is what makes it closed-loop rather than fire-and-forget.**

#### SLO monitoring (multi-window, multi-burn-rate)
SLOs are both a detector *and* the verification signal. Use the Google SRE
**multi-window multi-burn-rate** model rather than static thresholds: a fast-burn short
window (e.g. 5m/1h) catches acute outages and pages hard; a slow-burn long window
(e.g. 6h/3d) catches chronic degradation and pages soft. This yields far fewer false pages
than a single threshold, and — crucially — "did the error budget stop burning?" is exactly
the question the verify loop asks after a remediation. SLO definitions live in the Knowledge
layer; burn-rate state is recomputed continuously from the metrics backend.

### 3.7 Human interface & guardrails (cross-cutting)
- **ChatOps (Slack)** — incident + LLM explanation + proposed action + [Approve]/[Deny]/
  [Dry-run] buttons. The human-in-the-loop path.
- **Paging (PagerDuty/Opsgenie)** — escalation when confidence is low or the breaker trips.
- **Web UI** — incident timeline, action history, audit, runbook management (Grafana for
  telemetry + a custom incident/console app).
- **Audit log** — immutable record of {detected, reasoned (LLM I/O), decided, confidence,
  acted, approved-by, outcome} for *every* incident. Non-negotiable for trust and postmortems.
- **AuthZ** — **reuse the Keycloak/OIDC + dynamic-RBAC + permission model already built in
  the `Observability` project.** Permissions like `remediation:approve`, `runbook:manage`,
  `action:execute:prod` map cleanly onto that system. Environment-scoped: approve-in-staging
  ≠ approve-in-prod.

---

## 4. End-to-end flow (worked example: memory leak → OOM)

1. **Monitor**: container memory RSS trends up over hours; k8s watcher sees `OOMKilled`
   + restart count climbing on `checkout-service`.
2. **Analyze**: anomaly detector flags the RSS slope; correlation ties the OOM events +
   restarts + rising p99 latency into ONE incident. RCA notes no recent deploy → likely
   a genuine leak, not a regression.
3. **Plan**: RAG retrieves a similar past incident; LLM explains "steady RSS growth with
   periodic OOM, classic leak" and proposes ranked plan: (1) rolling-restart to reclaim
   memory now [risk: low], (2) bump memory limit [risk: low], (3) open a ticket for the
   leak [non-remediating]. Confidence high for (1) based on past success.
4. **Policy gate**: rolling-restart of a single deployment in this env, low risk, high
   confidence → within auto-execute allowlist. (In prod it might instead require Slack
   approval — policy-dependent.)
5. **Execute**: dry-run predicts "3 pods cycled, no downtime (surge=1)"; executor performs
   the rolling restart via the k8s API. Action + prediction audited.
6. **Verify**: RSS drops, OOM events stop, p99 recovers within the window → incident
   resolved, success recorded (raises future confidence). A follow-up ticket for the
   underlying leak is filed automatically with the telemetry attached.

---

## 5. Safety & guardrail model (the part that earns trust)

| Control | What it does |
|---|---|
| **Runbook allowlist** | Only pre-defined, reviewed actions can ever run. No free-form commands. |
| **Risk tiers** | Each runbook tagged low/med/high; higher tiers need higher confidence + approval. |
| **Environment scoping** | Auto-execute policy differs per env (staging vs prod). RBAC-enforced. |
| **Confidence threshold** | Below threshold → propose to a human, don't act. |
| **Dry-run first** | Untrusted actions show predicted effect before real execution. |
| **Circuit breaker** | Cap actions per incident/time window; trip → escalate to human. |
| **Reversibility** | Prefer runbooks with a defined undo; auto-rollback on failed remediation. |
| **Full audit** | Every reason + decision + action + outcome persisted immutably. |
| **Blast-radius check** | Estimate downstream impact from the service map before acting. |

Trust ratchet: an action starts *propose-only*, graduates to *dry-run + approve*, then
*auto-execute in staging*, then *auto-execute in prod* — promotion driven by its recorded
success rate, not a gut call.

### 5.1 Who watches the watcher
Aegis is itself a production system that can fail, flap, or make things worse. Self-guards:
- **Actuator circuit breaker** — N actions per incident/window → stop and page a human.
- **Dead-man's switch** — if Aegis stops heartbeating, fall back to normal human paging so a
  dead controller never silently swallows incidents.
- **Self-observability** — Aegis emits its own metrics/logs/traces into the same LGTM stack;
  you observe the observer, and its own SLOs are monitored like any other service.
- **Read-only default** — every environment starts with auto-execute *off*; actions are
  enabled per-runbook, per-environment, explicitly.

---

## 6. Language decision (ADR-0001)

**Status: Accepted.** **Decision: build v1 all-Python, with the cluster actuator isolated
behind a port so a Go implementation can be swapped in later without a rewrite.**

**Context.** The system has two hard parts that favor opposite languages. Kubernetes
actuation favors **Go** (`client-go`, `controller-runtime`, the entire k8s ecosystem). The
AI/analysis brain favors **Python** (first-class LLM clients — Anthropic, Ollama, OpenAI —
plus numpy/scipy/pandas/scikit-learn for detection, and it's the team's existing stack).
Neither is good at the other's job.

**Why all-Python for v1.** Phases 0–2 (foundation → detection → AI-assist) are *read-mostly*:
watch cluster state, build incidents, reason with the LLM. Heavy actuation doesn't arrive
until Phase 3. The Python k8s client is perfectly adequate for watching state and for the
simple API calls that actions require (restart/scale/rollback are not complex reconciliation).
One stack, one team context, full AI ecosystem for free.

**The consequence that makes this safe: the `Actuator` port.** The brain never touches the
k8s API directly — it calls an `Actuator` port (`dry_run(runbook, params)`, `execute(...)`,
`undo(...)`). v1 backs that port with a Python adapter. If Aegis later grows into a true
operator (CRDs, admission webhooks, level-triggered reconciliation) and wants Go, you replace
*only the adapter* — detection, AI, and policy are untouched. Put the boundary at the language
fault line so a future polyglot split is a swap, not a migration.

```
┌───────────────────────── Python "brain" ─────────────────────────┐
│ Monitor · Analyze · Plan · Policy · LLM · API · Incident store    │
└───────────────────────────────┬───────────────────────────────────┘
                                 │  Actuator port (interface / gRPC)
                                 ▼
        Python actuator adapter  ──(swap later)──▶  Go actuator "hands"
        (k8s Python client, v1)                     (client-go, if/when needed)
```

**Rejected alternatives.** *Go-first* — premature; pays polyglot cost before Phase 3 and
loses the Python AI ecosystem for the parts that are actually hard now. *Rust (kube-rs)* —
excellent and fast but steeper, smaller talent pool, thin AI ecosystem; no v1 value.
*Java/Kotlin (fabric8)* — mature client but heavy and weaker AI story.

### 6.1 Tech stack (following from the ADR)
- **Language**: **Python 3.13**, hexagonal layering (mirrors the `Observability` project
  conventions: implicit namespace packages, absolute `src.*` imports, manual composition root).
- **Actuator**: Python adapter via the official **`kubernetes`** client, behind the `Actuator`
  port. Go reserved for later, same contract.
- **LLM** (Phase 2+): pluggable behind the `IncidentAnalysisGateway` port — has been
  Anthropic-direct, then Claude-on-Vertex, and is **currently local Ollama**
  (`OLLAMA_URL`/`LLM_MODEL`, default `llama3.1`), talked to over `httpx`. Structured JSON
  output constrains `runbook_id` to the catalog enum so the model emits a *runbook reference +
  params*, never loose prose or an invented action — the safety comes from the schema + the
  port, not the provider, so swapping models is an adapter change. **RAG is implemented**
  (pgvector): a separate `EmbeddingGateway` port (`OllamaEmbeddingGateway`, `nomic-embed-text`,
  768-d) embeds incident symptoms, and an `IncidentMemory` port (pgvector adapter) recalls the
  nearest past incidents by cosine similarity to prime the prompt.
- **API/services**: FastAPI + uvicorn.
- **Telemetry**: LGTM (Loki/Grafana/Tempo/Prometheus) + OTel Collector.
- **State**: **Postgres** (incidents, runbooks, policies, SLOs) + **pgvector** (RAG, Phase 2)
  — one datastore, not five.
- **Policy** (Phase 3): **OPA / Rego** for the auto-execute decision gate.
- **Local dev cluster**: **kind** (per "start from scratch").
- **Messaging** (only if scale demands): NATS or Kafka between Monitor→Analyze→Plan.

---

## 7. Phased roadmap (foundation-first)

> Status (as of writing): Phase 0 ✅ done · Phase 1 ✅ done, driven end-to-end (API signals,
> detectors vs. live Prometheus, k8s watcher vs. minikube) · **Phase 2 ✅ done** (the
> `/incidents/{id}/explain` slice runs against local Ollama with pgvector RAG and Slack
> surfacing, all driven end-to-end) ·
> Phases 3–4 not started. Current provider/status detail lives in `CLAUDE.md`.

**Phase 0 — Foundation (no detection, no AI, no actions). ✅**
Stand up local k8s (kind) + OTel/LGTM pipeline + Postgres. Define the **incident data model**
and the **runbook contract** (interface only). Ingest telemetry + k8s events. Deliverable:
"we can observe everything and record an incident by hand." *This is the on-paper→code step.*

**Phase 1 — Detection & SLO (read-only). ✅**
Detectors + correlation engine + SLO/error-budget monitoring + alert ingestion. Produces
real incidents in the store. **No actions.** Deliverable: incidents open/close automatically
and are visible in the console; zero cluster mutation.

**Phase 2 — AI assist (read-only). ✅**
LLM incident explanation + RCA + ranked remediation *proposals* + RAG over history. Surface
in Slack. **Still nothing auto-executes.** Deliverable: on-call gets a diagnosis + suggested
runbook they can run manually. Lowest-risk way to validate LLM quality with real incidents.
*Built:* `ExplainIncident` + `IncidentAnalysisGateway` port + local-Ollama adapter +
`/incidents/{id}/explain`; **pgvector RAG** (`EmbeddingGateway` + `IncidentMemory` ports,
`OllamaEmbeddingGateway` + `PgVectorIncidentMemory` adapters — recall similar past incidents,
remember new ones); **ChatOps surfacing** (`IncidentNotifier` port + `SlackIncidentNotifier`
adapter — best-effort Block Kit post of the analysis, informational only, no execute
affordance). Next: Phase 3 guarded actuation.

**Phase 3 — Guarded actuation. 📐 designed (see [`PHASE3_DESIGN.md`](PHASE3_DESIGN.md)).**
Runbook registry + executor + dry-run + policy engine + circuit breaker + audit + RBAC.
**Correction to the original sketch:** Phase 3 requires **human approval for every** execution
— auto-execute (even in staging above a threshold) moves to Phase 4, so Phase 3 proves the
guarded pipeline with a human in the loop. Start with ONE low-risk reversible action
(`rolling-restart`) on `LOCAL`/minikube only; `PROD` disabled throughout. Deliverable: first
real *human-approved* remediation, fully audited, safely. Full design + sub-phases (3a real
dry-run · 3b policy/audit/approval/RBAC · 3c first real execution) in `PHASE3_DESIGN.md`.

**Phase 4 — Closed-loop verification + expansion.**
Post-action verification, undo/rollback, more failure modes (scale, rollback-deploy,
cordon-drain), blast-radius reasoning, trust-ratchet promotion to prod. Deliverable: the
platform as pitched — end to end, guarded.

---

## 8. Open questions (decide before/along the way)

1. **Cluster reality**: kind locally for v1 — but what's the eventual target (managed
   EKS/GKE/AKS, on-prem)? Affects the actuator's assumptions.
2. ~~**Language split**: accept Go+Python, or all-Python for v1?~~ **Decided — see ADR-0001
   (§6): all-Python v1, actuator behind a port, Go reserved for later.**
3. **Blast radius of auto-execute**: which single action do we trust first, and in which
   environment? (Recommendation: rolling-restart, staging only.)
4. **Human-in-the-loop channel**: Slack assumed — is that the right one? Teams? Custom UI?
5. **SLO source of truth**: define SLOs in Aegis, or import from an existing Sloth/OpenSLO
   setup?
6. **Data retention & privacy**: logs/traces fed to the LLM may contain sensitive data —
   what's the redaction/retention policy before anything leaves the cluster?
7. **Multi-tenancy**: one team/env for v1, but is multi-cluster/multi-team a near-term need
   that should shape the data model now?

---

## 9. What NOT to build first (anti-scope)

- Don't build a custom anomaly-detection ML pipeline before rules + LLM prove insufficient.
- Don't let the LLM generate free-form commands to execute — runbooks only, from day one.
- Don't auto-execute anything in prod until the trust ratchet + audit have real data.
- Don't build multi-cluster/multi-tenant before one cluster works end-to-end.
