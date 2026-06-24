"""
Camada de LLM com roteamento POR TAREFA e escalonamento entre provedores.

Provedores: groq (texto, rápido+grátis, cota própria) · gemini (texto+visão, cota
free POR MODELO, pool multi-chave com rotação) · ollama (LOCAL, grátis/offline,
reserva) · anthropic/Claude (PAGO — só no Modo Qualidade ou se escolhido no painel).

A ordem efetiva por tarefa está em _TASK_DEFAULTS (ver chain_for). Em geral: tarefas
de TEXTO → Groq-first (Ollama/Gemini de reserva); VISÃO → Gemini/Ollama (Groq não tem
visão). Cada chamada cai pro próximo da cadeia em erro/cota; tudo filtrado por
disponibilidade (sem chave = pulado). A SELEÇÃO de B-roll não usa LLM (é legenda/tags).

Config por env / ~/.vsl_config.json:
  LLM_BACKEND    = força um provedor em TODAS as tarefas ('' / 'auto' = roteamento)
  OLLAMA_MODEL   = "qwen2.5:7b"
  GEMINI_MODEL   = "gemini-flash-latest"  (thinking → thinkingBudget=0; precisa chave)
  GROQ_MODEL     = "llama-3.3-70b-versatile"  (precisa GROQ_API_KEY, gsk_...)
  MODELO_CLAUDE  = "claude-sonnet-4-6"  (precisa ANTHROPIC_API_KEY)
"""
import os
import re
import json
import time
import base64
import urllib.request
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llama3.2-vision:11b")
# gemini-flash-latest: cota free separada (fresca quando 2.5/2.0 estouram) e rápido.
# É "thinking" → exige thinkingBudget=0 (ver _gemini_gen_cfg) senão trunca/vem vazio.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Groq: API compatível com OpenAI, MUITO rápida e free tier generoso (cota separada
# do Google). Só texto (sem visão). Ótimo p/ classificador/diretor. Chave gsk_...
GROQ_URL = os.environ.get("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
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

# Roteamento por TAREFA (sistema híbrido grátis). Editável em ~/.vsl_config.json:
#   "provider_override": {"classifier":"ollama","phoenix":"gemini", ...}
# Princípio: TEXTO → Groq-first (rápido, grátis, cota separada do Google), com Ollama
# LOCAL e Gemini de reserva. VISÃO (vision_verify/auto_tag) → Gemini/Ollama (Groq não
# tem visão). Claude entra só via Modo Qualidade (_QUALITY_TASKS) ou quando escolhido no
# painel (LLM_BACKEND). Cada provedor só entra se tiver chave; senão chain_for o filtra e
# cai no próximo. (OpenRouter foi removido — cota free de ~50/dia não aguentava uma VSL.)
_TASK_DEFAULTS = {
    "director":      ["groq", "gemini", "ollama"],
    "classifier":    ["groq", "ollama", "gemini"],
    "ugc_prompt":    ["groq", "ollama", "gemini"],
    "vision_verify": ["gemini", "ollama"],   # visão: sem Groq
    "auto_tag":      ["gemini", "ollama"],
    "phoenix":       ["groq", "gemini", "ollama"],
    "context":       ["groq", "gemini", "ollama"],
}
_CONFIG_PATH = Path.home() / ".vsl_config.json"

# Cache do config por mtime — evita reler ~/.vsl_config.json do disco N× por VSL
# (_provider_override/_preferred_backend/_groq_key/_gemini_keys rodam muitas vezes).
_CFG_CACHE = {"mtime": None, "data": {}}


def _config() -> dict:
    try:
        m = _CONFIG_PATH.stat().st_mtime
    except Exception:
        return {}
    if m != _CFG_CACHE["mtime"]:
        try:
            _CFG_CACHE["data"] = json.loads(_CONFIG_PATH.read_text())
        except Exception:
            _CFG_CACHE["data"] = {}
        _CFG_CACHE["mtime"] = m
    return _CFG_CACHE["data"]


# Cache TTL da lista de modelos do Ollama — _backend_available/resolve_ollama_model são
# chamados por segmento; sem cache eram N roundtrips HTTP (cada um até 4s) por VSL.
_OLLAMA_MODELS_CACHE = {"ts": 0.0, "val": []}
_OLLAMA_MODELS_TTL = float(os.environ.get("OLLAMA_MODELS_TTL", "10"))

# Em modelos locais, lotes grandes ficam lentos/instáveis — o diretor processa
# os segmentos em lotes deste tamanho. (Gemini/Anthropic fazem tudo de uma vez.)
LOCAL_CHUNK = int(os.environ.get("LLM_LOCAL_CHUNK", "20"))


def _provider_override() -> dict:
    try:
        ov = _config().get("provider_override")
        return ov if isinstance(ov, dict) else {}
    except Exception:
        return {}




# Modo Qualidade Alta: usa Claude (anthropic) nas tarefas de ALTO VALOR (entendimento,
# diretor, visão, phoenix), mantendo as de alto volume (classificador/ugc) baratas.
_QUALITY_MODE = False
_QUALITY_TASKS = {"director", "vision_verify", "phoenix", "context", "auto_tag"}


def set_quality_mode(on: bool) -> None:
    global _QUALITY_MODE
    _QUALITY_MODE = bool(on)


def _preferred_backend() -> str:
    """Backend FORÇADO pelo usuário (seletor 'Modelo IA'): env LLM_BACKEND ou config
    llm_backend. 'auto'/'' = sem forçar (usa o roteamento B inteligente)."""
    p = (os.environ.get("LLM_BACKEND", "") or "").strip().lower()
    if not p:
        try:
            p = (_config().get("llm_backend", "") or "").strip().lower()
        except Exception:
            p = ""
    return p if p in ("ollama", "gemini", "anthropic", "groq") else ""


def chain_for(task: str) -> list:
    """Cadeia de backends para uma TAREFA: se o usuário escolheu um backend fixo, ele
    vem primeiro; senão usa o override do config → default da tarefa. Filtra por
    disponibilidade. (Claude entra aqui só quando escolhido — evita custo surpresa.)"""
    ov = _provider_override().get(task)
    if ov:
        order = [b.strip() for b in str(ov).split(",") if b.strip()]
    else:
        order = list(_TASK_DEFAULTS.get(task, DEFAULT_CHAIN))
    pref = _preferred_backend()
    if pref:
        order = [pref] + [b for b in order if b != pref]
    # Modo Qualidade Alta: Claude na frente nas tarefas de alto valor (se houver chave).
    elif _QUALITY_MODE and task in _QUALITY_TASKS and _backend_available("anthropic"):
        order = ["anthropic"] + [b for b in order if b != "anthropic"]
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
    now = time.time()
    if now - _OLLAMA_MODELS_CACHE["ts"] < _OLLAMA_MODELS_TTL:
        return _OLLAMA_MODELS_CACHE["val"]
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=4) as resp:
            data = json.loads(resp.read())
        models = [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        models = []
    _OLLAMA_MODELS_CACHE["ts"] = now
    _OLLAMA_MODELS_CACHE["val"] = models
    return models


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


def _groq_key() -> str:
    k = os.environ.get("GROQ_API_KEY", "")
    if k:
        return k
    try:
        return _config().get("groq_api_key", "") or ""
    except Exception:
        return ""


def _backend_available(name: str) -> bool:
    if name == "ollama":
        return len(_ollama_models()) > 0
    if name == "gemini":
        return len(_gemini_keys()) > 0
    if name == "groq":
        return bool(_groq_key())
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
    return {b: _backend_available(b) for b in ("ollama", "groq", "gemini", "anthropic")}


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
        cfg = _config()
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


def _groq_complete(system, user, max_tokens, temperature, force_json) -> str:
    key = _groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY ausente")
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        GROQ_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        clear_alert("groq")
        return (data["choices"][0]["message"]["content"] or "").strip()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            record_alert("groq", "Groq atingiu o limite de uso (429) — usando reserva.")
        elif e.code in (401, 403):
            record_alert("groq", "Chave Groq inválida/revogada — verifique (gsk_…).")
        raise


_CALLERS = {
    "ollama": _ollama_complete,
    "gemini": _gemini_complete,
    "anthropic": _anthropic_complete,
    "groq": _groq_complete,
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
    imgs = image_b64 if isinstance(image_b64, list) else [image_b64]
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": imgs},
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
    imgs = image_b64 if isinstance(image_b64, list) else [image_b64]
    content = [{"type": "image", "source": {"type": "base64",
                "media_type": media_type, "data": im}} for im in imgs]
    content.append({"type": "text", "text": user})
    msg = _anthropic_client.messages.create(
        model=ANTHROPIC_MODEL, max_tokens=min(max_tokens, 4000),
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return msg.content[0].text.strip()


def _gemini_vision(system, user, image_b64, max_tokens, temperature, force_json,
                   media_type="image/jpeg") -> str:
    imgs = image_b64 if isinstance(image_b64, list) else [image_b64]
    parts = [{"inline_data": {"mime_type": media_type, "data": im}} for im in imgs]
    parts.append({"text": user})
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": parts}],
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
