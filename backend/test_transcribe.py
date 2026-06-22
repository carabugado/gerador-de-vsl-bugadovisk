"""
Testes do parser de transcrição (.srt do Premiere → segmentos em tempo de sequência).
Rodar:  python test_transcribe.py
"""
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


if __name__ == "__main__":
    test_srt()
    test_vtt_and_empty()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
