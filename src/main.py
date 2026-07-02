import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import uvicorn
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from src.application.remediation.use_cases import (
    AutoRemediate,
    AutoRemediationSweep,
    ExecuteRemediation,
    PlanRemediation,
    VerifyRemediation,
)
from src.domain.detection.metrics_gateway import MetricsGateway
from src.domain.remediation.environment import Environment
from src.domain.remediation.recovery import RecoveryProbe
from src.infrastructure.auto_remediation import run_auto_remediation_loop
from src.infrastructure.change.in_memory_change_log import InMemoryChangeLog
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.db import (
    create_db_engine,
    create_session_factory,
    get_session_dependency,
)
from src.domain.analysis.embedding import EmbeddingGateway
from src.domain.analysis.gateway import IncidentAnalysisGateway
from src.infrastructure.detection.detector_factory import build_detectors
from src.domain.notification.notifier import IncidentNotifier
from src.infrastructure.llm.ollama_embedding_gateway import OllamaEmbeddingGateway
from src.infrastructure.llm.ollama_gateway import OllamaIncidentAnalysisGateway
from src.infrastructure.llm.vertex_llama_gateway import (
    VertexLlamaIncidentAnalysisGateway,
)
from src.infrastructure.notification.slack_notifier import SlackIncidentNotifier
from src.infrastructure.actuator.k8s_actuator import KubernetesActuator
from src.infrastructure.auth.header_identity import get_identity as header_identity
from src.infrastructure.auth.keycloak_identity import (
    build_keycloak_identity_dependency,
)
from src.infrastructure.auth.keycloak_token_validator import KeycloakTokenValidator
from src.infrastructure.kubernetes.cluster_client import KubernetesClusterClient
from src.infrastructure.policy.rule_based_policy import (
    AutoThreshold,
    RuleBasedPolicyEngine,
)
from src.infrastructure.remediation.metrics_recovery_probe import MetricsRecoveryProbe
from src.infrastructure.remediation.rule_based_selector import (
    RuleBasedRemediationSelector,
)
from src.infrastructure.remediation.signal_quiet_recovery_probe import (
    SignalQuietRecoveryProbe,
)
from src.infrastructure.persistence.audit_repository import SqlAlchemyAuditLog
from src.infrastructure.persistence.circuit_breaker_repository import (
    SqlAlchemyCircuitBreaker,
)
from src.infrastructure.persistence.incident_repository import (
    SqlAlchemyIncidentRepository,
)
from src.infrastructure.persistence.outcome_repository import SqlAlchemyOutcomeStore
from src.infrastructure.persistence.remediation_repository import (
    SqlAlchemyRemediationPlanRepository,
)
from src.infrastructure.runbook.static_registry import StaticRunbookRegistry
from src.infrastructure.metrics.prometheus_gateway import PrometheusMetricsGateway
from src.infrastructure.observability.logging import configure_logging
from src.infrastructure.observability.metrics import configure_metrics
from src.infrastructure.observability.tracing import configure_tracing
from src.infrastructure.reconcile import run_reconcile_loop
from src.infrastructure.runbook.static_catalog import StaticRunbookCatalog
from src.infrastructure.topology.seed import SERVICE_DEPENDENCIES
from src.infrastructure.topology.static_service_graph import StaticServiceGraph
from src.infrastructure.web.analysis_routes import build_analysis_router
from src.infrastructure.web.detection_routes import build_detection_router
from src.infrastructure.web.incident_routes import build_incident_router
from src.infrastructure.web.remediation_routes import build_remediation_router
from src.infrastructure.web.signal_routes import build_signal_router


