//! A janela do painel.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use eframe::egui::{self, Color32, RichText};

use crate::fetch::{self, Fetch};
use crate::project::{parse_paths, Doc, DocKind, Project};
use crate::schema::{self, Field, Kind, ModelSrc, Section, FIELDS, LOCAL_PROVIDERS};

const ACCENT: Color32 = Color32::from_rgb(0xd6, 0x33, 0x7f);
const WARN: Color32 = Color32::from_rgb(0xe0, 0x8a, 0x2a);
const ERROR: Color32 = Color32::from_rgb(0xe0, 0x4f, 0x4f);
const OK: Color32 = Color32::from_rgb(0x3f, 0xb3, 0x7f);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Page {
    Section(Section),
    Doc(DocKind),
}

pub struct PainelApp {
    project: Project,
    values: HashMap<&'static str, String>,
    loaded: HashMap<&'static str, String>,
    docs: Vec<Doc>,
    page: Page,
    search: String,
    /// Texto digitado nas caixas "adicionar" das listas.
    scratch: HashMap<&'static str, String>,
    /// Filtro dentro dos menus de modelos.
    menu_filter: String,
    revealed: HashSet<&'static str>,
    fetches: fetch::Results,
    piper_models: Vec<String>,
    toast: Option<(String, Color32, Instant)>,
    confirm_close: bool,
    allow_close: bool,
    folder_input: String,
}

impl PainelApp {
    pub fn new(cc: &eframe::CreationContext<'_>, root: PathBuf) -> Self {
        cc.egui_ctx.all_styles_mut(|style| {
            style.visuals.selection.bg_fill = ACCENT;
            style.visuals.hyperlink_color = ACCENT;
            style.spacing.item_spacing = egui::vec2(8.0, 6.0);
            style.spacing.button_padding = egui::vec2(8.0, 4.0);
        });
        let project = Project::load(&root);
        let mut app = Self {
            values: HashMap::new(),
            loaded: HashMap::new(),
            docs: Vec::new(),
            page: Page::Section(Section::Geral),
            search: String::new(),
            scratch: HashMap::new(),
            menu_filter: String::new(),
            revealed: HashSet::new(),
            fetches: Arc::new(Mutex::new(HashMap::new())),
            piper_models: Vec::new(),
            toast: None,
            confirm_close: false,
            allow_close: false,
            folder_input: root.to_string_lossy().into_owned(),
            project,
        };
        app.reload_from_project();
        app
    }

    fn reload_from_project(&mut self) {
        self.values = self.project.effective();
        self.loaded = self.values.clone();
        self.piper_models = self.project.piper_models();
        self.load_docs();
    }

    fn load_docs(&mut self) {
        let persona = self.project.resolve(&self.values["persona_file"]);
        let triggers = self.project.root.join("akemi/brain/triggers.yaml");
        let mcp = parse_paths(&self.values["mcp_config"])
            .into_iter()
            .next()
            .map(|p| self.project.resolve(&p))
            .unwrap_or_else(|| self.project.resolve(&self.values["data_dir"]).join("mcp.json"));
        self.docs = vec![
            Doc::load(DocKind::Persona, persona),
            Doc::load(DocKind::Triggers, triggers),
            Doc::load(DocKind::Mcp, mcp),
        ];
    }

    fn open_folder(&mut self, root: PathBuf) {
        let root = crate::project::find_root(&root).unwrap_or(root);
        self.project = Project::load(&root);
        self.folder_input = root.to_string_lossy().into_owned();
        self.reload_from_project();
        self.fetches.lock().unwrap().clear();
        self.notify(format!("Abri {}", root.display()), OK);
    }

    fn value(&self, key: &str) -> &str {
        self.values.get(key).map(String::as_str).unwrap_or("")
    }

    fn is_dirty(&self, field: &Field) -> bool {
        !schema::same_value(field, &self.values[field.key], &self.loaded[field.key])
    }

