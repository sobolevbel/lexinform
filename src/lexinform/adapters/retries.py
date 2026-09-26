def backoff_delay(base_seconds: float, attempt: int) -> float:
    return float(base_seconds * (2 ** (attempt - 1)))
