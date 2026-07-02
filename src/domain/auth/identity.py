from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    """Who is making a request and what they're allowed to do.

    Deliberately just a subject + a set of permission strings — the same shape the sibling
    `Observability` project's Keycloak/dynamic-RBAC model produces. Phase 3b resolves it from a
    bootstrap header adapter; swapping in Keycloak JWT validation later changes only *how* this
    is built, not the permission names checked against it (ARCHITECTURE.md §3.7).
    """

    subject: str
    permissions: frozenset[str]

    def has(self, permission: str) -> bool:
        return permission in self.permissions
