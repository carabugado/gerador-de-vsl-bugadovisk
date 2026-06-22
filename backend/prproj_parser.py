"""
Parser de projeto do Premiere (.prproj) — extrai os clipes de vídeo da timeline
SEM abrir o Premiere (aprendizado em lote, #L1 folder-batch).

O .prproj é um XML gzip. Cada <VideoClipTrackItem> tem Start/End (tempo de sequência,
em ticks) e um <SubClip> cujo <Name> casa com um <ActualMediaFilePath> (caminho real).
Distinguir narração de B-roll por faixa é frágil (formato interno, dois sistemas de id),
então o "host" (narração) é detectado por heurística: o arquivo que mais ocupa a tela.
Validado em projetos reais (avatar / vsl white).
"""
import os
import gzip
import collections
import xml.etree.ElementTree as ET
from typing import Optional, Dict

TICKS = 254016000000.0


def _read_xml(path: str):
    with open(path, "rb") as f:
        head = f.read(2)
    data = gzip.open(path, "rb").read() if head == b"\x1f\x8b" else open(path, "rb").read()
    return ET.fromstring(data)


def prproj_to_clips(path: str) -> Optional[Dict]:
    """Extrai {sequence_name, clips:[{path,name,seq_start,seq_end}], host, host_seq_start}.
    Retorna None se não der pra ler/parsear (nunca estoura)."""
    try:
        root = _read_xml(path)
    except Exception as e:
        print(f"[prproj] não leu {os.path.basename(path)}: {str(e)[:80]}")
        return None

    # basename → caminho real da mídia
    base2path: Dict[str, str] = {}
    for el in root.iter("ActualMediaFilePath"):
        t = (el.text or "").strip()
        if "/" in t:
            base2path.setdefault(os.path.basename(t), t)

    # SubClip ObjectID → Name (nome do arquivo de mídia)
    sub_name: Dict[str, str] = {}
    for el in root.iter("SubClip"):
        oid = el.get("ObjectID")
        nm = el.find("Name")
        if oid and nm is not None and nm.text:
            sub_name[oid] = nm.text.strip()

    clips = []
    for it in root.iter("VideoClipTrackItem"):
        s = it.find(".//TrackItem/Start")
        e = it.find(".//TrackItem/End")
        sc = it.find(".//SubClip")
        if s is None or e is None or sc is None:
            continue
        name = sub_name.get(sc.get("ObjectRef"), "")
        p = base2path.get(name, "")
        try:
            ss, ee = float(s.text) / TICKS, float(e.text) / TICKS
        except (TypeError, ValueError):
            continue
        if ee <= ss:
            continue
        clips.append({"path": p, "name": name,
                      "seq_start": round(ss, 3), "seq_end": round(ee, 3)})

    if not clips:
        return None

    # host de VÍDEO = mídia com maior duração total na timeline
    dur = collections.defaultdict(float)
    for c in clips:
        k = c["path"] or c["name"]
        if k:
            dur[k] += c["seq_end"] - c["seq_start"]
    host = max(dur, key=dur.get) if dur else ""
    host_start = min((c["seq_start"] for c in clips if (c["path"] or c["name"]) == host),
                     default=0.0)

    # VOZ (narração) = arquivo de ÁUDIO com maior duração total. Numa VSL a voz costuma
    # estar numa faixa de áudio separada (o vídeo do host pode não ter áudio), então é
    # ELE que deve ser transcrito — não o vídeo.
    adur = collections.defaultdict(float)
    a_start = {}
    for it in root.iter("AudioClipTrackItem"):
        s = it.find(".//TrackItem/Start")
        e = it.find(".//TrackItem/End")
        sc = it.find(".//SubClip")
        if s is None or e is None or sc is None:
            continue
        p = base2path.get(sub_name.get(sc.get("ObjectRef"), ""), "")
        if not p:
            continue
        try:
            ss, ee = float(s.text) / TICKS, float(e.text) / TICKS
        except (TypeError, ValueError):
            continue
        if ee > ss:
            adur[p] += ee - ss
            a_start[p] = min(a_start.get(p, ss), ss)
    voice = max(adur, key=adur.get) if adur else ""
    voice_start = a_start.get(voice, 0.0)

    return {"sequence_name": os.path.splitext(os.path.basename(path))[0],
            "clips": clips, "host": host, "host_seq_start": host_start,
            "voice": voice, "voice_seq_start": voice_start}


def find_project_files(folder: str) -> list:
    """.prproj reais da pasta (ignora Auto-Save e arquivos ._ do macOS)."""
    out = []
    for rootd, _dirs, files in os.walk(folder):
        if "Auto-Save" in rootd:
            continue
        for f in files:
            if f.lower().endswith(".prproj") and not f.startswith("._"):
                out.append(os.path.join(rootd, f))
    return out
