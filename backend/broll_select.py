"""
Seleção de B-roll pela IA (não só similaridade).

O CLIP (matcher.rank_segments) reduz a pasta inteira aos TOP candidatos por
segmento. Aqui o LLM LÊ a narração + direção visual e ESCOLHE qual candidato
ilustra melhor — ou decide que NENHUM serve (-1). O que não tem b-roll bom fica
marcado como "no_broll" (não insere lixo; pode ir pro Higgs/Storyblocks depois).
"""
import os
import json
from typing import List, Dict

import llm
from matcher import make_result, OK_THRESHOLD

# Backend da SELEÇÃO. Default segue a cadeia normal (Ollama trabalhador).
# Para máxima qualidade, defina SELECT_CHAIN="anthropic,gemini" (chefe escolhe).
_SELECT_CHAIN = [b.strip() for b in os.environ.get("SELECT_CHAIN", "").split(",") if b.strip()] or None

SELECT_SYSTEM = (
    "Você é um editor de VSL (vídeo de vendas) escolhendo o B-roll que melhor ILUSTRA "
    "cada trecho da narração. VSL tem TOM e CONTEXTO fortes — o b-roll precisa casar "
    "com a EMOÇÃO e o ASSUNTO do momento, não só com palavras soltas.\n"
    "REGRAS DURAS (decida pelo SENTIDO do TEXTO da narração, não pelo rótulo de arco):\n"
    "- DOR/PROBLEMA/doença/perda/medo → mostre struggle/preocupação/tensão. NUNCA "
    "pessoas sorrindo, festa, lifestyle alegre ou clichê feliz.\n"
    "- Falar de alguém que ADOECEU, foi diagnosticado, sofreu ou MORREU NUNCA é momento "
    "feliz — nada de gente sorrindo aí.\n"
    "- PESSOA NOMEADA (celebridade/especialista) sem clipe real DELA → -1. NÃO troque por "
    "um rosto/pessoa qualquer.\n"
    "- ASSUNTO ERRADO = -1: candidato de outro nicho/tema (barriga/emagrecimento, skincare, "
    "dinheiro numa VSL de saúde articular, etc.) → não escolha.\n"
    "- Respeite o que EVITAR do contexto. Cena feliz SÓ se o TEXTO for claramente positivo "
    "(cura, melhora, conquista) E do mesmo assunto.\n"
    "- Na dúvida, -1. É MELHOR não ter b-roll do que um fora de tom/assunto.\n"
    "Responda só JSON."
)

CHUNK = 6          # segmentos por chamada (cada um com sua lista de candidatos)
MAX_CANDS = 6      # candidatos mostrados ao LLM por segmento


def _choose(segments: List[Dict], ranked: List[Dict], context_text: str = "") -> Dict[int, int]:
    """Retorna {índice_segmento: posição_candidato_escolhido (ou -1)}."""
    choices: Dict[int, int] = {}
    work = [r for r in ranked if not r.get("skip") and r.get("candidates")]
    ctx = (context_text + "\n\n") if context_text else ""

    for start in range(0, len(work), CHUNK):
        block = work[start:start + CHUNK]
        parts, keys = [], []
        for r in block:
            i = r["index"]
            keys.append(str(i))
            seg = segments[i]
            vq = seg.get("visual_query", "")
            arc = seg.get("arc_position", "")
            peak = seg.get("emotional_peak", 5)
            scene = seg.get("scene_type", "")
            section = seg.get("vsl_section", "")
            tom = f"{arc}" + (f"/{section}" if section else "") + f" · intensidade {peak}/10 · cena ideal: {scene}"
            cand_lines = "\n".join(
                f"    {pos}: {c['clean_name']}"
                for pos, c in enumerate(r["candidates"][:MAX_CANDS])
            )
            parts.append(
                f'[{i}] Narração: "{seg["text"]}"\n'
                f'    Tom/contexto: {tom}\n'
                f'    Direção visual: {vq}\n'
                f'    Candidatos:\n{cand_lines}'
            )
        prompt = (
            ctx
            + "Para cada segmento, escolha o índice do candidato que MELHOR ilustra a "
            "narração (coerente com o produto/expert e respeitando o que EVITAR), "
            "ou -1 se nenhum serve.\n\n"
            + "\n\n".join(parts)
            + f"\n\nResponda UM objeto JSON {{índice_segmento: escolha}} com TODAS as "
              f"chaves: {{{', '.join(keys)}}}. Ex: {{\"{keys[0]}\": 0}}."
        )
        try:
            raw = llm.complete(SELECT_SYSTEM, prompt, max_tokens=60 * len(block) + 300,
                               temperature=0.2, force_json=True, backends=_SELECT_CHAIN).strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    val = v.get("choice") if isinstance(v, dict) else v
                    try:
                        choices[int(k)] = int(val)
                    except (TypeError, ValueError):
                        pass
        except Exception as e:
            print(f"[B-roll Select] lote {start}: {e}")

    return choices


def select(segments: List[Dict], ranked: List[Dict], context: dict = None) -> List[Dict]:
    from vsl_context import context_brief
    choices = _choose(segments, ranked, context_brief(context or {}))
    used: set = set()
    results: List[Dict] = []

    for r in ranked:
        i = r["index"]
        seg = segments[i]
        cands = r.get("candidates", [])

        if r.get("skip"):
            results.append(make_result(seg, None, "skip"))
            continue
        if not cands:
            results.append(make_result(seg, None, "no_broll", "sem candidatos"))
            continue

        pos = choices.get(i, 0)          # default: melhor do CLIP se o LLM não respondeu

        # LLM disse "nenhum serve"
        if pos is not None and pos < 0:
            results.append(make_result(seg, None, "no_broll", "IA: nenhum candidato ilustra bem"))
            continue

        chosen = cands[pos] if (isinstance(pos, int) and 0 <= pos < len(cands)) else cands[0]

        # evita reutilizar o mesmo b-roll: cai pro próximo candidato livre
        if chosen["path"] in used:
            chosen = next((c for c in cands if c["path"] not in used), None)
            if chosen is None:
                results.append(make_result(seg, None, "no_broll", "candidatos já usados"))
                continue

        used.add(chosen["path"])
        status = "ok" if chosen["score"] >= OK_THRESHOLD else "review"
        results.append(make_result(seg, chosen, status, "escolhido pela IA"))

    return results
