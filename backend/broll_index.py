"""
Indexa B-rolls usando CLIP: extrai frames e gera embeddings visuais.
Salva cache em .vsl_index.json na raiz da pasta de B-rolls.
"""
import os
import json
import time
import hashlib
import subprocess
import cv2
import numpy as np
import torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor

try:
    from tqdm import tqdm
except Exception:                       # tqdm é opcional
    def tqdm(it, **kw):
        return it

CACHE_FILE = ".vsl_index.json"
SUPPORTED = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mxf", ".webm"}
# 1 frame (50%) por padrão — 3x mais rápido. INDEX_FRAMES=3 volta ao multi-frame.
FRAMES_PER_CLIP = int(os.environ.get("INDEX_FRAMES", "1"))
CLIP_SIZE = 224                          # input do CLIP — extrai já reduzido
# Tempo máximo por arquivo (s). Arquivo que passar disso é pulado (corrompido/lento).
FRAME_TIMEOUT = int(os.environ.get("INDEX_FRAME_TIMEOUT", "25"))
# Salva o índice parcial a cada N clips novos (não perde tudo se cair).
CACHE_FLUSH_EVERY = int(os.environ.get("INDEX_CACHE_FLUSH_EVERY", "100"))
# Paralelismo da extração de frames (ffmpeg) e tamanho do batch do CLIP.
INDEX_WORKERS = int(os.environ.get("INDEX_WORKERS", "10"))
INDEX_BATCH = int(os.environ.get("INDEX_BATCH", "64"))

# Device do CLIP: cuda automático; mps/cpu via CLIP_DEVICE (mps é opt-in por segurança).
def _pick_device() -> str:
    forced = os.environ.get("CLIP_DEVICE", "").strip().lower()
    if forced:
        return forced
    return "cuda" if torch.cuda.is_available() else "cpu"

_DEVICE = _pick_device()
_clip_model = None
_clip_processor = None


def _load_clip():
    global _clip_model, _clip_processor
    if _clip_model is None:
        print(f"[CLIP] Carregando modelo (device={_DEVICE})...")
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _clip_model.eval()
        try:
            _clip_model.to(_DEVICE)
        except Exception as e:
            print(f"[CLIP] device {_DEVICE} indisponível ({e}) — usando cpu.")
            _clip_model.to("cpu")
        print("[CLIP] Modelo pronto.")
    return _clip_model, _clip_processor


def _file_hash(path: str) -> str:
    stat = os.stat(path)
    return hashlib.md5(f"{path}{stat.st_size}{stat.st_mtime}".encode()).hexdigest()


def _embed_pil_batch(pil_frames: list) -> np.ndarray:
    """Embedding NORMALIZADO de uma lista de PIL Images → (N, 512). Usa o device."""
    model, processor = _load_clip()
    inputs = processor(images=pil_frames, return_tensors="pt")
    pixel = inputs["pixel_values"].to(_DEVICE)
    with torch.no_grad():
        vision_out = model.vision_model(pixel_values=pixel)
        feats = model.visual_projection(vision_out.pooler_output)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy()


def _embed_frames_each(frames: list) -> np.ndarray:
    """Embedding NORMALIZADO de CADA frame (BGR np) → matriz (N, 512)."""
    pil_frames = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
    return _embed_pil_batch(pil_frames)


def _embed_images_batched(images_bgr: list, batch: int = INDEX_BATCH) -> np.ndarray:
    """BATCH: embeda muitas imagens (uma por clip) em lotes — N chamadas viram N/batch."""
    out = []
    for i in range(0, len(images_bgr), batch):
        chunk = images_bgr[i:i + batch]
        pil = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in chunk]
        out.append(_embed_pil_batch(pil))
    return np.vstack(out) if out else np.zeros((0, 512), dtype=np.float32)


def _embed_frames(frames: list) -> list:
    """Compat: embedding médio (normalizado) dos frames."""
    arr = _embed_frames_each(frames)
    avg = arr.mean(axis=0)
    return (avg / (np.linalg.norm(avg) + 1e-8)).tolist()


def _simple_clean(filename: str) -> str:
    return os.path.splitext(filename)[0].replace("-", " ").replace("_", " ").strip()


