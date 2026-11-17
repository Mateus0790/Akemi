//! A pasta da Akemi no disco: o `.env`, o `data/painel.json` e os arquivos de
//! texto que o painel edita (personalidade, gatilhos, MCP).

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::envfile::EnvFile;
use crate::schema::{self, Field, FIELDS};

/// Acha a pasta da Akemi subindo a partir de `start` (tem `akemi/core/config.py`).
pub fn find_root(start: &Path) -> Option<PathBuf> {
    let mut dir = Some(start);
    while let Some(d) = dir {
        if d.join("akemi").join("core").join("config.py").is_file() {
            return Some(d.to_path_buf());
        }
        dir = d.parent();
    }
    None
}

pub struct Project {
    pub root: PathBuf,
    pub env: EnvFile,
    pub env_exists: bool,
    /// Veio do `.env.example` porque ainda não havia `.env`.
    pub from_example: bool,
    pub painel: serde_json::Map<String, serde_json::Value>,
    backed_up: bool,
}

impl Project {
    pub fn load(root: &Path) -> Self {
        let env_path = root.join(".env");
        let (env, env_exists, from_example) = match fs::read_to_string(&env_path) {
            Ok(text) => (EnvFile::parse(&text), true, false),
            Err(_) => match fs::read_to_string(root.join(".env.example")) {
                Ok(text) => (EnvFile::parse(&text), false, true),
                Err(_) => (EnvFile::default(), false, false),
            },
        };
        let mut project = Self {
            root: root.to_path_buf(),
            env,
            env_exists,
            from_example,
            painel: serde_json::Map::new(),
            backed_up: false,
        };
        project.painel = project.read_painel();
        project
    }

    pub fn env_path(&self) -> PathBuf {
        self.root.join(".env")
    }

    fn data_dir(&self) -> PathBuf {
        let raw = self.env.get("DATA_DIR").unwrap_or_else(|| "data".into());
        self.resolve(&raw)
    }

    pub fn painel_path(&self) -> PathBuf {
        self.data_dir().join("painel.json")
    }

    fn read_painel(&self) -> serde_json::Map<String, serde_json::Value> {
        fs::read_to_string(self.painel_path())
            .ok()
            .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_default()
    }

    /// O valor que vale para cada campo: painel.json > .env > padrão.
    pub fn effective(&self) -> HashMap<&'static str, String> {
        let env = self.env.values();
        FIELDS
            .iter()
            .map(|field| {
                let value = self
                    .painel
                    .get(field.key)
                    .map(schema::from_json)
                    .or_else(|| env.get(&field.key.to_ascii_uppercase()).cloned())
                    .unwrap_or_else(|| field.default.to_string());
                (field.key, value)
            })
            .collect()
    }

    pub fn in_env(&self, field: &Field) -> bool {
        self.env.get(field.key).is_some()
    }

    pub fn in_painel(&self, field: &Field) -> bool {
        self.painel.contains_key(field.key)
    }

    /// Grava as mudanças. `None` = tirar a chave (volta ao padrão).
    pub fn save(&mut self, changes: &[(&'static Field, Option<String>)]) -> Result<(), String> {
        let mut painel_changed = false;
        for (field, value) in changes {
            match value {
                None => self.env.remove(field.key),
                Some(v) => {
                    let skip = !self.in_env(field) && schema::same_value(field, v, field.default);
                    if !skip {
                        self.env.set(field.key, v);
                    }
                }
            }
            // O painel web grava por cima do .env: se a chave estiver lá, atualiza também.
            if self.painel.contains_key(field.key) {
                painel_changed = true;
                match value {
                    None => {
                        self.painel.remove(field.key);
                    }
                    Some(v) => {
                        self.painel.insert(field.key.to_string(), schema::json_value(field, v));
                    }
                }
            }
        }

        let env_path = self.env_path();
        if self.env_exists && !self.backed_up {
            fs::copy(&env_path, self.root.join(".env.bak"))
                .map_err(|e| format!("não consegui fazer o backup .env.bak: {e}"))?;
            self.backed_up = true;
        }
        write_atomic(&env_path, &self.env.render())?;
        self.env_exists = true;
        self.from_example = false;

        if painel_changed {
            let text = serde_json::to_string_pretty(&serde_json::Value::Object(self.painel.clone()))
                .map_err(|e| e.to_string())?;
            write_atomic(&self.painel_path(), &text)?;
        }
        Ok(())
    }

    /// Caminho do `.env` > caminho de verdade (relativo à pasta da Akemi).
    pub fn resolve(&self, raw: &str) -> PathBuf {
        let path = PathBuf::from(raw.trim());
        if path.is_absolute() {
            path
        } else {
            self.root.join(path)
        }
    }

    /// Caminho escolhido no seletor > o que vai no `.env` (relativo se der).
    pub fn relativize(&self, path: &Path) -> String {
        let text = match path.strip_prefix(&self.root) {
            Ok(rel) if !rel.as_os_str().is_empty() => rel.to_string_lossy().into_owned(),
            Ok(_) => ".".into(),
            Err(_) => path.to_string_lossy().into_owned(),
        };
        text.replace('\\', "/")
    }

    /// Modelos .onnx do Piper na pasta de vozes.
    pub fn piper_models(&self) -> Vec<String> {
        let mut out = Vec::new();
        let dir = self.data_dir().join("voices");
        let mut stack = vec![dir];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = fs::read_dir(&dir) else { continue };
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|e| e == "onnx") {
                    let name = path.file_name().unwrap_or_default().to_string_lossy().to_lowercase();
                    // Kokoro e o reconhecimento de voz também são .onnx, mas não são do Piper.
                    if !name.contains("kokoro") && !name.contains("wespeaker") {
                        out.push(self.relativize(&path));
                    }
                }
            }
        }
        out.sort();
        out
    }
}

