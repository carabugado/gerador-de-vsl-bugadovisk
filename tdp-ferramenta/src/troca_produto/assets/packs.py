"""Casar a QUANTIDADE FALADA com o pack certo do produto novo.

"6 frascos", "free +3", "buy 3 get 1 free", "3 1" — cada um pede uma arte
diferente. Sem isso, a oferta mostra 1 frasco enquanto o locutor vende 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..pipeline.text_detect import _fuzzy_score, normalize

NUMBER_WORDS = {
    # inglês
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    # português
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4,
    "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "doze": 12,
}

_INT_RE = re.compile(r"\b(\d{1,2})\b")


@dataclass
class Quantity:
    numbers: list[int]
    primary: int = 0
    bonus: int = 0

    @property
    def total(self) -> int:
        return self.primary + self.bonus


def parse_quantity(text: str) -> Quantity:
    """Extrai as quantidades faladas.

    "6 frascos"              → primary 6
    "buy 3 get 1 free"       → primary 3, bonus 1
    "free +3"                → bonus 3
    "3 1"                    → primary 3, bonus 1
    """
    norm = normalize(text)
    numbers = [int(n) for n in _INT_RE.findall(norm)]
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", norm):
            numbers.append(value)
    numbers = [n for n in numbers if 1 <= n <= 24]
    if not numbers:
        return Quantity(numbers=[], primary=0, bonus=0)

    has_free = any(w in norm for w in ("free", "gratis", "bonus", "brinde", "get"))
    if norm.strip().startswith("free") or norm.strip().startswith("+"):
        return Quantity(numbers=numbers, primary=0, bonus=numbers[0])
    primary = numbers[0]
    bonus = 0
    if len(numbers) > 1 and (has_free or len(numbers) == 2):
        bonus = numbers[1]
    return Quantity(numbers=numbers, primary=primary, bonus=bonus)


def pick_pack(spoken: str, rules, *, threshold: float = 80.0) -> str:
    """Escolhe o pack_asset pro trecho falado.

    1) casamento fuzzy com o texto da regra ("6 frascos" ≈ "six bottles");
    2) senão, casa pela quantidade (primary, depois total).
    """
    if not rules:
        return ""
    best_score, best_asset = 0.0, ""
    for rule in rules:
        score = _fuzzy_score(spoken, getattr(rule, "spoken", "") or "")
        if score > best_score:
            best_score, best_asset = score, getattr(rule, "pack_asset", "") or ""
    if best_score >= threshold and best_asset:
        return best_asset

    want = parse_quantity(spoken)
    if not want.numbers:
        return ""
    for attr in ("primary", "total"):
        target = getattr(want, attr)
        if not target:
            continue
        for rule in rules:
            have = parse_quantity(getattr(rule, "spoken", "") or "")
            if getattr(have, attr) == target and getattr(rule, "pack_asset", ""):
                return rule.pack_asset
    return ""
