"""
Alter Ego — Diretor VSL.

Um agente especializado que conhece profundamente copywriting de VSL,
psicologia de vendas e direção cinematográfica.

Responsabilidades:
- Analisar cada segmento no contexto do arco narrativo completo
- Criar prompts visuais precisos para o Runway Gen-4
- Sugerir copy de lettering alinhada ao momento emocional
- Identificar o tipo de cena ideal (prova social, dor, transformação, CTA...)
"""

import os
import json
import llm
from typing import List, Dict

SYSTEM_PROMPT = """Você é o Diretor VSL — um alter ego especialista em Video Sales Letters de alta conversão.

Você combina três expertises:

1. COPYWRITER DE RESPOSTA DIRETA
   - Conhece profundamente os gatilhos: dor, esperança, prova, urgência, identidade
   - Identifica o momento emocional de cada frase: problema, agitação, solução, prova, CTA
   - Sabe quando o espectador está no pico de receptividade

2. DIRETOR CINEMATOGRÁFICO
   - Pensa em termos visuais: ângulo, luz, movimento, sujeito, emoção transmitida
   - Sabe que o B-roll deve AMPLIFICAR a emoção da narração, não apenas ilustrá-la
   - Escreve prompts em inglês técnico para IA generativa (Runway Gen-4 Turbo)
   - Usa termos cinematográficos: close-up, shallow depth of field, golden hour, slow motion, etc.

3. ESTRATEGISTA DE VSL
   - Entende o arco completo: Hook → Problema → Agitação → Solução → Prova → Oferta → CTA
   - Posiciona cada cena dentro desse arco
   - Sabe que o lettering na tela deve reforçar o ponto mais importante do segmento

REGRAS INVIOLÁVEIS:
- Prompts de vídeo SEMPRE em inglês, SEMPRE específicos, MÁXIMO 80 palavras
- Nunca sugerir texto, legendas ou lettering DENTRO do vídeo gerado
- Lettering sugerido para tela: máximo 6 palavras, impactante, sem verbosidade
- O prompt visual deve evocar EMOÇÃO, não apenas descrever ação
"""

def _build_director_prompt(indexed_segments, context_text: str = "") -> str:
    """indexed_segments: lista de (índice_global, segmento)."""
    transcript = "\n".join(
        f"[{gi}] {s['start']:.1f}s-{s['end']:.1f}s: {s['text']}"
        for gi, s in indexed_segments
    )
    keys = ", ".join(f'"{gi}"' for gi, _ in indexed_segments)
    ctx = (context_text + "\n\n") if context_text else ""
    return f"""{ctx}Analise esta transcrição de VSL como Diretor VSL.

Retorne UM objeto JSON onde cada CHAVE é o índice do segmento (entre colchetes) e
o valor é um objeto com:
- "arc_position": EXATAMENTE um de: hook, problem, agitation, solution, proof, offer, cta, transition
- "vsl_section": em que parte da VSL este trecho está (use as seções do contexto, se houver)
- "emotional_peak": número inteiro de 1 a 10
- "visual_prompt": prompt cinematográfico em INGLÊS p/ IA de vídeo (max 60 palavras, sem texto na cena, COERENTE com o produto/expert do contexto e respeitando o que EVITAR)
- "scene_type": um de: testimonial, transformation, lifestyle, pain, solution, abstract, product, social_proof
- "lettering": true/false (merece texto na tela?)
- "lettering_text": texto sugerido em inglês (max 6 palavras) — "" se lettering=false
- "lettering_type": stat, cta, benefit, pain ou title — "" se lettering=false

TRANSCRIÇÃO:
{transcript}

Use TODAS estas chaves: {{{keys}}}. Responda APENAS o objeto JSON, sem markdown."""


def _salvage_objects(raw: str) -> list:
    """Extrai todos os objetos JSON completos de uma string (mesmo array truncado)."""
    objs, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objs.append(json.loads(raw[start:i + 1]))
                    except Exception:
                        pass
                    start = None
    return objs


_ANALYSIS_FIELDS = {
    "index", "arc_position", "visual_prompt", "ugc_prompt",
    "emotional_peak", "scene_type", "lettering",
}
_ARCS = {"hook", "problem", "agitation", "solution", "proof", "offer", "cta", "transition"}
_PEAK_WORDS = {"low": 3, "medium": 5, "med": 5, "high": 8, "very high": 9, "muito alto": 9,
               "baixo": 3, "médio": 5, "medio": 5, "alto": 8}


def _to_int_peak(v) -> int:
    try:
        return max(1, min(10, int(float(v))))
    except (TypeError, ValueError):
        return _PEAK_WORDS.get(str(v).strip().lower(), 5)


