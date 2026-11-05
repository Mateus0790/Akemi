"""Reconhecer o dono pela voz.

Até aqui, tudo o que chegava pelo microfone com "Akemi, ..." era tratado como
o dono. Com a voz cadastrada (``akemi --cadastrar-voz``), cada frase é
conferida antes:

- **dono**: a voz bate com a cadastrada. Tudo como antes (ações, memória...).
- **outro**: claramente outra pessoa. Ela conversa como com uma visita, mas
  não faz ações, não guarda fatos e não conta coisas pessoais do dono.
- **incerto**: no meio do caminho (barulho, frase curta, voz rouca). Também
  sem ações; ela pede para repetir ou confirmar pelo terminal ou pelo app.

Na dúvida, ela fica do lado seguro: errar recusando o dono é chato, errar
aceitando outra pessoa é perigoso.

Como funciona: um modelo pequeno (roda offline, ~26 MB) transforma cada frase
numa "impressão" da voz (um vetor). No cadastro guardamos só a média das suas
impressões e os limites, nunca o áudio. Os limites saem do próprio cadastro:
o quanto a sua voz varia no seu microfone diz o quanto parecido precisa ser.

Limites honestos: não é segurança de banco. Uma gravação da sua voz ou uma
voz muito parecida pode passar. Serve para "alguém do meu lado não manda
nela", e o terminal e o app (com o AKEMI_TOKEN) continuam sendo o caminho
seguro.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import structlog

logger = structlog.get_logger()

SAMPLE_RATE = 16000
VOICE_FILE = "voz.json"
FORMAT_VERSION = 1
MIN_ENROLL_SAMPLES = 4
MIN_ENROLL_SECONDS = 1.5
MAX_DROPPED = 2  # mais que isso, é melhor recusar e pedir para gravar de novo
OUTLIER_GAP = 0.15  # o quanto uma amostra pode ficar abaixo da mediana
MAX_DIM = 4096

Who = Literal["dono", "outro", "incerto"]
Embedder = Callable[[np.ndarray], np.ndarray]  # float32 16 kHz mono -> vetor


class VoiceError(ValueError):
    pass


def pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / SAMPLE_RATE


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    if not math.isfinite(n) or n == 0:
        raise VoiceError("impressão de voz vazia")
    return v / n


@dataclass
class VoicePrint:
    centroid: np.ndarray
    accept: float  # daqui para cima: é o dono
    reject: float  # daqui para baixo: é outra pessoa (entre os dois: incerto)
    samples: int
    model: str = ""
    created_at: float = field(default_factory=time.time)

    def score(self, embedding: np.ndarray) -> float:
        return float(_unit(embedding) @ self.centroid)

    # ---------------------------------------------------------- arquivo
    def to_dict(self) -> dict:
        return {
            "versao": FORMAT_VERSION,
            "modelo": self.model,
            "amostras": self.samples,
            "aceitar": round(self.accept, 4),
            "recusar": round(self.reject, 4),
            "criado_em": self.created_at,
            "centro": [round(float(x), 6) for x in self.centroid],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VoicePrint":
        if not isinstance(d, dict) or d.get("versao") != FORMAT_VERSION:
            raise VoiceError("arquivo de voz em formato desconhecido")
        centro = d.get("centro")
        if not isinstance(centro, list) or not 8 <= len(centro) <= MAX_DIM:
            raise VoiceError("impressão de voz inválida")
        try:
            vec = np.array([float(x) for x in centro], dtype=np.float32)
            accept, reject = float(d["aceitar"]), float(d["recusar"])
            samples = int(d.get("amostras", 0))
            created = float(d.get("criado_em", 0) or 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise VoiceError(f"arquivo de voz inválido: {exc}") from exc
        if not np.all(np.isfinite(vec)):
            raise VoiceError("impressão de voz inválida")
        if not (0.0 < reject <= accept < 1.0):
            raise VoiceError("limites de voz inválidos")
        return cls(_unit(vec), accept, reject, samples, str(d.get("modelo", "")), created)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "VoicePrint | None":
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            raise VoiceError(f"não consegui ler {path}: {exc}") from exc


def _leave_one_out(vecs: np.ndarray) -> list[float]:
    """O quanto cada amostra parece com a média das outras."""
    return [float(vecs[i] @ _unit(np.delete(vecs, i, axis=0).mean(axis=0))) for i in range(len(vecs))]


def build_print(embeddings: list[np.ndarray], model: str = "", *, margin: float = 0.03, band: float = 0.15) -> VoicePrint:
    """Monta a impressão do dono e calibra os limites com as próprias amostras.

    Para cada amostra, mede o quanto ela parece com a média das outras: é o
    "quanto a voz dele varia". O limite para aceitar fica um pouco abaixo da
    pior dessas notas; abaixo de ``aceitar - band`` é outra pessoa.
    """
    if len(embeddings) < MIN_ENROLL_SAMPLES:
        raise VoiceError(f"preciso de pelo menos {MIN_ENROLL_SAMPLES} frases")
    vecs = np.stack([_unit(e) for e in embeddings])
    loo = _leave_one_out(vecs)
    # Barulho que passou por fala estraga o limite: ele vem da pior amostra.
    bad = [i for i, score in enumerate(loo) if score < float(np.median(loo)) - OUTLIER_GAP]
    if 0 < len(bad) <= MAX_DROPPED and len(vecs) - len(bad) >= MIN_ENROLL_SAMPLES:
        logger.info("voice samples dropped", dropped=len(bad), scores=[round(loo[i], 3) for i in bad])
        vecs = np.delete(vecs, bad, axis=0)
        loo = _leave_one_out(vecs)
    centroid = _unit(vecs.mean(axis=0))
    if float(np.mean(loo)) < 0.5:
        raise VoiceError(
            "as frases não parecem da mesma voz (barulho ou mais de uma pessoa falando). Tente de novo em silêncio."
        )
    accept = float(np.clip(min(loo) - margin, 0.5, 0.95))
    reject = float(np.clip(accept - band, 0.3, accept))
    logger.info("voice enrolled", samples=len(vecs), accept=round(accept, 3), reject=round(reject, 3))
    return VoicePrint(centroid, accept, reject, len(vecs), model)


@dataclass
class Verdict:
    who: Who
    score: float | None = None
    reason: str = ""


class VoiceID:
    """Confere cada frase do microfone contra a voz cadastrada."""

    def __init__(
        self,
        embed: Embedder | None,
        voiceprint: VoicePrint,
        *,
        min_seconds: float = 1.0,
        inherit_s: float = 20.0,
    ) -> None:
        self.embed = embed
        self.voiceprint = voiceprint
        self.min_seconds = min_seconds
        self.inherit_s = inherit_s
        self.last_owner_at = 0.0
        self.counts: dict[str, int] = {"dono": 0, "outro": 0, "incerto": 0}
        self.last: Verdict | None = None

    @property
    def ready(self) -> bool:
        return self.embed is not None

    def verify(self, pcm: bytes, now: float | None = None) -> Verdict:
        now = now or time.time()
        verdict = self._verify(pcm, now)
        if verdict.who == "dono":
            self.last_owner_at = now
        self.counts[verdict.who] += 1
        self.last = verdict
        logger.info("voice checked", who=verdict.who, score=verdict.score, reason=verdict.reason)
        return verdict

    def _verify(self, pcm: bytes, now: float) -> Verdict:
        if self.embed is None:
            # Cadastro existe mas o modelo não carregou: na dúvida, ninguém é o dono.
            return Verdict("incerto", None, "modelo de voz indisponível")
        if seconds(pcm) < self.min_seconds:
            # "Sim", "pode": curto demais para conferir. Vale se é a continuação
            # de uma conversa que acabou de ser conferida como sua.
            if now - self.last_owner_at <= self.inherit_s:
                return Verdict("dono", None, "continuação")
            return Verdict("incerto", None, "frase curta demais para reconhecer")
        try:
            score = self.voiceprint.score(self.embed(pcm_to_float(pcm)))
        except Exception as exc:
            logger.warning("voice check failed", error=str(exc))
            return Verdict("incerto", None, "erro ao conferir a voz")
        vp = self.voiceprint
        if score >= vp.accept:
            return Verdict("dono", score, "voz confere")
        if score < vp.reject:
            return Verdict("outro", score, "voz diferente")
        return Verdict("incerto", score, "voz parecida, mas sem certeza")

    def describe(self) -> str:
        vp = self.voiceprint
        when = time.strftime("%d/%m/%Y", time.localtime(vp.created_at)) if vp.created_at else "?"
        lines = [
            f"  voz cadastrada em {when} ({vp.samples} frases)",
            f"  limites: dono a partir de {vp.accept:.2f}, outra pessoa abaixo de {vp.reject:.2f}",
            f"  modelo: {'ok' if self.ready else 'NÃO CARREGOU (ninguém passa como você pelo microfone)'}",
            "  nesta sessão: " + ", ".join(f"{k} {v}" for k, v in self.counts.items()),
        ]
        if self.last is not None and self.last.score is not None:
            lines.append(f"  última frase: {self.last.who} (nota {self.last.score:.2f})")
        return "\n".join(lines)


class SherpaEmbedder:
    """Impressão de voz com o sherpa-onnx (offline, CPU). ``pip install -e ".[voz]"``."""

    def __init__(self, model_path: Path, threads: int = 1) -> None:
        model_path = Path(model_path)
        if not model_path.exists():
            raise VoiceError(f"modelo de voz não encontrado: {model_path} (veja docs/VOZ.md)")
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise VoiceError('falta o sherpa-onnx: pip install -e ".[voz]"') from exc
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=threads)
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.name = model_path.stem

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.asarray(audio, dtype=np.float32))
        stream.input_finished()
        return np.asarray(self._extractor.compute(stream), dtype=np.float32)


def load_voice_id(
    data_dir: Path,
    model_path: Path,
    *,
    inherit_s: float = 20.0,
    on_problem: Callable[[str], None] | None = None,
) -> VoiceID | None:
    """VoiceID se a voz estiver cadastrada; None se não (o microfone vale como hoje)."""
    try:
        vp = VoicePrint.load(Path(data_dir) / VOICE_FILE)
    except VoiceError as exc:
        # Arquivo estragado: melhor recusar do que liberar geral.
        logger.error("voice print unreadable", error=str(exc))
        if on_problem:
            on_problem(str(exc))
        return VoiceID(None, VoicePrint(_unit(np.ones(8)), 0.99, 0.98, 0), inherit_s=inherit_s)
    if vp is None:
        return None
    try:
        embed: Embedder | None = SherpaEmbedder(model_path)
    except Exception as exc:
        logger.error("voice model unavailable", error=str(exc))
        if on_problem:
            on_problem(str(exc))
        embed = None
    return VoiceID(embed, vp, inherit_s=inherit_s)
