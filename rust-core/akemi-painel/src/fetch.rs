//! Busca, numa thread, a lista de modelos de um provedor e as vozes da Fish Audio.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Debug, Clone)]
pub enum Fetch {
    Loading,
    Done(Vec<Item>),
    Failed(String),
}

#[derive(Debug, Clone)]
pub struct Item {
    pub id: String,
    pub label: String,
}

/// O que precisa para pedir a lista (já com os valores que estão na tela,
/// mesmo que ainda não salvos).
#[derive(Debug, Clone)]
pub struct Request {
    /// "nim", "openai", ..., ou "fish" para as vozes.
    pub what: String,
    pub base_url: String,
    pub api_key: String,
}

pub type Results = Arc<Mutex<HashMap<String, Fetch>>>;

pub fn start(results: &Results, request: Request, repaint: impl Fn() + Send + 'static) {
    results.lock().unwrap().insert(request.what.clone(), Fetch::Loading);
    let results = Arc::clone(results);
    std::thread::spawn(move || {
        let outcome = match run(&request) {
            Ok(mut items) if !items.is_empty() => {
                items.sort_by_key(|i| i.label.to_lowercase());
                Fetch::Done(items)
            }
            Ok(_) => Fetch::Failed("o provedor respondeu, mas sem nenhum item".into()),
            Err(err) => Fetch::Failed(err),
        };
        results.lock().unwrap().insert(request.what, outcome);
        repaint();
    });
}

fn agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(6))
        .timeout(Duration::from_secs(20))
        .user_agent("akemi-painel")
        .build()
}

fn run(req: &Request) -> Result<Vec<Item>, String> {
    let base = req.base_url.trim_end_matches('/');
    let response = match req.what.as_str() {
        "echo" => {
            return Ok(vec![Item {
                id: "echo".into(),
                label: "echo".into(),
            }])
        }
        "fish" => {
            if req.api_key.is_empty() {
                return Err("coloque a chave da Fish Audio primeiro".into());
            }
            agent()
                .get(&format!("{base}/model"))
                .query("self", "true")
                .query("page_size", "100")
                .set("Authorization", &format!("Bearer {}", req.api_key))
                .call()
        }
        "anthropic" => {
            if req.api_key.is_empty() {
                return Err("coloque a chave da Anthropic primeiro".into());
            }
            agent()
                .get("https://api.anthropic.com/v1/models")
                .query("limit", "1000")
                .set("x-api-key", &req.api_key)
                .set("anthropic-version", "2023-06-01")
                .call()
        }
        _ => {
            let mut call = agent().get(&format!("{base}/models"));
            if !req.api_key.is_empty() {
                call = call.set("Authorization", &format!("Bearer {}", req.api_key));
            } else if matches!(req.what.as_str(), "nim" | "openai") {
                return Err("coloque a chave de API primeiro".into());
            }
            call.call()
        }
    };
    let body: serde_json::Value = match response {
        Ok(resp) => resp.into_json().map_err(|e| format!("resposta estranha: {e}"))?,
        Err(ureq::Error::Status(401 | 403, _)) => return Err("a chave foi recusada".into()),
        Err(ureq::Error::Status(code, _)) => return Err(format!("o provedor respondeu {code}")),
        Err(ureq::Error::Transport(t)) => {
            return Err(format!(
                "não consegui falar com {base}: {}",
                t.message().unwrap_or("sem conexão")
            ))
        }
    };
    Ok(parse(&req.what, &body))
}

pub fn parse(what: &str, body: &serde_json::Value) -> Vec<Item> {
    let list = if what == "fish" { &body["items"] } else { &body["data"] };
    let Some(list) = list.as_array() else { return Vec::new() };
    list.iter()
        .filter_map(|item| {
            let id = item
                .get(if what == "fish" { "_id" } else { "id" })?
                .as_str()?
                .to_string();
            let name = item
                .get("title")
                .or_else(|| item.get("display_name"))
                .and_then(|v| v.as_str())
                .filter(|name| *name != id);
            let label = match name {
                Some(name) => format!("{name}  ({id})"),
                None => id.clone(),
            };
            Some(Item { id, label })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_openai_style_and_fish() {
        let models = serde_json::json!({"data": [{"id": "qwen2.5:7b"}, {"id": "llama3"}]});
        let items = parse("ollama", &models);
        assert_eq!(items.len(), 2);
        assert_eq!(items[0].id, "qwen2.5:7b");

        let anthropic = serde_json::json!({"data": [{"id": "claude-x", "display_name": "Claude X"}]});
        assert_eq!(parse("anthropic", &anthropic)[0].label, "Claude X  (claude-x)");

        let fish = serde_json::json!({"items": [{"_id": "abc123", "title": "Akemi"}]});
        let voices = parse("fish", &fish);
        assert_eq!(voices[0].id, "abc123");
        assert_eq!(voices[0].label, "Akemi  (abc123)");
    }
}