def _ffprobe_info(path: str, timeout: int = 15):
    """(has_video, duration) via ffprobe — subprocess matável (não trava como o cv2)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=timeout,
        )
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        dur = float(data.get("format", {}).get("duration", 0) or 0)
        return has_video, round(dur, 3)
    except subprocess.TimeoutExpired:
        print(f"[Index] ffprobe TIMEOUT — pulando: {os.path.basename(path)}")
        return False, 0.0
    except Exception:
        return False, 0.0


def _extract_frame_ffmpeg(path: str, ts: float, timeout: int = FRAME_TIMEOUT,
                          scale: int = 0):
    """1 frame no tempo ts via ffmpeg → np array BGR. Subprocess matável no timeout.
    scale>0 reduz o frame pra scale×scale já no ffmpeg (input do CLIP = 224)."""
    cmd = ["ffmpeg", "-nostdin", "-ss", str(max(0.0, ts)), "-i", path, "-frames:v", "1"]
    if scale:
        cmd += ["-vf", f"scale={scale}:{scale}"]
    cmd += ["-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if out.returncode != 0 or not out.stdout:
            return None
        arr = np.frombuffer(out.stdout, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)  # imdecode em memória não trava
    except subprocess.TimeoutExpired:
        print(f"[Index] ffmpeg TIMEOUT — pulando: {os.path.basename(path)}")
        return None
    except Exception:
        return None


def _extract_mid_frame(path: str):
    """Worker paralelo: ffprobe + 1 frame no meio (50%) já em 224×224.
    Retorna (path, image_bgr|None, duration)."""
    has_video, dur = _ffprobe_info(path)
    if not has_video or dur <= 0:
        return path, None, dur
    mid = dur * 0.5 if dur > 1.0 else 0.0
    img = _extract_frame_ffmpeg(path, mid, scale=CLIP_SIZE)
    return path, img, round(dur, 3)


def _sample_frames_safe(path: str, timeout: int = FRAME_TIMEOUT) -> Optional[list]:
    """Extrai frames via ffmpeg. Retorna [] se não for vídeo; None nunca trava o processo."""
    has_video, dur = _ffprobe_info(path)
    if not has_video or dur <= 0:
        return []  # áudio ou ilegível → pula
    # 3 frames representativos: 10%, 50%, 90% da duração
    times = [dur * 0.1, dur * 0.5, dur * 0.9] if dur > 1.0 else [0.0]
    frames = []
    for t in times[:FRAMES_PER_CLIP]:
        img = _extract_frame_ffmpeg(path, t)
        if img is not None:
            frames.append(img)
    return frames


def _get_duration(path: str) -> float:
    _, dur = _ffprobe_info(path)
    return dur


def _scan_videos(folder: str) -> List[str]:
    videos = []
    for root, _, files in os.walk(folder):
        for fname in files:
            if fname.startswith("._") or fname.startswith("."):
                continue
            if os.path.splitext(fname)[1].lower() in SUPPORTED:
                videos.append(os.path.join(root, fname))
    return videos


def _entry_from(vpath: str, img_bgr, dur: float, avg: np.ndarray,
                per_frame: np.ndarray, name_emb: np.ndarray) -> Dict:
    clean = _simple_clean(os.path.basename(vpath))
    return {
        "path": vpath,
        "filename": os.path.basename(vpath),
        "duration": round(dur, 3),
        "embedding": avg.tolist(),                 # compat (matcher)
        "visual_embedding": avg.tolist(),          # média (busca semântica)
        "frame_embeddings": per_frame.tolist(),    # best-frame matching (1+ frames)
        "name_embedding": name_emb.tolist(),
        "clean_name": clean,
    }


def index_folder(folder: str, progress_cb: Optional[Callable] = None) -> List[Dict]:
    """
    Varre a pasta recursivamente e indexa vídeos novos/modificados.

    Otimizado p/ bibliotecas grandes (1800+):
      - 1 frame (50%) já em 224×224 (INDEX_FRAMES=3 volta ao multi-frame)
      - extração de frames em PARALELO (ThreadPoolExecutor, INDEX_WORKERS)
      - embedding do CLIP em BATCH (INDEX_BATCH) no device (cuda/cpu)
      - incremental (mtime via hash), índice parcial a cada 100, log de velocidade
    """
    t0 = time.time()
    cache_path = os.path.join(folder, CACHE_FILE)
    cache: Dict = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    videos = _scan_videos(folder)

    def _flush():
        tmp = cache_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, cache_path)  # escrita atômica

    # Incremental: separa o que reaproveita (schema novo) do que precisa indexar.
    result: List[Dict] = []
    todo: List[tuple] = []   # (vpath, fhash)
    for vpath in videos:
        fhash = _file_hash(vpath)
        if fhash in cache and cache[fhash].get("frame_embeddings"):
            result.append(cache[fhash])
        else:
            todo.append((vpath, fhash))

    print(f"[Index] {len(videos)} vídeos | {len(result)} em cache | {len(todo)} a indexar "
          f"({FRAMES_PER_CLIP} frame/clip, {INDEX_WORKERS} workers, batch {INDEX_BATCH}, device {_DEVICE})")
    if not todo:
        print(f"[Index] Nada novo. {len(result)} clips em {time.time()-t0:.1f}s")
        return result

    multi = FRAMES_PER_CLIP > 1
    done = len(result)
    total = len(videos)
    new_since_flush = 0
    pbar = tqdm(total=len(todo), desc="indexando", unit="clip")

    def _tick(name):
        nonlocal done
        done += 1
        pbar.update(1)
        if progress_cb:
            progress_cb(done, total, name)

    # Processa em blocos: extrai os frames do bloco em PARALELO, embeda em BATCH.
    BLOCK = max(INDEX_BATCH * 4, 128)
    for bstart in range(0, len(todo), BLOCK):
        block = todo[bstart:bstart + BLOCK]
        paths = [vp for vp, _ in block]

        # 1) EXTRAÇÃO PARALELA dos frames (ffmpeg é I/O — threads escalam bem).
        #    Tica o progresso item a item pra a barra do painel andar suave.
        extract_fn = _sample_frames_safe if multi else _extract_mid_frame
        extracted = []
        with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as pool:
            for (vp, fh), res_ex in zip(block, pool.map(extract_fn, paths)):
                _tick(os.path.basename(vp))
                if multi:
                    if res_ex:                       # lista de frames
                        extracted.append((vp, fh, res_ex, _get_duration(vp)))
                else:
                    _, img, dur = res_ex             # (path, img, dur)
                    if img is not None:
                        extracted.append((vp, fh, [img], dur))

        if not extracted:
            continue

        # 2) EMBEDDING em BATCH: 1 imagem por clip → um único batch no CLIP/device
        try:
            names = [_simple_clean(os.path.basename(vp)) for vp, _, _, _ in extracted]
            name_mat = _embed_text_batched(names)
            if multi:
                # multi-frame: embeda os frames de cada clip e tira a média
                for k, (vp, fh, frames, dur) in enumerate(extracted):
                    per_frame = _embed_frames_each(frames)
                    avg = per_frame.mean(axis=0)
                    avg = avg / (np.linalg.norm(avg) + 1e-8)
                    cache[fh] = _entry_from(vp, frames[0], dur, avg, per_frame, name_mat[k])
                    result.append(cache[fh]); new_since_flush += 1
            else:
                imgs = [frames[0] for _, _, frames, _ in extracted]
                emb = _embed_images_batched(imgs)                 # (M, 512) batched
                for k, (vp, fh, frames, dur) in enumerate(extracted):
                    v = emb[k]
                    cache[fh] = _entry_from(vp, frames[0], dur,
                                            v, v.reshape(1, -1), name_mat[k])
                    result.append(cache[fh]); new_since_flush += 1
        except Exception as e:
            print(f"[Index] erro no batch de embedding: {e}")

        if new_since_flush >= CACHE_FLUSH_EVERY:
            _flush(); new_since_flush = 0

    pbar.close()
    _flush()
    elapsed = time.time() - t0
    rate = len(todo) / elapsed if elapsed > 0 else 0
    print(f"[Index] OK: {len(result)} clips ({len(todo)} novos) em {elapsed:.1f}s "
          f"({rate:.1f} clips/s)")
    return result


def embed_text(texts: List[str]) -> np.ndarray:
    model, processor = _load_clip()
    inputs = processor(text=texts, return_tensors="pt", padding=True, truncation=True)
    input_ids = inputs["input_ids"].to(_DEVICE)
    attention_mask = inputs["attention_mask"].to(_DEVICE)
    with torch.no_grad():
        text_out = model.text_model(input_ids=input_ids, attention_mask=attention_mask)
        feats = model.text_projection(text_out.pooler_output)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.cpu().numpy()


def _embed_text_batched(texts: List[str], batch: int = 256) -> np.ndarray:
    """Embeda muitos nomes de arquivo em lotes (evita um processor() gigante)."""
    if not texts:
        return np.zeros((0, 512), dtype=np.float32)
    out = []
    for i in range(0, len(texts), batch):
        out.append(embed_text(texts[i:i + batch]))
    return np.vstack(out)
