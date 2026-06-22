"""
Integração com Higgsfield CLI.
Gera vídeos de 7 segundos usando o modelo mais barato disponível.
"""
import os
import json
import subprocess
from typing import Optional, Callable

HIGGS_BIN  = os.path.expanduser("~/.npm-global/bin/higgsfield")
OUT_DIR    = os.environ.get("GENERATED_DIR", "./generated_clips")
DURATION   = 7        # segundos fixos
# Modelo padrão — atualiza após 'higgsfield model list --video'
DEFAULT_MODEL = os.environ.get("HIGGSFIELD_MODEL", "")


def _run(args: list, timeout: int = 30) -> dict:
    """Roda o CLI e retorna JSON."""
    cmd = [HIGGS_BIN, "--json"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout.strip())


# O CLI lista modelos como {display_name, job_set_type, type} — SEM campo de custo.
# "Mais barato" vem da política de roteamento, não da API.
PREFERRED_VIDEO_MODELS = [
    "wan2_7", "wan2_6", "grok_video", "seedance_2_0", "seedance1_5",
    "kling3_0", "soul_cast",
]
NON_GENERATOR_TYPES = {
    "bytedance_video_upscale", "video_upscale", "topaz_video", "video_deflicker",
    "video_background_remover", "reframe", "image_decompose", "sam_3_video",
    "clipify", "llm_text", "draw_to_video", "kling3_0_motion_control",
}


def _model_id_of(m: dict) -> str:
    return m.get("job_set_type") or m.get("id") or m.get("name") or ""


def get_cheapest_model() -> str:
    """Modelo de vídeo default (econômico) disponível na conta."""
    cached = os.environ.get("HIGGSFIELD_MODEL", "")
    if cached:
        return cached

    try:
        data = _run(["model", "list", "--video"])
        models = data if isinstance(data, list) else data.get("models", [])
        if not models:
            return ""

        available = {_model_id_of(m) for m in models if _model_id_of(m)}
        model_id = ""
        for mid in PREFERRED_VIDEO_MODELS:
            if mid in available:
                model_id = mid
                break
        if not model_id:
            for m in models:
                mid = _model_id_of(m)
                if mid and mid not in NON_GENERATOR_TYPES:
                    model_id = mid
                    break
        if not model_id:
            return ""

        os.environ["HIGGSFIELD_MODEL"] = model_id
        print(f"[Higgsfield] Modelo default (econômico): {model_id}")
        return model_id
    except Exception as e:
        print(f"[Higgsfield] Não conseguiu listar modelos: {e}")
        return ""


def _extract_url(stdout: str) -> str:
    """Extrai a URL do resultado (.mp4/.png/.jpg) do stdout do CLI (--wait)."""
    import re
    text = (stdout or "").strip()
    for pat in (
        r'https?://[^"\s\\]+\.mp4[^"\s\\]*',
        r'https?://[^"\s\\]+\.png[^"\s\\]*',
        r'https?://[^"\s\\]+\.jpe?g[^"\s\\]*',
    ):
        m = re.search(pat, text)
        if m:
            return m.group(0)
    try:
        data = json.loads(text)
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            for k in ("url", "result_url", "video_url", "image_url", "output_url"):
                if data.get(k):
                    return str(data[k])
            results = data.get("results") or data.get("outputs") or []
            if isinstance(results, list) and results and isinstance(results[0], dict):
                for k in ("url", "result_url", "video_url", "output_url"):
                    if results[0].get(k):
                        return str(results[0][k])
    except Exception:
        pass
    return ""


def _build_cmd(model, prompt, duration, aspect, with_negative, negative_prompt):
    cmd = [
        HIGGS_BIN, "--json", "generate", "create", model,
        "--prompt", prompt,
        "--duration", str(duration),
        "--aspect_ratio", aspect,
        "--resolution", os.environ.get("HIGGSFIELD_RESOLUTION", "720p"),
        "--wait", "--wait-timeout", "40m", "--wait-interval", "5s",
    ]
    if with_negative and negative_prompt:
        cmd[5:5] = ["--negative_prompt", negative_prompt]  # insere logo após --prompt
    return cmd


# Padrões de erro do CLI quando a flag --negative_prompt não existe nessa versão
_BAD_FLAG = ("no such option", "unrecognized arguments", "unexpected extra argument",
             "no such command", "got unexpected", "invalid value for")


def generate_clip(prompt: str, output_filename: str, poll_cb: Optional[Callable] = None,
                  negative_prompt: str = "", duration: float = None,
                  aspect_ratio: str = None) -> str:
    """
    Gera um vídeo com o modelo mais barato (bloqueante via --wait).
    `negative_prompt` é enviado via --negative_prompt; se a versão do CLI não
    suportar a flag, faz retry automático SEM ela (nunca trava a geração).
    Retorna o path do arquivo baixado.
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    model = get_cheapest_model()
    if not model:
        raise RuntimeError("Nenhum modelo Higgsfield disponível. Faça login: higgsfield auth login")

    out_dir = os.environ.get("GENERATED_DIR", OUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, output_filename)

    dur = duration if duration else DURATION
    aspect = aspect_ratio or os.environ.get("HIGGSFIELD_ASPECT", "9:16")

    if poll_cb:
        poll_cb("SUBMITTING", 0)
    print(f"[Higgsfield] Gerando com modelo '{model}': {prompt[:60]}...")
    if poll_cb:
        poll_cb("RUNNING", 10)

    cmd = _build_cmd(model, prompt, dur, aspect, bool(negative_prompt), negative_prompt)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60)

    # Retry sem --negative_prompt se a flag não for reconhecida
    if proc.returncode != 0 and negative_prompt:
        low = (proc.stderr or proc.stdout or "").lower()
        if any(p in low for p in _BAD_FLAG):
            print("[Higgsfield] CLI não aceita --negative_prompt — refazendo sem ela.")
            cmd = _build_cmd(model, prompt, dur, aspect, False, "")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "not_enough_credits" in err:
            raise RuntimeError("Higgsfield: créditos insuficientes (not_enough_credits).")
        raise RuntimeError(f"Higgsfield falhou: {err[:400]}")

    url = _extract_url(proc.stdout)
    if not url:
        raise RuntimeError(f"URL do resultado não encontrada. Saída: {proc.stdout[:300]}")

    if poll_cb:
        poll_cb("DOWNLOADING", 90)
    import urllib.request
    urllib.request.urlretrieve(url, out_path)
    print(f"[Higgsfield] Salvo: {out_path}")
    if poll_cb:
        poll_cb("DONE", 100)
    return out_path
