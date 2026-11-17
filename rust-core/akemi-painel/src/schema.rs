//! Tudo o que dá para configurar na Akemi, espelhando `akemi/core/config.py`.
//!
//! Cada campo guarda o valor do jeito que ele vai no `.env` (texto; listas em
//! JSON). `default` é o padrão do `config.py`: se o `.env` não diz nada, é isso
//! que vale. Quando alguém adicionar um campo novo no Python, é só pôr aqui.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Section {
    Geral,
    Cerebro,
    Voz,
    Ouvir,
    Visao,
    Pastas,
    Memoria,
    Comportamento,
    Chats,
    Apps,
    Oficina,
    Rede,
    Avatar,
}

impl Section {
    pub const ALL: [Section; 13] = [
        Section::Geral,
        Section::Cerebro,
        Section::Voz,
        Section::Ouvir,
        Section::Visao,
        Section::Pastas,
        Section::Memoria,
        Section::Comportamento,
        Section::Chats,
        Section::Apps,
        Section::Oficina,
        Section::Rede,
        Section::Avatar,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Section::Geral => "Geral",
            Section::Cerebro => "Modelos de IA",
            Section::Voz => "Voz",
            Section::Ouvir => "Ouvir",
            Section::Visao => "Visão",
            Section::Pastas => "Pastas e arquivos",
            Section::Memoria => "Memória",
            Section::Comportamento => "Comportamento",
            Section::Chats => "Secretária nos chats",
            Section::Apps => "Apps e integrações",
            Section::Oficina => "Oficina",
            Section::Rede => "Rede e painel",
            Section::Avatar => "Avatar",
        }
    }

    pub fn icon(self) -> &'static str {
        match self {
            Section::Geral => "🏠",
            Section::Cerebro => "💡",
            Section::Voz => "🔊",
            Section::Ouvir => "🎤",
            Section::Visao => "👁",
            Section::Pastas => "📁",
            Section::Memoria => "💭",
            Section::Comportamento => "✨",
            Section::Chats => "💬",
            Section::Apps => "🔌",
            Section::Oficina => "🔧",
            Section::Rede => "🌐",
            Section::Avatar => "🎨",
        }
    }

    pub fn intro(self) -> &'static str {
        match self {
            Section::Geral => "Quem ela é para você e o básico do dia a dia.",
            Section::Cerebro => {
                "Ela pensa com dois modelos: um rápido (respostas curtas e triagem) e um profundo \
                 (planejar e conversas difíceis). Se o principal cair, ela troca sozinha para o reserva. \
                 Use ↻ para buscar os modelos que o provedor tem."
            }
            Section::Voz => {
                "A voz principal e a reserva. Fish Audio é na nuvem; Kokoro e Piper rodam no seu PC, sem internet."
            }
            Section::Ouvir => "Microfone, áudio do PC e o Whisper, que transforma fala em texto.",
            Section::Visao => "Ela lê a tela (OCR) e, com um modelo de visão, entende jogos e vídeos.",
            Section::Pastas => {
                "Ela só lê arquivos das pastas liberadas aqui. Sem nenhuma pasta, ela não lê arquivo nenhum."
            }
            Section::Memoria => "Como ela guarda, resume e procura as lembranças.",
            Section::Comportamento => "Quando ela fala sozinha, o que pode fazer por conta própria e a autoevolução.",
            Section::Chats => {
                "Quando você está ocupado, ela responde quem te manda mensagem (WhatsApp, Telegram, Discord, Teams)."
            }
            Section::Apps => "Ferramentas de outros apps por MCP, clipes no OBS, Modo Casca e chamadas.",
            Section::Oficina => "Ela muda o próprio código e abre um PR para você revisar.",
            Section::Rede => "Núcleo e corpo (Docker/ZimaOS), painel no navegador e registros.",
            Section::Avatar => "O avatar de desktop (experimental).",
        }
    }
}

/// De onde vem a lista de modelos de um campo de modelo.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModelSrc {
    /// O provedor do cérebro (BRAIN_PROVIDER).
    Brain,
    /// DEEP_PROVIDER, ou o do cérebro.
    Deep,
    /// O primeiro da lista BRAIN_FALLBACK.
    Fallback,
    /// VISION_PROVIDER, ou o do cérebro.
    Vision,
    /// MEMORY_EMBED_PROVIDER.
    Embed,
    Anthropic,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Kind {
    Text,
    /// Chave de API, senha, token: aparece escondido.
    Secret,
    Bool,
    Float {
        min: f64,
        max: f64,
    },
    Int {
        min: i64,
        max: i64,
    },
    /// Só um destes valores.
    Choice(&'static [&'static str]),
    /// Sugestões, mas aceita qualquer texto.
    Combo(&'static [&'static str]),
    /// Lista de textos (JSON no `.env`).
    List,
    /// Lista com só estes itens possíveis.
    Checklist(&'static [&'static str]),
    File,
    Dir,
    DirList,
    Model(ModelSrc),
    FishVoice,
    PiperModel,
}

#[derive(Debug, Clone, Copy)]
pub struct Field {
    pub key: &'static str,
    pub label: &'static str,
    pub help: &'static str,
    pub section: Section,
    pub kind: Kind,
    /// O padrão do `config.py`, do jeito que iria no `.env`.
    pub default: &'static str,
    /// Aceita "vazio" no Python (`X | None`): vazio = tirar a linha do `.env`.
    pub optional: bool,
    /// Pouco usado: fica em "Avançado" dentro da seção.
    pub advanced: bool,
}

const fn f(
    section: Section,
    key: &'static str,
    label: &'static str,
    kind: Kind,
    default: &'static str,
    help: &'static str,
) -> Field {
    Field {
        key,
        label,
        help,
        section,
        kind,
        default,
        optional: false,
        advanced: false,
    }
}

const fn opt(mut field: Field) -> Field {
    field.optional = true;
    field
}

const fn adv(mut field: Field) -> Field {
    field.advanced = true;
    field
}

