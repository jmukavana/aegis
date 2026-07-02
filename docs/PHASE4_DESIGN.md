# Phase 4 — Earned autonomy (closed-loop auto-execute) — design

> **Status: 4a building.** Extends [`PHASE3_DESIGN.md`](PHASE3_DESIGN.md). The one new power is
> `ALLOW_AUTO`: executing an approved-by-*policy* remediation with **no human in the loop** —
> but only where a runbook has *earned* it. Every other Phase 3 guardrail still applies.

## The single new idea: trust is earned, not configured

Phases 0–3 always kept a human on the approval step. Phase 4 lets the **policy gate** stand in
for that human — but only for an action that has already proven it fixes this exact problem. The
"trust ratchet" (ARCHITECTURE.md §5) made concrete: a `(runbook, fingerprint)` auto-executes in
an environment iff **all** hold —

- global `AUTO_EXECUTE_ENABLED` is on (default **off** — even Phase 4 starts read-only);
- the environment is in `AUTO_EXECUTE_ENVIRONMENTS` (**never `prod`** in this phase);
- `risk_tier == LOW` and the action is **reversible**;
- the **verified** success rate ≥ `AUTO_EXECUTE_MIN_SUCCESS_RATE` over ≥ `AUTO_EXECUTE_MIN_SAMPLES`
  recorded outcomes.

Anything short of that → `REQUIRE_APPROVAL` (Phase 3 human path, unchanged). `prod` / high-risk →
`DENY`. The rate comes from `OutcomeStore`, and — critically — it counts **verified** outcomes
(`VerifyRemediation`), so an action earns autonomy only by *actually resolving* incidents, never
by an API call returning 200.

## Auto-execute removes the human, and *nothing else*

Auto-execute still runs the full guarded pipeline: dry-run → policy → enablement allowlist →
circuit breaker → global kill-switch → execute → **verify** → audit. It simply substitutes a
`ALLOW_AUTO` policy decision for the human `approve` step. Defence in depth is unchanged; the
blast radius of a bad auto-decision is still "a pre-vetted, reversible, rate-limited,
circuit-broken, audited LOW-risk action in a non-prod env, then immediately verified."

## Shape

- `PolicyInput` gains `success_rate` + `sample_size`; `RuleBasedPolicyEngine` gains the auto
  thresholds and returns `ALLOW_AUTO` per the rule above (still gated by `allow_auto`, default off).
- `OutcomeStore` gains `stats(runbook, fingerprint) -> OutcomeStats(sample_size, success_rate)`.
- `PlanRemediation` becomes trust-aware: it looks up the incident's fingerprint + the runbook's
  stats and feeds them to the policy. `ALLOW_AUTO` → plan is auto-approved (status APPROVED, audit
  `auto_approved`); otherwise unchanged.
- `AutoRemediate` orchestrator: plan (trust-aware) → if auto-approved, execute + verify as a
  system principal (`aegis-auto`); else leave the pending plan for a human. `POST
  /incidents/{id}/auto-remediate`.

## Sub-phases
- **4a — ✅ done, driven live on minikube.** `PolicyInput.success_rate`/`sample_size` +
  `RuleBasedPolicyEngine` `ALLOW_AUTO` rule; `OutcomeStore.stats`; trust-aware `PlanRemediation`
  (auto-approves on `ALLOW_AUTO`); `AutoRemediate` orchestrator (plan→execute→verify as
  `aegis-auto`); `POST /incidents/{id}/auto-remediate`. Config: `AUTO_EXECUTE_ENABLED` (default
  off) + `AUTO_EXECUTE_ENVIRONMENTS`/`MIN_SUCCESS_RATE`/`MIN_SAMPLES`. Drove it end-to-end: two
  supervised runs of `rolling-restart` for one fingerprint (each `require_approval`, then
  verified-recovered → 2/2 successes recorded), after which a third fresh incident was
  **auto-remediated with no human** — `allow_auto` ("100% over 2 runs ≥ 80%/2") → executed
  (pods really recreated) → verified → resolved, audit showing `auto_approved` and no human
  `approved` step. All Phase 3 guards (dry-run, allowlist, breaker, kill-switch, audit) still ran.
- **4b — ✅ done, driven live on minikube.** A `RemediationSelector` port +
  `RuleBasedRemediationSelector` (failure_class → runbook, params from the fingerprint) chooses a
  runbook deterministically (LLM stays advisory); `AutoRemediationSweep` sweeps open incidents
  that have no plan yet (idempotency via `RemediationPlanRepository.exists_for_incident`) and
  hands each to `AutoRemediate`; `run_auto_remediation_loop` runs it on an interval from the
  lifespan (`ENABLE_AUTO_REMEDIATION_LOOP`, default off). Drove it live: seeded a track record for
  `nginx/crashloop/default`, created a fresh incident and **touched nothing** — the loop found it,
  selected rolling-restart, and ran `drafted→…→allow_auto→executed→verified→resolved`
  autonomously (pods recreated), zero human, zero API call for the remediation. Un-earned or
  unmapped incidents are left as pending plans / untouched for a human — the trust ratchet is
  still the guard.
