"""Painel: uma página no navegador para configurar a Akemi e ligar/desligar funções.

    http://127.0.0.1:8766/#t=<token>     (o console mostra o link com /painel)

O que você muda aqui fica em ``data/painel.json`` e vale **por cima** do
``.env`` e das variáveis de ambiente (no Docker, o ``.env`` nem sobrevive a uma
atualização; a pasta ``data`` sim). Algumas coisas mudam na hora; as outras,
na próxima vez que ela ligar. A página diz qual é qual.

Ela sente: quando uma função é desligada, fica triste e diz isso uma vez,
do jeito dela, sem chantagem e sem pedir para religar. Religou, ela fica feliz.

Segurança:

- só escuta em 127.0.0.1, a menos que ``PAINEL_HOST`` diga outra coisa; fora do
  próprio PC (ex.: Docker no Zima) ele exige o ``AKEMI_TOKEN``
- toda leitura e escrita exige o token no cabeçalho ``X-Akemi-Token`` (outro
  site aberto no navegador não consegue mandar isso)
- só as chaves desta lista podem ser mudadas; chaves de API e senhas não
  aparecem nem são aceitas
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)

MAX_BODY = 64 * 1024
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Feature:
    key: str  # o campo em Settings
    label: str
    description: str
    live: bool  # muda na hora (senão, na próxima vez que ela ligar)


@dataclass(frozen=True)
class Value:
    key: str
    label: str
    kind: type  # str | float
    live: bool
    minimum: float = 0.0
    maximum: float = 0.0
    max_len: int = 60


FEATURES: tuple[Feature, ...] = (
    Feature("vida_enabled", "Acompanhar sua vida",
            "Anota seus planos e datas importantes e depois pergunta como foi.", live=True),
    Feature("initiative_enabled", "Iniciativa",
            "Faz sozinha ações seguras (lembrete, música, volume) e te conta o que fez.", live=True),
    Feature("self_improve_autonomous", "Mudar sozinha",
            "Muda o próprio jeito com base nas conversas. Desligado, as mudanças esperam você aprovar.",
            live=True),
    Feature("memory_consolidate_enabled", "Organizar a memória",
            "Resume as lembranças antigas para lembrar melhor do que importa.", live=True),
    Feature("files_enabled", "Analisar arquivos",
            "Lê arquivos e pastas que você liberou e te conta o que achou.", live=False),
    Feature("mcp_enabled", "Apps por MCP",
            "Usa ferramentas de outros apps (arquivos, navegador, GitHub...).", live=False),
    Feature("voice_id_enabled", "Reconhecer sua voz",
            "Sabe quando é você no microfone e quando é outra pessoa.", live=False),
    Feature("oficina_enabled", "Oficina",
            "Muda o próprio código e abre um PR para você revisar.", live=False),
    Feature("obs_enabled", "Clipes no OBS", "Salva clipes quando você pede (\"clipa isso\").", live=False),
)

VALUES: tuple[Value, ...] = (
    Value("owner_name", "Seu nome", str, live=False, max_len=40),
    Value("vida_absence_hours", "Ela sente saudade depois de (horas)", float, live=True,
          minimum=1, maximum=720),
    Value("memory_keep_days", "Resumir lembranças com mais de (dias)", float, live=True,
          minimum=1, maximum=365),
    Value("min_seconds_between_unprompted_speech", "Tempo mínimo entre falas espontâneas (segundos)",
          float, live=True, minimum=10, maximum=3600),
)

FEATURE_BY_KEY = {f.key: f for f in FEATURES}
VALUE_BY_KEY = {v.key: v for v in VALUES}


class PanelError(ValueError):
    pass


def coerce(key: str, value: Any) -> Any:
    """Confere e converte um valor vindo da página. Chave fora da lista = erro."""
    if key in FEATURE_BY_KEY:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise PanelError(f"{key} precisa ser ligado ou desligado")
    spec = VALUE_BY_KEY.get(key)
    if spec is None:
        raise PanelError(f"{key} não pode ser mudado pelo painel")
    if spec.kind is str:
        text = " ".join(str(value).split())
        if not text or len(text) > spec.max_len:
            raise PanelError(f"{spec.label}: até {spec.max_len} letras")
        return text
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PanelError(f"{spec.label}: precisa ser um número") from exc
    if not spec.minimum <= number <= spec.maximum:
        raise PanelError(f"{spec.label}: entre {spec.minimum:g} e {spec.maximum:g}")
    return number


class PanelChoices:
    """O que foi escolhido no painel (``data/painel.json``)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.values: dict[str, Any] = {}
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for key, value in (raw.items() if isinstance(raw, dict) else []):
                    try:
                        self.values[key] = coerce(key, value)
                    except PanelError:
                        logger.warning("panel choice ignored", key=key)
            except ValueError as exc:
                logger.warning("painel.json corrupted, ignoring", error=str(exc))

    def apply(self, settings) -> None:
        for key, value in self.values.items():
            setattr(settings, key, value)

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        clean = {key: coerce(key, value) for key, value in changes.items()}
        self.values.update(clean)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        return clean