pub const PROVIDERS: &[&str] = &["nim", "openai", "ollama", "llamacpp", "anthropic", "echo"];
pub const LOCAL_PROVIDERS: &[&str] = &["ollama", "llamacpp", "echo"];
const EMBED_PROVIDERS: &[&str] = &["", "nim", "openai", "ollama", "llamacpp"];
const WHISPER_MODELS: &[&str] = &[
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "large-v3-turbo",
    "distil-large-v3",
];
const ACTIONS: &[&str] = &[
    "criar_tarefa",
    "concluir_tarefa",
    "lembrete",
    "midia",
    "volume",
    "abrir_programa",
    "abrir_site",
    "pesquisar",
];
pub const KOKORO_VOICES: &[&str] = &[
    "pf_dora",
    "pm_alex",
    "pm_santa",
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_sarah",
    "af_sky",
    "af_nova",
    "af_river",
    "af_jessica",
    "af_kore",
    "af_aoede",
    "af_alloy",
    "am_adam",
    "am_michael",
    "am_echo",
    "am_eric",
    "am_liam",
    "am_onyx",
    "am_puck",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
    "ef_dora",
    "em_alex",
    "ff_siwis",
    "if_sara",
    "im_nicola",
    "jf_alpha",
    "jf_gongitsune",
    "jf_nezumi",
    "jf_tebukuro",
    "jm_kumo",
    "hf_alpha",
    "hf_beta",
    "hm_omega",
    "hm_psi",
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
];

use Kind::*;
use Section::*;

