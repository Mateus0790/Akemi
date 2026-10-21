"""Carrega a personalidade e monta o prompt de sistema."""

from __future__ import annotations

from dataclasses import dataclass, field
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
            return value

        return cls(
            name=raw.get("name", "Akemi"),
            identity=fmt(raw.get("identity", "")),
            traits=fmt(raw.get("traits", [])),
            speech_rules=fmt(raw.get("speech_rules", [])),
            live_rules=fmt(raw.get("live_rules", [])),
            autonomy=fmt(raw.get("autonomy", "")),
        )

    def system_prompt(self, *, live: bool = False, extra: str = "") -> str:
        parts = [self.identity]
        if self.traits:
            parts.append("Personalidade:\n" + "\n".join(f"- {t}" for t in self.traits))
        if self.speech_rules:
            parts.append("Como falar:\n" + "\n".join(f"- {r}" for r in self.speech_rules))
        if live and self.live_rules:
            parts.append("Regras da live:\n" + "\n".join(f"- {r}" for r in self.live_rules))
        if self.autonomy:
            parts.append(self.autonomy)
        if extra:
            parts.append(extra)
        return "\n\n".join(p for p in parts if p)
