# Phase 3 — Guarded Actuation (design)

> **Status: design / not yet built.** This is the on-paper step for Phase 3, mirroring how
> Phase 0 was designed before scaffolding. It refines the high-level safety model in
> [`ARCHITECTURE.md §3.4–3.7, §5`](ARCHITECTURE.md) into an implementable plan. No code here
> mutates a cluster; nothing in this doc weakens the read-only default.

## 0. The line we are crossing

Phases 0–2 were **read-only**: observe, correlate, explain, propose. Phase 3 is the first
phase that can **change a cluster**. Every design choice below exists to bound the blast
radius of that new power. The governing rule from ARCHITECTURE.md §5 stands:

> **The LLM proposes, policy decides, the actuator acts.** The model never has a path to
> Execute. Its output is a structured `{runbook_id, params}` referencing a pre-vetted runbook;
> an independent policy gate approves or rejects; a human approves before anything runs.

Phase 3 deliberately stops **short of autonomy**: *every* execution requires explicit human
approval. Auto-execute (earned via the confidence flywheel) is **Phase 4**. Phase 3 proves the
whole guarded pipeline works with a human in the loop, on the lowest-risk reversible action,
in a non-prod cluster.

## 1. The remediation pipeline (the Plan→Decide→Act→Verify spine)

A Phase 2 `RemediationProposal` is *advisory*. Phase 3 turns a chosen proposal into a
**`RemediationPlan`** — a concrete, validated, parameter-bound action against a named
environment — and walks it through a guarded lifecycle:

```
Phase 2 RemediationProposal  (advisory: {runbook_id, params, reasoning, confidence})
        │  operator picks one proposal for an incident  (POST /incidents/{id}/remediations)
        ▼
  PlanRemediation
        │  1. resolve runbook_id -> executable Runbook (RunbookRegistry); reject if unknown
        │  2. validate params against the runbook's schema; reject if malformed
        │  3. PolicyEngine.evaluate(plan) -> PolicyDecision
        ▼
  PolicyDecision
        ├─ DENY            -> plan.status=DENIED, audit, stop
        ├─ REQUIRE_APPROVAL-> Actuator.dry_run(plan)   (server-side dry-run, no mutation)
        │                     attach predicted effect + reversibility to the plan
        │                     plan.status=PENDING_APPROVAL
        │                     (ALLOW_AUTO is disabled in Phase 3 by the read-only default)
        ▼
  ApproveRemediation   (POST /remediations/{id}/approve)
        │  RBAC: remediation:approve  +  env-scoped action:execute:<env>
        │  CircuitBreaker.check(incident/fingerprint)  -> trip => escalate, refuse
        │  plan.status=APPROVED
        ▼
  ExecuteRemediation   (POST /remediations/{id}/execute, or chained after approve)
        │  GLOBAL KILL-SWITCH + per-env + per-runbook enable check
        │  Actuator.execute(plan) -> ActionResult ; persist ExecutionRecord
        ▼
  VerifyRemediation    (after a recovery window)
        │  watch triggering signal + SLO burn rate
        ├─ recovered     -> resolve incident; OutcomeStore.record(success)   ← flywheel
        └─ not recovered -> Actuator.undo (if reversible); escalate / next plan / trip breaker
        ▼
  AuditLog.append({detected, reasoned, decided, dry_run, approved_by, acted, outcome})  (immutable)
```

Every transition writes to the **audit log**. The audit trail — not the happy path — is the
Phase 3 deliverable that earns trust.

## 2. New domain model (ports & entities)

Kept in the same hexagonal style: entities/value-objects/ports in `domain`, adapters in
`infrastructure`, use cases in `application`. New pieces:

### Entities / value objects (`src/domain/remediation/`)
- **`Environment`** — value object: `LOCAL` (minikube), `STAGING`, `PROD`. Auto-execute and
  approval requirements differ per environment (§5 "environment scoping").
- **`RemediationPlan`** — the Phase 3 aggregate. Frozen dataclass with withers, mirroring the
  `Incident` style. Fields: `id`, `incident_id`, `runbook_id`, `params: dict`, `environment`,
  `risk_tier`, `confidence`, `status`, `dry_run: DryRunResult | None`,
  `decision: PolicyDecision | None`, `approvals: tuple[Approval, ...]`,
  `execution: ExecutionRecord | None`, timestamps.
- **`RemediationStatus`** — `DRAFTED → PENDING_APPROVAL → APPROVED → EXECUTING → EXECUTED`
  with terminal branches `DENIED`, `FAILED`, `ROLLED_BACK`. Illegal transitions raise.