    fn dirty_fields(&self) -> Vec<&'static Field> {
        FIELDS.iter().filter(|f| self.is_dirty(f)).collect()
    }

    fn errors(&self) -> Vec<(&'static Field, String)> {
        FIELDS
            .iter()
            .filter_map(|f| schema::validate(f, &self.values[f.key]).err().map(|e| (f, e)))
            .collect()
    }

    fn any_dirty(&self) -> bool {
        !self.dirty_fields().is_empty() || self.docs.iter().any(Doc::dirty)
    }

    fn notify(&mut self, text: impl Into<String>, color: Color32) {
        self.toast = Some((text.into(), color, Instant::now()));
    }

    fn save(&mut self) {
        let errors = self.errors();
        if let Some((field, err)) = errors.first() {
            self.notify(format!("Não salvei: \"{}\" {}", field.label, err), ERROR);
            return;
        }
        for doc in &self.docs {
            if doc.dirty() {
                if let Err(err) = doc.check() {
                    self.notify(format!("Não salvei: {}: {err}", doc.kind.title()), ERROR);
                    return;
                }
            }
        }
        let changes: Vec<(&'static Field, Option<String>)> = self
            .dirty_fields()
            .into_iter()
            .map(|field| {
                let value = schema::normalize(field, &self.values[field.key]);
                if field.optional && value.trim().is_empty() {
                    (field, None)
                } else {
                    (field, Some(value))
                }
            })
            .collect();
        let mut saved = 0;
        if !changes.is_empty() {
            if let Err(err) = self.project.save(&changes) {
                self.notify(err, ERROR);
                return;
            }
            saved += changes.len();
        }
        for doc in &mut self.docs {
            if doc.dirty() {
                if let Err(err) = doc.save() {
                    let msg = format!("{}: {err}", doc.kind.title());
                    self.toast = Some((msg, ERROR, Instant::now()));
                    return;
                }
                saved += 1;
            }
        }
        let docs = std::mem::take(&mut self.docs);
        self.values = self.project.effective();
        self.loaded = self.values.clone();
        self.docs = docs;
        // Se o caminho da personalidade ou do MCP mudou, recarrega os arquivos.
        if changes
            .iter()
            .any(|(f, _)| matches!(f.key, "persona_file" | "mcp_config" | "data_dir"))
        {
            self.load_docs();
        }
        self.notify(
            format!(
                "Salvo ({saved} mudança{}). Vale na próxima vez que ela ligar.",
                if saved == 1 { "" } else { "s" }
            ),
            OK,
        );
    }

    fn discard(&mut self) {
        self.values = self.loaded.clone();
        for doc in &mut self.docs {
            doc.text = doc.saved.clone();
        }
        self.notify("Mudanças descartadas.", OK);
    }

    // ============================================================ avisos
    fn warnings(&self) -> Vec<(Color32, String)> {
        let v = |k: &str| self.value(k);
        let mut out = Vec::new();
        let mut providers = vec![v("brain_provider").to_string()];
        if !v("deep_provider").is_empty() {
            providers.push(v("deep_provider").into());
        }
        for p in schema::parse_list(v("brain_fallback")).unwrap_or_default() {
            providers.push(p);
        }
        for (provider, key, name) in [
            ("nim", "nvidia_api_key", "a NVIDIA (NIM)"),
            ("anthropic", "anthropic_api_key", "a Anthropic"),
        ] {
            if providers.iter().any(|p| p == provider) && schema::secret_missing(v(key)) {
                out.push((ERROR, format!("Ela usa {name}, mas falta a chave (Modelos de IA).")));
            }
        }
        if providers.iter().any(|p| p == "openai")
            && schema::secret_missing(v("openai_api_key"))
            && v("openai_base_url").contains("api.openai.com")
        {
            out.push((ERROR, "Ela usa a OpenAI, mas falta a chave (Modelos de IA).".into()));
        }
        if schema::parse_list(v("brain_fallback")).unwrap_or_default().is_empty() {
            out.push((
                WARN,
                "Sem cérebro reserva: se a internet cair, ela fica sem pensar. Um Ollama local resolve.".into(),
            ));
        }
        match v("tts_engine") {
            "fish" if schema::secret_missing(v("fish_api_key")) => {
                out.push((ERROR, "A voz é a Fish Audio, mas falta a chave (Voz).".into()))
            }
            "piper" if !self.project.resolve(v("piper_model_path")).is_file() => out.push((
                ERROR,
                "A voz é o Piper, mas o modelo .onnx não está no lugar (Voz).".into(),
            )),
            "kokoro" if !self.project.resolve(v("kokoro_model_path")).is_file() => {
                out.push((ERROR, "A voz é o Kokoro, mas o modelo não está no lugar (Voz).".into()))
            }
            "console" => out.push((
                WARN,
                "Ela está sem voz (só texto no terminal). Escolha uma em Voz.".into(),
            )),
            _ => {}
        }
        if schema::parse_bool(v("files_enabled")) == Some(true) {
            let dirs = schema::parse_list(v("files_allowed_dirs")).unwrap_or_default();
            if dirs.is_empty() {
                out.push((
                    WARN,
                    "Nenhuma pasta liberada: ela não lê arquivos (Pastas e arquivos).".into(),
                ));
            }
            for dir in dirs {
                if !self.project.resolve(&dir).is_dir() {
                    out.push((WARN, format!("A pasta liberada \"{dir}\" não existe neste PC.")));
                }
            }
            let cloud = providers.iter().any(|p| !LOCAL_PROVIDERS.contains(&p.as_str()));
            if cloud && schema::parse_bool(v("files_allow_cloud")) != Some(true) {
                out.push((WARN, "O cérebro está na nuvem e \"mandar arquivos para a nuvem\" está desligado: a análise de arquivos fica parada.".into()));
            }
        }
        let token_len = v("akemi_token").chars().count();
        if token_len > 0 && token_len < 16 {
            out.push((ERROR, "O AKEMI_TOKEN precisa de 16 caracteres ou mais (Rede).".into()));
        }
        if !matches!(v("painel_host"), "127.0.0.1" | "localhost" | "::1") && token_len < 16 {
            out.push((
                ERROR,
                "O painel web está aberto para a rede sem AKEMI_TOKEN de 16+ caracteres.".into(),
            ));
        }
        let sec = v("secretaria_token").chars().count();
        if sec > 0 && sec < 16 {
            out.push((
                ERROR,
                "A senha da ponte da Secretária precisa de 16+ caracteres.".into(),
            ));
        }
        if schema::parse_bool(v("oficina_enabled")) == Some(true) && schema::secret_missing(v("github_token")) {
            out.push((ERROR, "A Oficina está ligada, mas falta o token do GitHub.".into()));
        }
        out
    }

    // ============================================================ provedores
    fn provider_for(&self, src: ModelSrc) -> String {
        let brain = self.value("brain_provider").to_string();
        let or_brain = |k: &str| {
            let p = self.value(k);
            if p.is_empty() {
                brain.clone()
            } else {
                p.to_string()
            }
        };
        match src {
            ModelSrc::Brain => brain.clone(),
            ModelSrc::Deep => or_brain("deep_provider"),
            ModelSrc::Vision => or_brain("vision_provider"),
            ModelSrc::Fallback => schema::parse_list(self.value("brain_fallback"))
                .ok()
                .and_then(|l| l.into_iter().next())
                .unwrap_or_else(|| "ollama".into()),
            ModelSrc::Embed => self.value("memory_embed_provider").to_string(),
            ModelSrc::Anthropic => "anthropic".into(),
        }
    }

    fn request_for(&self, what: &str) -> fetch::Request {
        let (url, key) = match what {
            "nim" => ("nim_base_url", "nvidia_api_key"),
            "openai" => ("openai_base_url", "openai_api_key"),
            "ollama" => ("ollama_base_url", ""),
            "llamacpp" => ("llamacpp_base_url", ""),
            "anthropic" => ("", "anthropic_api_key"),
            "fish" => ("fish_base_url", "fish_api_key"),
            _ => ("", ""),
        };
        fetch::Request {
            what: what.to_string(),
            base_url: if url.is_empty() {
                String::new()
            } else {
                self.value(url).trim().to_string()
            },
            api_key: if key.is_empty() || schema::is_placeholder(self.value(key)) {
                String::new()
            } else {
                self.value(key).trim().to_string()
            },
        }
    }

    fn start_fetch(&self, ctx: &egui::Context, what: &str) {
        let ctx = ctx.clone();
        fetch::start(&self.fetches, self.request_for(what), move || ctx.request_repaint());
    }

    fn fetch_state(&self, what: &str) -> Option<Fetch> {
        self.fetches.lock().unwrap().get(what).cloned()
    }
}

