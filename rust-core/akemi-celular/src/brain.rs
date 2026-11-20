//! O cérebro do modo "sozinha no celular": a Akemi chama a IA direto, sem o PC.
//!
//! A personalidade é a mesma do núcleo: o `akemi/brain/persona.yaml` entra no
//! app na compilação e o prompt é montado igual ao `Persona.system_prompt` do
//! Python. O que ela não tem aqui: a memória do núcleo, as ações no PC, a
//! secretária e as iniciativas (isso continua no PC ou no servidor).

use std::time::Duration;

use serde_json::{json, Value};

use crate::core::ChatItem;
use crate::settings::{Provider, Settings};

const PERSONA_YAML: &str = include_str!("../../../akemi/brain/persona.yaml");
/// Quantas mensagens anteriores vão junto em cada pergunta.
const CONTEXT_ITEMS: usize = 24;
/// Começo dos avisos de erro que o app põe na conversa (ver `Core::local_error`).
pub const ERROR_PREFIX: &str = "[Não consegui pensar";
const TIMEOUT: Duration = Duration::from_secs(60);

const ONLY_PHONE: &str = "Agora você está só no celular de {owner}, sem o computador e sem o servidor: \
não dá para fazer ações no PC, ver a tela, mexer em arquivos nem lembrar do que vocês conversaram por lá. \
Se ele pedir algo assim, diga de um jeito leve que isso dá quando o PC ou o servidor estiver ligado. \
Fora isso, converse normalmente.";

/// O prompt de sistema, igual ao do núcleo (sem a parte de rodar em segundo plano).
pub fn system_prompt(owner: &str) -> String {
    let raw: serde_yaml::Value = serde_yaml::from_str(PERSONA_YAML).unwrap_or(serde_yaml::Value::Null);
    let fmt = |s: &str| s.replace("{owner}", owner).trim().to_string();
    let text = |k: &str| raw.get(k).and_then(|v| v.as_str()).map(fmt).unwrap_or_default();
    let list = |k: &str| -> Vec<String> {
        raw.get(k)
            .and_then(|v| v.as_sequence())
            .map(|s| s.iter().filter_map(|v| v.as_str()).map(fmt).collect())
            .unwrap_or_default()
    };
    let name = raw.get("name").and_then(|v| v.as_str()).unwrap_or("Akemi").to_string();

    let mut parts = vec![text("identity"), text("loyalty"), text("inspiration")];
    let traits = list("traits");
    if !traits.is_empty() {
        parts.push(format!("Personalidade:\n{}", bullets(&traits)));
    }
    let rules = list("speech_rules");
    if !rules.is_empty() {
        parts.push(format!("Como falar:\n{}", bullets(&rules)));
    }
    if let Some(examples) = raw.get("examples").and_then(|v| v.as_sequence()) {
        let shots: Vec<String> = examples
            .iter()
            .filter_map(|e| {
                let dono = e.get("dono")?.as_str()?;
                let akemi = e.get("akemi")?.as_str()?;
                Some(format!("{owner}: {}\n{name}: {}", fmt(dono), fmt(akemi)))
            })
            .collect();
        if !shots.is_empty() {
            parts.push(format!(
                "Exemplos do seu jeito de responder (não copie, só o tom):\n{}",
                shots.join("\n\n")
            ));
        }
    }
    parts.push(text("will"));
    parts.push(fmt(ONLY_PHONE));
    parts.retain(|p| !p.is_empty());
    parts.join("\n\n")
}

fn bullets(items: &[String]) -> String {
    items.iter().map(|t| format!("- {t}")).collect::<Vec<_>>().join("\n")
}

/// Um pedido HTTP pronto para mandar (separado do envio para dar para testar).
#[derive(Debug, PartialEq)]
pub struct Request {
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Value,
}

pub struct Brain {
    provider: Provider,
    base_url: String,
    key: String,
    model: String,
    system: String,
}

impl Brain {
    pub fn new(s: &Settings) -> Self {
        Self {
            provider: s.provider,
            base_url: s.brain_base_url(),
            key: s.api_key.clone(),
            model: s.brain_model(),
            system: system_prompt(s.owner_name()),
        }
    }