- **`Approval`** — `{approver_subject, permission_used, environment, approved_at}`.
- **`ExecutionRecord`** — `{started_at, finished_at, result: ActionResult, undo: ActionResult | None}`.

### Ports (interfaces only)
- **`Actuator`** — *already exists* (`domain/actuator/port.py`): `dry_run` / `execute` /
  `undo`. Phase 3 adds the real adapter. Unchanged contract.
- **`Runbook` / `RunbookRegistry`** — *already contracted* (`domain/runbook/contract.py`).
  Phase 3 adds executable implementations + a concrete registry. The Phase 2 `RunbookCatalog`
  (specs the LLM sees) and the Phase 3 `RunbookRegistry` (executables) stay separate — the
  catalog is the menu, the registry is the kitchen.
- **`PolicyEngine`** (new) — `evaluate(plan) -> PolicyDecision`. `PolicyDecision`:
  `outcome ∈ {ALLOW_AUTO, REQUIRE_APPROVAL, DENY}`, `reasons: tuple[str, ...]`,
  `required_permission: str | None`. The **only** place "may this run?" is decided.
- **`CircuitBreaker`** (new) — `allow(incident_id, fingerprint) -> bool` +
  `record(...)`. Caps actions per incident/fingerprint/window; trip ⇒ escalate.
- **`AuditLog`** (new) — `append(entry) -> None`, `for_incident(id) -> list[AuditEntry]`.
  Append-only; no update/delete in the port at all.
- **`OutcomeStore`** (new) — `record(runbook_id, fingerprint, success)` +
  `success_rate(runbook_id, fingerprint) -> float`. The confidence flywheel (§3.4/§5); feeds
  Phase 4's auto-execute threshold. Read in Phase 3 for display, written by Verify.

### Application use cases (`src/application/remediation/`)
`PlanRemediation`, `ApproveRemediation`, `ExecuteRemediation`, `VerifyRemediation`. Each
depends only on ports; each appends to the audit log; none touch the k8s API directly.

## 3. The safety controls, made concrete

Mapping ARCHITECTURE.md §5's table onto Phase 3 mechanics:

| Control (§5) | Phase 3 mechanism |
|---|---|
| Runbook allowlist | `RunbookRegistry.get(id)` returns `None` ⇒ plan rejected before policy. The Phase 2 enum already stops the LLM naming a non-runbook; this is the executable-side twin. |
| Risk tiers | `Runbook.risk_tier`; policy raises the approval bar (and, in Phase 4, the confidence threshold) per tier. |
| Environment scoping | `Environment` on every plan; approval needs `action:execute:<env>`; kill-switch is per-env. `PROD` stays disabled through all of Phase 3. |
| Confidence threshold | Carried on the plan; **not** an auto-execute gate in Phase 3 (human always approves). Wired for Phase 4. |
| Dry-run first | `Actuator.dry_run` runs **before** approval; the predicted effect is what the human approves against. Server-side dry-run (`dryRun=All`) so the API validates it, not us. |
| Circuit breaker | `CircuitBreaker.allow()` checked at approval **and** execute; trip ⇒ escalate + refuse. |
| Reversibility | `DryRunResult.reversible` surfaced at approval; `Actuator.undo` on failed verify. Irreversible runbooks require a higher permission. |
| Full audit | Every use case appends an immutable `AuditEntry`. |
| Blast-radius check | Reuse the Phase 1 `ServiceGraph` to list downstream services touched; attach to the dry-run summary. (Estimator only in Phase 3; a policy input in Phase 4.) |

Plus the **global kill-switch** (`ACTUATION_ENABLED`, default **false**) and the
**read-only default** (`ALLOW_AUTO` disabled): even a misconfigured policy cannot auto-execute
in Phase 3, and even an approved plan cannot execute unless actuation is explicitly enabled
for that environment.

## 4. AuthZ — auth becomes mandatory here

Phases 0–2 ran unauthenticated (local, read-only). **Phase 3 is where identity becomes
load-bearing**, because approvals are now security decisions. Reuse the
Keycloak/OIDC + dynamic-RBAC + permission model already built in the sibling `Observability`
project (that was always the plan — ARCHITECTURE.md §3.7). Permissions:

- `remediation:plan` — draft a plan / run dry-run (read-mostly; low bar).
- `remediation:approve` — approve a pending plan.
- `action:execute:staging` / `action:execute:prod` — **environment-scoped** execute rights.
  Approve-in-staging ≠ approve-in-prod.
- `runbook:manage` — register/enable/disable runbooks and per-env kill-switches.

The approver's subject + the exact permission used are recorded on the `Approval` and in the
audit log. No self-approval of one's own plan (segregation-of-duty check).

## 5. Adapters to build (`src/infrastructure/`)

