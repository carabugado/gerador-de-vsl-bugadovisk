"""
Camada de LLM com HIERARQUIA e escalonamento:

  ollama (trabalhador)  ->  gemini (auxiliar)  ->  anthropic (chefe)

Por padrão o Ollama LOCAL faz o trabalho (grátis, offline). Se ele falhar
(erro/saída vazia) ou se o chamador pedir um nível acima ("precisa de mais
contexto"), complementa com Gemini e, por fim, com o Anthropic (autoridade final).

Config por env:
  LLM_CHAIN      = "ollama,gemini,anthropic"   (ordem da cadeia)
  OLLAMA_MODEL   = "qwen2.5:7b"
  GEMINI_MODEL   = "gemini-2.5-flash"  (precisa GEMINI_API_KEY)
  MODELO_CLAUDE  = "claude-sonnet-4-6" (precisa ANTHROPIC_API_KEY)
"""
import os
import re
import json
import time
import base64
import urllib.request
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llama3.2-vision:11b")
# gemini-flash-latest: cota free separada (fresca quando 2.5/2.0 estouram) e rápido.
# É "thinking" → exige thinkingBudget=0 (ver _gemini_gen_cfg) senão trunca/vem vazio.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
ANTHROPIC_MODEL = os.environ.get("MODELO_CLAUDE", "claude-sonnet-4-6")

# Opções do Ollama (Apple Silicon usa Metal/GPU automático). Ajustáveis por env.
_OLLAMA_OPTS = {
    "num_gpu":    int(os.environ.get("OLLAMA_NUM_GPU", "99")),
    "num_thread": int(os.environ.get("OLLAMA_NUM_THREAD", "10")),
    "num_ctx":    int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
}

DEFAULT_CHAIN = [b.strip() for b in
                 os.environ.get("LLM_CHAIN", "ollama,gemini,anthropic").split(",")
                 if b.strip()]

# Roteamento por TAREFA (sistema híbrido grátis). Repetitivo → Ollama; PHOENIX →
# Gemini; Claude só como último fallback. Editável em ~/.vsl_config.json:
#   "provider_override": {"classifier":"ollama","phoenix":"gemini", ...}
# Ollama LOCAL é a base confiável e grátis (sem cota). Gemini entra de reserva quando
# a cota free dele estiver disponível (qualidade melhor em texto/visão). OpenRouter foi
# removido (cota free de ~50/dia não aguenta o volume de uma VSL). Claude removido.
# Gemini-first nas tarefas de TEXTO pesadas (diretor/classificador são lentíssimos no
# Ollama local — ~muitos min). Gemini é rápido + paralelo + melhores descrições; cai no
# Ollama local quando sem chave/cota (rotação cobre). Visão idem.
# Rebalanceado (B, 2026-06-21): tarefas de ALTO VOLUME (classificador/UGC, ~50 calls
# cada) → Ollama local (grátis ilimitado), pra não estourar a cota free do Gemini.
# Tarefas de BAIXO volume e alto valor (diretor ~3, visão seletiva, phoenix) → Gemini
# (rápido). A seleção em si é por nome/tags — não usa LLM.
_TASK_DEFAULTS = {
    "director":      ["gemini", "ollama"],   # baixo volume, era lento no Ollama → Gemini
    "classifier":    ["ollama", "gemini"],   # ALTO volume → local (cota)
    "ugc_prompt":    ["ollama", "gemini"],   # ALTO volume → local (cota)
    "vision_verify": ["gemini", "ollama"],   # seletiva → Gemini rápido
    "auto_tag":      ["gemini", "ollama"],
    "phoenix":       ["gemini", "ollama"],
    "context":       ["gemini", "ollama"],
}
_CONFIG_PATH = Path.home() / ".vsl_config.json"

# Em modelos locais, lotes grandes ficam lentos/instáveis — o diretor processa
# os segmentos em lotes deste tamanho. (Gemini/Anthropic fazem tudo de uma vez.)
LOCAL_CHUNK = int(os.environ.get("LLM_LOCAL_CHUNK", "20"))


def _provider_override() -> dict:
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
        ov = cfg.get("provider_override")
        return ov if isinstance(ov, dict) else {}
    except Exception:
        return {}




def chain_for(task: str) -> list:
    """Cadeia de backends para uma TAREFA: override do config → default da tarefa,
    filtrada por disponibilidade. É como o roteamento híbrido fica grátis por padrão."""
    ov = _provider_override().get(task)
    if ov:
        order = [b.strip() for b in str(ov).split(",") if b.strip()]
    else:
        order = _TASK_DEFAULTS.get(task, DEFAULT_CHAIN)
    return [b for b in order if _backend_available(b)]


def safe_json(text: str):
    """Parse robusto de JSON de modelos locais (markdown, lixo ao redor, etc)."""
    if not text:
        return None
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, t)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None