// ================================================================ widgets
fn toggle(ui: &mut egui::Ui, on: &mut bool) -> egui::Response {
    let size = ui.spacing().interact_size.y * egui::vec2(2.0, 1.0);
    let (rect, mut response) = ui.allocate_exact_size(size, egui::Sense::click());
    if response.clicked() {
        *on = !*on;
        response.mark_changed();
    }
    response.widget_info(|| egui::WidgetInfo::selected(egui::WidgetType::Checkbox, ui.is_enabled(), *on, ""));
    if ui.is_rect_visible(rect) {
        let how_on = ui.ctx().animate_bool_responsive(response.id, *on);
        let visuals = ui.style().interact_selectable(&response, *on);
        let rect = rect.expand(visuals.expansion);
        let radius = 0.5 * rect.height();
        let fill = if *on { OK } else { visuals.bg_fill };
        ui.painter()
            .rect(rect, radius, fill, visuals.bg_stroke, egui::StrokeKind::Inside);
        let x = egui::lerp((rect.left() + radius)..=(rect.right() - radius), how_on);
        ui.painter().circle(
            egui::pos2(x, rect.center().y),
            0.75 * radius,
            Color32::WHITE,
            visuals.fg_stroke,
        );
    }
    response
}

#[cfg(any(windows, target_os = "macos"))]
fn pick_dir(start: &std::path::Path) -> Option<PathBuf> {
    rfd::FileDialog::new().set_directory(start).pick_folder()
}

#[cfg(any(windows, target_os = "macos"))]
fn pick_file(start: &std::path::Path) -> Option<PathBuf> {
    rfd::FileDialog::new().set_directory(start).pick_file()
}

#[cfg(not(any(windows, target_os = "macos")))]
fn pick_dir(_: &std::path::Path) -> Option<PathBuf> {
    None
}

#[cfg(not(any(windows, target_os = "macos")))]
fn pick_file(_: &std::path::Path) -> Option<PathBuf> {
    None
}

const HAS_PICKER: bool = cfg!(any(windows, target_os = "macos"));

