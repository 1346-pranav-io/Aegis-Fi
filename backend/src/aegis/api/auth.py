"""Authentication and role checks (D-15, D-30, NFR-SEC-01).

Bearer JWTs. The verifier is configured with an explicit algorithm list (never `none`), a required
issuer and audience, and required `exp` and `sub` claims. HS256 is for local development only (a
shared secret of at least 32 bytes); RS256 takes a PEM public key, which is how an OIDC provider's
keys would be supplied. Roles come from the token's `roles` claim and are mapped through the `Role`
enum: an unknown role string is ignored, so a token cannot invent privileges.

The human id is the token subject. Clients never send it in a request body.
"""

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum

import jwt
from fastapi import Depends, HTTPException, Request

from aegis.common.errors import PermissionDenied

ALLOWED_ALGORITHMS = ("HS256", "RS256")
MIN_HS_SECRET_BYTES = 32


class Role(StrEnum):
    ANALYST = "analyst"
    APPROVER = "approver"
    MODEL_RISK = "model_risk"
    AUDITOR = "auditor"
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: frozenset[Role]

    def has_any(self, roles: Collection[Role]) -> bool:
        return bool(self.roles & set(roles))


class JwtVerifier:
    def __init__(self, *, algorithm: str, key: str, issuer: str, audience: str) -> None:
        if algorithm not in ALLOWED_ALGORITHMS:
            raise ValueError(f"algorithm must be one of {ALLOWED_ALGORITHMS}")
        if algorithm == "HS256" and len(key.encode()) < MIN_HS_SECRET_BYTES:
            raise ValueError("an HS256 secret must be at least 32 bytes")
        if algorithm == "HS256" and key.lstrip().startswith("-----BEGIN"):
            # algorithm-confusion guard: a public key must never be used as a shared secret
            raise ValueError("an HS256 secret must not be a PEM key")
        if not issuer or not audience:
            raise ValueError("issuer and audience are required")
        self._alg, self._key, self._iss, self._aud = algorithm, key, issuer, audience

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=[self._alg],
                issuer=self._iss,
                audience=self._aud,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise PermissionDenied("invalid token") from exc
        # one identity, one spelling: the requester != decider rule compares these strings
        sub = str(claims["sub"]).strip().casefold()
        if not sub:
            raise PermissionDenied("token has an empty subject")
        known = {x.value for x in Role}
        raw_roles = claims.get("roles", [])
        if not isinstance(raw_roles, list):
            raise PermissionDenied("invalid token: roles must be a list")
        roles = {Role(r) for r in raw_roles if isinstance(r, str) and r in known}
        return Principal(sub, frozenset(roles))


def issue_dev_token(
    *, secret: str, user_id: str, roles: Collection[Role], issuer: str, audience: str,
    ttl_s: int = 3600,
) -> str:  # fmt: skip
    """LOCAL DEVELOPMENT ONLY: signs an HS256 token with the shared dev secret. A real deployment
    gets tokens from its OIDC provider and never calls this."""
    if len(secret.encode()) < MIN_HS_SECRET_BYTES:
        raise ValueError("an HS256 secret must be at least 32 bytes")
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "roles": sorted(r.value for r in roles), "iss": issuer, "aud": audience,
         "iat": now, "exp": now + ttl_s},
        secret,
        algorithm="HS256",
    )  # fmt: skip


def get_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401, detail="missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        principal: Principal = request.app.state.verifier.verify(token.strip())
    except PermissionDenied as exc:
        raise HTTPException(
            status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    return principal


def require(*roles: Role) -> Callable[[Principal], Principal]:
    """Dependency factory: the caller must hold at least one of `roles`. Deny by default."""
    allowed = frozenset(roles)
    if not allowed:
        raise ValueError("require() needs at least one role")

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:  # noqa: B008
        if not principal.has_any(allowed):
            raise HTTPException(status_code=403, detail="your role may not do this")
        return principal

    dependency.allowed_roles = allowed  # type: ignore[attr-defined]  # read by the RBAC matrix test
    return dependency


READERS = (Role.ANALYST, Role.APPROVER, Role.AUDITOR, Role.ADMIN)
WORKERS = (Role.ANALYST, Role.APPROVER)
