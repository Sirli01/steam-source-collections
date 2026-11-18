"""Align license package names to Steam appids.

The license page has no appid, and package names differ from game names
(bundles, "Retail" suffixes, renamed titles, DLC, demos...). Everything that
cannot be matched is reported instead of being guessed into a collection.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from .owned import Game

PARENS_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]|【[^】]*】")
NOISE_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
SUFFIX_RE = re.compile(
    r"\b(demo|playtest|beta|soundtrack|ost|dlc|dedicated server|server|"
    r"trial|free weekend|friend'?s pass|test app|原声|试玩|免费周末|好友通行证|测试应用)\b",
    re.I,
)
BUNDLE_RE = re.compile(r"bundle|捆绑|合集|package|pack|礼包|包", re.I)

# Steam prints the *package* name on the license page, not the game name.
# Retail / store bundles add edition and region words that the library never
# has ("Atomic Heart - Standard Edition (CN)" vs "Atomic Heart"). Stripping
# them is only safe when the removed part consists exclusively of such words;
# "Counter-Strike: Global Offensive" must never be shortened to "Counter-Strike".
VERSION_WORDS = (
    r"standard|deluxe|ultimate|collector'?s|complete|digital|retail|premium|starter|"
    r"game of the year|goty|edition|pre-?purchase|launch|limited free promotional"
)
REGION_WORDS = r"cn|china|cthm|row|ww|asia|prc|global|japan|jp|us|uk"
MONTH_YEAR_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}"
PROMO_SUFFIX_RE = re.compile(
    r"\b(?:limited\s+free\s+promotional(?:\s+package)?|free\s+weekend)"
    r"(?:[\s\-–—:：()\[\]（）]*" + MONTH_YEAR_RE + r"[\s()\[\]（）]*)?",
    re.I,
)
DATE_FRAGMENT_RE = re.compile(
    r"^(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)?\d{2,4}$",
    re.I,
)
# Region words are ONLY allowed when judging a trailing segment. Removing them
# from an arbitrary title would turn "Global Offensive" into a different game.
EDITION_WORD_RE = re.compile(r"\b(?:" + VERSION_WORDS + r")\b", re.I)
EDITION_PART_RE = re.compile(
    r"^[\s\[\]()（）]*(?:(?:" + VERSION_WORDS + r"|" + REGION_WORDS + r"|"
    r"pack|bundle|demo|playtest|free weekend|soundtrack|ost|dlc|base game|"
    r"promotional|package|" + MONTH_YEAR_RE + r")"
    r"\b[\s\-–—:：()\[\]（）]*)+$",
    re.I,
)
ALIAS_SPLIT_RE = re.compile(r"\s*(?:[-–—:：/|])\s*")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
VR_TOKEN_RE = re.compile(r"\bvr\b", re.I)


def strip_promo_suffix(package_name: str) -> str:
    """Strip Steam's dated promotional / free-weekend tail from a package name."""
    text = unicodedata.normalize("NFKC", re.sub(r"[™®©]", "", package_name or ""))
    cleaned = re.sub(r"\s+", " ", PROMO_SUFFIX_RE.sub(" ", text)).strip(" -–—:：/|")
    return cleaned or text.strip()


def _collect_alias_variants(text: str, out: list[tuple[str, str]]) -> None:
    parts = [p.strip() for p in ALIAS_SPLIT_RE.split(text) if p.strip()]
    if len(parts) >= 2:
        if all(EDITION_PART_RE.match(p) for p in parts[1:]):
            if len(normalize(parts[0])) >= 3:
                out.append((parts[0], "normalized"))
        if "/" in text:
            for part in parts:
                if CJK_RE.search(part):
                    continue
                if len(normalize(part)) >= 4:
                    out.append((part, "alias"))
    stripped = EDITION_WORD_RE.sub(" ", PARENS_RE.sub(" ", text))
    stripped = re.sub(r"\s+", " ", stripped).strip(" -–—:：/|")
    if stripped and normalize(stripped) != normalize(text) and len(normalize(stripped)) >= 3:
        out.append((stripped, "normalized"))


