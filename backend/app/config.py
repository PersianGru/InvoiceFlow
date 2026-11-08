"""Settings. Secrets come from the environment only (never committed).

Startup refuses unsafe configurations (spec §2 rule 4, TESTING.md §3 downgrade test):
a debug hub override or a plain ``http://`` hub origin in production, a missing installation key,
a missing master key or missing public keys.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.licensing.constants import CORE_TOOL, DEFAULT_KEY_PREFIX, HUB_ORIGIN, PRODUCT_ID
from app.licensing.installid import InstallationKeyError, install_key_parts
from app.licensing.keys import PublicKeyConfigError, keys_from_config
from app.licensing.project import PROJECT_INSTALLATION_KEY, PROJECT_PUBLIC_KEYS

AppEnv = Literal["development", "test", "production"]


class ConfigurationError(RuntimeError):
    """The product refuses to start (the message never contains a secret value)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = "development"
    app_version: str = "0.1.0"
    app_name: str = "InvoiceFlow"
    public_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000"
    cors_origin_regex: str | None = None  # development only, e.g. ^http://(localhost|127\.0\.0\.1)(:\d+)?$
    cookie_secure: bool | None = None

    # Secrets (required)
    secret_key: str = Field(default="", description="Session/CSRF signing key, >= 32 chars")
    master_key: str = Field(default="", description="base64url 32 bytes; wraps per-firm data keys")

    # Data stores
    database_url: str = "postgresql+psycopg://invoiceflow:invoiceflow@localhost:5432/invoiceflow"
    database_url_migrations: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/storage"
    s3_endpoint: str | None = None
    s3_bucket: str = "invoiceflow"
    s3_region: str = "eu-central-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_object_lock: Literal["", "governance", "compliance"] = ""  # WORM retention on the archive bucket

    # Licensing
    # Built into the product (app/licensing/project.py); the environment overrides them for the sandbox
    # and development stacks. An empty environment value keeps the built-in material.
    license_public_keys: str = Field(
        default=PROJECT_PUBLIC_KEYS, description="JWKS JSON or PEM blocks with '# kid:' lines"
    )
    license_installation_key: str = Field(
        default=PROJECT_INSTALLATION_KEY, description="ik1.<g>.<secret>; never logged"
    )
    license_key_prefix: str = DEFAULT_KEY_PREFIX
    license_hub_url_debug: str | None = None
    license_product_id_debug: str | None = None
    license_core_feature_debug: str | None = None  # mock hub only: which feature key means "core"
    license_send_instance_name: bool = False
    license_jwks_fetch: bool = True
    machine_id_file: str = "/host/etc/machine-id"
    store_url: str = "https://ivf.21cel.com"
    support_url: str = "https://21cel.com/support"

    # Pipeline
    clamav_host: str | None = None
    clamav_port: int = 3310
    virus_scan_required: bool | None = None  # default: required in production
    ocr_languages: str = ""  # tesseract language string, e.g. "eng+deu"; empty = installed languages
    ollama_url: str | None = None  # e.g. http://ollama:11434; empty = no local LLM
    ollama_model: str = "qwen2.5:7b"
    anthropic_api_key: str | None = None  # cloud fallback (only with cloud_ai + firm opt-in)
    cloud_model: str = "claude-sonnet-5-5"
    # Engine tiers. Local OCR: tesseract (system package) or the built-in rapidocr (pip, CPU).
    ocr_engine: Literal["auto", "tesseract", "rapidocr", "none"] = "auto"
    ocr_cloud: Literal["none", "mistral"] = "none"  # cloud OCR, same gate as cloud extraction
    mistral_api_key: str | None = None
    mistral_ocr_model: str = "mistral-ocr-latest"
    mistral_model: str = "mistral-small-latest"
    # Local LLM: ollama (OLLAMA_URL), the built-in llama.cpp model, or an OpenAI-compatible local server
    local_llm: Literal["auto", "ollama", "llamacpp", "openai_compat", "none"] = "auto"
    llamacpp_model: str = "qwen2.5-1.5b"  # a key from app.pipeline.models.MODELS or a .gguf path
    llamacpp_auto_download: bool = True
    llamacpp_threads: int | None = None
    llamacpp_context: int = 8192
    models_dir: str = "./data/models"
    local_openai_base_url: str | None = None  # e.g. http://lmstudio:1234/v1
    local_openai_api_key: str | None = None
    local_openai_model: str = ""
    local_openai_vision: bool = False
    # Cloud LLM: anthropic (ANTHROPIC_API_KEY), openai, mistral or any OpenAI-compatible endpoint
    cloud_provider: Literal["auto", "anthropic", "openai", "mistral", "openai_compat", "none"] = "auto"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    cloud_openai_compat_base_url: str | None = None
    cloud_openai_compat_api_key: str | None = None
    cloud_openai_compat_model: str = ""
    cloud_vision: bool = True  # send page images to a vision-capable cloud model when the OCR text is weak
    vies_enabled: bool = True
    ecb_rates_enabled: bool = True
    queue_backend: Literal["arq", "inline"] = "arq"

    # Public API and webhooks
    api_access_token_ttl_seconds: int = 3600
    api_default_rate_limit_per_minute: int = 120
    webhook_timeout_seconds: float = 10.0

    # Channels: all optional; a channel is on when its variables are set
    inbound_email_domain: str = "in.invoiceflow.eu"
    inbound_email_webhook_token: str | None = None  # bearer token the inbound-mail provider sends
    imap_host: str | None = None
    imap_port: int = 993
    imap_ssl: bool = True
    imap_user: str | None = None
    imap_password: str | None = None
    imap_folder: str = "INBOX"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str | None = None
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_api_version: str = "v21.0"

    # Login hardening
    login_lock_threshold: int = 5
    login_lock_minutes: int = 15
    login_ip_rate_per_minute: int = 20

    # Platform
    session_ttl_seconds: int = 12 * 3600
    password_min_length: int = 12
    log_level: str = "INFO"

    @field_validator(
        "llamacpp_threads", "virus_scan_required", "cloud_openai_compat_base_url", "ollama_url", mode="before"
    )
    @classmethod
    def _empty_is_unset(cls, v: object) -> object:
        """A compose file forwards every variable; an empty string means the operator left it unset."""
        return None if isinstance(v, str) and v.strip() == "" else v

    @field_validator("license_public_keys", mode="before")
    @classmethod
    def _public_keys_default(cls, v: object) -> object:
        return PROJECT_PUBLIC_KEYS if isinstance(v, str) and v.strip() == "" else v

    @field_validator("license_installation_key", mode="before")
    @classmethod
    def _installation_key_default(cls, v: object) -> object:
        return PROJECT_INSTALLATION_KEY if isinstance(v, str) and v.strip() == "" else v

    @field_validator("license_key_prefix")
    @classmethod
    def _prefix_upper(cls, v: str) -> str:
        return v.strip().upper()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def hub_origin(self) -> str:
        """The hub origin, also the token issuer. Only the debug override may change it."""
        if self.license_hub_url_debug and not self.is_production:
            return self.license_hub_url_debug.rstrip("/")
        return HUB_ORIGIN

    @property
    def product_id(self) -> str:
        if self.license_product_id_debug and not self.is_production:
            return self.license_product_id_debug
        return PRODUCT_ID

    @property
    def core_feature(self) -> str:
        """The feature/tool key that switches the product on (spec §8.4). Always ``core`` in production."""
        if self.license_core_feature_debug and not self.is_production:
            return self.license_core_feature_debug
        return CORE_TOOL

    @property
    def virus_scan_required_effective(self) -> bool:
        return self.virus_scan_required if self.virus_scan_required is not None else self.is_production

    @property
    def cookie_secure_effective(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.is_production

    def master_key_bytes(self) -> bytes:
        raw = base64.urlsafe_b64decode(self.master_key + "=" * (-len(self.master_key) % 4))
        if len(raw) != 32:
            raise ConfigurationError("MASTER_KEY must decode to 32 bytes")
        return raw


def validate_settings(s: Settings) -> Settings:
    """Everything that must stop the product from starting. Raises ``ConfigurationError``."""
    problems: list[str] = []
    if s.is_production:
        if s.license_hub_url_debug:
            problems.append("LICENSE_HUB_URL_DEBUG is set: the debug hub override is refused in production")
        if s.license_product_id_debug:
            problems.append("LICENSE_PRODUCT_ID_DEBUG is set: refused in production")
        if s.license_core_feature_debug:
            problems.append("LICENSE_CORE_FEATURE_DEBUG is set: refused in production")
    if not s.hub_origin.startswith("https://"):
        if s.is_production or not s.license_hub_url_debug:
            problems.append("the license hub origin must be https://")
    if s.license_hub_url_debug and not s.license_hub_url_debug.startswith(("http://", "https://")):
        problems.append("LICENSE_HUB_URL_DEBUG must be an http(s) origin")
    if s.is_production and s.hub_origin != HUB_ORIGIN:
        problems.append(f"the license hub origin must be {HUB_ORIGIN} in production")
    if not s.license_installation_key:
        problems.append("LICENSE_INSTALLATION_KEY is missing (sealed installation ids are mandatory)")
    else:
        try:
            install_key_parts(s.license_installation_key)
        except InstallationKeyError:
            problems.append("LICENSE_INSTALLATION_KEY has the wrong format (ik1.<generation>.<43 chars>)")
    try:
        keys_from_config(s.license_public_keys)
    except PublicKeyConfigError as e:
        problems.append(f"LICENSE_PUBLIC_KEYS: {e}")
    if len(s.secret_key) < 32:
        problems.append("SECRET_KEY must be at least 32 characters")
    try:
        s.master_key_bytes()
    except (ConfigurationError, ValueError):
        problems.append("MASTER_KEY must be base64url of 32 random bytes")
    if s.is_production and not s.database_url.startswith("postgresql"):
        problems.append("DATABASE_URL must be PostgreSQL")
    if s.is_production and not s.store_url.startswith("https://"):
        problems.append("STORE_URL must be https:// in production")
    if s.virus_scan_required_effective and not s.clamav_host:
        problems.append("CLAMAV_HOST is required when the virus scan is required (production default)")
    if s.ollama_url and not s.ollama_url.startswith(("http://", "https://")):
        problems.append("OLLAMA_URL must be an http(s) origin")
    if s.telegram_bot_token and not s.telegram_webhook_secret:
        problems.append("TELEGRAM_WEBHOOK_SECRET is required with TELEGRAM_BOT_TOKEN")
    if s.whatsapp_access_token and not (s.whatsapp_app_secret and s.whatsapp_verify_token):
        problems.append(
            "WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN are required with WHATSAPP_ACCESS_TOKEN"
        )
    if problems:
        raise ConfigurationError("refusing to start: " + "; ".join(problems))
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return validate_settings(Settings())


def reset_settings_cache() -> None:
    get_settings.cache_clear()
