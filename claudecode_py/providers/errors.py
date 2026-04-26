from __future__ import annotations


class ProviderError(RuntimeError):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderPermissionError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderNetworkError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderCapabilityError(ProviderError):
    pass


def classify_status_error(status_code: int | None, message: str) -> ProviderError:
    if status_code == 401:
        return ProviderAuthError(f"Authentication failed: {message}")
    if status_code == 403:
        return ProviderPermissionError(f"Permission denied by provider: {message}")
    if status_code == 429:
        return ProviderRateLimitError(f"Rate limit exceeded: {message}")
    if status_code is not None and 500 <= status_code < 600:
        return ProviderNetworkError(f"Provider server error ({status_code}): {message}")
    return ProviderError(message)
