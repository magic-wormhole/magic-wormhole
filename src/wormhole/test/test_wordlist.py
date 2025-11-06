from unittest import mock

from .._wordlist import PGPWordList

def test_completions():
    wl = PGPWordList()
    gc = wl.get_completions
    assert gc("ar", 2) == {"armistice-", "article-"}
    assert gc("armis", 2) == {"armistice-"}
    assert gc("armistice", 2) == {"armistice-"}
    lots = gc("armistice-", 2)
    assert len(lots) == 256
    first = list(lots)[0]
    assert first.startswith("armistice-")
    assert gc("armistice-ba", 2) == { "armistice-baboon", "armistice-backfield",
            "armistice-backward", "armistice-banjo" }
    assert gc("armistice-ba", 3) == { "armistice-baboon-", "armistice-backfield-",
        "armistice-backward-", "armistice-banjo-" }
    assert gc("armistice-baboon", 2) == {"armistice-baboon"}
    assert gc("armistice-baboon", 3) == {"armistice-baboon-"}
    assert gc("armistice-baboon", 4) == {"armistice-baboon-"}


def test_choose_words():
    wl = PGPWordList()
    with mock.patch("os.urandom", side_effect=[b"\x04", b"\x10"]):
        assert wl.choose_words(2) == "alkali-assume"
