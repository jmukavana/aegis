from fastapi import HTTPException, Request, status
from opentelemetry import trace

from src.domain.auth.identity import Identity
from src.infrastructure.auth.keycloak_token_validator import (
    InvalidTokenError,
    KeycloakTokenValidator,
)

tracer = trace.get_tracer(__name__)


def _extract_bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    return token


def build_keycloak_identity_dependency(validator: KeycloakTokenValidator, logger):
    """The real-auth swap-in for the Phase 3b bootstrap header identity (PHASE3_DESIGN.md §7
    decision 3): same `Identity(subject, permissions)` shape callers depend on, now backed by a
    signature-verified bearer JWT instead of trusted `X-Subject`/`X-Permissions` headers.
    Nothing downstream (RBAC checks, audit) changes — only how the identity is established.
    """

    async def get_identity(request: Request) -> Identity:
        token = _extract_bearer_token(request)
        with tracer.start_as_current_span("authenticate_request") as span:
            try:
                claims = await validator.validate(token)
            except InvalidTokenError as error:
                logger.info("Rejected invalid bearer token: %s", error)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
                ) from error
            span.set_attribute("identity.subject", claims.subject)
        return Identity(subject=claims.subject, permissions=claims.permissions)

    return get_identity
