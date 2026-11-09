"""AI engine settings from the dashboard: the platform section ``ai`` overrides the
environment for the pipeline. The worker rebuilds its pipeline when the section changes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import Settings
from app.settings_store import Section, SettingsStore

AI_FIELDS = (
    "local_llm",
    "ollama_url",
    "ollama_model",
    "local_openai_base_url",
    "local_openai_model",
    "local_openai_api_key",
    "local_openai_vision",
    "local_openai_thinking",
    "cloud_provider",
    "anthropic_api_key",
    "cloud_model",
    "openai_api_key",
    "openai_model",
    "mistral_api_key",
    "cloud_openai_compat_base_url",
    "cloud_openai_compat_api_key",
    "cloud_openai_compat_model",
    "cloud_vision",
)
CHOICES = {
    "local_llm": ("auto", "ollama", "llamacpp", "openai_compat", "none"),
    "cloud_provider": ("auto", "anthropic", "openai", "mistral", "openai_compat", "none"),
}


def merged(settings: Settings, section: Section) -> Settings:
    """``settings`` with every non-empty AI value of the dashboard section applied."""
    update: dict[str, Any] = {}
    for name in AI_FIELDS:
        value = section.get(name)
        if value in (None, ""):
            continue
        if name in CHOICES and value not in CHOICES[name]:
            continue
        update[name] = value
    return settings.model_copy(update=update) if update else settings


def signature(section: Section) -> str:
    """Changes whenever a value or a secret changes (the secret itself is not kept)."""
    blob = json.dumps({"v": section.values, "s": section.secrets, "src": section.source}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


async def effective_settings(settings: Settings, store: SettingsStore) -> tuple[Settings, str]:
    section = await store.effective("ai", None)
    return merged(settings, section), signature(section)