- **`KubernetesActuator`** (`infrastructure/actuator/k8s_actuator.py`) — implements `Actuator`
  via the official `kubernetes` async client. `dry_run` uses server-side dry-run
  (`dryRun=All`) so the API itself validates and predicts. `execute` performs the runbook's
  idempotent patch. Reuses the Phase 1 kube config/context wiring. **Replaces `NoopActuator`
  only when `ACTUATION_ENABLED` and the env/runbook are allowlisted.**
- **Executable runbooks** (`infrastructure/runbook/impl/`) — start with the two lowest-risk,
  reversible, idempotent ones: `rolling-restart` (patch a
  `kubectl.kubernetes.io/restartedAt` annotation on the Deployment's pod template) and
  `scale-deployment` (patch `spec.replicas`; `undo` restores the prior count). Both LOW risk.
- **`PolicyEngine`** — **embedded Python policy** for v1 (a small set of typed rules), behind
  the port so OPA/Rego can swap in later exactly like the Actuator (ADR-0001 pattern: minimize
  moving parts, isolate the swap point). See §7 decision 1.
- **`SqlAlchemyAuditLog`**, **`SqlAlchemyOutcomeStore`**, **`SqlAlchemyRemediationRepository`**
  — Postgres persistence (new migrations `0004…`). Audit table is append-only by convention +
  no update/delete methods on the adapter.
- **`InMemoryCircuitBreaker`** first; Postgres-backed if it needs to survive restarts.

## 6. API surface (Phase 3)

All mutating routes are RBAC-gated (§4):

- `POST /incidents/{id}/remediations` — draft a plan from `{runbook_id, params}`; runs
  validation + policy + dry-run; returns the plan with predicted effect + decision.
- `GET  /remediations` / `GET /remediations/{id}` — list / inspect (status, dry-run, decision).
- `POST /remediations/{id}/approve` — `remediation:approve` + `action:execute:<env>`.
- `POST /remediations/{id}/execute` — execute an approved plan (guarded by kill-switch).
- `GET  /remediations/{id}/audit` — the immutable trail.

Slack (Phase 2's `IncidentNotifier`) gains an *optional* interactive path later: Approve/Deny
buttons whose callback hits `POST /remediations/{id}/approve`. Deferred — see §7 decision 2.

## 7. Decisions (ADR-0002 candidates)

1. **Policy engine — ✅ DECIDED: embedded Python rules, behind the `PolicyEngine` port.**
   Consistent with ADR-0001 (all-Python, one fewer moving part); the port makes OPA/Rego a
   later adapter swap, not a rewrite. Accepted trade-off: policy is versioned code, not
   hot-reloadable Rego. v1 rule set: `PROD → DENY` (Phase 3); `risk=HIGH → DENY`;
   `reversible=False →` require an extra permission; else `→ REQUIRE_APPROVAL`.
2. **Approval surface — recommended default (confirm): API-first, Slack later.** RBAC-gated
   REST (`POST /remediations/{id}/approve`), fully drivable/testable locally; Slack Approve/Deny
   buttons layer on later, calling the same endpoint. Slack interactivity needs a public
   callback + a real Slack app we can't fully exercise locally.
3. **AuthZ scope — ✅ DONE: real Keycloak/OIDC, ported from the sibling `Observability`
   project.** `KeycloakTokenValidator` (`infrastructure/auth/keycloak_token_validator.py`)
   validates a bearer JWT's signature (via JWKS/`PyJWKClient`), issuer, and audience, then
   extracts `sub` + realm roles. Simplified vs. Observability: Aegis's permissions were already
   free-form strings with no separate local Role table, so realm roles ARE the permission
   strings directly (`remediation:plan`, `action:execute:local`, …) — no local User/Role
   database needed. Selectable via `AUTH_PROVIDER=header|keycloak` (default `header`, the Phase
   3b bootstrap, so the app still boots with zero external auth deps); switching providers
   changes nothing downstream (RBAC checks, audit) — same `Identity(subject, permissions)`
   shape either way. `docker-compose.yml` gained a `keycloak` service + realm fixture
   (`configs/keycloak/realm-export.json`, realm `aegis`, client `aegis-api`, demo users
   `planner-demo`/`approver-demo`/`operator-demo`). Verified live: 401 with no token, 403 with
   the wrong permission, real cryptographic subject UUIDs recorded on every plan/approval/audit
   line (not spoofable header strings), and segregation-of-duty enforced against real identities
   (operator-demo — who holds both plan and execute permissions — still 409s approving their own
   plan). The permission names and segregation-of-duty rule are identical to the bootstrap.
4. **Phase 3 auto-executes? No (locked by roadmap).** Human approval always required in Phase 3;
   auto-execute is Phase 4, earned from `OutcomeStore` success rates. Kept explicit so it isn't
   quietly relaxed.
5. **First target = minikube (`LOCAL`) + `rolling-restart` only.** `PROD` disabled throughout
   Phase 3; only after the full pipeline is proven end-to-end do more runbooks/envs turn on.

## 8. Build order (foundation-first sub-phases)

- **3a — Real dry-run, execution wired but OFF. ✅ done, driven live on GKE.**
  `KubernetesActuator.dry_run` (server-side `dryRun=All`), executable
  `rolling-restart`/`scale-deployment` over a `ClusterClient` seam, `StaticRunbookRegistry`, the
  `KubernetesActuator` guard facade (execute/undo refuse while `ACTUATION_ENABLED=false`),
  `DryRunRemediation` use case + `src/dry_run.py` CLI. Verified against a real GKE cluster
  (token-based kubeconfig — no gke-gcloud-auth-plugin needed): both runbooks predicted their
  effect and the deployment was provably unchanged; cluster torn down after.
- **3b — Decision pipeline: policy + audit + approval + RBAC. ✅ done, driven live on minikube.**
  Embedded `RuleBasedPolicyEngine` behind the `PolicyEngine` port (prod→DENY · high-risk→DENY ·
  irreversible→elevated perm · else REQUIRE_APPROVAL; ALLOW_AUTO gated off); the `RemediationPlan`
  aggregate + `RemediationStatus` lifecycle with `approve()` (enforces pending-status + no
  self-approval); append-only `AuditLog` (`SqlAlchemyAuditLog`) + `RemediationPlanRepository`
  (`SqlAlchemyRemediationPlanRepository`), migration 0004; `PlanRemediation` + `ApproveRemediation`
  use cases; an `Identity`/RBAC seam with a bootstrap header adapter (`get_identity`) carrying the
  real permission names; and the REST surface (`POST /incidents/{id}/remediations`,
  `POST /remediations/{id}/approve`, `GET /remediations/{id}`, `GET /incidents/{id}/audit`).
  Driven end-to-end: plan → real dry-run → policy → self-approval blocked (409) → missing-perm
  blocked (403) → valid approval → immutable 4-step audit; cluster provably unmutated (approval ≠
  execution). *Auth note:* the header identity is the **bootstrap**; the full Keycloak/OIDC JWT
  adapter swaps in behind the same `Identity`/permission seam without changing checks or audit.
  Execution still gated OFF. Deliverable met: *approved plans exist with a full immutable audit
  trail; still nothing runs.*
- **3c — First real guarded self-heal + closed loop. ✅ done, driven live on minikube.**
  `ExecuteRemediation` gated by: env-scoped execute permission · plan must be APPROVED ·
  per-env + per-runbook enablement allowlist (`ACTUATION_ENABLED`=true, `local`+`rolling-restart`
  only) · `CircuitBreaker` (`InMemoryCircuitBreaker`, N-per-incident window) · then the actuator
  (global kill-switch); `undo` on reversible failure. `VerifyRemediation` closes the loop
  (§3.6): a `RecoveryProbe` (`SignalQuietRecoveryProbe`) checks whether the incident went quiet —
  recovered → resolve + record **success**, else → escalate + record **failure** — and the
  *verified* outcome (not the execution succeeding) is what `OutcomeStore` (the flywheel) counts.
  Routes: `POST /remediations/{id}/execute` + `POST /remediations/{id}/verify`. Drove **both**
  paths live: incident A → plan → approve → **execute (nginx pods actually recreated, no
  downtime)** → **verify → recovered → resolved**; incident B → … → execute → re-signalled →
  **verify → not recovered → escalated**, each with the full `drafted→…→verified/verify_failed`
  audit trail. Deliverable met: *the first human-approved, reversible, audited remediation runs
  for real and the loop is closed.* *Remaining bridge to Phase 4:* durable `OutcomeStore`/
  `CircuitBreaker`, a metrics/SLO-burn `RecoveryProbe`, and — the whole point of Phase 4 —
  letting a runbook's accumulated success rate *earn* `ALLOW_AUTO` (auto-execute without a human).
- Verify + flywheel are the **bridge to Phase 4** (auto-execute once a runbook's success rate
  for a fingerprint clears the policy threshold).

## 9. Explicit non-goals for Phase 3
No auto-execute · no `PROD` · no admission webhooks/CRDs/operator pattern (still a service, not
a controller — that's the ADR-0001 "if/when" future) · no high-risk runbooks (cordon-drain,
node ops) enabled · no Slack interactive approval (API-first).
