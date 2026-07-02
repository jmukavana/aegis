class InvalidRunbookParametersError(Exception):
    """A runbook was asked to act with missing or malformed parameters."""


def require(params: dict, key: str) -> str:
    value = params.get(key)
    if value is None or value == "":
        raise InvalidRunbookParametersError(f"missing required parameter '{key}'")
    return str(value)


def require_int(params: dict, key: str) -> int:
    raw = require(params, key)
    try:
        return int(raw)
    except ValueError as error:
        raise InvalidRunbookParametersError(
            f"parameter '{key}' must be an integer, got {raw!r}"
        ) from error
