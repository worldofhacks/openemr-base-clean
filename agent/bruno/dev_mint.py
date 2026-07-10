#!/usr/bin/env python3
"""Mint a short-lived SMART-backed agent session for the F5 Bruno collection.

The delegated OAuth token stays in the agent. This helper drives the browser-only
SMART flow and stores only the opaque ``session_id`` in a gitignored Bruno
environment (§7, D14).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit


DEFAULT_AGENT_BASE_URL = "https://agent-production-9f62.up.railway.app"
DEFAULT_SELENIUM_URL = "http://localhost:4444/wd/hub"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "environments" / "Runtime.bru"
_OPAQUE_SESSION_RE = re.compile(r"[A-Za-z0-9_-]+")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class MintError(RuntimeError):
    """A safe-to-display failure from the dev-only SMART launch helper."""


class SessionPayload(NamedTuple):
    session_id: str
    patient_id: str


def parse_callback_payload(raw_body: str) -> SessionPayload:
    """Parse the agent callback without reflecting OAuth-bearing content in errors."""
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        raise MintError("the agent callback did not return its JSON session envelope") from None

    if not isinstance(payload, dict):
        raise MintError("the agent callback JSON was not an object")
    session_id = payload.get("session_id")
    patient_id = payload.get("patient_id")
    if not isinstance(session_id, str) or not session_id:
        raise MintError("the agent callback did not include a session_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise MintError("the agent callback did not include a patient_id")
    if _OPAQUE_SESSION_RE.fullmatch(session_id) is None:
        raise MintError("the callback session_id was not in the expected opaque format")
    return SessionPayload(session_id=session_id, patient_id=patient_id)


def _normalize_agent_base_url(agent_base_url: str) -> str:
    url = agent_base_url.rstrip("/")
    parts = urlsplit(url)
    is_loopback_http = parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    if parts.scheme != "https" and not is_loopback_http:
        raise MintError("the agent URL must use HTTPS (HTTP is allowed only for loopback development)")
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise MintError("the agent URL must be a plain absolute base URL")
    if any(character.isspace() for character in url) or "{" in url or "}" in url:
        raise MintError("the agent URL contains unsupported characters")
    return url


def render_runtime_environment(agent_base_url: str, session_id: str) -> str:
    """Render the minimum Bruno environment; never store patient data or bearer tokens."""
    base_url = _normalize_agent_base_url(agent_base_url)
    if _OPAQUE_SESSION_RE.fullmatch(session_id) is None:
        raise MintError("the session id contains unsupported characters")
    return (
        "vars {\n"
        f"  agent_base_url: {base_url}\n"
        f"  session_id: {session_id}\n"
        "}\n"
    )


def write_runtime_environment(output: Path, agent_base_url: str, session_id: str) -> None:
    """Atomically replace the gitignored runtime environment with owner-only permissions."""
    rendered = render_runtime_environment(agent_base_url, session_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=".Runtime.", delete=False
        ) as temporary:
            temporary.write(rendered)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(output)
        output.chmod(0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def mint_session(
    *,
    agent_base_url: str,
    selenium_url: str,
    username: str,
    password: str,
    patient_index: int,
    timeout_seconds: int,
) -> SessionPayload:
    """Drive standalone SMART launch/patient and return the agent's opaque session envelope."""
    base_url = _normalize_agent_base_url(agent_base_url)
    if patient_index < 0:
        raise MintError("patient index must be zero or greater")
    if timeout_seconds <= 0:
        raise MintError("timeout must be greater than zero")

    try:
        from selenium import webdriver
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise MintError('Selenium is missing; run `python -m pip install -e ".[dev]"` in agent/') from exc

    driver = None
    try:
        driver = webdriver.Remote(command_executor=selenium_url, options=Options())
        driver.set_page_load_timeout(timeout_seconds)
        driver.get(f"{base_url}/launch")
        wait = WebDriverWait(driver, timeout_seconds)

        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(username)
        driver.find_element(By.NAME, "password").send_keys(password)
        role_button = next(
            (
                button
                for button in driver.find_elements(By.CSS_SELECTOR, "button[name='user_role']")
                if "OpenEMR" in button.text
            ),
            None,
        )
        if role_button is None:
            raise MintError("the OpenEMR login role button was not found")
        role_button.click()

        # Standalone launch/patient normally displays a patient selector. Consent may be
        # remembered, so tolerate a direct transition to authorization or callback.
        try:
            WebDriverWait(driver, min(timeout_seconds, 15)).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button.patient-btn"))
            )
            patient_buttons = driver.find_elements(By.CSS_SELECTOR, "button.patient-btn")
            if patient_index >= len(patient_buttons):
                raise MintError("the requested synthetic patient index is not available")
            patient_buttons[patient_index].click()
        except TimeoutException:
            pass

        if "/callback" not in urlsplit(driver.current_url).path:
            wait.until(EC.element_to_be_clickable((By.ID, "authorize-btn"))).click()
        wait.until(lambda current: "/callback" in urlsplit(current.current_url).path)
        callback_body = driver.find_element(By.TAG_NAME, "body").text
        return parse_callback_payload(callback_body)
    except MintError:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitize browser/OAuth details before display
        raise MintError(
            f"the SMART browser flow failed ({type(exc).__name__}); check Selenium, credentials, and D14 enablement"
        ) from None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mint a SMART-backed session into Bruno's gitignored Runtime environment."
    )
    parser.add_argument(
        "--agent-base-url",
        default=os.environ.get("AGENT_BASE_URL", DEFAULT_AGENT_BASE_URL),
        help="deployed agent base URL (default: current demo agent)",
    )
    parser.add_argument(
        "--selenium-url",
        default=os.environ.get("SELENIUM_URL", DEFAULT_SELENIUM_URL),
        help="Selenium Remote WebDriver URL",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("OE_USERNAME", "admin"),
        help="synthetic-demo OpenEMR username (default: admin)",
    )
    parser.add_argument(
        "--patient-index",
        type=int,
        default=0,
        help="zero-based synthetic patient selector index (default: 0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="browser-step timeout in seconds (default: 60)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    password = os.environ.get("OE_ADMIN_PASS") or getpass.getpass(
        "Synthetic-demo OpenEMR password: "
    )
    if not password:
        print("dev-mint failed: a synthetic-demo OpenEMR password is required", file=sys.stderr)
        return 2
    try:
        session = mint_session(
            agent_base_url=args.agent_base_url,
            selenium_url=args.selenium_url,
            username=args.username,
            password=password,
            patient_index=args.patient_index,
            timeout_seconds=args.timeout,
        )
        write_runtime_environment(DEFAULT_OUTPUT, args.agent_base_url, session.session_id)
    except MintError as exc:
        print(f"dev-mint failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"SMART session created; wrote {DEFAULT_OUTPUT} with owner-only permissions "
        "(session id not printed)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
