"""
Servidor local FastAPI — backend do plugin VSL para Premiere Pro.
"""
import os
# Evita o spam/deadlock do tokenizers quando o processo dá fork (CLIP + threads).
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# Garante que ffmpeg/ffprobe (Homebrew/MacPorts) sejam encontrados mesmo quando o
# servidor é lançado por um contexto com PATH mínimo (app GUI, launchd, nohup, etc.).
for _bin in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
    if os.path.isdir(_bin) and _bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")
import sys
import time
import json
import asyncio
import hashlib
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

import llm
import vsl_context
from transcribe import transcribe, transcribe_composition, word_anchor
import highlights
from broll_index import index_folder
from matcher import rank_segments, make_result, OK_THRESHOLD
import broll_select
import broll_classifier
import broll_score
import broll_search
import broll_vision_verify
import asset_tagger
import ugc_prompt_gen
import copy_chief
from compliance import apply_compliance, detect_vertical, vertical_from_path
from compliance import _load_rules as _compliance_rules
from rhythm import apply_rhythm
from vsl_director import analyze_full_vsl
from copymerda import analyze_and_generate_prompts
from higgsfield_gen import generate_clip as higgs_generate

app = FastAPI(title="VSL B-Roll Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_session: Dict = {}
_progress: Dict = {"step": "", "current": 0, "total": 0, "detail": ""}
_phoenix_map: List[Dict] = []   # mapa de B-roll do PHOENIX, se o editor importou

CONFIG_PATH = Path.home() / ".vsl_config.json"


def _load_config() -> Dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_config(data: Dict):
    current = _load_config()
    current.update(data)
    CONFIG_PATH.write_text(json.dumps(current, indent=2))


def _load_keys_into_env():
    """Carrega chaves salvas no ambiente — hierarquia LLM (auxiliar/chefe) ativa desde o boot."""
    cfg = _load_config()
    if cfg.get("anthropic_api_key") and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = cfg["anthropic_api_key"]
    # Gemini: aceita lista (gemini_api_keys) ou string única; junta no env (o pool
    # em llm.py separa por vírgula e rotaciona).
    _gk = cfg.get("gemini_api_keys")
    if isinstance(_gk, list) and _gk:
        os.environ.setdefault("GEMINI_API_KEY", ",".join(str(k).strip() for k in _gk if str(k).strip()))
    elif cfg.get("gemini_api_key") and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = cfg["gemini_api_key"]
    if cfg.get("groq_api_key") and not os.environ.get("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = cfg["groq_api_key"]
    if cfg.get("pexels_api_key") and not os.environ.get("PEXELS_API_KEY"):
        os.environ["PEXELS_API_KEY"] = cfg["pexels_api_key"]
    _lb = (cfg.get("llm_backend") or "").strip().lower()
    if _lb in ("ollama", "groq", "gemini", "anthropic"):  # 'auto'/'' = roteamento inteligente
        os.environ["LLM_BACKEND"] = _lb


_load_keys_into_env()


def set_progress(step: str, current: int = 0, total: int = 0, detail: str = ""):
    _progress.update({"step": step, "current": current, "total": total, "detail": detail})


# ── Tagging robusto: roda em SUBPROCESSO destacado (não em thread daemon) ──
# Motivo: em Macs com pouca RAM o jetsam do macOS mata processos sob pressão SEM
# crash report (SIGKILL). Se o tagging vivesse dentro do servidor, morria junto e o
# progresso (em memória) zerava. Agora: subprocesso independente + progresso em
# ARQUIVO. Sobrevive a reinício do servidor; se o subprocesso for morto no meio, o
# /progress detecta o PID morto e RETOMA de onde parou (cada clip é salvo na hora).
_TAG_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tag_state.json")
_TAG_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tag_run.log")
_tag_popen = None  # handle do subprocesso (pra dar poll/reap e não virar zumbi)


def _pid_alive(pid) -> bool:
    """True se o PID está vivo. Usa o handle do Popen quando temos (poll() coleta o
    zumbi — senão um filho morto fica 'defunct' e o os.kill(pid,0) mente que vive).
    Sem handle (pid de outra instância do servidor) o órfão já foi coletado pelo
    launchd, então o os.kill basta."""
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if _tag_popen is not None and _tag_popen.pid == pid:
        return _tag_popen.poll() is None
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_tag_state() -> Optional[dict]:
    try:
        with open(_TAG_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_tag_state(st: dict) -> None:
    try:
        tmp = _TAG_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, _TAG_STATE_FILE)
    except Exception:
        pass


def _clear_tag_state() -> None:
    try:
        os.remove(_TAG_STATE_FILE)
    except OSError:
        pass


def _spawn_tagger(folder: str, enrich: bool = False, force: bool = False) -> Optional[int]:
    """Lança asset_tagger.py como subprocesso destacado (sobrevive ao servidor)."""
    args = [sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_tagger.py"),
            folder, "--progress-file", _TAG_STATE_FILE]
    if enrich:
        args.append("--enrich")
    if force:
        args.append("--force")
    global _tag_popen
    try:
        logf = open(_TAG_LOG_FILE, "a")
        p = subprocess.Popen(args, stdout=logf, stderr=logf,
                             start_new_session=True,  # destaca do grupo do servidor
                             cwd=os.path.dirname(os.path.abspath(__file__)))
        _tag_popen = p   # guarda o handle pra poll()/reap (evita zumbi)
        return p.pid
    except Exception as e:
        print(f"[tag_assets] falha ao lançar subprocesso: {str(e)[:120]}")
        return None


def _ensure_tag_alive() -> None:
    """Se o job de tagging morreu no meio (jetsam/crash), relança pra RETOMAR."""
    st = _read_tag_state()
    if not st or st.get("step") == "done":
        return
    if _pid_alive(st.get("pid")):
        return
    # PID morto e ainda não terminou → retoma. Throttle de 8s evita respawn em loop
    # (o painel faz polling a cada ~1-2s).
    if time.time() - st.get("_relaunch_at", 0) < 8:
        return
    folder = st.get("folder")
    if not folder or not os.path.isdir(folder):
        return
    st["_relaunch_at"] = time.time()
    _write_tag_state(st)
    new_pid = _spawn_tagger(folder, enrich=bool(st.get("enrich")), force=bool(st.get("force")))
    if new_pid:
        st["pid"] = new_pid
        _write_tag_state(st)
        print(f"[tag_assets] subprocesso morreu — retomado (pid {new_pid})")


class ProcessRequest(BaseModel):
    video_path: str
    broll_folder: str
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    generated_dir: Optional[str] = None
    higgsfield_model: Optional[str] = None
    # Composição completa da timeline (todos os clipes), vinda do plugin.
    # Cada item: {path, seq_start, seq_end, in_point, out_point, track}.
    composition: Optional[List[Dict]] = None
    # Doc/roteiro da VSL em texto — Etapa 1 da IA: entender expert/produto/seções.
    vsl_doc: Optional[str] = None
    groq_api_key: Optional[str] = None         # Groq (texto rápido/grátis)
    # Transcrição do Premiere (conteúdo .srt/.vtt) — já em tempo de SEQUÊNCIA.
    # Se vier, usa direto e pula o Whisper (melhor qualidade, mais rápido).
    transcript_srt: Optional[str] = None
    # Caminhos dos B-rolls já presentes na timeline (V2+) — excluídos do pool de
    # seleção pra não repetir o mesmo clipe que o editor já colou (#2a).
    timeline_broll_paths: Optional[List[str]] = None
    # Modelo escolhido pelo usuário p/ as etapas de trabalho: ollama|gemini|anthropic
    llm_backend: Optional[str] = None
    # Verificação Claude Vision no top-5 da busca (lento/custo — opcional)
    vision_verify: Optional[bool] = False
    # Modo Qualidade Alta: usa Claude (pago) nas tarefas de alto valor + visão + clipes curtos.
    quality_mode: Optional[bool] = False
    # Densidade de B-roll: "calm" (menos cortes) | "normal" | "intense" (mais cortes).
    # Controla o fatiamento de trechos longos em vários slots (mais cobertura).
    broll_density: Optional[str] = None
    # Vertical/nicho manual (WL|ED|NR|PT|VS|JT|FG) — vazio = detecta do doc/pasta.
    vertical: Optional[str] = None
    # Pasta de clipes sugestivos/+18 para vertical ED — indexada localmente, nunca vai p/ cloud.
    ed_folder: Optional[str] = None
    # Chave Pexels Videos API para fallback online quando biblioteca local não tem bom clip.
    pexels_api_key: Optional[str] = None


class LibraryConfigRequest(BaseModel):
    # Todos opcionais — o painel salva o que tiver preenchido (persiste no
    # ~/.vsl_config.json e sobrevive a recarregar o painel).
    library_folder: Optional[str] = None
    broll_folder: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None        # pode ter várias separadas por vírgula
    gemini_api_keys: Optional[List[str]] = None # pool de chaves (rotação na cota)
    groq_api_key: Optional[str] = None          # Groq (texto, rápido/grátis)
    generated_dir: Optional[str] = None
    video_path: Optional[str] = None
    llm_backend: Optional[str] = None
    provider_override: Optional[Dict] = None   # roteamento por tarefa (híbrido grátis)
    broll_density: Optional[str] = None        # calm|normal|intense (fatiamento de trechos longos)
    vertical: Optional[str] = None             # nicho manual (WL|ED|NR|PT|VS|JT|FG) ou vazio=auto
    pexels_api_key: Optional[str] = None       # Pexels (fallback online quando lib local não tem)
    ed_folder: Optional[str] = None            # pasta ED+ (clipes sugestivos, só vertical ED, só local)


class LearnProjectRequest(BaseModel):
    # B-rolls e clipes da timeline de um projeto FINALIZADO (getSequenceComposition).
    video_clips: List[Dict] = []
    # Clipes de narração (V1) — usados pra transcrever com Whisper se não vier .srt.
    narration_clips: List[Dict] = []
    # Transcrição (.srt do Premiere) em tempo de sequência — opcional (atalho).
    transcript_srt: Optional[str] = None
    # Identificação do projeto (nome da sequência) — banco cumulativo não reprocessa.
    project_name: Optional[str] = None


class ApproveRequest(BaseModel):
    approved_indices: List[int]
    rejected_indices: List[int]


class GenerateSegmentRequest(BaseModel):
    segment_index: int
    ugc_prompt: str


class SegmentActionRequest(BaseModel):
    index: int
    action: str   # "accept" | "reject" | "swap"


@app.get("/status")
def status():
    return {"ok": True, "session": bool(_session)}


_THUMB_DIR = Path(tempfile.gettempdir()) / "vsl_thumbs"


@app.get("/thumbnail")
def thumbnail(path: str):
    """Frame de preview (JPEG pequeno) de um b-roll — pro painel mostrar o visual."""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "arquivo não encontrado")
    _THUMB_DIR.mkdir(exist_ok=True)
    key = hashlib.md5(path.encode()).hexdigest()[:16]
    thumb = _THUMB_DIR / f"{key}.jpg"
    if not thumb.exists():
        subprocess.run(
            ["ffmpeg", "-nostdin", "-ss", "1", "-i", path, "-frames:v", "1",
             "-vf", "scale=260:-1", "-q:v", "5", "-y", str(thumb)],
            capture_output=True, timeout=20,
        )
    if not thumb.exists() or thumb.stat().st_size == 0:
        raise HTTPException(500, "falha ao gerar thumbnail")
    return Response(content=thumb.read_bytes(), media_type="image/jpeg")


@app.get("/llm_status")
def llm_status():
    """Quais backends de LLM estão ativos e qual é o trabalhador principal."""
    return {
        "available": llm.available(),
        "chain": llm.chain(),
        "primary": llm.primary_backend(),
        "backends": llm.status(),  # {ollama, groq, gemini, anthropic}
    }


@app.get("/ai_health")
def ai_health():
    """Semáforo dos providers (sistema híbrido grátis): Ollama local, Gemini, Claude,
    + qual provider cada tarefa vai usar de fato."""
    models = llm._ollama_models()
    tasks = ("director", "classifier", "ugc_prompt", "vision_verify", "auto_tag", "phoenix", "context")
    return {
        "ollama": {
            "running": len(models) > 0,
            "models": models,
            "has_vision": llm._ollama_has_vision(),
            "vision_model": llm.OLLAMA_VISION_MODEL,
            "text_model": llm.OLLAMA_MODEL,
        },
        "gemini": llm._backend_available("gemini"),
        "gemini_keys": len(llm._gemini_keys()),
        "groq": llm._backend_available("groq"),
        "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "vision_chain": llm.vision_chain(),
        "routing": {t: (llm.chain_for(t) or ["—"]) for t in tasks},
        "alerts": llm.get_alerts(),
    }


@app.get("/ai_alerts")
def ai_alerts():
    """Alertas ativos de API (cota/erro/chave) — pro painel avisar o usuário."""
    return {"alerts": llm.get_alerts()}


class TestKeyRequest(BaseModel):
    key: Optional[str] = None          # se vier, testa essa; senão, as configuradas


@app.post("/test_gemini")
def test_gemini(req: TestKeyRequest):
    """Testa chave(s) Gemini ao vivo (chamada real) e diz quais funcionam."""
    if req.key:
        return {"results": [llm.test_gemini_key(req.key)]}
    keys = llm._gemini_keys()
    if not keys:
        return {"results": [], "error": "nenhuma chave configurada"}
    return {"results": [llm.test_gemini_key(k) for k in keys]}


_CONFIG_FIELDS = [
    "library_folder", "broll_folder", "anthropic_api_key",
    "gemini_api_key", "gemini_api_keys", "groq_api_key", "generated_dir",
    "video_path", "llm_backend", "provider_override", "broll_density", "vertical",
    "pexels_api_key", "ed_folder",
]


@app.get("/config")
def get_config():
    cfg = _load_config()
    out = {k: cfg.get(k, "") for k in _CONFIG_FIELDS}
    out["configured"] = bool(cfg.get("library_folder"))
    return out


@app.post("/config")
def set_config(req: LibraryConfigRequest):
    # Salva só os campos enviados (não-None). Strings vazias são permitidas
    # (ex.: limpar um campo), exceto validação de pasta quando preenchida.
    data = {k: v for k, v in req.dict().items() if v is not None}

    lf = data.get("library_folder")
    if lf and not os.path.isdir(lf):
        raise HTTPException(400, f"Pasta não encontrada: {lf}")

    _save_config(data)
    return {"ok": True, **{k: data[k] for k in data if not k.endswith("api_key")}}


@app.get("/progress")
def progress_poll():
    """Polling simples — estado atual + alertas de API (cota/erro).
    Se houver um job de tagging (subprocesso) ativo ou recém-concluído, reporta ELE
    — e se o subprocesso tiver morrido no meio, retoma automaticamente."""
    _ensure_tag_alive()
    st = _read_tag_state()
    if st and st.get("step") in ("tagging", "done"):
        if _pid_alive(st.get("pid")) or st.get("step") == "done":
            return {"step": st.get("step", "tagging"),
                    "current": st.get("current", 0), "total": st.get("total", 0),
                    "detail": st.get("detail", ""), "alerts": llm.get_alerts()}
    return {**_progress, "alerts": llm.get_alerts()}


@app.post("/process")
async def process(req: ProcessRequest):
    global _session

    # Limpa qualquer estado de tagging (subprocesso) pra não shadowar o progresso do
    # vídeo no /progress. Processar e taguear não se sobrepõem na prática.
    _clear_tag_state()

    if req.anthropic_api_key:
        os.environ["ANTHROPIC_API_KEY"] = req.anthropic_api_key
    if req.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = req.gemini_api_key
    if req.groq_api_key:
        os.environ["GROQ_API_KEY"] = req.groq_api_key
    if req.pexels_api_key:
        os.environ["PEXELS_API_KEY"] = req.pexels_api_key
    if req.generated_dir:
        os.environ["GENERATED_DIR"] = req.generated_dir
    if req.higgsfield_model:
        os.environ["HIGGSFIELD_MODEL"] = req.higgsfield_model
    # Seletor "Modelo IA": 'auto' (ou vazio) = roteamento inteligente B; 'ollama'/
    # 'groq'/'gemini'/'anthropic' = força aquele backend em TODAS as etapas.
    _lb = (req.llm_backend or "").strip().lower()
    if _lb in ("ollama", "groq", "gemini", "anthropic"):
        os.environ["LLM_BACKEND"] = _lb
    else:
        os.environ.pop("LLM_BACKEND", None)

    # Modo Qualidade Alta: Claude nas tarefas de alto valor + visão ligada + clipes curtos.
    quality = bool(req.quality_mode) and llm._backend_available("anthropic")
    llm.set_quality_mode(quality)
    if req.quality_mode and not quality:
        print("[Qualidade Alta] pedido mas sem chave Anthropic — seguindo no modo normal.")

    composition = req.composition or []
    if not composition and not os.path.exists(req.video_path):
        raise HTTPException(400, f"Vídeo não encontrado: {req.video_path}")
    if not os.path.isdir(req.broll_folder):
        raise HTTPException(400, f"Pasta de B-rolls não encontrada: {req.broll_folder}")

    try:
        # 1. Transcrição.
        #    Preferência: transcrição do Premiere (.srt colado) — já em tempo de
        #    sequência, melhor qualidade e mais rápido (sem Whisper). Senão: composição
        #    inteira da timeline; senão: vídeo único.
        segments = None
        if req.transcript_srt and req.transcript_srt.strip():
            from transcribe import parse_srt_text
            set_progress("transcribing", detail="Lendo transcrição do Premiere (.srt)...")
            parsed = parse_srt_text(req.transcript_srt)
            if parsed:
                segments = parsed
                set_progress("transcribing", current=len(segments), total=len(segments),
                             detail=f"Transcrição do Premiere: {len(segments)} blocos")
            else:
                set_progress("transcribing",
                             detail="SRT do Premiere ilegível — caindo na transcrição automática...")

        if segments is None:
            if composition:
                set_progress("transcribing",
                             detail=f"Transcrevendo composição ({len(composition)} clipes)...")
                segments = await asyncio.get_event_loop().run_in_executor(
                    None, transcribe_composition, composition
                )
                # Fallback: composição sem áudio transcritível → cai no vídeo principal
                if not segments and os.path.exists(req.video_path):
                    set_progress("transcribing", detail="Composição vazia — usando vídeo principal...")
                    segments = await asyncio.get_event_loop().run_in_executor(
                        None, transcribe, req.video_path
                    )
            else:
                set_progress("transcribing", detail="Whisper processando áudio...")
                segments = await asyncio.get_event_loop().run_in_executor(
                    None, transcribe, req.video_path
                )
            set_progress("transcribing", current=len(segments), total=len(segments),
                         detail=f"{len(segments)} segmentos encontrados")

        # Falha que PARECE sucesso: 0 segmentos = nenhuma fala detectada. Em vez de
        # entregar "done" com 0 B-rolls (o usuário não sabe se foi mão-de-vaca ou falha),
        # aborta com mensagem acionável.
        if not segments:
            msg = ("Nenhuma fala detectada. Confira se a faixa de narração (áudio/V1) "
                   "tem voz e está na timeline, ou cole a legenda (.srt) do Premiere "
                   "antes de processar.")
            set_progress("error", detail=msg)
            raise HTTPException(422, msg)

        # 2. Etapa 1 da IA — ENTENDER a VSL a partir do doc (se fornecido)
        context: Dict = {}
        if req.vsl_doc and req.vsl_doc.strip() and llm.available():
            set_progress("understanding", detail="Lendo o doc da VSL (expert, produto, seções)...")
            context = await asyncio.get_event_loop().run_in_executor(
                None, vsl_context.extract_context, req.vsl_doc
            )
            if context:
                exp = (context.get("expert") or {}).get("name", "")
                prod = (context.get("product") or {}).get("name", "")
                set_progress("understanding",
                             detail=f"Contexto: {prod or context.get('niche','')} · expert {exp or '?'}")

        # 3. Diretor VSL + Copymerda — Gemini-first (rápido); Ollama local de reserva.
        if llm.available():
            _dir_chain = llm.chain_for("director") or llm.chain()
            backend_label = (_dir_chain[0].partition("=")[0] if _dir_chain else "local")
            set_progress("analyzing", detail=f"Diretor VSL ({backend_label}) analisando arco...")

            def _dir_progress(done, total):
                set_progress("analyzing", current=done, total=total,
                             detail=f"Diretor VSL ({backend_label}) — {done}/{total} trechos")
            segments = await asyncio.get_event_loop().run_in_executor(
                None, lambda: analyze_full_vsl(segments, context, progress_cb=_dir_progress)
            )

            lettering_count = sum(1 for s in segments if s.get("lettering"))
            if lettering_count:
                set_progress("analyzing", detail=f"{lettering_count} lettering marcado(s)")
        else:
            set_progress("analyzing", detail="LLM indisponível — pulando análise (matching por texto).")

        # 3. Indexação — biblioteca local (prioridade) + pasta do projeto
        library_folder = _load_config().get("library_folder", "")

        def index_with_progress(folder, label_prefix=""):
            def cb(current, total, name):
                set_progress("indexing", current=current, total=total,
                             detail=f"{label_prefix}{name}")
            return index_folder(folder, progress_cb=cb)

        library_brolls: list = []
        if library_folder and os.path.isdir(library_folder):
            set_progress("indexing", detail="Indexando biblioteca local...")
            raw = await asyncio.get_event_loop().run_in_executor(
                None, index_with_progress, library_folder, "[Biblioteca] "
            )
            library_brolls = [{**b, "_source": "library"} for b in raw]

        set_progress("indexing", detail="Indexando pasta do projeto...")
        project_brolls_raw = await asyncio.get_event_loop().run_in_executor(
            None, index_with_progress, req.broll_folder, "[Projeto] "
        )
        project_brolls = [{**b, "_source": "project"} for b in project_brolls_raw]

        # Pasta ED+ — indexada localmente e adicionada ao pool SOMENTE quando vertical=ED.
        # Clips marcados _local_only=True: nunca enviados p/ cloud (vision verify, Pexels).
        ed_folder = (req.ed_folder or _load_config().get("ed_folder") or "").strip()
        ed_brolls: list = []
        if ed_folder and os.path.isdir(ed_folder):
            # Detecta vertical provisoriamente (antes de checar o doc) só pra decidir carregar
            _pre_v = (req.vertical or os.environ.get("VSL_VERTICAL") or "").strip().upper()
            if _pre_v == "ED" or not _pre_v:   # ED manual ou auto (decide depois)
                set_progress("indexing", detail="Indexando pasta ED+ (local)...")
                ed_raw = await asyncio.get_event_loop().run_in_executor(
                    None, index_with_progress, ed_folder, "[ED+] "
                )
                # themes.txt na pasta ED+: temas globais injetados no doc de busca de TODOS os clips.
                # Ex.: "intimate couple, desire, passion, romance, sensual, bedroom, attraction"
                _themes_file = os.path.join(ed_folder, "themes.txt")
                _ed_themes = ""
                if os.path.exists(_themes_file):
                    try:
                        with open(_themes_file, encoding="utf-8") as _tf:
                            _ed_themes = _tf.read().strip()
                        print(f"[ED+] themes.txt carregado: {_ed_themes[:80]}")
                    except Exception:
                        pass
                else:
                    # Cria um themes.txt padrão se não existir
                    _default_themes = (
                        "intimate couple desire passion romance sensual bedroom attraction "
                        "love relationship kiss touch caress closeness warmth togetherness "
                        "emotional connection physical affection partner sex sexual intercourse "
                        "erection hard penis erect soft penis flaccid nude naked body skin "
                        "foreplay arousal orgasm climax pleasure seduction undressing lingerie "
                        "making love fuck fucking sexual performance virility masculinity "
                        "erectile dysfunction impotence libido testosterone stamina potency"
                    )
                    try:
                        with open(_themes_file, "w", encoding="utf-8") as _tf:
                            _tf.write(_default_themes)
                        _ed_themes = _default_themes
                        print(f"[ED+] themes.txt criado com temas padrão em: {_themes_file}")
                    except Exception:
                        pass
                ed_brolls = [{**b, "_source": "ed", "_local_only": True,
                              "_folder_themes": _ed_themes} for b in ed_raw]

        # Biblioteca na frente — matcher vai preferir esses primeiro.
        # ED+ só entra se a vertical confirmada for ED (verificado adiante).
        brolls = library_brolls + project_brolls

        # 4. Seleção do B-roll.
        #    PRIMÁRIO: busca semântica por embeddings (broll_search) — acha o clip
        #    que MOSTRA a descrição literal do classificador, sem depender de tags.
        #    Opcional: Claude Vision re-ranqueia o top-5 (vision_verify).
        #    Modo alternativo (SELECTION_MODE=tags): scoring por tags.
        #    Fallback: ranking CLIP + escolha por LLM.
        # Vertical: manual (painel) > detecção pelo doc/contexto > fallback pela pasta/vídeo.
        # Torna autoritativo via context["vertical"] (apply_compliance redetecta do context).
        _rules_v = _compliance_rules()
        _manual_v = (req.vertical or os.environ.get("VSL_VERTICAL") or "").strip().upper()
        if _manual_v in _rules_v.get("vertical_keywords", {}):
            vertical = _manual_v
        else:
            vertical = detect_vertical(context, _rules_v) \
                or vertical_from_path(req.broll_folder, _rules_v) \
                or vertical_from_path(library_folder, _rules_v) \
                or vertical_from_path(req.video_path or "", _rules_v)
        print(f"[Vertical] manual={_manual_v!r} detectado={vertical!r} ed_folder={ed_folder!r} ed_clips={len(ed_brolls)}")
        if vertical:
            if not isinstance(context, dict):
                context = {}
            context["vertical"] = vertical
        # Injeta ED+ no pool quando a vertical é ED.
        # ED+ vai NA FRENTE da biblioteca — prioridade máxima.
        if vertical == "ED" and ed_brolls:
            brolls = ed_brolls + project_brolls + library_brolls
            durations_ok = [b for b in ed_brolls if b.get("duration", 0) > 0]
            print(f"[ED+] {len(ed_brolls)} clips indexados, {len(durations_ok)} com duração > 0")
            set_progress("indexing",
                         detail=f"ED+: {len(ed_brolls)} clip(s) como pool principal (local)")
        for b in brolls:                      # anexa tags (histórico p/ bônus)
            b["tags"] = asset_tagger.load_tags(b["path"])
        tagged = [b for b in brolls if b.get("tags")]
        mode = os.environ.get("SELECTION_MODE", "embedding")
        profiles = None
        _exclude = set(req.timeline_broll_paths or ())   # pre-init (variantes precisam)
        selection_path = "embedding"

        try:
            # Classifica os trechos (descrição literal) quando o chefe está disponível
            if broll_classifier.available():
                set_progress("matching", detail="Classificando trechos (descrição literal)...")
                profiles = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: broll_classifier.classify_segments(segments, context)
                )
                # #Fase3: divide trechos que enumeram visuais ("3 ingredientes" → 3 clipes).
                before = len(segments)
                segments, profiles = broll_classifier.split_enumerations(segments, profiles)
                if len(segments) > before:
                    set_progress("matching", detail=f"Enumeração: {before}→{len(segments)} trechos (multi-B-roll)")
                # Fatia trechos LONGOS em vários slots (mais cobertura — ataca "seleciona
                # poucos"; cada slot pega um clipe distinto via dedup de sequência).
                # Default por vertical: ED (+18) quer imagem o tempo todo → "intense"
                # quando o editor não escolheu densidade. Demais verticais → "normal".
                _dens_default = "intense" if str(vertical or "").upper() == "ED" else "normal"
                _density = (req.broll_density or os.environ.get("BROLL_DENSITY")
                            or _load_config().get("broll_density") or _dens_default).strip().lower()
                if _density not in ("calm", "normal", "intense"):
                    _density = _dens_default
                before2 = len(segments)
                segments, profiles = broll_classifier.split_long_segments(segments, profiles, _density)
                if len(segments) > before2:
                    set_progress("matching",
                                 detail=f"Trechos longos fatiados: {before2}→{len(segments)} slots "
                                        f"(densidade {_density})")

            # #2 Ancoragem por palavra: começa o b-roll na palavra-chave concreta do
            # trecho (quando a transcrição tem tempos de palavra — Whisper). Não mexe na
            # janela da narração, só no start do b-roll. Pula sub-slots de rajada
            # (janela já precisa). Sem palavra casável → mantém o início do trecho.
            _anchored = 0
            for i, s in enumerate(segments):
                if not s.get("words") or s.get("_enum_group"):
                    continue
                q = ""
                if profiles and i < len(profiles) and profiles[i]:
                    q = profiles[i].get("visual_description") or ""
                q = q or s.get("visual_query") or s.get("text") or ""
                a = word_anchor(s["words"], q, s["start"], s["end"])
                if a is not None:
                    s["broll_start"] = a
                    _anchored += 1
            if _anchored:
                set_progress("matching",
                             detail=f"Ancoragem por palavra: {_anchored} b-roll(s) na palavra-chave")

            if mode == "tags" and broll_classifier.available() and \
               len(tagged) >= max(3, int(0.2 * len(brolls))):
                set_progress("matching", detail=f"Scoring por tags ({len(tagged)} tagados)...")
                ranked, matches = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: broll_score.select(segments, profiles, tagged, vertical)
                )
                selection_path = "scoring"
            elif brolls:
                # query = descrição literal (classificador) > direção do diretor > texto
                queries = [
                    ((profiles[i].get("visual_description") if profiles else "") or
                     seg.get("visual_query") or seg.get("text", ""))
                    for i, seg in enumerate(segments)
                ]
                rerank_fn = None
                if (req.vision_verify or quality) and broll_vision_verify.available():
                    # Visão local é lenta — reporta progresso por segmento pra não
                    # parecer travado e atualiza a barra do painel.
                    _vis_total = sum(1 for s in segments
                                     if (s["end"] - s["start"]) >= broll_search.MIN_BROLL_DURATION)
                    _vis_done = {"n": 0}
                    _vis_frames = 3 if quality else None     # Qualidade Alta: 3 frames/clip
                    _vis_label = "Claude vendo 3 frames" if quality else "IA de visão"
                    def rerank_fn(seg, q, c):
                        _vis_done["n"] += 1
                        set_progress("matching", current=_vis_done["n"], total=_vis_total,
                                     detail=f"{_vis_label} (trechos de risco) — "
                                            f"{_vis_done['n']} verificado(s)...")
                        # Clips _local_only (ED+) nunca saem da máquina — não enviar frames.
                        safe   = [x for x in c if not x.get("_local_only")]
                        local_ = [x for x in c if x.get("_local_only")]
                        verified = broll_vision_verify.rerank(seg.get("text", ""), q, safe,
                                                              n_frames=_vis_frames) if safe else []
                        # Merge por score — ED+ compete por slot, não fica empurrado pro fim.
                        return sorted(verified + local_,
                                      key=lambda x: x.get("score", 0.0), reverse=True)
                    set_progress("matching", detail="Busca semântica + visão nos trechos de risco...")
                else:
                    set_progress("matching", detail="Busca semântica por embeddings visuais...")

                def _sel_progress(done, total, found):
                    set_progress("matching", current=done, total=total,
                                 detail=f"{found} B-roll(s) encontrado(s) · {done}/{total} trechos")
                ranked, matches = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: broll_search.select(segments, queries, brolls, vertical,
                                                      rerank_fn, profiles, exclude_paths=_exclude,
                                                      progress_cb=_sel_progress)
                )
                selection_path = "embedding" + ("+vision" if rerank_fn else "")
            else:
                raise RuntimeError("sem brolls indexados")
        except Exception as e:
            print(f"[Seleção] busca semântica falhou ({str(e)[:100]}) — fallback CLIP+LLM.")
            set_progress("matching", detail="Fallback: ranking CLIP + escolha por LLM...")
            ranked = await asyncio.get_event_loop().run_in_executor(
                None, lambda: rank_segments(segments, brolls, context)
            )
            matches = await asyncio.get_event_loop().run_in_executor(
                None, lambda: broll_select.select(segments, ranked, context)
            )
            selection_path = "clip"

        # 4a. Compliance — bloqueia assets que violem regras ANTES do editor.
        #     Roda primeiro (validade do asset); o ritmo depois cuida do timing.
        set_progress("matching", detail="Compliance: validando assets...")
        compliance_info = apply_compliance(segments, matches, context)

        # 4b. Ritmo/timing — ajusta duração, gap, consecutivos e bloqueia
        #     momentos protegidos (CTA, preço, garantia). Roda in-place.
        set_progress("matching", detail="Aplicando regras de ritmo (timing)...")
        # Regra "nada > 3s na tela": modo intenso (default do ED) e Qualidade Alta
        # limitam cada b-roll a 3s → o visual troca antes de 3s.
        _rhythm_max = 3.0 if (quality or _density == "intense") else None
        # ED: piso de 1s (não 2s) — frase curta sexual ("veiny rock hard cock") também
        # recebe clipe em vez de virar buraco por "sem espaço para 2s".
        _rhythm_min = 1.0 if str(vertical or "").upper() == "ED" else None
        rhythm_counts = apply_rhythm(segments, matches, max_dur=_rhythm_max, min_dur=_rhythm_min)

        # 4c. Prompts UGC (Higgsfield) — só para segmentos sem B-roll local.
        #     Roda DEPOIS da seleção: N prompts viram M ≪ N prompts (só no_broll).
        _no_broll_segs = [seg for seg, m in zip(segments, matches)
                          if m.get("status") == "no_broll"]
        if _no_broll_segs and llm.available():
            set_progress("matching",
                         detail=f"Copymerda: prompts UGC para {len(_no_broll_segs)} trecho(s) sem B-roll...")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: analyze_and_generate_prompts(_no_broll_segs, context)
            )

        # 4d. Mapa do PHOENIX (se o editor clicou "Usar como base") — advisory:
        #     anota bloco/emoção/prioridade e bloqueia momentos 'prohibited'.
        phoenix_annotated = 0
        if _phoenix_map:
            phoenix_annotated = copy_chief.apply_map(segments, matches, _phoenix_map)

        # 5. Geração sob demanda — não gera automaticamente, mostra opção no painel
        # Clips com score baixo ficam como "generate" e o usuário escolhe gerar com Higgsfield
        needs_review = [
            {"index": i, **m}
            for i, m in enumerate(matches)
            if m["status"] == "review"
        ]

        # Marcadores de lettering
        lettering_markers = [
            {
                "start": seg["start"],
                "text":  seg.get("lettering_text", ""),
                "type":  seg.get("lettering_type", ""),
            }
            for seg in segments if seg.get("lettering")
        ]

        _session = {
            "video_path":        req.video_path,
            "segments":          segments,
            "matches":           matches,
            "ranked":            ranked,          # candidatos por segmento (p/ "trocar")
            "profiles":          profiles,        # perfil semântico p/ gerar UGC sob demanda
            "vertical":          vertical,
            "pending_review":    needs_review,
            "lettering_markers": lettering_markers,
            "context":           context,
            # Estado para geração de variantes on-demand (/variants/{n})
            "_variant_state": {
                "brolls":        brolls,
                "vertical":      vertical,
                "quality":       quality,
                "exclude_paths": _exclude if "embedding" in selection_path else set(),
            },
        }

        # Monta lista de segmentos enriquecidos para o painel
        enriched = []
        for i, (seg, match) in enumerate(zip(segments, matches)):
            enriched.append({
                "index":          i,
                "start":          seg["start"],
                "end":            seg["end"],
                "text":           seg["text"],
                "ugc_prompt":     seg.get("ugc_prompt", ""),
                "arc_position":   seg.get("arc_position", ""),
                "vsl_section":    seg.get("vsl_section", ""),
                "emotional_peak": seg.get("emotional_peak", 5),
                "broll_path":     match.get("broll_path", ""),
                "broll_filename": match.get("broll_filename", ""),
                "confidence":     match.get("confidence", 0),
                "status":         match.get("status", ""),
                "broll_source":   match.get("broll_source", ""),
                "select_reason":  match.get("select_reason", ""),
                "transition":     match.get("transition", ""),
                "phoenix":        seg.get("phoenix", None),
            })

        set_progress("done")

        return {
            "segments_total":    len(segments),
            "segments":          enriched,
            "matches":           matches,
            "needs_review":      needs_review,
            "lettering_markers": lettering_markers,
            "context":           context,
            "stats": {
                "ok":        sum(1 for m in matches if m["status"] == "ok"),
                "review":    len(needs_review),
                "no_broll":  sum(1 for m in matches if m["status"] == "no_broll"),
                "blocked":   sum(1 for m in matches if m["status"] == "blocked"),
                "compliance_blocked": compliance_info["blocked"],
                "generated": sum(1 for m in matches if m["status"] == "generated"),
                "error":     sum(1 for m in matches if m["status"] == "error"),
                "lettering": len(lettering_markers),
                "rhythm":    rhythm_counts,
                "vertical":  compliance_info["vertical"],
                "selection": selection_path,
                "tagged_assets": len(tagged),
                "phoenix_annotated": phoenix_annotated,
            }
        }

    except HTTPException:
        # erros já tratados (ex.: 422 sem fala) sobem com o status correto — não viram 500
        raise
    except Exception as e:
        set_progress("error", detail=str(e))
        raise HTTPException(500, str(e))


