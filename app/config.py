"""Runtime configuration. Every value comes from the environment (or .env), so switching an adapter from
mock to live is a config change, never a code change."""
from __future__ import annotations

import contextvars
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
if os.getenv("SCOPE2CASH_ENV_FILE"):  # optional extra env file kept outside the repo
    load_dotenv(os.environ["SCOPE2CASH_ENV_FILE"])

# Verified against https://tokenfactory.nebius.com/api/public/models_info on 2026-10-17.
DEFAULT_MODEL_FAST = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
DEFAULT_MODEL_MAIN = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_MODEL_DEEP = "nvidia/Nemotron-3-Ultra-550b-a55b"

# LLM providers (all OpenAI-compatible). `LLM_PROVIDER` picks one explicitly; otherwise the first provider whose key
# is set wins, in this order: nebius, deepseek, nvidia. Keys are read from the environment only, never from code.
PROVIDERS = {
    "nebius": {"label": "Nebius Token Factory", "family": "NVIDIA Nemotron", "key_env": "NEBIUS_API_KEY",
               "base_env": "NEBIUS_BASE_URL", "base": "https://api.tokenfactory.nebius.com/v1/",
               "models": (DEFAULT_MODEL_FAST, DEFAULT_MODEL_MAIN, DEFAULT_MODEL_DEEP), "effort": ("", "", "")},
    # Verified against GET https://api.deepseek.com/models and api-docs.deepseek.com/quick_start/pricing on 2026-10-17.
    "deepseek": {"label": "DeepSeek API", "family": "DeepSeek V4", "key_env": "DEEPSEEK_API_KEY",
                 "base_env": "DEEPSEEK_BASE_URL", "base": "https://api.deepseek.com",
                 "models": ("deepseek-flash", "deepseek-flash", "deepseek-v4-pro"), "effort": ("low", "low", "high")},
    "nvidia": {"label": "NVIDIA NIM", "family": "NVIDIA Nemotron", "key_env": "NVIDIA_API_KEY",
               "base_env": "NVIDIA_BASE_URL", "base": "https://integrate.api.nvidia.com/v1",
               "models": ("nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-super-120b-a12b",
                          "nvidia/nemotron-3-ultra-550b-a55b"), "effort": ("", "", "")},
    "custom": {"label": "OpenAI-compatible", "family": "LLM", "key_env": "LLM_API_KEY", "base_env": "LLM_BASE_URL",
               "base": "", "models": ("", "", ""), "effort": ("", "", "")},
}


def _provider() -> str:
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit in PROVIDERS:
        return explicit
    for name in ("nebius", "deepseek", "nvidia"):
        if (os.getenv(PROVIDERS[name]["key_env"]) or "").strip():
            return name
    return "nebius"


