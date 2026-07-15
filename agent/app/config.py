"""Typed application configuration (ARCHITECTURE.md §2, D3).

Configuration is wired in here, not in business logic (D-DI). Required secrets have
no defaults, so a missing one raises at construction time — fail-fast at startup,
never a 500 at request time. OpenEMR URLs are pinned to https:// (F-S.9: TLS is
edge-only on the deployment, so the agent must reject a downgrade). Secrets are
wrapped in Pydantic SecretStr so they never leak through repr()/logs.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read only from the real process environment — never auto-load a file, so config is a
    # deliberate wiring step and tests stay isolated. Locally, source the gitignored
    # agent/.env into the environment before running (`set -a; . .env; set +a`); on Railway
    # the platform injects the same vars. Secrets live in .env / the platform, never source.
    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # --- OpenEMR (Zone A) — read-only FHIR + OAuth surfaces (D9) ---
    openemr_fhir_base_url: HttpUrl = Field(
        ..., description="OpenEMR FHIR R4 base, e.g. .../apis/default/fhir"
    )
    openemr_oauth_base_url: HttpUrl = Field(
        ..., description="OpenEMR OAuth2 base, e.g. .../oauth2/default"
    )

    # --- SMART client (D2/D9): the agent's registered, enabled OAuth client ---
    smart_client_id: str = Field(..., min_length=1)
    smart_client_secret: SecretStr = Field(...)
    # The agent's own public callback (the OAuth redirect_uri the client is registered with).
    agent_callback_url: str = Field(
        default="http://localhost:8000/callback", min_length=1
    )
    token_lifetime_seconds: int = Field(
        default=3600, gt=0
    )  # delegated-token session lifetime bound

    # --- LLM provider (Zone C, D4) ---
    anthropic_api_key: SecretStr = Field(...)
    llm_model: str = Field(
        default="claude-sonnet-4-6", min_length=1
    )  # primary (D4); swap = config
    # A rich patient (many conditions/meds/labs) needs a large forced submit_claims payload —
    # one cited claim per fact. At 2048 the tool call truncates (stop_reason=max_tokens) and the
    # claims are lost, degrading every brief to the D13 fallback. 8192 holds the full typed brief.
    llm_max_tokens: int = Field(default=8192, gt=0)
    # A large-packet UC1 brief (many structured claims) can take >30s to generate; the default
    # SDK timeout is too short and times out into the D13 fallback. Give it real headroom.
    llm_timeout_seconds: float = Field(default=90.0, gt=0)
    llm_max_tool_iterations: int = Field(
        default=6, gt=0
    )  # tool loop cap → D13 if not converged
    # Small daily USD cap — first real LLM spend (E5). A trip degrades to the D13 fallback,
    # never an uncapped bill. In-process for the demo; prod needs a shared counter.
    daily_cost_cap_usd: float = Field(default=5.0, gt=0)

    # --- Session store (Postgres, D-O2 / §3a) ---
    session_store_dsn: SecretStr = Field(...)

    # --- Observability (Langfuse, D5) — optional: soft dependency (§6/§7) ---
    langfuse_host: HttpUrl | None = Field(default=None)
    langfuse_public_key: SecretStr | None = Field(default=None)
    langfuse_secret_key: SecretStr | None = Field(default=None)
    langfuse_log_content: bool = Field(
        default=False,
        description=(
            "When true, send raw prompt/answer content to Langfuse. Default OFF = D5 "
            "minimum-necessary. Enable ONLY on synthetic-data deployments; prod has no BAA "
            "on the US region."
        ),
    )

    # --- Turn/session budgets (§3a, D10) ---
    fhir_per_call_timeout_seconds: float = Field(default=8.0, gt=0)
    turn_total_budget_seconds: float = Field(default=30.0, gt=0)
    session_idle_timeout_seconds: int = Field(default=1800, gt=0)
    session_turn_cap: int = Field(default=20, gt=0)

    # --- W2 document runtime (W2-D1/D9/D10; §2/§3) ---
    # Disabled by default so the frozen W1 serving contract remains bootable. Enabling it
    # requires the replacement SMART client plus both OA3 category attestations and the
    # non-DEBUG Binary-readback guard; partial configuration fails at startup.
    w2_document_runtime_enabled: bool = False
    openemr_rest_base_url: HttpUrl | None = None
    source_document_path: str = "/AI-Source-Documents"
    source_document_category_id: str | None = None
    source_document_category_acl: str | None = None
    artifact_document_path: str = "/AI-Extractions"
    artifact_document_category_id: str | None = None
    artifact_document_category_acl: str | None = None
    # OpenEMR's document/vital standard routes use legacy numeric pid/eid even
    # though SMART/FHIR binds the session with UUIDs. Activation discovers these
    # exact synthetic-only pairs read-only; any other UUID fails closed.
    openemr_legacy_patient_uuid: str | None = None
    openemr_legacy_patient_id: str | None = None
    openemr_legacy_encounter_uuid: str | None = None
    openemr_legacy_encounter_id: str | None = None
    openemr_binary_readback_safe: bool = False
    document_credential_key: SecretStr | None = Field(
        default=None,
        description=(
            "URL-safe base64 Fernet key for the separately encrypted delegated-job "
            "credential; required only when the W2 document runtime is enabled"
        ),
    )
    document_worker_poll_seconds: float = Field(default=1.0, gt=0)
    document_worker_lease_seconds: int = Field(default=60, gt=0)
    document_worker_max_attempts: int = Field(default=3, gt=0)
    document_worker_base_backoff_seconds: int = Field(default=5, gt=0)
    document_worker_id: str = Field(default="document-worker", min_length=1)
    agent_version: str = Field(default="0.1.0", min_length=1)

    @field_validator(
        "openemr_fhir_base_url", "openemr_oauth_base_url", "openemr_rest_base_url"
    )
    @classmethod
    def _require_https(cls, v: HttpUrl | None) -> HttpUrl | None:
        # F-S.9 — reject a plaintext downgrade; TLS terminates at the Railway edge.
        if v is not None and v.scheme != "https":
            raise ValueError(
                "OpenEMR URLs must be https:// (F-S.9: no plaintext downgrade)"
            )
        return v

    @field_validator("document_credential_key")
    @classmethod
    def _require_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        try:
            Fernet(value.get_secret_value().encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError(
                "DOCUMENT_CREDENTIAL_KEY must be a valid Fernet key"
            ) from exc
        return value

    @field_validator(
        "openemr_legacy_patient_uuid", "openemr_legacy_encounter_uuid"
    )
    @classmethod
    def _require_canonical_uuid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            canonical = str(UUID(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("legacy route UUID must be canonical") from exc
        if canonical != value:
            raise ValueError("legacy route UUID must be canonical")
        return value

    @field_validator("openemr_legacy_patient_id", "openemr_legacy_encounter_id")
    @classmethod
    def _require_canonical_legacy_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value.isascii()
            or not value.isdecimal()
            or int(value) <= 0
            or str(int(value)) != value
        ):
            raise ValueError("legacy route ID must be a positive canonical decimal")
        return value

    @model_validator(mode="after")
    def _require_complete_document_runtime(self) -> "Settings":
        if not self.w2_document_runtime_enabled:
            return self
        missing: list[str] = []
        if self.openemr_rest_base_url is None:
            missing.append("OPENEMR_REST_BASE_URL")
        callback = urlsplit(self.agent_callback_url)
        if (
            callback.scheme != "https"
            or not callback.netloc
            or callback.path != "/callback"
            or callback.query
            or callback.fragment
        ):
            missing.append("AGENT_CALLBACK_URL=https://<agent-host>/callback")
        if self.source_document_path != "/AI-Source-Documents":
            missing.append("SOURCE_DOCUMENT_PATH=/AI-Source-Documents")
        if not (self.source_document_category_id or "").strip():
            missing.append("SOURCE_DOCUMENT_CATEGORY_ID")
        if self.source_document_category_acl != "patients|docs":
            missing.append("SOURCE_DOCUMENT_CATEGORY_ACL=patients|docs")
        if self.artifact_document_path != "/AI-Extractions":
            missing.append("ARTIFACT_DOCUMENT_PATH=/AI-Extractions")
        if not (self.artifact_document_category_id or "").strip():
            missing.append("ARTIFACT_DOCUMENT_CATEGORY_ID")
        if self.artifact_document_category_acl != "patients|docs":
            missing.append("ARTIFACT_DOCUMENT_CATEGORY_ACL=patients|docs")
        if self.openemr_legacy_patient_uuid is None:
            missing.append("OPENEMR_LEGACY_PATIENT_UUID")
        if self.openemr_legacy_patient_id is None:
            missing.append("OPENEMR_LEGACY_PATIENT_ID")
        if self.openemr_legacy_encounter_uuid is None:
            missing.append("OPENEMR_LEGACY_ENCOUNTER_UUID")
        if self.openemr_legacy_encounter_id is None:
            missing.append("OPENEMR_LEGACY_ENCOUNTER_ID")
        if not self.openemr_binary_readback_safe:
            missing.append("OPENEMR_BINARY_READBACK_SAFE=true")
        if self.document_credential_key is None:
            missing.append("DOCUMENT_CREDENTIAL_KEY")
        if missing:
            raise ValueError(
                "W2 document runtime requires attested configuration: "
                + ", ".join(missing)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (constructed from the real env)."""
    return Settings()
