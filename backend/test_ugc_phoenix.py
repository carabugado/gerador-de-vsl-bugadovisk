"""
Testes do gerador UGC (Melhoria 9) e do Copy Chief PHOENIX (Melhoria 10).
Rodar:  python test_ugc_phoenix.py
"""
import json
import ugc_prompt_gen as ug
import copy_chief as cc

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


# ───────────────────────── UGC: gerador (com mock) ────────────────────────────
def test_ugc_claude():
    print("\n[1] Gerador UGC (Claude mockado)")
    ug.llm._backend_available = lambda b: True
    ug.llm.complete = lambda *a, **k: json.dumps({
        "prompt": "Handheld smartphone video, average woman in messy bathroom, tired",
        "negative_prompt": "blurry",
        "camera": "selfie front camera", "lighting": "window light",
        "duration_seconds": 3.5, "aspect_ratio": "9:16"})
    out = ug.generate({"script_excerpt": "my knees ache every morning",
                       "block_type": "problem", "emotion": "frustration",
                       "energy_level": "low", "visual_type": "emotional",
                       "product": "FlexiKnee", "vertical": "WL", "visual_style": "clean"})
    check("retorna prompt", bool(out.get("prompt")))
    check("negative inclui base (cinematic)", "cinematic" in out["negative_prompt"])
    check("negative do bloco problema (smiling)", "smiling" in out["negative_prompt"])
    check("negative da vertical WL (scale)", "scale showing numbers" in out["negative_prompt"])
    check("aspect 9:16", out["aspect_ratio"] == "9:16")


def test_ugc_fallback():
    print("\n[2] Gerador UGC (fallback sem API)")
    ug.llm._backend_available = lambda b: False
    check("available() == False", ug.available() is False)
    out = ug.generate({"script_excerpt": "click the button now",
                       "block_type": "cta", "emotion": "urgency",
                       "energy_level": "high", "visual_type": "result",
                       "product": "X", "vertical": "ED", "visual_style": ""})
    check("fallback gera prompt", bool(out.get("prompt")))
    check("fallback é UGC (smartphone)", "smartphone" in out["prompt"].lower())
    check("fallback tem quality tail (unpolished)", "unpolished" in out["prompt"].lower())
    check("fallback nunca usa 'cinematic' no prompt", "cinematic" not in out["prompt"].lower())
    check("fallback negative tem base", "studio lighting" in out["negative_prompt"])
    check("fallback marcado _source=fallback", out["_source"] == "fallback")

    # especificidade literal: a regra está no system prompt e o fallback ancora no script
    check("system prompt tem a regra de especificidade literal",
          "ESPECIFICIDADE LITERAL" in ug.UGC_SYSTEM and "depicting literally" not in ug.UGC_SYSTEM)
    out3 = ug.generate({"script_excerpt": "struggling to twist open a glass jar lid",
                        "block_type": "problem", "emotion": "frustration",
                        "energy_level": "low", "visual_type": "emotional",
                        "product": "X", "vertical": "JT", "visual_style": ""})
    check("fallback embute a âncora literal do script",
          "glass jar lid" in out3["prompt"], extra=out3["prompt"])

    # bloco de problema → negative bloqueia 'celebrating'
    out2 = ug.generate({"script_excerpt": "the pain never stops", "block_type": "problem",
                        "emotion": "fear", "energy_level": "low", "visual_type": "emotional",
                        "product": "X", "vertical": "JT", "visual_style": ""})
    check("problema → negative bloqueia 'celebrating'", "celebrating" in out2["negative_prompt"])