@app.get("/variants/{n}")
async def get_variant(n: int):
    """Gera uma variante alternativa da seleção (seed diferente → clips diferentes).
    Troca _session["matches"] para esta variante — inserir na timeline usa o que estiver ativo."""
    if not _session or not _session.get("_variant_state"):
        raise HTTPException(404, "Sem sessão ativa — processe a VSL primeiro.")
    if n < 1 or n > 5:
        raise HTTPException(400, "Variante deve ser 1-5.")

    vs       = _session["_variant_state"]
    segments = _session["segments"]
    profiles = _session.get("profiles")
    brolls   = vs["brolls"]
    vertical = vs["vertical"]
    context  = _session.get("context", {})
    quality  = vs.get("quality", False)
    exclude  = vs.get("exclude_paths", set())

    # Variant 1 = seed None (preferências de score puro, sem ruído)
    seed = None if n == 1 else (n - 1) * 7

    queries = [
        ((profiles[i].get("visual_description") if profiles and i < len(profiles) and profiles[i] else "")
         or seg.get("visual_query") or seg.get("text", ""))
        for i, seg in enumerate(segments)
    ]

    _, var_matches = await asyncio.get_event_loop().run_in_executor(
        None, lambda: broll_search.select(
            segments, queries, brolls, vertical,
            rerank_fn=None,    # sem vision verify nas variantes (muito lento)
            profiles=profiles,
            exclude_paths=exclude,
            seed=seed,
        )
    )

    apply_compliance(segments, var_matches, context)
    # Mesma regra "nada > 3s na tela" + piso de 1s das variantes do vertical ED.
    _is_ed_v = str(vertical or "").upper() == "ED"
    _var_max = 3.0 if (quality or _is_ed_v) else None
    _var_min = 1.0 if _is_ed_v else None
    apply_rhythm(segments, var_matches, max_dur=_var_max, min_dur=_var_min)

    # Troca a sessão ativa → inserir na timeline usará esta variante
    _session["matches"] = var_matches

    enriched = []
    for i, (seg, match) in enumerate(zip(segments, var_matches)):
        enriched.append({
            "index":          i,
            "start":          seg["start"],
            "end":            seg["end"],
            "text":           seg["text"],
            "ugc_prompt":     seg.get("ugc_prompt", ""),
            "arc_position":   seg.get("arc_position", ""),
            "vsl_section":    seg.get("vsl_section", ""),
            "emotional_peak": seg.get("emotional_peak", 5),
            "broll_path":     match.get("broll_path", ""),
            "broll_filename": match.get("broll_filename", ""),
            "confidence":     match.get("confidence", 0),
            "status":         match.get("status", ""),
            "broll_source":   match.get("broll_source", ""),
            "select_reason":  match.get("select_reason", ""),
            "transition":     match.get("transition", ""),
            "phoenix":        seg.get("phoenix", None),
        })

    stats = {
        "ok":                 sum(1 for m in var_matches if m["status"] == "ok"),
        "review":             sum(1 for m in var_matches if m["status"] == "review"),
        "no_broll":           sum(1 for m in var_matches if m["status"] == "no_broll"),
        "blocked":            sum(1 for m in var_matches if m["status"] == "blocked"),
        "compliance_blocked": sum(1 for m in var_matches if m["status"] == "blocked_compliance"),
        "generated":          sum(1 for m in var_matches if m["status"] == "generated"),
        "error":              sum(1 for m in var_matches if m["status"] == "error"),
    }
    return {"variant": n, "segments": enriched, "stats": stats}