impl PainelApp {
    /// Uma linha de configuração: nome e explicação à esquerda, o controle à direita.
    fn field_row(&mut self, ui: &mut egui::Ui, field: &'static Field) {
        let dirty = self.is_dirty(field);
        ui.vertical(|ui| {
            ui.set_max_width(300.0);
            let mut title = RichText::new(field.label).strong();
            if dirty {
                title = title.color(ACCENT);
            }
            ui.horizontal_wrapped(|ui| {
                ui.label(title);
                if dirty {
                    ui.label(RichText::new("•").color(ACCENT))
                        .on_hover_text("Mudou e ainda não foi salvo");
                }
            });
            if !field.help.is_empty() {
                ui.label(RichText::new(field.help).small().weak());
            }
            ui.label(RichText::new(field.key.to_ascii_uppercase()).small().monospace().weak());
        });

        ui.vertical(|ui| {
            let mut value = self.values[field.key].clone();
            self.control(ui, field, &mut value);
            if value != self.values[field.key] {
                self.values.insert(field.key, value);
            }
            let current = self.values[field.key].clone();
            let current = current.as_str();
            if let Err(err) = schema::validate(field, current) {
                ui.label(RichText::new(format!("⚠ {err}")).color(ERROR).small());
            }
            ui.horizontal_wrapped(|ui| {
                if self.project.in_painel(field) {
                    ui.label(RichText::new("vem do painel web (painel.json)").small().color(WARN))
                        .on_hover_text(
                            "O painel no navegador grava por cima do .env. Salvando aqui, os dois ficam iguais.",
                        );
                }
                let upper = field.key.to_ascii_uppercase();
                if std::env::var_os(&upper).is_some() {
                    ui.label(
                        RichText::new(format!("a variável de ambiente {upper} manda nisto"))
                            .small()
                            .color(WARN),
                    )
                    .on_hover_text("Variáveis de ambiente do sistema valem por cima do .env.");
                }
                let default_differs = !schema::same_value(field, current, field.default);
                if default_differs
                    && ui
                        .small_button("↺ padrão")
                        .on_hover_text(if field.default.is_empty() {
                            "Voltar ao padrão (vazio)".to_string()
                        } else {
                            format!("Voltar ao padrão: {}", field.default)
                        })
                        .clicked()
                {
                    self.values.insert(field.key, field.default.to_string());
                }
                if dirty && ui.small_button("desfazer").clicked() {
                    self.values.insert(field.key, self.loaded[field.key].clone());
                }
            });
        });
        ui.end_row();
    }

    fn text_box(ui: &mut egui::Ui, value: &mut String, width: f32, hint: &str) -> egui::Response {
        ui.add(egui::TextEdit::singleline(value).desired_width(width).hint_text(hint))
    }

    fn control(&mut self, ui: &mut egui::Ui, field: &'static Field, value: &mut String) {
        let ctx = ui.ctx().clone();
        let hint = if field.default.is_empty() {
            "(vazio)"
        } else {
            field.default
        };
        match field.kind {
            Kind::Text => {
                Self::text_box(ui, value, 360.0, hint);
            }
            Kind::Secret => {
                ui.horizontal(|ui| {
                    let show = self.revealed.contains(field.key);
                    ui.add(
                        egui::TextEdit::singleline(value)
                            .password(!show)
                            .desired_width(320.0)
                            .hint_text("(não definida)"),
                    );
                    if ui.small_button(if show { "esconder" } else { "mostrar" }).clicked() {
                        if show {
                            self.revealed.remove(field.key);
                        } else {
                            self.revealed.insert(field.key);
                        }
                    }
                    if schema::is_placeholder(value) {
                        ui.label(RichText::new("é o exemplo do .env.example").small().color(WARN));
                    } else if !value.is_empty() {
                        ui.label(RichText::new("✔").color(OK)).on_hover_text("Definida");
                    }
                });
            }
            Kind::Bool => {
                let mut on = schema::parse_bool(value).unwrap_or(false);
                ui.horizontal(|ui| {
                    if toggle(ui, &mut on).changed() {
                        *value = on.to_string();
                    }
                    ui.label(if on { "ligado" } else { "desligado" });
                });
            }
            Kind::Float { .. } | Kind::Int { .. } => {
                Self::text_box(ui, value, 120.0, hint);
            }
            Kind::Choice(options) => {
                let shown = if value.is_empty() {
                    "(padrão)".to_string()
                } else {
                    value.clone()
                };
                egui::ComboBox::from_id_salt(field.key)
                    .selected_text(shown)
                    .width(220.0)
                    .show_ui(ui, |ui| {
                        if field.optional {
                            ui.selectable_value(value, String::new(), "(padrão)");
                        }
                        for opt in options {
                            let label = if opt.is_empty() { "(desligado)" } else { opt };
                            ui.selectable_value(value, opt.to_string(), label);
                        }
                    });
            }
            Kind::Combo(options) => {
                ui.horizontal(|ui| {
                    Self::text_box(ui, value, 280.0, hint);
                    ui.menu_button("⏷", |ui| {
                        egui::ScrollArea::vertical().max_height(320.0).show(ui, |ui| {
                            for opt in options {
                                let label = if opt.is_empty() { "(vazio)" } else { opt };
                                if ui.selectable_label(value == opt, label).clicked() {
                                    *value = opt.to_string();
                                    ui.close_menu();
                                }
                            }
                        });
                    });
                });
            }
            Kind::List => self.list_control(ui, field, value, false),
            Kind::DirList => self.list_control(ui, field, value, true),
            Kind::Checklist(options) => {
                let mut items = schema::parse_list(value).unwrap_or_default();
                let ordered = field.key == "brain_fallback";
                let mut changed = false;
                ui.horizontal_wrapped(|ui| {
                    ui.set_max_width(460.0);
                    for opt in options {
                        let pos = items.iter().position(|i| i == opt);
                        let mut on = pos.is_some();
                        let label = match (ordered, pos) {
                            (true, Some(p)) => format!("{}º {opt}", p + 1),
                            _ => opt.to_string(),
                        };
                        if ui.checkbox(&mut on, label).changed() {
                            changed = true;
                            if on {
                                items.push(opt.to_string());
                            } else {
                                items.retain(|i| i != opt);
                            }
                        }
                    }
                });
                if changed {
                    *value = schema::render_list(&items);
                }
            }
            Kind::File | Kind::Dir => {
                ui.horizontal(|ui| {
                    Self::text_box(ui, value, 320.0, hint);
                    if HAS_PICKER && ui.button("Escolher…").clicked() {
                        let start = self.project.root.clone();
                        let picked = if field.kind == Kind::Dir {
                            pick_dir(&start)
                        } else {
                            pick_file(&start)
                        };
                        if let Some(path) = picked {
                            *value = self.project.relativize(&path);
                        }
                    }
                    if !value.is_empty() {
                        let path = self.project.resolve(value);
                        let exists = if field.kind == Kind::Dir {
                            path.is_dir()
                        } else {
                            path.is_file()
                        };
                        if exists {
                            ui.label(RichText::new("✔").color(OK))
                                .on_hover_text(path.display().to_string());
                        } else {
                            ui.label(RichText::new("não existe").small().color(WARN))
                                .on_hover_text(path.display().to_string());
                        }
                    }
                });
            }
            Kind::PiperModel => {
                ui.horizontal(|ui| {
                    Self::text_box(ui, value, 320.0, hint);
                    let models = self.piper_models.clone();
                    ui.menu_button("⏷", |ui| {
                        if models.is_empty() {
                            ui.label("Nenhum .onnx do Piper em data/voices.");
                        }
                        for m in &models {
                            if ui.selectable_label(value == m, m).clicked() {
                                *value = m.clone();
                                ui.close_menu();
                            }
                        }
                    });
                    if HAS_PICKER && ui.button("Escolher…").clicked() {
                        if let Some(path) = pick_file(&self.project.root) {
                            *value = self.project.relativize(&path);
                        }
                    }
                    let exists = self.project.resolve(value).is_file();
                    if !exists {
                        ui.label(RichText::new("não existe").small().color(WARN));
                    }
                });
            }
            Kind::Model(src) => {
                let provider = self.provider_for(src);
                self.remote_list_control(ui, &ctx, field, value, &provider, hint);
            }
            Kind::FishVoice => self.remote_list_control(ui, &ctx, field, value, "fish", "(voz padrão)"),
        }
    }

