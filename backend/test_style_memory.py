"""
Testes da memória de estilo (#L1). Mocka CLIP (embed_text / frame / image embed).
Rodar:  python test_style_memory.py
"""
import tempfile
from pathlib import Path
import numpy as np

import broll_index
import style_memory

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


# embed determinístico: "jar" → eixo x; senão → eixo y (unitários, como o CLIP)
broll_index.embed_text = lambda texts: np.array(
    [[1.0, 0.0, 0.0] if "jar" in t.lower() else [0.0, 1.0, 0.0] for t in texts],
    dtype=np.float32)
broll_index._extract_frame_ffmpeg = lambda path, ts: "IMG"          # não-None
broll_index._embed_images_batched = lambda imgs: np.array([[0.0, 0.0, 1.0]], dtype=np.float32)

style_memory._MEM_PATH = Path(tempfile.mktemp(suffix=".json"))


def test_pairs_from_timeline():
    print("\n[1] pairs_from_timeline — alinhamento tempo→texto")
    brolls = [
        {"path": "/a/jar.mp4", "seq_start": 1.0, "seq_end": 4.0, "in_point": 0, "track": 1},
        {"path": "/a/none.mp4", "seq_start": 50.0, "seq_end": 52.0, "track": 1},  # sem narração
    ]
    narr = [{"start": 0.5, "end": 4.5, "text": "opening a jar"},
            {"start": 20, "end": 22, "text": "outra coisa"}]
    pairs = style_memory.pairs_from_timeline(brolls, narr)
    check("1 par (só o que tem narração sobreposta)", len(pairs) == 1, extra=str(pairs))
    check("texto correto", pairs[0]["text"] == "opening a jar")
    check("path correto", pairs[0]["broll_path"] == "/a/jar.mp4")


def test_add_and_query():
    print("\n[2] add_examples (dedup) + query")
    pairs = [{"text": "opening a jar", "broll_path": "/a/jar.mp4", "in_point": 0}]
    n1 = style_memory.add_examples(pairs, project_name="Projeto A")
    check("adicionou 1", n1 == 1)
    n2 = style_memory.add_examples(pairs, project_name="Projeto A")
    check("dedup: 0 na segunda vez", n2 == 0)

    items = style_memory.examples()
    check("guardou text_emb e visual_emb", items[0].get("text_emb") and items[0].get("visual_emb"))
    st = style_memory.stats()
    check("banco cumulativo: 1 exemplo, 1 projeto", st["examples"] == 1 and st["projects"] == 1, extra=str(st))
    check("project_learned True", style_memory.project_learned("Projeto A") is True)

    res = style_memory.query("how to open the jar", top_k=1)
    check("query acha o vizinho certo", res and res[0]["broll_path"] == "/a/jar.mp4", extra=str(res))
    check("similaridade ~1.0 (mesmo eixo)", res and res[0]["similarity"] > 0.99)

    # texto de outro tema não casa forte
    res2 = style_memory.query("a person walking", top_k=1)
    check("tema diferente → similaridade baixa", res2 and res2[0]["similarity"] < 0.5, extra=str(res2))


def test_quality_filter():
    print("\n[3] filtro de qualidade — pula efeito/curto/sem-arquivo")
    narr = [{"start": 0.0, "end": 60.0, "text": "narração cobrindo tudo"}]
    clips = [
        {"path": "/b/elderly-hands.mp4", "seq_start": 1.0, "seq_end": 5.0, "track": 1},   # ok
        {"path": "/b/OUT FILMBURNS 26.mov", "seq_start": 6.0, "seq_end": 6.4, "track": 1}, # efeito (nome+curto)
        {"path": "/b/Brilho Reveal.mov", "seq_start": 7.0, "seq_end": 10.0, "track": 1},   # efeito (nome)
        {"path": "/b/quick.mp4", "seq_start": 11.0, "seq_end": 12.0, "track": 1},          # curto (<2.5s)
        {"path": "", "name": "title", "seq_start": 13.0, "seq_end": 18.0, "track": 1},     # sem arquivo
        {"path": "/b/senior-park.mp4", "seq_start": 20.0, "seq_end": 24.0, "track": 1},    # ok
    ]
    pairs = style_memory.pairs_from_timeline(clips, narr)
    paths = sorted(p["broll_path"] for p in pairs)
    check("só os 2 B-rolls de conteúdo passam", paths == ["/b/elderly-hands.mp4", "/b/senior-park.mp4"], extra=str(paths))


def test_reset():
    print("\n[4] reset da memória")
    before = style_memory.reset()
    check("reset retorna o que havia", isinstance(before, dict) and "examples" in before)
    check("memória zerada", style_memory.stats() == {"examples": 0, "projects": 0})


if __name__ == "__main__":
    test_pairs_from_timeline()
    test_add_and_query()
    test_quality_filter()
    test_reset()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