def alias_candidates(package_name: str) -> list[tuple[str, str]]:
    """Conservative rewrites of a license package name into a game name.

    Only two shapes are produced: removing a pure edition/region/promotional tail,
    and taking the non-Chinese half of a bilingual name. Everything else keeps
    going through the original substring/fuzzy rules instead of being guessed.
    """
    text = unicodedata.normalize("NFKC", re.sub(r"[™®©]", "", package_name or ""))
    cleaned = strip_promo_suffix(text)
    out: list[tuple[str, str]] = []
    if cleaned and normalize(cleaned) != normalize(text) and len(normalize(cleaned)) >= 3:
        out.append((cleaned, "normalized"))
    _collect_alias_variants(cleaned, out)
    if cleaned != text:
        _collect_alias_variants(text, out)
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for label, via in out:
        key = normalize(label)
        if key and key not in seen:
            seen.add(key)
            result.append((label, via))
    return result


def normalize(value: str) -> str:
    """Aggressive but deterministic name normalization."""
    # Strip the symbols *before* NFKC: compatibility folding turns "™" into
    # the letters "tm" and "®" into "r", which otherwise makes
    # "Middle-earth™: Shadow of War™" != "Middle-earth: Shadow of War".
    text = re.sub(r"[™®©]", "", str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    text = PARENS_RE.sub(" ", text)
    text = SUFFIX_RE.sub(" ", text)
    text = NOISE_RE.sub(" ", text.casefold())
    text = re.sub(r"^the\s+", "", text.strip())
    return re.sub(r"\s+", "", text)


@dataclass
class MatchResult:
    games: list[Game] = field(default_factory=list)
    via: str = "none"  # exact | substring | fuzzy | bundle | none
    score: float = 0.0
    note: str = ""


def build_index(games: list[Game]) -> dict[str, list[Game]]:
    index: dict[str, list[Game]] = {}
    for game in games:
        index.setdefault(normalize(game.name), []).append(game)
    return index


def _substring_candidates(key: str, index: dict[str, list[Game]]) -> list[tuple[float, str]]:
    found: list[tuple[float, str]] = []
    for candidate in index:
        if len(key) < 8 or len(candidate) < 8:
            continue
        if key in candidate or candidate in key:
            overlap = min(len(key), len(candidate)) / max(len(key), len(candidate))
            if overlap >= 0.6:
                found.append((overlap, candidate))
    return sorted(found, reverse=True)


def match_package(package_name: str, index: dict[str, list[Game]], threshold: float = 0.86) -> MatchResult:
    key = normalize(package_name)
    if not key:
        return MatchResult(note="名称归一化后为空")

    if key in index:
        return MatchResult(games=list(index[key]), via="exact", score=1.0)

    for label, via in alias_candidates(package_name):
        candidate_key = normalize(label)
        if candidate_key and candidate_key in index:
            return MatchResult(
                games=list(index[candidate_key]),
                via=via,
                score=0.95,
                note=("剥离版本 / 地区后缀后精确匹配" if via == "normalized"
                      else "双语包名中的名称精确匹配"),
            )

    has_vr = bool(VR_TOKEN_RE.search(package_name))
    candidates = {
        candidate: games
        for candidate, games in index.items()
        if not has_vr or any(VR_TOKEN_RE.search(game.name) for game in games)
    }

    substrings = _substring_candidates(key, candidates)
    if substrings and substrings[0][0] >= 0.85:
        score, candidate = substrings[0]
        return MatchResult(games=list(index[candidate]), via="substring", score=round(score, 3))

    close = difflib.get_close_matches(key, list(candidates), n=1, cutoff=threshold)
    if close:
        candidate = close[0]
        score = difflib.SequenceMatcher(None, key, candidate).ratio()
        return MatchResult(games=list(index[candidate]), via="fuzzy", score=round(score, 3))

    # Bundles often contain several separately-owned games. Only accept
    # unambiguous fragment matches and flag the row for review.
    bundle_text = strip_promo_suffix(package_name)
    if BUNDLE_RE.search(bundle_text):
        fragments = [part.strip() for part in re.split(r"[+&,/·:：\-]", bundle_text) if len(part.strip()) >= 4]
        hits: list[Game] = []
        for fragment in fragments:
            if EDITION_PART_RE.match(fragment):
                continue
            fkey = normalize(fragment)
            if not fkey or fkey.isdigit() or DATE_FRAGMENT_RE.match(fkey):
                continue
            for candidate, games in index.items():
                if len(fkey) >= 5 and (fkey == candidate or (len(candidate) >= 8 and fkey in candidate)):
                    hits.extend(games)
        unique = {game.appid: game for game in hits}
        if unique:
            return MatchResult(
                games=list(unique.values()),
                via="bundle",
                score=0.5,
                note="疑似捆绑包，建议人工核对",
            )

    return MatchResult(note="未找到可靠匹配")
