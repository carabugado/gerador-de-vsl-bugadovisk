
import pytest

from troca_produto.assets.cutout import (
    REMBG_MODEL,
    is_fake_png,
    list_assets,
    prores4444_cmd,
)
from troca_produto.assets.packs import parse_quantity, pick_pack
from troca_produto.briefing import QuantityRule


def test_modelo_de_recorte_e_o_birefnet():
    assert REMBG_MODEL == "birefnet-general"


def test_png_sem_alpha_e_png_falso():
    assert is_fake_png("RGB")
    assert not is_fake_png("RGBA")
    assert not is_fake_png("LA")


def test_list_assets_funciona_em_pasta_com_colchete(tmp_path):
    # glob.glob morre aqui: '[GRUPO FENIX]' vira classe de caractere
    pasta = tmp_path / "[GRUPO FENIX]"
    pasta.mkdir()
    (pasta / "frasco.png").write_bytes(b"x")
    (pasta / "notas.txt").write_text("x")
    achados = list_assets(pasta)
    assert len(achados) == 1 and achados[0].endswith("frasco.png")


def test_list_assets_em_pasta_inexistente():
    assert list_assets("/nao/existe/mesmo") == []


def test_broll_com_alpha_e_prores4444(monkeypatch):
    monkeypatch.setenv("TDP_FFMPEG", "/usr/bin/ffmpeg")
    cmd = prores4444_cmd("in.png", "saida.mov")
    assert "prores_ks" in cmd
    assert "4444" in cmd
    assert "yuva444p10le" in cmd


def test_mp4_nao_guarda_alpha(monkeypatch):
    monkeypatch.setenv("TDP_FFMPEG", "/usr/bin/ffmpeg")
    with pytest.raises(ValueError):
        prores4444_cmd("in.png", "saida.mp4")


@pytest.mark.parametrize(
    "falado,primary,bonus",
    [
        ("6 frascos", 6, 0),
        ("six bottles", 6, 0),
        ("buy 3 get 1 free", 3, 1),
        ("3 1", 3, 1),
        ("free +3", 0, 3),
    ],
)
def test_parse_quantity(falado, primary, bonus):
    q = parse_quantity(falado)
    assert (q.primary, q.bonus) == (primary, bonus)


def test_parse_quantity_sem_numero():
    assert parse_quantity("compre agora").numbers == []


def test_pick_pack_casa_por_texto():
    regras = [QuantityRule(spoken="6 frascos", pack_asset="pack6.png")]
    assert pick_pack("6 frascos", regras) == "pack6.png"


def test_pick_pack_casa_por_quantidade_quando_o_texto_muda():
    regras = [QuantityRule(spoken="6 bottles", pack_asset="pack6.png"), QuantityRule(spoken="3 bottles", pack_asset="pack3.png")]
    assert pick_pack("leve 6 frascos hoje", regras) == "pack6.png"


def test_pick_pack_sem_regra_devolve_vazio():
    assert pick_pack("6 frascos", []) == ""
