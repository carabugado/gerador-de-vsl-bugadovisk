"""Cliente MiniMax (clone de voz + TTS).

DOIS DETALHES QUE CUSTARAM CARO:

1) A chave funciona OMITINDO o GroupId. Chame `POST /v1/t2a_v2` SEM
   `?GroupId=` na URL — mandar o parâmetro vazio dá erro. Por isso
   `endpoint()` nunca monta query string.

2) Voice clone tem COTA DE SLOTS. Se vier `2052 insufficient voice slot`,
   o padrão é clonar → gerar → deletar UMA voz por vez
   (`/v1/voice_clone` → `/v1/t2a_v2` → `/v1/delete_voice`). Nunca acumule.
   Use `ephemeral_voice()`, que deleta no finally mesmo se a geração falhar.

E sempre confira `base_resp.status_code == 0` no clone: a API responde 200 HTTP
com erro dentro do corpo.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from dataclasses import dataclass

BASE_URL = "https://api.minimax.io/v1"
INSUFFICIENT_VOICE_SLOT = 2052
DEFAULT_MODEL = "speech-02-hd"

# Vozes de catálogo usadas como último recurso (ver voice/rebrand.py)
STOCK_MALE = "Deep_Voice_Man"
STOCK_FEMALE = "Wise_Woman"


class MinimaxError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"MiniMax {code}: {message}")
        self.code = code
        self.message = message


class InsufficientVoiceSlot(MinimaxError):
    """2052 — a conta está com todos os slots de clone ocupados."""


def endpoint(path: str) -> str:
    """URL do endpoint. NUNCA inclui GroupId (com ele vazio a API recusa)."""
    return f"{BASE_URL}/{path.lstrip('/')}"


def check_base_resp(payload: dict, *, what: str = "chamada") -> dict:
    """Levanta se `base_resp.status_code != 0`."""
    data = payload or {}
    base = data.get("base_resp") or {}
    code = int(base.get("status_code", 0) or 0)
    if code == 0:
        return data
    message = base.get("status_msg") or f"falha na {what}"
    if code == INSUFFICIENT_VOICE_SLOT:
        raise InsufficientVoiceSlot(code, f"{message} — delete a voz anterior antes de clonar a próxima")
    raise MinimaxError(code, message)


def new_voice_id(prefix: str = "tdp") -> str:
    """MiniMax exige voice_id alfanumérico começando com letra."""
    return f"{prefix}{uuid.uuid4().hex[:16]}"


@dataclass
class TTSResult:
    audio: bytes
    voice_id: str
    trace: str = ""


class MinimaxClient:
    """Cliente mínimo. `transport` injetável pra teste."""

    def __init__(self, api_key: str | None = None, *, transport=None, model: str = DEFAULT_MODEL):
        self.api_key = (api_key or os.environ.get("MINIMAX_API_KEY", "")).strip()
        self.model = model
        self._transport = transport

    # ── infra ───────────────────────────────────────────────────────────
    @property
    def headers(self) -> dict:
        if not self.api_key:
            raise MinimaxError(-1, "MINIMAX_API_KEY não configurada (.env na raiz)")
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(self, path: str, *, json_body: dict | None = None, files: dict | None = None, data: dict | None = None) -> dict:
        url = endpoint(path)
        if self._transport is not None:
            return self._transport(url=url, headers=self.headers, json=json_body, files=files, data=data)
        import httpx  # noqa: PLC0415

        headers = dict(self.headers)
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        with httpx.Client(timeout=180.0) as client:
            response = client.post(url, headers=headers, json=json_body, files=files, data=data)
            response.raise_for_status()
            return response.json()

    # ── endpoints ───────────────────────────────────────────────────────
    def upload_clone_file(self, path: str | os.PathLike[str]) -> str:
        """Sobe o áudio de referência e devolve o file_id."""
        with open(path, "rb") as handle:
            payload = self._request(
                "files/upload",
                files={"file": (os.path.basename(str(path)), handle, "audio/wav")},
                data={"purpose": "voice_clone"},
            )
        data = check_base_resp(payload, what="upload do áudio de referência")
        file_id = ((data.get("file") or {}).get("file_id")) or data.get("file_id")
        if not file_id:
            raise MinimaxError(-1, "upload não retornou file_id")
        return str(file_id)

    def clone_voice(self, file_id: str, voice_id: str | None = None, *, text_preview: str | None = None) -> str:
        body: dict = {"file_id": file_id, "voice_id": voice_id or new_voice_id()}
        if text_preview:
            body["text"] = text_preview[:300]
            body["model"] = self.model
        payload = self._request("voice_clone", json_body=body)
        check_base_resp(payload, what="clonagem de voz")
        return body["voice_id"]

    def t2a(
        self,
        text: str,
        voice_id: str,
        *,
        speed: float = 1.0,
        volume: float = 1.0,
        pitch: int = 0,
        sample_rate: int = 32000,
        audio_format: str = "mp3",
    ) -> TTSResult:
        body = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": volume,
                "pitch": pitch,
            },
            "audio_setting": {
                "sample_rate": sample_rate,
                "format": audio_format,
                "channel": 1,
            },
        }
        payload = self._request("t2a_v2", json_body=body)  # sem ?GroupId=
        data = check_base_resp(payload, what="geração de áudio")
        audio_hex = ((data.get("data") or {}).get("audio")) or ""
        if not audio_hex:
            raise MinimaxError(-1, "t2a_v2 não retornou áudio")
        return TTSResult(audio=bytes.fromhex(audio_hex), voice_id=voice_id, trace=data.get("trace_id", ""))

    def delete_voice(self, voice_id: str, *, voice_type: str = "voice_cloning") -> None:
        payload = self._request("delete_voice", json_body={"voice_type": voice_type, "voice_id": voice_id})
        check_base_resp(payload, what="remoção da voz clonada")

    # ── padrão obrigatório: clonar → gerar → deletar ─────────────────────
    @contextlib.contextmanager
    def ephemeral_voice(self, reference_wav: str | os.PathLike[str], *, prefix: str = "tdp"):
        """Clona, entrega o voice_id e SEMPRE deleta no fim.

        É o único jeito de não estourar a cota de slots (2052).
        """
        file_id = self.upload_clone_file(reference_wav)
        voice_id = self.clone_voice(file_id, new_voice_id(prefix))
        try:
            yield voice_id
        finally:
            with contextlib.suppress(Exception):
                self.delete_voice(voice_id)

    def speak_cloned(self, reference_wav: str | os.PathLike[str], text: str, **kwargs) -> TTSResult:
        """Clone efêmero + geração, num passo só."""
        with self.ephemeral_voice(reference_wav) as voice_id:
            return self.t2a(text, voice_id, **kwargs)
