import numpy as np
import pytest

from troca_produto.media.audio import (
    atempo_chain,
    edge_trim_points,
    extract_audio_cmd,
    fit_duration_cmd,
    parse_silences,
    to_mono_mp3_cmd,
    trim_edges_cmd,
)
from troca_produto.media.ffmpeg import frames_to_timecode, parse_fps, parse_probe, seconds_to_frames
from troca_produto.media.frames import sample_times
from troca_produto.media.wavio import read_wav, slice_samples, write_wav


@pytest.fixture(autouse=True)
def _ffmpeg(monkeypatch):
    monkeypatch.setenv("TDP_FFMPEG", "/usr/bin/ffmpeg")
    monkeypatch.setenv("TDP_FFPROBE", "/usr/bin/ffprobe")


def test_parse_fps_de_fracao():
    assert parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert parse_fps("25/1") == 25.0
    assert parse_fps(None) == 30.0
    assert parse_fps("0/0") == 30.0


def test_parse_probe_le_video_e_audio():
    info = parse_probe(
        {
            "format": {"duration": "120.5"},
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30/1"},
                {"codec_type": "audio"},
            ],
        }
    )
    assert info.duration == pytest.approx(120.5)
    assert (info.width, info.height) == (1920, 1080)
    assert info.has_audio and info.frames == 3615


def test_timecode_de_frames():
    assert frames_to_timecode(0, 30) == "00:00:00:00"
    assert frames_to_timecode(90, 30) == "00:00:03:00"
    assert frames_to_timecode(30 * 60 * 61 + 5, 30) == "01:01:00:05"


def test_seconds_to_frames_arredonda():
    assert seconds_to_frames(1.0, 30) == 30
    assert seconds_to_frames(0.49, 30) == 15


def test_extrai_audio_mono_16k():
    cmd = extract_audio_cmd("v.mp4", "a.wav")
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "16000"


def test_trim_usa_ss_e_to_e_nunca_stop_periods():
    cmd = trim_edges_cmd("in.wav", "out.wav", 0.2, 3.4)
    texto = " ".join(cmd)
    assert "-ss" in cmd and "-to" in cmd
    assert "stop_periods" not in texto  # cortaria na 1ª pausa interna e truncaria a frase
    assert "silenceremove" not in texto


def test_parse_silences_pareia_start_e_end():
    log = "silence_start: 0.0\nsilence_end: 0.35 | silence_duration: 0.35\nsilence_start: 4.2\nsilence_end: 4.9"
    assert parse_silences(log) == [(0.0, 0.35), (4.2, 4.9)]


def test_edge_trim_ignora_silencio_do_meio():
    start, end = edge_trim_points([(0.0, 0.3), (2.0, 2.4), (4.7, 5.0)], 5.0)
    assert start == pytest.approx(0.3)
    assert end == pytest.approx(4.7)


def test_edge_trim_sem_silencio_mantem_tudo():
    assert edge_trim_points([], 5.0) == (0.0, 5.0)


def test_atempo_em_1_nao_gera_filtro():
    assert atempo_chain(1.0) == []


def test_atempo_quebra_valores_extremos():
    cadeia = atempo_chain(3.0)
    assert len(cadeia) == 2
    produto = 1.0
    for item in cadeia:
        produto *= float(item.split("=")[1])
    assert produto == pytest.approx(3.0)


def test_atempo_recusa_zero():
    with pytest.raises(ValueError):
        atempo_chain(0)


def test_fit_duration_limita_o_esticamento():
    cmd = fit_duration_cmd("in.mp3", "out.mp3", 10.0, 1.0, max_stretch=1.35)
    filtro = cmd[cmd.index("-filter:a") + 1]
    assert float(filtro.split("=")[1]) == pytest.approx(1.35)


def test_fit_duration_sem_ajuste_nao_poe_filtro():
    cmd = fit_duration_cmd("in.mp3", "out.mp3", 5.0, 5.0)
    assert "-filter:a" not in cmd


def test_clipe_de_voz_sai_mono():
    cmd = to_mono_mp3_cmd("in.wav", "out.mp3")
    assert cmd[cmd.index("-ac") + 1] == "1"


def test_sample_times_um_por_segundo():
    assert sample_times(3.0, every=1.0) == [0.0, 1.0, 2.0]
    assert sample_times(0, every=1.0) == []


def test_wav_roundtrip(tmp_path):
    sr = 16000
    sinal = np.sin(2 * np.pi * 120 * np.arange(sr) / sr).astype(np.float32)
    caminho = tmp_path / "t.wav"
    write_wav(caminho, sinal, sr)
    lido, taxa = read_wav(caminho)
    assert taxa == sr
    assert len(lido) == sr
    assert np.max(np.abs(lido - sinal)) < 0.01


def test_slice_samples_corta_pelo_tempo():
    dados = np.arange(1000, dtype=np.float32)
    pedaco = slice_samples(dados, 100, 1.0, 2.0)
    assert len(pedaco) == 100 and pedaco[0] == 100
