from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.session import Session


class ProviderCapabilitiesViewTests(unittest.TestCase):
    def test_session_describes_provider_capabilities(self) -> None:
        session = Session(
            SessionConfig(
                cwd=Path(__file__).resolve().parent,
                interactive=False,
                provider="openai-compatible",
                model="gpt-test",
                api_key="k",
                base_url="https://example.test/v1",
            )
        )

        description = session.describe_provider()

        self.assertIn("provider: openai-compatible", description)
        self.assertIn("model: gpt-test", description)
        self.assertIn("tool_calling: yes", description)
        self.assertIn("streaming: yes", description)


if __name__ == "__main__":
    unittest.main()
