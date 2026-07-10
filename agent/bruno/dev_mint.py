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
DEFAULT_OPENEMR_BASE_URL = "https://openemr-production-cc95.up.railway.app"
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


def _normalize_service_url(value: str, label: str) -> str:
    url = value.rstrip("/")
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError:
        raise MintError(f"the {label} is not a valid absolute URL") from None
    is_loopback_http = parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    if parts.scheme != "https" and not is_loopback_http:
        raise MintError(
            f"the {label} must use HTTPS (HTTP is allowed only for loopback development)"
        )
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise MintError(f"the {label} must be a plain absolute URL")
    if any(character.isspace() for character in url) or "{" in url or "}" in url:
        raise MintError(f"the {label} contains unsupported characters")
    return url


def _normalize_agent_base_url(agent_base_url: str) -> str:
    return _normalize_service_url(agent_base_url, "agent URL")


def validate_selenium_url(selenium_url: str) -> str:
    """Require encrypted remote WebDriver transport; allow HTTP only on loopback."""
    return _normalize_service_url(selenium_url, "Selenium URL")


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        if not parts.hostname or parts.username or parts.password:
            return None
        port = parts.port
    except ValueError:
        return None
    if port is None:
        if parts.scheme == "https":
            port = 443
        elif parts.scheme == "http":
            port = 80
        else:
            return None
    is_loopback_http = parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    if parts.scheme != "https" and not is_loopback_http:
        return None
    return parts.scheme, parts.hostname.lower(), port


def require_expected_origin(actual_url: str, expected_base_url: str, stage: str) -> None:
    """Stop before credentials or callback content cross an unexpected browser origin."""
    expected = _normalize_service_url(expected_base_url, f"expected {stage} URL")
    if _origin(actual_url) != _origin(expected):
        raise MintError(f"{stage} reached an unexpected origin; refusing to continue")


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
    openemr_base_url: str,
    selenium_url: str,
    username: str,
    password: str,
    patient_index: int,
    timeout_seconds: int,
) -> SessionPayload:
    """Drive standalone SMART launch/patient and return the agent's opaque session envelope."""
    base_url = _normalize_agent_base_url(agent_base_url)
    openemr_url = _normalize_service_url(openemr_base_url, "OpenEMR URL")
    webdriver_url = validate_selenium_url(selenium_url)
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
        driver = webdriver.Remote(command_executor=webdriver_url, options=Options())
        driver.set_page_load_timeout(timeout_seconds)
        driver.get(f"{base_url}/launch")
        require_expected_origin(driver.current_url, openemr_url, "OpenEMR login")
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
            require_expected_origin(driver.current_url, openemr_url, "OpenEMR patient selection")
            patient_buttons = driver.find_elements(By.CSS_SELECTOR, "button.patient-btn")
            if patient_index >= len(patient_buttons):
                raise MintError("the requested synthetic patient index is not available")
            patient_buttons[patient_index].click()
        except TimeoutException:
            pass

        if "/callback" not in urlsplit(driver.current_url).path:
            authorize_button = wait.until(EC.element_to_be_clickable((By.ID, "authorize-btn")))
            require_expected_origin(driver.current_url, openemr_url, "OpenEMR authorization")
            authorize_button.click()
        wait.until(lambda current: "/callback" in urlsplit(current.current_url).path)
        require_expected_origin(driver.current_url, base_url, "agent callback")
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
        "--openemr-base-url",
        default=os.environ.get("OPENEMR_BASE_URL", DEFAULT_OPENEMR_BASE_URL),
        help="expected OpenEMR browser origin (default: current synthetic demo)",
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
            openemr_base_url=args.openemr_base_url,
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
