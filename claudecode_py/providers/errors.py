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


class ProviderContextLimitError(ProviderError):
    pass


_CONTEXT_LIMIT_PHRASES = (
    "maximum context length",
    "context length",
    "context window",
    "prompt is too long",
    "prompt too long",
    "too many tokens",
    "input is too long",
)


def classify_context_limit_error(
    status_code: int | None,
    message: str,
) -> ProviderContextLimitError | None:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return None
    if not any(phrase in normalized for phrase in _CONTEXT_LIMIT_PHRASES):
        return None
    if status_code in {400, 413} or status_code is None:
        return ProviderContextLimitError(message)
    return None


def classify_status_error(status_code: int | None, message: str) -> ProviderError:
    context_limit_error = classify_context_limit_error(status_code, message)
    if context_limit_error is not None:
        return context_limit_error
    if status_code == 401:
        return ProviderAuthError(f"Authentication failed: {message}")
    if status_code == 403:
        return ProviderPermissionError(f"Permission denied by provider: {message}")
    if status_code == 429:
        return ProviderRateLimitError(f"Rate limit exceeded: {message}")
    if status_code is not None and 500 <= status_code < 600:
        return ProviderNetworkError(f"Provider server error ({status_code}): {message}")
    return ProviderError(message)
