"""
FASE 2 — Busca semântica de B-roll por embeddings visuais (CLIP).

Recebe a DESCRIÇÃO do b-roll ideal (a visual_description literal do classificador,
ou a visual_query do diretor) e acha o clip que MOSTRA aquilo — sem depender de
tags manuais ou nome de arquivo.

Score composto (sobre os embeddings já guardados no índice):
    best_frame * 0.50   (frame individual mais parecido — captura o momento exato)
  + visual_avg * 0.30   (média dos frames — contexto geral do clip)
  + name_sim  * 0.10    (nome do arquivo, se descritivo — bônus)
  + bônus/penalidades (repetição na sessão, histórico, subpasta da vertical, duração)

Limiar: melhor candidato < GEN_THRESHOLD → manda gerar no Higgs (sem asset bom).
Mantém o mesmo formato (ranked, matches) do resto do pipeline.
"""
import os
import re
import hashlib
import tempfile
import numpy as np
from typing import List, Dict, Tuple, Optional

from broll_index import embed_text
from asset_tagger import load_tags

# NOTA: o score absoluto do clip-vit-base-patch32 NÃO separa "relevante" de "vagamente
# parecido" — medido no índice real, match bom (0.29–0.34) e match fraco/errado
# (0.29–0.30) se sobrepõem. Logo o limiar CLIP serve só de piso de ruído; quem decide
# relevância de verdade é o Claude Vision (vision_score abaixo). Manter GEN baixo evita
# mandar gerar um match que era bom.
# Recalibrado pra escala do casamento por TAGS/nome (texto↔texto, #Fase1): match bom
# 0.85–1.0, medíocre ~0.6–0.8, nonsense ~0.5. GEN no vão pra usar match decente
# (cobertura ↑ — "para de economizar") e só gerar quando não há nada.
GEN_THRESHOLD = float(os.environ.get("SEARCH_GEN_THRESHOLD", "0.58"))  # abaixo → gerar IA
OK_THRESHOLD  = float(os.environ.get("SEARCH_OK_THRESHOLD", "0.82"))   # >= → "ok"
# Gate de RELEVÂNCIA por Claude Vision (0-10): só vale quando o rerank de visão rodou.
VISION_GEN = float(os.environ.get("VISION_GEN_SCORE", "5"))   # < isso → nenhum clip serve → gerar IA
VISION_OK  = float(os.environ.get("VISION_OK_SCORE", "7"))    # >= isso → "ok"; entre → "review"
# Piso de duração do trecho pra receber b-roll. 2s (antes 3s): frases curtas também
# entram — o editor pediu "trechos menores e frases". Sub-slots de rajada são isentos.
MIN_BROLL_DURATION = float(os.environ.get("SEARCH_MIN_BROLL_DURATION", "2.0"))

# ── Verificação de visão SELETIVA (#3) ────────────────────────────────────────
# A visão (local 11B) é lenta; verificar TODO segmento estoura o tempo. Critério:
# rodar visão só onde o ERRO custa caro ou o CLIP está inseguro — não onde o score
# está "alto" (o score CLIP não separa bom de ruim, então score sozinho não serve).
SELECTIVE_VISION = os.environ.get("SELECTIVE_VISION", "1") != "0"   # 0 → verifica tudo (antigo)
VISION_RISK_SCORE = float(os.environ.get("VISION_RISK_SCORE", "0.80"))   # melhor candidato abaixo → risco
VISION_RISK_MARGIN = float(os.environ.get("VISION_RISK_MARGIN", "0.03"))  # top1−top2 menor → empate ambíguo
# Tom sensível: clipe errado num momento de dor/medo/hook é o pior erro → sempre verifica.
_RISKY_BLOCKS = {"problem", "agitation", "hook"}
_RISKY_EMO = {"frustration", "fear"}

# ── Viés de ESTILO a partir da memória (#L2) ──────────────────────────────────
# Em cada trecho, se houver um momento parecido em projetos antigos, dá bônus aos
# clipes visualmente parecidos com o que o editor escolheu lá. Memória vazia → nada.
STYLE_ENABLED = os.environ.get("STYLE_MEMORY", "1") != "0"
STYLE_SIM_MIN = float(os.environ.get("STYLE_SIM_MIN", "0.45"))   # sim. de texto mínima p/ confiar no exemplo
STYLE_BONUS   = float(os.environ.get("STYLE_BONUS", "0.10"))     # peso máximo do bônus de estilo


