from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import CoverageMetrics


TECHNICAL_TOKEN_RE = re.compile(
    r"(\\(?:[Pp][Nn]|[Ss][Hh]|[Ww][Uu]|[NnLlGgBbRr])"
    r"|\\[A-Za-z]+\[[^\]]*\]"
    r"|\\[.!|^><]"
    r"|\\[0-9]+"
    r"|<[^>]+>"
    r"|\{\d+\}"
    r"|%\d*\$?[sSdDiIfF])"
)
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿŒœ]+)?")

FRENCH_MARKERS = {
    "alors", "avec", "bonjour", "car", "ce", "ces", "cette", "dans", "de",
    "des", "donc", "du", "elle", "elles", "encore", "est", "et", "être", "ici",
    "il", "ils", "je", "la", "le", "les", "mais", "merci", "mon", "ne", "non",
    "nous", "on", "oui", "pas", "plus", "pour", "que", "qui", "sans", "sera",
    "sont", "sur", "ta", "te", "tes", "ton", "tu", "un", "une", "vos", "votre",
    "vous", "village", "suis", "rencontrer", "heureux", "bienvenue",
}
ENGLISH_MARKERS = {
    "a", "again", "and", "are", "but", "can", "come", "do", "for", "from", "good",
    "hello", "here", "i", "in", "is", "it", "meet", "my", "no", "not", "of", "on",
    "or", "please", "thanks", "that", "the", "their", "there", "this", "to", "village",
    "we", "welcome", "with", "you", "your", "friends", "happy",
}


@dataclass(frozen=True)
class LanguageClassification:
    category: str
    words: int
    characters: int


def strip_technical_tokens(text: str) -> str:
    return re.sub(r"\s+", " ", TECHNICAL_TOKEN_RE.sub(" ", text or "")).strip()


def classify_text(text: str) -> LanguageClassification:
    cleaned = strip_technical_tokens(text)
    words = [word.casefold() for word in WORD_RE.findall(cleaned)]
    character_count = sum(character.isalpha() for character in cleaned)
    if not words or character_count == 0:
        return LanguageClassification("technique_exclu", 0, 0)
    if len(words) <= 2 or character_count < 10:
        return LanguageClassification("ambigu", len(words), character_count)

    french_score = sum(word in FRENCH_MARKERS for word in words)
    english_score = sum(word in ENGLISH_MARKERS for word in words)
    if any(re.search(r"[àâçéèêëîïôùûüÿœ]", word) for word in words):
        french_score += 2

    if french_score >= 2 and english_score >= 2:
        category = "mixte"
    elif french_score >= 2 and french_score >= english_score * 2:
        category = "francais_probable"
    elif english_score >= 2 and english_score >= french_score * 2:
        category = "anglais_probable"
    else:
        category = "ambigu"
    return LanguageClassification(category, len(words), character_count)


def calculate_coverage(
    texts: Iterable[str],
    *,
    incomplete_sources: bool = False,
) -> CoverageMetrics:
    metrics = CoverageMetrics(incomplete_sources=incomplete_sources)
    for text in texts:
        if TECHNICAL_TOKEN_RE.search(text or ""):
            metrics.protected_command_lines += 1
        classification = classify_text(text)
        metrics.line_counts[classification.category] += 1
        metrics.word_counts[classification.category] += classification.words
        metrics.character_counts[classification.category] += classification.characters
    return metrics
