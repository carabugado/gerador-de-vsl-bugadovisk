"""
Pexels Videos fallback: quando a lib local não tem nenhum clip bom (score abaixo
de GEN_THRESHOLD), busca e baixa um clip do Pexels Videos pra usar no lugar.

Requer PEXELS_API_KEY (gratuita em pexels.com/api — uso comercial OK).
Downloads cacheados em ~/.vsl_pexels_cache/ — não rebaixa o mesmo clip.
Desliga com PEXELS_ENABLED=0.
"""
import os
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict

PEXELS_ENABLED = os.environ.get("PEXELS_ENABLED", "1") != "0"
_CACHE_DIR = Path(os.environ.get("PEXELS_CACHE_DIR",
                                  str(Path.home() / ".vsl_pexels_cache")))
_RATE_TS = 0.0      # último request (rate-limit simples: 1 req/s)


def _key() -> str:
    return os.environ.get("PEXELS_API_KEY", "")


def available() -> bool:
    return bool(_key()) and PEXELS_ENABLED


def search(query: str, per_page: int = 3) -> List[Dict]:
    """
    Retorna lista de {id, duration, download_url, filename, width, height}.
    Devolve [] se desligado, sem chave ou qualquer erro.
    """
    global _RATE_TS
    if not available():
        return []
    now = time.time()
    if now - _RATE_TS < 1.0:
        time.sleep(1.1 - (now - _RATE_TS))
    _RATE_TS = time.time()

    params = urllib.parse.urlencode({
        "query":    query,
        "per_page": per_page,
        "size":     "medium",
        "locale":   "en-US",
    })
    url = f"https://api.pexels.com/videos/search?{params}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": _key(), "User-Agent": "VSLDirector/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[Pexels] search falhou: {str(e)[:80]}")
        return []

    out = []
    for v in data.get("videos", []):
        # Prefere HD próximo a 1280px; evita 4K (muito pesado)
        files = sorted(
            v.get("video_files", []),
            key=lambda f: (f.get("quality") != "hd",
                           abs((f.get("width") or 0) - 1280)),
        )
        if not files:
            continue
        chosen = files[0]
        slug = (v.get("url", "").rstrip("/").rsplit("/", 1)[-1] or str(v["id"]))
        out.append({
            "id":           str(v["id"]),
            "duration":     float(v.get("duration", 0)),
            "download_url": chosen.get("link", ""),
            "filename":     f"pexels_{slug}_{v['id']}.mp4",
            "width":        chosen.get("width", 0),
            "height":       chosen.get("height", 0),
        })
    return out


def download_clip(video_id: str, url: str, filename: str) -> Optional[str]:
    """Baixa o clip (se não estiver em cache) e retorna o caminho local."""
    if not url:
        return None
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _CACHE_DIR / filename
    if dest.exists() and dest.stat().st_size > 100_000:
        return str(dest)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "VSLDirector/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        if dest.stat().st_size < 10_000:
            dest.unlink(missing_ok=True)
            return None
        return str(dest)
    except Exception as e:
        print(f"[Pexels] download falhou ({video_id}): {str(e)[:80]}")
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def cache_stats() -> Dict:
    try:
        files = list(_CACHE_DIR.glob("pexels_*.mp4"))
        size = sum(f.stat().st_size for f in files)
        return {"cached": len(files), "size_mb": round(size / 1_048_576, 1),
                "dir": str(_CACHE_DIR)}
    except Exception:
        return {"cached": 0, "size_mb": 0, "dir": str(_CACHE_DIR)}
