import xml.etree.ElementTree as ET

import pytest

from troca_produto.briefing import Briefing
from troca_produto.export.premiere_xml import (
    LABEL_BY_KIND,
    build_timeline,
    label_for,
    path_to_url,
    write_xml,
)
from troca_produto.export.reports import build_report, write_csv
from troca_produto.pipeline.ranges import Range, check_coverage


@pytest.fixture
def timeline():
    ranges = [
        Range(1.0, 3.0, "spoken", 0.95, 'falado: "sugarbind"'),
        Range(10.0, 14.0, "visual", 0.82, "produto antigo (CLIP)"),
        Range(20.0, 21.0, "onscreen", 0.9, "texto na tela: SugarBind"),
    ]
    return build_timeline(
        video_path="/tmp/vsl.mp4",
        duration=60.0,
        fps=30.0,
        ranges=ranges,
        new_assets={0: "/tmp/novo.png", 1: "/tmp/novo.png"},
        voice_items={"narrator": [(1.0, 3.0, "/tmp/voz.mp3", "GlicoVita")]},
        audio_path="/tmp/base.wav",
    )


def test_cores_por_tipo_sao_distintas():
    assert label_for("spoken") != label_for("visual") != label_for("onscreen")
    assert label_for("desconhecido") == LABEL_BY_KIND["mixed"]


def test_path_to_url_escapa_colchete():
    url = path_to_url("/tmp/[GRUPO FENIX]/frasco.png")
    assert url.startswith("file://localhost/")
    assert "[" not in url and "%5B" in url


def test_timeline_tem_duas_trilhas_de_video(timeline):
    assert len(timeline.video_tracks) == 2


def test_v1_tem_um_clipe_por_aparicao(timeline):
    assert len(timeline.video_tracks[0]) == 3


def test_v1_colore_por_tipo(timeline):
    cores = [c.label for c in timeline.video_tracks[0]]
    assert cores == [LABEL_BY_KIND["spoken"], LABEL_BY_KIND["visual"], LABEL_BY_KIND["onscreen"]]


def test_v2_alinha_no_mesmo_frame_do_v1(timeline):
    v1 = timeline.video_tracks[0]
    v2 = timeline.video_tracks[1]
    assert len(v2) == 2
    assert v2[0].start == v1[0].start and v2[0].end == v1[0].end
    assert v2[1].start == v1[1].start


def test_a1_cobre_o_video_inteiro(timeline):
    a1 = timeline.audio_tracks[0][0]
    assert a1.start == 0 and a1.end == timeline.duration_frames


def test_a2_tem_a_voz_do_locutor(timeline):
    assert len(timeline.audio_tracks) == 2
    assert timeline.audio_tracks[1][0].label == LABEL_BY_KIND["voice"]


def test_marcadores_um_por_aparicao(timeline):
    assert len(timeline.markers) == 3
    assert timeline.markers[0].frame == 30


def test_xml_tem_doctype_e_estrutura(tmp_path, timeline):
    caminho = write_xml(timeline, tmp_path / "troca_COMPLETO.xml")
    texto = open(caminho, encoding="utf-8").read()
    assert texto.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')
    root = ET.fromstring(texto.split("\n", 2)[2])
    assert root.tag == "xmeml" and root.get("version") == "5"
    assert root.find("./sequence/media/video") is not None


def test_xml_define_cada_arquivo_uma_vez_so(tmp_path, timeline):
    caminho = write_xml(timeline, tmp_path / "t.xml")
    root = ET.fromstring(open(caminho, encoding="utf-8").read().split("\n", 2)[2])
    definidos = [f for f in root.iter("file") if f.find("pathurl") is not None]
    ids = [f.get("id") for f in definidos]
    assert len(ids) == len(set(ids))  # nenhum <file> redefinido


def test_xml_de_duas_exportacoes_e_identico(tmp_path, timeline):
    a = open(write_xml(timeline, tmp_path / "a.xml"), encoding="utf-8").read()
    b = open(write_xml(timeline, tmp_path / "b.xml"), encoding="utf-8").read()
    assert a == b  # ids não vazam entre exportações


def test_xml_traz_a_faixa_de_voz(tmp_path, timeline):
    caminho = write_xml(timeline, tmp_path / "t.xml")
    root = ET.fromstring(open(caminho, encoding="utf-8").read().split("\n", 2)[2])
    trilhas = root.findall("./sequence/media/audio/track")
    assert len(trilhas) == 2


def test_csv_tem_uma_linha_por_aparicao(tmp_path):
    ranges = [Range(1.0, 2.0, "spoken", 0.9, "x"), Range(5.0, 6.0, "visual", 0.8, "y")]
    caminho = write_csv(ranges, tmp_path / "t.csv", fps=30.0, new_assets={0: "novo.png"})
    linhas = open(caminho, encoding="utf-8").read().strip().splitlines()
    assert len(linhas) == 3
    assert "novo.png" in linhas[1]


def test_report_mostra_cobertura_e_avisa_de_over_detection():
    ranges = [Range(0, 45, "visual", 0.9, "clip")]
    texto = build_report(
        briefing=Briefing(old_product_name="A", new_product_name="B", video="v.mp4"),
        ranges=ranges,
        duration=100.0,
        coverage=check_coverage(ranges, 100.0),
    )
    assert "over-detection" in texto
    assert "troca_COMPLETO.xml" in texto
