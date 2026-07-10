"""Unit tests for the F5 SMART-session mint helper (§7, D14)."""

from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "dev_mint.py"
SPEC = importlib.util.spec_from_file_location("bruno_dev_mint", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dev_mint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_mint)


class ParseCallbackPayloadTests(unittest.TestCase):
    def test_accepts_the_agents_callback_envelope(self) -> None:
        payload = dev_mint.parse_callback_payload(
            '{"session_id":"session_abc-123","patient_id":"synthetic-patient"}'
        )

        self.assertEqual(payload.session_id, "session_abc-123")
        self.assertEqual(payload.patient_id, "synthetic-patient")

    def test_rejects_a_callback_without_a_session_id(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "session_id"):
            dev_mint.parse_callback_payload('{"patient_id":"synthetic-patient"}')

    def test_rejects_non_json_callback_content_without_echoing_it(self) -> None:
        secretish_body = "oauth-code=must-not-be-echoed"

        with self.assertRaises(dev_mint.MintError) as raised:
            dev_mint.parse_callback_payload(secretish_body)

        self.assertNotIn(secretish_body, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_renders_only_the_base_url_and_opaque_session_id(self) -> None:
        rendered = dev_mint.render_runtime_environment(
            "https://agent.example.test/", "session_abc-123"
        )

        self.assertEqual(
            rendered,
            "vars {\n"
            "  agent_base_url: https://agent.example.test\n"
            "  session_id: session_abc-123\n"
            "}\n",
        )
        self.assertNotIn("patient", rendered.lower())
        self.assertNotIn("access_token", rendered.lower())

    def test_rejects_non_https_remote_agent_urls(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "HTTPS"):
            dev_mint.render_runtime_environment(
                "http://agent.example.test", "session_abc-123"
            )

    def test_allows_http_only_for_loopback_development(self) -> None:
        rendered = dev_mint.render_runtime_environment(
            "http://localhost:8000", "session_abc-123"
        )

        self.assertIn("agent_base_url: http://localhost:8000", rendered)

    def test_rejects_values_that_could_break_the_bru_file(self) -> None:
        with self.assertRaises(dev_mint.MintError):
            dev_mint.render_runtime_environment(
                "https://agent.example.test", "session\nleak"
            )

    def test_write_is_private_and_replaces_stale_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "Runtime.bru"
            output.write_text("stale secret\n", encoding="utf-8")

            dev_mint.write_runtime_environment(
                output, "https://agent.example.test", "session_abc-123"
            )

            self.assertEqual(
                output.read_text(encoding="utf-8"),
                dev_mint.render_runtime_environment(
                    "https://agent.example.test", "session_abc-123"
                ),
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


class TrustBoundaryTests(unittest.TestCase):
    def test_rejects_plaintext_remote_webdriver(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "HTTPS"):
            dev_mint.validate_selenium_url("http://selenium.example.test:4444/wd/hub")

    def test_allows_plaintext_loopback_webdriver(self) -> None:
        self.assertEqual(
            dev_mint.validate_selenium_url("http://127.0.0.1:4444/wd/hub"),
            "http://127.0.0.1:4444/wd/hub",
        )

    def test_allows_https_remote_webdriver(self) -> None:
        self.assertEqual(
            dev_mint.validate_selenium_url("https://selenium.example.test/wd/hub"),
            "https://selenium.example.test/wd/hub",
        )

    def test_accepts_the_expected_origin_with_an_explicit_default_port(self) -> None:
        dev_mint.require_expected_origin(
            "https://openemr.example.test:443/oauth2/default/authorize",
            "https://openemr.example.test",
            "OpenEMR login",
        )

    def test_rejects_a_credential_page_on_an_unexpected_origin(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "unexpected origin"):
            dev_mint.require_expected_origin(
                "https://lookalike.example.test/login",
                "https://openemr.example.test",
                "OpenEMR login",
            )


if __name__ == "__main__":
    unittest.main()
