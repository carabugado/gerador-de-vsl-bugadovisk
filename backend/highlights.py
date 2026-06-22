"""
Mapeia os melhores momentos de um episódio (estilo Jovem Nerd) direto no Premiere:
transcreve com Whisper (transcribe.py) + escolhe os highlights com o LLM
(llm.py — Ollama-local-first / Gemini reserva). Devolve clips com in/out em
SEGUNDOS, prontos pro Highlights Cutter montar a sequência.
"""
import os
import re
import json
import base64
import tempfile
import subprocess
from typing import List, Dict, Optional, Callable

import llm
from transcribe import transcribe

TYPES = ["humor", "debate", "nerdola", "reacao", "momento", "insight"]

INLINE_LIMIT = 20 * 1024 * 1024   # limite inline do Gemini


def _ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        return float((out.stdout or "").strip() or 0)
    except Exception:
        return 0.0


def render_compact_audio(video_path: str, target_bytes: int = 18 * 1024 * 1024):
    """Extrai o áudio em MP3 mono com bitrate calculado pra caber em ~18MB
    (mesmo num episódio de 1h+). Retorna (caminho_temp, mime, kbps)."""
    dur = _ffprobe_duration(video_path)
    if dur and dur > 0:
        kbps = int((target_bytes * 8) / dur / 1000)
        kbps = max(16, min(64, kbps))      # piso/teto de qualidade pra fala
    else:
        kbps = 48
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-b:a", f"{kbps}k", tmp.name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200)
    return tmp.name, "audio/mpeg", kbps


def _parse_ts(ts) -> Optional[float]:
    """'MM:SS' / 'HH:MM:SS' / '48min12s' / número → segundos (float)."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip()
    if not s:
        return None
    if ":" in s:
        try:
            parts = [float(p.replace(",", ".")) for p in s.split(":")]
        except ValueError:
            return None
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return None
    m = re.search(r"(\d+)\s*min", s)
    sec = re.search(r"(\d+)\s*s", s)
    if m or sec:
        return (int(m.group(1)) * 60 if m else 0) + (int(sec.group(1)) if sec else 0)
    try:
        return float(s)
    except ValueError:
        return None


def _mmss(x: float) -> str:
    x = int(round(x))
    return f"{x // 60:02d}:{x % 60:02d}"


def _fmt_transcript(segments: List[Dict], limit: int = 60000) -> str:
    """Transcrição com marcações de tempo, pro LLM ancorar os timestamps."""
    lines = [f"[{_mmss(s['start'])} -> {_mmss(s['end'])}] {s['text']}" for s in segments]
    return "\n".join(lines)[:limit]


def _build_prompt(target_seconds: int, contexts: List[str], ep_context: str,
                  transcript: Optional[str] = None):
    ctx = ("Priorizar momentos de: " + ", ".join(contexts)) if contexts \
        else "Priorizar os melhores momentos em geral"
    ep = f"- Contexto do episódio: {ep_context}\n" if ep_context else ""

    system = ("Você é um editor de vídeo sênior especializado em highlights de podcasts e "
              "conteúdo nerd/geek brasileiro, estilo Jovem Nerd. Responda SEMPRE apenas com "
              "JSON válido, sem texto antes ou depois. Seja CONCISO: 1-2 frases por campo de "
              "texto, NUNCA repita palavras ou frases.")

    if transcript:
        fonte = "a transcrição (com timestamps)"
        bloco_transc = (f"\nTRANSCRIÇÃO COM TIMESTAMPS (use estes tempos — NÃO invente):\n"
                        f"{transcript}\n--- FIM DA TRANSCRIÇÃO ---\n")
        regra_ts = "Os timestamps DEVEM vir da transcrição acima."
    else:
        fonte = "o ÁUDIO deste episódio (ouça com atenção: tom, timing, ênfase, risadas)"
        bloco_transc = ""
        regra_ts = ("Estime os timestamps OUVINDO o áudio, o mais preciso possível "
                    "(formato HH:MM:SS). Preste atenção aos minutos exatos.")

    # Orçamento de tempo + regras derivadas do alvo (LLM é ruim em respeitar
    # somatório de duração, então damos números explícitos — e o código ainda
    # aplica uma trava determinística depois).
    if target_seconds and target_seconds > 0:
        per_clip_max = min(45, max(20, target_seconds // 5))   # ex: 3min → 36s
        sweet = max(18, target_seconds // 7)                   # duração ideal por clip
        n_lo = max(4, target_seconds // per_clip_max)
        n_hi = max(n_lo + 2, target_seconds // 20)
        objetivo = (f"Corte de ~{_mmss(target_seconds)} no TOTAL. O somatório das durações "
                    f"de TODOS os clips deve ficar entre {int(target_seconds*0.85)}s e "
                    f"{target_seconds}s. NUNCA ultrapasse {target_seconds}s.")
        regras = (
            f"1. ORÇAMENTO: a soma das durações de todos os clips ≤ {target_seconds}s "
            f"(alvo {_mmss(target_seconds)}). É proibido passar disso.\n"
            f"2. Clips CURTOS e punchy: cada um de 15 a {per_clip_max}s (ideal ~{sweet}s). "
            f"Corte a gordura — só o miolo do momento, sem enrolação no começo nem no fim.\n"
            f"3. Use tipicamente {n_lo} a {n_hi} clips. Prefira MAIS clips curtos a poucos longos.\n"
            f"4. Sem overlap maior que 5s entre clips.\n"
            f"5. Cada clip começa e termina em fala completa (não corte no meio de uma frase).\n"
            f"6. score_viral (0-10) = potencial de engajamento. Só entram os momentos REALMENTE fortes.\n"
            f"7. A sequência deve fluir e fazer sentido como um vídeo único, com começo e fim.\n"
            f"8. {regra_ts}"
        )
    else:
        objetivo = "Sem limite fixo — selecione só os melhores momentos, sem exagerar na quantidade."
        regras = (
            "1. Selecione entre 6 e 10 clips, só os REALMENTE fortes.\n"
            "2. Clips curtos e punchy: 15 a 60s cada. Corte a gordura.\n"
            "3. Sem overlap maior que 5s entre clips.\n"
            "4. Cada clip começa e termina em fala completa.\n"
            "5. score_viral (0-10) = potencial de engajamento.\n"
            "6. A sequência deve fluir e fazer sentido como um vídeo único.\n"
            f"7. {regra_ts}"
        )

    user = f"""Analise {fonte} e gere um HIGHLIGHTS MAP para edição.

