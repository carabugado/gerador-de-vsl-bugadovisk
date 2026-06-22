import whisper
import os
import re
import json
import subprocess
import tempfile
import hashlib
from typing import List, Dict, Optional

_model = None

CACHE_DIR = os.path.join(tempfile.gettempdir(), "vsl_transcribe_cache")


def get_model():
    global _model
    if _model is None:
        print("[Whisper] Baixando/carregando modelo 'base' (~145 MB na primeira vez)...")
        _model = whisper.load_model("base")
        print("[Whisper] Modelo pronto.")
    return _model


def extract_audio(video_path: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    # sem check=True: arquivo sem faixa de áudio não deve estourar (vira wav vazio →
    # transcribe() detecta pelo tamanho e devolve []).
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", tmp.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600,
        )
    except Exception as e:
        print(f"[Transcribe] extract_audio falhou ({str(e)[:60]})")
    return tmp.name


# ─── Cache + legenda existente ────────────────────────────────────────────────

def _cache_key(path: str) -> str:
    """Chave estável por caminho + tamanho + mtime (rápido, sem ler o arquivo todo)."""
    try:
        st = os.stat(path)
        raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = os.path.abspath(path)
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _cache_path(path: str) -> str:
    return os.path.join(CACHE_DIR, f"{_cache_key(path)}.json")


