"""Settings loaded from environment / .env (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["nim", "openai", "ollama", "llamacpp", "anthropic", "echo"]


class Settings(BaseSettings):
    """Todas as configurações da Akemi.

    Cada campo pode vir de uma variável de ambiente com o mesmo nome em
    maiúsculas (ex.: ``FAST_MODEL``) ou do arquivo ``.env``.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Cérebro: dois modelos (rápido para triagem/respostas curtas,
    # profundo para planejar e conversas complexas) ---
    brain_provider: ProviderName = "nim"
    fast_model: str = "meta/llama-3.1-8b-instruct"
    deep_model: str = "meta/llama-3.3-70b-instruct"
    deep_provider: ProviderName | None = None  # default = brain_provider
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.8

    nvidia_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://localhost:11434/v1"
    llamacpp_base_url: str = "http://localhost:8080/v1"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    # --- Personalidade e estado ---
    persona_file: Path = Path("akemi/brain/persona.yaml")
    data_dir: Path = Path("data")
    owner_name: str = "Mateus"

    # --- Comportamento autônomo ---
    idle_think_interval_s: float = 180.0
    min_seconds_between_unprompted_speech: float = 60.0
    chat_batch_window_s: float = 6.0
    chat_max_replies_per_minute: int = 6

    # --- Live ---
    twitch_channel: str = ""
    twitch_nick: str = ""  # vazio = leitura anônima
    twitch_oauth_token: str = ""

    # --- Rede ---
    hermes_api_url: str = "http://localhost:5000"
    control_api_host: str = "127.0.0.1"
    control_api_port: int = 8765

    log_level: str = "INFO"
    log_json: bool = False

    # --- Percepção ---
    vad_aggressiveness: int = Field(default=2, ge=0, le=3)
    system_audio_enabled: bool = False  # ouvir o áudio do PC (WASAPI loopback) e transcrever
    whisper_model: str = "small"
    stt_language: str = "pt"

    @property
    def effective_deep_provider(self) -> ProviderName:
        return self.deep_provider or self.brain_provider


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
