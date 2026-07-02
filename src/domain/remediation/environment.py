from enum import Enum


class Environment(str, Enum):
    """Where a remediation would run. Auto-execute and approval rules differ per environment
    (ARCHITECTURE.md §5 "environment scoping"): approve-in-staging never implies approve-in-prod.

    `LOCAL` is a dev cluster (minikube / a throwaway GKE cluster); `PROD` stays disabled through
    all of Phase 3.
    """

    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"
