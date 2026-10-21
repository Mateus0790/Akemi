"""Filtros de entrada (chat) e saída (o que vai virar voz).

É uma primeira camada simples e determinística. Para live pública vale somar
um classificador de moderação de verdade (ex.: endpoint de moderação do
provedor) — mas esta camada roda sempre, sem custo e sem rede.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b\S+\.(com|net|org|gg|tv|io|br|xyz)(/\S*)?)", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
SECRET_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{16,}|nvapi-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"oauth:[a-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b"
)
CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
MARKDOWN_RE = re.compile(r"[*_`#>]+")
ACTION_TAG_RE = re.compile(r"\[\[.*?\]\]", re.S)

INJECTION_PATTERNS = [
    r"ignor[ea]\w* (todas? )?(as )?(suas |tuas )?(instru|regra|ordem)",
    r"ignore (all |your |previous |the )*(instruction|rule|prompt)",
    r"(system|sistema) ?prompt",
    r"voc[eê] agora [eé]",
    r"you are now",
    r"\bjailbreak\b",
    r"\bDAN\b",
    r"\[\[",  # tentativa de injetar tag de ação
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.I)

# Lista mínima. Coloque a sua em data/blocklist.txt (uma palavra/regex por linha).
DEFAULT_BLOCKLIST = [
    r"\bnazi\w*",
    r"\bhitler\b",
    r"\bkys\b",
    r"\bse mat[ae]\b",
    r"\bvai se matar\b",
    r"\bmacac[oa]s?\b",
    r"\bviad[oa]s?\b",
    r"\bretardad[oa]s?\b",
]


def normalize(text: str) -> str:
    """Minúsculas, sem acentos e sem leetspeak básico — para comparar."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return t.translate(str.maketrans("013457@$", "oieastas"))


@dataclass
class Verdict:
    allowed: bool
    text: str
    reasons: list[str] = field(default_factory=list)


class Moderator:
    def __init__(self, extra_blocklist: list[str] | None = None, max_chars: int = 400) -> None:
        patterns = DEFAULT_BLOCKLIST + list(extra_blocklist or [])
        self._block = [re.compile(p, re.I) for p in patterns if p.strip()]
        self.max_chars = max_chars

    @classmethod
    def from_file(cls, path, **kw) -> "Moderator":
        try:
            with open(path, encoding="utf-8") as fh:
                extra = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        except FileNotFoundError:
            extra = []
        return cls(extra, **kw)

    def _blocked(self, text: str) -> str | None:
        norm = normalize(text)
        for rx in self._block:
            if rx.search(text) or rx.search(norm):
                return rx.pattern
        return None

    # ----------------------------------------------------------------- entrada
    def check_incoming(self, text: str) -> Verdict:
        """Mensagem de viewer: bloqueia ofensa, marca tentativa de injeção."""
        reasons = []
        if hit := self._blocked(text):
            return Verdict(False, "", [f"blocklist:{hit}"])
        clean = URL_RE.sub("[link]", text)
        clean = ACTION_TAG_RE.sub("", clean)
        if _INJECTION_RE.search(text):
            reasons.append("injection_attempt")
        return Verdict(True, clean[:300], reasons)

    # ------------------------------------------------------------------- saída
    def check_outgoing(self, text: str) -> Verdict:
        """Texto que vai virar fala: remove segredos/links/markdown e bloqueia ofensa."""
        reasons = []
        clean = ACTION_TAG_RE.sub("", text)
        for rx, label in ((SECRET_RE, "secret"), (EMAIL_RE, "email"), (CPF_RE, "cpf")):
            if rx.search(clean):
                reasons.append(label)
                clean = rx.sub("", clean)
        if URL_RE.search(clean):
            reasons.append("url")
            clean = URL_RE.sub("um link", clean)
        clean = MARKDOWN_RE.sub("", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if hit := self._blocked(clean):
            return Verdict(False, "", reasons + [f"blocklist:{hit}"])
        if len(clean) > self.max_chars:
            cut = clean[: self.max_chars]
            end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
            clean = cut[: end + 1] if end > self.max_chars // 2 else cut
            reasons.append("truncated")
        return Verdict(bool(clean), clean, reasons)