def _csv(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _build_auto_thresholds(settings: Settings) -> dict[Environment, AutoThreshold]:
    """Staged promotion (Phase 4c): only environments with a tier explicitly enabled appear
    here — everything else, including prod (which has no tier at all), never auto-executes."""
    thresholds: dict[Environment, AutoThreshold] = {}
    if settings.auto_execute_local_enabled:
        thresholds[Environment.LOCAL] = AutoThreshold(
            min_success_rate=settings.auto_execute_local_min_success_rate,
            min_samples=settings.auto_execute_local_min_samples,
            max_blast_radius=settings.auto_execute_local_max_blast_radius,
        )
    if settings.auto_execute_staging_enabled:
        thresholds[Environment.STAGING] = AutoThreshold(
            min_success_rate=settings.auto_execute_staging_min_success_rate,
            min_samples=settings.auto_execute_staging_min_samples,
            max_blast_radius=settings.auto_execute_staging_max_blast_radius,
        )
    return thresholds


def _build_recovery_probe(settings: Settings, metrics: MetricsGateway) -> RecoveryProbe:
    """Pick how the verify loop judges recovery (config swap behind the RecoveryProbe port)."""
    if settings.recovery_probe == "metrics":
        return MetricsRecoveryProbe(
            metrics,
            window=timedelta(seconds=settings.recovery_metrics_window_seconds),
            error_threshold=settings.recovery_error_threshold,
        )
    return SignalQuietRecoveryProbe(
        timedelta(seconds=settings.recovery_quiet_window_seconds)
    )


def _build_identity_dependency(settings: Settings, logger):
    """Pick the AuthN/Z provider behind the guarded remediation endpoints (config swap — the
    Phase 3b bootstrap header identity, or a real Keycloak bearer JWT). Same `Identity` shape
    either way, so no route or use case changes with the provider."""
    if settings.auth_provider == "keycloak":
        validator = KeycloakTokenValidator(
            settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url
        )
        return build_keycloak_identity_dependency(validator, logger)
    return header_identity


def _build_analysis_gateway(settings: Settings) -> IncidentAnalysisGateway | None:
    """Pick the LLM provider behind the incident-analysis port (Phase 2 boundary in action)."""
    if settings.llm_provider == "vertex":
        return VertexLlamaIncidentAnalysisGateway(
            settings.vertex_project, settings.vertex_location, settings.vertex_model
        )
    if settings.ollama_url:
        return OllamaIncidentAnalysisGateway(settings.ollama_url, settings.llm_model)
    return None


def create_application() -> FastAPI:
    settings = get_settings()

    # Order matters: tracing first so the tracer provider (with its service.name resource)
    # exists before LoggingInstrumentor captures it for log<->trace correlation.
    configure_tracing()
    logger = configure_logging()
    metrics = configure_metrics()

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    get_session = get_session_dependency(session_factory)

    service_graph = StaticServiceGraph(SERVICE_DEPENDENCIES)
    change_log = InMemoryChangeLog()
    get_identity = _build_identity_dependency(settings, logger)

    metrics_gateway = PrometheusMetricsGateway(settings.prometheus_url)
    detectors = build_detectors(metrics_gateway)

    runbook_catalog = StaticRunbookCatalog()
    analysis_gateway = _build_analysis_gateway(settings)
    embedder: EmbeddingGateway | None = (
        OllamaEmbeddingGateway(settings.ollama_url, settings.embedding_model)
        if settings.ollama_url and settings.enable_incident_memory
        else None
    )
    notifier: IncidentNotifier | None = (
        SlackIncidentNotifier(settings.slack_webhook_url)
        if settings.slack_webhook_url
        else None
    )

    # Phase 3 guarded actuation: the KubernetesActuator refuses execute/undo while
    # ACTUATION_ENABLED is false (default), so wiring it in stays read-only. The registry's
    # cluster client is built lazily, so no cluster is needed to boot.
    cluster_client = KubernetesClusterClient(settings.kube_context)
    actuator = KubernetesActuator(
        StaticRunbookRegistry(cluster_client),
        execution_enabled=settings.actuation_enabled,
    )
    policy_engine = RuleBasedPolicyEngine(
        allow_auto=settings.auto_execute_enabled,
        auto_thresholds=_build_auto_thresholds(settings),
    )
    # Phase 4c: the flywheel + circuit breaker are now durable (Postgres), built per request
    # from the session — they survive restarts/replicas. Only the stateless probe is a singleton.
    breaker_max_actions = settings.circuit_breaker_max_actions
    breaker_window = timedelta(seconds=settings.circuit_breaker_window_seconds)
    recovery_probe = _build_recovery_probe(settings, metrics_gateway)
    enabled_environments = _csv(settings.actuation_environments)
    enabled_runbooks = _csv(settings.actuation_runbooks)
    remediation_selector = RuleBasedRemediationSelector(
        Environment(settings.auto_remediation_environment)
    )

    def _build_sweep(session) -> AutoRemediationSweep:
        plans = SqlAlchemyRemediationPlanRepository(session)
        incidents = SqlAlchemyIncidentRepository(session)
        audit = SqlAlchemyAuditLog(session)
        outcomes = SqlAlchemyOutcomeStore(session)
        breaker = SqlAlchemyCircuitBreaker(session, breaker_max_actions, breaker_window)
        auto = AutoRemediate(
            PlanRemediation(
                actuator, policy_engine, runbook_catalog, audit, plans, incidents,
                outcomes, service_graph,
            ),
            ExecuteRemediation(
                actuator, plans, audit, breaker, enabled_environments, enabled_runbooks
            ),
            VerifyRemediation(plans, incidents, audit, outcomes, recovery_probe),
        )
        return AutoRemediationSweep(incidents, plans, remediation_selector, auto)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks: list[asyncio.Task] = []
        if settings.enable_reconcile_loop:
            tasks.append(
                asyncio.create_task(
                    run_reconcile_loop(
                        session_factory, logger, settings.reconcile_interval_seconds
                    )
                )
            )
        if settings.enable_auto_remediation_loop:
            tasks.append(
                asyncio.create_task(
                    run_auto_remediation_loop(
                        session_factory,
                        logger,
                        settings.auto_remediation_interval_seconds,
                        _build_sweep,
                    )
                )
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()

    application = FastAPI(
        title="Aegis — Self-Healing Ops Platform (Phase 2)", lifespan=lifespan
    )
    FastAPIInstrumentor.instrument_app(application)

    application.include_router(
        build_incident_router(
            incidents_recorded=metrics.incidents_recorded,
            logger=logger,
            get_session=get_session,
        )
    )
    application.include_router(
        build_signal_router(
            signals_ingested=metrics.signals_ingested,
            logger=logger,
            get_session=get_session,
            service_graph=service_graph,
            change_log=change_log,
        )
    )
    application.include_router(
        build_detection_router(
            logger=logger,
            get_session=get_session,
            detectors=detectors,
            service_graph=service_graph,
            change_log=change_log,
        )
    )
    application.include_router(
        build_analysis_router(
            logger=logger,
            get_session=get_session,
            gateway=analysis_gateway,
            catalog=runbook_catalog,
            embedder=embedder,
            recall_limit=settings.rag_recall_limit,
            notifier=notifier,
        )
    )
    application.include_router(
        build_remediation_router(
            logger=logger,
            get_session=get_session,
            get_identity=get_identity,
            actuator=actuator,
            policy=policy_engine,
            catalog=runbook_catalog,
            recovery_probe=recovery_probe,
            service_graph=service_graph,
            enabled_environments=enabled_environments,
            enabled_runbooks=enabled_runbooks,
            breaker_max_actions=breaker_max_actions,
            breaker_window=breaker_window,
        )
    )

    return application


app = create_application()


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
