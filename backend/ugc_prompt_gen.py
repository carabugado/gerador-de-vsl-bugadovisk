"""
MELHORIA 9 — Gerador de prompts UGC para o Higgs.

Transforma a classificação semântica do trecho (broll_classifier) + produto/
vertical/estilo num prompt otimizado que faz o vídeo parecer GRAVADO POR PESSOA
REAL COM CELULAR (nunca cinematográfico). Devolve prompt + negative_prompt +
câmera + luz + duração + aspect_ratio.

Usa Claude → Gemini. Se a API cair, monta um prompt UGC determinístico (com as
mesmas regras de bloco/vertical) — nunca trava a geração.
"""
import os
import json
from typing import Dict, Optional

import llm

UGC_SYSTEM = """Você é um produtor de conteúdo UGC especializado em VSLs de suplementos para o mercado americano. Sua função é criar prompts para geração de vídeo por IA que pareçam GRAVADOS POR PESSOAS REAIS COM CELULAR — nunca cinematográficos, nunca produzidos, nunca "de estúdio".

O objetivo é que o viewer pense "isso é uma pessoa real filmando a própria vida", não "isso é um anúncio".

Você recebe:
- Trecho do script
- Tipo de bloco (hook, problem, mechanism, proof, cta, etc)
- Emoção do momento (frustration, hope, fear, curiosity, confidence, urgency, etc)
- Nível de energia (low, medium, high)
- Tipo visual desejado (emotional, illustrative, authority, result, lifestyle, data_graphic)
- Produto e vertical
- Estilo visual do produto

Retorne APENAS um JSON válido sem markdown:
{
  "prompt": "o prompt completo pro Higgs em inglês",
  "negative_prompt": "o que o Higgs deve evitar",
  "camera": "tipo de enquadramento UGC",
  "lighting": "tipo de iluminação natural",
  "duration_seconds": 3.0,
  "aspect_ratio": "9:16"
}

REGRAS PARA O PROMPT:

0. ESPECIFICIDADE LITERAL — REGRA MAIS IMPORTANTE:
O prompt DEVE descrever EXATAMENTE o objeto/ação mencionado no script, nunca uma aproximação genérica. A IA de vídeo NÃO entende contexto — ela gera literalmente o que você pede. Se pedir "opening something", ela pode gerar abrindo uma lata, uma porta, uma carta. Peça EXATAMENTE o que o script descreve.

ERRO vs CORRETO:
Script: "dificuldade de abrir a tampa de um pote"
ERRADO: "person struggling to open something" / "person opening a can" / "hands trying to open container"
CORRETO: "close-up of elderly woman's hands struggling to twist open a glass jar lid in kitchen, fingers slipping on the metal lid, jar of pickles on kitchen counter, hands shaking with effort"

Script: "can't climb the stairs without getting out of breath"
ERRADO: "person exercising and tired" / "person walking and breathing hard"
CORRETO: "middle-aged man stopping halfway up carpeted home staircase, one hand on wooden railing, leaning forward catching his breath, other hand on his knee, hallway with family photos on wall"

Script: "reading the small print on a medicine bottle"
ERRADO: "person reading something" / "person looking at medication"
CORRETO: "woman in her 60s in bathroom holding small brown pill bottle at arm's length, squinting to read tiny label text, reading glasses pushed up on forehead, fluorescent bathroom light"

Script: "waking up 3 times at night to go to the bathroom"
ERRADO: "person in bedroom at night"
CORRETO: "man in his 50s sitting on edge of bed in dark bedroom, feet on floor about to stand up, alarm clock on nightstand showing 3:17 AM, wife sleeping in background, only moonlight through curtains"

COMO APLICAR: extraia do script o OBJETO EXATO (pote, escada, frasco, cama) e a AÇÃO EXATA (abrir tampa girando, subir degraus, ler texto pequeno, levantar da cama). Descreva o cenário com 3-4 detalhes específicos que ancorem a cena na realidade. Quanto mais específico, menos chance da IA inventar algo errado.
Se o script menciona algo abstrato/metafórico ("sentir o peso do mundo"), traduza para uma AÇÃO FÍSICA concreta que transmita a mesma emoção ("woman sitting at kitchen table with head in hands, staring at pile of unpaid bills").
Esta regra tem prioridade sobre as demais: o DNA UGC e as regras de bloco modulam COMO filmar, mas O QUE aparece tem que ser o objeto/ação literal do script.

1. DNA UGC — TODO prompt deve ter: smartphone footage / phone camera / selfie camera / handheld phone video; leve tremor de mão (nunca steady/stabilized/tripod); iluminação ambiente real (janela, cozinha, banheiro, luz de fora), NUNCA estúdio/softbox/ring light; qualidade de celular bom mas imperfeita, NUNCA 8K/cinema camera/RED/ARRI; cenários domésticos reais (cozinha bagunçada, banheiro normal, sala, quintal, carro), NUNCA estúdio/cenário montado/fundo infinito; pessoas normais (average looking, everyday person, regular body type), NUNCA modelos; roupas casuais (camiseta, moletom, pijama); imperfeições BEM-VINDAS (cabelo bagunçado, cama por fazer, louça na pia, luz desigual).

2. ESTRUTURA (nesta ordem): [FORMATO UGC] + [SUJEITO REAL] + [AÇÃO COTIDIANA] + [CENÁRIO DOMÉSTICO] + [LUZ AMBIENTE] + [EMOÇÃO NATURAL].

3. REGRAS POR BLOCO:
   HOOK: POV de celular na mão ("POV phone footage", "as if filming to show a friend"); pode ser selfie frontal com surpresa/choque. 2-3s.
   PROBLEM/AGITATION: pessoa sozinha vulnerável, "video diary feel"; cenário íntimo (banheiro, quarto, cozinha vazia, carro); expressão natural de cansaço/frustração; detalhes reais (olheira, cabelo descuidado, sem maquiagem). 3-4s.
   MECHANISM/INGREDIENTS: mão segurando celular filmando ingrediente/produto na mesa da cozinha; "close-up phone camera on kitchen counter"; pode filmar tela mostrando artigo/estudo; luz de cozinha amarelada; alguém lendo rótulo com cara de "olha isso". 3-4s.
   PROOF/AUTHORITY: celular filmando outra tela com depoimento/artigo; selfie contando resultado ("video testimonial feel"); screenshot de WhatsApp; se médico, consultório normal/telemedicina, NUNCA laboratório perfeito. 3-4s.
   RESULT/GUARANTEE: selfie animado sorrindo genuíno (não sorriso de propaganda); pessoa no espelho com "não acredito que funcionou"; atividade cotidiana com facilidade; "authentic joy", "candid moment", nunca "perfect smile"/"model pose". 3-4s.
   CTA: normalmente NÃO gerar B-roll; se necessário, mão filmando tela com o site ou unboxing caseiro na cozinha. 2-3s.

4. REGRAS POR VERTICAL:
   WL: mulheres reais 35-65, cozinha/banheiro/quarto, roupas largas, espelho, balança SEM número visível.
   ED: homens reais 45-65, banheiro/quarto/sala, sozinho ou com parceira vestidos/carinhosos, preocupação ou alívio.
   NR: adultos 50+, tentando lembrar algo, procurando chaves, esquecendo panela no fogo.
   PT: homens 50+, acordando à noite, desconforto sentado, alívio ao caminhar.
   VS: adultos apertando olhos pra ler celular, afastando livro, comparando com/sem óculos.

5. NEGATIVE PROMPT — sempre incluir: "cinematic, cinema camera, film look, movie quality, professional lighting, studio lighting, ring light; model, perfect skin, professional makeup, styled hair, fashion; text, watermark, logo, subtitle, UI elements, graphics overlay; deformed hands, extra fingers, distorted face; stock footage, commercial, advertisement, corporate; perfect composition, rule of thirds, golden ratio". Adicionar itens do bloco (problema → "smiling, happy, celebrating, energetic") e da vertical (WL → "scale showing numbers, measuring tape, bikini, before after split screen").

6. CAMERA — termos que IAs entendem: "smartphone footage", "handheld shaky", "selfie front camera", "POV phone", "vertical video 9:16", "slightly out of focus", "overhead phone shot".

7. QUALIDADE — terminar o prompt com: "smartphone quality, natural ambient lighting, authentic UGC feel, unpolished, real life, vertical video". NUNCA usar: "cinematic", "4K", "photorealistic", "film grain", "shallow depth of field", "color grading"."""

