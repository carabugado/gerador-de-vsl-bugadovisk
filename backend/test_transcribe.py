"""
Testes do parser de transcrição (.srt do Premiere → segmentos em tempo de sequência).
Rodar:  python test_transcribe.py
"""
import transcribe as T
from transcribe import parse_srt_text

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


SRT = """1
00:00:01,000 --> 00:00:04,500
Você não conseguia abrir um pote de vidro.

2
00:00:05,000 --> 00:00:08,250
Mas isso mudou em três semanas.
"""

VTT = """WEBVTT

00:00:02.000 --> 00:00:06.000
Scientists discovered a tiny protein.
"""


def test_srt():
    print("\n[1] parse_srt_text — SRT")
    segs = parse_srt_text(SRT)
    check("2 blocos", len(segs) == 2, extra=str(len(segs)))
    check("start/end do bloco 1", segs[0]["start"] == 1.0 and segs[0]["end"] == 4.5)
    check("texto sem número/timecode", segs[0]["text"] == "Você não conseguia abrir um pote de vidro.")
    check("bloco 2 em tempo de sequência", segs[1]["start"] == 5.0 and segs[1]["end"] == 8.25)


def test_vtt_and_empty():
    print("\n[2] VTT e entradas inválidas")
    segs = parse_srt_text(VTT)
    check("VTT parseia 1 bloco", len(segs) == 1 and "protein" in segs[0]["text"])
    check("WEBVTT não vira texto", "WEBVTT" not in segs[0]["text"])
    check("vazio → []", parse_srt_text("") == [])
    check("lixo sem timecode → []", parse_srt_text("sem timecode aqui") == [])


def test_word_anchor():
    print("\n[3] word_anchor — ancora o b-roll na palavra-chave")
    words = [{"word": "you", "start": 10.0, "end": 10.2},
             {"word": "feel", "start": 10.3, "end": 10.5},
             {"word": "pain", "start": 11.0, "end": 11.3},
             {"word": "knee", "start": 11.5, "end": 11.9}]
    a = T.word_anchor(words, "person holding their knee", 10.0, 16.0)
    check("ancora na palavra 'knee' (11.5)", a == 11.5, extra=str(a))
    a2 = T.word_anchor([{"word": "knee", "start": 10.1, "end": 10.4}], "knee closeup", 10.0, 16.0)
    check("palavra-chave já no começo → None", a2 is None, extra=str(a2))
    a3 = T.word_anchor(words, "person holding their knee", 10.0, 13.0)
    check("sem espaço mínimo (knee@11.5 > 13-3) → None", a3 is None, extra=str(a3))
    a4 = T.word_anchor(words, "doctor in white coat", 10.0, 16.0)
    check("sem token casável → None", a4 is None)
    check("sem palavras → None", T.word_anchor([], "knee", 0, 10) is None)


def test_composition_clip():
    print("\n[4] transcribe_composition — recorta na borda do corte + remapeia palavras")
    orig_tr, orig_exists = T.transcribe, T.os.path.exists
    T.transcribe = lambda path, use_cache=True: [{
        "start": 4.0, "end": 7.0, "text": "crosses the cut",
        "words": [{"word": "crosses", "start": 4.2, "end": 4.6},
                  {"word": "cut", "start": 6.5, "end": 6.9}],
    }]
    T.os.path.exists = lambda p: True
    try:
        clips = [{"path": "/a/n.mov", "seq_start": 100.0, "in_point": 3.0, "out_point": 5.0}]
        out = T.transcribe_composition(clips, use_cache=False)
    finally:
        T.transcribe, T.os.path.exists = orig_tr, orig_exists
    check("1 segmento mapeado", len(out) == 1, extra=str(out))
    seg = out[0]
    # origem [4,7] ∩ corte [in3,out5] = [4,5]; offset = 100-3 = 97 → [101,102]
    check("não vaza além do corte (end=102)", abs(seg["end"] - 102.0) < 1e-6, extra=str(seg))
    check("start mapeado (101)", abs(seg["start"] - 101.0) < 1e-6)
    ws = seg.get("words", [])
    check("palavra fora do corte ('cut') removida", len(ws) == 1 and ws[0]["word"] == "crosses", extra=str(ws))
    check("palavra remapeada p/ sequência (101.2)", abs(ws[0]["start"] - 101.2) < 1e-6)


def test_make_result_anchor():
    print("\n[5] make_result — usa broll_start ancorado, preserva fim do trecho")
    from matcher import make_result
    cand = {"path": "/a/x.mp4", "filename": "x.mp4", "duration": 4.0, "score": 0.9, "source": "project"}
    m = make_result({"start": 10.0, "end": 16.0, "text": "x", "broll_start": 12.5}, cand, "ok")
    check("b-roll começa no broll_start (12.5)", m["start"] == 12.5)
    check("end = fim do trecho (16.0)", m["end"] == 16.0)
    m2 = make_result({"start": 10.0, "end": 16.0, "text": "x"}, None, "skip")
    check("sem broll_start → usa início do trecho", m2["start"] == 10.0)


if __name__ == "__main__":
    test_srt()
    test_vtt_and_empty()
    test_word_anchor()
    test_composition_clip()
    test_make_result_anchor()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
