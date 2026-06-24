"""
Testes do controle de ritmo/timing (rhythm.apply_rhythm) e do fatiamento de
trechos longos (broll_classifier.split_long_segments).
Rodar:  python test_rhythm.py

Trava o comportamento ATUAL antes de qualquer mudança maior (a camada não tinha
nenhum teste). Fixa as constantes no topo pra não depender de env nem contaminar.
"""
import rhythm
import broll_classifier as bc

# Constantes determinísticas (independem de env/ordem de teste)
rhythm.MIN_DUR     = 2.0
rhythm.MAX_DUR     = 5.0
rhythm.MIN_GAP     = 1.5
rhythm.HOOK_BREATH = 1.0
rhythm.MAX_CONSEC  = 5
rhythm.PAUSE_RESET = 6.0

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


def mk_seg(s, e, text="narração comum sobre o tema", arc="story", **kw):
    return {"start": s, "end": e, "text": text, "arc_position": arc, **kw}


def mk_match(s, e, path="/a/clip.mp4", status="ok"):
    return {"status": status, "broll_path": path, "broll_filename": path.split("/")[-1],
            "start": s, "end": e, "transition": "cut"}


def test_trim():
    print("\n[1] trim para a duração máxima")
    # trecho longe do início (evita o respiro de hook) — clip de 20s deve cair p/ 5s
    segs = [mk_seg(20, 40)]
    m = [mk_match(20, 40)]
    c = rhythm.apply_rhythm(segs, m)
    check("trimma p/ MAX_DUR (5s)", abs((m[0]["end"] - m[0]["start"]) - 5.0) < 1e-6,
          extra=f'{m[0]["start"]}-{m[0]["end"]}')
    check("contou trimmed", c["trimmed"] >= 1)
    # Qualidade Alta: max_dur=3 encurta ainda mais
    segs2 = [mk_seg(20, 40)]; m2 = [mk_match(20, 40)]
    rhythm.apply_rhythm(segs2, m2, max_dur=3.0)
    check("max_dur=3 → clipe de 3s", abs((m2[0]["end"] - m2[0]["start"]) - 3.0) < 1e-6,
          extra=f'{m2[0]["start"]}-{m2[0]["end"]}')


def test_min_gap_push():
    print("\n[2] gap mínimo empurra o segundo b-roll")
    segs = [mk_seg(20, 26), mk_seg(25.5, 33)]
    m = [mk_match(20, 26), mk_match(25.5, 33)]
    c = rhythm.apply_rhythm(segs, m)
    # clip0 vira 20-25 (trim). clip1 começava 25.5 < 25+1.5 → empurrado p/ 26.5
    check("segundo b-roll empurrado p/ last_end+MIN_GAP",
          abs(m[1]["start"] - 26.5) < 1e-6, extra=f'{m[1]["start"]}')
    check("contou pushed", c["pushed"] >= 1)


def test_max_consec():
    print("\n[3] máximo de consecutivos sem pausa")
    segs, m = [], []
    for k in range(6):                       # 6 b-rolls seguidos (MAX_CONSEC=5)
        s = 20 + 5 * k
        segs.append(mk_seg(s, s + 3))
        m.append(mk_match(s, s + 3))
    c = rhythm.apply_rhythm(segs, m)
    check("o 6º consecutivo é bloqueado", m[5]["status"] == "blocked",
          extra=str([x["status"] for x in m]))
    check("os 5 primeiros entram", all(x["status"] == "ok" for x in m[:5]))
    check("contou blocked", c["blocked"] >= 1)


def test_price_guarantee():
    print("\n[4] momentos protegidos (preço / garantia)")
    segs = [mk_seg(20, 26, text="It's only $49 today"),
            mk_seg(40, 46, text="full 30-day money-back guarantee")]
    m = [mk_match(20, 26), mk_match(40, 46)]
    rhythm.apply_rhythm(segs, m)
    check("preço bloqueia b-roll", m[0]["status"] == "blocked" and "re" in m[0]["select_reason"].lower())
    check("garantia bloqueia b-roll", m[1]["status"] == "blocked")


def test_cta():
    print("\n[5] CTA nunca recebe b-roll")
    segs = [mk_seg(20, 26, arc="cta")]
    m = [mk_match(20, 26)]
    rhythm.apply_rhythm(segs, m)
    check("CTA → blocked", m[0]["status"] == "blocked")