# ─────────────────────────── disponibilidade ────────────────────────────────

def _ollama_models() -> list:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=4) as resp:
            data = json.loads(resp.read())
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def resolve_ollama_model() -> str:
    models = _ollama_models()
    if OLLAMA_MODEL in models:
        return OLLAMA_MODEL
    base = OLLAMA_MODEL.split(":")[0]
    for m in models:
        if m.startswith(base):
            return m
    for m in models:
        if "qwen" in m:
            return m
    return models[0] if models else OLLAMA_MODEL


def _backend_available(name: str) -> bool:
    if name == "ollama":
        return len(_ollama_models()) > 0
    if name == "gemini":
        return len(_gemini_keys()) > 0
    if name == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False


def chain() -> list:
    """Cadeia efetiva = backends disponíveis na ordem configurada."""
    forced = os.environ.get("LLM_BACKEND", "").strip().lower()
    order = [forced] if forced else DEFAULT_CHAIN
    return [b for b in order if _backend_available(b)]


def available() -> bool:
    return len(chain()) > 0


def primary_backend() -> str:
    c = chain()
    return c[0] if c else ""


def is_local() -> bool:
    """True quando o trabalhador principal é o Ollama (define se usa lotes)."""
    return primary_backend() == "ollama"


def status() -> dict:
    return {b: _backend_available(b) for b in ("ollama", "gemini", "anthropic")}


# ─────────────────────────────── backends ───────────────────────────────────

def _ollama_complete(system, user, max_tokens, temperature, force_json) -> str:
    payload = {
        "model": resolve_ollama_model(),
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {**_OLLAMA_OPTS, "temperature": temperature, "num_predict": max_tokens},
    }
    if force_json:
        payload["format"] = "json"
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read())
    return (data.get("message", {}) or {}).get("content", "").strip()


# ── Pool de chaves Gemini com ROTAÇÃO (várias contas multiplicam a cota free) ──
# Quando uma chave dá 429 (cota), marca cooldown e usa a próxima. Fontes (vírgula
# separa várias): env GEMINI_API_KEYS, env GEMINI_API_KEY, config gemini_api_keys
# (lista) e gemini_api_key (string — pode ter várias separadas por vírgula).
_GEMINI_COOLDOWN = {}        # chave -> epoch até quando pular
_GEMINI_COOLDOWN_S = int(os.environ.get("GEMINI_COOLDOWN_S", "120"))

# ── Alertas de API (cota/erro/chave) p/ avisar o usuário no painel ─────────────
_ALERTS = {}                 # provider -> {msg, level, ts}


def record_alert(provider: str, msg: str, level: str = "warn") -> None:
    _ALERTS[provider] = {"msg": msg, "level": level, "ts": time.time()}


def clear_alert(provider: str) -> None:
    _ALERTS.pop(provider, None)


def get_alerts(max_age: int = 900) -> list:
    """Alertas recentes (últimos max_age s), mais novos primeiro."""
    now = time.time()
    out = [{"provider": p, **a} for p, a in _ALERTS.items() if now - a["ts"] <= max_age]
    return sorted(out, key=lambda a: a["ts"], reverse=True)


def _split_keys(val) -> list:
    if isinstance(val, list):
        out = []
        for v in val:
            out += _split_keys(v)
        return out
    return [k.strip() for k in str(val or "").split(",") if k.strip()]


