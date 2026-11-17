//! Onde está a Akemi e como entrar (guardado no celular, num JSON na pasta do app).
//!
//! Dois modos:
//! - **PC/servidor**: o app conecta no núcleo (no PC, no servidor de casa ou na nuvem).
//! - **Sozinha no celular**: o app chama a IA direto, sem núcleo nenhum ligado.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

pub const DEFAULT_PORT: u16 = 8765;
pub const MIN_TOKEN: usize = 16;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mode {
    /// Conecta no núcleo da Akemi (PC ou servidor).
    #[default]
    Core,
    /// A IA direto do celular, sem o núcleo.
    Alone,
}

/// Qual IA o modo "sozinha" usa. As mesmas do núcleo (BRAIN_PROVIDER).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Provider {
    #[default]
    Nim,
    OpenAi,
    Anthropic,
    /// Qualquer API compatível com a da OpenAI (Ollama, llama.cpp, OpenRouter...).
    Other,
}

impl Provider {
    pub const ALL: [Provider; 4] = [Provider::Nim, Provider::OpenAi, Provider::Anthropic, Provider::Other];

    pub fn id(self) -> &'static str {
        match self {
            Provider::Nim => "nim",
            Provider::OpenAi => "openai",
            Provider::Anthropic => "anthropic",
            Provider::Other => "outro",
        }
    }

    fn from_id(id: &str) -> Self {
        Self::ALL.into_iter().find(|p| p.id() == id).unwrap_or_default()
    }

    pub fn index(self) -> i32 {
        Self::ALL.iter().position(|p| *p == self).unwrap_or(0) as i32
    }

    pub fn from_index(i: i32) -> Self {
        Self::ALL.get(i.max(0) as usize).copied().unwrap_or_default()
    }

    /// Os mesmos padrões do akemi/core/config.py.
    pub fn default_model(self) -> &'static str {
        match self {
            Provider::Nim => "nvidia/ising-calibration-1.5-31b",
            Provider::Anthropic => "claude-haiku-4-5",
            Provider::OpenAi | Provider::Other => "",
        }
    }

    pub fn default_url(self) -> &'static str {
        match self {
            Provider::Nim => "https://integrate.api.nvidia.com/v1",
            Provider::OpenAi => "https://api.openai.com/v1",
            Provider::Anthropic => "https://api.anthropic.com/v1",
            Provider::Other => "",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    pub mode: Mode,
    // ---- PC/servidor
    pub url: String,
    pub token: String,
    pub name: String,
    // ---- sozinha no celular
    pub provider: Provider,
    pub api_key: String,
    pub brain_model: String,
    /// Só para "outro": o endereço da API (ex.: http://192.168.0.10:11434/v1).
    pub brain_url: String,
    pub owner: String,
    // ---- os dois
    pub muted: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            mode: Mode::Core,
            url: String::new(),
            token: String::new(),
            name: "celular".into(),
            provider: Provider::Nim,
            api_key: String::new(),
            brain_model: String::new(),
            brain_url: String::new(),
            owner: String::new(),
            muted: false,
        }
    }
}

/// O que foi digitado na tela de configuração.
#[derive(Clone, Debug, Default)]
pub struct Form {
    pub mode: Mode,
    pub url: String,
    pub token: String,
    pub name: String,
    pub provider: Provider,
    pub api_key: String,
    pub brain_model: String,
    pub brain_url: String,
    pub owner: String,
}

impl Settings {
    pub fn complete(&self) -> bool {
        match self.mode {
            Mode::Core => !self.url.is_empty() && self.token.chars().count() >= MIN_TOKEN,
            Mode::Alone => match self.provider {
                Provider::Other => !self.brain_url.is_empty() && !self.brain_model.is_empty(),
                Provider::OpenAi => !self.api_key.is_empty() && !self.brain_model.is_empty(),
                _ => !self.api_key.is_empty(),
            },
        }
    }

    pub fn brain_model(&self) -> String {
        let m = self.brain_model.trim();
        if m.is_empty() {
            self.provider.default_model().into()
        } else {
            m.into()
        }
    }

    pub fn brain_base_url(&self) -> String {
        match self.provider {
            Provider::Other => self.brain_url.trim().trim_end_matches('/').to_string(),
            p => p.default_url().into(),
        }
    }

    pub fn owner_name(&self) -> &str {
        let o = self.owner.trim();
        if o.is_empty() {
            "você"
        } else {
            o
        }
    }

    fn file(dir: &Path) -> PathBuf {
        dir.join("akemi-celular.json")
    }

