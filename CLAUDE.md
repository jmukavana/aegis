# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## What this is

**Aegis** — a self-healing operations platform: automatically detect, diagnose, and
(eventually) remediate production problems (restart unhealthy services, scale pods, roll back
failed deploys, catch memory leaks), with an LLM that explains incidents and proposes
remediations behind strict guardrails.

**The design is the source of truth: read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
first.** It defines the MAPE-K control loop, the safety/guardrail model, the LLM boundary,
and the language decision (ADR-0001). Code should not contradict it; if it needs to, update
the doc in the same change. Phase 3's actuation/guardrail design is detailed in
[`docs/PHASE3_DESIGN.md`](docs/PHASE3_DESIGN.md) — **sub-phase 3a is done** (real server-side
dry-run driven live on GKE); **3b done** (policy gate + `RemediationPlan`/`AuditLog` persistence
+ `PlanRemediation`/`ApproveRemediation` + RBAC-gated approval API with segregation-of-duty;
**real Keycloak/OIDC auth now wired** — `AUTH_PROVIDER=header|keycloak`, default `header`
bootstrap; see "AuthN/Z" below); **3c core done,
driven live on minikube** (`ExecuteRemediation` + `CircuitBreaker` + `OutcomeStore` + per-env/
per-runbook enablement allowlist + `POST /remediations/{id}/execute` — a human-approved
rolling-restart actually recreated pods); **3c loop closed** (`VerifyRemediation` +
`RecoveryProbe`/`SignalQuietRecoveryProbe` + `POST /remediations/{id}/verify`: recovered→resolve
+record success, else→escalate+record failure; the *verified* outcome feeds `OutcomeStore`).
**Mutation is possible but tightly gated:** `ACTUATION_ENABLED` (default false) +
`ACTUATION_ENVIRONMENTS`/`ACTUATION_RUNBOOKS` allowlists + approval + circuit breaker; `PROD`
and all non-allowlisted runbooks stay refused. Phase 4: a runbook *earns* `ALLOW_AUTO` from its
`OutcomeStore` success rate (auto-execute without a human); plus durable breaker/outcomes and a
metrics/SLO `RecoveryProbe`.

## Current status: Phase 4c in progress — fully autonomous closed loop with durable trust. The flywheel (`OutcomeStore`) and circuit breaker are now Postgres-backed (migration 0005), so earned autonomy survives restarts/replicas (proven live: seeded trust → restart → still auto-executes). The Phase 4b loop (`ENABLE_AUTO_REMEDIATION_LOOP`, default off) sweeps open incidents, deterministically selects a runbook, and drives the whole MAPE-K loop with NO human — auto-healing runbooks that have earned trust (`ALLOW_AUTO`), leaving the rest for a human. See [`docs/PHASE4_DESIGN.md`](docs/PHASE4_DESIGN.md). Still gated: default-off switches + env allowlist (never prod) + earned success-rate threshold + all Phase 3 guards. Recovery is pluggable behind `RecoveryProbe` (`RECOVERY_PROBE=signal_quiet|metrics`): the metrics probe judges recovery from the service's Prometheus error ratio (SLO stopped burning), not just silence. Blast radius is now a policy input: `PlanRemediation` computes the transitive downstream services an action disrupts (via `ServiceGraph.dependents_of`) and even a trusted runbook falls back to human approval above its environment's blast-radius cap. Autonomy is now staged per environment (`RuleBasedPolicyEngine` takes a `dict[Environment, AutoThreshold]`): `local` has a light, on-by-default bar; `staging` is off by default and, once enabled, demands a much stricter track record; `prod` has no tier at all and is denied structurally regardless of config or trust — verified live (same track record auto-executed in local but was rejected in staging until it earned staging's own stricter bar; prod stayed denied even at a perfect 5/5). `rollback-deployment` is now a real executable too (reads a Deployment's actual ReplicaSet revision history via `ClusterClient.list_replica_set_revisions` and patches `spec.template` back — the `kubectl rollout undo` mechanism), registered in `StaticRunbookRegistry` alongside rolling-restart/scale-deployment; being MEDIUM risk it structurally can never auto-execute (Phase 4 auto is LOW-risk only), so it's approve-and-execute only, verified live (genuinely rolled an nginx image back on minikube). Phase 4c is now closed. **Real Keycloak/OIDC auth is wired** for the guarded remediation endpoints (`AUTH_PROVIDER=keycloak`, ported from the sibling `Observability` project) — verified live (401/403 enforcement, real cryptographic subject UUIDs on every audit line, segregation-of-duty against real identities). See "AuthN/Z" below.