def _pv(i_field: str, idx: int = -1):
    p = PROVIDERS[_provider()]
    v = p[i_field]
    return v[idx] if idx >= 0 else v


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    v = _get(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # LLM: any OpenAI-compatible provider (DeepSeek, Nebius Token Factory, NVIDIA NIM, custom); see PROVIDERS
    llm_provider: str = field(default_factory=_provider)
    llm_label: str = field(default_factory=lambda: _pv("label"))
    llm_family: str = field(default_factory=lambda: _pv("family"))
    llm_api_key: str = field(default_factory=lambda: _get(_pv("key_env")))
    llm_base_url: str = field(default_factory=lambda: _get(_pv("base_env"), _pv("base")))
    nebius_api_key: str = field(default_factory=lambda: _get("NEBIUS_API_KEY"))
    nebius_base_url: str = field(default_factory=lambda: _get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"))
    model_fast: str = field(default_factory=lambda: _get("MODEL_FAST", _pv("models", 0)))
    model_main: str = field(default_factory=lambda: _get("MODEL_MAIN", _pv("models", 1)))
    model_deep: str = field(default_factory=lambda: _get("MODEL_DEEP", _pv("models", 2)))
    # Per-tier reasoning effort (DeepSeek: low/high/max). LLM_REASONING_EFFORT, if set, overrides all tiers.
    effort_fast: str = field(default_factory=lambda: _get("LLM_EFFORT_FAST", _pv("effort", 0)))
    effort_main: str = field(default_factory=lambda: _get("LLM_EFFORT_MAIN", _pv("effort", 1)))
    effort_deep: str = field(default_factory=lambda: _get("LLM_EFFORT_DEEP", _pv("effort", 2)))
    llm_timeout_s: float = field(default_factory=lambda: _float("LLM_TIMEOUT_S", 90))
    llm_max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 2))
    llm_json_mode: bool = field(default_factory=lambda: _bool("LLM_JSON_MODE", True))
    llm_reasoning_effort: str = field(default_factory=lambda: _get("LLM_REASONING_EFFORT"))  # "", low, medium, high
    llm_max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 4096))
    llm_check_models: bool = field(default_factory=lambda: _bool("LLM_CHECK_MODELS", True))
    # Search: Tavily
    tavily_api_key: str = field(default_factory=lambda: _get("TAVILY_API_KEY"))
    tavily_base_url: str = field(default_factory=lambda: _get("TAVILY_BASE_URL", "https://api.tavily.com"))
    tavily_search_depth: str = field(default_factory=lambda: _get("TAVILY_SEARCH_DEPTH", "basic"))
    search_timeout_s: float = field(default_factory=lambda: _float("SEARCH_TIMEOUT_S", 20))
    # Payments: PayPal Invoicing v2
    paypal_client_id: str = field(default_factory=lambda: _get("PAYPAL_CLIENT_ID"))
    paypal_client_secret: str = field(default_factory=lambda: _get("PAYPAL_CLIENT_SECRET"))
    paypal_env: str = field(default_factory=lambda: _get("PAYPAL_ENV", "sandbox"))
    paypal_invoicer_email: str = field(default_factory=lambda: _get("PAYPAL_INVOICER_EMAIL"))
    paypal_business_name: str = field(default_factory=lambda: _get("PAYPAL_BUSINESS_NAME"))
    paypal_timeout_s: float = field(default_factory=lambda: _float("PAYPAL_TIMEOUT_S", 30))
    # Webhooks: the ID PayPal shows when you subscribe a listener URL (Developer Dashboard > your app > Webhooks).
    paypal_webhook_id: str = field(default_factory=lambda: _get("PAYPAL_WEBHOOK_ID"))
    # Mock mode only: HMAC key for locally simulated webhook deliveries (never accepted for PayPal-hosted invoices).
    mock_webhook_secret: str = field(default_factory=lambda: _get("SCOPE2CASH_MOCK_WEBHOOK_SECRET", "s2c-local-mock-webhooks"))
    # PayPal Agent Toolkit tools the collections agent may use (read tools run directly; write tools need approval).
    agent_max_steps: int = field(default_factory=lambda: _int("AGENT_MAX_STEPS", 6))
    # Behaviour
    fallback_to_mock: bool = field(default_factory=lambda: _bool("FALLBACK_TO_MOCK", True))
    currency: str = field(default_factory=lambda: _get("APP_CURRENCY", "USD"))
    default_rate: float = field(default_factory=lambda: _float("DEFAULT_HOURLY_RATE", 45))
    freelancer_name: str = field(default_factory=lambda: _get("FREELANCER_NAME", "Huilin"))
    db_path: str = field(default_factory=lambda: _get("SCOPE2CASH_DB", str(ROOT / "data" / "scope2cash.db")))
    force_mock: bool = field(default_factory=lambda: _get("SCOPE2CASH_FORCE_MOCK") == "1")
    # Public-demo protection (for a hosted URL that judges can open)
    public_demo: bool = field(default_factory=lambda: _get("SCOPE2CASH_PUBLIC_DEMO") == "1")
    rate_limit_per_hour: int = field(default_factory=lambda: int(_float("SCOPE2CASH_RATE_LIMIT", 0)))
    daily_live_cap: int = field(default_factory=lambda: int(_float("SCOPE2CASH_DAILY_LIVE_CAP", 0)))
    demo_client_email: str = field(default_factory=lambda: _get("SCOPE2CASH_DEMO_CLIENT_EMAIL", "sarah@bloompilates.example.com"))
    recipient_domains: str = field(default_factory=lambda: _get("SCOPE2CASH_RECIPIENT_DOMAINS", "example.com"))

    @property
    def effective_rate_limit(self) -> int:
        """AI requests per client IP per hour; defaults to 30 in public-demo mode, off otherwise."""
        return self.rate_limit_per_hour or (30 if self.public_demo else 0)

    def recipient_allowed(self, email: str) -> bool:
        if not self.public_demo:
            return True
        domain = email.rsplit("@", 1)[-1].lower()
        allowed = [d.strip().lower() for d in self.recipient_domains.split(",") if d.strip()]
        return any(domain == d or domain.endswith("." + d) for d in allowed)

    @property
    def llm_live(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url) and not self.force_mock and not OFFLINE_THIS_REQUEST.get()

    def effort_for(self, tier: str) -> str:
        return self.llm_reasoning_effort or {"fast": self.effort_fast, "main": self.effort_main, "deep": self.effort_deep}.get(tier, "")

    @property
    def search_live(self) -> bool:
        return bool(self.tavily_api_key) and not self.force_mock and not OFFLINE_THIS_REQUEST.get()

    @property
    def payments_live(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret) and not self.force_mock

    @property
    def paypal_base(self) -> str:
        return "https://api-m.paypal.com" if self.paypal_env == "live" else "https://api-m.sandbox.paypal.com"

    @property
    def paypal_provider(self) -> str:
        return "paypal-live" if self.paypal_env == "live" else "paypal-sandbox"

    def models(self) -> dict:
        return {"fast": self.model_fast, "main": self.model_main, "deep": self.model_deep}


# Set per request when the daily live-call cap is spent, so that request runs on the offline heuristics.
OFFLINE_THIS_REQUEST: contextvars.ContextVar[bool] = contextvars.ContextVar("offline_this_request", default=False)

settings = Settings()


def reload_settings() -> Settings:
    """Re-read the environment (used by tests and the eval script)."""
    global settings
    settings = Settings()
    return settings


def get_settings() -> Settings:
    return settings