    fn remote_list_control(
        &mut self,
        ui: &mut egui::Ui,
        ctx: &egui::Context,
        field: &'static Field,
        value: &mut String,
        what: &str,
        hint: &str,
    ) {
        let state = self.fetch_state(what);
        ui.horizontal(|ui| {
            Self::text_box(ui, value, 300.0, hint);
            if what.is_empty() {
                ui.label(RichText::new("escolha o provedor primeiro").small().weak());
                return;
            }
            let filter = &mut self.menu_filter;
            ui.menu_button("⏷", |ui| match &state {
                Some(Fetch::Done(items)) => {
                    ui.add(
                        egui::TextEdit::singleline(filter)
                            .hint_text("filtrar…")
                            .desired_width(260.0),
                    );
                    let needle = filter.to_lowercase();
                    egui::ScrollArea::vertical().max_height(360.0).show(ui, |ui| {
                        for item in items.iter().filter(|i| i.label.to_lowercase().contains(&needle)) {
                            if ui.selectable_label(*value == item.id, &item.label).clicked() {
                                *value = item.id.clone();
                                ui.close_menu();
                            }
                        }
                    });
                }
                Some(Fetch::Loading) => {
                    ui.spinner();
                }
                _ => {
                    ui.label("Clique em ↻ para buscar a lista.");
                }
            });
            let tip = if what == "fish" {
                "Buscar as suas vozes na Fish Audio".to_string()
            } else {
                format!("Buscar os modelos de {what}")
            };
            if ui.button("↻").on_hover_text(tip).clicked() {
                self.start_fetch(ctx, what);
            }
            if what != "fish" {
                ui.label(RichText::new(what).small().weak())
                    .on_hover_text("O provedor deste modelo");
            }
        });
        match state {
            Some(Fetch::Loading) => {
                ui.horizontal(|ui| {
                    ui.spinner();
                    ui.label(RichText::new("buscando…").small());
                });
            }
            Some(Fetch::Failed(err)) => {
                ui.label(RichText::new(format!("não deu para buscar: {err}")).small().color(WARN));
            }
            Some(Fetch::Done(items)) => {
                let known = value.is_empty() || items.iter().any(|i| i.id == *value);
                let note = if known {
                    format!("{} disponíveis", items.len())
                } else {
                    format!("{} disponíveis · este não está na lista", items.len())
                };
                ui.label(RichText::new(note).small().color(if known { OK } else { WARN }));
            }
            None => {}
        }
        let _ = field;
    }

