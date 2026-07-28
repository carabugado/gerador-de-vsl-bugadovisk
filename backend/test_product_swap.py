"""
Testes da Troca de Produto (product_swap) — lógica pura, sem torch/CLIP/ffmpeg.
Rodar:  python test_product_swap.py
"""
import product_swap as ps

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


# ── 1. Léxico e normalização ──────────────────────────────────────────────────

def test_terms():
    print("\n[1] léxico de formatos + nome do produto")
    t = ps.form_terms("capsulas")
    check("capsulas tem 'capsula'", "capsula" in t)
    check("capsulas tem 'pills' (en)", "pills" in t)
    t2 = ps.form_terms("Cápsulas")            # com acento e maiúscula
    check("acento/caixa não muda o léxico", t2 == t)
    t3 = ps.form_terms("gotas", "FloraLife Max")
    check("nome do produto entra", "floralife max" in t3)
    check("palavra forte do nome entra", "floralife" in t3)
    t4 = ps.form_terms("formato-desconhecido")
    check("formato livre vira termo literal", "formato-desconhecido" in t4)


# ── 2. Detecção de menções na narração ────────────────────────────────────────

def test_audio_detection():
    print("\n[2] menções de áudio")
    segs = [
        {"start": 0.0, "end": 4.0, "text": "Bem-vindo ao vídeo de hoje."},
        {"start": 4.0, "end": 9.0, "text": "Tome duas cápsulas por dia com água.",
         "words": [
             {"word": "Tome", "start": 4.0, "end": 4.3},
             {"word": "duas", "start": 4.3, "end": 4.6},
             {"word": "cápsulas", "start": 4.6, "end": 5.2},
             {"word": "por", "start": 5.2, "end": 5.4},
             {"word": "dia", "start": 5.4, "end": 5.7},
         ]},
        {"start": 9.0, "end": 13.0, "text": "Ela encapsula todo o poder da natureza."},
        {"start": 13.0, "end": 17.0, "text": "Take two capsules every morning."},
    ]
    m = ps.detect_audio_mentions(segs, "capsulas")
    check("2 menções (pt + en)", len(m) == 2, extra=str(len(m)))
    check("'encapsula' NÃO casa (palavra inteira)",
          all("encapsula" not in (x.get("matched_terms") or []) for x in m))
    first = m[0]
    check("tempo preciso pela palavra", first["precise"] and
          abs(first["start"] - (4.6 - ps.MENTION_PAD)) < 1e-6,
          extra=f"{first['start']}")
    check("fim cobre a palavra + folga",
          abs(first["end"] - (5.2 + ps.MENTION_PAD)) < 1e-6)
    second = m[1]
    check("sem words → janela do segmento", not second["precise"] and
          second["seg_start"] == 13.0)
    # nome do produto
    m2 = ps.detect_audio_mentions(
        [{"start": 0, "end": 3, "text": "O segredo do VitaMax é a pureza."}],
        "gotas", "VitaMax")
    check("nome do produto casa mesmo sem formato", len(m2) == 1)


# ── 3. Agrupamento de hits visuais em zonas ───────────────────────────────────

def test_grouping():
    print("\n[3] agrupamento de frames em zonas")
    # hits a cada 1s: 10-14s contíguo; buraco de 2s (tolerado); 30s isolado
    hits = [(10.0, 0.5), (11.0, 0.6), (12.0, 0.55), (14.0, 0.5), (30.0, 0.7)]
    zones = ps.group_hits(hits, interval=1.0, min_zone=0.8, pad=0.5, gap_mult=2.2)
    check("2 zonas", len(zones) == 2, extra=str(len(zones)))
    z0 = zones[0]
    check("zona 1 começa com folga", abs(z0["start"] - 9.5) < 1e-6, extra=str(z0["start"]))
    check("zona 1 termina após o último hit + frame + folga",
          abs(z0["end"] - 15.5) < 1e-6, extra=str(z0["end"]))
    check("buraco de 2s não quebra a zona", z0["hits"] == 4)
    check("hit isolado vira zona própria", zones[1]["hits"] == 1)
    # gap maior que a tolerância quebra
    zones2 = ps.group_hits([(10.0, 0.5), (13.5, 0.5)], interval=1.0)
    check("gap 3.5s quebra em 2 zonas", len(zones2) == 2)
    # zona curta demais cai
    zones3 = ps.group_hits([(10.0, 0.5)], interval=0.1, min_zone=2.0, pad=0.0)
    check("zona menor que min_zone cai", len(zones3) == 0)


