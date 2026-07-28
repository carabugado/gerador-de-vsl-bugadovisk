import json

import pytest

from troca_produto.briefing import Briefing
from troca_produto.cli import main
from troca_produto.pipeline.ranges import Range
from troca_produto.project import Project, State


def test_init_cria_briefing(tmp_path, capsys):
    saida = tmp_path / "briefing.json"
    assert main(["init", "--out", str(saida)]) == 0
    assert saida.is_file()
    assert "old_product_name" in capsys.readouterr().out


def test_init_nao_sobrescreve(tmp_path):
    saida = tmp_path / "briefing.json"
    main(["init", "--out", str(saida)])
    assert main(["init", "--out", str(saida)]) == 1


def test_doctor_lista_binarios_e_chaves(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "doctor"]) == 0
    texto = capsys.readouterr().out
    assert "ffmpeg" in texto and "MINIMAX_API_KEY" in texto
    assert "engine visual" in texto


def test_doctor_le_o_env_da_raiz(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    (tmp_path / ".env").write_text("MINIMAX_API_KEY=abc123\n")
    main(["--root", str(tmp_path), "doctor"])
    linha = [l for l in capsys.readouterr().out.splitlines() if "MINIMAX_API_KEY" in l][0]
    assert linha.strip().startswith("ok")


def test_analyze_recusa_briefing_incompleto(tmp_path, capsys):
    caminho = tmp_path / "b.json"
    Briefing(old_product_name="", new_product_name="").save(caminho)
    code = main(["analyze", "--briefing", str(caminho), "--dir", str(tmp_path / "proj")])
    assert code == 1
    assert "briefing incompleto" in capsys.readouterr().out


def _projeto(tmp_path):
    project = Project(tmp_path / "proj").ensure()
    Briefing(
        old_product_name="SugarBind",
        new_product_name="GlicoVita",
        new_assets=["arte.png"],
        video="v.mp4",
    ).save(project.briefing_path)
    state = State(
        video=str(tmp_path / "v.mp4"),
        duration=60.0,
        fps=30.0,
        ranges=[
            Range(1, 3, "spoken", 0.9, "falado: sugarbind", meta={"text": "sugarbind"}),
            Range(10, 14, "visual", 0.8, "produto antigo (CLIP)"),
        ],
    )
    project.save_state(state)
    return project


def test_review_mostra_a_tabela(tmp_path, capsys):
    project = _projeto(tmp_path)
    assert main(["review", "--dir", str(project.dir)]) == 0
    assert "falado" in capsys.readouterr().out


def test_review_derruba_e_persiste(tmp_path):
    project = _projeto(tmp_path)
    assert main(["review", "--dir", str(project.dir), "--drop", "2"]) == 0
    assert project.load_state().dropped == [1]


def test_review_auto_assets(tmp_path):
    project = _projeto(tmp_path)
    main(["review", "--dir", str(project.dir), "--auto-assets"])
    assert project.load_state().new_assets["0"] == "arte.png"


def test_review_sem_analise(tmp_path, capsys):
    assert main(["review", "--dir", str(tmp_path / "vazio")]) == 1
    assert "analyze" in capsys.readouterr().out


def test_export_gera_xml_csv_e_report(tmp_path, capsys):
    project = _projeto(tmp_path)
    main(["review", "--dir", str(project.dir), "--auto-assets"])
    assert main(["export", "--dir", str(project.dir)]) == 0
    assert project.xml_path.is_file()
    assert project.csv_path.is_file()
    assert project.report_path.is_file()
    texto = project.xml_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE xmeml>" in texto and "label2" in texto


def test_export_respeita_faixas_derrubadas(tmp_path):
    project = _projeto(tmp_path)
    main(["review", "--dir", str(project.dir), "--drop", "1"])
    main(["export", "--dir", str(project.dir)])
    linhas = project.csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 2  # cabeçalho + 1 faixa


def test_export_sem_estado(tmp_path, capsys):
    project = Project(tmp_path / "proj").ensure()
    Briefing(old_product_name="A", new_product_name="B").save(project.briefing_path)
    assert main(["export", "--dir", str(project.dir)]) == 1


def test_rebrand_sem_mencoes(tmp_path, capsys):
    project = _projeto(tmp_path)
    assert main(["rebrand", "--dir", str(project.dir)]) == 1
    assert "analyze" in capsys.readouterr().out


def test_rebrand_run_sem_chave(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    project = _projeto(tmp_path)
    state = project.load_state()
    state.mentions = [{"start": 1.0, "end": 1.5, "text": "sugarbind", "target": "SugarBind", "score": 99.0}]
    project.save_state(state)
    project.transcript_path.write_text(json.dumps({"words": []}), encoding="utf-8")
    assert main(["--root", str(tmp_path), "rebrand", "--dir", str(project.dir), "--run"]) == 1
    assert "MINIMAX_API_KEY" in capsys.readouterr().out


def test_cutout_audit_em_caminho_inexistente(tmp_path, capsys):
    assert main(["cutout", "--input", str(tmp_path / "nao_existe"), "--audit"]) == 1


def test_help_lista_todos_os_comandos(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    texto = capsys.readouterr().out
    for comando in ("init", "doctor", "analyze", "review", "rebrand", "export", "cutout", "qa"):
        assert comando in texto


def test_refs_sem_mencoes(tmp_path, capsys):
    project = _projeto(tmp_path)
    assert main(["refs", "--dir", str(project.dir)]) == 1
    assert "analyze" in capsys.readouterr().out