def _gemini_keys() -> list:
    keys = _split_keys(os.environ.get("GEMINI_API_KEYS", ""))
    keys += _split_keys(os.environ.get("GEMINI_API_KEY", ""))
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
        keys += _split_keys(cfg.get("gemini_api_keys"))
        keys += _split_keys(cfg.get("gemini_api_key"))
    except Exception:
        pass
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _gemini_post(payload: dict, timeout: int = 120) -> dict:
    """POST no Gemini rotacionando as chaves: pula as em cooldown; em 429/503 marca
    cooldown e tenta a próxima. Sem chave livre → erro (chamador escala pro Ollama)."""
    all_keys = _gemini_keys()
    if not all_keys:
        raise RuntimeError("GEMINI_API_KEY ausente")
    now = time.time()
    order = [k for k in all_keys if _GEMINI_COOLDOWN.get(k, 0) <= now]
    if not order:
        # Todas as chaves em cooldown (429/erro recente) → não martela; cai rápido
        # pro Ollama. Avisa o usuário e levanta na hora.
        record_alert("gemini", "Todas as chaves Gemini estão sem cota (429) agora. "
                     "Usando a reserva local (mais lento). Use chaves de CONTAS diferentes.")
        raise RuntimeError("Gemini: todas as chaves em cooldown (cota)")
    last_err = None
    for k in order:
        # Manda a chave dos DOIS jeitos: ?key= (clássico, chaves AIza) e header
        # x-goog-api-key (método moderno — pode ser o que as chaves AQ. exigem).
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{GEMINI_MODEL}:generateContent?key={k}")
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": k},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            clear_alert("gemini")                 # funcionou → limpa alerta
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            # 429/503 = cota/sobrecarga (volta depois); 400/401/403 = chave inválida.
            # Em QUALQUER caso de chave, rotaciona pra próxima; só não cooldowna erro
            # de request genérico (que falharia em todas → levanta no fim).
            if e.code in (400, 401, 403, 429, 500, 503):
                cool = _GEMINI_COOLDOWN_S if e.code in (429, 503) else 1800  # chave ruim: descansa mais
                _GEMINI_COOLDOWN[k] = time.time() + cool
                if len(all_keys) > 1:
                    print(f"[Gemini] chave …{k[-6:]} falhou ({e.code}) — rotacionando")
                continue
            raise
    # Todas as chaves falharam → registra alerta legível pro painel.
    code = getattr(last_err, "code", None)
    if code in (429, 503):
        record_alert("gemini", f"Gemini atingiu o limite de uso (cota, {code}). "
                     f"Usando a reserva local (mais lento). Adicione outra chave ou aguarde.")
    elif code in (400, 401, 403):
        record_alert("gemini", f"Chave Gemini inválida/revogada ({code}) — verifique a chave "
                     f"(formato AIza). Usando reserva local.")
    else:
        record_alert("gemini", "Gemini indisponível — usando reserva local.")
    if last_err:
        raise last_err
    raise RuntimeError("Gemini: nenhuma chave disponível")


def test_gemini_key(key: str) -> dict:
    """Faz uma chamada mínima REAL com a chave (header moderno + ?key=) e diz se
    funciona. Resposta definitiva (não depende de prefixo AIza/AQ)."""
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "chave vazia"}
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={key}")
    payload = {"contents": [{"parts": [{"text": "ping"}]}],
               "generationConfig": _gemini_gen_cfg(8, 0.0, False)}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            _gemini_extract(json.loads(r.read()))
        return {"ok": True, "prefix": key[:4]}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:160]
        except Exception:
            pass
        return {"ok": False, "code": e.code, "error": body or str(e), "prefix": key[:4]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160], "prefix": key[:4]}


def _gemini_gen_cfg(max_tokens, temperature, force_json) -> dict:
    """generationConfig do Gemini. Desliga o 'thinking' nos modelos 2.5 (senão ele
    gasta o orçamento de tokens pensando e devolve resposta vazia/truncada)."""
    cfg = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if force_json:
        cfg["responseMimeType"] = "application/json"
    # Modelos "thinking" (2.5, *-latest) gastam o orçamento pensando → desliga.
    if "2.5" in GEMINI_MODEL or "latest" in GEMINI_MODEL:
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    return cfg


def _gemini_extract(data: dict) -> str:
    """Extrai o texto da resposta do Gemini de forma robusta (lida com ausência de
    'parts', bloqueio de prompt e finishReason sem texto) em vez de estourar KeyError."""
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:160])
    cands = data.get("candidates") or []
    if not cands:
        fb = (data.get("promptFeedback") or {}).get("blockReason")
        raise RuntimeError(f"Gemini sem candidates ({fb or 'resposta vazia'})")
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError(f"Gemini sem texto (finishReason={cands[0].get('finishReason','?')})")
    return text


def _gemini_complete(system, user, max_tokens, temperature, force_json) -> str:
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": _gemini_gen_cfg(max_tokens, temperature, force_json),
    }
    return _gemini_extract(_gemini_post(payload))


def gemini_available() -> bool:
    """Tem ao menos uma chave Gemini configurada?"""
    return bool(_gemini_keys())


def gemini_audio(system: str, user: str, audio_b64: str, mime_type: str = "audio/mpeg",
                 max_tokens: int = 4000, temperature: float = 0.3,
                 force_json: bool = True, timeout: int = 600) -> str:
    """Manda ÁUDIO (inline, base64) + prompt pro Gemini multimodal e devolve o texto.
    O Gemini OUVE o episódio (tom, timing, ênfase) — seleção melhor que só transcrição.
    Reusa a rotação de chaves/cooldown de _gemini_post."""
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": audio_b64}},
            {"text": user},
        ]}],
        "generationConfig": _gemini_gen_cfg(max_tokens, temperature, force_json),
    }
    return _gemini_extract(_gemini_post(payload, timeout=timeout))


_anthropic_client = None
_anthropic_key = None