pub fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("não consegui criar {}: {e}", parent.display()))?;
    }
    let tmp = path.with_extension("painel-tmp");
    fs::write(&tmp, text).map_err(|e| format!("não consegui gravar {}: {e}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("não consegui gravar {}: {e}", path.display()))
}

/// `MCP_CONFIG`: vários arquivos separados por ";" ou uma lista JSON.
pub fn parse_paths(raw: &str) -> Vec<String> {
    let raw = raw.trim();
    if raw.starts_with('[') {
        if let Ok(list) = schema::parse_list(raw) {
            return list.into_iter().filter(|s| !s.trim().is_empty()).collect();
        }
    }
    raw.split(';')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

// ================================================================ documentos
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DocKind {
    Persona,
    Triggers,
    Mcp,
}

impl DocKind {
    pub fn title(self) -> &'static str {
        match self {
            DocKind::Persona => "Personalidade",
            DocKind::Triggers => "Gatilhos",
            DocKind::Mcp => "Servidores MCP",
        }
    }

    pub fn icon(self) -> &'static str {
        match self {
            DocKind::Persona => "💗",
            DocKind::Triggers => "⚡",
            DocKind::Mcp => "🔌",
        }
    }

    pub fn intro(self) -> &'static str {
        match self {
            DocKind::Persona => {
                "O jeito dela: identidade, lealdade, como fala. {owner} vira o seu nome. \
                 Ela relê isto toda vez que liga."
            }
            DocKind::Triggers => "Regras para comentários espontâneos: o que ela vê ou ouve e quando vale comentar.",
            DocKind::Mcp => {
                "No mesmo formato do Claude Desktop, Cursor e VS Code: {\"mcpServers\": {\"nome\": {\"command\": ..., \"args\": [...]}}}"
            }
        }
    }
}

pub struct Doc {
    pub kind: DocKind,
    pub path: PathBuf,
    pub text: String,
    pub saved: String,
    pub exists: bool,
}

impl Doc {
    pub fn load(kind: DocKind, path: PathBuf) -> Self {
        let (text, exists) = match fs::read_to_string(&path) {
            Ok(t) => (t, true),
            Err(_) => (
                match kind {
                    DocKind::Mcp => "{\n  \"mcpServers\": {}\n}\n".to_string(),
                    _ => String::new(),
                },
                false,
            ),
        };
        Self {
            kind,
            path,
            saved: text.clone(),
            text,
            exists,
        }
    }

