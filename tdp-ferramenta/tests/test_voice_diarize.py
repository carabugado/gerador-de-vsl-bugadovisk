import numpy as np
import pytest

from troca_produto.voice.diarize import (
    NARRATOR_SIM_THRESHOLD,
    NarratorReference,
    centroid,
    check_source,
    cosine,
    estimate_f0,
    gender_from_f0,
    tag_speakers,
)


def _tone(freq, seconds=1.0, sr=16000):
    t = np.arange(int(sr * seconds)) / sr
    # onda com harmônicos: parece mais voz que uma senoide pura
    return 0.5 * np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t)


def test_cosine_de_vetor_com_ele_mesmo():
    assert cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_com_vetor_zero_nao_explode():
    assert cosine([0, 0], [1, 1]) == 0.0


def test_centroide_e_normalizado():
    vetor = centroid([[1, 0], [0, 1]])
    assert np.linalg.norm(vetor) == pytest.approx(1.0)


def test_centroide_vazio_reclama():
    with pytest.raises(ValueError):
        centroid([])


def test_narrador_reconhecido_acima_de_085():
    ref = NarratorReference.from_embeddings([[1.0, 0.0, 0.0], [0.98, 0.2, 0.0]])
    papel, sim = ref.classify([1.0, 0.05, 0.0])
    assert papel == "narrator" and sim >= NARRATOR_SIM_THRESHOLD


def test_locutor_distinto_abaixo_de_085():
    ref = NarratorReference.from_embeddings([[1.0, 0.0, 0.0]])
    papel, sim = ref.classify([0.0, 1.0, 0.0])
    assert papel == "speaker" and sim < NARRATOR_SIM_THRESHOLD


def test_gender_pelo_f0():
    assert gender_from_f0(120) == "male"
    assert gender_from_f0(210) == "female"
    assert gender_from_f0(164.9) == "male"
    assert gender_from_f0(165.0) == "female"
    assert gender_from_f0(0) == "unknown"


def test_estimate_f0_acha_a_frequencia_de_homem():
    f0 = estimate_f0(_tone(110), 16000)
    assert 100 <= f0 <= 122
    assert gender_from_f0(f0) == "male"


def test_estimate_f0_acha_a_frequencia_de_mulher():
    f0 = estimate_f0(_tone(210), 16000)
    assert 195 <= f0 <= 225
    assert gender_from_f0(f0) == "female"


def test_estimate_f0_no_silencio_e_zero():
    assert estimate_f0(np.zeros(16000), 16000) == 0.0


def test_tag_speakers_agrupa_locutores_distintos():
    ref = NarratorReference.from_embeddings([[1.0, 0.0, 0.0]])
    spans = [(0, 1), (5, 6), (9, 10)]
    embeddings = [[1.0, 0.02, 0.0], [0.0, 1.0, 0.0], [0.02, 1.0, 0.0]]
    tags = tag_speakers(spans, embeddings, ref, f0s=[110.0, 205.0, 200.0])
    assert tags[0].role == "narrator" and tags[0].speaker_id == "narrator"
    assert tags[1].role == "speaker" and tags[2].role == "speaker"
    assert tags[1].speaker_id == tags[2].speaker_id  # mesma pessoa, dois trechos
    assert tags[1].gender == "female"


def test_check_source_reclama_de_audio_com_tts():
    problemas = check_source("saida/final_render.wav", "tdp/audio/base.wav")
    assert any("TTS" in p for p in problemas)


def test_check_source_aceita_a_base():
    assert check_source("tdp/audio/base.wav", "tdp/audio/base.wav") == []
