"""
Testes do glossário conceito→visual (broll_glossary).
Rodar:  python test_glossary.py
"""
import broll_glossary as bg

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


def test_loaded():
    print("\n[1] glossário carregado")
    s = bg.stats()
    check("100+ entradas carregadas", s["entries"] >= 100, extra=str(s))
    check("muitos gatilhos", s["triggers"] >= 300, extra=str(s))


def test_match_pt_en():
    print("\n[2] detecta conceito em PT e EN")
    cpt = {c["concept"] for c in bg.match_concepts("ela sente muita dor no joelho")}
    check("'dor no joelho' → knee_pain", "knee_pain" in cpt, extra=str(cpt))
    cen = {c["concept"] for c in bg.match_concepts("his knee hurts so bad")}
    check("'knee' → knee_pain (EN)", "knee_pain" in cen)
    cb = {c["concept"] for c in bg.match_concepts("a gordura na barriga não some")}
    check("'barriga' → belly_stomach", "belly_stomach" in cb, extra=str(cb))


def test_word_boundary():
    print("\n[3] word-boundary (não casa por substring)")
    cm = {c["concept"] for c in bg.match_concepts("um molho de tomate na cozinha")}
    check("'molho' NÃO dispara 'olho' (eyes_vision)", "eyes_vision" not in cm, extra=str(cm))


def test_enrich():
    print("\n[4] enrich_query injeta frase visual concreta")
    q = bg.enrich_query("a person at home", "estou com a visão embaçada e não enxergo")
    check("injetou frase de visão", q != "a person at home"
          and any(w in q.lower() for w in ("vision", "blurry", "eye", "read")), extra=q)
    # sem conceito → query intacta
    q2 = bg.enrich_query("xyz abstract topic", "lorem ipsum dolor")
    check("sem conceito → query intacta", q2 == "xyz abstract topic", extra=q2)


def test_cap():
    print("\n[5] cap de frases (não dilui o embedding)")
    ph, _ = bg.expand("dor no joelho, nas costas e na barriga ao mesmo tempo", max_phrases=3)
    check("respeita o cap de 3 frases", len(ph) == 3, extra=str(len(ph)))
    ph2, _ = bg.expand("dor no joelho", max_phrases=2)
    check("cap menor também respeitado", len(ph2) <= 2, extra=str(len(ph2)))


def test_priority_order():
    print("\n[6] conceito de prioridade alta vem primeiro")
    # 'joelho' (high) + algo normal juntos → as 1ªs frases são de joelho
    ph, _ = bg.expand("a dor no joelho atrapalha a sua vida", max_phrases=2)
    check("frases priorizam o literal forte (knee)",
          any("knee" in p.lower() for p in ph), extra=str(ph))


def test_disabled():
    print("\n[7] flag de desligar")
    orig = bg._GLOSSARY_ON
    bg._GLOSSARY_ON = False
    try:
        check("desligado → query intacta",
              bg.enrich_query("knee", "dor no joelho") == "knee")
    finally:
        bg._GLOSSARY_ON = orig


if __name__ == "__main__":
    test_loaded()
    test_match_pt_en()
    test_word_boundary()
    test_enrich()
    test_cap()
    test_priority_order()
    test_disabled()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