@app.post("/learn_project")
def learn_project(req: LearnProjectRequest):
    """#L1 — aprende com um projeto finalizado: alinha B-rolls (V2+) ao texto da
    narração e guarda os pares no banco cumulativo. Texto vem do .srt (se houver)
    OU do Whisper (transcreve a narração) — não exige exportar nada."""
    import style_memory

    # Texto da narração em tempo de sequência: .srt (atalho) → Whisper (fallback).
    narration = []
    if req.transcript_srt and req.transcript_srt.strip():
        from transcribe import parse_srt_text
        narration = parse_srt_text(req.transcript_srt)
    if not narration and req.narration_clips:
        set_progress("learning", detail="Transcrevendo a narração do projeto (Whisper)...")
        try:
            narration = transcribe_composition(req.narration_clips)
        except Exception as e:
            print(f"[learn_project] Whisper falhou: {str(e)[:80]}")
            narration = []
    if not narration:
        raise HTTPException(400, "Sem texto da narração: carregue o .srt ou inclua os "
                                 "clipes de narração (V1) pra transcrever.")

    brolls = [c for c in (req.video_clips or [])
              if (c.get("track") or 0) >= 1 and c.get("path")]
    pairs = style_memory.pairs_from_timeline(brolls, narration)   # já filtra efeito/curto
    learned = style_memory.add_examples(
        pairs, project_id=req.project_name or "", project_name=req.project_name or "")
    st = style_memory.stats()
    return {"learned": learned, "pairs_found": len(pairs),
            "skipped": max(0, len(brolls) - len(pairs)),
            "total": st["examples"], "projects": st["projects"]}