def _srt_time_to_sec(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(ts)


def parse_srt_text(content: str) -> List[Dict]:
    """Parseia o TEXTO de uma legenda .srt/.vtt → segmentos {start,end,text}.
    Usado tanto pela leitura de sidecar quanto pela transcrição colada do Premiere
    (que já vem em tempo de SEQUÊNCIA — não precisa remapear)."""
    segments: List[Dict] = []
    blocks = re.split(r"\n\s*\n", (content or "").strip())
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        time_line = next((l for l in lines if "-->" in l), None)
        if not time_line:
            continue
        try:
            start_str, end_str = [p.strip() for p in time_line.split("-->")]
        except ValueError:
            continue
        text_lines = [l for l in lines if "-->" not in l and not l.strip().isdigit()
                      and not l.strip().upper().startswith("WEBVTT")]
        text = " ".join(text_lines).strip()
        if not text:
            continue
        segments.append({
            "start": round(_srt_time_to_sec(start_str), 3),
            "end": round(_srt_time_to_sec(end_str.split(" ")[0]), 3),
            "text": text,
        })
    return segments


def _parse_srt(srt_path: str) -> List[Dict]:
    """Lê um arquivo .srt/.vtt do disco e parseia (wrapper de parse_srt_text)."""
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        return parse_srt_text(f.read())


def _find_sidecar(video_path: str) -> Optional[str]:
    """Procura uma legenda já existente ao lado do vídeo (.srt/.vtt)."""
    base = os.path.splitext(video_path)[0]
    for ext in (".srt", ".vtt"):
        cand = base + ext
        if os.path.exists(cand):
            return cand
    return None


def transcribe(video_path: str, use_cache: bool = True) -> List[Dict]:
    """
    Retorna lista de segmentos da TRANSCRIÇÃO da origem (tempo do arquivo):
    [{"start": 0.0, "end": 4.2, "text": "..."}]

    Ordem de preferência:
    1. Legenda existente ao lado do vídeo (.srt/.vtt) — "acessar a transcrição".
    2. Cache local de execuções anteriores.
    3. Whisper (e salva no cache) — "se não existir, faz".
    """
    if not video_path or not os.path.exists(video_path):
        return []

    # 1. Legenda já existente no projeto
    sidecar = _find_sidecar(video_path)
    if sidecar:
        try:
            segs = _parse_srt(sidecar)
            if segs:
                print(f"[Transcribe] Usando legenda existente: {os.path.basename(sidecar)}")
                return segs
        except Exception as e:
            print(f"[Transcribe] Falha ao ler legenda {sidecar}: {e}")

    # 2. Cache
    cache_file = _cache_path(video_path)
    if use_cache and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                print(f"[Transcribe] Cache: {os.path.basename(video_path)}")
                return json.load(f)
        except Exception:
            pass

    # 3. Whisper
    audio_path = extract_audio(video_path)
    try:
        # Sem áudio / arquivo vazio → não tenta transcrever (Whisper estoura com 0 samples)
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 2000:
            print(f"[Transcribe] sem áudio em {os.path.basename(video_path)} — pulando.")
            return []
        model = get_model()
        result = model.transcribe(audio_path, word_timestamps=True)
        segments = []
        for seg in result["segments"]:
            segments.append({
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "text": seg["text"].strip(),
            })
        if use_cache:
            os.makedirs(CACHE_DIR, exist_ok=True)
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(segments, f, ensure_ascii=False)
            except Exception:
                pass
        return segments
    except Exception as e:
        print(f"[Transcribe] falhou em {os.path.basename(video_path)}: {str(e)[:80]} — []")
        return []
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass


def transcribe_composition(clips: List[Dict], use_cache: bool = True) -> List[Dict]:
    """
    Transcreve a COMPOSIÇÃO inteira da timeline, em tempo de SEQUÊNCIA.

    Cada clipe (dict) deve ter:
      - "path": caminho da mídia de origem
      - "seq_start": início na timeline (segundos)
      - "in_point" / "out_point": in/out na origem (segundos)

    Importante: na prática a narração costuma ser UMA fonte cortada em vários
    clipes (jump cuts). Para NÃO duplicar texto, iteramos os segmentos do Whisper
    (não os clipes) e atribuímos cada segmento a UM único clipe — o que contém o
    início do segmento na origem — mapeando para o tempo de sequência. Fontes
    repetidas são transcritas só uma vez.
    """
    from collections import defaultdict

    clips_by_source: Dict[str, List[Dict]] = defaultdict(list)
    for clip in clips:
        path = clip.get("path", "")
        if path:
            clips_by_source[path].append(clip)

    out: List[Dict] = []

    for path, clip_list in clips_by_source.items():
        if not os.path.exists(path):
            continue

        source_segments = transcribe(path, use_cache=use_cache)

        # Ordena os clipes desta fonte pelo in_point (ordem na origem)
        clip_list = sorted(clip_list, key=lambda c: float(c.get("in_point", 0) or 0))

        def host_clip(t: float) -> Optional[Dict]:
            """Clipe cujo trecho de origem [in,out] contém o instante t."""
            for c in clip_list:
                in_pt = float(c.get("in_point", 0) or 0)
                out_pt = c.get("out_point", None)
                out_pt = float(out_pt) if out_pt is not None else None
                if t >= in_pt - 0.05 and (out_pt is None or t < out_pt + 0.05):
                    return c
            return None

        for seg in source_segments:
            s, e = seg["start"], seg["end"]
            host = host_clip(s) or host_clip((s + e) / 2.0)
            if host is None:
                continue  # trecho da origem que não está na timeline
            in_pt = float(host.get("in_point", 0) or 0)
            seq_start = float(host.get("seq_start", 0) or 0)
            offset = seq_start - in_pt  # mapeamento linear origem→sequência
            new_start = s + offset
            new_end = e + offset
            if new_end <= new_start:
                continue
            out.append({
                "start": round(new_start, 3),
                "end": round(new_end, 3),
                "text": seg["text"],
            })

    out.sort(key=lambda x: x["start"])

    # Dedupe de segurança: remove repetições exatas de (start, texto)
    seen = set()
    deduped = []
    for s in out:
        key = (s["start"], s["text"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


# ─── Exportação SRT + CLI ─────────────────────────────────────────────────────

def _sec_to_srt_time(sec: float) -> str:
    """Segundos → 'HH:MM:SS,mmm' (formato de tempo SRT)."""
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: List[Dict]) -> str:
    """Converte segmentos {start,end,text} em texto .srt pronto pra colar no
    HIGHLIGHTS_MAP (ou importar em qualquer editor)."""
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(
            f"{i}\n"
            f"{_sec_to_srt_time(seg['start'])} --> {_sec_to_srt_time(seg['end'])}\n"
            f"{seg['text']}\n"
        )
    return "\n".join(blocks)


def _cli():
    """CLI: transcreve um vídeo/áudio com Whisper e gera um .srt.

    Uso (dentro do backend, com a venv ativa):
        python -m transcribe "/caminho/episodio.mp4"
        python -m transcribe "/caminho/episodio.mp4" -o legenda.srt
        python -m transcribe "/caminho/episodio.mp4" --stdout > legenda.srt
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Transcreve áudio/vídeo com Whisper e gera SRT "
                    "(para colar no HIGHLIGHTS_MAP).")
    ap.add_argument("input", help="caminho do vídeo ou áudio (mp4, mp3, m4a, wav…)")
    ap.add_argument("-o", "--output",
                    help="arquivo .srt de saída (padrão: ao lado do vídeo)")
    ap.add_argument("--stdout", action="store_true",
                    help="imprime o SRT no stdout em vez de salvar em arquivo")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignora o cache do Whisper e re-transcreve")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"ERRO: arquivo não encontrado: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"[Transcribe] Processando {os.path.basename(args.input)}…", file=sys.stderr)
    segs = transcribe(args.input, use_cache=not args.no_cache)
    if not segs:
        print("ERRO: nenhuma fala detectada ou falha na transcrição.", file=sys.stderr)
        sys.exit(2)

    srt = segments_to_srt(segs)
    if args.stdout:
        sys.stdout.write(srt)
    else:
        out = args.output or (os.path.splitext(args.input)[0] + ".srt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(srt)
        dur = segs[-1]["end"]
        print(f"✅ {len(segs)} segmentos ({_sec_to_srt_time(dur)}) → {out}", file=sys.stderr)


if __name__ == "__main__":
    _cli()
