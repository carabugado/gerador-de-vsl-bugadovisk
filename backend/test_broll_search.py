"""
Testes da busca semântica por embeddings (Fase 2).
Rodar:  python test_broll_search.py
Mocka embed_text para não carregar o CLIP — testa o SCORING/seleção.
"""
import numpy as np
import broll_search as bs

# Isola dos testes: o viés de estilo (L2) é testado à parte (test [9], passando
# style_emb direto pro search). Aqui desligamos pra não puxar a memória real do usuário.
bs.STYLE_ENABLED = False
# Estes testes exercitam o scoring por FRAMES (fallback CLIP). O casamento por TAGS
# (#Fase1) é validado ao vivo no índice real — aqui desligamos pra não interferir.
bs._tag_doc = lambda *a, **k: ""

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


# vetores unitários simples em 3D pra controlar a similaridade
def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-8)

JAR   = _unit([1, 0, 0])   # "abrir pote"
STAIR = _unit([0, 1, 0])   # "subir escada"
MIX   = _unit([1, 1, 0])   # meio termo


def _asset(path, frames, name_vec, dur=4.0, tags=None):
    return {"path": path, "filename": path.split("/")[-1],
            "clean_name": path.split("/")[-1], "duration": dur,
            "frame_embeddings": [list(map(float, f)) for f in frames],
            "visual_embedding": list(map(float, _unit(np.mean(frames, axis=0)))),
            "name_embedding": list(map(float, name_vec)),
            "_source": "project", "tags": tags or {}}


def _patch_query(vec):
    bs.embed_text = lambda texts: np.array([vec])
    # também o load_tags usado dentro do search (evita I/O)
    bs.load_tags = lambda p: None


def test_best_frame():
    print("\n[1] Best-frame matching")
    _patch_query(JAR)
    assets = [
        # clip cujo frame do meio MOSTRA o pote (best-frame alto), média diluída
        _asset("/a/jar.mp4", [STAIR, JAR, STAIR], STAIR),
        # clip de escada (nenhum frame casa com 'jar')
        _asset("/a/stairs.mp4", [STAIR, STAIR, STAIR], STAIR),
    ]
    res = bs.search("open a jar", assets, top_k=3)
    check("jar vence pelo best-frame", res[0]["path"] == "/a/jar.mp4", extra=str([r["path"] for r in res]))
    check("jar tem visual_similarity alta (~1.0)", res[0]["visual_similarity"] > 0.9)
    check("stairs fica abaixo", res[1]["score"] < res[0]["score"])


def test_bonuses():
    print("\n[2] Bônus/penalidades")
    _patch_query(JAR)
    base = _asset("/lib/WL/jar.mp4", [JAR, JAR, JAR], JAR)
    # repetição na sessão penaliza -0.15
    s_fresh = bs.search("jar", [base], top_k=1)[0]["score"]
    s_used  = bs.search("jar", [base], top_k=1, used={"/lib/WL/jar.mp4"})[0]["score"]
    check("repetição reduz ~0.15", abs((s_fresh - s_used) - 0.15) < 1e-4, extra=f"{s_fresh}->{s_used}")
    # bônus de subpasta da vertical (+0.05)
    s_vert = bs.search("jar", [base], top_k=1, vertical="WL")[0]["score"]
    check("vertical na subpasta soma ~0.05", abs((s_vert - s_fresh) - 0.05) < 1e-4)
    # duração ruim penaliza
    longclip = _asset("/a/long.mp4", [JAR, JAR, JAR], JAR, dur=30)
    s_long = bs.search("jar", [longclip], top_k=1)[0]["score"]
    check("duração>10s penaliza ~0.05", abs((s_fresh - s_long) - 0.05) < 1e-4, extra=f"{s_fresh} vs {s_long}")


def test_select_and_threshold():
    print("\n[3] select() + limiar de geração")
    # mock ciente do conteúdo: 'jar' → JAR, senão STAIR (ortogonal ao asset de pote)
    bs.embed_text = lambda texts: np.array([JAR if "jar" in texts[0].lower() else STAIR])
    bs.load_tags = lambda p: None
    bs.GEN_THRESHOLD = 0.25
    bs.OK_THRESHOLD = 0.30
    segs = [
        {"text": "struggling to open a jar", "start": 0, "end": 5, "visual_query": "open jar"},
        {"text": "totally unrelated topic", "start": 6, "end": 11, "visual_query": "spaceship"},
    ]
    queries = ["elderly hands opening a glass jar lid", "spaceship in deep space"]
    assets = [_asset("/a/jar.mp4", [JAR, JAR, JAR], JAR)]   # só existe o clip do pote
    ranked, matches = bs.select(segs, queries, assets, vertical="")
    check("seg0 casa com o jar (ok/review)", matches[0]["status"] in ("ok", "review"))
    check("seg0 broll_path setado", matches[0]["broll_path"] == "/a/jar.mp4")
    check("seg1 sem match → no_broll (gerar)", matches[1]["status"] == "no_broll")
    check("seg1 vira prompt de geração literal", segs[1]["ugc_prompt"] == "spaceship in deep space")
    check("ranked tem candidates pro seg0", len(ranked[0]["candidates"]) >= 1)