class LearnFolderRequest(BaseModel):
    folder: str


def _sibling_srt(prproj_path: str) -> str:
    d = os.path.dirname(prproj_path)
    try:
        cands = [f for f in os.listdir(d) if f.lower().endswith((".srt", ".vtt"))]
    except OSError:
        return ""
    if not cands:
        return ""
    cands.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    return os.path.join(d, cands[0])


@app.post("/learn_folder")
def learn_folder(req: LearnFolderRequest):
    """#L1 lote — aprende de TODOS os .prproj de uma pasta (offline, sem abrir o
    Premiere). Texto vem do .srt irmão (preferido) ou do Whisper no host."""
    import style_memory
    import prproj_parser
    from transcribe import parse_srt_text

    if not req.folder or not os.path.isdir(req.folder):
        raise HTTPException(400, f"Pasta não encontrada: {req.folder}")
    projfiles = prproj_parser.find_project_files(req.folder)
    if not projfiles:
        raise HTTPException(400, "Nenhum .prproj na pasta.")

    results = []
    total_learned = 0
    for pf in projfiles:
        name = os.path.splitext(os.path.basename(pf))[0]
        set_progress("learning", detail=f"Lendo projeto {name}...")
        parsed = prproj_parser.prproj_to_clips(pf)
        if not parsed:
            results.append({"project": name, "error": "não parseou"})
            continue

        # Texto: SÓ o .srt irmão (tempo de sequência, exato). Whisper-via-.prproj é
        # não-confiável (mídia offline, áudio picado, offset errado) e geraria pares
        # desalinhados — melhor pular e pedir o .srt do que envenenar a memória.
        narration = []
        srt = _sibling_srt(pf)
        if srt:
            try:
                narration = parse_srt_text(Path(srt).read_text(encoding="utf-8", errors="replace"))
            except Exception:
                narration = []
        if not narration:
            results.append({"project": parsed["sequence_name"],
                            "error": "sem .srt na pasta — exporte a legenda do projeto p/ aprender"})
            continue

        brolls = [c for c in parsed["clips"] if c["path"] and c["path"] != parsed["host"]]
        pairs = style_memory.pairs_from_timeline(brolls, narration)   # filtra efeito/curto
        learned = style_memory.add_examples(
            pairs, project_id=parsed["sequence_name"], project_name=parsed["sequence_name"])
        total_learned += learned
        results.append({"project": parsed["sequence_name"], "learned": learned,
                        "brolls": len(brolls), "kept": len(pairs)})

    st = style_memory.stats()
    return {"total_learned": total_learned, "projects_processed": len(projfiles),
            "results": results, "memory": st}