The roadmap (ARCHITECTURE.md §7) is phased on purpose. **Everything through Phase 2 is
read-only — the LLM only *proposes*, nothing auto-executes and nothing touches a cluster.**
Do not add runbook *implementations*, a policy engine, or real actuation yet — those are
Phases 3–4.

- **Phase 0 (done):** incident data model, `Runbook`/`Actuator` contracts (interfaces only),
  no-op actuator, by-hand incident API, OTel wiring, local infra stack.
- **Phase 1 (done, driven end-to-end):** correlation engine (`application/correlation`,
  fingerprint→topological→change grouping), incident lifecycle + auto-resolve reconcile loop
  (`application/incident/lifecycle.py` + `infrastructure/reconcile.py`, wired via FastAPI
  lifespan), SLO multi-window burn-rate (`domain/slo`), detectors + `MetricsGateway`/Prometheus
  adapter, and the Kubernetes event watcher (`infrastructure/kubernetes/event_watcher.py` +
  `src/watch_events.py`, run `KUBE_CONTEXT=minikube uv run python -m src.watch_events`;
  it now skips `kube-system`/`kube-public`/`kube-node-lease` control-plane noise).
- **Phase 2 (done):** LLM incident explanation + RCA + ranked remediation *proposals*,
  retrieval-augmented over past incidents, surfaced to Slack. `POST /incidents/{id}/explain` →
  `ExplainIncident`
  (`application/analysis`) → `IncidentAnalysisGateway` port. **Provider is selectable via
  `LLM_PROVIDER`**: `ollama` (local, `infrastructure/llm/ollama_gateway.py`,
  `OLLAMA_URL`/`LLM_MODEL`) or `vertex` (Vertex AI Llama MaaS,
  `infrastructure/llm/vertex_llama_gateway.py`, OpenAI-compatible endpoint, ADC bearer-token,
  `VERTEX_PROJECT`/`VERTEX_LOCATION`/`VERTEX_MODEL` — the Llama model must be enabled in Model
  Garden for the project). Both share `infrastructure/llm/prompting.py` (system prompt, the
  `runbook_id`-enum schema, reply parsing) so the enum constraint lives in one place; the use
  case additionally drops any proposal referencing an unknown runbook.
  - **RAG (done):** before explaining, `ExplainIncident` recalls the most similar past
    incidents from an `IncidentMemory` port (pgvector adapter,
    `infrastructure/persistence/incident_memory.py`) and injects their prior root causes into
    the prompt as *advisory hints*; afterwards it remembers this incident + its analysis.
    Embeddings come from a separate `EmbeddingGateway` port (`OllamaEmbeddingGateway`,
    `/api/embed` with `nomic-embed-text`, 768-d — must match `EMBED_DIM` / migration 0003).
    Similarity is cosine over the incident *symptoms* (title + fingerprint + signal summaries),
    not the diagnosis, so a fresh incident lands near past ones with matching symptoms.
    Requires the `pgvector/pgvector:pg16` Postgres image (not `postgres:16-alpine`). Config:
    `ENABLE_INCIDENT_MEMORY` (default true), `EMBEDDING_MODEL`, `RAG_RECALL_LIMIT` (default 3).
    Memory is optional — with no embedder the explainer still runs, just without cross-incident
    context.
  - **ChatOps (done):** after explaining, the route surfaces the analysis to Slack via an
    `IncidentNotifier` port (`SlackIncidentNotifier`, Incoming Webhook + Block Kit,
    `infrastructure/notification/slack_notifier.py`) — an informational message only (root
    cause + ranked runbooks + "nothing executed" footer), **no execute affordance** (that's
    Phase 3). Delivery is best-effort: a Slack failure logs a warning and never fails
    `/explain`. Config: `SLACK_WEBHOOK_URL` (empty disables surfacing).

## Key architectural decisions (don't relitigate without updating the ADR)

- **All-Python for v1** (ADR-0001). The k8s actuator is isolated behind the `Actuator` port
  (`src/domain/actuator/port.py`) so a Go implementation can replace *only that adapter*
  later. **The brain must never touch the Kubernetes API directly — always go through the
  `Actuator` port.**
