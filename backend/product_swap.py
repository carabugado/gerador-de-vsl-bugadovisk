"""
Troca de Produto (troca de pote) — detecta TODOS os trechos da VSL onde o
produto antigo aparece e monta a troca pelo produto novo.

Duas frentes de detecção:
  1. ÁUDIO — menções ao formato/nome do produto na narração ("cápsulas",
     "tome 2 cápsulas ao dia", nome do produto), com tempo preciso por PALAVRA
     quando a transcrição tem word timestamps. Narração não pode ser cortada,
     então menções viram MARCADORES na timeline (re-gravar / regravar TTS).
  2. VISUAL — frames onde o pote/frasco aparece em cena, via CLIP:
     prompts de texto do formato antigo (+ nome) contra prompts de fundo
     (pessoa falando, gráfico, paisagem) e, opcionalmente, imagens de
     REFERÊNCIA do produto antigo (muito mais preciso). Frames consecutivos
     acima do limiar viram ZONAS [start, end].

Para cada zona visual o sistema escolhe o melhor clipe do produto NOVO
(pasta indexada com o broll_index) por encaixe de duração + similaridade de
enquadramento, e monta o plano de troca. A montagem final pode ser:
  - Na timeline do Premiere (faixa V3 cobre o pote antigo + marcadores), ou
  - Render direto por ffmpeg: corta no lugar certo, troca SÓ o vídeo das
    zonas e mantém o áudio original inteiro (nada da narração é retirado).

Imports pesados (torch/CLIP via broll_index) são LAZY — só na análise visual.
"""
import os
import re
import json
import subprocess
import unicodedata
from typing import List, Dict, Optional, Callable, Tuple

# ── Configuração (env-tunable) ────────────────────────────────────────────────
SCAN_INTERVAL = float(os.environ.get("SWAP_SCAN_INTERVAL", "1.0"))   # s entre frames
SCAN_WORKERS = int(os.environ.get("SWAP_SCAN_WORKERS", "10"))
MIN_ZONE = float(os.environ.get("SWAP_MIN_ZONE", "0.8"))             # zona mínima (s)
ZONE_PAD = float(os.environ.get("SWAP_ZONE_PAD", "0.5"))             # folga nas bordas
GAP_MULT = float(os.environ.get("SWAP_GAP_MULT", "2.2"))             # tolerância de gap
MENTION_PAD = float(os.environ.get("SWAP_MENTION_PAD", "0.35"))      # folga da menção

# Limiar por sensibilidade: margem (pos - fundo) p/ prompts de texto e
# similaridade mínima p/ imagem de referência (imagem-imagem é bem mais alta).
_SENSITIVITY = {
    #            margem_texto  pos_mínimo  ref_imagem
    "low":     (0.035,        0.22,       0.72),
    "normal":  (0.022,        0.20,       0.65),
    "high":    (0.012,        0.18,       0.58),
}