@app.post("/style_reset")
def style_reset():
    """Zera a memória de estilo (recomeçar limpo)."""
    import style_memory
    before = style_memory.reset()
    return {"ok": True, "cleared": before}


@app.get("/style_stats")
def style_stats():
    import style_memory
    return style_memory.stats()


@app.post("/approve_all")
def approve_all():
    """Força todos os segmentos com B-roll para status 'ok', prontos para inserir."""
    if not _session:
        raise HTTPException(400, "Nenhuma sessão ativa.")
    matches = _session["matches"]
    changed = 0
    for m in matches:
        if m.get("broll_path") and m["status"] in ("review", "generate", "error"):
            m["status"] = "ok"
            changed += 1
    _session["matches"] = matches
    return {"ok": True, "changed": changed}


@app.post("/approve")
def approve(req: ApproveRequest):
    global _session
    if not _session:
        raise HTTPException(400, "Nenhuma sessão ativa.")

    matches = _session["matches"]
    for i in req.approved_indices:
        if 0 <= i < len(matches):
            matches[i]["status"] = "ok"
    for i in req.rejected_indices:
        if 0 <= i < len(matches):
            matches[i]["status"] = "generate_queued"

    _session["matches"] = matches
    return {"ok": True}


def _enriched_one(i: int) -> Dict:
    """Monta o dicionário de UM segmento no mesmo formato que o painel renderiza."""
    seg = _session["segments"][i]
    m = _session["matches"][i]
    return {
        "index":          i,
        "start":          seg["start"],
        "end":            seg["end"],
        "text":           seg["text"],
        "ugc_prompt":     seg.get("ugc_prompt", ""),
        "arc_position":   seg.get("arc_position", ""),
        "vsl_section":    seg.get("vsl_section", ""),
        "emotional_peak": seg.get("emotional_peak", 5),
        "broll_path":     m.get("broll_path", ""),
        "broll_filename": m.get("broll_filename", ""),
        "confidence":     m.get("confidence", 0),
        "status":         m.get("status", ""),
        "broll_source":   m.get("broll_source", ""),
        "select_reason":  m.get("select_reason", ""),
        "transition":     m.get("transition", ""),
    }


