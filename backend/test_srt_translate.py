"""
Testes da tradução de legendas (srt_translate).
Rodar:  python test_srt_translate.py

Trava o contrato do módulo SEM depender de IA real: a função llm.complete é
substituída por mocks determinísticos. Foca no que importa para "tradução
simultânea": preservar nº de blocos, preservar timecodes verbatim e falhar
honestamente quando a IA não traduz.
"""
import json
import re
import llm
import srt_translate as s

_fails = []

# Extrai os blocos-fonte do prompt (formato <<<N>>> texto) que o módulo monta.
_U_RE = re.compile(r"<<<\s*(\d+)\s*>>>[ \t]*(.*?)(?=<<<\s*\d+\s*>>>|\Z)", re.DOTALL)


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


SAMPLE = (
    "1\n00:00:00,750 --> 00:00:03,003\nGood morning, gentlemen.\n\n"
    "2\n00:00:03,003 --> 00:00:05,046\nWell, today I brought Rafaela\n\n"
    "3\n00:00:05,046 --> 00:00:06,172\nas our interpreter,\n\n"
    "4\n00:00:06,172 --> 00:00:07,590\nbecause after\n"
)


def _src_texts(user):
    """Extrai os blocos-fonte do prompt (formato <<<N>>> texto), em ordem."""
    items = sorted(((int(n), t.strip()) for n, t in _U_RE.findall(user)), key=lambda x: x[0])
    return [t for _, t in items]


def mock_passthrough(system, user, **kw):
    """IA fake: 'traduz' prefixando [PT], devolvendo no formato de marcadores."""
    return "\n".join(f"<<<{i + 1}>>> [PT] {t}" for i, t in enumerate(_src_texts(user)))


def mock_wrong_count(system, user, **kw):
    """IA fake quebrada: junta tudo num marcador só (errado p/ vários) — força fallback.
    No bloco a bloco (1 item) responde certo."""
    arr = _src_texts(user)
    if len(arr) == 1:
        return f"<<<1>>> [PT] {arr[0]}"
    return "<<<1>>> juntei tudo num bloco só"


def mock_dict_object(system, user, **kw):
    """Reproduz o BUG real do qwen2.5: objeto JSON com chaves erradas, mas com N
    valores na ordem. O fallback dict-values (len casa) deve resgatar."""
    arr = _src_texts(user)
    return json.dumps({f"chave_{i}": "[PT] " + t for i, t in enumerate(arr)}, ensure_ascii=False)


def mock_dead(system, user, **kw):
    raise RuntimeError("IA offline / cota")


# ── parsing / round-trip ──────────────────────────────────────────────────────
def test_parse_and_timecodes():
    blocks = s.parse_srt(SAMPLE)
    check("parse: 4 blocos", len(blocks) == 4, str(len(blocks)))
    check("parse: timecode verbatim", blocks[0]["start"] == "00:00:00,750"
          and blocks[0]["end"] == "00:00:03,003")
    check("parse: texto limpo", blocks[1]["text"] == "Well, today I brought Rafaela")
    rebuilt = s.build_srt(blocks)
    check("build: timecodes preservados",
          all(b["start"] in rebuilt and b["end"] in rebuilt for b in blocks))
    check("build: renumera de 1", rebuilt.startswith("1\n"))


# ── tradução feliz (mock) ─────────────────────────────────────────────────────
def test_happy_path():
    llm.complete = mock_passthrough
    llm.available = lambda: True
    res = s.translate_srt_text(SAMPLE, target="pt")
    check("happy: ok", res.get("ok") is True)
    check("happy: 4 blocos", res.get("blocks") == 4)
    check("happy: sem warning", res.get("warning") is None)
    out = s.parse_srt(res["srt"])
    src = s.parse_srt(SAMPLE)
    check("happy: nº de blocos preservado", len(out) == len(src) == 4)
    check("happy: timecodes idênticos",
          all(a["start"] == b["start"] and a["end"] == b["end"] for a, b in zip(src, out)))
    check("happy: tudo traduzido", all(t["text"].startswith("[PT] ") for t in out))


