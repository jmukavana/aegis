from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "observability-ai"
    database_url: str = (
        "postgresql+asyncpg://aegis:aegis@localhost:15432/aegis"
    )
    prometheus_url: str = "http://localhost:5090"
    # Empty => the k8s event watcher stays disabled (Phase 0 runs fine without a cluster).
    kube_context: str = ""

    # AuthN/Z for the guarded remediation endpoints (plan/approve/execute). "header" is the
    # Phase 3b bootstrap — trusts X-Subject/X-Permissions headers outright, dev/test only, and
    # is the default so the app boots with zero external auth dependencies. "keycloak" swaps in
    # a real, signature-verified bearer JWT via the same Identity(subject, permissions) shape —
    # nothing downstream (RBAC checks, audit) changes.
    auth_provider: str = "header"
    oidc_issuer: str = "http://localhost:5080/realms/aegis"
    oidc_audience: str = "aegis-api"

    @property
    def oidc_jwks_url(self) -> str:
        return f"{self.oidc_issuer}/protocol/openid-connect/certs"

    # Phase 3 guarded actuation. The global kill-switch: while false, the actuator will
    # dry-run (read-only, always allowed) but REFUSES execute/undo. Default false — even an
    # approved plan cannot mutate a cluster until this is explicitly turned on (Phase 3c).
    actuation_enabled: bool = False
    # The narrow Phase 3c opening: execution is additionally allowlisted per environment and
    # per runbook (CSV). Defaults to the lowest-risk reversible action in a dev cluster only.
    actuation_environments: str = "local"
    actuation_runbooks: str = "rolling-restart"
    # Circuit breaker: cap actions per incident within the window (anti-flapping).
    circuit_breaker_max_actions: int = 3
    circuit_breaker_window_seconds: float = 3600.0
    # Verify loop: how to judge recovery. "signal_quiet" = no new signals for a window;
    # "metrics" = the service's error ratio has fallen to/below the threshold (SLO stopped
    # burning), read from the Prometheus MetricsGateway.
    recovery_probe: str = "signal_quiet"
    recovery_quiet_window_seconds: float = 30.0
    recovery_metrics_window_seconds: float = 300.0
    recovery_error_threshold: float = 0.01

    # Phase 4 earned autonomy. auto_execute_enabled is the master switch (default OFF — even
    # Phase 4 starts with a human on every action). Beyond that, each environment is its own
    # *staged promotion* tier with its own bar (Phase 4c) — an environment with no tier enabled
    # below never auto-executes no matter the track record, and prod has no tier at all (never
    # auto-executes, full stop, enforced in the policy engine regardless of config).
    auto_execute_enabled: bool = False

    # local: the proving ground — light bar, wide blast-radius tolerance.
    auto_execute_local_enabled: bool = True
    auto_execute_local_min_success_rate: float = 0.8
    auto_execute_local_min_samples: int = 3
    auto_execute_local_max_blast_radius: int = 5

    # staging: the next rung — off by default (opt-in promotion once local has proven the
    # runbook), and deliberately much stricter: a long, clean track record and a tight
    # blast-radius cap before autonomy is trusted somewhere more consequential.
    auto_execute_staging_enabled: bool = False
    auto_execute_staging_min_success_rate: float = 0.97
    auto_execute_staging_min_samples: int = 20
    auto_execute_staging_max_blast_radius: int = 1

    # Phase 4b closed-loop trigger: a background loop sweeps open incidents and drives
    # AutoRemediate itself (no human initiates). Off by default; the environment it acts in and
    # the sweep cadence are configurable.
    enable_auto_remediation_loop: bool = False
    auto_remediation_interval_seconds: float = 30.0
    auto_remediation_environment: str = "local"

    # Auto-resolve reconcile loop (Phase 1 lifecycle).
    enable_reconcile_loop: bool = True
    reconcile_interval_seconds: float = 60.0

    # Phase 2 LLM incident-explainer. Provider is swappable behind the IncidentAnalysisGateway
    # port: "ollama" (local) or "vertex" (Vertex AI Llama Model-as-a-Service).
    llm_provider: str = "ollama"

    # Ollama provider. Empty ollama_url disables /explain when provider=ollama.
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1"

    # Vertex AI Llama MaaS provider (used when llm_provider=vertex). Auth via Application
    # Default Credentials; the model must be enabled in Model Garden for the project.
    vertex_project: str = ""  # required when provider=vertex
    vertex_location: str = "us-central1"
    vertex_model: str = "meta/llama-3.3-70b-instruct-maas"

    # Phase 2 RAG: retrieval-augmented explanation over past incidents (pgvector + Ollama
    # embeddings). Disable to run the explainer without cross-incident memory.
    enable_incident_memory: bool = True
    embedding_model: str = "nomic-embed-text"  # 768-d; must match EMBED_DIM in migration 0003
    rag_recall_limit: int = 3

    # Phase 2 ChatOps: surface each explanation to Slack via an Incoming Webhook. Empty
    # disables it — /explain still returns the analysis, it just isn't posted anywhere.
    slack_webhook_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