    /// A conversa recente no formato das APIs (as falas que vieram do PC entram como dela).
    fn messages(history: &[ChatItem]) -> Vec<Value> {
        let start = history.len().saturating_sub(CONTEXT_ITEMS);
        let mut out: Vec<Value> = Vec::new();
        for item in &history[start..] {
            if item.text.starts_with(ERROR_PREFIX) {
                continue; // os avisos de erro do app não são falas dela
            }
            let role = if item.mine { "user" } else { "assistant" };
            // As APIs pedem papéis alternados e a primeira do usuário: junta as seguidas.
            match out.last_mut() {
                Some(last) if last["role"] == role => {
                    let joined = format!("{}\n{}", last["content"].as_str().unwrap_or(""), item.text);
                    last["content"] = Value::String(joined);
                }
                _ => out.push(json!({"role": role, "content": item.text})),
            }
        }
        while out.first().is_some_and(|m| m["role"] != "user") {
            out.remove(0);
        }
        out
    }

    pub fn request(&self, history: &[ChatItem]) -> Request {
        let messages = Self::messages(history);
        let base = self.base_url.trim_end_matches('/');
        match self.provider {
            Provider::Anthropic => Request {
                url: format!("{base}/messages"),
                headers: vec![
                    ("x-api-key".into(), self.key.clone()),
                    ("anthropic-version".into(), "2023-06-01".into()),
                ],
                body: json!({
                    "model": self.model,
                    "system": self.system,
                    "messages": messages,
                    "max_tokens": 600,
                    "temperature": 0.8,
                }),
            },
            _ => {
                let mut all = vec![json!({"role": "system", "content": self.system})];
                all.extend(messages);
                let mut headers = Vec::new();
                if !self.key.is_empty() {
                    headers.push(("Authorization".into(), format!("Bearer {}", self.key)));
                }
                Request {
                    url: format!("{base}/chat/completions"),
                    headers,
                    body: json!({
                        "model": self.model,
                        "messages": all,
                        "max_tokens": 600,
                        "temperature": 0.8,
                    }),
                }
            }
        }
    }

    /// Tira o texto da resposta da API.
    pub fn parse(&self, body: &Value) -> Result<String, String> {
        let text = match self.provider {
            Provider::Anthropic => body["content"]
                .as_array()
                .map(|blocks| {
                    blocks
                        .iter()
                        .filter(|b| b["type"] == "text")
                        .filter_map(|b| b["text"].as_str())
                        .collect::<String>()
                })
                .unwrap_or_default(),
            _ => body["choices"][0]["message"]["content"]
                .as_str()
                .unwrap_or("")
                .to_string(),
        };
        let text = text.trim();
        if text.is_empty() {
            Err("a IA respondeu vazio".into())
        } else {
            Ok(text.to_string())
        }
    }

