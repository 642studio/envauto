"""Configuración central. Lee de .env y expone un singleton `settings`."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Toda la configuración del servicio en un único objeto inmutable."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Servidor
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    api_token: str = "cambiame-por-favor"

    # Rutas
    storage_dir: Path = ROOT / "storage"
    auth_dir: Path = ROOT / "auth"
    storage_state_file: Path = ROOT / "auth" / "storage_state.json"

    # URL pública desde la que se sirven los assets generados.
    public_base_url: str = "http://localhost:8000"

    # Playwright
    headless: bool = True
    browser_channel: str = "chromium"
    nav_timeout_ms: int = 60_000
    generation_timeout_ms: int = 300_000
    browser_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
    browser_locale: str = "en-US"
    browser_timezone_id: str = "America/New_York"
    browser_viewport_width: int = 1440
    browser_viewport_height: int = 900

    # Envato
    envato_login_url: str = "https://account.envato.com/sign_in"
    envato_ai_home: str = "https://www.envato.com/ai/"


settings = Settings()

# Asegurar carpetas requeridas.
settings.storage_dir.mkdir(parents=True, exist_ok=True)
settings.auth_dir.mkdir(parents=True, exist_ok=True)