def _normalize_to_list(data) -> list:
    """Aceita: array de objetos, {wrapper:[...]}, ou {indice: {...}} (mapa por chave)."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        # objeto único de análise
        if _ANALYSIS_FIELDS & set(data.keys()):
            return [data]
        # wrapper {"segments":[...]} / {"objects":[...]}
        for v in data.values():
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
        # mapa por índice {"0": {...}, "1": {...}}
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                v = dict(v)
                v.setdefault("index", k)
                out.append(v)
        return out
    return []


def _parse_analysis(raw: str) -> list:
    """Parse robusto: lida com array, mapa por índice, wrapper e truncamento."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return _normalize_to_list(json.loads(raw))
    except Exception:
        salvaged = _salvage_objects(raw)
        if salvaged:
            print(f"[Diretor VSL] JSON truncado — recuperados {len(salvaged)} objetos.")
        return salvaged


def analyze_full_vsl(segments: List[Dict], context: dict = None,
                     progress_cb=None) -> List[Dict]:
    """
    Analisa a transcrição completa como um VSL diretor.
    `context`: objeto de contexto da VSL (vsl_context.extract_context) — opcional.
    `progress_cb(done, total)`: callback opcional p/ a barra do painel andar.
    Retorna segmentos enriquecidos com análise profunda.
    """
    from vsl_context import context_brief
    context_text = context_brief(context or {})
    try:
        # Cadeia da tarefa (Gemini-first; Ollama de reserva). Só lota se o PRIMÁRIO for
        # local (Ollama lento); Gemini faz tudo de uma vez.
        chain = llm.chain_for("director") or llm.chain()
        local_primary = (not chain) or chain[0].partition("=")[0] == "ollama"
        chunk = max(1, llm.LOCAL_CHUNK if local_primary else len(segments))
        analysis_map: Dict[int, Dict] = {}
        total = len(segments)

        def run_batch(indexed, backends=chain):
            prompt = _build_director_prompt(indexed, context_text)
            max_tokens = (300 * len(indexed) + 1000) if local_primary else 32000
            raw = llm.complete(
                SYSTEM_PROMPT, prompt,
                max_tokens=max_tokens, temperature=0.6, force_json=True,
                backends=backends,
            )
            for obj in _parse_analysis(raw):
                if isinstance(obj, dict) and "index" in obj:
                    try:
                        analysis_map[int(obj["index"])] = obj
                    except (TypeError, ValueError):
                        pass

        # Passo 1 — cadeia da tarefa, em lotes (1 lote só quando não-local)
        for start in range(0, len(segments), chunk):
            sub = [(start + j, s) for j, s in enumerate(segments[start:start + chunk])]
            try:
                run_batch(sub)
            except Exception as e:
                print(f"[Diretor VSL] lote {start} falhou: {e}")
            if progress_cb:
                try:
                    progress_cb(min(start + chunk, total), total)
                except Exception:
                    pass

        # Passo 2 — escalonamento: o que faltou vai pra RESERVA (não repete o primário)
        missing = [i for i in range(len(segments)) if i not in analysis_map]
        primary = chain[0].partition("=")[0] if chain else ""
        escalate = [b for b in ("ollama", "gemini", "anthropic")
                    if b != primary and llm._backend_available(b)]
        if missing and escalate:
            print(f"[Diretor VSL] {len(missing)} segmentos sem análise — escalando p/ {escalate}")
            for start in range(0, len(missing), chunk):
                sub = [(i, segments[i]) for i in missing[start:start + chunk]]
                try:
                    run_batch(sub, backends=escalate)
                except Exception as e:
                    print(f"[Diretor VSL] escalonamento falhou: {e}")

        if not analysis_map:
            raise ValueError("Diretor VSL não retornou análise utilizável.")

        enriched = []
        for i, seg in enumerate(segments):
            s = dict(seg)
            a = analysis_map.get(i, {})
            vp = (a.get("visual_prompt") or "").strip() or seg["text"]
            arc = str(a.get("arc_position", "transition")).strip().lower()
            if arc not in _ARCS:
                arc = "transition"
            s["visual_prompt"]   = vp
            s["arc_position"]    = arc
            s["emotional_peak"]  = _to_int_peak(a.get("emotional_peak", 5))
            s["scene_type"]      = a.get("scene_type", "lifestyle")
            s["lettering"]       = bool(a.get("lettering", False))
            s["lettering_text"]  = a.get("lettering_text", "") or ""
            s["lettering_type"]  = a.get("lettering_type", "") or ""
            s["director_note"]   = a.get("director_note", "") or ""
            s["vsl_section"]     = a.get("vsl_section", "") or ""
            s["visual_query"]    = vp  # usado pelo CLIP matcher
            enriched.append(s)

        return enriched

    except Exception as e:
        print(f"[Diretor VSL] Erro na análise: {e}")
        for seg in segments:
            seg.setdefault("visual_prompt", seg["text"])
            seg.setdefault("visual_query",  seg["text"])
            seg.setdefault("arc_position",  "transition")
            seg.setdefault("emotional_peak", 5)
            seg.setdefault("scene_type",    "lifestyle")
            seg.setdefault("lettering",     False)
            seg.setdefault("lettering_text", "")
            seg.setdefault("lettering_type", "")
            seg.setdefault("director_note", "")
        return segments

