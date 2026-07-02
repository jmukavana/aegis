import asyncio
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient


class InvalidTokenError(Exception):
    """Raised when a bearer token fails signature, issuer, audience, or expiry validation."""


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    permissions: frozenset[str]


class KeycloakTokenValidator:
    """Validates a bearer JWT against Keycloak's JWKS endpoint.

    Ported from the sibling `Observability` project's auth model (ARCHITECTURE.md §3.7), with
    one simplification: Observability maps coarse Keycloak realm roles ("admin"/"member") to a
    locally-managed Role/Permission table, because its permissions are a separate administered
    resource. Aegis's permissions were already free-form strings with no such local role layer
    (`remediation:plan`, `action:execute:local`, …), so realm roles ARE the permission strings
    directly here — one fewer moving part, and the same "permissions are dynamic strings" model
    this design already borrows. `runbook:manage`-style role administration, if ever needed,
    would still be managed in Keycloak, not duplicated into a second local table.

    `PyJWKClient` does blocking HTTP under the hood (fetching/caching the signing keys), so
    `validate` offloads to a worker thread — the same sync-adapter bridge used elsewhere
    (Kubernetes actuator, event watcher) to keep the async app non-blocking.
    """

    def __init__(self, issuer: str, audience: str, jwks_url: str) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_client = PyJWKClient(jwks_url)

    async def validate(self, token: str) -> TokenClaims:
        return await asyncio.to_thread(self._validate, token)

    def _validate(self, token: str) -> TokenClaims:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
            )
        except jwt.PyJWTError as error:
            raise InvalidTokenError(str(error)) from error

        return TokenClaims(
            subject=payload["sub"],
            permissions=frozenset(payload.get("realm_access", {}).get("roles", [])),
        )
