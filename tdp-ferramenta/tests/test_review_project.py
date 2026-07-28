import pytest

from troca_produto.briefing import Briefing, QuantityRule
from troca_produto.pipeline.ocr_detect import detect_onscreen, match_ocr_text
from troca_produto.pipeline.qa import Finding, QaReport, qa_exit_code
from troca_produto.pipeline.ranges import Range
from troca_produto.project import Project, State
from troca_produto.review import (
    assets_for_export,
    auto_assign_assets,
    drop,
    format_table,
    keep,
    keep_only,
    parse_indices,
    set_asset,
)


@pytest.fixture
def state():
    return State(
        duration=100.0,
        fps=30.0,
        ranges=[
            Range(1, 2, "spoken", 0.9, "falado: sugarbind", meta={"text": "sugarbind"}),
            Range(10, 12, "visual", 0.8, "produto antigo (CLIP)"),
            Range(30, 33, "spoken", 0.95, "falado: leve 6 frascos", meta={"text": "leve 6 frascos"}),
        ],
    )


def test_parse_indices_aceita_lista_e_intervalo():
    assert parse_indices("1,3,5-7") == [0, 2, 4, 5, 6]


def test_parse_indices_ignora_lixo_e_limita():
    assert parse_indices("abc,2,99", total=3) == [1]


def test_drop_e_keep(state):
    drop(state, [0, 1])
    assert state.dropped == [0, 1]
    keep(state, [0])
    assert state.dropped == [1]


def test_keep_only(state):
    keep_only(state, [2])
    assert state.dropped == [0, 1]
    assert [r.start for r in state.kept_ranges] == [30]


def test_set_asset_guarda_por_indice(state):
    set_asset(state, 1, "novo.png")
    assert state.new_assets["1"] == "novo.png"


def test_auto_assets_usa_o_pack_da_quantidade_falada(state):
    briefing = Briefing(
        new_assets=["arte_padrao.png"],
        quantity_map=[QuantityRule(spoken="6 frascos", pack_asset="pack6.png")],
    )
    auto_assign_assets(state, briefing)
    assert state.new_assets["2"] == "pack6.png"
    assert state.new_assets["0"] == "arte_padrao.png"


def test_assets_for_export_reindexa_apos_derrubar(state):
    set_asset(state, 2, "pack6.png")
    drop(state, [0])
    # a faixa 2 vira a 1 depois de derrubar a 0
    assert assets_for_export(state) == {1: "pack6.png"}


def test_format_table_mostra_derrubadas(state):
    drop(state, [1])
    texto = format_table(state)
    assert "3 " in texto and "1 derrubadas" in texto


def test_format_table_sem_faixas():
    assert "nenhuma" in format_table(State())


def test_state_roundtrip_no_disco(tmp_path, state):
    project = Project(tmp_path / "proj").ensure()
    state.new_assets["0"] = "x.png"
    project.save_state(state)
    voltou = project.load_state()
    assert len(voltou.ranges) == 3
    assert voltou.new_assets["0"] == "x.png"
    assert voltou.ranges[0].meta["text"] == "sugarbind"


def test_project_cria_o_layout(tmp_path):
    project = Project(tmp_path / "proj").ensure()
    for path in (project.cache_dir, project.frames_dir, project.audio_dir, project.voice_dir, project.export_dir):
        assert path.is_dir()
    assert project.base_audio.name == "base.wav"
    assert project.xml_path.name == "troca_COMPLETO.xml"


def test_state_vazio_quando_nao_ha_arquivo(tmp_path):
    assert Project(tmp_path / "nada").load_state().ranges == []


def test_ocr_casa_nome_torto_na_tela():
    score, alvo = match_ocr_text("BUY SUGARBIND TODAY", ["SugarBind"])
    assert alvo == "SugarBind" and score >= 78


def test_ocr_ignora_texto_sem_relacao():
    assert match_ocr_text("100% money back guarantee", ["SugarBind"]) == (0.0, "")


def test_detect_onscreen_agrupa_frames_seguidos():
    ranges = detect_onscreen(
        ["f0.jpg", "f1.jpg", "f2.jpg"],
        [0.0, 1.0, 2.0],
        ["SugarBind"],
        reader=lambda p: "SUGARBIND" if p != "f2.jpg" else "nada",
    )
    assert len(ranges) == 1
    assert ranges[0].kind == "onscreen"
    assert ranges[0].end == 2.0


def test_qa_limpo_sai_com_zero():
    assert QaReport().exit_code == 0
    assert qa_exit_code([]) == 0


def test_qa_com_sobra_sai_com_dois():
    report = QaReport(findings=[Finding("spoken", 1.0, 2.0, 'ainda fala "sugarbind"')])
    assert report.exit_code == 2
    assert not report.clean
    assert "REPROVADO" in report.summary()
