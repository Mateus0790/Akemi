//! Lê e escreve o `.env` sem estragar o que já está nele.
//!
//! Comentários, linhas em branco e a ordem ficam como estavam. Uma chave que já
//! existe é trocada no mesmo lugar; uma chave nova vai para o fim. Os valores
//! seguem as regras do python-dotenv, que é quem lê o arquivo do lado da Akemi:
//!
//! - `'simples'`: literal, sem escapes
//! - `"duplas"`: aceita `\\`, `\"`, `\n`...
//! - sem aspas: vai até o fim da linha, e ` #` começa um comentário

use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
enum Line {
    /// Comentário, linha em branco ou qualquer coisa que não seja `CHAVE=valor`.
    Other(String),
    /// `CHAVE=valor`. Guarda o texto original para reescrever igual se não mudar.
    Pair { key: String, value: String, raw: String },
}

#[derive(Debug, Clone, Default)]
pub struct EnvFile {
    lines: Vec<Line>,
}

impl EnvFile {
    pub fn parse(text: &str) -> Self {
        let lines = text
            .lines()
            .map(|raw| match parse_pair(raw) {
                Some((key, value)) => Line::Pair {
                    key,
                    value,
                    raw: raw.to_string(),
                },
                None => Line::Other(raw.to_string()),
            })
            .collect();
        Self { lines }
    }

    /// Todas as chaves com valor (em maiúsculas). Se repetir, vale a última,
    /// como no python-dotenv.
    pub fn values(&self) -> HashMap<String, String> {
        let mut out = HashMap::new();
        for line in &self.lines {
            if let Line::Pair { key, value, .. } = line {
                out.insert(key.to_ascii_uppercase(), value.clone());
            }
        }
        out
    }

    pub fn get(&self, key: &str) -> Option<String> {
        self.values().remove(&key.to_ascii_uppercase())
    }

    /// Troca (ou cria) `KEY=valor`. Se a chave aparece mais de uma vez, a
    /// primeira recebe o valor e as outras saem, para não haver dúvida.
    pub fn set(&mut self, key: &str, value: &str) {
        let upper = key.to_ascii_uppercase();
        let new_raw = format!("{upper}={}", encode_value(value));
        let mut done = false;
        self.lines.retain_mut(|line| match line {
            Line::Pair { key: k, value: v, raw } if k.eq_ignore_ascii_case(&upper) => {
                if done {
                    return false;
                }
                done = true;
                if v != value {
                    *k = upper.clone();
                    *v = value.to_string();
                    *raw = new_raw.clone();
                }
                true
            }
            _ => true,
        });
        if !done {
            self.lines.push(Line::Pair {
                key: upper,
                value: value.to_string(),
                raw: new_raw,
            });
        }
    }

    /// Tira a chave do arquivo (ela volta ao padrão da Akemi).
    pub fn remove(&mut self, key: &str) {
        self.lines
            .retain(|line| !matches!(line, Line::Pair { key: k, .. } if k.eq_ignore_ascii_case(key)));
    }

    pub fn render(&self) -> String {
        let mut out = String::new();
        for line in &self.lines {
            match line {
                Line::Other(raw) | Line::Pair { raw, .. } => out.push_str(raw),
            }
            out.push('\n');
        }
        out
    }
}

fn is_key(s: &str) -> bool {
    let mut chars = s.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.')
}

fn parse_pair(raw: &str) -> Option<(String, String)> {
    let line = raw.trim_start();
    if line.starts_with('#') {
        return None;
    }
    let line = line.strip_prefix("export ").map(str::trim_start).unwrap_or(line);
    let (key, rest) = line.split_once('=')?;
    let key = key.trim();
    if !is_key(key) {
        return None;
    }
    Some((key.to_string(), decode_value(rest)))
}

