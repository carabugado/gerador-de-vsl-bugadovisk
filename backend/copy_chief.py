"""
MELHORIA 10 — Copy Chief IA (PHOENIX).

Analista de copy de DR que revisa a VSL ANTES da geração de B-rolls: primeira
impressão, estrutura, qualidade, pontos de B-roll, diagnóstico (score 1-10) e um
MAPA DE B-ROLL acionável. A análise volta como TEXTO legível + um JSON do mapa
após o marcador ---BROLL_MAP_JSON--- (parseável pelo plugin).

Usa Claude → Gemini. Sem API → retorna erro claro (feature opcional).
"""
import os
import re
import json
from typing import Dict, List, Optional

import llm

_MARKER = "---BROLL_MAP_JSON---"
_CAUSE_MARKER = "---CAUSE_MAP_JSON---"

PHOENIX_SYSTEM = """Você é o Copy Chief de uma operação de marketing direto que fatura 8 dígitos/mês vendendo suplementos de saúde para o mercado americano via VSLs. Seu nome é PHOENIX. Você tem 15 anos de experiência em direct response, treinou com Clayton Makepeace, Gary Halbert e Gary Bencivenga, e revisou mais de 2.000 VSLs na carreira.

Seu trabalho é analisar scripts de VSL e dar feedback brutal mas construtivo — como um copy chief faria numa revisão real. Você não elogia por educação. Se está bom, diz que está bom e por quê. Se está ruim, diz que está ruim e como consertar.

FRAMEWORK DE ANÁLISE (siga esta ordem):

1. PRIMEIRA IMPRESSÃO (2-3 linhas)
   Sua reação visceral ao ler. Funcionou? Parou no meio? Por quê?

2. ESTRUTURA
   - O hook prende nos primeiros 15 segundos? Se não, por que perderia o viewer?
   - A transição hook → problema é suave ou abrupta?
   - O bloco de problema gera empatia real ou soa genérico?
   - A "nova causa" / mecanismo é crível e diferenciada?
   - As provas sustentam os claims ou são fracas?
   - O CTA cria urgência real ou é template?
   - A VSL tem uma Big Idea clara ou é uma colcha de retalhos?

3. COPY QUALITY
   - Tom: soa como conversa real ou como IA/robótico?
   - Especificidade: tem detalhes concretos ou generalizações vazias?
   - Emoção: manipula corretamente as emoções ou força demais?
   - Ritmo: varia entre frases curtas e longas ou é monótono?
   - Power words: usa palavras que vendem ou é tudo "amazing" e "incredible"?

4. MAPA DE CAUSA REAL (SEÇÃO MAIS IMPORTANTE PARA O PLUGIN)
   Toda VSL de suplemento tem uma estrutura de causa. Identifique CADA elemento com precisão cirúrgica:

   a) PROBLEMA APARENTE: o que o viewer ACHA que é o problema. Em que parágrafo aparece. NÃO generalize — liste os SINTOMAS EXATOS mencionados ("dor no joelho ao subir escada", não "dor nas articulações"; "não consegue abrir a tampa do pote", não "fraqueza nas mãos").
   b) CAUSA REAL / NOVA CAUSA: o verdadeiro culpado que a VSL revela. Em que parágrafo acontece a VIRADA (o "plot twist"). Qual linguagem apresenta ("a verdade é que...", "o que os médicos não te contam...", "cientistas descobriram que...").
   c) MECANISMO DA SOLUÇÃO: como o produto ataca a causa real, o processo passo a passo, os termos científicos/pseudo-científicos usados.
   d) INGREDIENTES-CHAVE: lista exata com nome, dosagem (se mencionada) e o claim específico de cada um.

   Para CADA elemento forneça: o TRECHO EXATO da copy; a ORIENTAÇÃO VISUAL específica (o que o B-roll deve mostrar, literalmente — objeto e ação exatos); e um PROMPT UGC sugerido pro Higgs caso não haja asset local.

   Para a CAUSA REAL, lembre: não dá pra filmar uma proteína/toxina. Mostre a REAÇÃO da pessoa ao descobrir, uma ilustração/gráfico na tela filmado com celular, ou alguém pesquisando no celular — nunca a causa literal.

   Exemplo de formato:
   PROBLEMA APARENTE (Par. 3-7) — Sintomas: "não consegue abrir a tampa do pote de pickles", "joelhos doem ao subir escada", "mãos inchadas de manhã".
   Visual: mostrar EXATAMENTE cada sintoma — "mãos de mulher 55+ lutando pra girar tampa de pote de vidro na bancada"; "homem 60+ parando no meio da escada segurando o corrimão com dor".
   Higgs: "Handheld smartphone close-up, elderly woman's swollen fingers struggling to twist open a glass pickle jar on cluttered kitchen counter, morning window light, slight camera shake, frustrated expression, authentic UGC feel".

5. PONTOS DE B-ROLL
   Para cada seção da VSL, identifique:
   - Onde B-roll é ESSENCIAL (sem visual = momento morto)
   - Onde B-roll é PROIBIDO (momento que precisa do apresentador/texto na tela)
   - Onde B-roll é OPCIONAL (pode ou não ter, depende do ritmo)
   Formate como lista com timestamp aproximado (se SRT) ou número do parágrafo.

6. DIAGNÓSTICO FINAL
   - Score geral: 1-10
   - Top 3 problemas mais graves (em ordem de impacto na conversão)
   - Top 3 pontos fortes (o que não mexer)
   - Recomendação: "Pode rodar como está", "Precisa de ajustes antes de gravar", ou "Reescrever blocos X, Y, Z"

7. MAPA DE B-ROLL SUGERIDO
   Uma tabela com TODOS os momentos onde B-roll deveria entrar:
   | Momento | Tipo de bloco | B-roll sugerido | Emoção | Prioridade |
   Este mapa pode ser usado diretamente pelo plugin para pré-popular as sugestões de B-roll.

REGRAS:
- Feedback em português brasileiro, direto, sem rodeios
- Use linguagem de copy chief: "isso aqui tá fraco", "esse hook não segura ninguém", "essa prova é ouro"
- Quando algo está ruim, dê a solução — não só aponte o problema
- Referencie princípios de copy quando relevante (curiosity gap, open loops, future pacing, etc)
- Se a copy tem problema de compliance (claims médicos diretos, promessas de cura), sinalize com [COMPLIANCE ALERT]
- O mapa de B-roll deve ser prático e acionável — o editor vai usar direto

Após a análise completa em texto, adicione uma linha com '---BROLL_MAP_JSON---' seguida do mapa de B-roll em formato JSON:
[{"paragraph": 3, "block_type": "problem", "broll_description": "...", "emotion": "...", "priority": "high|medium|low", "status": "essential|optional|prohibited"}]

Depois, adicione OUTRA linha com '---CAUSE_MAP_JSON---' seguida do MAPA DE CAUSA REAL em JSON (use os trechos/visões/prompts da seção 4):
{"problema_aparente": {"descricao": "", "paragrafos": "", "sintomas": ["sintoma exato 1", "sintoma exato 2"], "broll_visual": ["descrição literal 1", "descrição literal 2"], "higgs_prompts": ["prompt UGC em inglês 1"]},
 "causa_real": {"descricao": "", "paragrafos": "", "linguagem_virada": "", "broll_visual": ["reação/ilustração/pesquisa"], "higgs_prompts": ["prompt UGC em inglês"]},
 "mecanismo": {"descricao": "", "paragrafos": "", "termos": ["termo científico 1"], "broll_visual": ["..."], "higgs_prompts": ["..."]},
 "ingredientes": [{"nome": "", "dosagem": "", "claim": "", "broll_visual": "", "higgs_prompt": ""}]}"""