def test_rerank_hook():
    print("\n[4] rerank_fn (gancho do Vision)")
    _patch_query(JAR)
    segs = [{"text": "open jar", "start": 0, "end": 5, "visual_query": "open jar"}]
    # 2 candidatos; o rerank inverte a ordem
    a = _asset("/a/1.mp4", [JAR, JAR, JAR], JAR)
    b = _asset("/a/2.mp4", [MIX, MIX, MIX], MIX)
    calls = {"n": 0}
    def fake_rerank(seg, q, cands):
        calls["n"] += 1
        return list(reversed(cands))
    # profile de tom sensível → segmento entra como "risco" e o gancho dispara
    ranked, matches = bs.select(segs, ["open jar"], [a, b], rerank_fn=fake_rerank,
                                profiles=[{"block_type": "problem"}])
    check("rerank_fn foi chamado", calls["n"] == 1)
    check("ordem foi alterada pelo rerank", ranked[0]["candidates"][0]["path"] == "/a/2.mp4")


def test_sequence_awareness():
    print("\n[5] M2 — consciência de sequência (não repetir cena)")
    _patch_query(JAR)
    a = _asset("/a/jar1.mp4", [JAR, JAR, JAR], JAR)   # idêntico ao já escolhido
    b = _asset("/a/jar2.mp4", [MIX, MIX, MIX], MIX)   # diferente
    base = {r["path"]: r["score"] for r in bs.search("jar", [a, b], top_k=2)}
    gap_base = base["/a/jar1.mp4"] - base["/a/jar2.mp4"]
    res = {r["path"]: r["score"] for r in bs.search("jar", [a, b], top_k=2,
                                                    prev_embeddings=[list(JAR)])}
    check("clip idêntico ao escolhido é penalizado (-0.30)",
          abs((base["/a/jar1.mp4"] - res["/a/jar1.mp4"]) - 0.30) < 1e-4,
          extra=f"{base['/a/jar1.mp4']}->{res['/a/jar1.mp4']}")
    gap_prev = res["/a/jar1.mp4"] - res["/a/jar2.mp4"]
    check("penalização aproxima o clip diferente (gap cai ~0.25)",
          (gap_base - gap_prev) > 0.2, extra=f"gap {gap_base:.3f}->{gap_prev:.3f}")


def test_diversity_mmr():
    print("\n[6] M3 — diversidade MMR no top-K")
    _patch_query(JAR)
    # 3 clips quase idênticos (todos JAR) + 1 diferente (MIX)
    clips = [
        _asset("/a/j1.mp4", [JAR, JAR, JAR], JAR),
        _asset("/a/j2.mp4", [JAR, JAR, JAR], JAR),
        _asset("/a/j3.mp4", [JAR, JAR, JAR], JAR),
        _asset("/a/diff.mp4", [MIX, MIX, MIX], MIX),
    ]
    div = bs.search("jar", clips, top_k=3, diversify=True, diversity_threshold=0.75)
    paths = [r["path"] for r in div]
    check("top-3 inclui o clip diferente (não 3 iguais)", "/a/diff.mp4" in paths, extra=str(paths))
    nodiv = bs.search("jar", clips, top_k=3, diversify=False)
    check("sem diversidade, top-3 são os 3 idênticos",
          "/a/diff.mp4" not in [r["path"] for r in nodiv])


