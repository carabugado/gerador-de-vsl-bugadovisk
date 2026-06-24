"""
MELHORIA 1 — Classificador semântico (chefe: Claude → auxiliar: Gemini).

Recebe o TRECHO de texto do script e devolve um perfil semântico ESTRUTURADO
(bloco, emoção, energia, tipo visual, search_terms, avoid, transição, duração)
ANTES de buscar na pasta. Esse perfil alimenta o scoring (broll_score.py).

- Usa a API do Claude (claude-sonnet-4-6, max_tokens=1000) via a camada llm.py,
  com Gemini de reserva. Se nada disponível/ falhar → retorna None e o chamador
  cai no matching CLIP atual (nunca trava o editor).
- Cache por hash do texto (re-rodar a mesma VSL é de graça).
"""
import os
import json
import math
import hashlib
import tempfile
import concurrent.futures
from typing import Optional, List, Dict

import llm

# System prompt EXATO do classificador (não alterar — é contrato com o produto).
CLASSIFIER_SYSTEM = (
    "Você é um diretor de arte de VSLs de suplementos para o mercado americano. "
    "Sua função é analisar um trecho de script e definir EXATAMENTE que tipo de "
    "B-roll visual deve acompanhar esse momento do vídeo.\n\n"
    "Contexto: VSLs de marketing direto para suplementos de saúde. O B-roll "
    "precisa reforçar a emoção e a mensagem do momento, nunca contradizer.\n\n"
    "Retorne APENAS um JSON válido sem markdown, sem explicação, neste formato:\n"
    "{\n"
    '"block_type": "hook|problem|agitation|mechanism|ingredients|proof|guarantee|cta|transition|story",\n'
    '"emotion": "frustration|hope|fear|curiosity|confidence|urgency|relief|empathy|authority|excitement",\n'
    '"energy_level": "low|medium|high",\n'
    '"visual_type": "emotional|illustrative|authority|result|lifestyle|data_graphic",\n'
    '"visual_description": "descrição curta e específica do que o B-roll ideal mostraria",\n'
    '"search_terms": ["termo1", "termo2", "termo3", "termo4", "termo5"],\n'
    '"avoid": ["o que NÃO mostrar neste momento"],\n'
    '"broll_items": ["quando o trecho ENUMERA vários visuais distintos (ex: 3 '
    'ingredientes; partes do corpo: barriga, coxa, cabeça), liste cada um em INGLÊS; '
    'senão []"],\n'
    '"transition": "cut|dissolve|none",\n'
    '"suggested_duration": 3.0\n'
    "}\n\n"
    "Regras:\n"
    "- broll_items: SÓ preencha quando o trecho lista 2+ coisas visuais distintas que "
    "mereçam clipes separados (ingredientes, sintomas, partes do corpo, etapas). Caso "
    "normal = [] (um único B-roll). Cada item: visual concreto e específico em inglês.\n"
    "- search_terms devem ser descritivos e visuais (\"woman looking frustrated in "
    "mirror\"), não palavras do copy\n"
    "- Se o trecho fala de problema/dor, emotion NUNCA pode ser positive (hope, "
    "confidence, excitement, relief)\n"
    "- Se o trecho fala de solução/resultado, emotion NUNCA pode ser negative "
    "(frustration, fear)\n"
    "- avoid deve listar visuais que contradiriam o momento (ex: no bloco de "
    "problema, avoid \"happy person\", \"celebration\")\n"
    "- visual_description deve ser específica o suficiente para alguém encontrar "
    "ou gerar o clip certo\n"
    "- suggested_duration: 2-3s para momentos rápidos (hook, transition), 3-5s "
    "para momentos densos (mechanism, proof)"
)

