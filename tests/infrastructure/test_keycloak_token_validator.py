from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.infrastructure.auth.keycloak_token_validator import (
    InvalidTokenError,
    KeycloakTokenValidator,
)

ISSUER = "http://keycloak/realms/aegis"
AUDIENCE = "aegis-api"


def _keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _token(
    private_key,
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    sub: str = "user-123",
    roles: tuple[str, ...] = (),
    exp_delta: timedelta = timedelta(minutes=5),
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "exp": now + exp_delta,
        "realm_access": {"roles": list(roles)},
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _validator_trusting(monkeypatch, public_key) -> KeycloakTokenValidator:
    """A validator whose JWKS lookup is stubbed to return a known key — tests our issuer/
    audience/expiry/claims logic without needing a live JWKS HTTP server."""
    validator = KeycloakTokenValidator(ISSUER, AUDIENCE, jwks_url="http://unused/certs")
    fake_signing_key = type("FakeSigningKey", (), {"key": public_key})()
    monkeypatch.setattr(
        validator._jwks_client, "get_signing_key_from_jwt", lambda token: fake_signing_key
    )
    return validator


@pytest.mark.asyncio
async def test_valid_token_extracts_subject_and_permissions(monkeypatch):
    private_key, public_key = _keypair()
    validator = _validator_trusting(monkeypatch, public_key)
    token = _token(private_key, sub="alice", roles=("remediation:plan", "action:execute:local"))

    claims = await validator.validate(token)

    assert claims.subject == "alice"
    assert claims.permissions == frozenset({"remediation:plan", "action:execute:local"})


@pytest.mark.asyncio
async def test_token_with_no_roles_has_empty_permissions(monkeypatch):
    private_key, public_key = _keypair()
    validator = _validator_trusting(monkeypatch, public_key)
    claims = await validator.validate(_token(private_key))
    assert claims.permissions == frozenset()


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(monkeypatch):
    private_key, public_key = _keypair()
    validator = _validator_trusting(monkeypatch, public_key)
    token = _token(private_key, audience="someone-else")
    with pytest.raises(InvalidTokenError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected(monkeypatch):
    private_key, public_key = _keypair()
    validator = _validator_trusting(monkeypatch, public_key)
    token = _token(private_key, issuer="http://not-keycloak/realms/other")
    with pytest.raises(InvalidTokenError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_expired_token_is_rejected(monkeypatch):
    private_key, public_key = _keypair()
    validator = _validator_trusting(monkeypatch, public_key)
    token = _token(private_key, exp_delta=timedelta(minutes=-5))
    with pytest.raises(InvalidTokenError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_token_signed_by_an_untrusted_key_is_rejected(monkeypatch):
    # Validator is configured to trust key B's public key; the token is signed with key A's
    # private key — a forged/unauthorized token must be rejected, not silently accepted.
    forged_private_key, _ = _keypair()
    _, trusted_public_key = _keypair()
    validator = _validator_trusting(monkeypatch, trusted_public_key)
    token = _token(forged_private_key)
    with pytest.raises(InvalidTokenError):
        await validator.validate(token)