def _used_paths(exclude_index: int) -> set:
    """Caminhos de b-roll já em uso por OUTROS segmentos (evita duplicar no swap)."""
    used = set()
    for j, mm in enumerate(_session["matches"]):
        if j == exclude_index:
            continue
        if mm.get("broll_path") and mm.get("status") in ("ok", "review", "generated"):
            used.add(mm["broll_path"])
    return used


@app.post("/segment_action")
def segment_action(req: SegmentActionRequest):
    """Ações do editor por sugestão: accept / reject / swap. Empilha undo."""
    global _session
    if not _session:
        raise HTTPException(400, "Nenhuma sessão ativa.")
    i = req.index
    matches = _session["matches"]
    if not (0 <= i < len(matches)):
        raise HTTPException(400, "Índice inválido.")

    seg = _session["segments"][i]
    # snapshot para undo
    _session.setdefault("undo_stack", []).append((i, dict(matches[i])))

    if req.action == "accept":
        # feedback p/ scoring: asset aceito sobe na busca futura
        if matches[i].get("broll_path"):
            asset_tagger.update_stats(matches[i]["broll_path"], accepted=True)
        matches[i]["status"] = "ok"
        matches[i]["select_reason"] = "aprovado pelo editor"

    elif req.action == "reject":
        # feedback p/ scoring: asset rejeitado desce na busca futura
        if matches[i].get("broll_path"):
            asset_tagger.update_stats(matches[i]["broll_path"], rejected=True)
        matches[i] = make_result(seg, None, "no_broll", "rejeitado pelo editor")

    elif req.action == "swap":
        cands = []
        for r in _session.get("ranked", []):
            if r.get("index") == i:
                cands = r.get("candidates", [])
                break
        if not cands:
            _session["undo_stack"].pop()
            raise HTTPException(400, "Sem candidatos alternativos.")
        cur = matches[i].get("broll_path")
        used = _used_paths(i)
        # posição atual na lista; pega o próximo candidato livre depois dela
        cur_pos = next((k for k, c in enumerate(cands) if c["path"] == cur), -1)
        nxt = None
        for k in range(cur_pos + 1, len(cands)):
            if cands[k]["path"] not in used:
                nxt = cands[k]; break
        if nxt is None:                      # deu a volta — procura do início
            for k in range(0, cur_pos + 1):
                if cands[k]["path"] not in used and cands[k]["path"] != cur:
                    nxt = cands[k]; break
        if nxt is None:
            _session["undo_stack"].pop()
            raise HTTPException(400, "Não há outro b-roll livre para trocar.")
        status = "ok" if nxt["score"] >= OK_THRESHOLD else "review"
        matches[i] = make_result(seg, nxt, status, "trocado pelo editor")
    else:
        _session["undo_stack"].pop()
        raise HTTPException(400, "Ação inválida.")

    _session["matches"] = matches
    return {"ok": True, "segment": _enriched_one(i)}