def test_selective_vision():
    print("\n[7] #3 — verificação de visão SELETIVA")
    good = [{"score": 0.90}, {"score": 0.85}]          # score alto + margem clara (escala tags)
    check("tom 'problem' → risco", bs._is_risky({"block_type": "problem"}, good) is True)
    check("emoção 'frustration' → risco", bs._is_risky({"emotion": "frustration"}, good) is True)
    check("score baixo → risco",
          bs._is_risky({"block_type": "proof"}, [{"score": 0.60}, {"score": 0.50}]) is True)
    check("empate apertado → risco",
          bs._is_risky({"block_type": "proof"}, [{"score": 0.90}, {"score": 0.89}]) is True)
    check("positivo + score alto + margem clara → NÃO verifica",
          bs._is_risky({"block_type": "proof", "emotion": "confidence"}, good) is False)
    check("sem candidatos → risco (deixa o gate decidir)",
          bs._is_risky({"block_type": "proof"}, []) is True)

    # Integração: o rerank de visão só roda nos segmentos de risco.
    _patch_query(JAR)
    segs = [{"start": 0, "end": 4, "text": "problema"},
            {"start": 4, "end": 8, "text": "prova"}]
    profiles = [{"block_type": "problem", "emotion": "frustration"},
                {"block_type": "proof", "emotion": "confidence"}]
    fixed = [{"path": "/a/1.mp4", "filename": "1.mp4", "clean_name": "1", "duration": 4.0,
              "score": 0.90, "visual_similarity": 0.90, "name_match": 0.0, "source": "project"},
             {"path": "/a/2.mp4", "filename": "2.mp4", "clean_name": "2", "duration": 4.0,
              "score": 0.85, "visual_similarity": 0.85, "name_match": 0.0, "source": "project"}]
    orig_search = bs.search
    bs.search = lambda *a, **k: [dict(c) for c in fixed]
    calls = []
    def fake_rerank(seg, q, cands):
        calls.append(seg["text"])
        for c in cands:
            c["vision_score"] = 8
        return cands
    try:
        bs.select(segs, ["q1", "q2"], [], rerank_fn=fake_rerank, profiles=profiles)
    finally:
        bs.search = orig_search
    check("visão rodou no trecho de PROBLEMA (dano alto)", "problema" in calls)
    check("visão NÃO rodou no trecho positivo+confiante", "prova" not in calls)
    check("verificou só 1 de 2 segmentos", len(calls) == 1, extra=str(calls))


def test_exclude_timeline():
    print("\n[8] #2a — exclui B-roll já na timeline")
    _patch_query(JAR)
    segs = [{"start": 0, "end": 5, "text": "open jar", "visual_query": "open jar"}]
    a = _asset("/a/used.mp4", [JAR, JAR, JAR], JAR)   # melhor match, mas JÁ na timeline
    b = _asset("/a/free.mp4", [MIX, MIX, MIX], MIX)   # pior match, mas livre
    # sem exclusão: o melhor (used) vence
    r0, m0 = bs.select(segs, ["open jar"], [a, b])
    check("sem exclusão, escolhe o melhor (used)", m0[0]["broll_path"] == "/a/used.mp4")
    # com exclusão: 'used' some do pool → escolhe o livre
    r1, m1 = bs.select(segs, ["open jar"], [a, b], exclude_paths={"/a/used.mp4"})
    paths = [c["path"] for c in r1[0]["candidates"]]
    check("excluído não aparece nos candidatos", "/a/used.mp4" not in paths, extra=str(paths))
    check("escolhe o B-roll livre", m1[0]["broll_path"] == "/a/free.mp4", extra=str(m1[0]["broll_path"]))


def test_style_bonus():
    print("\n[9] #L2 — bônus de estilo (memória)")
    _patch_query(MIX)   # query no meio: jar e stair empatariam ~igual
    jar   = _asset("/a/jar.mp4",   [JAR, JAR, JAR], JAR)
    stair = _asset("/a/stair.mp4", [STAIR, STAIR, STAIR], STAIR)
    # sem estilo: query MIX favorece levemente quem? ambos ~0.707; pega o 1º estável
    base = bs.search("algo", [jar, stair], top_k=2, diversify=False)
    # com estilo apontando pra STAIR (escolha passada), stair deve subir e vencer
    r = bs.search("algo", [jar, stair], top_k=2, diversify=False,
                  style_emb=_unit([0, 1, 0]), style_w=1.0)
    top = r[0]["path"]
    check("estilo (STAIR) faz o clip de escada vencer", top == "/a/stair.mp4", extra=str([x['path'] for x in r]))
    # style_w=0 (sem confiança) → sem efeito
    r0 = bs.search("algo", [jar, stair], top_k=2, diversify=False,
                   style_emb=_unit([0, 1, 0]), style_w=0.0)
    check("style_w=0 → sem bônus (igual ao base)",
          [x["path"] for x in r0] == [x["path"] for x in base], extra=str([x['path'] for x in r0]))