    pub fn load(dir: &Path) -> Self {
        let Ok(raw) = fs::read_to_string(Self::file(dir)) else {
            return Self::default();
        };
        let v: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
        let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_string);
        let d = Self::default();
        Self {
            mode: if s("mode").as_deref() == Some("sozinha") {
                Mode::Alone
            } else {
                Mode::Core
            },
            url: s("url").unwrap_or(d.url),
            token: s("token").unwrap_or(d.token),
            name: s("name").unwrap_or(d.name),
            provider: Provider::from_id(&s("provider").unwrap_or_default()),
            api_key: s("api_key").unwrap_or_default(),
            brain_model: s("brain_model").unwrap_or_default(),
            brain_url: s("brain_url").unwrap_or_default(),
            owner: s("owner").unwrap_or_default(),
            muted: v.get("muted").and_then(Value::as_bool).unwrap_or(false),
        }
    }

    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        fs::create_dir_all(dir)?;
        let v = json!({
            "mode": if self.mode == Mode::Alone { "sozinha" } else { "pc" },
            "url": self.url, "token": self.token, "name": self.name,
            "provider": self.provider.id(), "api_key": self.api_key,
            "brain_model": self.brain_model, "brain_url": self.brain_url, "owner": self.owner,
            "muted": self.muted,
        });
        // Escreve num arquivo ao lado e troca: se o app morrer no meio, o antigo fica.
        let tmp = dir.join("akemi-celular.json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(&v).unwrap_or_default())?;
        fs::rename(tmp, Self::file(dir))
    }

    /// Confere o que foi digitado na tela de configuração. Devolve as
    /// configurações novas ou a mensagem de erro para mostrar.
    pub fn validated(&self, f: &Form) -> Result<Self, &'static str> {
        let mut new = self.clone();
        new.mode = f.mode;
        let name = f.name.trim();
        new.name = if name.is_empty() {
            "celular".into()
        } else {
            name.to_string()
        };
        new.owner = f.owner.trim().to_string();
        match f.mode {
            Mode::Core => {
                let url = normalize_url(&f.url);
                let token = f.token.trim();
                if url.is_empty() {
                    return Err("Coloque o endereço da Akemi.");
                }
                if parse_ws_url(&url).is_none() {
                    return Err("Endereço inválido. Use algo como ws://192.168.0.10:8765");
                }
                if token.chars().count() < MIN_TOKEN {
                    return Err("O token tem pelo menos 16 caracteres (é o AKEMI_TOKEN do .env).");
                }
                new.url = url;
                new.token = token.to_string();
            }
            Mode::Alone => {
                new.provider = f.provider;
                new.api_key = f.api_key.trim().to_string();
                new.brain_model = f.brain_model.trim().to_string();
                new.brain_url = f.brain_url.trim().trim_end_matches('/').to_string();
                if f.provider == Provider::Other {
                    if !(new.brain_url.starts_with("http://") || new.brain_url.starts_with("https://")) {
                        return Err("Coloque o endereço da API, como http://192.168.0.10:11434/v1");
                    }
                } else if new.api_key.is_empty() {
                    return Err("Coloque a chave da API da IA.");
                }
                if new.brain_model().is_empty() {
                    return Err("Coloque o nome do modelo.");
                }
            }
        }
        Ok(new)
    }
}

/// Aceita "192.168.0.10", "192.168.0.10:8765" ou o endereço completo.
pub fn normalize_url(input: &str) -> String {
    let mut s = input.trim().to_string();
    if s.is_empty() {
        return s;
    }
    if !s.contains("://") {
        s = format!("ws://{s}");
    }
    while s.ends_with('/') {
        s.pop();
    }
    if let Some(rest) = s.strip_prefix("ws://") {
        let (hostport, path) = rest.split_once('/').map_or((rest, ""), |(h, p)| (h, p));
        if !has_port(hostport) {
            let path = if path.is_empty() {
                String::new()
            } else {
                format!("/{path}")
            };
            s = format!("ws://{hostport}:{DEFAULT_PORT}{path}");
        }
    }
    s
}

fn has_port(hostport: &str) -> bool {
    if let Some(end) = hostport.rfind(']') {
        // IPv6: [::1]:8765
        return hostport[end..].contains(':');
    }
    hostport.contains(':')
}