    fn list_control(&mut self, ui: &mut egui::Ui, field: &'static Field, value: &mut String, dirs: bool) {
        let Ok(mut items) = schema::parse_list(value) else {
            // Não é JSON: deixa editar o texto cru até consertar.
            ui.add(egui::TextEdit::singleline(value).desired_width(360.0));
            return;
        };
        let mut changed = false;
        let mut remove = None;
        for (i, item) in items.iter().enumerate() {
            ui.horizontal(|ui| {
                if dirs {
                    let ok = self.project.resolve(item).is_dir();
                    ui.label(RichText::new(if ok { "📁" } else { "⚠" }).color(if ok { OK } else { WARN }))
                        .on_hover_text(if ok {
                            "A pasta existe"
                        } else {
                            "Esta pasta não existe neste PC"
                        });
                }
                if ui.small_button("✖").on_hover_text("Tirar").clicked() {
                    remove = Some(i);
                }
                let width = ui.available_width().min(520.0);
                ui.allocate_ui(egui::vec2(width, 20.0), |ui| {
                    ui.add(egui::Label::new(RichText::new(item).monospace()).truncate())
                        .on_hover_text(item);
                });
            });
        }
        if let Some(i) = remove {
            items.remove(i);
            changed = true;
        }
        if items.is_empty() {
            ui.label(
                RichText::new(if dirs {
                    "Nenhuma pasta liberada."
                } else {
                    "Lista vazia."
                })
                .weak()
                .small(),
            );
        }
        ui.horizontal(|ui| {
            let scratch = self.scratch.entry(field.key).or_default();
            let hint = if dirs { "C:/Users/voce/Documentos" } else { "novo item" };
            let resp = ui.add(egui::TextEdit::singleline(scratch).desired_width(260.0).hint_text(hint));
            let enter = resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
            if (ui.button("Adicionar").clicked() || enter) && !scratch.trim().is_empty() {
                let new = scratch.trim().replace('\\', "/");
                if !items.contains(&new) {
                    items.push(new);
                    changed = true;
                }
                scratch.clear();
            }
            if dirs && HAS_PICKER && ui.button("Escolher pasta…").clicked() {
                if let Some(path) = pick_dir(&self.project.root) {
                    let new = path.to_string_lossy().replace('\\', "/");
                    if !items.contains(&new) {
                        items.push(new);
                        changed = true;
                    }
                }
            }
        });
        if changed {
            *value = schema::render_list(&items);
        }
    }