OBJETIVO:
- {objetivo}
- {ctx}
{ep}
TIPOS DE MOMENTOS (use exatamente estes valores em "tipo"):
- "humor" → piada, trocadilho, timing cômico, erro engraçado
- "debate" → opinião forte, discordância, argumento interessante
- "nerdola" → referência geek, curiosidade, fact interessante
- "reacao" → reação genuína, surpresa, expressão marcante
- "momento" → momento especial, emotivo ou viral
- "insight" → análise inteligente, ponto de vista único
{bloco_transc}
REGRAS:
{regras}

Responda APENAS com JSON neste schema exato:
{{
  "duracao_total_video": "ex: 48min12s",
  "resumo_episodio": "2-3 frases",
  "participantes": ["nomes identificados"],
  "clips": [
    {{
      "id": 1,
      "titulo": "título chamativo (max 60 chars)",
      "tipo": "humor|debate|nerdola|reacao|momento|insight",
      "timestamp_inicio": "MM:SS ou HH:MM:SS",
      "timestamp_fim": "MM:SS ou HH:MM:SS",
      "descricao": "o que acontece (2-3 frases, quem fala)",
      "por_que_destacar": "por que funciona pra highlights",
      "texto_falado": "trecho central da fala (15-30 palavras)",
      "energia": 8,
      "score_viral": 9,
      "transicao_para_proximo": "ex: corte seco, fade, J-cut, L-cut"
    }}
  ],
  "sequencia_sugerida": [ids na ordem de montagem, ex: 3,1,5,2],
  "notas_editor": "ritmo, tom, abertura/encerramento sugeridos"
}}"""
    return system, user


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


RAW_DUMP = os.path.join(tempfile.gettempdir(), "highlights_last_raw.txt")


def _salvage_clips(raw: str) -> Optional[Dict]:
    """Resgata os clips COMPLETOS de uma resposta corrompida (o flash às vezes entra
    em loop de repetição e quebra o JSON). Caminha pelo array "clips" extraindo cada
    objeto {...} balanceado e parando no primeiro incompleto."""
    if not raw:
        return None
    i = raw.find('"clips"')
    if i < 0:
        return None
    j = raw.find('[', i)
    if j < 0:
        return None

    clips: List[Dict] = []
    k, n = j + 1, len(raw)
    while k < n:
        while k < n and raw[k] in ' \t\r\n,':
            k += 1
        if k >= n or raw[k] != '{':
            break                          # fim do array ou lixo
        depth, in_str, esc, end = 0, False, False, None
        start = k
        while k < n:
            ch = raw[k]
            if in_str:
                if esc:        esc = False
                elif ch == '\\': esc = True
                elif ch == '"':  in_str = False
            else:
                if ch == '"':   in_str = True
                elif ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = k + 1
                        break
            k += 1
        if end is None:
            break                          # objeto incompleto → para (degeneração)
        try:
            clips.append(json.loads(raw[start:end]))
        except Exception:
            break                          # objeto corrompido → para
        k = end

    if not clips:
        return None
    out = {"clips": clips}
    for key in ("duracao_total_video", "resumo_episodio"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', raw)
        if m:
            out[key] = m.group(1)
    return out


def _parse_or_dump(raw: str, tag: str):
    """safe_json + resgate: se não vier um dict com 'clips', tenta salvar os clips
    completos do prefixo; e sempre grava a resposta crua + head/tail no log."""
    data = llm.safe_json(raw)
    if isinstance(data, dict) and "clips" in data:
        return data

    # resposta quebrada → grava pra diagnóstico
    try:
        with open(RAW_DUMP, "w", encoding="utf-8") as f:
            f.write(raw or "")
    except Exception:
        pass
    r = raw or ""
    print(f"[Highlights] ⚠️ resposta NÃO-parseável ({tag}, {len(r)} chars) — salva em {RAW_DUMP}\n"
          f"  HEAD: {r[:300].replace(chr(10), ' ')}\n  TAIL: {r[-200:].replace(chr(10), ' ')}", flush=True)

    salv = _salvage_clips(r)
    if salv:
        print(f"[Highlights] 🛟 resgatei {len(salv['clips'])} clips completos da resposta corrompida", flush=True)
        return salv
    return data


def _normalize_map(data: Dict, video_path: str, target_seconds: int, engine: str) -> Dict:
    """Resposta crua do LLM → mapa normalizado (clips com in/out em segundos + ordem)."""
    if not isinstance(data, dict) or "clips" not in data:
        return {"ok": False, "error": "A IA não devolveu um mapa válido."}

    by_id: Dict = {}
    norm: List[Dict] = []
    for i, c in enumerate(data.get("clips") or []):
        ins = _parse_ts(c.get("timestamp_inicio"))
        outs = _parse_ts(c.get("timestamp_fim"))
        if ins is None or outs is None or outs <= ins:
            continue
        cid = c.get("id", i + 1)
        item = {
            "id": cid,
            "titulo": c.get("titulo", ""),
            "tipo": (c.get("tipo") or "").lower(),
            "descricao": c.get("descricao", ""),
            "por_que_destacar": c.get("por_que_destacar", ""),
            "texto_falado": c.get("texto_falado", ""),
            "score_viral": c.get("score_viral", 0),
            "energia": c.get("energia", 0),
            "transicao_para_proximo": c.get("transicao_para_proximo", ""),
            "in": round(ins, 3),
            "out": round(outs, 3),
            "dur": int(round(outs - ins)),
        }
        norm.append(item)
        by_id[cid] = item

    if not norm:
        return {"ok": False, "error": "O mapa veio sem clips com timestamps válidos."}

    # ── Trava de orçamento: o LLM costuma estourar a duração. Se o total passar
    # do alvo (10% de folga), derruba os clips de MENOR score até caber. ──
    dropped = 0
    if target_seconds and target_seconds > 0:
        budget = round(target_seconds * 1.10)
        total = sum(c["dur"] for c in norm)
        if total > budget:
            kept, acc = [], 0
            for c in sorted(norm, key=lambda x: (-_num(x.get("score_viral")), x["dur"])):
                if not kept or acc + c["dur"] <= budget:
                    kept.append(c)
                    acc += c["dur"]
            dropped = len(norm) - len(kept)
            norm = kept
            by_id = {c["id"]: c for c in norm}
            print(f"[Highlights] orçamento: total {total}s > {budget}s — "
                  f"removidos {dropped} clips de menor score (ficou {acc}s, "
                  f"{len(norm)} clips)", flush=True)

    seq = data.get("sequencia_sugerida") or [c["id"] for c in norm]
    ordem = 1
    for sid in seq:
        if sid in by_id and "ordem" not in by_id[sid]:
            by_id[sid]["ordem"] = ordem
            ordem += 1
    for c in norm:                       # clips fora da sequência vão pro fim
        if "ordem" not in c:
            c["ordem"] = ordem
            ordem += 1
    norm.sort(key=lambda x: x["ordem"])

    return {
        "ok": True,
        "engine": engine,
        "dropped_for_budget": dropped,
        "source_filename": os.path.basename(video_path),
        "target_seconds": target_seconds,
        "duracao_total_video": data.get("duracao_total_video", ""),
        "resumo_episodio": data.get("resumo_episodio", ""),
        "participantes": data.get("participantes", []),
        "notas_editor": data.get("notas_editor", ""),
        "sequencia_sugerida": [c["id"] for c in norm],
        "clips": norm,
    }


def _map_via_gemini_audio(video_path, target_seconds, contexts, ep_context, say) -> Dict:
    """Renderiza o áudio comprimido (<20MB) e manda pro Gemini OUVIR — melhor seleção."""
    say("Renderizando áudio comprimido (<20MB)...")
    audio_path = None
    try:
        audio_path, mime, kbps = render_compact_audio(video_path)
        size = os.path.getsize(audio_path)
        if size > INLINE_LIMIT:
            return {"ok": False, "error": f"áudio ficou {size // 1024 // 1024}MB (>20MB)"}
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return {"ok": False, "error": f"ffmpeg falhou ao extrair áudio: {str(e)[:120]}"}
    finally:
        if audio_path:
            try: os.unlink(audio_path)
            except OSError: pass

    print(f"[Highlights] áudio renderizado: {size} bytes ({size/1024/1024:.1f}MB) "
          f"@ {kbps}kbps mono — ENVIANDO pro Gemini ({mime})", flush=True)
    say(f"Gemini ouvindo o episódio (~{size // 1024 // 1024}MB) e mapeando...")
    system, user = _build_prompt(target_seconds, contexts, ep_context, transcript=None)
    try:
        raw = llm.gemini_audio(system, user, b64, mime, max_tokens=8192,
                               temperature=0.5, force_json=True)
    except Exception as e:
        print(f"[Highlights] ❌ Gemini (áudio) erro: {str(e)[:150]}", flush=True)
        return {"ok": False, "error": f"Gemini (áudio): {str(e)[:150]}"}
    print(f"[Highlights] ✓ Gemini respondeu ao áudio ({len(raw or '')} chars)", flush=True)
    return _normalize_map(_parse_or_dump(raw, "gemini_audio"), video_path, target_seconds, "gemini_audio")


def _map_via_transcript(video_path, target_seconds, contexts, ep_context, say) -> Dict:
    """Whisper local → transcrição → LLM (Ollama/Gemini). Funciona offline."""
    say("Transcrevendo com Whisper (pode demorar na 1ª vez)...")
    segs = transcribe(video_path)
    if not segs:
        return {"ok": False, "error": "Não consegui transcrever (sem áudio, ou Whisper falhou)."}
    say("Mapeando os melhores momentos (IA)...")
    system, user = _build_prompt(target_seconds, contexts, ep_context, _fmt_transcript(segs))
    raw = llm.complete(system, user, max_tokens=8192, temperature=0.3, force_json=True)
    if not raw:
        return {"ok": False, "error": "Nenhuma IA disponível — ligue o Ollama local ou "
                                      "configure uma chave Gemini."}
    return _normalize_map(_parse_or_dump(raw, "transcript"), video_path, target_seconds, "transcript")


def map_highlights(video_path: str, target_seconds: int = 180,
                   contexts: Optional[List[str]] = None, ep_context: str = "",
                   engine: str = "auto",
                   progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Mapeia os melhores momentos.

    engine:
      - "auto" (padrão): se houver chave Gemini → ÁUDIO pro Gemini (melhor seleção),
        com fallback automático pra transcrição local se falhar; senão, transcrição.
      - "gemini_audio": força o caminho de áudio.
      - "transcript": força Whisper + LLM local.
    """
    def _say(m):
        print(f"[Highlights] {m}", flush=True)   # vai pro log do servidor
        if progress:
            progress(m)

    if not video_path or not os.path.exists(video_path):
        return {"ok": False, "error": f"Vídeo não encontrado: {video_path}"}

    contexts = contexts or []
    use_audio = engine == "gemini_audio" or (engine == "auto" and llm.gemini_available())
    print(f"[Highlights] engine pedido='{engine}' | gemini_disponivel={llm.gemini_available()} "
          f"| caminho={'ÁUDIO→Gemini' if use_audio else 'transcrição local'}", flush=True)

    if use_audio:
        res = _map_via_gemini_audio(video_path, target_seconds, contexts, ep_context, _say)
        if res.get("ok"):
            print(f"[Highlights] ✅ mapeado via ÁUDIO→Gemini ({len(res.get('clips', []))} clips)", flush=True)
            return res
        if engine == "gemini_audio":        # forçado: não cai pra local
            print(f"[Highlights] ❌ áudio/Gemini falhou (forçado): {res.get('error')}", flush=True)
            return res
        _say(f"Áudio/Gemini falhou ({res.get('error', '')[:60]}) — tentando transcrição local...")

    res = _map_via_transcript(video_path, target_seconds, contexts, ep_context, _say)
    if res.get("ok"):
        print(f"[Highlights] ✅ mapeado via TRANSCRIÇÃO local ({len(res.get('clips', []))} clips)", flush=True)
    return res
