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
    fast_model: str = "nvidia/ising-calibration-1.5-31b"
    deep_model: str = "openai/gpt-oss-20b"
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

    # --- Cérebro reserva: se o principal cair (internet, NIM fora do ar), ela
    # troca sozinha para o próximo da lista e volta quando ele se recupera ---
    brain_fallback: list[ProviderName] = []  # ex. ["ollama"] ou ["ollama", "llamacpp"]
    fallback_fast_model: str = "qwen2.5:7b"  # nome do modelo no cérebro reserva
    fallback_deep_model: str = ""  # vazio = o mesmo do rápido
    brain_retry_after_s: float = 120.0  # depois disso tenta voltar ao principal

    # --- Personalidade e estado ---
    persona_file: Path = Path("akemi/brain/persona.yaml")
    data_dir: Path = Path("data")
    owner_name: str = "Mateus"

    # --- Identidade portátil, corpos e autoevolução ---
    body_name: str = ""  # nome deste corpo (vazio = nome do computador)
    chip_key: str = ""  # chave para assinar/verificar chips (akemi-chip)
    self_improve_interval_s: float = 21600.0  # revisão automática (0 = só com /evoluir)
    self_improve_autonomous: bool = True  # muda sozinha (False = espera /aprovar)
    self_improve_min_conversations: int = 6  # conversas novas para valer a revisão

    # --- Comportamento autônomo ---
    idle_think_interval_s: float = 180.0
    min_seconds_between_unprompted_speech: float = 60.0
    # Iniciativa (estilo J.A.R.V.I.S.): nos momentos de pensamento ela pode
    # fazer sozinha uma ação da lista abaixo e te contar o que fez.
    initiative_enabled: bool = True
    initiative_actions: list[str] = [
        "criar_tarefa", "concluir_tarefa", "lembrete", "midia", "volume",
    ]
    initiative_max_per_hour: int = 6
    chat_batch_window_s: float = 6.0
    chat_max_replies_per_minute: int = 6

    # --- Voz ---
    tts_engine: Literal["console", "piper", "kokoro", "fish"] = "console"
    # Voz reserva: se a principal falhar (Fish Audio sem internet ou sem
    # créditos), a frase sai nesta. auto = com Fish, a voz local que você
    # tiver baixado (Kokoro, depois Piper); senão, o terminal.
    tts_fallback: Literal["auto", "console", "piper", "kokoro"] = "auto"
    # Fish Audio: voz principal na nuvem. Ver akemi/tts/fish_engine.py
    fish_api_key: str = ""
    fish_voice_id: str = ""  # id da voz (final de fish.audio/m/<id>); vazio = voz padrão
    fish_model: str = "s2.1-pro"  # s2.1-pro-free = grátis, para testar
    fish_latency: Literal["low", "normal", "balanced"] = "balanced"  # balanced/low começam a falar antes
    fish_speed: float = 1.0  # 0.5 a 2.0, >1 fala mais rápido
    fish_volume_db: float = 0.0  # ganho em dB (ex. -3 mais baixo, 3 mais alto)
    fish_language: str = ""  # vazio = detecta sozinho; ex. "pt"
    fish_sample_rate: int = 44100
    fish_base_url: str = "https://api.fish.audio"
    fish_timeout_s: float = 20.0
    # Kokoro: voz feminina pt-BR (pf_dora). Ver akemi/tts/kokoro_engine.py
    kokoro_model_path: Path = Path("data/voices/kokoro/kokoro-v1.0.int8.onnx")
    kokoro_voices_path: Path = Path("data/voices/kokoro/voices-v1.0.bin")
    kokoro_voice: str = "pf_dora"
    kokoro_speed: float = 1.0  # >1 fala mais rápido
    piper_model_path: Path = Path("data/voices/pt_BR-faber-medium.onnx")
    piper_speaker_id: int | None = None
    piper_length_scale: float = 1.0  # >1 fala mais devagar
    piper_volume: float = 1.0
    tts_output_device: str | None = None  # nome/índice da saída de áudio (vazio = padrão)

    # --- Núcleo/corpo (akemi-servidor no ZimaOS, akemi-corpo no PC) ---
    akemi_token: str = ""  # segredo compartilhado entre núcleo e corpo
    server_host: str = "0.0.0.0"
    server_port: int = 8765
    server_url: str = ""  # onde o corpo acha o núcleo, ex. ws://192.168.0.10:8765

    # --- Oficina: ela mexe no próprio código e abre PR (docs/OFICINA.md) ---
    oficina_enabled: bool = False
    github_token: str = ""  # fine-grained, só este repositório: Contents e Pull requests (escrita)
    github_repo: str = "Mateus0790/Akemi"
    oficina_repo_dir: Path = Path(".")  # onde o repositório está clonado
    oficina_base: str = "main"
    oficina_per_day: int = 2
    oficina_model: str = ""  # vazio = DEEP_MODEL
    oficina_llm_timeout_s: float = 300.0  # escrever código demora mais que conversar
    oficina_test_timeout_s: float = 900.0

    # --- Habilidades: ela percebe o programa aberto e cria um jeito de participar (docs/HABILIDADES.md) ---
    habilidades_enabled: bool = True
    habilidades_perguntar: bool = True  # pergunta sozinha quando abre um jogo/programa conhecido
    habilidades_por_dia: int = 2  # criações em 24 horas
    habilidades_intervalo_s: float = 20.0  # de quanto em quanto tempo olha os programas abertos
    habilidades_bloqueados: list[str] = []  # programas extras onde ela nunca entra, ex. ["chrome"]
    habilidades_model: str = ""  # vazio = OFICINA_MODEL, senão DEEP_MODEL

    # --- Rede ---
    control_api_host: str = "127.0.0.1"
    control_api_port: int = 8765

    log_level: str = "INFO"
    log_json: bool = False

    # --- Percepção ---
    vad_aggressiveness: int = Field(default=2, ge=0, le=3)
    system_audio_enabled: bool = False  # ouvir o áudio do PC (WASAPI loopback) e transcrever
    mic_enabled: bool = False  # ouvir você pelo microfone
    mic_device: str | None = None  # nome/índice; vazio = padrão do sistema
    # wake_word: só responde se ouvir o nome dela (ou logo depois de uma conversa)
    # always: tudo que você falar é para ela
    mic_mode: Literal["wake_word", "always"] = "wake_word"
    wake_words: list[str] = ["akemi"]
    followup_window_s: float = 20.0  # tempo para continuar a conversa sem dizer o nome
    barge_in_rms: float = 1500.0  # volume mínimo para interromper enquanto ela fala
    # Reconhecer o dono pela voz (docs/VOZ.md). Só vale depois de akemi --cadastrar-voz.
    voice_id_enabled: bool = True
    voice_model_path: Path = Path("data/voices/wespeaker_en_voxceleb_resnet34.onnx")
    whisper_model: str = "small"
    stt_language: str = "pt"

    # --- Visão ---
    screen_enabled: bool = False  # olhar a tela (pip install -e ".[vision]" + Tesseract)
    screen_monitor: int = 1  # 1 = principal, 2 = segundo monitor...
    screen_interval_s: float = 5.0
    screen_change_threshold: float = 0.06  # fração da tela que precisa mudar
    ocr_lang: str = "por+eng"
    tesseract_cmd: str = ""  # vazio = procura no PATH / pasta padrão do Windows
    tessdata_dir: Path = Path("data/tessdata")  # idiomas do OCR (se a pasta existir)
    # Trechos de título de janela que ela nunca lê (vazio = lista padrão)
    screen_ignore_windows: list[str] = []
    # Modelo de visão para descrever a cena (jogos, vídeos). Vazio = só OCR.
    vision_model: str = ""
    vision_provider: ProviderName | None = None  # default = brain_provider
    vision_describe_interval_s: float = 45.0

    # --- Modo Casca: ela vira o corpo do Claude Code e do Codex (docs/CAPRI.md) ---
    # auto = o que você ligou com "akemi --casca claude|codex|ambos"
    casca: Literal["auto", "claude", "codex", "ambos", "desligado"] = "auto"
    # nada = não fala (veja com /casca); importante = fala quando pedem seu OK, terminam ou dão erro
    casca_voz: Literal["nada", "importante", "tudo"] = "importante"

    # --- Clipes pelo OBS: "clipa isso" salva o replay buffer (docs/CAPRI.md) ---
    obs_enabled: bool = False
    obs_url: str = "ws://127.0.0.1:4455"  # obs-websocket 5 (OBS 28+)
    obs_password: str = ""  # Ferramentas > Configurações do Servidor WebSocket
    obs_replay_autostart: bool = True  # liga o replay buffer se estiver desligado

    # --- Modo chamada: ela na sua call do Discord (docs/CAPRI.md) ---
    call_output_device: str = ""  # cabo virtual da voz dela na call, ex. "CABLE Input"
    call_announce: bool = True  # ao entrar, se apresenta e avisa que está ouvindo
    call_friends_can_ask: bool = False  # amigos pedem ações e você confirma
    call_friend_actions: list[str] = ["abrir_programa", "abrir_site", "pesquisar", "midia", "volume"]

    # --- Avatar de desktop (docs/CAPRI.md): desativado por enquanto ---
    avatar_enabled: bool = False  # experimental: só liga com AVATAR_ENABLED=true
    avatar_scale: float = 1.0  # 0.5 a 3
    avatar_skin: Path | None = None  # pasta com parado.png, piscando.png, falando.png...
    avatar_perch: bool = True  # senta no topo da janela em foco (Windows)
    avatar_sleep_after_s: float = 300.0  # sem nada acontecendo, ela cochila

    # --- Análise de arquivos (docs/ARQUIVOS.md) ---
    files_enabled: bool = True
    files_allowed_dirs: list[Path] = []  # sem nenhuma pasta liberada, a ação não existe
    files_allow_cloud: bool = False  # true = aceita mandar o conteúdo para um modelo na nuvem
    files_max_per_run: int = 12  # arquivos lidos por pedido, ao analisar uma pasta

    @property
    def effective_deep_provider(self) -> ProviderName:
        return self.deep_provider or self.brain_provider


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