# Prompt ENXUTO p/ Llama 3.1 8B (few-shot, 1 tarefa, JSON mínimo). Usado quando o
# provider ativo é o Ollama; Claude/Gemini usam o CLASSIFIER_SYSTEM acima.
CLASSIFIER_SYSTEM_OLLAMA = (
    "Você classifica trechos de VSL de suplemento. Leia o trecho e retorne JSON com "
    "o tipo de bloco, emoção e o que o B-roll deve mostrar.\n\n"
    "REGRA CRÍTICA: descreva a cena do B-roll com OBJETOS E AÇÕES EXATAS do texto. "
    "Nunca generalize.\n\n"
    'Exemplo 1:\nINPUT: "You can\'t even open a pickle jar anymore. Your hands shake '
    'and your fingers slip on the lid."\nOUTPUT: {"block_type": "problem", "emotion": '
    '"frustration", "energy": "low", "visual_type": "emotional", "broll_scene": '
    '"close-up of elderly woman hands trembling while trying to twist open a glass '
    'pickle jar lid on kitchen counter, fingers slipping", "avoid": "happy person, '
    'celebrating, strong hands", "duration": 3}\n\n'
    'Exemplo 2:\nINPUT: "Scientists at Johns Hopkins discovered a tiny protein called '
    'MMP-13 that eats away at your cartilage like acid."\nOUTPUT: {"block_type": '
    '"mechanism", "emotion": "curiosity", "energy": "medium", "visual_type": '
    '"illustrative", "broll_scene": "person scrolling medical article on phone screen '
    'at kitchen table, finger swiping, reading glasses nearby, concerned expression", '
    '"avoid": "happy person, celebration, real surgery", "duration": 4}\n\n'
    'Exemplo 3:\nINPUT: "Within just 3 weeks, Martha from Ohio could finally play with '
    'her grandkids at the park again."\nOUTPUT: {"block_type": "proof", "emotion": '
    '"hope", "energy": "high", "visual_type": "result", "broll_scene": "woman in her '
    '60s smiling while watching children play at suburban park, standing without '
    'support, casual clothes, sunny day, smartphone footage feel", "avoid": "sad '
    'person, pain, medical setting", "duration": 3}\n\n'
    'Exemplo 4:\nINPUT: "But here\'s what your doctor won\'t tell you. It\'s not your '
    'age. It\'s not your genetics."\nOUTPUT: {"block_type": "transition", "emotion": '
    '"curiosity", "energy": "medium", "visual_type": "emotional", "broll_scene": '
    '"person sitting up straighter on couch looking at phone with surprised '
    'expression, as if reading something unexpected, living room background", "avoid": '
    '"doctor, hospital, sad crying", "duration": 3}\n\n'
    'Exemplo 5:\nINPUT: "Click the button below right now. Supplies are limited and '
    'this offer won\'t last."\nOUTPUT: {"block_type": "cta", "emotion": "urgency", '
    '"energy": "high", "visual_type": "none", "broll_scene": "NO BROLL - show offer '
    'page on screen", "avoid": "any broll - this is CTA", "duration": 0}\n\n'
    'Exemplo 6 (ENUMERAÇÃO — o trecho LISTA 2+ visuais distintos → preencha '
    '"broll_items" com um clip por item, em inglês concreto):\nINPUT: "This formula '
    'combines three powerful ingredients: turmeric, ginger and black pepper."\n'
    'OUTPUT: {"block_type": "ingredients", "emotion": "confidence", "energy": '
    '"medium", "visual_type": "illustrative", "broll_scene": "close-up of turmeric, '
    'ginger and black pepper on a kitchen counter", "broll_items": ["fresh turmeric '
    'root close-up", "fresh ginger root on cutting board", "black peppercorns in a '
    'wooden spoon"], "avoid": "pills, lab", "duration": 5}\n\n'
    'REGRA broll_items: SÓ inclua quando o trecho ENUMERA 2+ coisas visuais distintas '
    "(ingredientes, sintomas, partes do corpo, etapas) que merecem clipes separados — "
    "liste cada uma como uma cena concreta em inglês. Caso normal: NÃO inclua o campo "
    '(ou use []).\n\n'
    "Retorne APENAS o JSON. Sem explicação, sem markdown."
)

_GENERIC_PHRASES = ("person doing something", "someone in room", "person at home",
                    "generic scene", "general view")

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vsl_classify_cache")

# Coerência de tom (regras do prompt, reforçadas no código)
_NEGATIVE_BLOCKS = {"problem", "agitation"}
_POSITIVE_BLOCKS = {"mechanism", "ingredients", "proof", "guarantee", "cta"}
_POSITIVE_EMO = {"hope", "confidence", "excitement", "relief"}
_NEGATIVE_EMO = {"frustration", "fear"}

_VALID_BLOCKS = {"hook", "problem", "agitation", "mechanism", "ingredients",
                 "proof", "guarantee", "cta", "transition", "story"}
_VALID_VTYPE = {"emotional", "illustrative", "authority", "result",
                "lifestyle", "data_graphic"}

# Mapa arco→bloco para o perfil de FALLBACK (quando a API não está disponível)
_ARC_TO_BLOCK = {
    "hook": "hook", "problem": "problem", "agitation": "agitation",
    "solution": "mechanism", "proof": "proof", "offer": "guarantee",
    "cta": "cta", "transition": "transition",
}