    // ============================================================ páginas
    fn fields_grid(&mut self, ui: &mut egui::Ui, id: &str, fields: &[&'static Field]) {
        egui::Grid::new(id)
            .num_columns(2)
            .spacing([28.0, 18.0])
            .min_col_width(240.0)
            .show(ui, |ui| {
                for field in fields {
                    self.field_row(ui, field);
                }
            });
    }

    fn section_page(&mut self, ui: &mut egui::Ui, section: Section) {
        ui.heading(format!("{} {}", section.icon(), section.title()));
        ui.label(RichText::new(section.intro()).weak());
        ui.add_space(12.0);

        if section == Section::Geral {
            self.summary(ui);
            ui.add_space(12.0);
        }

        let main: Vec<&'static Field> = FIELDS.iter().filter(|f| f.section == section && !f.advanced).collect();
        let advanced: Vec<&'static Field> = FIELDS.iter().filter(|f| f.section == section && f.advanced).collect();
        self.fields_grid(ui, "main", &main);
        if !advanced.is_empty() {
            ui.add_space(12.0);
            egui::CollapsingHeader::new(RichText::new("Avançado").strong())
                .id_salt(("adv", section.title()))
                .show(ui, |ui| self.fields_grid(ui, "adv", &advanced));
        }
        if section == Section::Apps {
            ui.add_space(16.0);
            ui.separator();
            self.doc_editor(ui, DocKind::Mcp, 18);
        }
    }

    fn summary(&mut self, ui: &mut egui::Ui) {
        let v = |k: &str| self.value(k).to_string();
        let fallback = schema::parse_list(&v("brain_fallback")).unwrap_or_default();
        let dirs = schema::parse_list(&v("files_allowed_dirs")).unwrap_or_default();
        let on = |k: &str| schema::parse_bool(&v(k)) == Some(true);
        let voice = match v("tts_engine").as_str() {
            "fish" => format!(
                "Fish Audio ({})",
                if v("fish_voice_id").is_empty() {
                    "voz padrão".into()
                } else {
                    v("fish_voice_id")
                }
            ),
            "kokoro" => format!("Kokoro ({})", v("kokoro_voice")),
            "piper" => format!(
                "Piper ({})",
                v("piper_model_path").rsplit('/').next().unwrap_or_default()
            ),
            _ => "sem voz (terminal)".into(),
        };
        let deep_provider = if v("deep_provider").is_empty() {
            v("brain_provider")
        } else {
            v("deep_provider")
        };
        let rows = [
            (
                "Cérebro rápido",
                format!("{} · {}", v("brain_provider"), v("fast_model")),
            ),
            ("Cérebro profundo", format!("{deep_provider} · {}", v("deep_model"))),
            (
                "Reserva",
                if fallback.is_empty() {
                    "nenhum".into()
                } else {
                    format!("{} · {}", fallback.join(" > "), v("fallback_fast_model"))
                },
            ),
            ("Voz", voice),
            (
                "Ouvir",
                match (on("mic_enabled"), on("system_audio_enabled")) {
                    (true, true) => format!("microfone e áudio do PC · Whisper {}", v("whisper_model")),
                    (true, false) => format!("microfone · Whisper {}", v("whisper_model")),
                    (false, true) => format!("áudio do PC · Whisper {}", v("whisper_model")),
                    _ => "desligado".into(),
                },
            ),
            (
                "Tela",
                if on("screen_enabled") {
                    "olhando".into()
                } else {
                    "desligada".into()
                },
            ),
            (
                "Pastas liberadas",
                if !on("files_enabled") {
                    "análise de arquivos desligada".into()
                } else {
                    format!("{}", dirs.len())
                },
            ),
        ];
        egui::Frame::group(ui.style()).inner_margin(12.0).show(ui, |ui| {
            ui.set_width(ui.available_width());
            egui::Grid::new("resumo")
                .num_columns(2)
                .spacing([24.0, 6.0])
                .show(ui, |ui| {
                    for (k, val) in rows {
                        ui.label(RichText::new(k).weak());
                        ui.label(RichText::new(val).strong());
                        ui.end_row();
                    }
                });
        });
        let warnings = self.warnings();
        if !warnings.is_empty() {
            ui.add_space(8.0);
            for (color, text) in warnings {
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new(if color == ERROR { "✖" } else { "⚠" }).color(color));
                    ui.label(text);
                });
            }
        }
    }

    fn doc_editor(&mut self, ui: &mut egui::Ui, kind: DocKind, rows: usize) {
        let root = self.project.root.clone();
        let Some(doc) = self.docs.iter_mut().find(|d| d.kind == kind) else {
            return;
        };
        ui.heading(format!("{} {}", kind.icon(), kind.title()));
        ui.label(RichText::new(kind.intro()).weak());
        let shown = doc.path.strip_prefix(&root).unwrap_or(&doc.path).display().to_string();
        ui.horizontal(|ui| {
            ui.label(RichText::new(shown).monospace().small());
            if !doc.exists {
                ui.label(
                    RichText::new("(ainda não existe: será criado ao salvar)")
                        .small()
                        .color(WARN),
                );
            }
            if doc.dirty() {
                ui.label(RichText::new("• mudou").small().color(ACCENT));
                if ui.small_button("desfazer").clicked() {
                    doc.text = doc.saved.clone();
                }
            }
        });
        match doc.check() {
            Ok(()) => ui.label(RichText::new("✔ formato certo").small().color(OK)),
            Err(err) => ui.label(RichText::new(format!("⚠ {err}")).small().color(ERROR)),
        };
        ui.add_space(4.0);
        ui.add(
            egui::TextEdit::multiline(&mut doc.text)
                .code_editor()
                .desired_rows(rows)
                .desired_width(f32::INFINITY),
        );
    }

    fn search_page(&mut self, ui: &mut egui::Ui) {
        let needle = self.search.to_lowercase();
        ui.heading(format!("🔍 \"{}\"", self.search));
        let mut found = false;
        for section in Section::ALL {
            let fields: Vec<&'static Field> = FIELDS
                .iter()
                .filter(|f| f.section == section)
                .filter(|f| {
                    f.label.to_lowercase().contains(&needle)
                        || f.help.to_lowercase().contains(&needle)
                        || f.key.contains(&needle.replace(' ', "_"))
                })
                .collect();
            if fields.is_empty() {
                continue;
            }
            found = true;
            ui.add_space(10.0);
            ui.label(
                RichText::new(format!("{} {}", section.icon(), section.title()))
                    .strong()
                    .size(16.0),
            );
            ui.separator();
            self.fields_grid(ui, section.title(), &fields);
        }
        if !found {
            ui.label("Nada com esse nome.");
        }
    }

    fn sidebar(&mut self, ui: &mut egui::Ui) {
        ui.add_space(8.0);
        for section in Section::ALL {
            let dirty = FIELDS.iter().any(|f| f.section == section && self.is_dirty(f));
            let errors = FIELDS
                .iter()
                .any(|f| f.section == section && schema::validate(f, &self.values[f.key]).is_err());
            let mut text = format!("{}  {}", section.icon(), section.title());
            if dirty {
                text.push_str("  •");
            }
            let mut label = RichText::new(text);
            if errors {
                label = label.color(ERROR);
            }
            let selected = self.search.is_empty() && self.page == Page::Section(section);
            if ui.selectable_label(selected, label).clicked() {
                self.page = Page::Section(section);
                self.search.clear();
            }
        }
        ui.add_space(6.0);
        ui.separator();
        ui.label(RichText::new("Arquivos").small().weak());
        for kind in [DocKind::Persona, DocKind::Triggers] {
            let dirty = self.docs.iter().any(|d| d.kind == kind && d.dirty());
            let text = format!("{}  {}{}", kind.icon(), kind.title(), if dirty { "  •" } else { "" });
            let selected = self.search.is_empty() && self.page == Page::Doc(kind);
            if ui.selectable_label(selected, text).clicked() {
                self.page = Page::Doc(kind);
                self.search.clear();
            }
        }
    }

    fn top_bar(&mut self, ui: &mut egui::Ui) {
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            ui.label(RichText::new("Painel da Akemi").size(20.0).strong().color(ACCENT));
            ui.add_space(12.0);
            ui.add(
                egui::TextEdit::singleline(&mut self.search)
                    .hint_text("🔍 procurar configuração…")
                    .desired_width(240.0),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let dirty = self.any_dirty();
                let errors = self.errors().len();
                let save = ui.add_enabled(
                    dirty,
                    egui::Button::new(RichText::new("💾 Salvar").strong().color(Color32::WHITE)).fill(ACCENT),
                );
                if save.on_hover_text("Ctrl+S").clicked() {
                    self.save();
                }
                if ui.add_enabled(dirty, egui::Button::new("Descartar")).clicked() {
                    self.discard();
                }
                let count = self.dirty_fields().len() + self.docs.iter().filter(|d| d.dirty()).count();
                if errors > 0 {
                    ui.label(RichText::new(format!("{errors} com erro")).color(ERROR));
                } else if count > 0 {
                    ui.label(
                        RichText::new(format!(
                            "{count} mudança{} sem salvar",
                            if count == 1 { "" } else { "s" }
                        ))
                        .color(ACCENT),
                    );
                }
            });
        });
        ui.horizontal(|ui| {
            let env = self.project.env_path();
            ui.label(RichText::new("Pasta da Akemi:").small().weak());
            ui.add(
                egui::TextEdit::singleline(&mut self.folder_input)
                    .desired_width(380.0)
                    .font(egui::TextStyle::Small),
            );
            if ui.small_button("Abrir").clicked() {
                let path = PathBuf::from(self.folder_input.trim());
                if path.is_dir() {
                    self.open_folder(path);
                } else {
                    self.notify("Essa pasta não existe.", ERROR);
                }
            }
            if HAS_PICKER && ui.small_button("Escolher…").clicked() {
                if let Some(path) = pick_dir(&self.project.root) {
                    self.open_folder(path);
                }
            }
            if ui
                .small_button("Recarregar")
                .on_hover_text("Lê de novo o .env e os arquivos do disco")
                .clicked()
            {
                let root = self.project.root.clone();
                self.open_folder(root);
            }
            let note = if self.project.env_exists {
                format!("editando {}", env.display())
            } else if self.project.from_example {
                "sem .env ainda: ao salvar, ele é criado a partir do .env.example".into()
            } else {
                "sem .env ainda: ao salvar, ele é criado".into()
            };
            ui.label(RichText::new(note).small().weak());
        });
        if crate::project::find_root(&self.project.root).is_none() {
            ui.label(
                RichText::new("⚠ Esta pasta não parece ser a da Akemi (não achei akemi/core/config.py).")
                    .small()
                    .color(WARN),
            );
        }
        ui.add_space(4.0);
    }
}