# Prompt ENXUTO p/ Llama 3.1 8B (few-shot). Usado quando o provider é Ollama.
UGC_SYSTEM_OLLAMA = (
    "Transforme a descrição de B-roll em prompt pra gerar vídeo por IA. O vídeo DEVE "
    "parecer filmado com celular por pessoa real. Nunca cinematográfico.\n\n"
    "REGRA: copie o OBJETO e AÇÃO exatos da descrição. Adicione detalhes UGC "
    "(smartphone, tremor, casa real, pessoa comum, roupa casual).\n\n"
    'Exemplo 1:\nINPUT: {"broll_scene": "elderly woman hands trembling while trying to '
    'twist open glass pickle jar lid on kitchen counter", "emotion": "frustration", '
    '"vertical": "JT"}\nOUTPUT: {"prompt": "Handheld smartphone close-up, elderly '
    "woman's wrinkled hands struggling to twist open a glass pickle jar on cluttered "
    'kitchen counter, fingers slipping on metal lid, slight camera shake, morning '
    'window light, casual pajama sleeves visible, authentic UGC feel, smartphone '
    'quality", "negative_prompt": "cinematic, studio lighting, professional, model, '
    'perfect skin, steady camera, 4K, film grain, happy, smiling, strong grip", '
    '"camera": "handheld close-up", "duration_seconds": 3, "aspect_ratio": "9:16"}\n\n'
    'Exemplo 2:\nINPUT: {"broll_scene": "man stopping halfway up home staircase holding '
    'railing with pain expression", "emotion": "frustration", "vertical": "JT"}\n'
    'OUTPUT: {"prompt": "Shaky phone footage from top of stairs looking down, man in '
    'his 60s in sweatpants stopped midway up carpeted home staircase, one hand gripping '
    'wooden railing, other hand on knee, grimacing expression, slightly out of breath, '
    'family photos on wall behind him, overhead hallway light, authentic home video '
    'feel", "negative_prompt": "cinematic, gym, exercise, professional athlete, studio, '
    'model, happy, energetic, running, steady camera", "camera": "POV phone overhead", '
    '"duration_seconds": 3, "aspect_ratio": "9:16"}\n\n'
    'Exemplo 3:\nINPUT: {"broll_scene": "woman smiling watching grandkids play at park", '
    '"emotion": "hope", "vertical": "JT"}\nOUTPUT: {"prompt": "Selfie angle smartphone '
    'video, woman in her 60s at suburban park bench smiling genuinely while watching '
    'children playing on playground in background, casual jacket and jeans, hair '
    'slightly messy from wind, natural outdoor daylight, slight camera wobble, '
    'authentic candid moment, phone camera quality", "negative_prompt": "cinematic, '
    'model, perfect makeup, studio, professional photo, posed, steady tripod, 4K, '
    'commercial", "camera": "selfie front camera", "duration_seconds": 3, '
    '"aspect_ratio": "9:16"}\n\nRetorne APENAS o JSON. Sem explicação.'
)