pub const FIELDS: &[Field] = &[
    // ------------------------------------------------------------ Geral
    f(
        Geral,
        "owner_name",
        "Seu nome",
        Text,
        "Mateus",
        "Como ela te chama. Também entra no {owner} da personalidade.",
    ),
    f(
        Geral,
        "body_name",
        "Nome deste corpo",
        Text,
        "",
        "Vazio = o nome do computador. Ela percebe quando troca de corpo.",
    ),
    adv(f(
        Geral,
        "persona_file",
        "Arquivo da personalidade",
        File,
        "akemi/brain/persona.yaml",
        "O YAML com o jeito dela (aba Personalidade).",
    )),
    adv(f(
        Geral,
        "data_dir",
        "Pasta de dados",
        Dir,
        "data",
        "Memória, estado, vozes baixadas, painel.json...",
    )),
    f(
        Geral,
        "chip_key",
        "Chave do chip de identidade",
        Secret,
        "",
        "Assina e confere os chips portáteis (akemi-chip).",
    ),
    // ------------------------------------------------------------ Cérebro
    f(
        Cerebro,
        "brain_provider",
        "Provedor principal",
        Choice(PROVIDERS),
        "nim",
        "Quem roda o cérebro dela. echo = só repete (testes).",
    ),
    f(
        Cerebro,
        "fast_model",
        "Modelo rápido",
        Model(ModelSrc::Brain),
        "nvidia/ising-calibration-1.5-31b",
        "Triagem e respostas curtas. Quanto mais rápido, melhor.",
    ),
    f(
        Cerebro,
        "deep_model",
        "Modelo profundo",
        Model(ModelSrc::Deep),
        "openai/gpt-oss-20b",
        "Planejar e conversas complexas.",
    ),
    opt(f(
        Cerebro,
        "deep_provider",
        "Provedor do modelo profundo",
        Choice(PROVIDERS),
        "",
        "Vazio = o mesmo do principal.",
    )),
    f(
        Cerebro,
        "llm_temperature",
        "Criatividade (temperatura)",
        Float { min: 0.0, max: 2.0 },
        "0.8",
        "0 = sempre igual; mais alto = mais solta.",
    ),
    adv(f(
        Cerebro,
        "llm_timeout_s",
        "Tempo máximo por resposta (s)",
        Float { min: 1.0, max: 600.0 },
        "30.0",
        "",
    )),
    f(
        Cerebro,
        "nvidia_api_key",
        "Chave da NVIDIA (NIM)",
        Secret,
        "",
        "build.nvidia.com > Get API Key.",
    ),
    adv(f(
        Cerebro,
        "nim_base_url",
        "Endereço do NIM",
        Text,
        "https://integrate.api.nvidia.com/v1",
        "",
    )),
    f(
        Cerebro,
        "openai_api_key",
        "Chave da OpenAI",
        Secret,
        "",
        "Também serve para qualquer servidor compatível com a OpenAI.",
    ),
    adv(f(
        Cerebro,
        "openai_base_url",
        "Endereço da OpenAI",
        Text,
        "https://api.openai.com/v1",
        "Troque para usar OpenRouter, Groq, LM Studio...",
    )),
    f(
        Cerebro,
        "ollama_base_url",
        "Endereço do Ollama",
        Text,
        "http://localhost:11434/v1",
        "",
    ),
    f(
        Cerebro,
        "llamacpp_base_url",
        "Endereço do llama.cpp",
        Text,
        "http://localhost:8080/v1",
        "",
    ),
    f(Cerebro, "anthropic_api_key", "Chave da Anthropic", Secret, "", ""),
    f(
        Cerebro,
        "anthropic_model",
        "Modelo da Anthropic",
        Model(ModelSrc::Anthropic),
        "claude-haiku-4-5",
        "Usado quando o provedor é anthropic.",
    ),
    f(
        Cerebro,
        "brain_fallback",
        "Cérebros reserva (em ordem)",
        Checklist(PROVIDERS),
        "[]",
        "Se o principal cair (internet, provedor fora do ar), ela troca para o próximo daqui.",
    ),
    f(
        Cerebro,
        "fallback_fast_model",
        "Modelo rápido no reserva",
        Model(ModelSrc::Fallback),
        "qwen2.5:7b",
        "",
    ),
    f(
        Cerebro,
        "fallback_deep_model",
        "Modelo profundo no reserva",
        Model(ModelSrc::Fallback),
        "",
        "Vazio = o mesmo do rápido.",
    ),
    adv(f(
        Cerebro,
        "brain_retry_after_s",
        "Tenta voltar ao principal depois de (s)",
        Float { min: 5.0, max: 86400.0 },
        "120.0",
        "",
    )),
    // ------------------------------------------------------------ Voz
    f(
        Voz,
        "tts_engine",
        "Voz principal",
        Choice(&["console", "fish", "elevenlabs", "kokoro", "piper"]),
        "console",
        "console = só texto no terminal; fish e elevenlabs = nuvem; kokoro e piper = no seu PC.",
    ),
    f(
        Voz,
        "tts_fallback",
        "Voz reserva",
        Choice(&["auto", "console", "piper", "kokoro"]),
        "auto",
        "Se a principal falhar. auto = a voz local que você tiver baixado.",
    ),
    opt(f(
        Voz,
        "tts_output_device",
        "Saída de áudio",
        Text,
        "",
        "Nome ou número do dispositivo. Vazio = o padrão do sistema.",
    )),
    f(
        Voz,
        "fish_api_key",
        "Chave da Fish Audio",
        Secret,
        "",
        "fish.audio > API Keys.",
    ),
    f(
        Voz,
        "fish_voice_id",
        "Voz da Fish Audio",
        FishVoice,
        "",
        "O id no fim de fish.audio/m/<id>. ↻ busca as suas vozes.",
    ),
    f(
        Voz,
        "fish_model",
        "Modelo da Fish Audio",
        Combo(&["s2.1-pro", "s2.1-pro-free", "s1", "speech-1.6", "speech-1.5"]),
        "s2.1-pro",
        "s2.1-pro-free é grátis, para testar.",
    ),
    f(
        Voz,
        "fish_latency",
        "Latência",
        Choice(&["low", "normal", "balanced"]),
        "balanced",
        "low e balanced começam a falar antes.",
    ),
    f(
        Voz,
        "fish_speed",
        "Velocidade (Fish)",
        Float { min: 0.5, max: 2.0 },
        "1.0",
        "Mais que 1 = fala mais rápido.",
    ),
    f(
        Voz,
        "fish_volume_db",
        "Volume (Fish, dB)",
        Float { min: -20.0, max: 20.0 },
        "0.0",
        "Ex.: -3 mais baixo, 3 mais alto.",
    ),
    f(
        Voz,
        "fish_language",
        "Idioma (Fish)",
        Combo(&["", "pt", "en", "ja", "es"]),
        "",
        "Vazio = detecta sozinho.",
    ),
    adv(f(
        Voz,
        "fish_sample_rate",
        "Taxa de amostragem (Fish)",
        Int { min: 8000, max: 48000 },
        "44100",
        "",
    )),
    adv(f(
        Voz,
        "fish_base_url",
        "Endereço da Fish Audio",
        Text,
        "https://api.fish.audio",
        "",
    )),
    adv(f(
        Voz,
        "fish_timeout_s",
        "Tempo máximo (Fish, s)",
        Float { min: 1.0, max: 120.0 },
        "20.0",
        "",
    )),
    f(
        Voz,
        "elevenlabs_api_key",
        "Chave do ElevenLabs",
        Secret,
        "",
        "elevenlabs.io > Settings > API Keys (com a permissão Text to Speech).",
    ),
    f(
        Voz,
        "elevenlabs_voice_id",
        "Voz do ElevenLabs",
        Text,
        "",
        "Obrigatório: no site, abra a voz e use \"Copy voice ID\".",
    ),
    f(
        Voz,
        "elevenlabs_model",
        "Modelo do ElevenLabs",
        Combo(&[
            "eleven_multilingual_v2",
            "eleven_flash_v2_5",
            "eleven_turbo_v2_5",
            "eleven_v3",
        ]),
        "eleven_multilingual_v2",
        "multilingual_v2 = melhor qualidade; flash_v2_5 = começa a falar mais rápido (e mais barato).",
    ),
    opt(f(
        Voz,
        "elevenlabs_stability",
        "Estabilidade (ElevenLabs)",
        Float { min: 0.0, max: 1.0 },
        "",
        "0 a 1. Vazio = o que a voz já tem.",
    )),
    opt(f(
        Voz,
        "elevenlabs_similarity_boost",
        "Semelhança (ElevenLabs)",
        Float { min: 0.0, max: 1.0 },
        "",
        "0 a 1. Vazio = o que a voz já tem.",
    )),
    opt(f(
        Voz,
        "elevenlabs_style",
        "Estilo (ElevenLabs)",
        Float { min: 0.0, max: 1.0 },
        "",
        "0 a 1. Vazio = o que a voz já tem.",
    )),
    f(
        Voz,
        "elevenlabs_speed",
        "Velocidade (ElevenLabs)",
        Float { min: 0.7, max: 1.2 },
        "1.0",
        "0.7 a 1.2. Mais que 1 = fala mais rápido.",
    ),
    f(
        Voz,
        "elevenlabs_language",
        "Idioma (ElevenLabs)",
        Combo(&["", "pt", "en", "ja", "es"]),
        "",
        "Vazio = detecta sozinho. Forçar o idioma só vale nos modelos flash/turbo v2.5 e v3.",
    ),
    adv(f(
        Voz,
        "elevenlabs_sample_rate",
        "Taxa de amostragem (ElevenLabs)",
        Choice(&["8000", "16000", "22050", "24000", "44100", "48000"]),
        "24000",
        "24000 funciona em todos os planos; 44100 só nos pagos mais altos.",
    )),
    adv(f(
        Voz,
        "elevenlabs_base_url",
        "Endereço do ElevenLabs",
        Text,
        "https://api.elevenlabs.io",
        "",
    )),
    adv(f(
        Voz,
        "elevenlabs_timeout_s",
        "Tempo máximo (ElevenLabs, s)",
        Float { min: 1.0, max: 120.0 },
        "20.0",
        "",
    )),
    f(
        Voz,
        "kokoro_voice",
        "Voz do Kokoro",
        Combo(KOKORO_VOICES),
        "pf_dora",
        "pf_dora = feminina pt-BR. p = português, f/m = feminina/masculina.",
    ),
    f(
        Voz,
        "kokoro_speed",
        "Velocidade (Kokoro)",
        Float { min: 0.5, max: 2.0 },
        "1.0",
        "Mais que 1 = fala mais rápido.",
    ),
    adv(f(
        Voz,
        "kokoro_model_path",
        "Modelo do Kokoro",
        File,
        "data/voices/kokoro/kokoro-v1.0.int8.onnx",
        "",
    )),
    adv(f(
        Voz,
        "kokoro_voices_path",
        "Vozes do Kokoro",
        File,
        "data/voices/kokoro/voices-v1.0.bin",
        "",
    )),
    f(
        Voz,
        "piper_model_path",
        "Modelo do Piper",
        PiperModel,
        "data/voices/pt_BR-faber-medium.onnx",
        "Um arquivo .onnx (com o .onnx.json do lado).",
    ),
    opt(adv(f(
        Voz,
        "piper_speaker_id",
        "Locutor (Piper)",
        Int { min: 0, max: 10000 },
        "",
        "Só para modelos com vários locutores.",
    ))),
    f(
        Voz,
        "piper_length_scale",
        "Ritmo (Piper)",
        Float { min: 0.3, max: 3.0 },
        "1.0",
        "Mais que 1 = fala mais devagar.",
    ),
    f(
        Voz,
        "piper_volume",
        "Volume (Piper)",
        Float { min: 0.0, max: 3.0 },
        "1.0",
        "",
    ),
    // ------------------------------------------------------------ Ouvir
    f(
        Ouvir,
        "mic_enabled",
        "Ouvir pelo microfone",
        Bool,
        "false",
        "Fale \"Akemi, ...\" e ela responde.",
    ),
    opt(f(
        Ouvir,
        "mic_device",
        "Microfone",
        Text,
        "",
        "Nome ou número (akemi --listar-mics). Vazio = o padrão.",
    )),
    f(
        Ouvir,
        "mic_mode",
        "Quando ela responde",
        Choice(&["wake_word", "always"]),
        "wake_word",
        "wake_word = só quando ouve o nome dela; always = tudo que você fala é para ela.",
    ),
    f(Ouvir, "wake_words", "Nomes que a chamam", List, "[\"akemi\"]", ""),
    f(
        Ouvir,
        "followup_window_s",
        "Continuar a conversa sem o nome por (s)",
        Float { min: 0.0, max: 300.0 },
        "20.0",
        "",
    ),
    f(
        Ouvir,
        "mic_end_silence_ms",
        "Silêncio que encerra sua fala (ms)",
        Int { min: 200, max: 3000 },
        "700",
        "",
    ),
    adv(f(
        Ouvir,
        "barge_in_rms",
        "Volume para interromper ela",
        Float { min: 0.0, max: 30000.0 },
        "1500.0",
        "Falar por cima dela acima disso a faz parar.",
    )),
    f(
        Ouvir,
        "voice_id_enabled",
        "Reconhecer sua voz",
        Bool,
        "true",
        "Só vale depois de akemi --cadastrar-voz. Outra pessoa conversa, mas não manda.",
    ),
    adv(f(
        Ouvir,
        "voice_model_path",
        "Modelo de reconhecimento de voz",
        File,
        "data/voices/wespeaker_en_voxceleb_resnet34.onnx",
        "",
    )),
    f(
        Ouvir,
        "system_audio_enabled",
        "Ouvir o áudio do PC",
        Bool,
        "false",
        "Transcreve o que toca no PC (vídeos, jogos, calls).",
    ),
    f(
        Ouvir,
        "system_audio_max_load",
        "Fatia do Whisper para o áudio do PC",
        Float { min: 0.01, max: 1.0 },
        "0.25",
        "O resto fica para a sua voz.",
    ),
    f(
        Ouvir,
        "whisper_model",
        "Modelo do Whisper",
        Combo(WHISPER_MODELS),
        "small",
        "Maior = entende melhor, mas pesa mais.",
    ),
    f(
        Ouvir,
        "whisper_device",
        "Onde o Whisper roda",
        Choice(&["auto", "cpu", "cuda"]),
        "auto",
        "cuda = placa NVIDIA (bem mais rápido).",
    ),
    f(
        Ouvir,
        "whisper_threads",
        "Threads do Whisper",
        Int { min: 0, max: 64 },
        "0",
        "0 = padrão (4). Menos = PC mais leve.",
    ),
    f(
        Ouvir,
        "stt_language",
        "Idioma da fala",
        Combo(&["pt", "en", "es", "ja"]),
        "pt",
        "",
    ),
    adv(f(
        Ouvir,
        "vad_aggressiveness",
        "Rigor do detector de voz (0–3)",
        Int { min: 0, max: 3 },
        "2",
        "Mais alto = ignora mais ruído.",
    )),
    // ------------------------------------------------------------ Visão
    f(
        Visao,
        "screen_enabled",
        "Olhar a tela",
        Bool,
        "false",
        "Precisa do Tesseract instalado.",
    ),
    f(
        Visao,
        "screen_monitor",
        "Monitor",
        Int { min: 1, max: 16 },
        "1",
        "1 = principal, 2 = segundo...",
    ),
    f(
        Visao,
        "screen_interval_s",
        "Olha a cada (s)",
        Float { min: 0.5, max: 600.0 },
        "5.0",
        "",
    ),
    adv(f(
        Visao,
        "screen_change_threshold",
        "Quanto da tela precisa mudar",
        Float { min: 0.0, max: 1.0 },
        "0.06",
        "",
    )),
    f(
        Visao,
        "screen_ignore_windows",
        "Janelas que ela nunca lê",
        List,
        "[]",
        "Trechos do título. Vazio = a lista padrão (senha, banco...).",
    ),
    f(
        Visao,
        "vision_model",
        "Modelo de visão",
        Model(ModelSrc::Vision),
        "",
        "Para descrever jogos e vídeos. Vazio = só OCR.",
    ),
    opt(f(
        Visao,
        "vision_provider",
        "Provedor da visão",
        Choice(PROVIDERS),
        "",
        "Vazio = o do cérebro.",
    )),
    f(
        Visao,
        "vision_describe_interval_s",
        "Descreve a cena a cada (s)",
        Float { min: 5.0, max: 3600.0 },
        "45.0",
        "",
    ),
    f(Visao, "ocr_lang", "Idiomas do OCR", Text, "por+eng", ""),
    adv(f(
        Visao,
        "tesseract_cmd",
        "Tesseract",
        File,
        "",
        "Vazio = procura no PATH e na pasta padrão do Windows.",
    )),
    adv(f(
        Visao,
        "tessdata_dir",
        "Idiomas do Tesseract",
        Dir,
        "data/tessdata",
        "",
    )),
    // ------------------------------------------------------------ Pastas
    f(
        Pastas,
        "files_enabled",
        "Analisar arquivos",
        Bool,
        "true",
        "Lê arquivos e pastas liberados e te conta o que achou.",
    ),
    f(
        Pastas,
        "files_allowed_dirs",
        "Pastas que ela pode ler",
        DirList,
        "[]",
        "Ela lê só dentro destas (e das subpastas).",
    ),
    f(
        Pastas,
        "files_allow_cloud",
        "Pode mandar arquivos para IA na nuvem",
        Bool,
        "false",
        "Desligado, ela só analisa arquivos com um cérebro local (Ollama, llama.cpp).",
    ),
    f(
        Pastas,
        "files_max_per_run",
        "Arquivos por pedido",
        Int { min: 1, max: 500 },
        "12",
        "Quantos ela lê de uma vez ao analisar uma pasta.",
    ),
    // ------------------------------------------------------------ Memória
    f(
        Memoria,
        "memory_consolidate_enabled",
        "Organizar a memória",
        Bool,
        "true",
        "Resume as lembranças antigas para lembrar melhor do que importa.",
    ),
    f(
        Memoria,
        "memory_keep_days",
        "Resumir lembranças com mais de (dias)",
        Float { min: 1.0, max: 365.0 },
        "7.0",
        "",
    ),
    adv(f(
        Memoria,
        "memory_consolidate_interval_s",
        "Organiza a cada (s)",
        Float {
            min: 0.0,
            max: 604800.0,
        },
        "21600.0",
        "0 = só com /resumir.",
    )),
    f(
        Memoria,
        "memory_embed_provider",
        "Busca por significado",
        Choice(EMBED_PROVIDERS),
        "",
        "Vazio = só por palavra. ollama (nomic-embed-text) funciona offline.",
    ),
    f(
        Memoria,
        "memory_embed_model",
        "Modelo de embeddings",
        Model(ModelSrc::Embed),
        "",
        "Vazio = o padrão do provedor.",
    ),
    adv(f(
        Memoria,
        "memory_embed_timeout_s",
        "Tempo máximo dos embeddings (s)",
        Float { min: 1.0, max: 120.0 },
        "15.0",
        "",
    )),
    adv(f(
        Memoria,
        "memory_embed_interval_s",
        "Indexa o que é novo a cada (s)",
        Float { min: 5.0, max: 86400.0 },
        "60.0",
        "",
    )),
    f(
        Memoria,
        "vida_enabled",
        "Acompanhar sua vida",
        Bool,
        "true",
        "Anota seus planos e datas importantes e depois pergunta como foi.",
    ),
    f(
        Memoria,
        "vida_absence_hours",
        "Ela sente saudade depois de (horas)",
        Float { min: 1.0, max: 720.0 },
        "20.0",
        "",
    ),
    // ------------------------------------------------------------ Comportamento
    f(
        Comportamento,
        "idle_think_interval_s",
        "Pensa sozinha a cada (s)",
        Float {
            min: 10.0,
            max: 86400.0,
        },
        "180.0",
        "",
    ),
    f(
        Comportamento,
        "min_seconds_between_unprompted_speech",
        "Tempo mínimo entre falas espontâneas (s)",
        Float { min: 10.0, max: 3600.0 },
        "60.0",
        "",
    ),
    f(
        Comportamento,
        "initiative_enabled",
        "Iniciativa",
        Bool,
        "true",
        "Faz sozinha ações seguras e te conta o que fez.",
    ),
    f(
        Comportamento,
        "initiative_actions",
        "Ações que ela pode fazer sozinha",
        Checklist(ACTIONS),
        "[\"criar_tarefa\", \"concluir_tarefa\", \"lembrete\", \"midia\", \"volume\"]",
        "",
    ),
    f(
        Comportamento,
        "initiative_max_per_hour",
        "Ações sozinha por hora (máx.)",
        Int { min: 0, max: 120 },
        "6",
        "",
    ),
    f(
        Comportamento,
        "self_improve_autonomous",
        "Mudar sozinha",
        Bool,
        "true",
        "Desligado, as mudanças de jeito esperam você aprovar (/aprovar).",
    ),
    f(
        Comportamento,
        "self_improve_interval_s",
        "Revisa o próprio jeito a cada (s)",
        Float {
            min: 0.0,
            max: 2592000.0,
        },
        "21600.0",
        "0 = só com /evoluir.",
    ),
    adv(f(
        Comportamento,
        "self_improve_min_conversations",
        "Conversas novas para revisar",
        Int { min: 1, max: 1000 },
        "6",
        "",
    )),
    f(
        Comportamento,
        "chat_batch_window_s",
        "Junta mensagens do chat da live por (s)",
        Float { min: 0.0, max: 120.0 },
        "6.0",
        "",
    ),
    f(
        Comportamento,
        "chat_max_replies_per_minute",
        "Respostas ao chat por minuto",
        Int { min: 0, max: 120 },
        "6",
        "",
    ),
    // ------------------------------------------------------------ Chats
    f(Chats, "secretaria_enabled", "Secretária nos chats", Bool, "false", ""),
    f(
        Chats,
        "secretaria_quando",
        "Quando ela responde",
        Choice(&["ocupado", "sempre"]),
        "ocupado",
        "ocupado = só enquanto você está ocupado (\"tô em reunião\").",
    ),
    f(Chats, "secretaria_grupos", "Responder em grupos", Bool, "false", ""),
    f(
        Chats,
        "secretaria_ignorar",
        "Nunca responder",
        List,
        "[]",
        "Nomes ou números.",
    ),
    f(
        Chats,
        "secretaria_max_por_contato",
        "Respostas por pessoa a cada 10 min",
        Int { min: 1, max: 100 },
        "4",
        "",
    ),
    f(
        Chats,
        "secretaria_token",
        "Senha da ponte (WhatsApp)",
        Secret,
        "",
        "16+ caracteres. Vazio = o AKEMI_TOKEN.",
    ),
    adv(f(
        Chats,
        "secretaria_host",
        "Endereço da ponte",
        Text,
        "127.0.0.1",
        "0.0.0.0 para o celular alcançar pela rede de casa.",
    )),
    adv(f(
        Chats,
        "secretaria_port",
        "Porta da ponte",
        Int { min: 1, max: 65535 },
        "8767",
        "",
    )),
    f(
        Chats,
        "telegram_bot_token",
        "Token do bot do Telegram",
        Secret,
        "",
        "Do @BotFather.",
    ),
    f(Chats, "discord_bot_token", "Token do bot do Discord", Secret, "", ""),
    f(
        Chats,
        "discord_dono_id",
        "Seu ID no Discord",
        Text,
        "",
        "Modo desenvolvedor > clique no seu nome > Copiar ID.",
    ),
    f(
        Chats,
        "discord_presenca",
        "\"Não perturbe\" no Discord = ocupado",
        Bool,
        "false",
        "Precisa do intent PRESENCE no bot.",
    ),
    f(
        Chats,
        "teams_client_id",
        "Teams: client id do app",
        Text,
        "",
        "Registro de app no Entra ID. Login: akemi --teams-login.",
    ),
    f(
        Chats,
        "teams_tenant",
        "Teams: empresa (tenant)",
        Text,
        "organizations",
        "",
    ),
    f(Chats, "teams_presenca", "Reunião no Teams = ocupado", Bool, "true", ""),
    adv(f(
        Chats,
        "teams_intervalo_s",
        "Olha o Teams a cada (s)",
        Float { min: 5.0, max: 3600.0 },
        "15.0",
        "",
    )),
    // ------------------------------------------------------------ Apps
    f(
        Apps,
        "mcp_enabled",
        "Apps por MCP",
        Bool,
        "true",
        "Ferramentas de outros apps (arquivos, navegador, GitHub...). Os servidores ficam no mcp.json, logo abaixo.",
    ),
    f(
        Apps,
        "mcp_config",
        "Arquivo(s) do MCP",
        Text,
        "",
        "Vazio = data/mcp.json. Vários: separe com ;",
    ),
    adv(f(
        Apps,
        "mcp_timeout_s",
        "Tempo máximo por ferramenta (s)",
        Float { min: 1.0, max: 3600.0 },
        "60.0",
        "",
    )),
    adv(f(
        Apps,
        "mcp_connect_timeout_s",
        "Tempo para conectar (s)",
        Float { min: 1.0, max: 3600.0 },
        "90.0",
        "",
    )),
    adv(f(
        Apps,
        "mcp_max_tools",
        "Ferramentas no máximo",
        Int { min: 1, max: 1000 },
        "60",
        "",
    )),
    adv(f(
        Apps,
        "mcp_result_max_chars",
        "Tamanho máximo da resposta de uma ferramenta",
        Int { min: 100, max: 1000000 },
        "4000",
        "",
    )),
    f(
        Apps,
        "obs_enabled",
        "Clipes no OBS",
        Bool,
        "false",
        "\"clipa isso\" salva os últimos segundos.",
    ),
    f(
        Apps,
        "obs_url",
        "Endereço do OBS",
        Text,
        "ws://127.0.0.1:4455",
        "obs-websocket 5 (OBS 28+).",
    ),
    f(
        Apps,
        "obs_password",
        "Senha do OBS",
        Secret,
        "",
        "Ferramentas > Configurações do Servidor WebSocket.",
    ),
    f(
        Apps,
        "obs_replay_autostart",
        "Ligar o replay buffer sozinha",
        Bool,
        "true",
        "",
    ),
    f(
        Apps,
        "casca",
        "Modo Casca",
        Choice(&["auto", "claude", "codex", "ambos", "desligado"]),
        "auto",
        "Ela vira o corpo do Claude Code e do Codex enquanto eles trabalham.",
    ),
    f(
        Apps,
        "casca_voz",
        "O que ela fala no Modo Casca",
        Choice(&["nada", "importante", "tudo"]),
        "importante",
        "",
    ),
    f(
        Apps,
        "call_output_device",
        "Cabo virtual da voz na call",
        Text,
        "",
        "Ex.: CABLE Input.",
    ),
    f(
        Apps,
        "call_announce",
        "Se apresenta ao entrar na call",
        Bool,
        "true",
        "",
    ),
    f(
        Apps,
        "call_friends_can_ask",
        "Amigos na call pedem ações",
        Bool,
        "false",
        "Você confirma cada uma.",
    ),
    f(
        Apps,
        "call_friend_actions",
        "Ações que amigos podem pedir",
        Checklist(ACTIONS),
        "[\"abrir_programa\", \"abrir_site\", \"pesquisar\", \"midia\", \"volume\"]",
        "",
    ),
    // ------------------------------------------------------------ Oficina
    f(Oficina, "oficina_enabled", "Oficina", Bool, "false", ""),
    f(
        Oficina,
        "github_token",
        "Token do GitHub",
        Secret,
        "",
        "Fine-grained, só este repositório: Contents e Pull requests (escrita).",
    ),
    f(Oficina, "github_repo", "Repositório", Text, "Mateus0790/Akemi", ""),
    f(Oficina, "oficina_repo_dir", "Onde o repositório está", Dir, ".", ""),
    f(Oficina, "oficina_base", "Branch base", Text, "main", ""),
    f(
        Oficina,
        "oficina_per_day",
        "PRs por dia (máx.)",
        Int { min: 0, max: 100 },
        "2",
        "",
    ),
    f(
        Oficina,
        "oficina_model",
        "Modelo da Oficina",
        Model(ModelSrc::Deep),
        "",
        "Vazio = o modelo profundo.",
    ),
    adv(f(
        Oficina,
        "oficina_llm_timeout_s",
        "Tempo máximo para escrever código (s)",
        Float { min: 10.0, max: 7200.0 },
        "300.0",
        "",
    )),
    adv(f(
        Oficina,
        "oficina_test_timeout_s",
        "Tempo máximo dos testes (s)",
        Float { min: 10.0, max: 7200.0 },
        "900.0",
        "",
    )),
    // ------------------------------------------------------------ Rede
    f(
        Rede,
        "akemi_token",
        "Token do núcleo (AKEMI_TOKEN)",
        Secret,
        "",
        "Segredo entre núcleo e corpo. 16+ caracteres.",
    ),
    f(
        Rede,
        "server_url",
        "Onde o corpo acha o núcleo",
        Text,
        "",
        "Ex.: ws://192.168.0.10:8765",
    ),
    adv(f(Rede, "server_host", "Núcleo escuta em", Text, "0.0.0.0", "")),
    adv(f(
        Rede,
        "server_port",
        "Porta do núcleo",
        Int { min: 1, max: 65535 },
        "8765",
        "",
    )),
    adv(f(
        Rede,
        "control_api_host",
        "API de controle escuta em",
        Text,
        "127.0.0.1",
        "",
    )),
    adv(f(
        Rede,
        "control_api_port",
        "Porta da API de controle",
        Int { min: 1, max: 65535 },
        "8765",
        "",
    )),
    f(
        Rede,
        "painel_enabled",
        "Painel no navegador",
        Bool,
        "true",
        "O painel web (/painel), que muda algumas coisas na hora.",
    ),
    f(
        Rede,
        "painel_host",
        "Painel web escuta em",
        Text,
        "127.0.0.1",
        "Fora do PC (Docker): 0.0.0.0, e precisa de AKEMI_TOKEN.",
    ),
    f(
        Rede,
        "painel_port",
        "Porta do painel web",
        Int { min: 1, max: 65535 },
        "8766",
        "",
    ),
    f(
        Rede,
        "log_level",
        "Nível dos registros",
        Choice(&["DEBUG", "INFO", "WARNING", "ERROR"]),
        "INFO",
        "",
    ),
    adv(f(Rede, "log_json", "Registros em JSON", Bool, "false", "")),
    // ------------------------------------------------------------ Avatar
    f(
        Avatar,
        "avatar_enabled",
        "Avatar de desktop",
        Bool,
        "false",
        "Experimental.",
    ),
    f(
        Avatar,
        "avatar_scale",
        "Tamanho",
        Float { min: 0.5, max: 3.0 },
        "1.0",
        "",
    ),
    opt(f(
        Avatar,
        "avatar_skin",
        "Aparência (pasta)",
        Dir,
        "",
        "Pasta com parado.png, piscando.png, falando.png... Vazio = a padrão.",
    )),
    f(
        Avatar,
        "avatar_perch",
        "Senta no topo da janela em foco",
        Bool,
        "true",
        "Windows.",
    ),
    f(
        Avatar,
        "avatar_sleep_after_s",
        "Cochila depois de (s) parada",
        Float {
            min: 10.0,
            max: 86400.0,
        },
        "300.0",
        "",
    ),
];