# ───────────────────────── PHOENIX: parse + map ───────────────────────────────
def test_phoenix_parse():
    print("\n[3] PHOENIX (parse de análise + mapa)")
    fake = (
        "1. PRIMEIRA IMPRESSÃO\nEsse hook não segura ninguém.\n\n"
        "5. DIAGNÓSTICO FINAL\nScore geral: 7/10\n\n"
        "6. MAPA DE B-ROLL\n(tabela...)\n"
        "---BROLL_MAP_JSON---\n"
        '[{"paragraph": 3, "block_type": "problem", "broll_description": "woman frustrated mirror", '
        '"emotion": "frustration", "priority": "high", "status": "essential"},'
        '{"paragraph": 12, "block_type": "cta", "broll_description": "price reveal", '
        '"emotion": "urgency", "priority": "high", "status": "prohibited"}]\n'
        "---CAUSE_MAP_JSON---\n"
        '{"problema_aparente": {"descricao": "dor articular", "paragrafos": "3-7", '
        '"sintomas": ["joelhos doem ao subir escada", "maos inchadas de manha"], '
        '"broll_visual": ["homem parando na escada"], "higgs_prompts": ["handheld phone, man on stairs"]},'
        '"causa_real": {"descricao": "proteina MMP-13", "paragrafos": "12-15", '
        '"linguagem_virada": "o problema nao e a idade", "broll_visual": ["pessoa pesquisando no celular"], '
        '"higgs_prompts": ["POV phone scrolling medical article"]},'
        '"mecanismo": {"descricao": "neutraliza MMP-13", "paragrafos": "16-20", "termos": ["MMP-13"], '
        '"broll_visual": ["grafico na tela"], "higgs_prompts": ["phone filming laptop diagram"]},'
        '"ingredientes": [{"nome": "Curcumin", "dosagem": "500mg", "claim": "reduz inflamacao", '
        '"broll_visual": "turmeric root on counter", "higgs_prompt": "phone close-up turmeric"}]}'
    )
    cc.llm._backend_available = lambda b: True
    cc.llm.complete = lambda *a, **k: fake
    res = cc.analyze("minha copy completa aqui")
    check("ok", res.get("ok") is True)
    check("texto não contém o marcador", "---BROLL_MAP_JSON---" not in res["analysis"])
    check("análise preservada", "hook não segura" in res["analysis"])
    check("score extraído = 7", res.get("score") == 7)
    check("mapa com 2 entradas", len(res["broll_map"]) == 2)
    check("primeira entrada é problem/essential",
          res["broll_map"][0]["status"] == "essential")
    # MAPA DE CAUSA REAL
    cm = res.get("cause_map", {})
    check("cause_map parseado", bool(cm))
    check("problema_aparente com sintomas exatos",
          "joelhos doem ao subir escada" in cm.get("problema_aparente", {}).get("sintomas", []))
    check("causa_real com linguagem da virada",
          "idade" in cm.get("causa_real", {}).get("linguagem_virada", ""))
    check("ingredientes com nome+dosagem",
          cm.get("ingredientes", [{}])[0].get("nome") == "Curcumin" and
          cm["ingredientes"][0].get("dosagem") == "500mg")
    check("texto não vaza CAUSE marker", "---CAUSE_MAP_JSON---" not in res["analysis"])


def test_phoenix_apply_map():
    print("\n[4] PHOENIX apply_map (bloqueia 'prohibited')")
    from matcher import make_result
    segs = [
        {"text": "I felt so frustrated looking in the mirror every morning",
         "start": 0, "end": 5, "arc_position": "problem"},
        {"text": "today only the price drops to forty nine dollars",
         "start": 6, "end": 11, "arc_position": "offer"},
    ]
    matches = [
        make_result(segs[0], {"path": "/a.mp4", "filename": "a.mp4", "duration": 6,
                              "score": 30, "source": "project"}, "ok", "x"),
        make_result(segs[1], {"path": "/b.mp4", "filename": "b.mp4", "duration": 6,
                              "score": 30, "source": "project"}, "ok", "x"),
    ]
    bmap = [
        {"block_type": "problem", "broll_description": "woman frustrated mirror morning",
         "emotion": "frustration", "priority": "high", "status": "essential"},
        {"block_type": "cta", "broll_description": "price reveal dollars offer",
         "emotion": "urgency", "priority": "high", "status": "prohibited"},
    ]
    n = cc.apply_map(segs, matches, bmap)
    check("anotou 2 segmentos", n == 2)
    check("seg0 recebeu phoenix", segs[0].get("phoenix", {}).get("status") == "essential")
    check("seg1 (price) virou blocked", matches[1]["status"] == "blocked")
    check("seg1 sem broll_path", matches[1]["broll_path"] is None)
    check("seg0 segue ok", matches[0]["status"] == "ok")


if __name__ == "__main__":
    test_ugc_claude()
    test_ugc_fallback()
    test_phoenix_parse()
    test_phoenix_apply_map()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