# termos UGC obrigatórios no prompt final (validação)
_UGC_TERMS = ("smartphone", "phone", "handheld", "ugc", "authentic")

# Negative base (sempre presente, mesmo no fallback)
_NEG_BASE = ("cinematic, cinema camera, film look, movie quality, professional lighting, "
             "studio lighting, ring light, model, perfect skin, professional makeup, "
             "styled hair, fashion, text, watermark, logo, subtitle, UI elements, "
             "graphics overlay, deformed hands, extra fingers, distorted face, stock footage, "
             "commercial, advertisement, corporate, perfect composition, rule of thirds, "
             "4K, 8K, photorealistic, film grain, shallow depth of field, color grading")

_NEG_BLOCK = {
    "problem": "smiling, happy, celebrating, energetic",
    "agitation": "smiling, happy, celebrating, energetic",
    "hook": "boring, static, lifeless",
    "cta": "",
}
_NEG_VERTICAL = {
    "WL": "scale showing numbers, measuring tape, bikini, before after split screen",
    "ED": "explicit, nudity, sexual content, anatomy, size comparison",
    "NR": "mri scan, ct scan, brain xray, real medical exam",
    "PT": "explicit anatomy, catheter, graphic pain",
    "VS": "eye surgery, lasik, graphic eye disease",
}