def _anthropic_complete(system, user, max_tokens, temperature, force_json) -> str:
    global _anthropic_client, _anthropic_key
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if _anthropic_client is None or key != _anthropic_key:
        _anthropic_client = anthropic.Anthropic(api_key=key)
        _anthropic_key = key
    with _anthropic_client.messages.stream(
        model=ANTHROPIC_MODEL,
        max_tokens=min(max_tokens, 32000),
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        return stream.get_final_message().content[0].text.strip()


_CALLERS = {
    "ollama": _ollama_complete,
    "gemini": _gemini_complete,
    "anthropic": _anthropic_complete,
}


# ───────────────────────────────── API ──────────────────────────────────────

def complete(system: str, user: str, max_tokens: int = 4000,
             temperature: float = 0.7, force_json: bool = False,
             backends: list = None) -> str:
    """Tenta cada backend da cadeia até obter uma resposta não-vazia.

    `backends`: força uma ordem específica (ex.: ["anthropic","gemini"] para
    escalar pro chefe). Default = cadeia padrão (ollama→gemini→anthropic).
    """
    order = backends if backends is not None else chain()
    order = [b for b in order if _backend_available(b)]
    if not order:
        record_alert("ai", "Nenhuma IA disponível: ligue o Ollama local ou configure uma "
                     "chave (Gemini). A análise não vai rodar.", level="error")
        return ""
    last_err = None
    for b in order:
        try:
            out = _CALLERS[b](system, user, max_tokens, temperature, force_json)
            if out and out.strip():
                if b != order[0] or backends is not None:
                    print(f"[LLM] usou '{b}'")
                clear_alert("ai")
                return out.strip()
        except Exception as e:
            last_err = e
            print(f"[LLM] '{b}' falhou: {str(e)[:120]} — escalando...")
            continue
    if last_err:
        raise last_err
    return ""


# ─────────────────────────────── VISÃO ──────────────────────────────────────

def _ollama_has_vision() -> bool:
    """True se o modelo de visão (llama3.2-vision) está baixado no Ollama."""
    models = _ollama_models()
    base = OLLAMA_VISION_MODEL.split(":")[0]
    return any(m == OLLAMA_VISION_MODEL or m.startswith(base) for m in models)


def vision_available(backend: str) -> bool:
    if backend == "ollama":
        return _ollama_has_vision()
    if backend == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if backend == "gemini":
        return len(_gemini_keys()) > 0
    return False


def vision_chain() -> list:
    """Backends com visão disponíveis, na ordem da tarefa vision_verify."""
    return [b for b in chain_for("vision_verify") if vision_available(b)]


def _ollama_vision(system, user, image_b64, max_tokens, temperature, force_json) -> str:
    model = OLLAMA_VISION_MODEL
    if model not in _ollama_models():                 # tenta o que estiver baixado
        base = model.split(":")[0]
        model = next((m for m in _ollama_models() if m.startswith(base)), model)
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": [image_b64]},
        ],
        "options": {**_OLLAMA_OPTS, "temperature": temperature, "num_predict": max_tokens},
    }
    if force_json:
        payload["format"] = "json"
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    return (data.get("message", {}) or {}).get("content", "").strip()


def _anthropic_vision(system, user, image_b64, max_tokens, temperature, force_json,
                      media_type="image/jpeg") -> str:
    global _anthropic_client, _anthropic_key
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if _anthropic_client is None or key != _anthropic_key:
        _anthropic_client = anthropic.Anthropic(api_key=key)
        _anthropic_key = key
    msg = _anthropic_client.messages.create(
        model=ANTHROPIC_MODEL, max_tokens=min(max_tokens, 4000),
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": user},
        ]}],
    )
    return msg.content[0].text.strip()


def _gemini_vision(system, user, image_b64, max_tokens, temperature, force_json,
                   media_type="image/jpeg") -> str:
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [
            {"inline_data": {"mime_type": media_type, "data": image_b64}},
            {"text": user},
        ]}],
        "generationConfig": _gemini_gen_cfg(max_tokens, temperature, force_json),
    }
    return _gemini_extract(_gemini_post(payload))


_VISION_CALLERS = {
    "ollama": _ollama_vision,
    "anthropic": _anthropic_vision,
    "gemini": _gemini_vision,
}


def vision_complete(system: str, user: str, image_b64: str, max_tokens: int = 500,
                    temperature: float = 0.3, force_json: bool = False,
                    backends: list = None) -> str:
    """Chamada de VISÃO com fallback (Ollama vision → Anthropic → Gemini)."""
    order = backends if backends is not None else chain_for("vision_verify")
    order = [b for b in order if vision_available(b)]
    last_err = None
    for b in order:
        try:
            out = _VISION_CALLERS[b](system, user, image_b64, max_tokens,
                                     temperature, force_json)
            if out and out.strip():
                if b != order[0]:
                    print(f"[Vision] usou '{b}'")
                return out.strip()
        except Exception as e:
            last_err = e
            print(f"[Vision] '{b}' falhou: {str(e)[:100]} — escalando...")
            continue
    if last_err:
        raise last_err
    return ""