def _cache_path(text: str, sig: str = "") -> str:
    key = hashlib.md5((text.strip() + "|" + sig).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _ctx_blurb(context: Optional[dict]) -> str:
    """Linha(s) de contexto do produto/VSL p/ ancorar a descrição visual."""
    if not context:
        return ""
    prod = (context.get("product") or {}).get("name", "")
    niche = context.get("niche", "")
    avatar = context.get("avatar", "")
    donts = context.get("visual_donts") or []
    lines = []
    if prod or niche:
        lines.append(f"Produto/nicho da VSL: {prod or '?'}" + (f" — {niche}" if niche else ""))
    if avatar:
        lines.append(f"Público-alvo: {avatar}")
    if donts:
        lines.append(f"NUNCA mostrar (regra do produto): {', '.join(str(d) for d in donts[:5])}")
    return "\n".join(lines)


def _build_user(text: str, context: Optional[dict], neighbors: Optional[dict],
                is_ollama: bool) -> str:
    """Monta a mensagem do usuário com contexto do produto + vizinhança narrativa,
    pra o modelo resolver referências ('isso', 'a solução') e não classificar no vácuo."""
    ctx = _ctx_blurb(context)
    nb = neighbors or {}
    prev = (nb.get("prev") or "").strip()
    nxt = (nb.get("next") or "").strip()
    arc = (nb.get("arc") or "").strip()
    section = (nb.get("section") or "").strip()
    moment = arc + ((" / " + section) if section else "") if (arc or section) else ""

    if is_ollama:
        # Compacto: contexto acima, o trecho-alvo por último (mantém o padrão few-shot).
        head = ""
        if ctx:
            head += ctx + "\n"
        if moment:
            head += f"Momento no vídeo: {moment}\n"
        if prev:
            head += f'(fala anterior: "{prev[:90]}")\n'
        return head + text

    parts = []
    if ctx:
        parts.append(ctx)
    if moment:
        parts.append(f"Momento no vídeo: {moment}")
    if prev:
        parts.append(f'Fala anterior: "{prev}"')
    parts.append(f'TRECHO A CLASSIFICAR: "{text}"')
    if nxt:
        parts.append(f'Próxima fala: "{nxt}"')
    parts.append("Classifique APENAS o trecho a classificar. Use o contexto acima para "
                 "entender referências do texto (ex.: 'isso', 'esse problema', 'a solução') "
                 "e ancorar a cena no produto/condição reais — não invente um tema genérico.")
    return "\n".join(parts)


_STYLE_FEWSHOT = os.environ.get("STYLE_FEWSHOT", "1") != "0"


def _with_style_examples(user: str, text: str) -> str:
    """#L3 — anexa ao prompt exemplos REAIS do estilo do editor (de projetos
    aprendidos) pra o modelo escolher como ele escolheria. Memória vazia → no-op."""
    if not _STYLE_FEWSHOT:
        return user
    try:
        import style_memory
        ex = style_memory.few_shot(text, k=2, min_sim=0.5)
    except Exception:
        ex = []
    if not ex:
        return user
    lines = "\n".join(f'- num trecho como "{e["text"][:70]}", o editor usou: {e["scene"]}'
                      for e in ex)
    return (user + "\n\nESTILO DO EDITOR (exemplos reais de projetos passados — siga o "
            "mesmo tipo de escolha visual quando fizer sentido):\n" + lines)


def _parse(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)
    return data if isinstance(data, dict) else None


def _sanitize(p: dict) -> dict:
    """Garante campos e aplica coerência de tom (problema≠positivo, solução≠negativo).
    Aceita também os nomes curtos do prompt Llama (energy, broll_scene, duration)."""
    p = dict(p)
    # mapeia campos do prompt Llama → schema canônico
    if "energy" in p and "energy_level" not in p:
        p["energy_level"] = p.get("energy")
    if "broll_scene" in p and not p.get("visual_description"):
        p["visual_description"] = p.get("broll_scene")
    if "duration" in p and "suggested_duration" not in p:
        p["suggested_duration"] = p.get("duration")
    if isinstance(p.get("avoid"), str):              # Llama manda string; canônico é lista
        p["avoid"] = [a.strip() for a in p["avoid"].split(",") if a.strip()]

    block = str(p.get("block_type", "")).strip().lower()
    if block not in _VALID_BLOCKS:
        block = "transition"
    p["block_type"] = block

    emo = str(p.get("emotion", "")).strip().lower()
    if block in _NEGATIVE_BLOCKS and emo in _POSITIVE_EMO:
        emo = "frustration"
    if block in _POSITIVE_BLOCKS and emo in _NEGATIVE_EMO:
        emo = "confidence"
    p["emotion"] = emo

    vtype = p.get("visual_type", "")
    if isinstance(vtype, list):
        vtype = vtype[0] if vtype else ""
    vtype = str(vtype).strip().lower()
    if vtype not in _VALID_VTYPE:
        vtype = "emotional" if block in _NEGATIVE_BLOCKS else "lifestyle"
    p["visual_type"] = vtype

    energy = str(p.get("energy_level", "")).strip().lower()
    p["energy_level"] = energy if energy in ("low", "medium", "high") else "medium"

    p["search_terms"] = [str(t).strip() for t in (p.get("search_terms") or []) if str(t).strip()]
    if not p["search_terms"] and p.get("visual_description"):
        p["search_terms"] = [w for w in p["visual_description"].replace(",", " ").split()
                             if len(w) > 3][:6]
    p["avoid"] = [str(t).strip() for t in (p.get("avoid") or []) if str(t).strip()]
    # enumeração (#Fase3): lista de visuais distintos; máx 4, sem duplicar
    items, seen = [], set()
    for t in (p.get("broll_items") or []):
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower()); items.append(t)
    p["broll_items"] = items[:4]

    trans = str(p.get("transition", "")).strip().lower()
    p["transition"] = trans if trans in ("cut", "dissolve", "none") else "cut"

    try:
        p["suggested_duration"] = float(p.get("suggested_duration", 3.0))
    except (TypeError, ValueError):
        p["suggested_duration"] = 3.0
    p["suggested_duration"] = max(2.0, min(5.0, p["suggested_duration"]))

    p.setdefault("visual_description", "")
    p.setdefault("_source", "llm")
    return p