# ── Léxico de formatos de produto (pt/en/es) ─────────────────────────────────
# Chave canônica → termos falados (casados por palavra inteira, sem acento).
PRODUCT_FORMS: Dict[str, Dict] = {
    "capsulas": {
        "label": "cápsulas",
        "terms": ["capsula", "capsulas", "capsule", "capsules",
                  "pilula", "pilulas", "pill", "pills", "comprimido",
                  "comprimidos", "tablet", "tablets"],
        "prompts": [
            "a supplement bottle full of capsules",
            "a hand holding a pill bottle of capsules",
            "capsule pills spilling out of a supplement bottle",
            "close-up of a supplement capsules jar with a label",
            "a person taking a capsule pill with a glass of water",
        ],
    },
    "gotas": {
        "label": "gotas",
        "terms": ["gota", "gotas", "drop", "drops", "conta-gotas", "gotinha",
                  "gotinhas", "dropper", "tincture", "gotero", "goteo"],
        "prompts": [
            "a dropper bottle of liquid supplement",
            "a hand holding a tincture dropper bottle",
            "liquid drops falling from a glass dropper",
            "close-up of a small amber dropper bottle with a label",
            "a person putting supplement drops under the tongue",
        ],
    },
    "gummies": {
        "label": "gummies",
        "terms": ["gummy", "gummies", "goma", "gomas", "gominha", "gominhas",
                  "bala", "balinha", "balinhas"],
        "prompts": [
            "a bottle of gummy vitamin supplements",
            "colorful gummy vitamins spilling out of a jar",
            "a hand holding gummy supplement candies",
            "close-up of a gummies supplement bottle with a label",
        ],
    },
    "po": {
        "label": "pó",
        "terms": ["po", "pos", "powder", "polvo", "sache", "saches", "sachet",
                  "sachets", "scoop", "dose"],
        "prompts": [
            "a jar of powder supplement with a scoop",
            "powder supplement being mixed into a glass of water",
            "a hand scooping protein powder from a container",
            "close-up of a supplement powder tub with a label",
        ],
    },
    "spray": {
        "label": "spray",
        "terms": ["spray", "sprays", "borrifada", "borrifadas", "jato", "jatos"],
        "prompts": [
            "a small spray bottle of supplement",
            "a hand holding an oral spray supplement bottle",
            "a person spraying supplement into their mouth",
        ],
    },
    "creme": {
        "label": "creme",
        "terms": ["creme", "cremes", "cream", "gel", "geis", "pomada",
                  "pomadas", "serum"],
        "prompts": [
            "a tube of cream product",
            "a hand applying cream from a jar",
            "close-up of a cosmetic cream jar with a label",
        ],
    },
}

# Prompts de FUNDO (o que uma VSL mostra quando NÃO é o pote) — a margem
# pos - fundo separa "pote em cena" de "resto do vídeo".
BACKGROUND_PROMPTS = [
    "a person talking to the camera",
    "a doctor in a medical office",
    "text and graphics on a plain background",
    "a nature landscape",
    "people exercising at a gym",
    "food on a table",
    "a couple at home",
    "a city street",
]

_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_VID_EXT = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}


# ── Utilidades puras (testáveis sem torch) ────────────────────────────────────

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_accents((s or "").lower())


def form_terms(form: str, product_name: str = "") -> List[str]:
    """Termos de busca do formato (canônico ou livre) + nome do produto."""
    key = _norm(form).replace(" ", "")
    info = PRODUCT_FORMS.get(key)
    terms = list(info["terms"]) if info else ([_norm(form)] if form else [])
    name = _norm(product_name).strip()
    if name:
        terms.append(name)
        # nome composto: casa também cada palavra "forte" (≥4 letras) do nome
        terms += [t for t in name.split() if len(t) >= 4]
    # dedupe preservando ordem
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def form_label(form: str) -> str:
    info = PRODUCT_FORMS.get(_norm(form).replace(" ", ""))
    return info["label"] if info else form


def _term_in_text(term: str, text_norm: str) -> bool:
    """Casamento por palavra inteira no texto normalizado (sem acentos)."""
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])",
                     text_norm) is not None


def detect_audio_mentions(segments: List[Dict], old_form: str,
                          old_name: str = "") -> List[Dict]:
    """Menções ao produto antigo na narração. Usa tempos por PALAVRA quando
    existem (Whisper) — a menção fica cravada na palavra falada; senão usa a
    janela do segmento inteiro (.srt)."""
    terms = form_terms(old_form, old_name)
    if not terms:
        return []
    mentions: List[Dict] = []
    for seg in segments:
        text_norm = _norm(seg.get("text", ""))
        matched = [t for t in terms if _term_in_text(t, text_norm)]
        if not matched:
            continue
        # tempo preciso: primeira/última palavra que casa com algum termo
        w_start, w_end = None, None
        for w in (seg.get("words") or []):
            wt = _norm(re.sub(r"[^\wà-ÿ]", "", str(w.get("word", ""))))
            if any(_term_in_text(t, wt) or wt == t for t in matched):
                ws = float(w.get("start", seg["start"]))
                we = float(w.get("end", seg["end"]))
                w_start = ws if w_start is None else min(w_start, ws)
                w_end = we if w_end is None else max(w_end, we)
        start = w_start if w_start is not None else float(seg["start"])
        end = w_end if w_end is not None else float(seg["end"])
        mentions.append({
            "source": "audio",
            "start": round(max(0.0, start - MENTION_PAD), 3),
            "end": round(end + MENTION_PAD, 3),
            "seg_start": float(seg["start"]),
            "seg_end": float(seg["end"]),
            "text": seg.get("text", ""),
            "matched_terms": matched,
            "precise": w_start is not None,
        })
    return mentions


