"""
Testes das 3 melhorias de seleção de B-roll.
Rodar:  python test_broll_improvements.py
Sem dependência de pytest — usa asserts e imprime PASS/FAIL.
"""
import json
import broll_classifier as bc
import broll_score as bs

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


# ─────────────────── MELHORIA 1: classificador (com mock da API) ───────────────
def test_classifier():
    print("\n[1] Classificador semântico")

    # mock da camada LLM: devolve o que pedirmos, e marca os backends como disponíveis
    canned = {"raw": "{}"}
    bc.llm.complete = lambda *a, **k: canned["raw"]
    bc.llm._backend_available = lambda b: True

    # (a) hook positivo
    canned["raw"] = json.dumps({
        "block_type": "hook", "emotion": "excitement", "energy_level": "high",
        "visual_type": "lifestyle", "visual_description": "energetic senior hiking",
        "search_terms": ["happy senior hiking", "morning energy"], "avoid": ["sadness"],
        "transition": "cut", "suggested_duration": 2.5})
    p = bc.classify("Imagine waking up full of energy again", use_cache=False)
    check("hook → bloco hook", p["block_type"] == "hook")
    check("hook → emoção positiva mantida", p["emotion"] == "excitement")

    # (b) bloco de problema com emoção ERRADA (positiva) → deve ser corrigida
    canned["raw"] = json.dumps({
        "block_type": "problem", "emotion": "hope", "energy_level": "low",
        "visual_type": "emotional", "visual_description": "woman rubbing aching knee",
        "search_terms": ["woman knee pain", "joint ache"], "avoid": ["happy person"],
        "transition": "dissolve", "suggested_duration": 4.0})
    p = bc.classify("Every morning the joint pain makes it impossible to walk", use_cache=False)
    check("problema → emoção NÃO pode ser positiva", p["emotion"] not in bc._POSITIVE_EMO,
          extra=p["emotion"])
    check("problema → avoid preserva 'happy person'", "happy person" in p["avoid"])

    # (c) CTA urgente
    canned["raw"] = json.dumps({
        "block_type": "cta", "emotion": "urgency", "energy_level": "high",
        "visual_type": "result", "visual_description": "hand clicking order button",
        "search_terms": ["click buy now", "limited offer"], "avoid": ["boredom"],
        "transition": "none", "suggested_duration": 2.0})
    p = bc.classify("Click the button below before stock runs out", use_cache=False)
    check("cta → bloco cta", p["block_type"] == "cta")
    check("cta → emoção não-negativa", p["emotion"] not in bc._NEGATIVE_EMO, extra=p["emotion"])
    check("cta → duração curta clampada (2-5s)", 2.0 <= p["suggested_duration"] <= 5.0)


# ─────────────────────────── MELHORIA 3: scoring ──────────────────────────────
def _asset(path, tags, dur=6.0):
    return {"path": path, "filename": path.split("/")[-1], "duration": dur,
            "_source": "project", "tags": tags}


def test_scoring():
    print("\n[2] Scoring multicritério")

    profile = {"block_type": "problem", "emotion": "frustration",
               "energy_level": "low", "visual_type": "emotional",
               "search_terms": ["woman frustrated mirror", "alone struggle"],
               "avoid": ["celebration"]}
    vertical = "WL"

    assets = [
        # ideal: emoção+10, tipo+8, energia+5, bloco+6, vertical+5, kw(frustrated,mirror)+6, novo+2 = 42
        _asset("/a/woman-frustrated-mirror.mp4", {
            "emotions": ["frustration", "defeat"], "energy_level": "low",
            "visual_type": ["emotional"], "suitable_blocks": ["problem", "agitation"],
            "unsuitable_blocks": ["cta"], "verticals": ["WL"],
            "keywords": ["frustrated", "mirror", "struggle"], "compliance_safe": True,
            "times_used": 0, "times_accepted": 0, "times_rejected": 0}),
        # inadequado pro bloco problem → ELIMINADO
        _asset("/a/celebration-party.mp4", {
            "emotions": ["excitement"], "energy_level": "high",
            "visual_type": ["result"], "suitable_blocks": ["cta", "guarantee"],
            "unsuitable_blocks": ["problem"], "verticals": ["WL"],
            "keywords": ["celebration", "party"], "compliance_safe": True,
            "times_used": 0, "times_accepted": 0, "times_rejected": 0}),
        # bate com avoid (celebration) → ELIMINADO
        _asset("/a/confetti.mp4", {
            "emotions": ["excitement"], "energy_level": "high",
            "visual_type": ["lifestyle"], "suitable_blocks": ["hook"],
            "unsuitable_blocks": [], "verticals": ["WL"],
            "keywords": ["celebration", "confetti"], "compliance_safe": True,
            "times_used": 0, "times_accepted": 0, "times_rejected": 0}),
        # ok mas mais fraco: tipo+8, energia+5, bloco+6, vertical+5, novo+2 = 26 (sem emoção/kw)
        _asset("/a/sad-man-window.mp4", {
            "emotions": ["sadness"], "energy_level": "low",
            "visual_type": ["emotional"], "suitable_blocks": ["problem"],
            "unsuitable_blocks": [], "verticals": ["WL"],
            "keywords": ["sad", "window", "lonely"], "compliance_safe": True,
            "times_used": 0, "times_accepted": 0, "times_rejected": 0}),
    ]

    ranked = bs.rank_segment(profile, assets, vertical, used_paths=set(), top_k=3)
    paths = [c["path"] for c in ranked]
    check("melhor é o frustrated-mirror", paths[0] == "/a/woman-frustrated-mirror.mp4",
          extra=str(paths))
    check("celebration eliminado (bloco inadequado)", "/a/celebration-party.mp4" not in paths)
    check("confetti eliminado (avoid)", "/a/confetti.mp4" not in paths)
    check("sad-man entra como alternativa", "/a/sad-man-window.mp4" in paths)
    check("score do top > segundo", ranked[0]["score"] > ranked[1]["score"])

    # histórico negativo derruba o asset
    bad = _asset("/a/overused.mp4", {
        "emotions": ["frustration"], "energy_level": "low", "visual_type": ["emotional"],
        "suitable_blocks": ["problem"], "unsuitable_blocks": [], "verticals": ["WL"],
        "keywords": ["frustrated"], "compliance_safe": True,
        "times_used": 10, "times_accepted": 1, "times_rejected": 8})
    sc, _ = bs.score_asset(bad, profile, vertical, set())
    sc_fresh, _ = bs.score_asset(assets[0], profile, vertical, set())
    check("histórico ruim pontua menos que o fresco", (sc or -999) < sc_fresh)

    # repetição na sessão penaliza
    sc_used, _ = bs.score_asset(assets[0], profile, vertical, {"/a/woman-frustrated-mirror.mp4"})
    check("repetição (-12) reduz o score", sc_used < sc_fresh)


