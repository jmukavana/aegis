# Aegis — Self-Healing Ops Platform

A platform that automatically **detects, diagnoses, and (eventually) fixes** production
problems — restart unhealthy services, scale pods, roll back failed deploys, catch memory
leaks — with an LLM that explains incidents and proposes remediation, behind strict
guardrails.

> **Design first.** The full architecture (MAPE-K control loop, safety model, LLM boundary,
> language decision) lives in **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**. Read that
> before the code.

## Where this is: Phase 0 (foundation)

Per the phased roadmap, Phase 0 is **foundation only — no detection, no AI, no cluster
actions.** What exists today:

- **Incident data model** — `Signal` (one detector firing) and `Incident` (a correlated,
  human-meaningful unit of work), the `signals → incident` relationship, and a `Fingerprint`
  value object (`service/failure_class/resource`).
- **Contracts (interfaces only)** — the `Runbook` remediation contract, `RunbookRegistry`,
  and the `Actuator` port that is the *single seam* between the Python brain and a cluster
  (see ADR-0001 in the architecture doc). Phase 0 ships a `NoopActuator` that refuses to
  mutate anything.
- **A minimal incident API** — record / list / transition incidents *by hand* (`POST`,
  `GET`, `PATCH /incidents/{id}/status`). This is the Phase 0 deliverable: "we can observe
  everything and record an incident by hand."
- **Observability wired in** — OTel traces/metrics/logs with a `service.name` resource and
  log↔trace correlation, exporting to a local LGTM stack.
- **Foundation infra** — Postgres, LGTM stack (Loki/Grafana/Tempo/Prometheus) + OTel
  Collector via docker-compose, and a `kind` cluster config for later.

Explicitly **not** here yet: detectors, correlation engine, LLM, runbook implementations,
policy engine, actuation. Those are Phases 1–4.

## Layout

```
docs/ARCHITECTURE.md          # the design (read first)
src/domain/                   # entities, value objects, ports — no I/O
  incident/                   #   Signal, Incident, Fingerprint, IncidentRepository
  runbook/contract.py         #   Runbook + RunbookRegistry (interfaces only)
  actuator/port.py            #   Actuator port (the brain<->cluster seam, ADR-0001)
src/application/incident/     # use cases: RecordIncident, ListIncidents, TransitionIncident
src/infrastructure/           # adapters: config, db, persistence, observability, web, k8s
  actuator/noop_actuator.py   #   Phase 0 no-op adapter behind the Actuator port
  kubernetes/event_watcher.py #   event->signal mapping (watcher itself is Phase 1)
src/main.py                   # composition root (manual wiring)
migrations/                   # alembic (hand-written)
configs/ , infra/kind/        # LGTM + collector configs, kind cluster
```

Conventions mirror the sibling `Observability` project: **no `__init__.py`** (implicit
namespace packages), absolute `src.*` imports, hexagonal layering, manual wiring in
`main.py`. See [`CLAUDE.md`](CLAUDE.md).

## Run it (Phase 0)

```bash
uv sync

# Foundation stack (Postgres + LGTM + OTel Collector); ports are offset (5xxx / 15432)
# so this runs alongside the separate Observability project.
docker compose up -d

uv run alembic upgrade head
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# Record an incident by hand:
curl -s -X POST http://localhost:8000/incidents -H "Content-Type: application/json" -d '{
  "title": "checkout OOMing",
  "fingerprint": "checkout/oom/prod-ns",
  "source": "manual",
  "severity": "critical",
  "summary": "OOMKilled x5 in 10m"
}'
curl -s http://localhost:8000/incidents
```

Service endpoints (offset from the Observability project): API `:8000`, Grafana `:5000`,
Prometheus `:5090`, Tempo `:5200`, Loki `:5100`, OTel Collector `:5317`/`:5318`,
Postgres `:15432`.

## Dev

```bash
uv run pytest          # domain + application unit tests (no DB/cluster needed)
uv run ruff check .
uv run mypy src
```

## Next: Phase 1

Detectors + correlation engine (fingerprint/topological/change grouping) + SLO monitoring +
the real Kubernetes event watcher. Still read-only — no cluster mutation. See the roadmap in
`docs/ARCHITECTURE.md` §7.