def _validate(prof: dict) -> dict:
    """Marca _quality_flag se a cena ficou genérica/curta (gatilho de retry no Ollama)."""
    scene = (prof.get("visual_description") or "").lower()
    if prof.get("block_type") != "cta":               # CTA pode ter cena vazia
        if any(g in scene for g in _GENERIC_PHRASES):
            prof["_quality_flag"] = "generic_scene"
        elif len(scene.split()) < 10:
            prof["_quality_flag"] = "scene_too_short"
    return prof


def _retry_scene(text: str, backends: list) -> str:
    """Retry de correção: pede uma cena específica de >=15 palavras (Ollama)."""
    prompt = (f'O trecho do script diz: "{text}"\n\n'
              "Descreva em PELO MENOS 15 palavras em inglês uma cena ESPECÍFICA pra B-roll. "
              "Mencione: quem (pessoa, idade, gênero), fazendo o quê (ação EXATA), onde "
              "(cômodo, móveis), com qual expressão facial.\n"
              'Exemplo: "close-up of elderly woman\'s trembling hands struggling to twist '
              'open a glass pickle jar on cluttered kitchen counter, fingers slipping on '
              'metal lid, frustrated expression, morning window light"\n\nSua descrição:')
    try:
        out = llm.complete("Descreva uma cena específica de vídeo.", prompt,
                           max_tokens=200, temperature=0.4, backends=backends)
        return (out or "").strip().strip('"').strip("'")
    except Exception:
        return ""


def available() -> bool:
    return len(llm.chain_for("classifier")) > 0