def group_hits(hits: List[Tuple[float, float]], interval: float,
               min_zone: float = MIN_ZONE, pad: float = ZONE_PAD,
               gap_mult: float = GAP_MULT) -> List[Dict]:
    """Agrupa frames-hit (t, score) consecutivos em zonas [start, end].
    Buracos de até gap_mult × interval não quebram a zona (pote saiu de
    quadro por 1 frame). Zonas com menos que min_zone (após folga) caem."""
    if not hits:
        return []
    hits = sorted(hits)
    max_gap = gap_mult * interval
    zones: List[Dict] = []
    cur = [hits[0]]
    for h in hits[1:]:
        if h[0] - cur[-1][0] <= max_gap:
            cur.append(h)
        else:
            zones.append(cur)
            cur = [h]
    zones.append(cur)

    out = []
    for z in zones:
        t0 = max(0.0, z[0][0] - pad)
        t1 = z[-1][0] + interval + pad   # o hit cobre até o próximo frame
        if t1 - t0 < min_zone:
            continue
        out.append({
            "source": "visual",
            "start": round(t0, 3),
            "end": round(t1, 3),
            "score": round(sum(s for _, s in z) / len(z), 4),
            "hits": len(z),
        })
    return out


def merge_zones(visual_zones: List[Dict], audio_mentions: List[Dict]) -> List[Dict]:
    """Junta as duas frentes numa lista única ordenada por tempo. Menção de
    áudio que cai DENTRO de uma zona visual marca a zona como 'both' (o
    narrador fala do produto enquanto ele aparece) — a menção não some, o
    marcador continua sendo gerado pra ela."""
    zones = [dict(z) for z in visual_zones]
    for z in zones:
        z["type"] = "visual"
        z["mentions"] = []
    for m in audio_mentions:
        hit = None
        for z in zones:
            if m["start"] < z["end"] and m["end"] > z["start"]:
                hit = z
                break
        if hit is not None:
            hit["type"] = "both"
            hit["mentions"].append(m)
    merged = zones + [dict(m, type="audio") for m in audio_mentions]
    merged.sort(key=lambda x: (x["start"], 0 if x.get("source") == "visual" else 1))
    return merged


def build_timeline_spans(zones: List[Dict], duration: float) -> List[Dict]:
    """Plano de corte do render: alterna trechos originais ('keep') e trechos
    trocados ('swap'), cobrindo o vídeo inteiro sem sobreposição. Só zonas
    VISUAIS com substituto entram — a narração/áudio nunca é cortada."""
    cuts = []
    for i, z in enumerate(zones):
        if z.get("type") not in ("visual", "both"):
            continue
        if not z.get("replacement_path"):
            continue
        if z.get("status") == "rejected":
            continue
        s = max(0.0, min(float(z["start"]), duration))
        e = max(0.0, min(float(z["end"]), duration))
        if e - s < 0.1:
            continue
        cuts.append((s, e, i))
    cuts.sort()

    spans: List[Dict] = []
    pos = 0.0
    for s, e, i in cuts:
        s = max(s, pos)              # zonas sobrepostas não duplicam tempo
        if e <= pos:
            continue
        if s - pos > 0.01:
            spans.append({"type": "keep", "start": round(pos, 3), "end": round(s, 3)})
        spans.append({"type": "swap", "start": round(s, 3), "end": round(e, 3),
                      "zone_index": i})
        pos = e
    if duration - pos > 0.01:
        spans.append({"type": "keep", "start": round(pos, 3), "end": round(duration, 3)})
    return spans


