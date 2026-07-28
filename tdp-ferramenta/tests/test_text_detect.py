import pytest

from troca_produto.pipeline.text_detect import (
    DEFAULT_THRESHOLD,
    Mention,
    _fuzzy_score,
    find_mentions,
    find_sentence,
    hotwords_prompt,
    is_false_positive,
    iter_windows,
    normalize,
    phonetic_key,
    to_tokens,
    tokens_from_segments,
)


def test_normalize_tira_acento_e_pontuacao():
    assert normalize("Glicovita®, o MELHOR!") == "glicovita o melhor"


def test_phonetic_colapsa_grafias_diferentes_do_mesmo_som():
    assert phonetic_key("glykavit") == phonetic_key("glicovita")


@pytest.mark.parametrize(
    "heard",
    ["sugar bean", "sugarbent", "sugar bind", "shugarbind", "sugarbind"],
)
def test_fuzzy_pega_os_erros_do_asr(heard):
    assert _fuzzy_score(heard, "SugarBind") >= DEFAULT_THRESHOLD


def test_fuzzy_nao_casa_palavra_solta_generica():
    assert _fuzzy_score("sugar", "SugarBind") < DEFAULT_THRESHOLD


def test_fuzzy_nao_casa_palavra_sem_relacao():
    assert _fuzzy_score("wonderful", "SugarBind") < DEFAULT_THRESHOLD


def test_fuzzy_identico_da_cem():
    assert _fuzzy_score("Sugar Bind", "sugarbind") == 100.0


def test_fuzzy_com_vazio_da_zero():
    assert _fuzzy_score("", "SugarBind") == 0.0


def test_iter_windows_gera_1_a_3_palavras(tokens_factory):
    tokens = tokens_factory("um dois tres")
    janelas = [w for _, _, w in iter_windows(tokens, 3)]
    assert "um" in janelas and "um dois" in janelas and "um dois tres" in janelas


def test_find_mentions_acha_nome_quebrado_em_duas_palavras(make_words):
    palavras = make_words("take", "sugar", "bean", "every", "morning")
    achadas = find_mentions(palavras, ["SugarBind"])
    assert [m.text for m in achadas] == ["sugar bean"]
    assert achadas[0].words == 2
    assert achadas[0].start == pytest.approx(0.4)


def test_find_mentions_descarta_blood_sugar(make_words):
    palavras = make_words("your", "blood", "sugar", "levels", "drop")
    assert find_mentions(palavras, ["SugarBind"]) == []


def test_find_mentions_acha_menção_mesmo_perto_de_blood_sugar(make_words):
    palavras = make_words("blood", "sugar", "levels", "with", "sugarbind", "daily")
    achadas = find_mentions(palavras, ["SugarBind"])
    assert [m.text for m in achadas] == ["sugarbind"]


def test_find_mentions_sem_sobreposicao(make_words):
    palavras = make_words("sugarbind", "sugarbind")
    achadas = find_mentions(palavras, ["SugarBind"])
    assert len(achadas) == 2
    assert achadas[0].end <= achadas[1].start


def test_find_mentions_ordenadas_no_tempo(make_words):
    palavras = make_words("intro", "sugarbind", "meio", "meio", "sugar", "bean")
    achadas = find_mentions(palavras, ["SugarBind"])
    assert [round(m.start, 2) for m in achadas] == sorted(round(m.start, 2) for m in achadas)


def test_find_mentions_sem_alvo_retorna_vazio(make_words):
    assert find_mentions(make_words("qualquer", "coisa"), []) == []


def test_find_mentions_usa_aliases(make_words):
    palavras = make_words("peguei", "o", "glykavit", "hoje")
    achadas = find_mentions(palavras, ["Glicovita", "gliko vita"])
    assert achadas and achadas[0].target in ("Glicovita", "gliko vita")


def test_is_false_positive_so_derruba_quando_a_janela_e_a_expressao():
    assert is_false_positive("sugar", "your blood sugar levels")
    assert not is_false_positive("sugarbind", "take sugarbind with blood sugar meals")


def test_to_tokens_aceita_dicts_e_ignora_vazios():
    tokens = to_tokens([{"word": " Take ", "start": 0, "end": 1}, {"word": "  ", "start": 1, "end": 2}])
    assert [t.word for t in tokens] == ["take"]


def test_tokens_from_segments_interpola_tempo():
    tokens = tokens_from_segments([{"text": "um dois", "start": 0.0, "end": 2.0}])
    assert len(tokens) == 2
    assert tokens[1].start == pytest.approx(1.0)


def test_find_sentence_expande_ate_a_pausa(tokens_factory):
    tokens = tokens_factory("eu tomei sugarbind por tres meses", step=0.3)
    tokens.append(type(tokens[0])(word="depois", start=tokens[-1].end + 1.5, end=tokens[-1].end + 1.8))
    mention = Mention(start=tokens[2].start, end=tokens[2].end, text="sugarbind", target="SugarBind", score=100.0)
    start, end, texto = find_sentence(tokens, mention)
    assert texto == "eu tomei sugarbind por tres meses"
    assert start == pytest.approx(tokens[0].start)
    assert end == pytest.approx(tokens[5].end)


def test_hotwords_prompt_lista_os_nomes():
    prompt = hotwords_prompt(["SugarBind", " ", "Sugar Bind"])
    assert "SugarBind" in prompt and "Sugar Bind" in prompt


def test_hotwords_prompt_vazio():
    assert hotwords_prompt([]) == ""