- **Signals vs. incidents are distinct model concepts** with a many-to-one relationship. A
  `Signal` is one noisy detector firing; an `Incident` is the correlated, human-meaningful
  unit. Keep them separate.
- **The LLM proposes, policy decides, the actuator acts** (Phase 2+). The LLM only ever emits
  a structured `{runbook_id, params, ...}` referencing an existing runbook — never free-form
  commands to execute. This boundary is non-negotiable. The LLM lives behind the
  `IncidentAnalysisGateway` **port** (`domain/analysis`), so the provider is swappable without
  touching the application layer — it has already been Anthropic-direct, then Claude-on-Vertex,
  now **local Ollama**; the schema-level `runbook_id` enum + use-case validation are what make
  any provider safe, not the provider itself.
- **Remediations are versioned code (runbooks), not LLM improvisation.** Phase 2 ships the
  *catalog* of runbook specs (`domain/runbook/spec.py`) the LLM references; executable
  implementations arrive in Phase 3.

## Conventions

Mirror the sibling `Observability` project:

- **No `__init__.py` anywhere under `src/`** — implicit namespace packages. `[tool.mypy]`
  sets `explicit_package_bases = true` and `[tool.pytest.ini_options]` sets
  `pythonpath = ["."]` so absolute `from src.foo.bar import ...` imports resolve.
- **Hexagonal layering**: `src/domain` (entities, value objects, ports — no I/O),
  `src/application` (use cases depending only on ports), `src/infrastructure` (adapters).
- **Manual composition root** in `src/main.py` — build collaborators once, pass them down as
  explicit parameters/closures. No DI container.
- **Observability wiring**: call `configure_tracing()` BEFORE `configure_logging()` so
  `LoggingInstrumentor` captures the tracer provider (with its `service.name` resource) for
  log↔trace correlation. Both tracer and meter providers must be built with
  `get_resource()` (`src/infrastructure/observability/resource.py`).
- **Migrations are hand-written** (no autogenerate — no live DB assumed in dev/CI).

## Commands

```bash
uv sync
docker compose up -d                 # Postgres + LGTM + OTel Collector (ports offset: 5xxx / 15432)
uv run alembic upgrade head
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

uv run pytest                        # domain + application unit tests (no DB/cluster needed)
uv run pytest path::test_name        # single test
uv run ruff check .                  # lint
uv run black .                       # format
uv run mypy src                      # type-check
```

Ports are deliberately offset (API `:8000`, Grafana `:5000`, Prometheus `:5090`, Tempo
`:5200`, Loki `:5100`, OTel `:5317`/`:5318`, Postgres `:15432`, Keycloak `:5080`) so this
project can run alongside the separate `Observability` project without clashing.

### AuthN/Z for the guarded remediation endpoints

`POST /incidents/{id}/remediations`, `/remediations/{id}/approve`, and
`/remediations/{id}/execute` require an `Identity` (subject + permissions), resolved by
`AUTH_PROVIDER` (default `header`):

- **`header`** (default, dev/test bootstrap) — trusts `X-Subject` / comma-separated
  `X-Permissions` request headers outright. No external dependency, zero setup, but the
  identity is whatever the caller claims — never use this for anything but local iteration.