@app.post("/segment_undo")
def segment_undo():
    """Desfaz a última ação do editor (accept/reject/swap)."""
    global _session
    if not _session or not _session.get("undo_stack"):
        raise HTTPException(400, "Nada para desfazer.")
    i, prev = _session["undo_stack"].pop()
    _session["matches"][i] = prev
    return {"ok": True, "segment": _enriched_one(i)}


@app.get("/matches")
def get_matches():
    if not _session:
        raise HTTPException(400, "Nenhuma sessão ativa.")
    insertable = [
        m for m in _session["matches"]
        if m["status"] in ("ok", "generated", "review") and m.get("broll_path")
    ]
    return {"insertable": insertable, "video_path": _session["video_path"]}


@app.get("/segments")
def get_segments():
    """Retorna todos os segmentos com B-roll selecionado e prompt UGC."""
    if not _session:
        raise HTTPException(400, "Nenhuma sessão ativa.")
    segments = _session.get("segments", [])
    matches  = _session.get("matches", [])
    result = []
    for i, (seg, match) in enumerate(zip(segments, matches)):
        result.append({
            "index":         i,
            "start":         seg["start"],
            "end":           seg["end"],
            "text":          seg["text"],
            "ugc_prompt":    seg.get("ugc_prompt", ""),
            "arc_position":  seg.get("arc_position", ""),
            "emotional_peak": seg.get("emotional_peak", 5),
            "broll_path":    match.get("broll_path", ""),
            "broll_filename": match.get("broll_filename", ""),
            "confidence":    match.get("confidence", 0),
            "status":        match.get("status", ""),
            "select_reason": match.get("select_reason", ""),
            "transition":    match.get("transition", ""),
            "broll_source":  match.get("broll_source", ""),
        })
    return {"segments": result}


_gen_progress: Dict = {}  # seg_index -> {"state": ..., "pct": ...}


@app.get("/gen_progress/{index}")
def gen_progress(index: int):
    return _gen_progress.get(index, {"state": "idle", "pct": 0})


def _build_ugc(idx: int, seg: Dict, fallback_prompt: str) -> Dict:
    """Monta o input do gerador UGC a partir do perfil do segmento + contexto."""
    profiles = _session.get("profiles") or []
    profile = profiles[idx] if (profiles and idx < len(profiles) and profiles[idx]) else None
    ctx = _session.get("context") or {}
    if profile is None:
        # sem perfil (caminho CLIP): classifica sob demanda, com contexto + fallback
        profile = broll_classifier.classify(seg.get("text", ""), ctx) \
            or broll_classifier.fallback_profile(seg)
    prod = (ctx.get("product") or {}).get("name", "")
    inp = {
        "script_excerpt": seg.get("text", ""),
        "block_type":  profile.get("block_type", ""),
        "emotion":     profile.get("emotion", ""),
        "energy_level": profile.get("energy_level", "medium"),
        "visual_type": profile.get("visual_type", ""),
        "product":     prod,
        "vertical":    _session.get("vertical", ""),
        "visual_style": ctx.get("niche", ""),
    }
    try:
        out = ugc_prompt_gen.generate(inp)
        if out and out.get("prompt"):
            return out
    except Exception as e:
        print(f"[UGC] erro ao montar prompt seg {idx}: {e}")
    # último recurso: o prompt curto que já vinha do copymerda
    return {"prompt": fallback_prompt, "negative_prompt": "", "aspect_ratio": "9:16"}


class AnalyzeCopyRequest(BaseModel):
    doc: Optional[str] = None


@app.post("/analyze_copy")
async def analyze_copy(req: AnalyzeCopyRequest):
    """PHOENIX (Copy Chief) — analisa a copy e devolve feedback + mapa de B-roll."""
    doc = (req.doc or "").strip()
    if not doc:
        raise HTTPException(400, "Envie o texto da copy (carregue o doc da VSL).")
    if not copy_chief.available():
        raise HTTPException(400, "PHOENIX precisa de ANTHROPIC_API_KEY ou GEMINI_API_KEY.")
    set_progress("analyzing_copy", detail="PHOENIX revisando a copy...")
    result = await asyncio.get_event_loop().run_in_executor(None, copy_chief.analyze, doc)
    set_progress("done")
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "Falha na análise."))
    return result


class HighlightsRequest(BaseModel):
    video_path: str
    target_seconds: int = 180
    contexts: List[str] = []
    ep_context: Optional[str] = ""
    engine: Optional[str] = "auto"   # auto | gemini_audio | transcript


@app.post("/highlights")
async def highlights_map(req: HighlightsRequest):
    """Mapeia os melhores momentos do vídeo (Whisper + LLM) → clips com in/out
    em segundos, prontos pro Highlights Cutter montar no Premiere."""
    path = (req.video_path or "").strip()
    if not path:
        raise HTTPException(400, "Informe o caminho do vídeo (detecte da timeline).")

    def _run():
        return highlights.map_highlights(
            path, req.target_seconds, req.contexts, req.ep_context or "",
            engine=req.engine or "auto",
            progress=lambda m: set_progress("highlights", detail=m),
        )

    set_progress("highlights", detail="Iniciando...")
    result = await asyncio.get_event_loop().run_in_executor(None, _run)
    set_progress("done")
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "Falha ao mapear highlights."))
    return result


class PhoenixMapRequest(BaseModel):
    broll_map: List[Dict] = []


@app.post("/phoenix_map")
def set_phoenix_map(req: PhoenixMapRequest):
    """'Usar como base': guarda o mapa do PHOENIX p/ o próximo processamento."""
    global _phoenix_map
    _phoenix_map = req.broll_map or []
    return {"ok": True, "count": len(_phoenix_map)}


class TranslateSrtRequest(BaseModel):
    srt_path: Optional[str] = ""
    srt_text: Optional[str] = ""
    target_lang: str = "pt"
    out_path: Optional[str] = ""
    offset_seconds: float = 0.0   # desloca os tempos (ancora a legenda no playhead)


@app.post("/translate_srt")
async def translate_srt_endpoint(req: TranslateSrtRequest):
    """Traduz uma legenda .srt mantendo os tempos EXATOS (tradução simultânea).
    Aceita srt_path (lê e grava ao lado, ingles.srt → ingles.pt.srt) ou srt_text."""
    import srt_translate
    path = (req.srt_path or "").strip()
    text = (req.srt_text or "").strip()
    if not path and not text:
        raise HTTPException(400, "Envie srt_path ou srt_text.")
    target = req.target_lang or "pt"

    offset = float(req.offset_seconds or 0.0)

    def _run():
        prog = lambda m: set_progress("translate_srt", detail=m)
        if path:
            return srt_translate.translate_srt_file(
                path, target=target, out_path=(req.out_path or None),
                offset_seconds=offset, progress=prog)
        res = srt_translate.translate_srt_text(text, target=target,
                                               offset_seconds=offset, progress=prog)
        if res.get("ok") and req.out_path:
            Path(req.out_path).write_text(res["srt"], encoding="utf-8")
            res["out_path"] = req.out_path
        return res

    set_progress("translate_srt", detail="Iniciando tradução...")
    result = await asyncio.get_event_loop().run_in_executor(None, _run)
    set_progress("done")
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "Falha ao traduzir a legenda."))
    return result


