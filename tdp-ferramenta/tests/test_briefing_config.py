import json

from troca_produto.briefing import Briefing, QuantityRule, write_template
from troca_produto.config import Settings, parse_env_file, resolve_visual_engine


def test_template_gera_json_editavel(tmp_path):
    path = write_template(tmp_path / "briefing.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["old_product_name"] and data["new_product_name"]
    assert "swap_kind" in data and "swap_target" in data


def test_template_nao_sobrescreve_sem_force(tmp_path):
    path = write_template(tmp_path / "briefing.json")
    try:
        write_template(path)
    except FileExistsError:
        pass
    else:  # pragma: no cover
        raise AssertionError("deveria recusar sobrescrever")


def test_roundtrip_preserva_quantity_map(tmp_path):
    brief = Briefing(
        old_product_name="SugarBind",
        new_product_name="GlicoVita",
        quantity_map=[QuantityRule(spoken="6 frascos", pack_asset="pack6.png")],
    )
    path = brief.save(tmp_path / "b.json")
    voltou = Briefing.load(path)
    assert voltou.quantity_map[0].pack_asset == "pack6.png"


def test_old_terms_junta_nome_e_aliases_sem_repetir():
    brief = Briefing(old_product_name="SugarBind", old_aliases=["sugar bind", "SUGARBIND", ""])
    assert brief.old_terms == ["SugarBind", "sugar bind"]


def test_validate_cobra_assets_quando_troca_visual():
    brief = Briefing(old_product_name="A", new_product_name="B", swap_target="both")
    problemas = brief.validate(check_files=False)
    assert any("old_assets" in p for p in problemas)
    assert any("new_assets" in p for p in problemas)


def test_validate_aceita_briefing_so_de_audio():
    brief = Briefing(old_product_name="A", new_product_name="B", swap_target="audio")
    assert brief.validate(check_files=False) == []


def test_validate_recusa_swap_kind_invalido():
    brief = Briefing(old_product_name="A", new_product_name="B", swap_target="audio", swap_kind="xpto")
    assert any("swap_kind" in p for p in brief.validate(check_files=False))


def test_format_change_marca_troca_de_formato():
    assert Briefing(swap_kind="format_change").changes_format
    assert not Briefing(swap_kind="same_format").changes_format


def test_parse_env_file_ignora_comentario_e_aspas():
    valores = parse_env_file('# comentario\nexport MINIMAX_API_KEY="abc"\nVAZIO=\nsemigual\n')
    assert valores["MINIMAX_API_KEY"] == "abc"
    assert valores["VAZIO"] == ""
    assert "semigual" not in valores


def test_settings_le_chaves_e_flags():
    settings = Settings.from_env({"MINIMAX_API_KEY": "k", "TDP_HOTWORDS": "1", "TDP_VISUAL_ENGINE": "cloud"})
    assert settings.has_minimax and settings.hotwords
    assert not settings.has_anthropic
    assert settings.visual_engine == "cloud"


def test_engine_desconhecido_cai_no_cloud():
    assert resolve_visual_engine({"TDP_VISUAL_ENGINE": "opencv"}) == "cloud"
    assert resolve_visual_engine({}) == "cloud"
    assert resolve_visual_engine({"TDP_VISUAL_ENGINE": "LOCAL"}) == "local"
