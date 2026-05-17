"""Eigene Exceptions für den AI-Schicht."""


class RateLimitError(Exception):
    """Wird geworfen wenn ein Provider das Rate-Limit erreicht hat."""
    def __init__(self, wait_seconds: float = 1200, message: str = ""):
        self.wait_seconds = wait_seconds
        super().__init__(message or f"Rate-Limit erreicht. Erneut in {wait_seconds:.0f}s verfügbar.")


class ProviderError(Exception):
    """Allgemeiner Provider-Fehler (Verbindung, Timeout, etc.)."""
    pass