# ── contagem errada → fallback bloco a bloco mantém contagem ───────────────────
def test_count_mismatch_fallback():
    llm.complete = mock_wrong_count
    llm.available = lambda: True
    res = s.translate_srt_text(SAMPLE, target="pt")
    check("fallback: ok", res.get("ok") is True)
    out = s.parse_srt(res["srt"])
    check("fallback: contagem preservada (4)", len(out) == 4, str(len(out)))
    check("fallback: traduziu via bloco a bloco", all(t["text"].startswith("[PT] ") for t in out))


# ── objeto JSON com chaves erradas (bug do qwen) → resgatado por dict-values ────
def test_dict_object_fallback():
    llm.complete = mock_dict_object
    llm.available = lambda: True
    res = s.translate_srt_text(SAMPLE, target="pt")
    check("dict-bug: ok", res.get("ok") is True)
    out = s.parse_srt(res["srt"])
    check("dict-bug: contagem preservada (4)", len(out) == 4, str(len(out)))
    check("dict-bug: sem warning (tudo traduzido)", res.get("warning") is None)
    check("dict-bug: tudo traduzido", all(t["text"].startswith("[PT] ") for t in out))


# ── IA morta → falha honesta, sem entregar original disfarçado ─────────────────
def test_honest_failure():
    llm.complete = mock_dead
    llm.available = lambda: True
    res = s.translate_srt_text(SAMPLE, target="pt")
    check("morta: ok == False", res.get("ok") is False)
    check("morta: tem erro", bool(res.get("error")))
    check("morta: NÃO retorna srt original", "srt" not in res)


# ── IA indisponível de cara ───────────────────────────────────────────────────
def test_unavailable():
    llm.available = lambda: False
    res = s.translate_srt_text(SAMPLE, target="pt")
    check("indisponível: ok == False", res.get("ok") is False)
    check("indisponível: tem erro", bool(res.get("error")))


# ── offset (encaixe no playhead) desloca os tempos ─────────────────────────────
def test_offset_shift():
    # helpers de tempo
    check("ts→ms", s._ts_to_ms("00:00:01,500") == 1500)
    check("ms→ts", s._ms_to_ts(1500) == "00:00:01,500")
    check("ms→ts clampa em 0", s._ms_to_ts(-50) == "00:00:00,000")
    # tradução com offset de 4.77s
    llm.complete = mock_passthrough
    llm.available = lambda: True
    res = s.translate_srt_text(SAMPLE, target="pt", offset_seconds=4.77)
    out = s.parse_srt(res["srt"])
    src = s.parse_srt(SAMPLE)
    check("offset: 1º bloco deslocado +4,77s",
          out[0]["start"] == s._ms_to_ts(s._ts_to_ms(src[0]["start"]) + 4770),
          out[0]["start"])
    check("offset: nº de blocos preservado", len(out) == len(src))
    # offset 0 = verbatim
    res0 = s.translate_srt_text(SAMPLE, target="pt", offset_seconds=0)
    out0 = s.parse_srt(res0["srt"])
    check("offset 0: timecodes verbatim",
          all(a["start"] == b["start"] for a, b in zip(src, out0)))


# ── SRT vazio ─────────────────────────────────────────────────────────────────
def test_empty():
    llm.available = lambda: True
    res = s.translate_srt_text("sem timecodes aqui", target="pt")
    check("vazio: ok == False", res.get("ok") is False)


if __name__ == "__main__":
    print("== srt_translate ==")
    test_parse_and_timecodes()
    test_happy_path()
    test_count_mismatch_fallback()
    test_dict_object_fallback()
    test_honest_failure()
    test_unavailable()
    test_offset_shift()
    test_empty()
    print()
    if _fails:
        print(f"❌ {len(_fails)} falha(s): {', '.join(_fails)}")
        raise SystemExit(1)
    print("✅ todos os testes passaram")