# ── Detecção de conteúdo íntimo/sexual para vertical ED ──────────────────────
# Quando o segmento fala de desejo, corpo, intimidade, etc. → força busca EXCLUSIVA
# na pasta ED+ (pool local). Sem detecção = pool completo com boost +0.22.
_ED_INTIMATE_RE = re.compile(
    r"\b(?:"
    # PT
    r"desej[oa]?|íntim[oa]?|intimidade|prazer|sexual|relação|romance|paixão|apaixon"
    r"|casal|corpo|nu(?:dez)?|seduz|sedução|atração|beij|sensual|excita|ereção|potência"
    r"|libido|mulher|homem|parceiro|parceira|noite|cama|leito|toque|carinho|satisfaç"
    r"|orgasmo|clímax|energia sexual|virilidade|masculin|feminilidade|encant|foder|sexo"
    r"|pênis|pau|tesão|gozar|transar|fazer amor|pelado|nua|seio|peito"
    # EN
    r"|desire|desir|intimat|intimacy|pleasure|sexual|romance|passion|passionat"
    r"|couple|body|nude|naked|attract|seduc|kiss|sensu|excit|erect|potency"
    r"|libido|partner|night|bed|touch|caress|satisf|orgasm|climax|virility"
    r"|sex\b|fuck|hard penis|soft penis|penis|intercourse|foreplay|arousal"
    r"|making love|undress|lingerie|skin|stamina|testosterone"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

def _is_ed_intimate(text: str) -> bool:
    """True se o texto do segmento ou query sugere conteúdo íntimo/sexual."""
    return bool(_ED_INTIMATE_RE.search(text or ""))


def _is_risky(profile: Optional[dict], cands: List[Dict]) -> bool:
    """True se o segmento merece verificação de visão (dano alto ou CLIP inseguro)."""
    if not cands:
        return True                      # sem candidato → deixa a visão/gate decidir
    # 1) tom sensível (dano alto se o clipe contradiz o momento)
    if profile:
        if str(profile.get("block_type", "")).strip().lower() in _RISKY_BLOCKS:
            return True
        if str(profile.get("emotion", "")).strip().lower() in _RISKY_EMO:
            return True
    # 2) match fraco de verdade (zona de "talvez não tenha nada bom")
    top = cands[0].get("score", 0.0)
    if top < VISION_RISK_SCORE:
        return True
    # 3) empate ambíguo entre os dois melhores (a visão desempata)
    if len(cands) >= 2 and (top - cands[1].get("score", 0.0)) < VISION_RISK_MARGIN:
        return True
    return False


# ── Garantia de query em INGLÊS ───────────────────────────────────────────────
# O CLIP (ViT-B/32) é treinado só em inglês: query em PT derruba o match (~0.34→0.29)
# e arruína a relevância (top-3 vira lixo). Como a `visual_description` (do Llama
# local) É a query, e o fallback usa o texto cru da VSL em PT, normalizamos TODA
# query pra inglês conciso e concreto aqui — único ponto antes do embed_text.
_Q_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vsl_query_en_cache")
_PT_DIACRITICS = re.compile(r"[áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ]")
_PT_STOPWORDS = re.compile(
    r"\b(de|da|do|das|dos|que|com|sem|uma?|para|pra|pessoa|mãos?|mulher|homem|"
    r"idosa?|cozinha|dor|não|está|sendo|sua|seu|num|numa|ele|ela|isso|esse)\b",
    re.IGNORECASE)


def _looks_portuguese(text: str) -> bool:
    if _PT_DIACRITICS.search(text):
        return True
    return len(_PT_STOPWORDS.findall(text)) >= 2


def _english_query(text: str) -> str:
    """Devolve o texto em inglês conciso/concreto pra busca CLIP. Se já parece inglês,
    retorna como está. Traduz/afia via LLM (gemini→ollama→claude) com cache em disco.
    Qualquer falha → retorna o original (nunca trava a busca)."""
    text = (text or "").strip()
    if not text or not _looks_portuguese(text):
        return text
    try:
        import llm
        chain = llm.chain_for("context") or llm.chain_for("classifier")
    except Exception:
        chain = []
    if not chain:
        return text
    # Provider na chave do cache: uma tradução RUIM de um modelo fraco não fica
    # congelada quando o usuário troca de modelo (ex.: liga Qualidade Alta). Antes
    # a chave era só o md5(texto) → o valor velho era servido para sempre.
    prov = chain[0].partition("=")[0]
    key = hashlib.md5(f"{text}|{prov}|v2".encode("utf-8")).hexdigest()[:16]
    cache_file = os.path.join(_Q_CACHE_DIR, f"{key}.txt")
    if os.path.exists(cache_file):
        try:
            cached = open(cache_file, encoding="utf-8").read().strip()
            if cached:
                return cached
        except Exception:
            pass
    try:
        out = llm.complete(
            "You rewrite video B-roll scene descriptions as one concise, concrete "
            "English phrase. Output ONLY the phrase.",
            "Rewrite as ONE concise English phrase for searching stock B-roll "
            "(concrete subject + action + setting, max 20 words, no quotes):\n" + text,
            max_tokens=60, temperature=0.2, backends=chain)
        out = (out or "").strip().strip('"').strip("'").strip()
        if out:
            os.makedirs(_Q_CACHE_DIR, exist_ok=True)
            try:
                open(cache_file, "w", encoding="utf-8").write(out)
            except Exception:
                pass
            return out
    except Exception as e:
        print(f"[Search] normalização EN da query falhou: {str(e)[:60]}")
    return text


# Valência emocional → direciona o CLIP pro tom certo (provado: query com valência
# muda o ranking). Injetada como prefixo natural na query do caminho de embedding.
_VALENCE = {
    "frustration": "frustrated, struggling",
    "fear": "anxious, worried",
    "hope": "hopeful, relieved",
    "confidence": "confident, capable",
    "relief": "relieved, calm",
    "excitement": "excited, energetic",
    "empathy": "caring, gentle",
    "urgency": "urgent",
    "curiosity": "curious, intrigued",
    "authority": "professional, clinical",
}


def _with_valence(query: str, profile: Optional[dict]) -> str:
    """Prefixa a query com a valência da emoção do perfil (tom correto no CLIP)."""
    if not profile:
        return query
    val = _VALENCE.get(str(profile.get("emotion", "")).strip().lower())
    if not val or not query:
        return query
    low = query.lower()
    # não duplica se a query já carrega a valência
    if any(w in low for w in val.split(", ")):
        return query
    return f"{val} — {query}"


def _vec(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float32)


# ── Casamento por TAGS (texto↔texto = sinal FORTE — #Fase1) ───────────────────
# A fala (visual_description) casada contra as KEYWORDS de cada clip por texto↔texto
# é muito mais confiável que CLIP texto→imagem (provado no índice real: "médico"
# 0.28 misturado → 0.67 só médicos). Vira o sinal principal do score.
TAG_WEIGHT  = float(os.environ.get("SEARCH_TAG_WEIGHT", "1.0"))   # peso do match texto↔tags
FRAME_BONUS = float(os.environ.get("SEARCH_FRAME_BONUS", "0.15")) # frame CLIP como bônus leve
_GENERIC_TAG = re.compile(
    r"\b(generic|filler|unspecified|unidentified|b-?roll|broll|transition|"
    r"neutral|stock|placeholder|miscellaneous|random|unknown)\b", re.I)


def _tag_doc(tags: Optional[dict], fallback_name: str = "",
             folder_themes: str = "") -> str:
    """Texto de conteúdo do clip p/ o pivô texto↔texto. Prioridade:
    1) legenda LOCAL do BLIP (caption) — descrição densa do que aparece no clip;
    2) keywords enriquecidas (caption_keywords) + keywords/visual_type das tags;
    3) NOME do arquivo limpo (muitos clips têm nome descritivo 'doctor-talking').
    folder_themes: temas da pasta (themes.txt) injetados na frente do doc.
    A legenda entra na FRENTE: é o sinal mais rico (resolve o clip de nome-hash)."""
    cap = ""
    parts = []
    if tags:
        cap = str(tags.get("caption") or "").strip()
        # caption_keywords: geradas pelo LLM a partir do caption (mais ricas que BLIP cru)
        cap_kws = list(tags.get("caption_keywords") or [])
        parts = cap_kws + list(tags.get("keywords") or []) + list(tags.get("visual_type") or [])
    doc = " ".join(str(p) for p in parts if p).strip()
    full = (cap + " " + doc).strip() if cap else doc
    if not full:
        try:
            from matcher import _clean_name
            cn = _clean_name(fallback_name or "")
        except Exception:
            cn = ""
        full = cn if len(cn) >= 4 else ""
    # Temas da pasta (themes.txt) entram SÓ quando o clip não tem descrição própria
    # (sem caption/keywords/nome). Quando há caption/keywords — o sinal rico — o blob
    # de temas só DILUI e embaralha o ranking (queries diferentes davam o mesmo top),
    # então é ignorado. (Verificado no índice ED+: com blob o ranking era ~aleatório.)
    if not full and folder_themes:
        full = folder_themes
    return full


def _is_generic_doc(doc: str) -> bool:
    """Tag pobre/vaga ("generic broll filler") que casaria com tudo → penalizar."""
    hits = len(_GENERIC_TAG.findall(doc))
    uniq = len(set(doc.lower().split()))
    return hits >= 2 or (uniq <= 4 and hits >= 1)


def _embed_chunked(texts: List[str], chunk: int = 256) -> np.ndarray:
    out = []
    for i in range(0, len(texts), chunk):
        out.append(embed_text(texts[i:i + chunk]))
    return np.vstack(out) if out else np.zeros((0, 512), dtype=np.float32)


def attach_tag_embeddings(brolls: List[Dict]) -> None:
    """Pré-embeda as TAGS de cada clip uma vez → `_tag_emb` + `_tag_generic` no dict.
    Idempotente (pula quem já tem). Chamado no início de select()."""
    pending = [b for b in brolls if "_tag_emb" not in b]
    docs = [_tag_doc(b.get("tags") or load_tags(b.get("path", "") or ""),
                     b.get("filename", "") or b.get("clean_name", ""),
                     folder_themes=b.get("_folder_themes", "")) for b in pending]
    idx = [i for i, d in enumerate(docs) if d]
    if not idx:
        for b in pending:
            b.setdefault("_tag_emb", None)
        return
    embs = _embed_chunked([docs[i] for i in idx])
    pos = {i: k for k, i in enumerate(idx)}
    for i, b in enumerate(pending):
        if i in pos:
            b["_tag_emb"] = embs[pos[i]]
            b["_tag_generic"] = _is_generic_doc(docs[i])
        else:
            b["_tag_emb"] = None


# Limiares de SIMILARIDADE com clips já escolhidos (M2 — consciência de sequência)
_SEQ_DUP = 0.90    # quase idêntico → -0.30
_SEQ_HIGH = 0.75   # muito parecido → -0.15
_SEQ_MID = 0.60    # parecido → -0.05
# Limiar de DIVERSIDADE no top-K (M3 — MMR)
DIVERSITY_THRESHOLD = float(os.environ.get("SEARCH_DIVERSITY", "0.75"))


def _diversify(cands: List[Dict], top_k: int, threshold: float) -> List[Dict]:
    """MMR: monta o top-K garantindo que os clips sejam VISUALMENTE diferentes
    entre si (similaridade <= threshold). Completa com o ranking se faltar."""
    if len(cands) <= 1:
        return cands[:top_k]
    selected = [cands[0]]                          # o melhor sempre entra
    for c in cands[1:]:
        if len(selected) >= top_k:
            break
        ce = c.get("_emb")
        if ce is None:
            continue
        too_similar = any(
            s.get("_emb") is not None and float(np.dot(ce, s["_emb"])) > threshold
            for s in selected
        )
        if not too_similar:
            selected.append(c)
    # se não achou K diversos, completa com os próximos (melhor repetir tema do que faltar opção)
    if len(selected) < top_k:
        for c in cands:
            if c not in selected and len(selected) < top_k:
                selected.append(c)
    return selected


def search(query_text: str, brolls: List[Dict], top_k: int = 5,
           used: set = None, vertical: str = None,
           prev_embeddings: List = None, diversify: bool = True,
           diversity_threshold: float = DIVERSITY_THRESHOLD,
           style_emb=None, style_w: float = 0.0, ed_boost: bool = True) -> List[Dict]:
    """Top-K clips mais parecidos com a descrição textual.

    prev_embeddings: embeddings dos b-rolls JÁ escolhidos na VSL — penaliza
                     candidatos visualmente parecidos (não repetir cena).
    diversify: aplica MMR pra o top-K vir visualmente variado.
    """
    used = used or set()
    prev = [_vec(p) for p in (prev_embeddings or [])]
    q = embed_text([_english_query(query_text) or ""])[0]   # EN garantido + normalizado

    results = []
    for b in brolls:
        frame_embs = b.get("frame_embeddings") or [b.get("embedding")]
        best_frame = max(float(np.dot(q, _vec(fe))) for fe in frame_embs if fe is not None)
        avg = b.get("visual_embedding") or b.get("embedding")
        avg_vec = _vec(avg) if avg is not None else None
        visual_sim = float(np.dot(q, avg_vec)) if avg_vec is not None else 0.0
        name_sim = float(np.dot(q, _vec(b["name_embedding"]))) if b.get("name_embedding") else 0.0

        # SINAL PRINCIPAL: texto↔tags (forte). Frame/nome viram bônus leve. Clip sem
        # tags cai no score CLIP-imagem antigo (raro — ~todo o acervo é tagueado).
        tag_emb = b.get("_tag_emb")
        if tag_emb is not None:
            tag_sim = max(0.0, float(np.dot(q, _vec(tag_emb))))
            if b.get("_tag_generic"):
                tag_sim *= 0.4                       # tag vaga não pode dominar
            score = TAG_WEIGHT * tag_sim + FRAME_BONUS * best_frame + 0.05 * name_sim
        else:
            score = best_frame * 0.50 + visual_sim * 0.30 + name_sim * 0.10

        path = b["path"]
        # repetição exata na sessão
        if path in used:
            score -= 0.15
        # histórico de aceitação (das tags, se existirem)
        tags = b.get("tags") or load_tags(path) or {}
        total = tags.get("times_used", 0) or 0
        if total > 3:
            rate = (tags.get("times_accepted", 0) or 0) / total
            if rate > 0.7:
                score += 0.05
            elif rate < 0.3:
                score -= 0.08
        # subpasta da vertical correta — só conta quando a vertical aparece como
        # COMPONENTE do caminho (pasta/nome), não substring solta ("ed" em "reduced.mp4",
        # "pt" em "script.mp4"), que dava +0.05 espúrio a clipes irrelevantes.
        if vertical and re.search(rf'(?:^|[\\/_\- ]){re.escape(vertical)}(?:[\\/_\- ]|$)',
                                  path, re.I):
            score += 0.05
        # Clips da pasta ED+ recebem boost forte SÓ em segmento íntimo (ed_boost) — sem
        # ele perdem para a biblioteca; com ele, num segmento não-íntimo (pills/médico)
        # venceriam o clipe certo. Por isso o boost é gateado pela intimidade do trecho.
        if ed_boost and b.get("_source") == "ed" and str(vertical or "").upper() == "ED":
            score += 0.22
        # duração inadequada p/ b-roll
        dur = b.get("duration", 0) or 0
        if dur > 0 and (dur < 1.5 or dur > 10):
            score -= 0.05
        # M2: similaridade com o que JÁ foi escolhido (usa o pico, não soma)
        if prev and avg_vec is not None:
            msim = max(float(np.dot(avg_vec, p)) for p in prev)
            if msim > _SEQ_DUP:
                score -= 0.30
            elif msim > _SEQ_HIGH:
                score -= 0.15
            elif msim > _SEQ_MID:
                score -= 0.05
        # L2: bônus de estilo — candidato parecido com a escolha passada em momento
        # similar sobe (escalado pela confiança no exemplo passado, style_w).
        if style_emb is not None and avg_vec is not None:
            style_sim = max(0.0, float(np.dot(avg_vec, style_emb)))
            score += STYLE_BONUS * style_w * style_sim

        results.append({
            "path": path,
            "filename": b["filename"],
            "clean_name": b.get("clean_name", b["filename"]),
            "duration": b.get("duration", 0),
            "score": round(score, 4),
            "visual_similarity": round(best_frame, 4),
            "name_match": round(name_sim, 4),
            "source": b.get("_source", "project"),
            "_emb": avg_vec,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    pool = results[:max(top_k * 3, top_k)]
    final = _diversify(pool, top_k, diversity_threshold) if diversify else pool[:top_k]
    for r in final:
        r.pop("_emb", None)                 # campo interno — não vaza no retorno
    return final


def select(segments: List[Dict], queries: List[str], brolls: List[Dict],
           vertical: str = "", rerank_fn=None,
           profiles: Optional[List[Dict]] = None,
           exclude_paths: Optional[set] = None,
           progress_cb=None,
           seed: Optional[int] = None) -> Tuple[List[Dict], List[Dict]]:
    """Produz (ranked, matches) — mesmo formato de matcher.rank_segments + broll_select.

    queries[i] = descrição literal do b-roll ideal p/ o segmento i (do classificador
    ou do diretor). Abaixo do limiar → no_broll (vai pro Higgs com a própria query).
    rerank_fn(seg, query, candidates) -> candidates: filtro opcional (ex.: Claude Vision).
    profiles[i] = perfil do classificador — usado p/ injetar a valência da emoção na
                  query (tom correto no CLIP).
    exclude_paths = caminhos de B-roll JÁ usados na timeline (V2+) — excluídos do pool
                    pra não repetir o mesmo clipe que o editor já colou (#2a).
    """
    from matcher import make_result

    attach_tag_embeddings(brolls)         # #Fase1: pré-embeda as tags (texto↔texto)
    exclude = set(exclude_paths or ())
    used: set = set()
    prev_embeddings: List = []        # M2: embeddings dos b-rolls já escolhidos
    path_emb = {b["path"]: (b.get("visual_embedding") or b.get("embedding"))
                for b in brolls}
    ranked: List[Dict] = []
    matches: List[Dict] = []
    top_k = 5 if rerank_fn else 3

    _is_ed = vertical and str(vertical).upper() == "ED"
    _ed_in_pool = [b for b in brolls if b.get("_source") == "ed"]
    if _is_ed:
        print(f"[ED+] select() pool: {len(brolls)} total, {len(_ed_in_pool)} ED+ clips"
              f", {len([b for b in _ed_in_pool if b.get('duration',0)>0])} com duração")

    for i, seg in enumerate(segments):
        if progress_cb:                       # reporta progresso + matches achados até agora
            try:
                progress_cb(i, len(segments), sum(1 for m in matches if m.get("broll_path")))
            except Exception:
                pass
        duration_needed = seg["end"] - seg["start"]
        # Sub-slots de enumeração ("3 ingredientes") são curtos DE PROPÓSITO (rajada);
        # o piso não se aplica a eles — o ritmo (rhythm) cuida do timing da rajada.
        # Vertical ED: piso baixo (1s) pra frase curta também receber clipe +18 — o
        # editor quer cobertura máxima (a pasta tem clipe de sobra). Demais: piso normal.
        _seg_floor = 1.0 if _is_ed else MIN_BROLL_DURATION
        if duration_needed < _seg_floor and not seg.get("_enum_group"):
            ranked.append({"index": i, "skip": True, "candidates": []})
            matches.append(make_result(seg, None, "skip"))
            continue

        query = (queries[i] if i < len(queries) else "") or seg.get("visual_query") or seg["text"]
        # Glossário: injeta frases visuais concretas dos conceitos da fala (joelho→
        # "person holding their knee"), forçando o match literal e consertando VSL em PT.
        try:
            import broll_glossary
            query = broll_glossary.enrich_query(query, seg.get("text", ""))
        except Exception:
            pass
        profile = profiles[i] if profiles and i < len(profiles) else None
        query = _with_valence(query, profile)

        # L2: busca um momento parecido na memória de estilo (pela narração do trecho).
        style_emb, style_w = None, 0.0
        if STYLE_ENABLED:
            try:
                import style_memory
                ex = style_memory.query(seg.get("text", ""), top_k=1, min_sim=STYLE_SIM_MIN)
                if ex and ex[0].get("visual_emb"):
                    style_emb = _vec(ex[0]["visual_emb"])
                    style_w = float(ex[0].get("similarity", 0.0))
            except Exception:
                style_emb, style_w = None, 0.0

        usable = [b for b in brolls if b.get("duration", 0) >= duration_needed * 0.8
                  and b["path"] not in exclude]

        # Vertical ED: a pasta +18 é o pool PRIMÁRIO em TODO segmento — é um produto
        # adulto, os clipes são on-theme e abundantes. A biblioteca geral dava clipe fora
        # de contexto ("scientist in a laboratory" pra "exclusive for heterosexual men") E
        # batia no compliance (clipe não-+18 não é isento). Então a biblioteca só entra no
        # FALLBACK (abaixo) quando a +18 não casa NADA. Mantém o produto coerente.
        _is_ed = vertical and str(vertical).upper() == "ED"
        _seg_intimate = bool(_is_ed and _is_ed_intimate((seg.get("text", "") + " " + query)))
        _ed_usable = [b for b in usable if b.get("_source") == "ed"] if _is_ed else []
        _ed_pool = _ed_usable
        usable_primary = _ed_pool if _ed_pool else usable

        def _ed_fill():
            """Último recurso (vertical ED): melhor clipe da pasta ED+ em vez de buraco."""
            if not _ed_usable:
                return None
            fc = search(query, _ed_usable, top_k=1, used=used, vertical=vertical,
                        prev_embeddings=prev_embeddings, style_emb=style_emb,
                        style_w=style_w, ed_boost=True)
            return fc[0] if fc else None

        def _use_fill(fc, reason):
            used.add(fc["path"])
            _e = path_emb.get(fc["path"])
            if _e is not None:
                prev_embeddings.append(_e)
            matches.append(make_result(seg, fc, "review", reason))

        cands = search(query, usable_primary, top_k=top_k, used=used, vertical=vertical,
                       prev_embeddings=prev_embeddings, style_emb=style_emb, style_w=style_w,
                       ed_boost=bool(_is_ed))
        # Safety net: ED+ não casou NADA decente → tenta pool completo (biblioteca/gerados).
        # Raro: caption dos +18 dá score alto; só cai aqui em fala muito fora de tema.
        if _ed_pool and (not cands or cands[0].get("score", 0) < GEN_THRESHOLD * 0.5):
            cands_full = search(query, usable, top_k=top_k, used=used, vertical=vertical,
                                prev_embeddings=prev_embeddings, style_emb=style_emb, style_w=style_w,
                                ed_boost=bool(_is_ed))
            if cands_full:
                cands = cands_full
        # Visão SELETIVA: só roda o rerank de visão nos segmentos de risco (tom
        # sensível, match fraco ou empate). SELECTIVE_VISION=0 verifica tudo.
        # NUNCA no vertical ED: clip da pasta +18 não vai pra nuvem (privacidade) e o
        # Claude recusaria conteúdo adulto, zerando o match e a cobertura.
        if (rerank_fn and cands and not _is_ed
                and (not SELECTIVE_VISION or _is_risky(profile, cands))):
            try:
                cands = rerank_fn(seg, query, cands)
            except Exception as e:
                print(f"[Search] rerank seg {i} falhou: {str(e)[:80]}")
        # Variante: ruído leve nos scores antes de escolher o top-1 (seed por segmento)
        if seed is not None and cands:
            rng = np.random.RandomState(seed * 1000 + i)
            noise = rng.uniform(-0.12, 0.12, len(cands))
            cands = [dict(c, score=float(np.clip(c.get("score", 0.0) + float(noise[j]),
                                                  0.0, 1.0)))
                     for j, c in enumerate(cands)]
            cands.sort(key=lambda x: x["score"], reverse=True)
        cands = cands[:3]
        ranked.append({"index": i, "skip": False, "candidates": cands})

        chosen = cands[0] if cands else None

        # GATE DE RELEVÂNCIA: se o Claude Vision avaliou (rerank), ele MANDA — olhou o
        # clip de verdade. Vision baixo no melhor candidato = a lib não tem nada que
        # sirva → gera IA (em vez de inserir um clip errado). Substitui o score CLIP
        # (que não separa relevante de parecido).
        vscore = chosen.get("vision_score") if chosen else None
        if vscore is not None:
            if vscore < VISION_GEN:
                seg["ugc_prompt"] = query
                matches.append(make_result(seg, None, "no_broll",
                                           f"Claude Vision {vscore:.0f}/10 — nenhum clip serve → gerar IA"))
                continue
            used.add(chosen["path"])
            emb = path_emb.get(chosen["path"])
            if emb is not None:
                prev_embeddings.append(emb)
            status = "ok" if vscore >= VISION_OK else "review"
            matches.append(make_result(seg, chosen, status,
                                       f"Claude Vision {vscore:.0f}/10"
                                       + (f" — {chosen.get('vision_note','')}" if chosen.get('vision_note') else "")))
            continue

        # Sem visão: cai no piso de ruído do CLIP (limiar grosseiro).
        best = chosen["score"] if chosen else 0.0
        # Vertical ED: clips locais (ED+) aceitam limiar muito mais baixo — o CLIP
        # puro de clips sem tags fica ~0.25–0.45, abaixo do GEN_THRESHOLD de 0.58.
        # Para ED+ não cair no "gerar IA", aceita qualquer score > 0 quando é o pool
        # primário (não há fallback de nuvem para conteúdo adulto).
        _ed_local_chosen = chosen and chosen.get("_local_only") and _is_ed
        _effective_threshold = (GEN_THRESHOLD * 0.40) if _ed_local_chosen else GEN_THRESHOLD
        if not chosen or best < _effective_threshold:
            # Clip local (ED+) mas score ainda muito baixo: aceita mesmo assim (sem nuvem)
            if chosen and chosen.get("_local_only"):
                pass  # cai no caminho de sucesso abaixo
            else:
                # Pexels fallback: bloqueado só em segmento ÍNTIMO (não manda fala de
                # sexo pro Pexels). Fala não-íntima do vertical ED (pills, médico, efeito
                # colateral) PODE buscar no Pexels — é o que preenche esses momentos.
                if not _seg_intimate:
                    try:
                        import pexels_search
                        if pexels_search.available():
                            px_list = pexels_search.search(query, per_page=3)
                            if px_list:
                                p = px_list[0]
                                local = pexels_search.download_clip(
                                    p["id"], p["download_url"], p["filename"]
                                )
                                if local:
                                    px_cand = {
                                        "path":     local,
                                        "filename": p["filename"],
                                        "duration": p["duration"],
                                        "score":    0.60,
                                        "source":   "pexels",
                                        "visual_similarity": 0.0,
                                        "tag_sim":  0.0,
                                    }
                                    used.add(local)
                                    emb = path_emb.get(local)
                                    if emb is not None:
                                        prev_embeddings.append(emb)
                                    matches.append(make_result(seg, px_cand, "review",
                                                               f"🌐 Pexels: {p['filename']}"))
                                    continue
                    except Exception as _px_err:
                        print(f"[Pexels] fallback falhou: {str(_px_err)[:60]}")
                # Vertical ED: antes de desistir, PREENCHE com a pasta ED+ (não deixa
                # buraco). Só fica vazio se a ED+ não tiver clipe usável p/ este trecho.
                _fill = _ed_fill()
                if _fill:
                    _use_fill(_fill, f"ED+ (preenchido — sem opção melhor, busca {best:.2f})")
                    continue
                # sem asset bom → gerar IA com a descrição literal como prompt
                seg["ugc_prompt"] = query
                matches.append(make_result(seg, None, "no_broll",
                                           f"busca {best:.2f} < {GEN_THRESHOLD:.2f} → gerar IA"))
                continue

        used.add(chosen["path"])
        # M2: guarda o embedding do escolhido pra penalizar cenas parecidas adiante
        emb = path_emb.get(chosen["path"])
        if emb is not None:
            prev_embeddings.append(emb)
        status = "ok" if chosen["score"] >= OK_THRESHOLD else "review"
        matches.append(make_result(seg, chosen, status,
                                   f"busca visual {chosen['score']:.2f} "
                                   f"(frame {chosen['visual_similarity']:.2f})"))

    # REDE DE SEGURANÇA ED: nenhum trecho COM FALA fica sem vídeo se a pasta +18 tem
    # clipe — pega QUALQUER vazio (skip/no_broll/blocked, de qualquer caminho acima) e
    # preenche com o melhor clipe +18 ainda não usado. Só deixa vazio se o trecho é
    # curtíssimo (< 1s, ~pausa) ou a pasta +18 acabou. Atende "só vazio sem opção mesmo".
    if _is_ed:
        _ed_all = [b for b in brolls if b.get("_source") == "ed"]
        for idx in range(min(len(segments), len(matches))):
            seg, m = segments[idx], matches[idx]
            if m.get("broll_path"):
                continue
            dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
            if dur < 1.0:
                continue   # trecho curtíssimo / pausa real — deixa vazio
            pool = [b for b in _ed_all
                    if b.get("duration", 0) >= dur * 0.6 and b["path"] not in used]
            if not pool:
                continue
            q = ((queries[idx] if idx < len(queries) else "") or
                 seg.get("visual_query") or seg.get("text", ""))
            fc = search(q, pool, top_k=1, used=used, vertical=vertical,
                        prev_embeddings=prev_embeddings, ed_boost=True)
            if fc:
                ch = fc[0]
                used.add(ch["path"])
                _e = path_emb.get(ch["path"])
                if _e is not None:
                    prev_embeddings.append(_e)
                matches[idx] = make_result(seg, ch, "review",
                                           "ED+ (cobertura — sem opção melhor)")

    if progress_cb:
        try:
            progress_cb(len(segments), len(segments),
                        sum(1 for m in matches if m.get("broll_path")))
        except Exception:
            pass
    return ranked, matches
