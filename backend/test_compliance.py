"""
Testes da camada de compliance (INEGOCIÁVEL — bloqueia b-roll proibido).
Rodar:  python test_compliance.py

A camada não tinha NENHUM teste. Cobre: regras universais, all/any, detecção de
vertical, regras JT/FG novas, fail-open com regras vazias, e — o ganho desta sessão —
a LEGENDA do clipe entrando no asset_text (pega clipe de nome-hash que mostra coisa
proibida). Usa regras inline (monkeypatch de _load_rules) p/ não depender do JSON real.
"""
import compliance as cp
import asset_tagger as at

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


RULES = {
    "vertical_keywords": {
        "WL": ["weight", "weight loss", "emagrec"],
        "ED": ["erectile", "libido"],
        "JT": ["joint", "knee", "arthritis"],
        "FG": ["fungus", "toenail", "nail"],
    },
    "universal_block": [
        {"any": ["surgery", "scalpel", "syringe"], "reason": "Cirurgia/medicamento"},
        {"all": ["before", "after"], "reason": "Antes/depois"},
        {"any": ["sex", "fuck", "nude", "naked"], "reason": "Nudez/conteúdo sexual"},
    ],
    "by_vertical": {
        "WL": [{"any": ["bathroom scale", "weighing"], "reason": "WL: balança"}],
        "JT": [{"any": ["joint injection", "knee replacement"], "reason": "JT: procedimento articular"}],
        "FG": [{"any": ["toenail removal", "nail fungus closeup"], "reason": "FG: imagem clínica de unha"}],
    },
}


def mk_match(path, status="ok"):
    return {"status": status, "broll_path": path,
            "broll_filename": path.split("/")[-1], "select_reason": ""}


def mk_seg(text="", vq="", st=""):
    return {"text": text, "visual_query": vq, "scene_type": st}


def test_detect_vertical():
    print("\n[1] detect_vertical")
    check("vertical explícito vence", cp.detect_vertical({"vertical": "ED"}, RULES) == "ED")
    check("detecta por contagem de keywords",
          cp.detect_vertical({"niche": "weight loss for women"}, RULES) == "WL")
    check("escolhe o de mais hits",
          cp.detect_vertical({"niche": "joint and knee arthritis pain"}, RULES) == "JT")
    check("sem contexto → ''", cp.detect_vertical({}, RULES) == "")


def test_match_rule():
    print("\n[2] _matches_rule — all exige todos; any exige um")
    check("'all' só bloqueia com TODOS os termos",
          cp._matches_rule("before and after photo", {"all": ["before", "after"]}) is True)
    check("'all' não bloqueia com só um termo",
          cp._matches_rule("the day before lunch", {"all": ["before", "after"]}) is False)
    check("'any' bloqueia com um termo", cp._matches_rule("operating surgery room", {"any": ["surgery"]}) is True)


def _run(segments, matches, context):
    orig = cp._load_rules
    cp._load_rules = lambda: RULES
    try:
        return cp.apply_compliance(segments, matches, context)
    finally:
        cp._load_rules = orig


def test_apply_universal_and_vertical():
    print("\n[3] apply_compliance — universal + vertical")
    segs = [mk_seg(vq="surgeon in operating room"), mk_seg(vq="woman drinking water")]
    ms = [mk_match("/a/scalpel_scene.mp4"), mk_match("/a/clean.mp4")]
    info = _run(segs, ms, {"niche": "weight loss"})
    check("clipe com 'surgery/scalpel' no nome → bloqueado",
          ms[0]["status"] == "blocked_compliance", extra=ms[0]["status"])
    check("clipe limpo passa", ms[1]["status"] == "ok")
    check("conta 1 bloqueio + detecta vertical WL",
          info["blocked"] == 1 and info["vertical"] == "WL", extra=str(info))
    # regra de vertical WL (balança)
    segs2 = [mk_seg(vq="bathroom scale closeup")]
    ms2 = [mk_match("/a/clip_x.mp4")]
    info2 = _run(segs2, ms2, {"niche": "weight loss"})
    check("regra de vertical WL bloqueia balança", ms2[0]["status"] == "blocked_compliance")


