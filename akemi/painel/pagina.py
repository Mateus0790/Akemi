"""A página do painel (um arquivo só: sem internet, sem bibliotecas de fora)."""

PAGE = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Painel da Akemi</title>
<style>
  :root {
    /* Paleta da Akemi: rosa #FC3B7F, roxo #5420A8, ciano #42D9E8, lilás #E9D5FF, creme #FFF1E6. */
    --bg: #fff1e6; --card: #ffffff; --text: #24103f; --muted: #6b5a85; --line: #e9d5ff;
    --accent: #fc3b7f; --on-accent: #fff1e6; --btn: #5420a8; --btn-text: #ffffff; --focus: #5420a8;
    --on: #2f9e6b; --warn: #b4540a; --sad: #4f6fa8;
    --shadow: 0 1px 2px rgba(20, 16, 40, .06), 0 4px 16px rgba(20, 16, 40, .05);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #160b2b; --card: #20123d; --text: #fff1e6; --muted: #b9a8d6; --line: #3a2563;
      --btn: #fc3b7f; --btn-text: #1d0b3a; --focus: #42d9e8;
      --on: #4cc58d; --warn: #f0a050; --sad: #8aa7e0;
      --shadow: none;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  main { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
  header { display: flex; align-items: center; gap: 14px; margin-bottom: 20px; }
  .face {
    width: 52px; height: 52px; border-radius: 50%; flex: none; display: grid; place-items: center;
    background: var(--accent); color: var(--on-accent); font-size: 26px; transition: background .3s, color .3s;
  }
  .face.triste { background: color-mix(in srgb, var(--sad) 18%, transparent); color: var(--sad); }
  h1 { font-size: 22px; margin: 0; }
  .sub { color: var(--muted); margin: 2px 0 0; }
  section {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 6px 18px; margin: 16px 0; box-shadow: var(--shadow);
  }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 14px 0 4px; }
  .row { display: flex; gap: 14px; align-items: center; padding: 14px 0; border-top: 1px solid var(--line); }
  h2 + .row { border-top: 0; }
  .row .txt { flex: 1; min-width: 0; }
  .row b { display: block; font-weight: 600; }
  .row p { margin: 2px 0 0; color: var(--muted); font-size: 14px; }
  .tag { display: inline-block; font-size: 12px; color: var(--muted); border: 1px solid var(--line);
         border-radius: 99px; padding: 0 8px; margin-top: 6px; }
  .tag.later { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, transparent); }
  .switch { position: relative; width: 48px; height: 28px; flex: none; }
  .switch input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; z-index: 1; }
  .switch span { position: absolute; inset: 0; border-radius: 99px; background: var(--line); transition: background .2s; }
  .switch span::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 22px; height: 22px; border-radius: 50%;
    background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.25); transition: transform .2s;
  }
  .switch input:checked + span { background: var(--on); }
  .switch input:checked + span::after { transform: translateX(20px); }
  .switch input:focus-visible + span { outline: 2px solid var(--focus); outline-offset: 2px; }
  input[type=text], input[type=number] {
    width: 140px; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line);
    background: var(--bg); color: var(--text); font: inherit;
  }
  input[type=text] { width: 180px; }
  button {
    font: inherit; font-weight: 600; border: 0; border-radius: 10px; padding: 10px 16px;
    background: var(--btn); color: var(--btn-text); cursor: pointer;
  }
  button:disabled { opacity: .5; cursor: default; }
  .actions { display: flex; justify-content: flex-end; padding: 12px 0 14px; border-top: 1px solid var(--line); }
  .status { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 4px 16px; padding: 12px 0 14px; }
  .status div { color: var(--muted); font-size: 14px; }
  .status strong { color: var(--text); font-weight: 600; }
  #msg { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); max-width: calc(100% - 32px);
         background: var(--text); color: var(--bg); padding: 10px 16px; border-radius: 10px; opacity: 0;
         transition: opacity .2s; pointer-events: none; }
  #msg.show { opacity: 1; }
  #login { display: none; }
  .err { color: var(--warn); }
  @media (max-width: 520px) {
    .row { flex-wrap: wrap; }
    .row input[type=text], .row input[type=number] { width: 100%; }
  }
</style>
</head>
<body>
<main>
  <header>
    <div class="face" id="face" aria-hidden="true">✿</div>
    <div>
      <h1>Painel da Akemi</h1>
      <p class="sub" id="sub">Carregando…</p>
    </div>
  </header>

  <section id="login">
    <h2>Entrar</h2>
    <div class="row">
      <div class="txt"><b>Token do painel</b><p>Use o link que o console mostra com <code>/painel</code>.
        No Docker, é o <code>AKEMI_TOKEN</code>.</p></div>
      <input type="text" id="tok" autocomplete="off" aria-label="Token do painel">
    </div>
    <div class="actions"><button id="enter">Entrar</button></div>
  </section>

  <div id="app" hidden>
    <section>
      <h2>Como ela está</h2>
      <div class="status" id="status"></div>
    </section>
    <section>
      <h2>Funções</h2>
      <div id="features"></div>
    </section>
    <section>
      <h2>Ajustes</h2>
      <div id="values"></div>
      <div class="actions"><button id="saveValues">Salvar ajustes</button></div>
    </section>
  </div>
