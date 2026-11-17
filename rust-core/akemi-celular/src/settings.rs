//! Onde está a Akemi e como entrar (guardado no celular, num JSON na pasta do app).

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

pub const DEFAULT_PORT: u16 = 8765;
pub const MIN_TOKEN: usize = 16;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    pub url: String,
    pub token: String,
    pub name: String,
    pub muted: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            url: String::new(),
            token: String::new(),
            name: "celular".into(),
            muted: false,
        }
    }
}

impl Settings {
    pub fn complete(&self) -> bool {
        !self.url.is_empty() && self.token.chars().count() >= MIN_TOKEN
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
            url: s("url").unwrap_or(d.url),
            token: s("token").unwrap_or(d.token),
            name: s("name").unwrap_or(d.name),
            muted: v.get("muted").and_then(Value::as_bool).unwrap_or(false),
        }
    }

    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        fs::create_dir_all(dir)?;
        let v = json!({"url": self.url, "token": self.token, "name": self.name, "muted": self.muted});
        // Escreve num arquivo ao lado e troca: se o app morrer no meio, o antigo fica.
        let tmp = dir.join("akemi-celular.json.tmp");
        fs::write(&tmp, serde_json::to_string_pretty(&v).unwrap_or_default())?;
        fs::rename(tmp, Self::file(dir))
    }

    /// Confere o que foi digitado na tela de conexão. Devolve as configurações
    /// novas ou a mensagem de erro para mostrar.
    pub fn validated(&self, url: &str, token: &str, name: &str) -> Result<Self, &'static str> {
        let url = normalize_url(url);
        let token = token.trim();
        if url.is_empty() {
            return Err("Coloque o endereço da Akemi.");
        }
        if parse_ws_url(&url).is_none() {
            return Err("Endereço inválido. Use algo como ws://192.168.0.10:8765");
        }
        if token.chars().count() < MIN_TOKEN {
            return Err("O token tem pelo menos 16 caracteres (é o AKEMI_TOKEN do .env).");
        }
        let name = name.trim();
        Ok(Self {
            url,
            token: token.to_string(),
            name: if name.is_empty() {
                "celular".into()
            } else {
                name.to_string()
            },
            muted: self.muted,
        })
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

    #[test]
    fn valida_o_formulario() {
        let s = Settings::default();
        assert!(s.validated("", "x".repeat(16).as_str(), "").is_err());
        assert!(s.validated("192.168.0.10", "curto", "").is_err());
        let ok = s.validated("192.168.0.10", "x".repeat(16).as_str(), " ").unwrap();
        assert_eq!(ok.url, "ws://192.168.0.10:8765");
        assert_eq!(ok.name, "celular");
        assert!(ok.complete());
    }

    #[test]
    fn salva_e_carrega() {
        let dir = std::env::temp_dir().join(format!("akemi-celular-teste-{}", std::process::id()));
        let s = Settings {
            url: "ws://a:1".into(),
            token: "t".repeat(20),
            name: "meu".into(),
            muted: true,
        };
        s.save(&dir).unwrap();
        assert_eq!(Settings::load(&dir), s);
        let _ = fs::remove_dir_all(dir);
        assert_eq!(Settings::load(Path::new("/nao/existe")), Settings::default());
    }
}
