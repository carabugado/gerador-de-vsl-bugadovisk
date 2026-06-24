"""
Testes da legendagem local (captioner) e da integração no documento de busca.
Rodar:  python test_captioner.py

Mocka o modelo BLIP (não baixa nada) — testa o WIRING e a integração com _tag_doc.
"""
import os
import shutil
import tempfile
import captioner
import broll_search as bs
import broll_index
import asset_tagger as at

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


# ── fakes do BLIP (não carrega/baixa o modelo de verdade) ──────────────────────
class _FakeBatch(dict):
    def to(self, dev):
        return self


class _FakeProc:
    def __call__(self, images=None, return_tensors=None):
        return _FakeBatch()

    def decode(self, ids, skip_special_tokens=True):
        return "  an elderly woman holding her stomach in a kitchen  "


class _FakeModel:
    def generate(self, **kw):
        return [[0, 1, 2]]


def test_available():
    print("\n[1] available() não baixa modelo")
    captioner._model = None
    captioner._load_failed = False
    captioner._DISABLED = False
    check("available() retorna bool (deps presentes)", isinstance(captioner.available(), bool))
    captioner._DISABLED = True
    check("CAPTION_DISABLED desliga", captioner.available() is False)
    captioner._DISABLED = False
    captioner._load_failed = True
    check("falha de load não re-tenta (available False)", captioner.available() is False)
    captioner._load_failed = False


def test_caption_image_wiring():
    print("\n[2] caption_image — wiring com modelo mockado")
    captioner._model = _FakeModel()
    captioner._proc = _FakeProc()
    captioner._DEV = "cpu"
    captioner._load_failed = False
    out = captioner.caption_image(None)
    check("decodifica e faz strip da legenda",
          out == "an elderly woman holding her stomach in a kitchen", extra=repr(out))
    captioner._model = None
    captioner._proc = None


def test_caption_path_no_frame():
    print("\n[3] caption_path — sem frame → '' (não trava)")
    captioner._model = None
    captioner._load_failed = False
    captioner._DISABLED = False
    orig = broll_index._extract_frame_ffmpeg
    broll_index._extract_frame_ffmpeg = lambda *a, **k: None
    try:
        check("frame ausente → string vazia", captioner.caption_path("/x/y.mp4") == "")
    finally:
        broll_index._extract_frame_ffmpeg = orig


def test_tag_doc_uses_caption():
    print("\n[4] _tag_doc prioriza a legenda local")
    d = bs._tag_doc({"caption": "elderly woman holding her stomach",
                     "keywords": ["pain", "stomach"], "visual_type": ["emotional"]})
    check("legenda entra no doc", "elderly woman holding her stomach" in d, extra=d)
    check("keywords também entram", "pain" in d)
    check("legenda vem na frente", d.strip().startswith("elderly woman"), extra=d)
    # sem legenda → cai nas keywords (comportamento antigo preservado)
    d2 = bs._tag_doc({"keywords": ["doctor", "clinic"]})
    check("sem caption usa keywords", "doctor" in d2 and "clinic" in d2)
    # sem nada → cai no nome do arquivo limpo
    d3 = bs._tag_doc({}, fallback_name="doctor-talking-to-patient.mp4")
    check("sem tags usa nome do arquivo", "doctor" in d3.lower(), extra=d3)


def test_caption_folder():
    print("\n[5] caption_folder — passada local, cria sidecar e pula já-legendados")
    d = tempfile.mkdtemp()
    for n in ("a.mp4", "b.mov"):
        open(os.path.join(d, n), "wb").close()
    orig_av, orig_cp = at.captioner.available, at.captioner.caption_path
    at.captioner.available = lambda: True
    at.captioner.caption_path = lambda p, t=1.0: "a person standing in a kitchen"
    try:
        c = at.caption_folder(d)
        check("legendou os 2 clipes", c["captioned"] == 2 and c["total"] == 2, extra=str(c))
        tg = at.load_tags(os.path.join(d, "a.mp4"))
        check("sidecar gravado com caption", bool(tg) and tg.get("caption") == "a person standing in a kitchen")
        check("método = caption_only", tg.get("tagging_method") == "caption_only")
        c2 = at.caption_folder(d)
        check("2ª passada pula os já legendados", c2["skipped"] == 2 and c2["captioned"] == 0, extra=str(c2))
    finally:
        at.captioner.available, at.captioner.caption_path = orig_av, orig_cp
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_available()
    test_caption_image_wiring()
    test_caption_path_no_frame()
    test_tag_doc_uses_caption()
    test_caption_folder()
    print("\n" + ("✅ TODOS PASSARAM" if not _fails else f"❌ FALHARAM: {_fails}"))
    raise SystemExit(1 if _fails else 0)