</main>
<div id="msg" role="status" aria-live="polite"></div>
<script>
(() => {
  const $ = (id) => document.getElementById(id);
  let token = "";
  try {
    const m = location.hash.match(/t=([^&]+)/);
    if (m) { token = decodeURIComponent(m[1]); sessionStorage.setItem("akemi-painel", token); }
    else { token = sessionStorage.getItem("akemi-painel") || ""; }
    if (m) history.replaceState(null, "", location.pathname);  // tira o token da barra
  } catch (e) { /* sem storage: segue só com o hash */ }

  function toast(text) {
    const el = $("msg"); el.textContent = text; el.classList.add("show");
    clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 4200);
  }

  async function api(path, body) {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: Object.assign({"X-Akemi-Token": token}, body ? {"Content-Type": "application/json"} : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { showLogin("Token errado."); throw new Error("token"); }
    if (!res.ok) throw new Error(data.erro || ("erro " + res.status));
    return data;
  }

  function showLogin(why) {
    $("app").hidden = true; $("login").style.display = "block";
    $("sub").textContent = why || "Precisa do token para entrar.";
  }

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "text") e.textContent = v; else if (k === "class") e.className = v; else e.setAttribute(k, v);
    }
    for (const c of children || []) e.append(c);
    return e;
  }

  function when(live) {
    return el("span", {class: "tag" + (live ? "" : " later"), text: live ? "muda na hora" : "quando ela religar"});
  }

  function render(s) {
    $("login").style.display = "none"; $("app").hidden = false;
    const sad = s.humor === "triste";
    $("face").textContent = sad ? "︶" : "✿";
    $("face").classList.toggle("triste", sad);
    $("sub").textContent = sad && s.desligadas.length
      ? `${s.nome} está triste: desligaram algo dela.` : `${s.nome} está ${s.humor}.`;

    const st = $("status"); st.replaceChildren();
    const item = (label, value) => st.append(el("div", {}, [label + ": ", el("strong", {text: value})]));
    item("Humor", s.humor);
    item("Código", s.codigo);
    item("Desligadas", s.desligadas.length ? s.desligadas.join(", ") : "nenhuma");
    if (s.reiniciar.length) item("Esperando ela religar", s.reiniciar.join(", "));

    const fs = $("features"); fs.replaceChildren();
    for (const f of s.funcoes) {
      const input = el("input", {type: "checkbox", role: "switch", "aria-label": f.nome});
      input.checked = f.ligada;
      input.addEventListener("change", () => save({[f.chave]: input.checked}, input));
      fs.append(el("div", {class: "row"}, [
        el("div", {class: "txt"}, [el("b", {text: f.nome}), el("p", {text: f.descricao}), when(f.na_hora)]),
        el("label", {class: "switch"}, [input, el("span")]),
      ]));
    }

    const vs = $("values"); vs.replaceChildren();
    for (const v of s.ajustes) {
      const input = el("input", v.tipo === "texto"
        ? {type: "text", maxlength: String(v.max_letras)}
        : {type: "number", min: String(v.minimo), max: String(v.maximo), step: "any"});
      input.value = v.valor; input.dataset.key = v.chave; input.setAttribute("aria-label", v.nome);
      vs.append(el("div", {class: "row"}, [el("div", {class: "txt"}, [el("b", {text: v.nome}), when(v.na_hora)]), input]));
    }
  }

  async function save(changes, control) {
    if (control) control.disabled = true;
    try {
      const r = await api("/api/salvar", {mudancas: changes});
      render(r.estado);
      toast(r.aviso || "Salvo.");
    } catch (e) {
      if (e.message !== "token") { toast("Não deu: " + e.message); await load(); }
    } finally { if (control) control.disabled = false; }
  }

  async function load() {
    if (!token) return showLogin();
    try { render(await api("/api/estado")); }
    catch (e) { if (e.message !== "token") $("sub").innerHTML = '<span class="err">Não consegui falar com ela.</span>'; }
  }

  $("saveValues").addEventListener("click", () => {
    const changes = {};
    for (const input of $("values").querySelectorAll("input")) changes[input.dataset.key] = input.value;
    save(changes, $("saveValues"));
  });
  $("enter").addEventListener("click", () => {
    token = $("tok").value.trim();
    try { sessionStorage.setItem("akemi-painel", token); } catch (e) {}
    load();
  });
  load();
  setInterval(() => {
    if ($("app").hidden || $("values").contains(document.activeElement)) return;  // não atropela quem digita
    api("/api/estado").then(render).catch(() => {});
  }, 15000);
})();
</script>
</body>
</html>
"""
