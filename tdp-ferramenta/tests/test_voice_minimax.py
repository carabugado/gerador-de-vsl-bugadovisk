import pytest

from troca_produto.voice.minimax import (
    INSUFFICIENT_VOICE_SLOT,
    InsufficientVoiceSlot,
    MinimaxClient,
    MinimaxError,
    check_base_resp,
    endpoint,
    new_voice_id,
)

OK = {"base_resp": {"status_code": 0, "status_msg": "success"}}


class FakeTransport:
    """Grava as chamadas e devolve respostas roteirizadas."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, *, url, headers, json=None, files=None, data=None):
        self.calls.append(url)
        path = url.rsplit("/", 1)[-1]
        response = self.responses.get(path, OK)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def paths(self):
        return [c.rsplit("/", 1)[-1] for c in self.calls]


def test_endpoint_nunca_manda_groupid():
    url = endpoint("t2a_v2")
    assert url == "https://api.minimax.io/v1/t2a_v2"
    assert "GroupId" not in url and "?" not in url


def test_check_base_resp_deixa_passar_status_zero():
    assert check_base_resp(OK) is not None


def test_check_base_resp_levanta_em_erro_generico():
    with pytest.raises(MinimaxError):
        check_base_resp({"base_resp": {"status_code": 1004, "status_msg": "auth failed"}})


def test_2052_vira_erro_de_slot_com_dica():
    with pytest.raises(InsufficientVoiceSlot) as exc:
        check_base_resp({"base_resp": {"status_code": INSUFFICIENT_VOICE_SLOT, "status_msg": "insufficient voice slot"}})
    assert "delete a voz anterior" in str(exc.value)


def test_voice_id_comeca_com_letra():
    ident = new_voice_id()
    assert ident[0].isalpha() and ident.isalnum()


def test_t2a_decodifica_o_audio_hex():
    transport = FakeTransport({"t2a_v2": {"base_resp": {"status_code": 0}, "data": {"audio": "48656c6c6f"}}})
    client = MinimaxClient("chave", transport=transport)
    resultado = client.t2a("oi", "voz1")
    assert resultado.audio == b"Hello"


def test_t2a_sem_audio_reclama():
    transport = FakeTransport({"t2a_v2": {"base_resp": {"status_code": 0}, "data": {}}})
    with pytest.raises(MinimaxError):
        MinimaxClient("chave", transport=transport).t2a("oi", "voz1")


def test_sem_chave_nao_chama_a_api():
    with pytest.raises(MinimaxError):
        MinimaxClient("", transport=FakeTransport()).t2a("oi", "voz1")


def test_ciclo_clona_gera_e_deleta(tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    transport = FakeTransport(
        {
            "upload": {"base_resp": {"status_code": 0}, "file": {"file_id": "f1"}},
            "t2a_v2": {"base_resp": {"status_code": 0}, "data": {"audio": "00"}},
        }
    )
    client = MinimaxClient("chave", transport=transport)
    client.speak_cloned(wav, "texto")
    assert transport.paths == ["upload", "voice_clone", "t2a_v2", "delete_voice"]


def test_voz_e_deletada_mesmo_se_a_geracao_falhar(tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    transport = FakeTransport(
        {
            "upload": {"base_resp": {"status_code": 0}, "file": {"file_id": "f1"}},
            "t2a_v2": {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}},
        }
    )
    client = MinimaxClient("chave", transport=transport)
    with pytest.raises(MinimaxError):
        client.speak_cloned(wav, "texto")
    assert "delete_voice" in transport.paths  # nunca acumula slot


def test_clone_confere_base_resp(tmp_path):
    transport = FakeTransport({"voice_clone": {"base_resp": {"status_code": INSUFFICIENT_VOICE_SLOT, "status_msg": "slot"}}})
    client = MinimaxClient("chave", transport=transport)
    with pytest.raises(InsufficientVoiceSlot):
        client.clone_voice("f1", "voz1")
