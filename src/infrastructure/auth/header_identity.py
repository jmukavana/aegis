from fastapi import Header, HTTPException

from src.domain.auth.identity import Identity


async def get_identity(
    x_subject: str = Header(default=""),
    x_permissions: str = Header(default=""),
) -> Identity:
    """Bootstrap identity resolver (Phase 3b): trusts `X-Subject` + comma-separated
    `X-Permissions` headers.

    This is the swap point for real auth, NOT production auth: it carries the exact permission
    names the Keycloak/OIDC model uses (`remediation:plan`, `action:execute:<env>`, …), so
    replacing it with a `KeycloakTokenValidator`-backed dependency — validating a bearer JWT and
    extracting the same permissions — changes nothing downstream (ARCHITECTURE.md §3.7).
    """
    if not x_subject:
        raise HTTPException(status_code=401, detail="X-Subject header required")
    permissions = frozenset(p.strip() for p in x_permissions.split(",") if p.strip())
    return Identity(subject=x_subject, permissions=permissions)
