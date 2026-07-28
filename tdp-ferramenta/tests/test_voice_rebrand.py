import pytest

from troca_produto.pipeline.text_detect import Mention
from troca_produto.voice.diarize import SpeakerTag
from troca_produto.voice.minimax import STOCK_FEMALE, STOCK_MALE
from troca_produto.voice.rebrand import (
    MODE_SENTENCE,
    MODE_WORD,
    audio_tracks,
    build_plan,
    collect_reference_spans,
    pick_voice,
    plan_mode,
    replace_mention,
    tag_for,
)


def test_narrador_troca_so_a_palavra():
    assert plan_mode("narrator") == MODE_WORD


def test_depoimento_regenera_a_frase():
    assert plan_mode("speaker") == MODE_SENTENCE


def test_replace_mention_preserva_o_resto_da_frase():
    frase = "eu tomei sugar bean por tres meses"
    assert replace_mention(frase, "sugar bean", "GlicoVita") == "eu tomei GlicoVita por tres meses"


def test_replace_mention_quando_nao_acha_devolve_a_frase():
    assert replace_mention("nada aqui", "sugarbind", "GlicoVita") == "nada aqui"


def test_clone_ganha_de_qualquer_fallback():
    escolha = pick_voice("speaker", "female", cloned_voice="voz_clonada")
    assert escolha.voice_id == "voz_clonada" and not escolha.is_fallback


def test_narrador_jamais_recebe_voz_feminina():
    escolha = pick_voice("narrator", "female")
    assert escolha.voice_id == STOCK_MALE
    assert escolha.is_fallback


def test_mulher_cai_em_outra_voz_feminina_clonada():
    escolha = pick_voice("speaker", "female", female_pool=["voz_feminina_2"])
    assert escolha.voice_id == "voz_feminina_2"
    assert escolha.kind == "fallback_female"


def test_mulher_sem_pool_cai_na_stock_feminina():
    assert pick_voice("speaker", "female").voice_id == STOCK_FEMALE


def test_homem_cai_na_stock_masculina():
    assert pick_voice("speaker", "male").voice_id == STOCK_MALE


def test_tag_for_pega_o_trecho_com_maior_sobreposicao():
    tags = [SpeakerTag(0, 5, "narrator"), SpeakerTag(5, 10, "speaker", speaker_id="speaker_01")]
    assert tag_for(tags, 6.0, 7.0).speaker_id == "speaker_01"


def test_referencia_junta_varios_trechos_ate_13s():
    tags = [SpeakerTag(0, 6, "speaker", speaker_id="speaker_01"), SpeakerTag(20, 29, "speaker", speaker_id="speaker_01")]
    spans = collect_reference_spans(tags, "speaker_01")
    total = sum(b - a for a, b in spans)
    assert total >= 13.0


def test_referencia_nao_usa_o_proprio_trecho_da_mencao():
    tags = [SpeakerTag(0, 20, "speaker", speaker_id="speaker_01")]
    spans = collect_reference_spans(tags, "speaker_01", exclude=(0, 20))
    assert spans == []


def test_plano_do_narrador_fala_so_o_nome_novo(tokens_factory):
    tokens = tokens_factory("tome sugarbind todo dia")
    mention = Mention(start=tokens[1].start, end=tokens[1].end, text="sugarbind", target="SugarBind", score=99.0)
    tags = [SpeakerTag(0, 10, "narrator", "male", 0.95)]
    plano = build_plan([mention], tokens, tags, new_product_name="GlicoVita")
    assert plano[0].mode == MODE_WORD
    assert plano[0].text_to_speak == "GlicoVita"
    assert plano[0].start == pytest.approx(mention.start)


def test_plano_do_depoimento_fala_a_frase_inteira(tokens_factory):
    tokens = tokens_factory("eu tomei sugarbind por tres meses", step=0.3)
    mention = Mention(start=tokens[2].start, end=tokens[2].end, text="sugarbind", target="SugarBind", score=99.0)
    tags = [SpeakerTag(0, 10, "speaker", "female", 0.4, speaker_id="speaker_01")]
    plano = build_plan([mention], tokens, tags, new_product_name="GlicoVita")
    item = plano[0]
    assert item.mode == MODE_SENTENCE
    assert item.text_to_speak == "eu tomei GlicoVita por tres meses"
    assert item.start == pytest.approx(tokens[0].start)
    assert item.end == pytest.approx(tokens[-1].end)


def test_audio_tracks_uma_faixa_por_locutor(tokens_factory):
    tokens = tokens_factory("um sugarbind dois sugarbind")
    m1 = Mention(start=tokens[1].start, end=tokens[1].end, text="sugarbind", target="S", score=99.0)
    m2 = Mention(start=tokens[3].start, end=tokens[3].end, text="sugarbind", target="S", score=99.0)
    tags = [
        SpeakerTag(tokens[1].start, tokens[1].end, "narrator", "male", 0.95),
        SpeakerTag(tokens[3].start, tokens[3].end, "speaker", "female", 0.3, speaker_id="speaker_01"),
    ]
    plano = build_plan([m1, m2], tokens, tags, new_product_name="GlicoVita")
    tracks = audio_tracks(plano)
    assert set(tracks) == {"narrator", "speaker_01"}