def test_enum_burst():
    print("\n[6] rajada de enumeração (_enum_group) — sem gap nem limite entre irmãos")
    G = 12345
    segs = [mk_seg(20, 22.3, _enum_group=G, text="turmeric"),
            mk_seg(22.3, 24.6, _enum_group=G, text="ginger"),
            mk_seg(24.6, 26.9, _enum_group=G, text="pepper")]
    m = [mk_match(20, 22.3), mk_match(22.3, 24.6), mk_match(24.6, 26.9)]
    c = rhythm.apply_rhythm(segs, m)
    check("os 3 irmãos entram (rajada)", all(x["status"] == "ok" for x in m),
          extra=str([x["status"] for x in m]))
    check("sem empurrão entre irmãos da mesma enumeração", c["pushed"] == 0)
    check("2º irmão começa colado no 1º", abs(m[1]["start"] - m[0]["end"]) < 0.3,
          extra=f'{m[0]["end"]} vs {m[1]["start"]}')


def test_min_dur_block():
    print("\n[7] sem espaço p/ MIN_DUR após empurrar → bloqueia")
    segs = [mk_seg(20, 25), mk_seg(25.2, 26.8)]
    m = [mk_match(20, 25), mk_match(25.2, 26.8)]
    rhythm.apply_rhythm(segs, m)
    check("2º vira blocked (janela < 2s após gap)", m[1]["status"] == "blocked",
          extra=f'{m[1]["status"]} {m[1].get("select_reason","")}')


def test_split_long_segments():
    print("\n[8] split_long_segments — fatia trecho longo em vários slots")
    # trecho de 18s de narração → vários sub-slots, cada um vira uma rajada (_enum_group)
    segs = [mk_seg(0, 18, text="long narration about the same topic", arc="story")]
    profs = [{"visual_description": "topic scene"}]
    s2, p2 = bc.split_long_segments(segs, profs, density="normal")
    check("18s vira 2+ sub-slots", len(s2) >= 2, extra=str(len(s2)))
    check("alinhado com profiles", len(s2) == len(p2))
    check("sub-slots contíguos cobrindo a janela",
          abs(s2[0]["start"] - 0.0) < 1e-6 and abs(s2[-1]["end"] - 18.0) < 1e-6)
    check("marca rajada (_enum_group) p/ ritmo e isenção de piso",
          all(x.get("_enum_group") == s2[0].get("_enum_group") for x in s2)
          and s2[0].get("_enum_group") is not None)
    check("lettering só no 1º sub-slot",
          s2[0].get("lettering", None) != False and all(x.get("lettering") is False for x in s2[1:]))
    # trecho curto NÃO é fatiado
    s3, _ = bc.split_long_segments([mk_seg(0, 5)], [{"visual_description": "x"}])
    check("trecho curto (5s) não fatia", len(s3) == 1)
    # CTA não fatia (não recebe b-roll)
    s4, _ = bc.split_long_segments([mk_seg(0, 30, arc="cta")], [{"visual_description": "x"}])
    check("CTA não fatia", len(s4) == 1)
    # já é enumeração → não re-fatia
    s5, _ = bc.split_long_segments([mk_seg(0, 30, _enum_group=99)], [{"visual_description": "x"}])
    check("enumeração existente não é re-fatiada", len(s5) == 1)
    # densidade intensa gera mais cortes que calma
    si, _ = bc.split_long_segments([mk_seg(0, 24)], [{"visual_description": "x"}], density="intense")
    sc, _ = bc.split_long_segments([mk_seg(0, 24)], [{"visual_description": "x"}], density="calm")
    check("intenso corta mais que calmo", len(si) > len(sc), extra=f"intenso={len(si)} calmo={len(sc)}")


def test_split_then_rhythm():
    print("\n[9] integração: fatiar trecho longo e o ritmo mantém a rajada")
    segs = [mk_seg(20, 38, text="long topic", arc="story")]
    profs = [{"visual_description": "topic"}]
    s2, _ = bc.split_long_segments(segs, profs, density="normal")
    n = len(s2)
    m = [mk_match(x["start"], x["end"]) for x in s2]
    c = rhythm.apply_rhythm(s2, m)
    active = sum(1 for x in m if x["status"] == "ok")
    check("ritmo mantém todos os sub-slots da rajada (sem bloquear por consecutivos)",
          active == n, extra=f"{active}/{n} ativos; blocked={c['blocked']}")
    check("sem empurrão dentro da rajada", c["pushed"] == 0)


