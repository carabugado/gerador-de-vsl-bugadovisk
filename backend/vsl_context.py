"""
Etapa 1 da IA — ENTENDIMENTO da VSL a partir do doc (roteiro/copy em texto).

Extrai um objeto de contexto (expert, nicho, produto, promessa, seções, diretrizes
visuais) que alimenta TODAS as etapas seguintes (diretor, seleção de b-roll, copy).

Usa o topo da hierarquia primeiro (anthropic → gemini → ollama): é UMA chamada por
VSL, barata e a mais importante — vale o "chefe".
"""
import os
import json
import llm

CONTEXT_SYSTEM = (
    "Você é um estrategista de VSL sênior. A partir do ROTEIRO/COPY de uma VSL, "
    "você extrai a estrutura e o contexto de forma fiel — sem inventar o que não "
    "está no texto. Responde só com JSON válido."
)

# Entendimento é tarefa de 'chefe': usa SÓ Anthropic → Gemini (sem o Ollama).
# Se nenhum estiver disponível, pula o contexto (melhor nada do que um entendimento ruim).
_CONTEXT_CHAIN = [b.strip() for b in
                  os.environ.get("VSL_CONTEXT_CHAIN", "anthropic,gemini").split(",")
                  if b.strip()]


def extract_context(doc_text: str) -> dict:
    """Lê o doc da VSL e devolve o objeto de contexto (ou {} se vazio/falhar)."""
    doc_text = (doc_text or "").strip()
    if not doc_text:
        return {}

    usable = llm.chain_for("context")          # Gemini grátis primeiro
    if not usable:
        print("[VSL Context] nenhum provider disponível — pulando entendimento "
              "(configure GEMINI_API_KEY para o melhor resultado grátis).")
        return {}

    prompt = f"""Leia o ROTEIRO da VSL abaixo e extraia um objeto JSON com:

- "expert": {{"name": "", "credentials": "", "persona": ""}}  (quem apresenta)
- "niche": ""                 (nicho/assunto: ex. saúde articular, emagrecimento, finanças)
- "avatar": ""                (para quem é o produto)
- "product": {{"name": "", "what": "", "mechanism": "", "format": ""}}
- "promise": ""               (promessa central)
- "sections": [               (estrutura narrativa, na ordem que aparece no roteiro;
                               QUEBRE em várias seções, uma por parte)
    {{"name": "escolha UMA palavra: lead, story, mechanism, proof, offer, cta ou transition",
      "summary": "resumo curto da parte", "keywords": ["palavra", "palavra"]}}
  ]
- "visual_dos": ["", ""]       (o que faz sentido MOSTRAR nesta VSL)
- "visual_donts": ["", ""]     (o que EVITAR visualmente — incoerências, exageros)

Seja conciso. Use só o que está no roteiro. Não invente nomes/claims.

=== ROTEIRO DA VSL ===
{doc_text[:24000]}
=== FIM ===

Responda APENAS o objeto JSON, sem markdown."""

    try:
        raw = llm.complete(
            CONTEXT_SYSTEM, prompt,
            max_tokens=2500, temperature=0.2, force_json=True,
            backends=usable,
        ).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        ctx = json.loads(raw)
        return ctx if isinstance(ctx, dict) else {}
    except Exception as e:
        print(f"[VSL Context] falha ao extrair contexto: {e}")
        return {}


def context_brief(ctx: dict) -> str:
    """Resumo compacto do contexto pra injetar nos prompts das etapas seguintes."""
    if not ctx:
        return ""
    expert = ctx.get("expert", {}) or {}
    product = ctx.get("product", {}) or {}
    lines = []

    def add(label, value):
        value = (value or "").strip() if isinstance(value, str) else value
        if value:
            lines.append(f"- {label}: {value}")

    add("Expert", " — ".join(x for x in [expert.get("name"), expert.get("persona")] if x))
    add("Produto", " — ".join(x for x in [product.get("name"), product.get("what")] if x))
    add("Mecanismo", product.get("mechanism"))
    add("Nicho", ctx.get("niche"))
    add("Avatar", ctx.get("avatar"))
    add("Promessa", ctx.get("promise"))

    sections = ctx.get("sections") or []
    if sections:
        names = ", ".join(s.get("name", "") for s in sections if isinstance(s, dict))
        add("Seções da VSL (em ordem)", names)

    dos = ctx.get("visual_dos") or []
    donts = ctx.get("visual_donts") or []
    if dos:
        add("Mostrar", "; ".join(dos))
    if donts:
        add("EVITAR visualmente", "; ".join(donts))

    if not lines:
        return ""
    return "CONTEXTO DA VSL (use para coerência):\n" + "\n".join(lines)
