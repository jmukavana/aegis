class IncidentNotFoundError(Exception):
    def __init__(self, identifier: str) -> None:
        super().__init__(f"Incident not found: {identifier}")
        self.identifier = identifier