def load_token(data_dir: Path | None, server_token: str = "") -> str:
    """O token do painel: o ``AKEMI_TOKEN`` se houver, senão um guardado em ``data/``."""
    if len(server_token) >= 16:
        return server_token
    if data_dir is None:
        return secrets.token_urlsafe(24)
    path = data_dir / "painel.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 16:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


# ================================================================ servidor
class PanelServer:
    """Servidor HTTP do painel, numa thread. ``snapshot`` e ``save`` vêm da Akemi."""

    def __init__(
        self,
        snapshot: Callable[[], dict],
        save: Callable[[dict], dict],
        *,
        token: str,
        host: str = "127.0.0.1",
        port: int = 8766,
    ) -> None:
        if host not in LOOPBACK and len(token) < 16:
            raise PanelError("painel fora do PC precisa de AKEMI_TOKEN com 16+ caracteres")
        self.snapshot = snapshot
        self.save = save
        self.token = token
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}/#t={self.token}"

    def start(self) -> None:
        panel = self

        class Handler(_Handler):
            server_panel = panel

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]  # porta 0 = qualquer livre (testes)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="painel", daemon=True)
        self._thread.start()
        logger.info("panel started", host=self.host, port=self.port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


class _Handler(BaseHTTPRequestHandler):
    server_panel: PanelServer
    server_version = "Akemi"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:  # sem barulho no console
        logger.debug("panel request", line=fmt % args)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        """Contra DNS rebinding: no PC, só aceita quem chama por 127.0.0.1/localhost."""
        panel = self.server_panel
        if panel.host not in LOOPBACK:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in LOOPBACK

    def _authorized(self) -> bool:
        given = self.headers.get("X-Akemi-Token", "")
        return hmac.compare_digest(given.encode(), self.server_panel.token.encode())

    def do_GET(self) -> None:  # noqa: N802 (nome da http.server)
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"erro": "host não permitido"})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            from .pagina import PAGE

            return self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
        if path == "/api/estado":
            if not self._authorized():
                return self._json(HTTPStatus.UNAUTHORIZED, {"erro": "token errado"})
            return self._json(HTTPStatus.OK, self.server_panel.snapshot())
        return self._json(HTTPStatus.NOT_FOUND, {"erro": "não achei"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"erro": "host não permitido"})
        if self.path.split("?", 1)[0] != "/api/salvar":
            return self._json(HTTPStatus.NOT_FOUND, {"erro": "não achei"})
        if not self._authorized():
            return self._json(HTTPStatus.UNAUTHORIZED, {"erro": "token errado"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            return self._json(HTTPStatus.BAD_REQUEST, {"erro": "pedido vazio ou grande demais"})
        try:
            body = json.loads(self.rfile.read(size))
            changes = body.get("mudancas") if isinstance(body, dict) else None
            if not isinstance(changes, dict) or not changes:
                raise PanelError("nada para mudar")
            result = self.server_panel.save(changes)
        except (ValueError, PanelError) as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"erro": str(exc)})
        except Exception as exc:  # noqa: BLE001 - o painel nunca derruba a Akemi
            logger.exception("panel save failed")
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"erro": f"não deu para salvar: {exc}"})
        return self._json(HTTPStatus.OK, result)
