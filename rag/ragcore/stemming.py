"""Romanian word-family matching (Snowball stemming, not lemmatization).

Stems are compared token-by-token, not by string prefix. This prevents
"suspendare" from accidentally matching unrelated longer words.
"""
import re
import unicodedata
from functools import lru_cache

WORD = re.compile(r"[^\W_]+", re.UNICODE)


def fold(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


@lru_cache(maxsize=1)
def _stemmer():
    import snowballstemmer
    return snowballstemmer.stemmer("romanian")


@lru_cache(maxsize=50000)
def stem_word(word: str) -> str:
    return _stemmer().stemWord(word)


def stem_sequence(text: str) -> tuple[str, ...]:
    return tuple(stem_word(m.group(0)) for m in WORD.finditer(fold(text)))


def count_phrases(text: str, phrases: list[tuple[str, ...]]) -> list[int]:
    words = stem_sequence(text)
    counts = []
    for phrase in phrases:
        length = len(phrase)
        counts.append(sum(
            words[i:i + length] == phrase
            for i in range(len(words) - length + 1)
        ))
    return counts