# ─────────────────────────── Fallback sem API ─────────────────────────────────
def test_fallback():
    print("\n[3] Fallback quando a API cai")

    bc.llm._backend_available = lambda b: False     # nenhuma API disponível
    check("classifier.available() == False", bc.available() is False)
    check("classify() retorna None sem API", bc.classify("qualquer texto") is None)

    segs = [
        {"text": "the pain never stops", "arc_position": "problem", "emotional_peak": 8,
         "visual_query": "man holding lower back in pain", "start": 0.0, "end": 5.0},
        {"text": "now you can feel young again", "arc_position": "solution",
         "emotional_peak": 6, "visual_query": "active senior smiling outdoors",
         "start": 6.0, "end": 11.0},
    ]
    profiles = bc.classify_segments(segs)
    check("classify_segments devolve 1 perfil por segmento", len(profiles) == len(segs))
    check("perfis são de fallback", all(p["_source"] == "fallback" for p in profiles))
    check("fallback do problema → emoção negativa", profiles[0]["emotion"] in bc._NEGATIVE_EMO)
    check("fallback da solução → bloco mechanism", profiles[1]["block_type"] == "mechanism")

    # o scoring ainda funciona com perfil de fallback
    asset = _asset("/a/back-pain.mp4", {
        "emotions": ["frustration"], "energy_level": "high", "visual_type": ["emotional"],
        "suitable_blocks": ["problem"], "unsuitable_blocks": [], "verticals": ["JT"],
        "keywords": ["back", "pain"], "compliance_safe": True,
        "times_used": 0, "times_accepted": 0, "times_rejected": 0})
    ranked, matches = bs.select(segs, profiles, [asset], vertical="JT")
    check("select() não quebra com fallback", len(matches) == len(segs))


def test_enumeration():
    print("\n[4] #Fase3 — split_enumerations")
    # 8s + 3 itens → 3 sub-slots distintos
    segs = [{"start": 10.0, "end": 18.0, "text": "turmeric, ginger and pepper", "arc_position": "ingredients"}]
    profs = [{"visual_description": "spices", "broll_items": ["turmeric", "ginger", "pepper"]}]
    s2, p2 = bc.split_enumerations(segs, profs)
    check("8s/3itens → 3 sub-segmentos", len(s2) == 3, extra=str(len(s2)))
    check("queries distintas por item", [p["visual_description"] for p in p2] == ["turmeric", "ginger", "pepper"])
    check("fatias contíguas e dentro da janela",
          abs(s2[0]["start"] - 10.0) < 1e-6 and abs(s2[-1]["end"] - 18.0) < 1e-6
          and abs(s2[0]["end"] - s2[1]["start"]) < 1e-6)
    check("mesmo _enum_group (rajada)", s2[0]["_enum_group"] == s2[2]["_enum_group"])
    # 3s + 3 itens → não cabe (cada sub < 2s) → NÃO divide
    s3, p3 = bc.split_enumerations(
        [{"start": 0.0, "end": 3.0, "text": "a, b, c"}],
        [{"visual_description": "x", "broll_items": ["a", "b", "c"]}])
    check("3s/3itens → não divide (sem tempo)", len(s3) == 1)
    # sem broll_items → não divide
    s4, _ = bc.split_enumerations([{"start": 0.0, "end": 10.0}], [{"broll_items": []}])
    check("sem itens → não divide", len(s4) == 1)
    # regressão: o prompt LOCAL (Ollama) precisa pedir broll_items, senão "3 ingredientes"
    # colapsa em 1 clipe no caminho grátis (objetivo nº1 do usuário).
    check("prompt Ollama menciona broll_items", "broll_items" in bc.CLASSIFIER_SYSTEM_OLLAMA)


if __name__ == "__main__":
    test_classifier()
    test_scoring()
    test_fallback()
    test_enumeration()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