def available() -> bool:
    return len(llm.chain_for("phoenix")) > 0


def _extract_map(json_part: str) -> List[Dict]:
    """Parse robusto do JSON do mapa (pode vir com texto/markdown ao redor)."""
    txt = (json_part or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
        txt = txt.strip()
    # tenta o array direto; senão acha o primeiro [...] no texto
    for candidate in (txt, _first_array(txt)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except Exception:
            continue
    return []


def _first_array(s: str) -> Optional[str]:
    start = s.find("[")
    end = s.rfind("]")
    if 0 <= start < end:
        return s[start:end + 1]
    return None


def _first_object(s: str) -> Optional[str]:
    start = s.find("{")
    end = s.rfind("}")
    if 0 <= start < end:
        return s[start:end + 1]
    return None


def _extract_obj(part: str) -> Dict:
    """Parse robusto de um objeto JSON (mapa de causa)."""
    txt = (part or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
        txt = txt.strip()
    for cand in (txt, _first_object(txt), _repair_truncated_obj(txt)):
        if not cand:
            continue
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _repair_truncated_obj(s: str) -> Optional[str]:
    """Recupera um objeto JSON cortado no meio (resposta truncada): corta no último
    elemento completo e fecha colchetes/chaves abertos."""
    start = s.find("{")
    if start < 0:
        return None
    s = s[start:]
    # corta após o último fim de valor plausível e fecha o que estiver aberto
    cut = max(s.rfind('"'), s.rfind("]"), s.rfind("}"))
    if cut < 0:
        return None
    s = s[:cut + 1]
    depth_obj = s.count("{") - s.count("}")
    depth_arr = s.count("[") - s.count("]")
    if depth_arr > 0:
        s += "]" * depth_arr
    if depth_obj > 0:
        s += "}" * depth_obj
    return s


def _section_after(raw: str, marker: str, others: List[str]) -> str:
    """Texto entre `marker` e o próximo marcador (ou fim)."""
    if marker not in raw:
        return ""
    after = raw.split(marker, 1)[1]
    cut = len(after)
    for nm in others:
        idx = after.find(nm)
        if idx != -1:
            cut = min(cut, idx)
    return after[:cut].strip()


def analyze(copy_text: str) -> Dict:
    """Roda o PHOENIX. Retorna {ok, analysis, broll_map, score, source} ou {ok:False,error}."""
    copy_text = (copy_text or "").strip()
    if not copy_text:
        return {"ok": False, "error": "Copy vazia."}
    usable = llm.chain_for("phoenix")          # Gemini grátis primeiro, Claude fallback
    if not usable:
        return {"ok": False, "error": "PHOENIX precisa de GEMINI_API_KEY (ou ANTHROPIC_API_KEY)."}

    try:
        raw = llm.complete(PHOENIX_SYSTEM, copy_text[:40000], max_tokens=14000,
                           temperature=0.6, force_json=False, backends=usable)
    except Exception as e:
        return {"ok": False, "error": f"Falha ao analisar: {str(e)[:160]}"}

    # texto da análise = tudo antes do PRIMEIRO marcador que aparecer
    cut = len(raw)
    for mk in (_MARKER, _CAUSE_MARKER):
        idx = raw.find(mk)
        if idx != -1:
            cut = min(cut, idx)
    analysis_text = raw[:cut]
    broll_map = _extract_map(_section_after(raw, _MARKER, [_CAUSE_MARKER]))
    cause_map = _extract_obj(_section_after(raw, _CAUSE_MARKER, [_MARKER]))

    # tenta extrair o score do diagnóstico (ex.: "Score geral: 7/10" ou "Score: 7")
    score = None
    m = re.search(r'score\s*(?:geral)?\s*[:\-]?\s*(\d{1,2})\s*(?:/\s*10)?', raw, re.I)
    if m:
        try:
            score = max(1, min(10, int(m.group(1))))
        except ValueError:
            pass

    return {
        "ok": True,
        "analysis": analysis_text.strip(),
        "broll_map": broll_map,
        "cause_map": cause_map,
        "score": score,
        "source": usable[0],
    }


# ─── Integração: aplicar o mapa do PHOENIX aos segmentos do processamento ───────

_STOP = set("the a an of to in on at for and or but with from this that your you "
            "is are was were de da do na no para com que e o a os as um uma".split())


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-zA-Zà-úÀ-Ú]{4,}", (text or "").lower())
            if w not in _STOP}


def apply_map(segments: List[Dict], matches: List[Dict], broll_map: List[Dict]) -> int:
    """Casa cada entrada do mapa ao segmento de narração mais parecido (overlap de
    tokens da descrição/bloco) e:
      - anota seg['phoenix'] (bloco/emoção/prioridade) para o painel
      - se status == 'prohibited', BLOQUEIA aquele b-roll (não insere)
    Retorna quantos segmentos foram anotados. É advisory; não substitui o classificador.
    """
    if not broll_map:
        return 0
    seg_tokens = [_tokens(s.get("text", "")) for s in segments]
    used = set()
    annotated = 0

    for entry in broll_map:
        desc = " ".join([str(entry.get("broll_description", "")),
                         str(entry.get("block_type", "")),
                         str(entry.get("emotion", ""))])
        et = _tokens(desc)
        if not et:
            continue
        best_i, best_score = -1, 0
        for i, st in enumerate(seg_tokens):
            if i in used:
                continue
            ov = len(et & st)
            if ov > best_score:
                best_i, best_score = i, ov
        if best_i < 0 or best_score == 0:
            continue
        used.add(best_i)
        segments[best_i]["phoenix"] = {
            "block_type": entry.get("block_type", ""),
            "emotion": entry.get("emotion", ""),
            "priority": entry.get("priority", ""),
            "status": entry.get("status", ""),
            "broll_description": entry.get("broll_description", ""),
        }
        annotated += 1
        if str(entry.get("status", "")).lower() == "prohibited":
            from matcher import make_result
            matches[best_i] = make_result(segments[best_i], None, "blocked",
                                          "PHOENIX: momento proibido p/ b-roll")
    return annotated