    /// Pergunta à IA (bloqueia até responder).
    pub fn reply(&self, history: &[ChatItem]) -> Result<String, String> {
        let req = self.request(history);
        let agent = ureq::AgentBuilder::new().timeout(TIMEOUT).build();
        let mut call = agent.post(&req.url).set("Content-Type", "application/json");
        for (k, v) in &req.headers {
            call = call.set(k, v);
        }
        match call.send_json(req.body) {
            Ok(resp) => {
                let body: Value = resp.into_json().map_err(|e| format!("resposta estranha da IA: {e}"))?;
                self.parse(&body)
            }
            Err(ureq::Error::Status(code, resp)) => {
                let detail = resp.into_string().unwrap_or_default();
                Err(match code {
                    401 | 403 => "a chave da IA foi recusada. Confira em Configurar.".into(),
                    404 => format!("a IA não achou o modelo \"{}\". Confira em Configurar.", self.model),
                    429 => "a IA pediu para esperar um pouco (limite de uso).".into(),
                    _ => format!("a IA deu erro {code}: {}", detail.chars().take(160).collect::<String>()),
                })
            }
            Err(e) => Err(format!("sem conexão com a IA ({e}).")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(mine: bool, text: &str) -> ChatItem {
        ChatItem {
            mine,
            text: text.into(),
            elsewhere: false,
        }
    }

    #[test]
    fn prompt_tem_a_personalidade_do_nucleo() {
        let p = system_prompt("Mateus");
        assert!(p.contains("Seu nome é Akemi"));
        assert!(p.contains("Sua lealdade é a Mateus"));
        assert!(p.contains("Como falar:"));
        assert!(p.contains("Mateus: Oi Akemi"));
        assert!(p.contains("só no celular de Mateus"));
        assert!(!p.contains("{owner}"));
        // A parte de rodar em segundo plano não serve no celular.
        assert!(!p.contains("momento de pensamento"));
    }

    #[test]
    fn mensagens_alternam_e_comecam_pelo_dono() {
        let h = [
            item(false, "Oi!"),
            item(true, "oi"),
            item(true, "tudo bem?"),
            item(false, "Tudo."),
        ];
        let m = Brain::messages(&h);
        assert_eq!(
            m,
            vec![
                json!({"role": "user", "content": "oi\ntudo bem?"}),
                json!({"role": "assistant", "content": "Tudo."})
            ]
        );
    }

    #[test]
    fn pedido_openai_e_anthropic() {
        let mut s = Settings {
            api_key: "k".into(),
            ..Settings::default()
        };
        let h = [item(true, "oi")];

        let r = Brain::new(&s).request(&h);
        assert_eq!(r.url, "https://integrate.api.nvidia.com/v1/chat/completions");
        assert_eq!(r.headers, vec![("Authorization".to_string(), "Bearer k".to_string())]);
        assert_eq!(r.body["messages"][0]["role"], "system");
        assert_eq!(r.body["messages"][1], json!({"role": "user", "content": "oi"}));

        s.provider = Provider::Anthropic;
        let b = Brain::new(&s);
        let r = b.request(&h);
        assert_eq!(r.url, "https://api.anthropic.com/v1/messages");
        assert!(r.headers.contains(&("x-api-key".to_string(), "k".to_string())));
        assert_eq!(r.body["messages"], json!([{"role": "user", "content": "oi"}]));
        assert!(r.body["system"].as_str().unwrap().contains("Akemi"));
        let resp = json!({"content": [{"type": "text", "text": " Oi! "}]});
        assert_eq!(b.parse(&resp).unwrap(), "Oi!");
        assert!(b.parse(&json!({"content": []})).is_err());
    }

    /// Uma IA de mentira, compatível com a OpenAI, num servidor HTTP local.
    #[test]
    fn conversa_com_uma_ia_compativel() {
        use std::io::{BufRead, BufReader, Read, Write};
        let server = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = server.local_addr().unwrap().port();
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = server.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let (mut len, mut auth, mut line) = (0usize, String::new(), String::new());
            reader.read_line(&mut line).unwrap();
            let first = line.clone();
            loop {
                line.clear();
                reader.read_line(&mut line).unwrap();
                let l = line.trim().to_ascii_lowercase();
                if l.is_empty() {
                    break;
                }
                if let Some(v) = l.strip_prefix("content-length:") {
                    len = v.trim().parse().unwrap();
                }
                if l.starts_with("authorization:") {
                    auth = line.trim().to_string();
                }
            }
            let mut body = vec![0; len];
            reader.read_exact(&mut body).unwrap();
            let answer = r#"{"choices":[{"message":{"role":"assistant","content":"Oi! Tô aqui no celular."}}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{answer}",
                answer.len()
            )
            .unwrap();
            (first, auth, String::from_utf8(body).unwrap())
        });
        let s = Settings {
            provider: Provider::Other,
            brain_url: format!("http://127.0.0.1:{port}/v1"),
            brain_model: "qwen2.5:7b".into(),
            api_key: "segredo".into(),
            ..Settings::default()
        };
        let reply = Brain::new(&s).reply(&[item(true, "oi")]).unwrap();
        assert_eq!(reply, "Oi! Tô aqui no celular.");
        let (first, auth, body) = handle.join().unwrap();
        assert!(first.starts_with("POST /v1/chat/completions"));
        assert_eq!(auth, "Authorization: Bearer segredo");
        let body: Value = serde_json::from_str(&body).unwrap();
        assert_eq!(body["model"], "qwen2.5:7b");
    }
}