@app.post("/generate_segment")
async def generate_segment(req: GenerateSegmentRequest):
    """Gera um vídeo Higgsfield para um segmento específico (prompt UGC enriquecido)."""
    if not _session:
        raise HTTPException(400, "Nenhuma sessão ativa.")

    idx = req.segment_index
    matches = _session["matches"]
    if idx < 0 or idx >= len(matches):
        raise HTTPException(400, f"Índice inválido: {idx}")

    _gen_progress[idx] = {"state": "SUBMITTING", "pct": 0}

    def do_generate():
        try:
            seg = _session["segments"][idx]
            duration = seg["end"] - seg["start"]
            filename = f"higgs_{idx:03d}_{int(seg['start'])}s.mp4"

            def poll_cb(state, pct):
                _gen_progress[idx] = {"state": state, "pct": pct}

            # Melhoria 9: enriquece o prompt curto num prompt UGC completo
            # (formato celular + negative prompt + regras por bloco/vertical).
            ugc = _build_ugc(idx, seg, req.ugc_prompt)
            path = higgs_generate(
                ugc["prompt"], filename, poll_cb=poll_cb,
                negative_prompt=ugc.get("negative_prompt", ""),
                duration=min(max(duration, 2.0), 7.0),
                aspect_ratio=ugc.get("aspect_ratio", "9:16"),
            )
            matches[idx]["broll_path"]       = path
            matches[idx]["broll_filename"]   = filename
            matches[idx]["generated_prompt"] = ugc["prompt"]
            matches[idx]["negative_prompt"]  = ugc.get("negative_prompt", "")
            matches[idx]["status"]           = "generated"
            _gen_progress[idx] = {"state": "DONE", "pct": 100, "path": path}
        except Exception as e:
            matches[idx]["status"] = "error"
            matches[idx]["error"]  = str(e)
            _gen_progress[idx] = {"state": "ERROR", "pct": 0, "error": str(e)}

    asyncio.get_event_loop().run_in_executor(None, do_generate)
    return {"ok": True, "message": f"Gerando segmento {idx}..."}


class ReindexRequest(BaseModel):
    folder: Optional[str] = None
    rebuild: bool = False   # apaga o cache e reindexa do zero (schema novo)


@app.post("/reindex")
async def reindex(req: ReindexRequest):
    """Reindexa a biblioteca de B-rolls (embeddings visuais por frame). Roda no
    servidor (processo separado do CEP). 'rebuild' apaga o índice e refaz tudo."""
    folder = req.folder or _load_config().get("broll_folder", "")
    if not folder or not os.path.isdir(folder):
        raise HTTPException(400, f"Pasta inválida: {folder}")

    if req.rebuild:
        cache = os.path.join(folder, ".vsl_index.json")
        if os.path.exists(cache):
            try:
                os.remove(cache)
            except OSError:
                pass

    def cb(cur, total, name):
        set_progress("indexing", current=cur, total=total, detail=name)

    set_progress("indexing", detail="Reindexando biblioteca (embeddings visuais)...")
    brolls = await asyncio.get_event_loop().run_in_executor(
        None, lambda: index_folder(folder, progress_cb=cb)
    )
    set_progress("done")
    return {"ok": True, "indexed": len(brolls), "folder": folder}


# ── Busca em tempo real (modelo já carregado neste servidor persistente) ───────
_search_idx_cache: Dict = {}   # folder -> (mtime, [brolls])


def _load_search_index(folder: str) -> List[Dict]:
    """Carrega os b-rolls do .vsl_index.json (cacheado por mtime). O CLIP já está
    carregado neste processo — busca responde em <100ms, sem recarregar modelo."""
    cache_path = os.path.join(folder, ".vsl_index.json")
    if not os.path.exists(cache_path):
        return []
    mtime = os.path.getmtime(cache_path)
    cached = _search_idx_cache.get(folder)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(cache_path) as f:
        data = json.load(f)
    brolls = [{**v, "_source": "library"} for v in data.values()]
    _search_idx_cache[folder] = (mtime, brolls)
    return brolls


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    used_in_session: List[str] = []
    previous_embeddings: List = []
    vertical: Optional[str] = None
    folder: Optional[str] = None


@app.post("/search")
def search_broll(req: SearchRequest):
    """Busca semântica de UM trecho. Modelo já carregado → resposta rápida."""
    folder = req.folder or _load_config().get("broll_folder", "")
    brolls = _load_search_index(folder)
    if not brolls:
        raise HTTPException(400, "Índice vazio — rode 'Reindexar Biblioteca'.")
    res = broll_search.search(
        req.query, brolls, top_k=req.top_k, used=set(req.used_in_session),
        vertical=req.vertical, prev_embeddings=req.previous_embeddings,
    )
    return {"results": res}


class BatchSearchRequest(BaseModel):
    queries: List[Dict]   # [{query, block_id, vertical?}]
    top_k: int = 3
    folder: Optional[str] = None


@app.post("/batch_search")
def batch_search(req: BatchSearchRequest):
    """Busca a VSL inteira de uma vez, com consciência de sequência (M2) e
    diversidade (M3): acumula paths/embeddings escolhidos pra não repetir cena."""
    folder = req.folder or _load_config().get("broll_folder", "")
    brolls = _load_search_index(folder)
    if not brolls:
        raise HTTPException(400, "Índice vazio — rode 'Reindexar Biblioteca'.")
    path_emb = {b["path"]: (b.get("visual_embedding") or b.get("embedding")) for b in brolls}

    used_paths: set = set()
    used_embeddings: List = []
    out: Dict = {}
    for q in req.queries:
        bid = str(q.get("block_id", len(out)))
        res = broll_search.search(
            q.get("query", ""), brolls, top_k=req.top_k, used=used_paths,
            vertical=q.get("vertical"), prev_embeddings=used_embeddings,
        )
        out[bid] = res
        if res:
            best = res[0]
            used_paths.add(best["path"])
            emb = path_emb.get(best["path"])
            if emb is not None:
                used_embeddings.append(emb)
    return {"results": out}


class TagAssetsRequest(BaseModel):
    folder: Optional[str] = None
    force: bool = False
    enrich: bool = False   # True → gera caption_keywords via LLM nos clips já tagueados


@app.post("/tag_assets")
async def tag_assets(req: TagAssetsRequest):
    """Gera/atualiza as tags semânticas (.tags.json) dos assets de uma pasta.
    Retorna imediatamente — o trabalho roda em background; acompanhe pelo /progress."""
    folder = req.folder or _load_config().get("broll_folder", "")
    if not folder or not os.path.isdir(folder):
        raise HTTPException(400, f"Pasta inválida: {folder}")
    # Enrich usa Ollama LOCAL (não precisa de chave de nuvem). Só exige chave quando
    # for tagging "do zero" (visão/classificação na nuvem) sem reserva local.
    if not req.enrich and not broll_classifier.available() and not llm.chain():
        raise HTTPException(400, "Tagging precisa de ANTHROPIC_API_KEY, GEMINI_API_KEY ou Ollama local.")

    # Já existe um job rodando para esta pasta? Não duplica.
    st = _read_tag_state()
    if st and st.get("step") == "tagging" and _pid_alive(st.get("pid")):
        return {"ok": True, "status": "already_running",
                "current": st.get("current", 0), "total": st.get("total", 0)}

    # Lança em SUBPROCESSO destacado: sobrevive a reinício/crash do servidor e, se for
    # morto no meio (jetsam), o /progress retoma de onde parou (cada clip é salvo na hora).
    _write_tag_state({"pid": 0, "folder": folder, "enrich": bool(req.enrich),
                      "force": bool(req.force), "step": "tagging", "current": 0,
                      "total": 0, "detail": "Iniciando auto-tagging...", "_relaunch_at": 0})
    pid = _spawn_tagger(folder, enrich=req.enrich, force=req.force)
    if not pid:
        _clear_tag_state()
        raise HTTPException(500, "Não consegui iniciar o tagueamento (subprocesso).")
    st2 = _read_tag_state() or {}
    st2["pid"] = pid
    _write_tag_state(st2)
    return {"ok": True, "status": "running", "pid": pid}


@app.get("/index_status")
def index_status(folder: str):
    cache_path = os.path.join(folder, ".vsl_index.json")
    if not os.path.exists(cache_path):
        return {"indexed": 0}
    with open(cache_path) as f:
        data = json.load(f)
    return {"indexed": len(data)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7821, log_level="info")