    pub fn dirty(&self) -> bool {
        self.text != self.saved
    }

    pub fn check(&self) -> Result<(), String> {
        match self.kind {
            DocKind::Persona | DocKind::Triggers => {
                let value: serde_yaml::Value =
                    serde_yaml::from_str(&self.text).map_err(|e| format!("YAML com erro: {e}"))?;
                match (self.kind, &value) {
                    (DocKind::Persona, serde_yaml::Value::Mapping(map)) => {
                        if map.get("name").is_none() {
                            return Err("falta o campo name".into());
                        }
                        Ok(())
                    }
                    (DocKind::Persona, _) => Err("a personalidade precisa ser um mapa (nome: valor)".into()),
                    (_, serde_yaml::Value::Sequence(_) | serde_yaml::Value::Null) => Ok(()),
                    _ => Err("os gatilhos precisam ser uma lista (- ...)".into()),
                }
            }
            DocKind::Mcp => {
                let value: serde_json::Value =
                    serde_json::from_str(&self.text).map_err(|e| format!("JSON com erro: {e}"))?;
                let servers = value.get("mcpServers").or_else(|| value.get("servers"));
                match servers {
                    Some(serde_json::Value::Object(_)) => Ok(()),
                    _ => Err("falta a chave \"mcpServers\" com os servidores".into()),
                }
            }
        }
    }

    pub fn save(&mut self) -> Result<(), String> {
        self.check()?;
        write_atomic(&self.path, &self.text)?;
        self.saved = self.text.clone();
        self.exists = true;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("akemi-painel-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("akemi/core")).unwrap();
        fs::write(dir.join("akemi/core/config.py"), "").unwrap();
        dir
    }

    #[test]
    fn painel_json_wins_and_is_kept_in_sync() {
        let root = temp_root("sync");
        fs::write(root.join(".env"), "# meu .env\nOWNER_NAME=Ana\nVIDA_ENABLED=true\n").unwrap();
        fs::create_dir_all(root.join("data")).unwrap();
        fs::write(root.join("data/painel.json"), r#"{"vida_enabled": false}"#).unwrap();

        let mut project = Project::load(&root);
        let values = project.effective();
        assert_eq!(values["owner_name"], "Ana");
        assert_eq!(values["vida_enabled"], "false"); // painel.json manda
        assert_eq!(values["brain_provider"], "nim"); // padrão

        let vida = schema::field("vida_enabled").unwrap();
        let fast = schema::field("fast_model").unwrap();
        let deep_provider = schema::field("deep_provider").unwrap();
        project
            .save(&[
                (vida, Some("true".into())),
                (fast, Some("qwen2.5:7b".into())),
                (deep_provider, None),
            ])
            .unwrap();

        let env = fs::read_to_string(root.join(".env")).unwrap();
        assert!(env.starts_with("# meu .env\nOWNER_NAME=Ana\nVIDA_ENABLED=true\n"));
        assert!(env.contains("FAST_MODEL=qwen2.5:7b"));
        assert!(root.join(".env.bak").exists());
        let painel: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(root.join("data/painel.json")).unwrap()).unwrap();
        assert_eq!(painel["vida_enabled"], serde_json::Value::Bool(true));

        assert_eq!(find_root(&root.join("akemi/core")).as_deref(), Some(root.as_path()));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn mcp_paths() {
        assert_eq!(parse_paths("a.json; b.json"), vec!["a.json", "b.json"]);
        assert_eq!(parse_paths("[\"C:/x.json\"]"), vec!["C:/x.json"]);
        assert!(parse_paths("").is_empty());
    }

    #[test]
    fn real_docs_are_valid() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        for (kind, rel) in [
            (DocKind::Persona, "akemi/brain/persona.yaml"),
            (DocKind::Triggers, "akemi/brain/triggers.yaml"),
        ] {
            let doc = Doc::load(kind, root.join(rel));
            if doc.exists {
                assert_eq!(doc.check(), Ok(()), "{rel}");
            }
        }
    }
}