- **4c — 🔨 in progress. Durability ✅ done, driven live.** `SqlAlchemyOutcomeStore` +
  `SqlAlchemyCircuitBreaker` (migration 0005: `remediation_outcomes`, `circuit_breaker_actions`)
  replace the in-memory adapters — the flywheel and breaker now live in Postgres, built
  per-request from the session, so earned trust survives restarts and is shared across replicas.
  Proven live: seeded 2 verified successes for a fingerprint, **restarted the app** (which would
  have wiped the in-memory store), then a fresh incident auto-remediated on `allow_auto` reading
  the rate from Postgres — pre-4c it would have reverted to `require_approval`.
  **Metrics RecoveryProbe ✅ done.** `MetricsRecoveryProbe` (behind the `RecoveryProbe` port,
  selected by `RECOVERY_PROBE=metrics`) judges recovery from the actual error signal — an
  incident's service is recovered once its error ratio over the window falls to/below the
  threshold (the SLO stopped burning), read via the Phase 1 Prometheus `MetricsGateway` — rather
  than merely "went quiet". Unit-tested (recovered / not-recovered / no-series) and verified live
  against real Prometheus.
  **Blast-radius policy input ✅ done.** `ServiceGraph` gained `dependents_of` (reverse edges)
  and a `blast_radius(graph, service)` transitive-closure function; `PlanRemediation` computes
  the set of downstream services an action would disrupt, records it on the audit trail, and
  feeds its size to the policy. `RuleBasedPolicyEngine` gains `max_auto_blast_radius`: a trusted,
  low-risk, reversible runbook still **falls back to human approval if it would disrupt too many
  downstream services** (`AUTO_EXECUTE_MAX_BLAST_RADIUS`) — an autonomy brake for
  highly-connected services. Unit-tested (brake blocks / within-cap allows) and verified live
  (planning on `ledger` recorded "blast_radius: 2 downstream — checkout, payments").
  **Staged promotion ✅ done.** `RuleBasedPolicyEngine` now takes a `dict[Environment,
  AutoThreshold]` instead of one flat threshold set — each environment is its own trust tier,
  stricter the closer to prod. Config: `AUTO_EXECUTE_LOCAL_*` (light bar, on by default —
  0.8 success rate / 3 samples / blast radius 5) and `AUTO_EXECUTE_STAGING_*` (off by default;
  when enabled, a much stricter bar — 0.97/20/1). An environment absent from the map — including
  `prod`, which has no tier at all — never auto-executes regardless of track record; that DENY
  is structural, not config-driven. Verified live: the *same* (runbook, fingerprint) track record
  that auto-executed in `local` at 2/2 still required a human in `staging` ("100% over 2" <
  staging's 95%/4 bar); two more supervised `staging` runs brought the pool to 5/5, after which
  `staging` auto-executed ("100% verified success over 5 runs in staging ≥ 95%/4"); `prod`
  remained `deny` throughout, even with a perfect 5/5 record.
  **rollback-deployment executable ✅ done.** `ClusterClient` gained `list_replica_set_revisions`
  (+ `DeploymentState.selector`) so a runbook can read a Deployment's real rollout history — the
  same ReplicaSet-revision mechanism `kubectl rollout undo` uses. `RollbackDeploymentRunbook`
  patches `spec.template` to the second-most-recent revision; marked `reversible=False` (its
  `undo` honestly refuses rather than guessing an inverse from `params` alone — a human recovers
  via a fresh plan). Registered in `StaticRunbookRegistry` alongside rolling-restart and
  scale-deployment (already registered since 3a). Being MEDIUM risk, it **structurally never
  earns Phase 4 auto-execute** (`ALLOW_AUTO` is LOW-risk only) — proven even at a 999-sample,
  100% track record. Verified live on minikube: dry-run against nginx's *real* revision history
  predicted "roll back to revision 19"; a human-approved execute (via the elevated
  `:irreversible` permission) genuinely rolled the image back
  (`nginx:1.27.2-alpine → nginx:1.27-alpine`), full audit trail
  `drafted→dry_run→blast_radius→policy_decision→approved→executing→executed`. 4c is now closed.

## Non-goals for 4a
No `prod` auto-execute · no auto-trigger yet (an operator still calls the endpoint) · no change to
the guardrails other than substituting policy for the human approval when trust is earned.