_QUALITY_TAIL = ("smartphone quality, natural ambient lighting, authentic UGC feel, "
                 "unpolished, real life, vertical video")

# Sujeito UGC por vertical (para o fallback determinístico)
_SUBJECT = {
    "WL": "an average looking woman in her 50s, casual clothes",
    "ED": "a regular man in his 50s, casual t-shirt",
    "NR": "an everyday older adult in their 60s",
    "PT": "a regular man in his 50s",
    "VS": "an everyday adult squinting at a phone",
    "JT": "an average older adult, casual clothes",
    "FG": "a regular person at home",
}
_SETTING_EMO = {
    "problem": "alone in a normal bathroom, video diary feel, tired natural expression",
    "agitation": "alone in a messy bedroom, frustrated sigh, no makeup",
    "mechanism": "at a kitchen counter, hand holding phone filming a supplement bottle",
    "ingredients": "at a kitchen counter, reading a supplement label closely",
    "proof": "selfie video testimonial feel, talking to the phone",
    "guarantee": "smiling genuinely in the mirror, candid moment",
    "result": "doing an everyday activity with ease, authentic joy",
    "hook": "POV phone footage, as if showing something to a friend, surprised expression",
    "cta": "hand filming a laptop screen with a website",
    "transition": "casual handheld moment at home",
    "story": "sitting at home telling a personal story to the phone",
}


def available() -> bool:
    return len(llm.chain_for("ugc_prompt")) > 0


def _validate_ugc(out: dict, block: str, vertical: str) -> dict:
    """Garante DNA UGC no prompt e bloqueio do cinematográfico no negative (Llama 8B)."""
    prompt = str(out.get("prompt", "")).strip()
    if prompt and not any(t in prompt.lower() for t in _UGC_TERMS):
        prompt += ", smartphone footage, authentic UGC feel"
    out["prompt"] = prompt
    out["negative_prompt"] = _merge_negative(block, vertical, out.get("negative_prompt", ""))
    out.setdefault("camera", "handheld smartphone")
    out.setdefault("lighting", "natural ambient lighting")
    out.setdefault("aspect_ratio", "9:16")
    try:
        out["duration_seconds"] = float(out.get("duration_seconds", 3.0))
    except (TypeError, ValueError):
        out["duration_seconds"] = 3.0
    return out


def _merge_negative(profile_block: str, vertical: str, extra: str = "") -> str:
    parts = [_NEG_BASE]
    if _NEG_BLOCK.get(profile_block):
        parts.append(_NEG_BLOCK[profile_block])
    if _NEG_VERTICAL.get((vertical or "").upper()):
        parts.append(_NEG_VERTICAL[vertical.upper()])
    if extra:
        parts.append(extra)
    return ", ".join(p for p in parts if p)


