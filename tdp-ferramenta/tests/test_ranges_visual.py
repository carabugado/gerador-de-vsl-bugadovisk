import numpy as np
import pytest

from troca_produto.pipeline.analyze import candidate_times, combine_ranges, mentions_to_ranges
from troca_produto.pipeline.ranges import (
    Range,
    check_coverage,
    coverage_ratio,
    merge_ranges,
    subtract,
)
from troca_produto.pipeline.text_detect import Mention
from troca_produto.pipeline.visual_detect import (
    best_scores,
    cosine_matrix,
    engine_plan,
    hits_to_ranges,
    suggest_threshold,
)


def test_merge_junta_faixas_coladas():
    merged = merge_ranges([Range(0, 1, "visual"), Range(1.5, 2.5, "visual")], gap=0.75)
    assert len(merged) == 1 and merged[0].end == 2.5


def test_merge_respeita_buraco_grande():
    merged = merge_ranges([Range(0, 1, "visual"), Range(5, 6, "visual")], gap=0.75)
    assert len(merged) == 2


def test_merge_nao_mistura_tipos_diferentes():
    merged = merge_ranges([Range(0, 1, "visual"), Range(1.1, 2, "spoken")], gap=0.75)
    assert len(merged) == 2


def test_coverage_conta_uniao_e_nao_soma():
    ranges = [Range(0, 10, "visual"), Range(5, 15, "spoken")]
    assert coverage_ratio(ranges, 100.0) == pytest.approx(0.15)


def test_cobertura_alta_e_reprovada():
    report = check_coverage([Range(0, 45, "visual")], 100.0)
    assert report.level == "fail" and not report.ok
    assert "over-detection" in report.message


def test_cobertura_media_avisa():
    assert check_coverage([Range(0, 33, "visual")], 100.0).level == "warn"


def test_cobertura_baixa_passa():
    assert check_coverage([Range(0, 8, "visual")], 100.0).ok


def test_subtract_abre_buraco():
    resto = subtract([Range(0, 10, "visual")], [Range(4, 6, "spoken")])
    assert [(r.start, r.end) for r in resto] == [(0, 4), (6, 10)]


def test_engine_cloud_sem_chave_avisa_e_nao_usa_opencv():
    plan = engine_plan("cloud", has_anthropic=False)
    assert plan.use_clip and not plan.use_opencv and not plan.use_cloud_vision
    assert any("contact sheet" in w for w in plan.warnings)


def test_engine_cloud_com_chave_liga_visao():
    plan = engine_plan("cloud", has_anthropic=True)
    assert plan.use_cloud_vision and not plan.use_opencv


def test_engine_local_avisa_dos_73_por_cento():
    plan = engine_plan("local")
    assert plan.use_opencv and any("73%" in w for w in plan.warnings)


def test_cosine_de_vetores_iguais_e_um():
    a = np.array([[1.0, 0.0], [0.0, 2.0]])
    sims = cosine_matrix(a, a)
    assert sims[0][0] == pytest.approx(1.0)
    assert sims[0][1] == pytest.approx(0.0)


def test_best_scores_pega_a_melhor_referencia():
    frames = np.array([[1.0, 0.0]])
    refs = np.array([[0.0, 1.0], [1.0, 0.0]])
    assert best_scores(frames, refs)[0] == pytest.approx(1.0)


def test_hits_to_ranges_agrupa_frames_seguidos():
    times = [0.0, 1.0, 2.0, 10.0]
    scores = [0.9, 0.9, 0.9, 0.1]
    ranges = hits_to_ranges(times, scores, threshold=0.78, every=1.0)
    assert len(ranges) == 1
    assert ranges[0].start == 0.0 and ranges[0].end == 3.0


def test_hits_to_ranges_descarta_pico_curto_demais():
    ranges = hits_to_ranges([0.0], [0.99], threshold=0.78, every=0.2, min_duration=0.6)
    assert ranges == []


def test_suggest_threshold_nunca_desce_do_piso():
    assert suggest_threshold([0.1, 0.2, 0.3]) == pytest.approx(0.78)


def test_mentions_to_ranges_poe_folga_nas_pontas():
    ranges = mentions_to_ranges([Mention(start=1.0, end=1.5, text="sugarbind", target="SugarBind", score=95.0)])
    assert ranges[0].start < 1.0 and ranges[0].end > 1.5
    assert ranges[0].kind == "spoken"


def test_combine_nao_deixa_faixas_sobrepostas_na_v1():
    combinadas = combine_ranges(
        [Range(0, 5, "spoken", 0.9, "falado")],
        [Range(3, 8, "visual", 0.8, "clip")],
    )
    assert len(combinadas) == 1
    assert combinadas[0].kind == "mixed"
    assert (combinadas[0].start, combinadas[0].end) == (0, 8)


def test_combine_mantem_tipo_quando_nao_ha_cruzamento():
    combinadas = combine_ranges([Range(0, 2, "spoken")], [Range(9, 10, "visual")])
    assert [c.kind for c in combinadas] == ["spoken", "visual"]


def test_combine_vazio():
    assert combine_ranges([], []) == []


def test_candidate_times_cerca_cada_mencao():
    times = candidate_times([{"start": 10.0}], 60.0, offsets=(-1.5, 0.5, 3.0))
    assert times == [8.5, 10.5, 13.0]


def test_candidate_times_nao_sai_do_video():
    assert candidate_times([{"start": 0.2}], 1.0, offsets=(-1.5, 0.5, 3.0)) == [0.7]


def test_candidate_times_nao_repete_momento():
    times = candidate_times([{"start": 10.0}, {"start": 10.0}], 60.0, offsets=(0.5,))
    assert times == [10.5]


def test_candidate_times_respeita_o_limite():
    mentions = [{"start": float(i)} for i in range(50)]
    assert len(candidate_times(mentions, 100.0, limit=10)) == 10