def classify(text: str, context: Optional[dict] = None,
             neighbors: Optional[dict] = None, use_cache: bool = True) -> Optional[dict]:
    """Classifica UM trecho. Roteia por tarefa (Ollama→Gemini→Claude). No Ollama usa
    o prompt few-shot enxuto + validação + 1 retry de correção se a cena vier genérica.

    context: {product, niche, avatar, visual_donts} extraído do doc da VSL — ancora a
             descrição visual no produto/condição reais.
    neighbors: {prev, next, arc, section} — vizinhança narrativa pra resolver referências.
    """
    text = (text or "").strip()
    if not text:
        return None
    usable = llm.chain_for("classifier")
    if not usable:
        return None

    # Assinatura do contexto entra na chave de cache (não reaproveita cena sem contexto).
    sig = ""
    if context or neighbors:
        sig_src = json.dumps({"c": _ctx_blurb(context), "n": neighbors or {}},
                             sort_keys=True, ensure_ascii=False)
        sig = hashlib.md5(sig_src.encode("utf-8")).hexdigest()[:8]

    cache_file = _cache_path(text, sig)
    if use_cache and os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    is_ollama = usable[0] == "ollama"
    system = CLASSIFIER_SYSTEM_OLLAMA if is_ollama else CLASSIFIER_SYSTEM
    user = _build_user(text, context, neighbors, is_ollama)
    user = _with_style_examples(user, text)

    try:
        raw = llm.complete(system, user, max_tokens=1000, temperature=0.3,
                           force_json=True, backends=usable)
        prof = llm.safe_json(raw)
        if not isinstance(prof, dict):
            return None
        prof = _validate(_sanitize(prof))

        # Retry de correção (só faz sentido no Ollama; Claude/Gemini já vêm específicos)
        if is_ollama and prof.get("_quality_flag"):
            better = _retry_scene(text, usable)
            if better and len(better.split()) >= 10:
                prof["visual_description"] = better
                prof.pop("_quality_flag", None)
        prof.pop("_quality_flag", None)

        if use_cache:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(prof, f, ensure_ascii=False)
            except Exception:
                pass
        return prof
    except Exception as e:
        print(f"[Classifier] falhou ({str(e)[:80]}) — fallback p/ matching atual.")
        return None


ENUM_MIN_SUB_DUR = float(os.environ.get("ENUM_MIN_SUB_DUR", "2.0"))