def _literal_anchor(excerpt: str) -> str:
    """Reduz a narração a uma âncora literal do que MOSTRAR (sem API)."""
    t = (excerpt or "").strip().strip('"').rstrip(".!?")
    if not t:
        return ""
    # tira muleta de 1ª pessoa pra virar descrição de cena
    for pat in ("i can't ", "i cannot ", "i couldn't ", "i can ", "i ", "you can't ",
                "you cannot ", "you ", "my ", "your ", "we ", "they "):
        if t.lower().startswith(pat):
            t = t[len(pat):]
            break
    return t[:140]


def _fallback(inp: Dict) -> Dict:
    """Prompt UGC determinístico (sem API) — mantém o DNA, as regras e a ÂNCORA LITERAL.

    Sem LLM não dá pra reescrever a cena, então embutimos o trecho do script
    como instrução literal ('depicting exactly ...') pra IA não inventar outra coisa.
    """
    block = (inp.get("block_type") or "transition").lower()
    vertical = (inp.get("vertical") or "").upper()
    emotion = inp.get("emotion") or "natural"
    subject = _SUBJECT.get(vertical, "an everyday person, casual clothes")
    setting = _SETTING_EMO.get(block, "casual moment at home")
    cam = "selfie front camera" if block in ("hook", "proof", "result", "guarantee") else "handheld shaky smartphone footage"
    light = "kitchen ambient light" if block in ("mechanism", "ingredients") else "natural window light"
    dur = 2.5 if block in ("hook", "cta", "transition") else 3.5
    anchor = _literal_anchor(inp.get("script_excerpt", ""))
    literal = f"depicting literally: {anchor}, " if anchor else ""
    prompt = (f"Handheld smartphone video, {subject}, {literal}{setting}, {light}, "
              f"{emotion} natural emotion, slight camera shake, {_QUALITY_TAIL}")
    return {
        "prompt": prompt,
        "negative_prompt": _merge_negative(block, vertical),
        "camera": cam,
        "lighting": light,
        "duration_seconds": dur,
        "aspect_ratio": "9:16",
        "_source": "fallback",
    }


def _parse(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def generate(inp: Dict) -> Dict:
    """inp: {script_excerpt, block_type, emotion, energy_level, visual_type,
    product, vertical, visual_style}. Retorna o dict de prompt UGC (nunca None)."""
    block = (inp.get("block_type") or "transition").lower()
    vertical = inp.get("vertical") or ""

    usable = llm.chain_for("ugc_prompt")
    if usable:
        is_ollama = usable[0] == "ollama"
        try:
            if is_ollama:
                # Llama: input enxuto, com a cena literal do classificador
                system = UGC_SYSTEM_OLLAMA
                user = json.dumps({
                    "broll_scene": inp.get("script_excerpt", "") or inp.get("visual_description", ""),
                    "emotion": inp.get("emotion", ""),
                    "vertical": vertical,
                }, ensure_ascii=False)
            else:
                system = UGC_SYSTEM
                user = json.dumps({
                    "script_excerpt": inp.get("script_excerpt", ""),
                    "block_type": block, "emotion": inp.get("emotion", ""),
                    "energy_level": inp.get("energy_level", "medium"),
                    "visual_type": inp.get("visual_type", ""),
                    "product": inp.get("product", ""), "vertical": vertical,
                    "visual_style": inp.get("visual_style", ""),
                }, ensure_ascii=False)
            raw = llm.complete(system, user, max_tokens=1200, temperature=0.7,
                               force_json=True, backends=usable)
            out = llm.safe_json(raw)
            if isinstance(out, dict) and out.get("prompt"):
                out = _validate_ugc(out, block, vertical)
                out["_source"] = usable[0]
                return out
        except Exception as e:
            print(f"[UGC] gerador falhou ({str(e)[:80]}) — fallback determinístico.")

    return _fallback(inp)
