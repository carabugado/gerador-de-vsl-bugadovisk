"""
Legendagem LOCAL de clipes (BLIP) — grátis, offline, sem cota.

Gera uma descrição densa do CONTEÚDO de cada clipe ("elderly woman holding her
stomach in a kitchen") a partir de 1 frame. Essa legenda alimenta o documento de
busca (broll_search._tag_doc) no pivô texto↔texto — onde 93% dos clipes hoje caem
só no nome do arquivo (hash) e ficam invisíveis pra busca.

Roda no mesmo stack que o CLIP (transformers + torch — já instalados). O modelo
(~990MB) baixa sozinho no 1º uso, igual ao Whisper/CLIP. Desliga com CAPTION_DISABLED=1.
"""
import os

_MODEL_NAME = os.environ.get("CAPTION_MODEL", "Salesforce/blip-image-captioning-base")
_DISABLED   = os.environ.get("CAPTION_DISABLED", "0") == "1"
_MAX_TOKENS = int(os.environ.get("CAPTION_MAX_TOKENS", "40"))

_model = None
_proc = None
_DEV = None
_load_failed = False


def _device() -> str:
    d = (os.environ.get("CAPTION_DEVICE", "") or "").lower()
    if d in ("cpu", "cuda", "mps"):
        return d
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _load() -> bool:
    """Carrega o BLIP sob demanda (baixa no 1º uso). Marca falha p/ não re-tentar 1610x."""
    global _model, _proc, _DEV, _load_failed
    if _DISABLED or _load_failed:
        return False
    if _model is not None:
        return True
    try:
        from transformers import BlipProcessor, BlipForConditionalGeneration
        _DEV = _device()
        print(f"[Caption] Carregando BLIP ({_MODEL_NAME}, device={_DEV}) — baixa ~990MB no 1º uso...")
        _proc = BlipProcessor.from_pretrained(_MODEL_NAME)
        _model = BlipForConditionalGeneration.from_pretrained(_MODEL_NAME).to(_DEV)
        _model.eval()
        print("[Caption] BLIP pronto.")
    except Exception as e:
        print(f"[Caption] indisponível ({str(e)[:120]}) — clipes caem no nome do arquivo.")
        _model = None
        _load_failed = True
    return _model is not None


def available() -> bool:
    """Dá pra legendar? (checa deps/flag SEM baixar o modelo — download só no 1º caption.)"""
    if _DISABLED or _load_failed:
        return False
    if _model is not None:
        return True
    try:
        import torch          # noqa: F401
        import transformers   # noqa: F401
        return True
    except Exception:
        return False


def caption_image(pil_img) -> str:
    """Legenda de UMA imagem PIL. '' em qualquer falha (nunca trava o pipeline)."""
    if not _load():
        return ""
    try:
        import torch
        inputs = _proc(images=pil_img, return_tensors="pt").to(_DEV)
        with torch.no_grad():
            out = _model.generate(**inputs, max_new_tokens=_MAX_TOKENS, num_beams=3)
        return _proc.decode(out[0], skip_special_tokens=True).strip()
    except Exception as e:
        print(f"[Caption] geração falhou: {str(e)[:100]}")
        return ""


def caption_path(video_path: str, t: float = 1.0) -> str:
    """Extrai 1 frame do vídeo e legenda. '' se não der pra extrair/legendar."""
    if not available():
        return ""
    try:
        from broll_index import _extract_frame_ffmpeg
        img = _extract_frame_ffmpeg(video_path, t)
        if img is None:
            return ""
        import numpy as np
        from PIL import Image
        rgb = np.ascontiguousarray(img[:, :, ::-1])     # cv2 BGR → RGB
        return caption_image(Image.fromarray(rgb))
    except Exception as e:
        print(f"[Caption] frame falhou ({os.path.basename(video_path)}): {str(e)[:100]}")
        return ""