#[cfg_attr(not(test), allow(dead_code))]
pub fn field(key: &str) -> Option<&'static Field> {
    FIELDS.iter().find(|f| f.key == key)
}

// ============================================================ valores
/// O `.env.example` traz chaves de mentira ("nvapi-coloque-sua-chave-aqui").
pub fn is_placeholder(value: &str) -> bool {
    let v = value.to_lowercase();
    v.contains("coloque") || v.contains("sua-chave") || v.contains("your-key") || v.contains("changeme")
}

pub fn secret_missing(value: &str) -> bool {
    value.trim().is_empty() || is_placeholder(value)
}

pub fn parse_bool(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "t" | "yes" | "y" | "on" | "sim" => Some(true),
        "0" | "false" | "f" | "no" | "n" | "off" | "nao" | "não" | "" => Some(false),
        _ => None,
    }
}

/// Lista JSON de textos. O pydantic também aceita um texto só, sem colchetes?
/// Não: listas no `.env` têm que ser JSON. Então isto devolve erro se não for.
pub fn parse_list(value: &str) -> Result<Vec<String>, String> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(Vec::new());
    }
    match serde_json::from_str::<serde_json::Value>(value) {
        Ok(serde_json::Value::Array(items)) => items
            .into_iter()
            .map(|item| match item {
                serde_json::Value::String(s) => Ok(s),
                other => Ok(other.to_string()),
            })
            .collect(),
        _ => Err("precisa ser uma lista JSON, ex.: [\"a\", \"b\"]".into()),
    }
}