impl eframe::App for PainelApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if ctx.input_mut(|i| i.consume_key(egui::Modifiers::COMMAND, egui::Key::S)) && self.any_dirty() {
            self.save();
        }

        if ctx.input(|i| i.viewport().close_requested()) && !self.allow_close && self.any_dirty() {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.confirm_close = true;
        }

        egui::TopBottomPanel::top("topo").show(ctx, |ui| self.top_bar(ui));
        egui::SidePanel::left("menu")
            .resizable(false)
            .exact_width(220.0)
            .show(ctx, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| {
                    ui.with_layout(egui::Layout::top_down_justified(egui::Align::LEFT), |ui| {
                        ui.spacing_mut().button_padding = egui::vec2(10.0, 6.0);
                        self.sidebar(ui)
                    })
                });
            });
        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
                ui.add_space(8.0);
                if !self.search.trim().is_empty() {
                    self.search_page(ui);
                } else {
                    match self.page {
                        Page::Section(section) => self.section_page(ui, section),
                        Page::Doc(kind) => self.doc_editor(ui, kind, 32),
                    }
                }
                ui.add_space(40.0);
            });
        });

        if self.confirm_close {
            egui::Modal::new(egui::Id::new("fechar")).show(ctx, |ui| {
                ui.set_width(340.0);
                ui.heading("Tem mudanças sem salvar");
                ui.label("Quer salvar antes de fechar?");
                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    if ui.button(RichText::new("Salvar e fechar").strong()).clicked() {
                        self.save();
                        if !self.any_dirty() {
                            self.allow_close = true;
                            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                        }
                        self.confirm_close = false;
                    }
                    if ui.button("Fechar sem salvar").clicked() {
                        self.allow_close = true;
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                    if ui.button("Cancelar").clicked() {
                        self.confirm_close = false;
                    }
                });
            });
        }

        if let Some((text, color, at)) = &self.toast {
            let age = at.elapsed();
            if age > Duration::from_secs(5) {
                self.toast = None;
            } else {
                egui::Area::new(egui::Id::new("toast"))
                    .anchor(egui::Align2::CENTER_BOTTOM, [0.0, -20.0])
                    .show(ctx, |ui| {
                        egui::Frame::popup(ui.style())
                            .stroke(egui::Stroke::new(1.5_f32, *color))
                            .show(ui, |ui| {
                                ui.set_max_width(560.0);
                                ui.label(RichText::new(text.as_str()).color(*color).strong());
                            });
                    });
                ctx.request_repaint_after(Duration::from_millis(250));
            }
        }
    }
}