# ── 4. Merge áudio + visual ───────────────────────────────────────────────────

def test_merge():
    print("\n[4] merge de zonas visuais + menções")
    vis = [{"source": "visual", "start": 10.0, "end": 16.0, "score": 0.6, "hits": 5}]
    aud = [
        {"source": "audio", "start": 12.0, "end": 13.0, "seg_start": 11, "seg_end": 14,
         "text": "nossas cápsulas", "matched_terms": ["capsulas"], "precise": True},
        {"source": "audio", "start": 40.0, "end": 41.0, "seg_start": 39, "seg_end": 42,
         "text": "duas cápsulas ao dia", "matched_terms": ["capsulas"], "precise": True},
    ]
    merged = ps.merge_zones(vis, aud)
    check("3 itens no total", len(merged) == 3)
    both = [z for z in merged if z["type"] == "both"]
    check("menção dentro da zona marca 'both'", len(both) == 1 and both[0]["start"] == 10.0)
    check("menção fora fica 'audio'",
          any(z["type"] == "audio" and z["start"] == 40.0 for z in merged))
    check("ordenado por tempo",
          [z["start"] for z in merged] == sorted(z["start"] for z in merged))


# ── 5. Plano de corte do render ───────────────────────────────────────────────

def test_spans():
    print("\n[5] plano de corte (spans)")
    zones = [
        {"type": "visual", "start": 10.0, "end": 15.0, "replacement_path": "/n/a.mp4"},
        {"type": "audio", "start": 20.0, "end": 21.0},                    # marcador — não corta
        {"type": "visual", "start": 30.0, "end": 33.0, "replacement_path": "/n/b.mp4"},
        {"type": "visual", "start": 50.0, "end": 55.0},                    # sem clipe — não corta
        {"type": "visual", "start": 58.0, "end": 60.0, "replacement_path": "/n/c.mp4",
         "status": "rejected"},                                            # rejeitada — não corta
    ]
    spans = ps.build_timeline_spans(zones, duration=60.0)
    kinds = [(s["type"], s["start"], s["end"]) for s in spans]
    check("alterna keep/swap cobrindo tudo", kinds == [
        ("keep", 0.0, 10.0), ("swap", 10.0, 15.0),
        ("keep", 15.0, 30.0), ("swap", 30.0, 33.0),
        ("keep", 33.0, 60.0)], extra=str(kinds))
    total = sum(s["end"] - s["start"] for s in spans)
    check("duração total preservada (áudio síncrono)", abs(total - 60.0) < 1e-6)
    # zonas sobrepostas não duplicam tempo
    zo = [
        {"type": "visual", "start": 5.0, "end": 12.0, "replacement_path": "/n/a.mp4"},
        {"type": "both", "start": 10.0, "end": 15.0, "replacement_path": "/n/b.mp4"},
    ]
    sp2 = ps.build_timeline_spans(zo, duration=20.0)
    total2 = sum(s["end"] - s["start"] for s in sp2)
    check("sobreposição não duplica tempo", abs(total2 - 20.0) < 1e-6,
          extra=str([(s['type'], s['start'], s['end']) for s in sp2]))
    # zona além do fim é clampada
    sp3 = ps.build_timeline_spans(
        [{"type": "visual", "start": 55.0, "end": 90.0, "replacement_path": "/n/a.mp4"}], 60.0)
    check("zona clampada na duração", sp3[-1]["end"] == 60.0)