fn decode_value(rest: &str) -> String {
    let rest = rest.trim_start();
    if let Some(body) = rest.strip_prefix('\'') {
        // Literal até a próxima aspa simples que não esteja escapada.
        let mut out = String::new();
        let mut chars = body.chars();
        while let Some(c) = chars.next() {
            match c {
                '\\' => match chars.next() {
                    Some(e @ ('\'' | '\\')) => out.push(e),
                    Some(other) => {
                        out.push('\\');
                        out.push(other);
                    }
                    None => out.push('\\'),
                },
                '\'' => return out,
                _ => out.push(c),
            }
        }
        return out;
    }
    if let Some(body) = rest.strip_prefix('"') {
        let mut out = String::new();
        let mut chars = body.chars();
        while let Some(c) = chars.next() {
            match c {
                '\\' => match chars.next() {
                    Some('n') => out.push('\n'),
                    Some('t') => out.push('\t'),
                    Some('r') => out.push('\r'),
                    Some(e @ ('\\' | '"' | '\'')) => out.push(e),
                    Some(other) => {
                        out.push('\\');
                        out.push(other);
                    }
                    None => out.push('\\'),
                },
                '"' => return out,
                _ => out.push(c),
            }
        }
        return out;
    }
    // Sem aspas: " #" começa comentário.
    let mut end = rest.len();
    let bytes = rest.as_bytes();
    for i in 1..bytes.len() {
        if bytes[i] == b'#' && (bytes[i - 1] == b' ' || bytes[i - 1] == b'\t') {
            end = i;
            break;
        }
    }
    rest[..end].trim_end().to_string()
}

/// Escreve um valor de um jeito que o python-dotenv leia de volta igual.
pub fn encode_value(value: &str) -> String {
    // Sem aspas as barras ficam literais (bom para caminhos do Windows e JSON).
    let plain = value.trim() == value
        && !value.starts_with(['\'', '"'])
        && !value.contains(['#', '\n', '\r'])
        && !value.contains('$');
    if plain {
        return value.to_string();
    }
    // Aspas simples: literal, mas o python-dotenv ainda troca \\ e \'.
    if !value.contains(['\'', '\\', '\n', '\r']) {
        return format!("'{value}'");
    }
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ => out.push(c),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "# Cérebro\nBRAIN_PROVIDER=nim\n\n# FAST_MODEL=x\nWAKE_WORDS=[\"akemi\"]\nexport OWNER_NAME='Mateus Silva'\nFISH_LANGUAGE=pt # idioma\n";

    #[test]
    fn reads_values() {
        let env = EnvFile::parse(SAMPLE);
        let v = env.values();
        assert_eq!(v["BRAIN_PROVIDER"], "nim");
        assert_eq!(v["WAKE_WORDS"], "[\"akemi\"]");
        assert_eq!(v["OWNER_NAME"], "Mateus Silva");
        assert_eq!(v["FISH_LANGUAGE"], "pt");
        assert!(!v.contains_key("FAST_MODEL"));
    }

    #[test]
    fn untouched_file_is_identical() {
        assert_eq!(EnvFile::parse(SAMPLE).render(), SAMPLE);
    }

    #[test]
    fn set_replaces_in_place_and_appends_new() {
        let mut env = EnvFile::parse(SAMPLE);
        env.set("brain_provider", "ollama");
        env.set("DEEP_MODEL", "qwen2.5:14b");
        let text = env.render();
        assert!(text.starts_with("# Cérebro\nBRAIN_PROVIDER=ollama\n\n# FAST_MODEL=x\n"));
        assert!(text.ends_with("DEEP_MODEL=qwen2.5:14b\n"));
    }

    #[test]
    fn remove_and_duplicates() {
        let mut env = EnvFile::parse("A=1\nB=2\nA=3\n");
        assert_eq!(env.get("a").as_deref(), Some("3"));
        env.set("A", "9");
        assert_eq!(env.render(), "A=9\nB=2\n");
        env.remove("b");
        assert_eq!(env.render(), "A=9\n");
    }

    #[test]
    fn round_trip_tricky_values() {
        for value in [
            "simples",
            "com espaço",
            "tem # cerquilha",
            "aspas 'simples'",
            "aspas \"duplas\" e 'simples'",
            "C:\\Users\\voce",
            "[\"C:\\\\Users\\\\voce\\\\Projetos #1\"]",
            "[\"akemi\", \"amiga\"]",
            " espaço na ponta ",
            "",
        ] {
            let mut env = EnvFile::default();
            env.set("X", value);
            let back = EnvFile::parse(&env.render());
            assert_eq!(back.get("X").as_deref(), Some(value), "valor: {value:?}");
        }
    }
}
