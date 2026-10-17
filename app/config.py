import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str = _get("NEBIUS_API_KEY")
    nebius_base_url: str = _get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    model_fast: str = _get("MODEL_FAST", "nvidia/Nemotron-3-Nano-30B-A3B")
    model_main: str = _get("MODEL_MAIN", "nvidia/nemotron-3-super-120b-a12b")
    model_deep: str = _get("MODEL_DEEP", "nvidia/Nemotron-3-Ultra-550b-a55b")
    tavily_api_key: str = _get("TAVILY_API_KEY")
    paypal_client_id: str = _get("PAYPAL_CLIENT_ID")
    paypal_client_secret: str = _get("PAYPAL_CLIENT_SECRET")
    paypal_env: str = _get("PAYPAL_ENV", "sandbox")
    paypal_invoicer_email: str = _get("PAYPAL_INVOICER_EMAIL")
    currency: str = _get("APP_CURRENCY", "USD")
    default_rate: float = float(_get("DEFAULT_HOURLY_RATE", "45") or 45)
    force_mock: bool = _get("SCOPE2CASH_FORCE_MOCK", "") == "1"

    @property
    def llm_live(self) -> bool:
        return bool(self.nebius_api_key) and not self.force_mock

    @property
    def search_live(self) -> bool:
        return bool(self.tavily_api_key) and not self.force_mock

    @property
    def payments_live(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret) and not self.force_mock


settings = Settings()