# ── 6. Escolha do clipe substituto ────────────────────────────────────────────

def test_replacements():
    print("\n[6] plano de substituição")
    zones = [
        {"type": "visual", "start": 10.0, "end": 14.0},    # 4s
        {"type": "audio", "start": 20.0, "end": 21.0},
        {"type": "visual", "start": 30.0, "end": 34.0},    # 4s
    ]
    clips = [
        {"path": "/novo/curto.mp4", "filename": "curto.mp4", "duration": 2.0},
        {"path": "/novo/longo.mp4", "filename": "longo.mp4", "duration": 8.0},
        {"path": "/novo/medio.mp4", "filename": "medio.mp4", "duration": 6.0},
    ]
    n = ps.plan_replacements(zones, clips)
    check("2 zonas visuais planejadas", n == 2)
    check("zona de áudio não recebe clipe", "replacement_path" not in zones[1])
    check("escolhe clipe que cobre a zona",
          zones[0]["replacement_path"] in ("/novo/longo.mp4", "/novo/medio.mp4"),
          extra=zones[0]["replacement_path"])
    check("rodízio: zonas vizinhas não repetem o mesmo clipe",
          zones[0]["replacement_path"] != zones[2]["replacement_path"],
          extra=f'{zones[0]["replacement_path"]} vs {zones[2]["replacement_path"]}')
    check("candidatos expostos p/ troca manual", len(zones[0]["candidates"]) >= 2)
    check("sem clips → 0 planejado", ps.plan_replacements(zones, []) == 0)


# ── 7. Comando ffmpeg do render ───────────────────────────────────────────────

def test_ffmpeg_cmd():
    print("\n[7] comando ffmpeg")
    zones = [
        {"type": "visual", "start": 10.0, "end": 15.0, "replacement_path": "/novo/a.mp4"},
    ]
    spans = ps.build_timeline_spans(zones, duration=30.0)
    cmd = ps.build_ffmpeg_command("/vsl/video.mp4", spans, zones, "/out/final.mp4",
                                  1920, 1080, "30/1")
    joined = " ".join(cmd)
    check("input original primeiro", cmd[cmd.index("-i") + 1] == "/vsl/video.mp4")
    check("clipe novo vira input", "/novo/a.mp4" in cmd)
    graph = cmd[cmd.index("-filter_complex") + 1]
    check("concat de 3 trechos", "concat=n=3:v=1:a=0" in graph, extra=graph[-80:])
    check("trecho original com trim", "trim=start=0.0:end=10.0" in graph)
    check("clipe novo escalado pro quadro", "scale=1920:1080" in graph)
    check("tpad clona se o clipe for curto", "tpad=stop_mode=clone" in graph)
    check("áudio original mapeado intacto", "0:a?" in cmd and "copy" in cmd)
    check("saída no fim", cmd[-1] == "/out/final.mp4")


# ── 8. Prompts visuais ────────────────────────────────────────────────────────

def test_prompts():
    print("\n[8] prompts visuais")
    p = ps.visual_prompts("capsulas", "VitaMax")
    check("prompts do formato", any("capsule" in x for x in p))
    check("nome do produto no prompt", any("VitaMax" in x for x in p))
    p2 = ps.visual_prompts("elixir raro")
    check("formato livre gera prompt genérico", any("elixir raro" in x for x in p2))


if __name__ == "__main__":
    test_terms()
    test_audio_detection()
    test_grouping()
    test_merge()
    test_spans()
    test_replacements()
    test_ffmpeg_cmd()
    test_prompts()
    print("\n" + ("❌ FALHAS: " + ", ".join(_fails) if _fails else "✅ Tudo passou."))
    raise SystemExit(1 if _fails else 0)
