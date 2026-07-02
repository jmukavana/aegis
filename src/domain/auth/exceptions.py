class PermissionDeniedError(Exception):
    """Raised when an identity lacks the permission a guarded action requires."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"missing required permission '{permission}'")
        self.permission = permission