def test_jt_fg_rules():
    print("\n[4] regras novas de JT e FG")
    segs = [mk_seg(vq="knee replacement surgery animation")]
    ms = [mk_match("/a/joint_injection_clip.mp4")]
    info = _run(segs, ms, {"niche": "joint pain knee arthritis"})
    check("vertical JT detectada", info["vertical"] == "JT", extra=str(info))
    check("regra JT bloqueia procedimento articular", ms[0]["status"] == "blocked_compliance")
    segs2 = [mk_seg(vq="closeup of a toenail removal")]
    ms2 = [mk_match("/a/clip_99.mp4")]
    info2 = _run(segs2, ms2, {"niche": "toenail fungus nail"})
    check("vertical FG detectada", info2["vertical"] == "FG", extra=str(info2))
    check("regra FG bloqueia imagem clínica de unha", ms2[0]["status"] == "blocked_compliance")


def test_caption_closes_gap():
    print("\n[5] a LEGENDA do clipe entra no compliance (clipe de nome-hash)")
    # filename hash + visual_query e narração LIMPOS; só a legenda revela 'surgery'
    segs = [mk_seg(text="our doctor explained the method", vq="person at home smiling")]
    ms = [mk_match("/a/ivz_1.mp4")]
    orig = at.load_tags
    at.load_tags = lambda p: {"caption": "a surgeon performing surgery in an operating room"}
    try:
        info = _run(segs, ms, {})
        check("legenda 'surgery' bloqueia clipe de nome-hash",
              ms[0]["status"] == "blocked_compliance", extra=ms[0]["status"])
    finally:
        at.load_tags = orig
    # narração mencionando cirurgia NÃO bloqueia (asset_text não usa o texto da narração)
    segs2 = [mk_seg(text="the surgery was scary", vq="person walking in a park")]
    ms2 = [mk_match("/a/clean2.mp4")]
    at.load_tags2 = at.load_tags
    at.load_tags = lambda p: {"caption": "a person walking in a sunny park"}
    try:
        info2 = _run(segs2, ms2, {})
        check("narração com 'surgery' NÃO bloqueia (valida o ASSET, não a fala)",
              ms2[0]["status"] == "ok", extra=ms2[0]["status"])
    finally:
        at.load_tags = orig


def test_fail_open_empty_rules():
    print("\n[6] regras vazias → fail-open documentado (não bloqueia nada)")
    orig = cp._load_rules
    cp._load_rules = lambda: {}
    try:
        ms = [mk_match("/a/surgery_scene.mp4")]
        info = cp.apply_compliance([mk_seg(vq="surgery")], ms, {})
        check("sem regras → blocked=0 e nada bloqueado",
              info["blocked"] == 0 and ms[0]["status"] == "ok", extra=str(info))
    finally:
        cp._load_rules = orig


def test_ed_local_exempt():
    print("\n[7] pasta +18 (broll_source='ed') é ISENTA do compliance sexual")
    orig = at.load_tags
    at.load_tags = lambda p: {"caption": "a woman is fucked by a man, nude sex in bed"}
    try:
        # clipe da pasta +18 → NÃO bloqueia (sexual é intencional, local, não vai pra nuvem)
        m_ed = mk_match("/ed/clip.mp4"); m_ed["broll_source"] = "ed"
        _run([mk_seg(vq="couple in bed")], [m_ed], {"niche": "erectile libido"})
        check("clipe +18 (source=ed) passa apesar do caption sexual",
              m_ed["status"] == "ok", extra=m_ed["status"])
        # mesmo caption mas da biblioteca → bloqueia normalmente
        m_lib = mk_match("/lib/clip.mp4"); m_lib["broll_source"] = "project"
        _run([mk_seg(vq="couple in bed")], [m_lib], {"niche": "erectile libido"})
        check("clipe da biblioteca com mesmo caption → bloqueado",
              m_lib["status"] == "blocked_compliance", extra=m_lib["status"])
    finally:
        at.load_tags = orig


if __name__ == "__main__":
    test_detect_vertical()
    test_match_rule()
    test_apply_universal_and_vertical()
    test_jt_fg_rules()
    test_caption_closes_gap()
    test_fail_open_empty_rules()
    test_ed_local_exempt()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
