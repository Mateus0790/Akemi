"""Regras de gatilho para comentários proativos (ver ``triggers.yaml``)."""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from akemi.safety.moderation import normalize

DEFAULT_TRIGGERS_PATH = Path(__file__).with_name("triggers.yaml")


@dataclass
class TriggerRule:
    id: str
    when: str
    keywords: list[str] = field(default_factory=list)
    cooldown_s: float = 300.0
    hint: str = ""
    last_fired: float = 0.0

    def matches(self, kind: str, text: str, now: float) -> bool:
        if not fnmatch.fnmatchcase(kind, self.when):
            return False
        if now - self.last_fired < self.cooldown_s:
            return False
        if not self.keywords:
            return bool(text.strip())
        norm = normalize(text)
        return any(normalize(k) in norm for k in self.keywords)


class TriggerSet:
    def __init__(self, rules: list[TriggerRule]) -> None:
        self.rules = rules

    @classmethod
    def load(cls, path: Path | None = None) -> "TriggerSet":
        p = Path(path) if path else DEFAULT_TRIGGERS_PATH
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
        rules = [
            TriggerRule(
                id=r["id"],
                when=r.get("when", "*"),
                keywords=[str(k) for k in r.get("keywords", []) or []],
                cooldown_s=float(r.get("cooldown_s", 300)),
                hint=r.get("hint", ""),
            )
            for r in raw
            if isinstance(r, dict) and "id" in r
        ]
        return cls(rules)

    def match(self, kind: str, text: str, now: float | None = None) -> TriggerRule | None:
        """Primeira regra que casa (a ordem do YAML é a prioridade)."""
        now = now or time.time()
        for rule in self.rules:
            if rule.matches(kind, text, now):
                return rule
        return None

    @staticmethod
    def fire(rule: TriggerRule, now: float | None = None) -> None:
        rule.last_fired = now or time.time()