def plan_replacements(zones: List[Dict], new_clips: List[Dict],
                      zone_embeddings: Optional[Dict[int, list]] = None) -> int:
    """Escolhe o clipe do produto NOVO pra cada zona visual: encaixe de duração
    (clipe deve cobrir a zona) + similaridade visual com o frame da zona
    (enquadramento parecido = corte natural) + rodízio pra não repetir o mesmo
    clipe em zonas vizinhas. Preenche replacement_* in-place; retorna nº planejado."""
    if not new_clips:
        return 0
    try:
        import numpy as np
    except ImportError:
        np = None
    use_count: Dict[str, int] = {}
    planned = 0
    for i, z in enumerate(zones):
        if z.get("type") not in ("visual", "both"):
            continue
        zdur = float(z["end"]) - float(z["start"])
        zemb = (zone_embeddings or {}).get(i)
        scored = []
        for c in new_clips:
            cdur = float(c.get("duration", 0) or 0)
            dur_fit = min(cdur / zdur, 1.0) if zdur > 0 else 1.0
            vis = 0.0
            if np is not None and zemb is not None and c.get("visual_embedding"):
                a = np.asarray(zemb, dtype=np.float32)
                b = np.asarray(c["visual_embedding"], dtype=np.float32)
                na, nb = np.linalg.norm(a), np.linalg.norm(b)
                if na > 0 and nb > 0:
                    vis = float(a @ b / (na * nb))
            score = 0.55 * dur_fit + 0.45 * max(vis, 0.0) \
                - 0.15 * use_count.get(c["path"], 0)
            scored.append((score, dur_fit, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_fit, best = scored[0]
        z["replacement_path"] = best["path"]
        z["replacement_filename"] = best.get("filename", os.path.basename(best["path"]))
        z["replacement_score"] = round(best_score, 4)
        z["replacement_covers"] = best_fit >= 0.999   # clipe cobre a zona inteira?
        z["candidates"] = [
            {"path": c["path"], "filename": c.get("filename", ""),
             "score": round(s, 4)} for s, _, c in scored[:5]
        ]
        use_count[best["path"]] = use_count.get(best["path"], 0) + 1
        planned += 1
    return planned


# ── Análise visual (CLIP — imports pesados lazy) ──────────────────────────────

def visual_prompts(old_form: str, old_name: str = "") -> List[str]:
    key = _norm(old_form).replace(" ", "")
    info = PRODUCT_FORMS.get(key)
    prompts = list(info["prompts"]) if info else [
        f"a bottle of {old_form} product",
        f"a hand holding a {old_form} product container",
    ]
    if old_name.strip():
        prompts.append(f'a product bottle labeled "{old_name.strip()}"')
    return prompts


def _ref_embeddings(ref_folder: str):
    """Embeddings CLIP das imagens/vídeos de referência do produto antigo."""
    import numpy as np
    import cv2
    import broll_index as bi
    frames = []
    for root, _, files in os.walk(ref_folder):
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            path = os.path.join(root, fname)
            if ext in _IMG_EXT:
                img = cv2.imread(path)
                if img is not None:
                    frames.append(cv2.resize(img, (224, 224)))
            elif ext in _VID_EXT:
                _, dur = bi._ffprobe_info(path)
                if dur > 0:
                    img = bi._extract_frame_ffmpeg(path, dur * 0.5, scale=224)
                    if img is not None:
                        frames.append(img)
    if not frames:
        return None
    return bi._embed_images_batched(frames)   # (N, 512) normalizado


def scan_video(video_path: str, old_form: str, old_name: str = "",
               ref_folder: str = "", sensitivity: str = "normal",
               interval: float = None,
               progress_cb: Optional[Callable] = None) -> Tuple[List[Dict], Dict[int, list]]:
    """Varre o vídeo frame a frame (a cada `interval` s), pontua cada frame
    contra o produto antigo e devolve (zonas visuais, embedding médio por zona).
    O embedding da zona alimenta a escolha do clipe substituto (enquadramento)."""
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    import broll_index as bi

    interval = float(interval or SCAN_INTERVAL)
    margin_thr, min_pos, ref_thr = _SENSITIVITY.get(
        sensitivity, _SENSITIVITY["normal"])

    has_video, duration = bi._ffprobe_info(video_path)
    if not has_video or duration <= 0:
        raise RuntimeError(f"Vídeo ilegível ou sem stream de vídeo: {video_path}")

    times = [round(t * interval, 3) for t in range(int(duration / interval) + 1)]
    times = [t for t in times if t < duration]

    # 1) extração paralela dos frames (já em 224×224)
    frames, stamps = [], []
    done = 0
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for t, img in zip(times, pool.map(
                lambda ts: bi._extract_frame_ffmpeg(video_path, ts, scale=224),
                times)):
            done += 1
            if progress_cb and (done % 20 == 0 or done == len(times)):
                progress_cb("scan", done, len(times))
            if img is not None:
                frames.append(img)
                stamps.append(t)
    if not frames:
        raise RuntimeError("Não consegui extrair frames do vídeo.")

    # 2) embeddings: frames (batch) + prompts de texto + referências
    if progress_cb:
        progress_cb("embed", 0, len(frames))
    frame_emb = bi._embed_images_batched(frames)              # (F, 512)
    pos_emb = bi.embed_text(visual_prompts(old_form, old_name))
    neg_emb = bi.embed_text(BACKGROUND_PROMPTS)
    ref_emb = _ref_embeddings(ref_folder) if ref_folder and os.path.isdir(ref_folder) else None

    pos_sim = frame_emb @ pos_emb.T          # (F, P)
    neg_sim = frame_emb @ neg_emb.T
    pos_max = pos_sim.max(axis=1)
    neg_max = neg_sim.max(axis=1)
    margin = pos_max - neg_max

    hits: List[Tuple[float, float]] = []
    hit_idx: List[int] = []
    ref_max = None
    if ref_emb is not None:
        ref_max = (frame_emb @ ref_emb.T).max(axis=1)
    for k, t in enumerate(stamps):
        text_hit = margin[k] >= margin_thr and pos_max[k] >= min_pos
        ref_hit = ref_max is not None and ref_max[k] >= ref_thr
        if text_hit or ref_hit:
            score = float(ref_max[k]) if ref_hit else float(margin[k])
            hits.append((t, score))
            hit_idx.append(k)

    zones = group_hits(hits, interval)

    # embedding médio dos frames de cada zona (p/ escolher substituto parecido)
    zone_embs: Dict[int, list] = {}
    stamp_to_emb = {stamps[k]: frame_emb[k] for k in hit_idx}
    for i, z in enumerate(zones):
        embs = [stamp_to_emb[t] for t, _ in hits
                if z["start"] <= t <= z["end"] and t in stamp_to_emb]
        if embs:
            avg = np.mean(embs, axis=0)
            n = np.linalg.norm(avg)
            if n > 0:
                avg = avg / n
            zone_embs[i] = avg.tolist()
    return zones, zone_embs


# ── Render final (ffmpeg) — corta no lugar certo e mantém o áudio ─────────────

def _video_props(path: str) -> Tuple[int, int, str, float]:
    """(width, height, fps_str, duration) do stream de vídeo principal."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True, text=True, timeout=30)
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", [])
              if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError("Sem stream de vídeo.")
    fps = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    if fps in ("0/0", "0"):
        fps = v.get("r_frame_rate") or "30/1"
    dur = float(data.get("format", {}).get("duration", 0) or 0)
    return int(v.get("width", 1920)), int(v.get("height", 1080)), fps, dur


def build_ffmpeg_command(video_path: str, spans: List[Dict], zones: List[Dict],
                         out_path: str, width: int, height: int,
                         fps: str) -> List[str]:
    """Monta o comando ffmpeg: vídeo = concat dos trechos (originais + trocas,
    escalados/tpad pro tamanho exato) · áudio = o ORIGINAL inteiro, intocado."""
    repl_paths: List[str] = []
    repl_input: Dict[int, int] = {}       # zone_index -> índice do input ffmpeg
    for sp in spans:
        if sp["type"] != "swap":
            continue
        zi = sp["zone_index"]
        path = zones[zi]["replacement_path"]
        if zi not in repl_input:
            repl_input[zi] = 1 + len(repl_paths)
            repl_paths.append(path)

    parts = []
    labels = []
    for k, sp in enumerate(spans):
        dur = sp["end"] - sp["start"]
        lbl = f"v{k}"
        if sp["type"] == "keep":
            parts.append(
                f"[0:v]trim=start={sp['start']}:end={sp['end']},"
                f"setpts=PTS-STARTPTS,fps={fps}[{lbl}]")
        else:
            idx = repl_input[sp["zone_index"]]
            # scale+pad preserva o aspecto do clipe novo dentro do quadro da VSL;
            # tpad clona o último frame se o clipe for mais curto que a zona.
            parts.append(
                f"[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},"
                f"tpad=stop_mode=clone:stop_duration={dur + 2:.3f},"
                f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[{lbl}]")
        labels.append(f"[{lbl}]")

    graph = ";".join(parts) + ";" + "".join(labels) + \
        f"concat=n={len(labels)}:v=1:a=0[vout]"

    cmd = ["ffmpeg", "-y", "-nostdin", "-i", video_path]
    for p in repl_paths:
        cmd += ["-i", p]
    cmd += ["-filter_complex", graph,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-c:a", "copy", out_path]
    return cmd


def render_swap(video_path: str, zones: List[Dict], out_path: str = "",
                progress_cb: Optional[Callable] = None) -> Dict:
    """Renderiza o vídeo trocado: zonas visuais recebem o clipe do produto novo
    (corte exato), o resto do vídeo e TODO o áudio ficam originais."""
    width, height, fps, duration = _video_props(video_path)
    spans = build_timeline_spans(zones, duration)
    n_swaps = sum(1 for s in spans if s["type"] == "swap")
    if n_swaps == 0:
        return {"ok": False, "error": "Nenhuma zona visual com substituto aprovado."}

    if not out_path:
        base, ext = os.path.splitext(video_path)
        out_path = base + "_trocado" + (ext or ".mp4")

    cmd = build_ffmpeg_command(video_path, spans, zones, out_path,
                               width, height, fps)
    if progress_cb:
        progress_cb("render", 0, 1)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0 or not os.path.exists(out_path):
        tail = (proc.stderr or "")[-400:]
        return {"ok": False, "error": f"ffmpeg falhou: {tail}"}
    return {"ok": True, "out_path": out_path, "swaps": n_swaps,
            "spans": len(spans), "duration": duration}


# ── Pipeline completo ─────────────────────────────────────────────────────────

def analyze(video_path: str, old_form: str, new_form: str,
            old_name: str = "", new_name: str = "",
            segments: Optional[List[Dict]] = None,
            new_folder: str = "", ref_folder: str = "",
            sensitivity: str = "normal", interval: float = None,
            progress_cb: Optional[Callable] = None) -> Dict:
    """Roda as duas detecções + plano de troca. `segments` = transcrição já em
    tempo do vídeo (Whisper/srt); se None, a menção de áudio é pulada."""
    # 1. Áudio
    mentions = detect_audio_mentions(segments or [], old_form, old_name)

    # 2. Visual
    zones, zone_embs = scan_video(
        video_path, old_form, old_name, ref_folder=ref_folder,
        sensitivity=sensitivity, interval=interval, progress_cb=progress_cb)

    # 3. Junta e planeja a troca
    merged = merge_zones(zones, mentions)

    new_clips: List[Dict] = []
    if new_folder and os.path.isdir(new_folder):
        import broll_index as bi
        def _cb(cur, total, name):
            if progress_cb:
                progress_cb("index", cur, total)
        new_clips = bi.index_folder(new_folder, progress_cb=_cb)

    # remapeia embeddings de zona (índices mudaram no merge)
    merged_embs: Dict[int, list] = {}
    vi = 0
    for i, z in enumerate(merged):
        if z.get("source") == "visual":
            if vi in zone_embs:
                merged_embs[i] = zone_embs[vi]
            vi += 1
    planned = plan_replacements(merged, new_clips, merged_embs)

    # status por zona (pro painel)
    for z in merged:
        if z["type"] == "audio":
            z["status"] = "marker"
        elif z.get("replacement_path"):
            z["status"] = "ok" if z.get("replacement_covers") else "review"
        else:
            z["status"] = "no_clip"

    return {
        "zones": merged,
        "old_label": form_label(old_form),
        "new_label": form_label(new_form),
        "stats": {
            "visual": sum(1 for z in merged if z["type"] in ("visual", "both")),
            "audio": sum(1 for z in merged if z["type"] == "audio"),
            "both": sum(1 for z in merged if z["type"] == "both"),
            "planned": planned,
            "new_clips": len(new_clips),
        },
    }