def test_enum_exemption():
    print(f"\n[10] sub-slot de enumeração isento do piso ({bs.MIN_BROLL_DURATION}s, MIN_BROLL_DURATION)")
    bs.embed_text = lambda texts: np.array([JAR])
    bs.load_tags = lambda p: None
    asset = _asset("/a/jar.mp4", [JAR, JAR, JAR], JAR, dur=4.0)
    # trecho curtíssimo (1.5s, abaixo do piso) SEM marca de enumeração → pulado
    plain = [{"text": "x", "start": 0.0, "end": 1.5, "visual_query": "jar"}]
    r0, m0 = bs.select(plain, ["jar"], [asset])
    check("trecho curto comum (< piso) é pulado",
          r0[0]["skip"] is True and m0[0]["status"] == "skip")
    # mesmo trecho curto MAS marcado como rajada ("3 ingredientes") → NÃO pula
    enum = [{"text": "turmeric", "start": 0.0, "end": 1.5,
             "visual_query": "turmeric", "_enum_group": 123}]
    r1, m1 = bs.select(enum, ["turmeric root"], [asset])
    check("sub-slot de enumeração (< piso) NÃO é pulado",
          r1[0]["skip"] is False, extra=str(r1[0]))
    check("sub-slot de enumeração recebe candidato",
          len(r1[0]["candidates"]) >= 1 and m1[0]["status"] != "skip")


def test_ed_pool_primary():
    print("\n[11] vertical ED: pasta +18 é o pool PRIMÁRIO em TODO segmento")
    V = _unit([1, 0, 0])
    bs.embed_text = lambda texts: np.array([V])
    bs.load_tags = lambda p: None
    ed = _asset("/ed/clip.mp4", [V], V, dur=6.0)
    ed["_source"] = "ed"; ed["_local_only"] = True
    lib = _asset("/lib/other.mp4", [V], V, dur=6.0)   # biblioteca com MESMO score
    pool = [ed, lib]
    # tanto fala íntima quanto não-íntima escolhem a pasta +18 (não a biblioteca)
    for label, txt in [("íntimo", "fuck my wife entire night"),
                       ("não-íntimo", "take this every single morning")]:
        r, _ = bs.select([{"text": txt, "start": 0.0, "end": 5.0, "visual_query": txt}],
                         [txt], pool, vertical="ED")
        check(f"{label}: escolhe a pasta +18 (não a biblioteca)",
              r[0]["candidates"] and r[0]["candidates"][0]["path"] == "/ed/clip.mp4",
              extra=str([c["path"] for c in r[0]["candidates"]]))


def test_ed_guaranteed_fill():
    print("\n[12] vertical ED: trecho sem opção na biblioteca é PREENCHIDO com ED+ (não vazio)")
    SEX = _unit([1, 0, 0]); PILLS = _unit([0, 1, 0])
    ed = _asset("/ed/x.mp4", [SEX], SEX, dur=6.0)
    ed["_source"] = "ed"; ed["_local_only"] = True
    lib = _asset("/lib/weak.mp4", [PILLS], PILLS, dur=6.0)   # biblioteca, score fraco
    pool = [lib, ed]
    bs.load_tags = lambda p: None
    real_search = bs.search
    _gen, _ok = bs.GEN_THRESHOLD, bs.OK_THRESHOLD     # outros testes mexem nisso
    bs.GEN_THRESHOLD, bs.OK_THRESHOLD = 0.58, 0.82    # 0.40 da biblioteca fica abaixo → fill

    def fake_search(query, brolls, **kw):
        srcs = {b["path"] for b in brolls}
        # busca primária (pool cheio): melhor da biblioteca é FRACO (< limiar), não-local
        if "/lib/weak.mp4" in srcs and "/ed/x.mp4" in srcs:
            return [{"path": "/lib/weak.mp4", "filename": "weak.mp4", "duration": 6.0,
                     "score": 0.40, "source": "project", "visual_similarity": 0.4, "tag_sim": 0.0}]
        # _ed_fill (só pool ED+): devolve o clipe da pasta +18 pra preencher
        if srcs == {"/ed/x.mp4"}:
            return [{"path": "/ed/x.mp4", "filename": "x.mp4", "duration": 6.0, "score": 0.30,
                     "source": "ed", "_local_only": True, "visual_similarity": 0.3, "tag_sim": 0.0}]
        return []
    bs.search = fake_search
    try:
        seg = [{"text": "pills full of side effects", "start": 0.0, "end": 5.0,
                "visual_query": "pills"}]
        _, m = bs.select(seg, ["pills"], pool, vertical="ED")
        check("não-íntimo sem opção boa → preenchido com ED+ (status != no_broll)",
              m[0]["status"] != "no_broll" and m[0]["broll_path"] == "/ed/x.mp4",
              extra=f'{m[0]["status"]} {m[0].get("broll_path")}')
    finally:
        bs.search = real_search
        bs.GEN_THRESHOLD, bs.OK_THRESHOLD = _gen, _ok


if __name__ == "__main__":
    test_best_frame()
    test_bonuses()
    test_select_and_threshold()
    test_rerank_hook()
    test_sequence_awareness()
    test_diversity_mmr()
    test_selective_vision()
    test_exclude_timeline()
    test_style_bonus()
    test_enum_exemption()
    test_ed_pool_primary()
    test_ed_guaranteed_fill()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