def split_enumerations(segments: List[Dict], profiles: List[Dict]):
    """#Fase3 — divide segmentos que ENUMERAM visuais (profile.broll_items) em
    sub-segmentos fatiados no tempo, cada um com UMA entidade. Conservador: só divide
    com 2+ itens E tempo suficiente (cada sub ≥ ENUM_MIN_SUB_DUR). Mantém o resto igual.
    Retorna (segments, profiles) alinhados 1:1 — pronto pro resto do pipeline."""
    out_segs, out_profs = [], []
    for seg, prof in zip(segments, profiles):
        items = (prof or {}).get("broll_items") or []
        try:
            dur = float(seg["end"]) - float(seg["start"])
        except (KeyError, TypeError, ValueError):
            dur = 0.0
        n = min(len(items), int(dur // ENUM_MIN_SUB_DUR)) if items else 0
        if n >= 2:
            slot = dur / n
            for k, it in enumerate(items[:n]):
                sub = dict(seg)
                sub["start"] = round(float(seg["start"]) + k * slot, 3)
                sub["end"] = round(float(seg["start"]) + (k + 1) * slot, 3)
                sub["text"] = it
                sub["visual_query"] = it
                sub["_enum_group"] = id(seg)          # marca a rajada (ritmo)
                if k > 0:
                    sub["lettering"] = False           # lettering só no 1º sub-slot
                p = dict(prof or {})
                p["visual_description"] = it
                p["broll_items"] = []
                out_segs.append(sub)
                out_profs.append(p)
        else:
            out_segs.append(seg)
            out_profs.append(prof)
    return out_segs, out_profs


LONG_SEG_MIN     = float(os.environ.get("LONG_SEG_MIN", "4.0"))      # só fatia trechos >= isso
LONG_SEG_SLOT    = float(os.environ.get("LONG_SEG_SLOT", "5.0"))     # alvo de seg por sub-slot (densidade normal)
LONG_SEG_MIN_SUB = float(os.environ.get("LONG_SEG_MIN_SUB", "2.0"))  # cada sub-slot >= isso
# Regra do editor: "não pode ter 3 segundos da mesma coisa na tela". No modo INTENSO
# (default do vertical ED) cada sub-slot é forçado a <= MAX_SHOT → o visual troca antes
# de 3s. Cada slot recebe um clipe DISTINTO (dedup M2 da seleção).
MAX_SHOT         = float(os.environ.get("MAX_SHOT_SEC", "3.0"))
_NO_SPLIT_ARCS   = {"cta"}                                            # blocos que não recebem b-roll
_DENSITY_FACTOR  = {"calm": 1.6, "normal": 1.0, "intense": 0.65}     # slot maior = menos cortes


def split_long_segments(segments: List[Dict], profiles, density: str = "normal"):
    """Fatia trechos LONGOS de narração em vários sub-slots de TEMPO — cada um recebe um
    clipe DISTINTO (a dedup de sequência M2 da seleção evita repetir a mesma cena). Ataca
    o teto de "1 clipe por trecho" (a causa real de "seleciona poucos"), INDEPENDENTE de o
    trecho enumerar coisas. Conservador: só trechos >= LONG_SEG_MIN, que NÃO sejam já uma
    rajada de enumeração e que recebam b-roll (CTA não). Marca cada slot com `_enum_group`
    (reusa a plumbing de rajada: isento do piso de 3s na seleção + ritmo sem gap/limite de
    consecutivos entre irmãos). density: "calm" (menos cortes) | "normal" | "intense" (mais).
    Retorna (segments, profiles) alinhados 1:1 — pronto pro resto do pipeline."""
    profiles = profiles if profiles is not None else [None] * len(segments)
    slot_target = LONG_SEG_SLOT * _DENSITY_FACTOR.get(density, 1.0)
    out_segs, out_profs = [], []
    for seg, prof in zip(segments, profiles):
        if seg.get("_enum_group") is not None:          # já é rajada → não re-fatia
            out_segs.append(seg); out_profs.append(prof); continue
        try:
            dur = float(seg["end"]) - float(seg["start"])
        except (KeyError, TypeError, ValueError):
            dur = 0.0
        arc = str(seg.get("arc_position", "")).lower()
        eligible = dur >= LONG_SEG_MIN and arc not in _NO_SPLIT_ARCS
        n = int(round(dur / slot_target)) if eligible else 0
        # Regra "nada > 3s na tela": no modo intenso, garante fatias <= MAX_SHOT (mais cortes)
        if eligible and density == "intense":
            n = max(n, math.ceil(dur / MAX_SHOT))
        n = min(n, int(dur // LONG_SEG_MIN_SUB))        # garante cada slot >= LONG_SEG_MIN_SUB
        if n >= 2:
            slot = dur / n
            gid = id(seg)
            for k in range(n):
                sub = dict(seg)
                sub["start"] = round(float(seg["start"]) + k * slot, 3)
                sub["end"]   = round(float(seg["start"]) + (k + 1) * slot, 3)
                sub["_enum_group"] = gid                 # rajada → ritmo/seleção tratam igual
                if k > 0:
                    sub["lettering"] = False              # lettering só no 1º sub-slot
                out_segs.append(sub)
                out_profs.append(dict(prof) if prof else prof)
        else:
            out_segs.append(seg)
            out_profs.append(prof)
    return out_segs, out_profs


def fallback_profile(seg: dict) -> dict:
    """Perfil derivado da análise do diretor — usado quando a API não está disponível."""
    arc = str(seg.get("arc_position", "transition")).lower()
    block = _ARC_TO_BLOCK.get(arc, "transition")
    peak = int(seg.get("emotional_peak", 5) or 5)
    energy = "high" if peak >= 7 else ("low" if peak <= 3 else "medium")
    negative = block in _NEGATIVE_BLOCKS
    vq = seg.get("visual_query") or seg.get("visual_prompt") or seg.get("text", "")
    terms = [w for w in vq.replace(",", " ").split() if len(w) > 3][:5]
    return {
        "block_type": block,
        "emotion": "frustration" if negative else "confidence",
        "energy_level": energy,
        "visual_type": seg.get("scene_type") if seg.get("scene_type") in _VALID_VTYPE
                       else ("emotional" if negative else "lifestyle"),
        "visual_description": vq,
        "search_terms": terms or [vq[:40]],
        "avoid": ["happy person", "celebration"] if negative else [],
        "transition": "dissolve" if block in ("mechanism", "proof") else "cut",
        "suggested_duration": 3.0,
        "_source": "fallback",
    }


def classify_segments(segments: List[Dict], context: Optional[dict] = None,
                      use_cache: bool = True, max_workers: int = 4) -> List[Dict]:
    """Classifica todos os segmentos (paralelo). Cai no fallback por segmento
    sempre que a API não responder — a lista volta SEMPRE alinhada a `segments`.

    context: contexto do produto/VSL (do doc) — ancora cada cena no tema real."""
    profiles: List[Optional[dict]] = [None] * len(segments)

    def _neigh(i: int) -> dict:
        return {
            "prev": segments[i - 1].get("text", "") if i > 0 else "",
            "next": segments[i + 1].get("text", "") if i + 1 < len(segments) else "",
            "arc": segments[i].get("arc_position", ""),
            "section": segments[i].get("vsl_section", ""),
        }

    if available():
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(classify, seg.get("text", ""), context, _neigh(i), use_cache): i
                    for i, seg in enumerate(segments)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    profiles[i] = fut.result()
                except Exception:
                    profiles[i] = None
    # preenche faltantes com fallback derivado do diretor
    return [profiles[i] or fallback_profile(seg) for i, seg in enumerate(segments)]
