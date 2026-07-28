import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from troca_produto.pipeline.text_detect import Token  # noqa: E402


def words(*pairs, step=0.4, gap=0.0):
    """[(palavra, inicio)] ou só palavras → lista no formato do Whisper."""
    out = []
    cursor = 0.0
    for item in pairs:
        if isinstance(item, tuple):
            word, start = item
            cursor = start
        else:
            word = item
        out.append({"word": word, "start": round(cursor, 3), "end": round(cursor + step, 3)})
        cursor += step + gap
    return out


@pytest.fixture
def make_words():
    return words


@pytest.fixture
def tokens_factory():
    def build(text, step=0.4, gap=0.0, start=0.0):
        cursor = start
        out = []
        for word in text.split():
            out.append(Token(word=word, start=round(cursor, 3), end=round(cursor + step, 3)))
            cursor += step + gap
        return out

    return build
