"""Typed application configuration (ARCHITECTURE.md §2, D3).

Configuration is wired in here, not in business logic (D-DI). Required secrets have
no defaults, so a missing one raises at construction time — fail-fast at startup,
never a 500 at request time. OpenEMR URLs are pinned to https:// (F-S.9: TLS is
edge-only on the deployment, so the agent must reject a downgrade). Secrets are
wrapped in Pydantic SecretStr so they never leak through repr()/logs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read only from the real process environment — never auto-load a file, so config is a
    # deliberate wiring step and tests stay isolated. Locally, source the gitignored
    # agent/.env into the environment before running (`set -a; . .env; set +a`); on Railway
    # the platform injects the same vars. Secrets live in .env / the platform, never source.
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- OpenEMR (Zone A) — read-only FHIR + OAuth surfaces (D9) ---
    openemr_fhir_base_url: HttpUrl = Field(..., description="OpenEMR FHIR R4 base, e.g. .../apis/default/fhir")
    openemr_oauth_base_url: HttpUrl = Field(..., description="OpenEMR OAuth2 base, e.g. .../oauth2/default")

    # --- SMART client (D2/D9): the agent's registered, enabled OAuth client ---
    smart_client_id: str = Field(..., min_length=1)
    smart_client_secret: SecretStr = Field(...)
    # The agent's own public callback (the OAuth redirect_uri the client is registered with).
    agent_callback_url: str = Field(default="http://localhost:8000/callback", min_length=1)
    token_lifetime_seconds: int = Field(default=3600, gt=0)  # delegated-token session lifetime bound

    # --- LLM provider (Zone C, D4) ---
    anthropic_api_key: SecretStr = Field(...)
    llm_model: str = Field(default="claude-sonnet-4-6", min_length=1)  # primary (D4); swap = config
    # A rich patient (many conditions/meds/labs) needs a large forced submit_claims payload —
    # one cited claim per fact. At 2048 the tool call truncates (stop_reason=max_tokens) and the
    # claims are lost, degrading every brief to the D13 fallback. 8192 holds the full typed brief.
    llm_max_tokens: int = Field(default=8192, gt=0)
    # A large-packet UC1 brief (many structured claims) can take >30s to generate; the default
    # SDK timeout is too short and times out into the D13 fallback. Give it real headroom.
    llm_timeout_seconds: float = Field(default=90.0, gt=0)
    llm_max_tool_iterations: int = Field(default=6, gt=0)  # tool loop cap → D13 if not converged
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

    @field_validator("openemr_fhir_base_url", "openemr_oauth_base_url")
    @classmethod
    def _require_https(cls, v: HttpUrl) -> HttpUrl:
        # F-S.9 — reject a plaintext downgrade; TLS terminates at the Railway edge.
        if v.scheme != "https":
            raise ValueError("OpenEMR URLs must be https:// (F-S.9: no plaintext downgrade)")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (constructed from the real env)."""
    return Settings()
