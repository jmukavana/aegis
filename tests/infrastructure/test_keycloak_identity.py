from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from src.infrastructure.auth.keycloak_identity import build_keycloak_identity_dependency
from src.infrastructure.auth.keycloak_token_validator import KeycloakTokenValidator

ISSUER = "http://keycloak/realms/aegis"
AUDIENCE = "aegis-api"


class _NullLogger:
    def info(self, *args, **kwargs) -> None:
        pass


def _request(authorization: str | None) -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/"}
    return Request(scope)


def _token(private_key, *, sub="alice", roles=("remediation:plan",)) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "realm_access": {"roles": list(roles)},
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _validator_trusting(monkeypatch, public_key) -> KeycloakTokenValidator:
    validator = KeycloakTokenValidator(ISSUER, AUDIENCE, jwks_url="http://unused/certs")
    fake_signing_key = type("FakeSigningKey", (), {"key": public_key})()
    monkeypatch.setattr(
        validator._jwks_client, "get_signing_key_from_jwt", lambda token: fake_signing_key
    )
    return validator


@pytest.mark.asyncio
async def test_valid_bearer_token_produces_matching_identity(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    validator = _validator_trusting(monkeypatch, private_key.public_key())
    get_identity = build_keycloak_identity_dependency(validator, _NullLogger())
    token = _token(private_key, sub="alice", roles=("remediation:plan", "action:execute:local"))

    identity = await get_identity(_request(f"Bearer {token}"))

    assert identity.subject == "alice"
    assert identity.has("remediation:plan")
    assert identity.has("action:execute:local")
    assert not identity.has("action:execute:staging")


@pytest.mark.asyncio
async def test_missing_authorization_header_is_401():
    get_identity = build_keycloak_identity_dependency(
        KeycloakTokenValidator(ISSUER, AUDIENCE, "http://unused/certs"), _NullLogger()
    )
    with pytest.raises(HTTPException) as excinfo:
        await get_identity(_request(None))
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_non_bearer_scheme_is_401():
    get_identity = build_keycloak_identity_dependency(
        KeycloakTokenValidator(ISSUER, AUDIENCE, "http://unused/certs"), _NullLogger()
    )
    with pytest.raises(HTTPException) as excinfo:
        await get_identity(_request("Basic dXNlcjpwYXNz"))
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_signature_is_401(monkeypatch):
    _, wrong_public_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key(),
    )
    real_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    validator = _validator_trusting(monkeypatch, wrong_public_key)
    get_identity = build_keycloak_identity_dependency(validator, _NullLogger())
    token = _token(real_private_key)

    with pytest.raises(HTTPException) as excinfo:
        await get_identity(_request(f"Bearer {token}"))
    assert excinfo.value.status_code == 401