pub fn render_list(items: &[String]) -> String {
    let parts: Vec<String> = items
        .iter()
        .map(|s| serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into()))
        .collect();
    format!("[{}]", parts.join(", "))
}

/// Confere um valor. `Ok` = pode ir para o `.env`.
pub fn validate(field: &Field, value: &str) -> Result<(), String> {
    if field.optional && value.trim().is_empty() {
        return Ok(());
    }
    match field.kind {
        Bool => parse_bool(value)
            .map(|_| ())
            .ok_or_else(|| "ligado ou desligado".into()),
        Float { min, max } => {
            let n: f64 = value
                .trim()
                .replace(',', ".")
                .parse()
                .map_err(|_| "precisa ser um número".to_string())?;
            if n < min || n > max {
                return Err(format!("entre {min} e {max}"));
            }
            Ok(())
        }
        Int { min, max } => {
            let n: i64 = value
                .trim()
                .parse()
                .map_err(|_| "precisa ser um número inteiro".to_string())?;
            if n < min || n > max {
                return Err(format!("entre {min} e {max}"));
            }
            Ok(())
        }
        Choice(options) => {
            if options.contains(&value) {
                Ok(())
            } else {
                Err(format!("escolha um destes: {}", options.join(", ")))
            }
        }
        List | DirList => parse_list(value).map(|_| ()),
        Checklist(options) => {
            let items = parse_list(value)?;
            match items.iter().find(|i| !options.contains(&i.as_str())) {
                Some(bad) => Err(format!("\"{bad}\" não é uma opção")),
                None => Ok(()),
            }
        }
        Text | Secret | Combo(_) | File | Dir | Model(_) | FishVoice | PiperModel => {
            if value.contains(['\n', '\r']) {
                Err("uma linha só".into())
            } else {
                Ok(())
            }
        }
    }
}