def test_dense_no_pauses():
    print("\n[10] PADRÃO denso: sem gap nem limite de consecutivos (não inventa pausa)")
    _gap, _consec, _breath = rhythm.MIN_GAP, rhythm.MAX_CONSEC, rhythm.HOOK_BREATH
    rhythm.MIN_GAP, rhythm.MAX_CONSEC, rhythm.HOOK_BREATH = 0.0, 999, 0.0
    try:
        segs, m = [], []
        for k in range(10):                       # 10 b-rolls colados, contíguos
            s = 20 + 3 * k
            segs.append(mk_seg(s, s + 3)); m.append(mk_match(s, s + 3))
        c = rhythm.apply_rhythm(segs, m)
        check("todos os 10 entram (nenhum bloqueado por consecutivos)",
              all(x["status"] == "ok" for x in m), extra=str([x["status"] for x in m]))
        check("nada empurrado (sem gap forçado)", c["pushed"] == 0)
        check("1º b-roll não espera respiro de hook", abs(m[0]["start"] - 20.0) < 1e-6,
              extra=f'{m[0]["start"]}')
    finally:
        rhythm.MIN_GAP, rhythm.MAX_CONSEC, rhythm.HOOK_BREATH = _gap, _consec, _breath


def test_protect_money_toggle():
    print("\n[11] RHYTHM_PROTECT_MONEY=0 desliga o bloqueio de preço/CTA")
    _pm = rhythm.PROTECT_MONEY
    rhythm.PROTECT_MONEY = False
    try:
        segs = [mk_seg(20, 26, text="It's only $49 today"), mk_seg(40, 46, arc="cta")]
        m = [mk_match(20, 26), mk_match(40, 46)]
        rhythm.apply_rhythm(segs, m)
        check("preço NÃO bloqueia quando desligado", m[0]["status"] == "ok",
              extra=m[0].get("select_reason", ""))
        check("CTA NÃO bloqueia quando desligado", m[1]["status"] == "ok",
              extra=m[1].get("select_reason", ""))
    finally:
        rhythm.PROTECT_MONEY = _pm


def test_max_shot_3s():
    print("\n[12] regra 'nada > 3s na tela': intenso fatia <= 3s e o ritmo respeita")
    segs = [mk_seg(0, 18, text="long topic", arc="story")]
    s2, _ = bc.split_long_segments(segs, [{"visual_description": "x"}], density="intense")
    longest_slot = max(x["end"] - x["start"] for x in s2)
    check("intenso: todo sub-slot <= 3.3s", longest_slot <= 3.4, extra=f"maior={longest_slot:.2f}")
    check("sub-slots cobrem os 18s contíguos",
          abs(s2[0]["start"]) < 1e-6 and abs(s2[-1]["end"] - 18.0) < 1e-6)
    _g, _c, _b = rhythm.MIN_GAP, rhythm.MAX_CONSEC, rhythm.HOOK_BREATH
    rhythm.MIN_GAP, rhythm.MAX_CONSEC, rhythm.HOOK_BREATH = 0.0, 999, 0.0
    try:
        m = [mk_match(x["start"], x["end"]) for x in s2]
        rhythm.apply_rhythm(s2, m, max_dur=3.0)
        oks = [x for x in m if x["status"] == "ok"]
        longest = max(x["end"] - x["start"] for x in oks)
        check("nenhum b-roll fica > 3s na tela", longest <= 3.0 + 1e-6, extra=f"maior={longest:.2f}")
        check("todos os sub-slots viram b-roll (cobertura cheia)",
              len(oks) == len(s2), extra=f"{len(oks)}/{len(s2)}")
    finally:
        rhythm.MIN_GAP, rhythm.MAX_CONSEC, rhythm.HOOK_BREATH = _g, _c, _b


if __name__ == "__main__":
    test_trim()
    test_min_gap_push()
    test_max_consec()
    test_price_guarantee()
    test_cta()
    test_enum_burst()
    test_min_dur_block()
    test_split_long_segments()
    test_split_then_rhythm()
    test_dense_no_pauses()
    test_protect_money_toggle()
    test_max_shot_3s()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