- **`keycloak`** — real, signature-verified bearer JWTs. `docker compose up -d keycloak`
  auto-provisions realm `aegis` from `configs/keycloak/realm-export.json` (client `aegis-api`,
  demo users `planner-demo`/`approver-demo`/`operator-demo` — dev-only fixture, plaintext
  passwords, mirrors the sibling `Observability` project's Keycloak setup). Realm roles ARE the
  permission strings directly (`remediation:plan`, `action:execute:local`, …) — no separate
  local Role table, since Aegis's permissions were already free-form strings.

```bash
docker compose up -d keycloak
TOKEN=$(curl -s -X POST http://localhost:5080/realms/aegis/protocol/openid-connect/token \
  -d grant_type=password -d client_id=aegis-api \
  -d username=planner-demo -d password=planner-demo | jq -r .access_token)
AUTH_PROVIDER=keycloak uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
curl -s -X POST http://localhost:8000/incidents/<id>/remediations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"runbook_id":"rolling-restart","environment":"local","params":{"namespace":"default","deployment":"nginx"}}'
```

Config: `OIDC_ISSUER` (default `http://localhost:5080/realms/aegis`), `OIDC_AUDIENCE`
(default `aegis-api`), derived `oidc_jwks_url`. Switching providers changes nothing downstream
(RBAC checks, audit) — both produce the same `Identity(subject, permissions)` shape consumed
by `PlanRemediation`/`ApproveRemediation`/`ExecuteRemediation`.

### CI/CD and deployment

Source lives at `github.com/jmukavana/aegis` (private). `.github/workflows/ci-cd.yml`:
- **`test`** (every push/PR): `uv sync` → `ruff check` → `mypy src` → `pytest`.
- **`deploy`** (push to `main` only, after `test` passes): builds the `Dockerfile` image, pushes
  it to Artifact Registry, and `gcloud run deploy`s it to Cloud Run in GCP project
  `aegis-501206` — the same project used for the Phase 3a/4 GKE/Vertex live drives earlier.
  Auth is **Workload Identity Federation** (`google-github-actions/auth`) — the runner
  authenticates as `aegis-deployer@aegis-501206.iam.gserviceaccount.com` via a short-lived
  token exchanged for its GitHub OIDC identity; no service-account key is stored as a secret.
  The pool/provider is attribute-conditioned to `assertion.repository=='jmukavana/aegis'`, so
  only this repo can impersonate the deployer.
- The deployed service runs as its own minimal-privilege runtime identity
  (`aegis-runtime@aegis-501206.iam.gserviceaccount.com`, `roles/aiplatform.user` +
  `roles/cloudsql.client` only — no Kubernetes/actuation access from Cloud Run), backed by a
  small Cloud SQL Postgres instance (`aegis-db`, `db-f1-micro`) reached over the Cloud SQL Unix
  socket (`--add-cloudsql-instances`). `LLM_PROVIDER=vertex` — Vertex AI Llama MaaS "just works"
  in this environment via ADC, no Ollama container needed. `ENABLE_INCIDENT_MEMORY=false` in
  this deployment (Phase 2 RAG needs an embedding model; no Ollama is reachable from Cloud
  Run — would need a hosted embedding endpoint to re-enable). GitHub repo vars: `GCP_PROJECT_ID`,
  `GCP_REGION`, `ARTIFACT_REPO`, `CLOUD_RUN_SERVICE`, `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`,
  `RUNTIME_SERVICE_ACCOUNT`; one secret, `DB_PASSWORD`.
- Ingress is public/unauthenticated (`--allow-unauthenticated`) — the guarded remediation
  write-paths (`/remediations/*`) still enforce `Identity`/RBAC internally regardless, and
  `ACTUATION_ENABLED`/`AUTO_EXECUTE_ENABLED` are unset (default off) in this deployment, so no
  cluster is reachable or mutable from it. This is a demo/portfolio deployment, not a production
  on-call system — treat it accordingly (e.g. the Cloud SQL instance has no backups enabled).

### Phase 2 LLM (Ollama)

`POST /incidents/{id}/explain` requires a local Ollama reachable at `OLLAMA_URL`:

```bash
# one-time: install Ollama, then pull the chat model (LLM_MODEL, default llama3.1) and the
# embedding model used for RAG (EMBEDDING_MODEL, default nomic-embed-text).
ollama pull llama3.1
ollama pull nomic-embed-text
# create an incident (Phase 1 flow), then explain it:
curl -s -X POST http://localhost:8000/incidents/<incident-id>/explain | jq
```

If `OLLAMA_URL` is empty the endpoint returns 503; if it's set but Ollama is unreachable the
call errors on connect. RAG needs the `pgvector/pgvector:pg16` image + `alembic upgrade head`
(migration 0003 does `CREATE EXTENSION vector` and builds `incident_memory`); set
`ENABLE_INCIDENT_MEMORY=false` to run the explainer without it. Unit tests never touch Ollama
or Postgres — the gateway/embedder/memory are faked, and both Ollama adapters are pinned with
`httpx.MockTransport` learning tests.

## Origin

This project began as an architecture-first design (Phase 0 scaffold) split off from the
`Observability` FastAPI/LGTM demo. The auth/RBAC model built there (Keycloak/OIDC + dynamic
permissions) is the intended AuthZ layer for Aegis's approval/execution paths later
(`remediation:approve`, `action:execute:prod`, etc.) — see ARCHITECTURE.md §3.7.