/// Deixa o valor no formato que o Python espera (ex.: "0,5" > "0.5").
pub fn normalize(field: &Field, value: &str) -> String {
    match field.kind {
        Float { .. } => value.trim().replace(',', "."),
        Int { .. } => value.trim().to_string(),
        Bool => parse_bool(value).unwrap_or(false).to_string(),
        _ => value.to_string(),
    }
}

/// O mesmo valor escrito de outro jeito ("1.0" e "1", "True" e "true") conta como igual.
pub fn same_value(field: &Field, a: &str, b: &str) -> bool {
    match field.kind {
        Bool => parse_bool(a).is_some() && parse_bool(a) == parse_bool(b),
        Float { .. } | Int { .. } => match (a.trim().replace(',', ".").parse::<f64>(), b.trim().parse::<f64>()) {
            (Ok(x), Ok(y)) => x == y,
            _ => a.trim() == b.trim(),
        },
        List | DirList | Checklist(_) => match (parse_list(a), parse_list(b)) {
            (Ok(x), Ok(y)) => x == y,
            _ => a == b,
        },
        _ => a == b,
    }
}

/// O valor como vai no `data/painel.json` (que guarda tipos de verdade).
pub fn json_value(field: &Field, value: &str) -> serde_json::Value {
    match field.kind {
        Bool => serde_json::Value::Bool(parse_bool(value).unwrap_or(false)),
        Float { .. } | Int { .. } => value
            .trim()
            .replace(',', ".")
            .parse::<f64>()
            .ok()
            .and_then(serde_json::Number::from_f64)
            .map(serde_json::Value::Number)
            .unwrap_or_else(|| serde_json::Value::String(value.into())),
        List | DirList | Checklist(_) => serde_json::Value::Array(
            parse_list(value)
                .unwrap_or_default()
                .into_iter()
                .map(serde_json::Value::String)
                .collect(),
        ),
        _ => serde_json::Value::String(value.into()),
    }
}

