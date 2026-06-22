"""
Copymerda — Alter ego especialista em prompts UGC curtos para geração de vídeo IA.
Roda no LLM configurado (Ollama local por padrão, ou Anthropic). Ver llm.py.
"""
import json
import llm
from typing import List, Dict

SYSTEM_PROMPT = """Você é o Copymerda.

Sua única missão: criar prompts de vídeo UGC curtos, em inglês, que funcionem
perfeitamente com IA generativa de vídeo.

REGRAS DO COPYMERDA:
- Máximo 15 palavras por prompt. Sem enrolação.
- Estilo UGC: câmera na mão, luz natural, pessoa real, ambiente real
- Nunca sugere texto, legenda ou overlay dentro do vídeo
- Foca na EMOÇÃO do momento, não na ação literal
- Prefere close-ups e reações humanas a cenas genéricas
- Pensa em 7 segundos: o que vai impactar em 7 segundos?

EXEMPLOS DO QUE O COPYMERDA FAZ:
- "woman reading shocking news on phone, close-up reaction, natural light"
- "man stepping on scale, relieved smile, bathroom morning light"
- "person holding cash, counting slowly, focused expression"
- "woman looking mirror, touching face, surprised joy"

O QUE O COPYMERDA NUNCA FAZ:
- Prompts longos e descritivos demais
- Cenas genéricas sem emoção ("person walking in city")
- Descrições cinematográficas complexas (isso é papo de diretor, não de UGC)
"""

def _gen(prompt_text: str, max_tokens: int = 2000, force_json: bool = False,
         backends: list = None) -> str:
    return llm.complete(
        SYSTEM_PROMPT, prompt_text,
        max_tokens=max_tokens, temperature=0.7, force_json=force_json,
        backends=backends,
    ).strip()


def _fallback_prompt(seg: Dict) -> str:
    return seg.get("visual_prompt") or seg.get("visual_query") or seg["text"][:80]


def analyze_and_generate_prompts(segments: List[Dict], context: dict = None) -> List[Dict]:
    if not segments:
        return segments

    from vsl_context import context_brief
    ctx = context_brief(context or {})
    ctx = (ctx + "\n\n") if ctx else ""

    # Local (Ollama) em lotes; Gemini/Anthropic de uma vez só.
    chunk = max(1, llm.LOCAL_CHUNK if llm.is_local() else len(segments))
    prompt_map: Dict[int, str] = {}

    def run_batch(indices, backends=None):
        lines = []
        for gi in indices:
            s = segments[gi]
            lines.append(f'[{gi}] {s.get("arc_position","")} (peak:{s.get("emotional_peak",5)}): "{s["text"]}"')
        keys = ", ".join(f'"{gi}"' for gi in indices)
        prompt = f"""{ctx}Para cada segmento abaixo, crie um prompt UGC de máximo 15 palavras em INGLÊS.

{chr(10).join(lines)}

Retorne UM objeto JSON onde a CHAVE é o índice (entre colchetes) e o valor é o prompt:
{{"{indices[0]}": "woman crying at home, close-up, natural light", ...}}

Use TODAS estas chaves: {{{keys}}}. Sem markdown."""
        raw = _gen(prompt, max_tokens=80 * len(indices) + 400, force_json=True, backends=backends).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        if isinstance(data, dict):
            for k, v in data.items():
                val = v.get("ugc_prompt") if isinstance(v, dict) else v
                if isinstance(val, str) and val.strip():
                    try:
                        prompt_map[int(k)] = val.strip().strip('"')
                    except (TypeError, ValueError):
                        pass

    # Passo 1 — trabalhador (Ollama)
    for start in range(0, len(segments), chunk):
        try:
            run_batch(list(range(start, min(start + chunk, len(segments)))))
        except Exception as e:
            print(f"[Copymerda] lote {start}: {e}")

    # Passo 2 — escalonamento dos faltantes p/ auxiliar/chefe
    missing = [i for i in range(len(segments)) if i not in prompt_map]
    escalate = [b for b in ("gemini", "anthropic") if llm._backend_available(b)]
    if missing and escalate:
        print(f"[Copymerda] {len(missing)} prompts faltando — escalando p/ {escalate}")
        for start in range(0, len(missing), chunk):
            try:
                run_batch(missing[start:start + chunk], backends=escalate)
            except Exception as e:
                print(f"[Copymerda] escalonamento: {e}")

    for i, seg in enumerate(segments):
        seg["ugc_prompt"] = prompt_map.get(i) or _fallback_prompt(seg)

    return segments
