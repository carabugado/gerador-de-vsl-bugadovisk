"""Briefing da demanda: o que sai, o que entra, e com quais assets."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

SWAP_KINDS = ("same_format", "format_change")
SWAP_TARGETS = ("audio", "visual", "both")


@dataclass
class QuantityRule:
    """Casa a quantidade FALADA com o pack certo do produto novo.

    Ex.: {"spoken": "6 frascos", "pack_asset": "packs/novo_6un.png"}
         {"spoken": "free +3",   "pack_asset": "packs/novo_3un.png"}
         {"spoken": "3 1",       "pack_asset": "packs/novo_3un.png"}
    """

    spoken: str = ""
    pack_asset: str = ""


@dataclass
class Briefing:
    video: str = ""
    old_product_name: str = ""
    new_product_name: str = ""
    old_aliases: list[str] = field(default_factory=list)
    new_aliases: list[str] = field(default_factory=list)
    old_assets: list[str] = field(default_factory=list)
    new_assets: list[str] = field(default_factory=list)
    swap_kind: str = "same_format"
    swap_target: str = "both"
    language: str = "en"
    quantity_map: list[QuantityRule] = field(default_factory=list)
    notes: str = ""

    # ── nomes a procurar ────────────────────────────────────────────────
    @property
    def old_terms(self) -> list[str]:
        return _dedupe([self.old_product_name, *self.old_aliases])

    @property
    def new_terms(self) -> list[str]:
        return _dedupe([self.new_product_name, *self.new_aliases])

    @property
    def wants_audio(self) -> bool:
        return self.swap_target in ("audio", "both")

    @property
    def wants_visual(self) -> bool:
        return self.swap_target in ("visual", "both")

    @property
    def changes_format(self) -> bool:
        """gotas → cápsula: o b-roll antigo não pode ser reaproveitado."""
        return self.swap_kind == "format_change"

    # ── serialização ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Briefing":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        rules = clean.get("quantity_map") or []
        clean["quantity_map"] = [
            r if isinstance(r, QuantityRule) else QuantityRule(**{k: v for k, v in (r or {}).items() if k in {"spoken", "pack_asset"}})
            for r in rules
        ]
        for key in ("old_aliases", "new_aliases", "old_assets", "new_assets"):
            if clean.get(key) is None:
                clean[key] = []
        return cls(**clean)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Briefing":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    def save(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    # ── validação ───────────────────────────────────────────────────────
    def validate(self, *, check_files: bool = True) -> list[str]:
        """Lista de problemas. Vazia = pronto pra rodar."""
        problems: list[str] = []
        if not self.old_product_name.strip():
            problems.append("old_product_name vazio")
        if not self.new_product_name.strip():
            problems.append("new_product_name vazio")
        if self.swap_kind not in SWAP_KINDS:
            problems.append(f"swap_kind inválido: {self.swap_kind!r} (use {'/'.join(SWAP_KINDS)})")
        if self.swap_target not in SWAP_TARGETS:
            problems.append(f"swap_target inválido: {self.swap_target!r} (use {'/'.join(SWAP_TARGETS)})")
        if self.wants_visual and not self.old_assets:
            problems.append("old_assets vazio — o CLIP precisa de imagens do produto ANTIGO pra achar as aparições")
        if self.wants_visual and not self.new_assets:
            problems.append("new_assets vazio — sem produto novo não dá pra montar a V2")
        if check_files:
            for label, paths in (("old_assets", self.old_assets), ("new_assets", self.new_assets)):
                for item in paths:
                    if item and not Path(item).exists():
                        problems.append(f"{label}: caminho não existe → {item}")
            if self.video and not _looks_like_url(self.video) and not Path(self.video).exists():
                problems.append(f"video não existe → {self.video}")
        for rule in self.quantity_map:
            if rule.spoken and not rule.pack_asset:
                problems.append(f"quantity_map: '{rule.spoken}' sem pack_asset")
        return problems


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = (item or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


TEMPLATE = Briefing(
    video="vsl_original.mp4",
    old_product_name="PRODUTO ANTIGO",
    new_product_name="PRODUTO NOVO",
    old_aliases=["apelido do produto antigo", "como o locutor às vezes fala"],
    new_aliases=[],
    old_assets=["assets/antigo/frente.png", "assets/antigo/lado.png"],
    new_assets=["assets/novo/frente.png"],
    swap_kind="same_format",
    swap_target="both",
    language="en",
    quantity_map=[QuantityRule(spoken="6 frascos", pack_asset="assets/novo/pack_6.png")],
    notes="format_change = muda o formato (gotas → cápsula); aí o b-roll antigo não serve.",
)


def write_template(path: str | os.PathLike[str], *, force: bool = False) -> Path:
    target = Path(path)
    if target.exists() and not force:
        raise FileExistsError(f"{target} já existe (use --force pra sobrescrever)")
    return TEMPLATE.save(target)