/// O texto que um valor do `painel.json` teria no `.env`.
pub fn from_json(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Array(items) => render_list(
            &items
                .iter()
                .map(|i| i.as_str().map(str::to_string).unwrap_or_else(|| i.to_string()))
                .collect::<Vec<_>>(),
        ),
        serde_json::Value::Null => String::new(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn keys_are_unique_and_defaults_valid() {
        let mut seen = HashSet::new();
        for field in FIELDS {
            assert!(seen.insert(field.key), "repetido: {}", field.key);
            assert_eq!(field.key, field.key.to_ascii_lowercase());
            assert!(validate(field, field.default).is_ok(), "padrão inválido: {}", field.key);
        }
    }

    #[test]
    fn every_setting_in_config_py_is_here() {
        // Confere contra o config.py de verdade, quando o painel está dentro do repositório.
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../akemi/core/config.py");
        let Ok(text) = std::fs::read_to_string(path) else {
            return;
        };
        let mut missing = Vec::new();
        for line in text.lines() {
            let line = line.trim_start();
            let Some((name, rest)) = line.split_once(':') else {
                continue;
            };
            let indent_ok = text.lines().any(|l| l == format!("    {line}"));
            if !indent_ok
                || !name
                    .chars()
                    .all(|c| c.is_ascii_lowercase() || c == '_' || c.is_ascii_digit())
            {
                continue;
            }
            if name == "model_config" || !rest.contains('=') {
                continue;
            }
            if field(name).is_none() {
                missing.push(name.to_string());
            }
        }
        assert!(missing.is_empty(), "faltam no painel: {missing:?}");
    }

    #[test]
    fn lists_round_trip() {
        let items = vec!["C:\\Users\\voce".to_string(), "akemi \"x\"".to_string()];
        assert_eq!(parse_list(&render_list(&items)).unwrap(), items);
        assert!(parse_list("akemi").is_err());
        assert_eq!(parse_list("").unwrap(), Vec::<String>::new());
    }

    #[test]
    fn validation() {
        let temp = field("llm_temperature").unwrap();
        assert!(validate(temp, "0,5").is_ok());
        assert_eq!(normalize(temp, "0,5"), "0.5");
        assert!(validate(temp, "3").is_err());
        assert!(validate(field("brain_provider").unwrap(), "gemini").is_err());
        assert!(validate(field("deep_provider").unwrap(), "").is_ok());
        assert!(validate(field("brain_fallback").unwrap(), "[\"ollama\"]").is_ok());
        assert!(validate(field("brain_fallback").unwrap(), "[\"skynet\"]").is_err());
        assert!(same_value(field("fish_speed").unwrap(), "1", "1.0"));
        assert!(same_value(field("mic_enabled").unwrap(), "True", "true"));
    }
}