/// `ws://host:porta/caminho` em partes. Só `ws://`: é a rede de casa (ou a
/// Tailscale), igual ao `akemi-corpo`.
pub fn parse_ws_url(url: &str) -> Option<(String, u16)> {
    let rest = url.strip_prefix("ws://")?;
    let hostport = rest.split('/').next()?;
    let (host, port) = if let Some(end) = hostport.rfind(']') {
        let host = hostport.get(1..end)?;
        let port = hostport[end + 1..]
            .strip_prefix(':')
            .map_or(Some(80), |p| p.parse().ok())?;
        (host, port)
    } else {
        match hostport.rsplit_once(':') {
            Some((h, p)) => (h, p.parse().ok()?),
            None => (hostport, 80),
        }
    };
    if host.is_empty() || host.contains(char::is_whitespace) {
        return None;
    }
    Some((host.to_string(), port))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endereco_aceita_so_o_ip() {
        assert_eq!(normalize_url("192.168.0.10"), "ws://192.168.0.10:8765");
        assert_eq!(normalize_url(" 192.168.0.10:9000 "), "ws://192.168.0.10:9000");
        assert_eq!(normalize_url("ws://192.168.0.10:9000/"), "ws://192.168.0.10:9000");
        assert_eq!(normalize_url("ws://pc.local"), "ws://pc.local:8765");
        assert_eq!(normalize_url("[fd7a::1]"), "ws://[fd7a::1]:8765");
        assert_eq!(normalize_url(""), "");
    }

    #[test]
    fn partes_do_endereco() {
        assert_eq!(
            parse_ws_url("ws://192.168.0.10:8765"),
            Some(("192.168.0.10".into(), 8765))
        );
        assert_eq!(parse_ws_url("ws://[fd7a::1]:9000"), Some(("fd7a::1".into(), 9000)));
        assert_eq!(parse_ws_url("wss://x:1"), None);
        assert_eq!(parse_ws_url("ws://x:porta"), None);
    }

    fn core_form(url: &str, token: &str) -> Form {
        Form {
            url: url.into(),
            token: token.into(),
            ..Form::default()
        }
    }

    #[test]
    fn valida_o_formulario() {
        let s = Settings::default();
        assert!(s.validated(&core_form("", &"x".repeat(16))).is_err());
        assert!(s.validated(&core_form("192.168.0.10", "curto")).is_err());
        let ok = s.validated(&core_form("192.168.0.10", &"x".repeat(16))).unwrap();
        assert_eq!(ok.url, "ws://192.168.0.10:8765");
        assert_eq!(ok.name, "celular");
        assert!(ok.complete());
    }

    #[test]
    fn valida_o_modo_sozinha() {
        let s = Settings::default();
        let mut f = Form {
            mode: Mode::Alone,
            provider: Provider::Nim,
            ..Form::default()
        };
        assert_eq!(s.validated(&f).unwrap_err(), "Coloque a chave da API da IA.");
        f.api_key = " nvapi-x ".into();
        let ok = s.validated(&f).unwrap();
        assert_eq!(ok.api_key, "nvapi-x");
        assert_eq!(ok.brain_model(), "nvidia/ising-calibration-1.5-31b");
        assert!(ok.complete());

        f.provider = Provider::OpenAi;
        assert_eq!(s.validated(&f).unwrap_err(), "Coloque o nome do modelo.");

        let ollama = Form {
            mode: Mode::Alone,
            provider: Provider::Other,
            brain_url: "http://192.168.0.10:11434/v1/".into(),
            brain_model: "qwen2.5:7b".into(),
            ..Form::default()
        };
        let ok = s.validated(&ollama).unwrap();
        assert_eq!(ok.brain_base_url(), "http://192.168.0.10:11434/v1");
        assert!(ok.complete());
        assert!(s
            .validated(&Form {
                brain_url: "192.168.0.10".into(),
                ..ollama
            })
            .is_err());
    }

    #[test]
    fn salva_e_carrega() {
        let dir = std::env::temp_dir().join(format!("akemi-celular-teste-{}", std::process::id()));
        let s = Settings {
            mode: Mode::Alone,
            url: "ws://a:1".into(),
            token: "t".repeat(20),
            name: "meu".into(),
            provider: Provider::Anthropic,
            api_key: "sk".into(),
            brain_model: "m".into(),
            brain_url: String::new(),
            owner: "Mateus".into(),
            muted: true,
        };
        s.save(&dir).unwrap();
        assert_eq!(Settings::load(&dir), s);
        let _ = fs::remove_dir_all(dir);
        assert_eq!(Settings::load(Path::new("/nao/existe")), Settings::default());
    }
}
