"""
Testes do roteamento híbrido grátis (Ollama/Gemini/Claude) e helpers do llm.py.
Rodar:  python test_hybrid_ai.py
"""
import os
import llm

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


def test_safe_json():
    print("\n[1] safe_json (JSON robusto do Ollama)")
    check("JSON puro", llm.safe_json('{"a": 1}') == {"a": 1})
    check("dentro de markdown", llm.safe_json('```json\n{"a": 2}\n```') == {"a": 2})
    check("com texto ao redor", llm.safe_json('Claro! {"a": 3} pronto.') == {"a": 3})
    check("array", llm.safe_json('lista: [1, 2, 3]') == [1, 2, 3])
    check("lixo → None", llm.safe_json("sem json aqui") is None)
    check("vazio → None", llm.safe_json("") is None)


def test_chain_for():
    print("\n[2] chain_for — roteamento por tarefa")
    orig = llm._backend_available
    llm._provider_override = lambda: {}        # sem override no config
    try:
        llm._backend_available = lambda b: True   # tudo disponível
        check("classifier → Ollama primeiro (alto volume → local/cota)",
              llm.chain_for("classifier")[0] == "ollama" and "gemini" in llm.chain_for("classifier"))
        check("ugc_prompt → Ollama primeiro", llm.chain_for("ugc_prompt")[0] == "ollama")
        check("director → Gemini primeiro (baixo volume; Ollama reserva)",
              llm.chain_for("director")[0] == "gemini" and "ollama" in llm.chain_for("director"))
        check("vision_verify → Gemini primeiro (Ollama reserva)",
              llm.chain_for("vision_verify")[0] == "gemini"
              and "ollama" in llm.chain_for("vision_verify"))
        check("phoenix → Gemini primeiro (Ollama reserva)",
              llm.chain_for("phoenix")[0] == "gemini" and "ollama" in llm.chain_for("phoenix"))
        check("context → Gemini primeiro", llm.chain_for("context")[0] == "gemini")
        check("nenhuma tarefa usa OpenRouter ou Claude",
              all("openrouter" not in b and b != "anthropic"
                  for t in llm._TASK_DEFAULTS for b in llm.chain_for(t)))

        # só Gemini disponível → classifier cai pro Gemini
        llm._backend_available = lambda b: b == "gemini"
        check("sem Ollama, classifier usa Gemini", llm.chain_for("classifier") == ["gemini"])

        # override do config força Claude no phoenix
        llm._provider_override = lambda: {"phoenix": "anthropic"}
        llm._backend_available = lambda b: True
        check("override phoenix→anthropic respeitado", llm.chain_for("phoenix")[0] == "anthropic")
    finally:
        llm._backend_available = orig
        llm._provider_override = llm._provider_override


def test_vision_routing():
    print("\n[3] vision — disponibilidade e cadeia")
    orig_models = llm._ollama_models
    orig_avail = llm._backend_available
    llm._provider_override = lambda: {}
    try:
        # Ollama com modelo de visão baixado
        llm._ollama_models = lambda: ["llama3.1:8b", "llama3.2-vision:11b"]
        check("ollama tem visão", llm._ollama_has_vision() is True)
        check("vision_available(ollama) True", llm.vision_available("ollama") is True)

        # sem o modelo de visão
        llm._ollama_models = lambda: ["llama3.1:8b"]
        check("sem modelo vision → has_vision False", llm._ollama_has_vision() is False)

        had_gem = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key"
        llm._backend_available = lambda b: b in ("ollama", "gemini")
        llm._ollama_models = lambda: ["llama3.1:8b"]      # ollama sem visão
        vc = llm.vision_chain()
        check("vision_chain cai pro Gemini quando ollama sem visão",
              "ollama" not in vc and "gemini" in vc, extra=str(vc))
    finally:
        llm._ollama_models = orig_models
        llm._backend_available = orig_avail
        if had_gem is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = had_gem


def test_classifier_llama_fields():
    print("\n[4] classificador aceita campos curtos do Llama")
    import broll_classifier as bc
    bc.llm._provider_override = lambda: {}
    bc.llm._backend_available = lambda b: b == "ollama"     # força Ollama
    # Llama devolve broll_scene/energy/duration/avoid-string
    bc.llm.complete = lambda *a, **k: (
        '{"block_type":"problem","emotion":"frustration","energy":"low",'
        '"visual_type":"emotional","broll_scene":"close-up of elderly woman hands '
        'trembling trying to twist open a glass pickle jar on kitchen counter",'
        '"avoid":"happy person, celebrating","duration":3}'
    )
    p = bc.classify("cant open the jar", use_cache=False)
    check("mapeia energy→energy_level", p["energy_level"] == "low")
    check("mapeia broll_scene→visual_description", "pickle jar" in p["visual_description"])
    check("avoid string→lista", isinstance(p["avoid"], list) and "happy person" in p["avoid"])
    check("duration→suggested_duration", p["suggested_duration"] == 3.0)
    check("deriva search_terms da cena", len(p["search_terms"]) > 0)


if __name__ == "__main__":
    test_safe_json()
    test_chain_for()
    test_vision_routing()
    test_classifier_llama_fields()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
