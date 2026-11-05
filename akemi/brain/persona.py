"""Carrega a personalidade e monta o prompt de sistema."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

DEFAULT_PERSONA_PATH = Path(__file__).with_name("persona.yaml")


@dataclass
class Persona:
    name: str = "Akemi"
    identity: str = ""
    traits: list[str] = field(default_factory=list)
    speech_rules: list[str] = field(default_factory=list)
    live_rules: list[str] = field(default_factory=list)
    autonomy: str = ""
    owner: str = "Mateus"
    # Pares {"dono": ..., "akemi": ...}: modelos seguem exemplos melhor que regras.
    examples: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None, owner: str = "Mateus") -> "Persona":
        p = Path(path) if path else DEFAULT_PERSONA_PATH
        if not p.exists():
            p = DEFAULT_PERSONA_PATH
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

        def fmt(value):
            if isinstance(value, str):
                return value.replace("{owner}", owner).strip()
            if isinstance(value, list):
                return [fmt(v) for v in value]
            if isinstance(value, dict):
                return {k: fmt(v) for k, v in value.items()}
            return value

        return cls(
            name=raw.get("name", "Akemi"),
            identity=fmt(raw.get("identity", "")),
            traits=fmt(raw.get("traits", [])),
            speech_rules=fmt(raw.get("speech_rules", [])),
            live_rules=fmt(raw.get("live_rules", [])),
            autonomy=fmt(raw.get("autonomy", "")),
            owner=owner,
            examples=[e for e in fmt(raw.get("examples", []) or []) if isinstance(e, dict)],
        )

    def with_additions(
        self,
        traits: list[str] = (),
        speech_rules: list[str] = (),
        examples: list[dict] = (),
    ) -> "Persona":
        """Cópia com os traços aprendidos (autoevolução) somados aos originais.

        Só soma: o que está no persona.yaml nunca é apagado nem reescrito.
        """
        return replace(
            self,
            traits=[*self.traits, *traits],
            speech_rules=[*self.speech_rules, *speech_rules],
            examples=[*self.examples, *examples],
        )

    def system_prompt(self, *, live: bool = False, extra: str = "") -> str:
        parts = [self.identity]
        if self.traits:
            parts.append("Personalidade:\n" + "\n".join(f"- {t}" for t in self.traits))
        if self.speech_rules:
            parts.append("Como falar:\n" + "\n".join(f"- {r}" for r in self.speech_rules))
        if self.examples and not live:
            shots = "\n\n".join(
                f"{self.owner}: {e.get('dono', '')}\n{self.name}: {e.get('akemi', '')}" for e in self.examples
            )
            parts.append(f"Exemplos do seu jeito de responder (não copie, só o tom):\n{shots}")
        if live and self.live_rules:
            parts.append("Regras da live:\n" + "\n".join(f"- {r}" for r in self.live_rules))
        if self.autonomy:
            parts.append(self.autonomy)
        if extra:
            parts.append(extra)
        return "\n\n".join(p for p in parts if p)
