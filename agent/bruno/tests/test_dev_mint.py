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


class ParseAppRedirectTests(unittest.TestCase):
    def test_accepts_the_agents_app_redirect(self) -> None:
        payload = dev_mint.parse_app_redirect(
            "https://agent.example.test/app?sid=session_abc-123",
            "https://agent.example.test",
        )

        self.assertEqual(payload.session_id, "session_abc-123")

    def test_rejects_an_app_redirect_without_a_session_id(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "session id"):
            dev_mint.parse_app_redirect(
                "https://agent.example.test/app",
                "https://agent.example.test",
            )

    def test_rejects_duplicate_session_ids_without_echoing_them(self) -> None:
        secretish_url = (
            "https://agent.example.test/app?sid=must-not-be-echoed&sid=also-secret"
        )

        with self.assertRaises(dev_mint.MintError) as raised:
            dev_mint.parse_app_redirect(secretish_url, "https://agent.example.test")

        self.assertNotIn("must-not-be-echoed", str(raised.exception))
        self.assertNotIn("also-secret", str(raised.exception))

    def test_rejects_an_app_redirect_on_an_unexpected_origin(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "unexpected origin"):
            dev_mint.parse_app_redirect(
                "https://lookalike.example.test/app?sid=session_abc-123",
                "https://agent.example.test",
            )

    def test_rejects_a_non_app_path(self) -> None:
        with self.assertRaisesRegex(dev_mint.MintError, "app redirect"):
            dev_mint.parse_app_redirect(
                "https://agent.example.test/callback?sid=session_abc-123",
                "https://agent.example.test",
            )


class BrowserInterceptionTests(unittest.TestCase):
    def test_blocks_only_chat_on_the_expected_agent_origin(self) -> None:
        class RecordingDriver:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def execute_cdp_cmd(self, command: str, params: dict[str, object]) -> None:
                self.calls.append((command, params))

        driver = RecordingDriver()

        dev_mint.configure_chat_interception(driver, "https://agent.example.test/")

        self.assertEqual(
            driver.calls,
            [
                ("Network.enable", {}),
                (
                    "Network.setBlockedURLs",
                    {"urls": ["https://agent.example.test/chat*"]},
                ),
            ],
        )


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
