from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.providers.anthropic import AnthropicProvider
from claudecode_py.providers.errors import (
    ProviderAuthError,
    ProviderConfigurationError,
    ProviderNetworkError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from claudecode_py.providers.openai_compatible import OpenAICompatibleProvider


class _FakeStatusError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class ProviderErrorsTests(unittest.TestCase):
    def test_openai_provider_missing_key_is_configuration_error(self) -> None:
        provider = OpenAICompatibleProvider(model="m", max_tokens=1, api_key=None, base_url=None)
        original_api_key = provider.api_key
        provider.api_key = None
        try:
            with self.assertRaises(ProviderConfigurationError):
                provider._ensure_client()
        finally:
            provider.api_key = original_api_key

    def test_openai_status_error_mapping(self) -> None:
        provider = OpenAICompatibleProvider(model="m", max_tokens=1, api_key="k", base_url=None)
        self.assertIsInstance(provider._wrap_error(_FakeStatusError(401, "bad key")), ProviderAuthError)
        self.assertIsInstance(provider._wrap_error(_FakeStatusError(403, "forbidden")), ProviderPermissionError)
        self.assertIsInstance(provider._wrap_error(_FakeStatusError(429, "rate")), ProviderRateLimitError)

    def test_openai_network_and_timeout_mapping(self) -> None:
        provider = OpenAICompatibleProvider(model="m", max_tokens=1, api_key="k", base_url=None)
        self.assertIsInstance(provider._wrap_error(APITimeoutError("slow")), ProviderTimeoutError)
        self.assertIsInstance(provider._wrap_error(APIConnectionError("offline")), ProviderNetworkError)

    def test_anthropic_status_error_mapping(self) -> None:
        provider = AnthropicProvider(model="m", max_tokens=1, api_key="k")
        self.assertIsInstance(provider._wrap_error(_FakeStatusError(401, "bad key")), ProviderAuthError)
        self.assertIsInstance(provider._wrap_error(_FakeStatusError(403, "forbidden")), ProviderPermissionError)
        self.assertIsInstance(provider._wrap_error(_FakeStatusError(429, "rate")), ProviderRateLimitError)


if __name__ == "__main__":
    unittest.main()
