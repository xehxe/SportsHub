"""
Network name normalization.

The previous version of channel_matching.py compared cleaned channel/
broadcast names with plain substring containment ("espn" in "espn2" is
True), which meant an ESPN2 broadcast could silently get matched to a
plain ESPN channel and vice versa. This module gives networks the same
treatment normalization.py already gives teams: every known channel
identity has a canonical key, and every spelling/suffix variant a real
IPTV provider or ESPN uses resolves to exactly that key — not a
substring neighbor.
"""
from __future__ import annotations

import difflib
import re

_BRACKETED_RE = re.compile(r"[\[(][^\])]*[\])]")           # "[4K]", "(East)"
_COUNTRY_PREFIX_RE = re.compile(r"^\s*(usa?|us|uk|ca)\s*[:\-|]\s*", re.IGNORECASE)
_QUALITY_TAG_RE = re.compile(
    r"\b(hd|fhd|uhd|4k|sd|hevc|h265|raw|backup|alt|east|west|feed\s*\d*)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[.\-_/|]")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_iptv_noise(name: str) -> str:
    """Strip provider prefixes, quality tags, and region suffixes an IPTV
    list layers on top of a channel's actual name — but leave digits that
    are part of a channel's identity (ESPN2, FS1, NBA TV) untouched."""
    name = _COUNTRY_PREFIX_RE.sub("", name)
    name = _BRACKETED_RE.sub("", name)
    name = _QUALITY_TAG_RE.sub("", name)
    name = _PUNCT_RE.sub(" ", name)
    name = _WHITESPACE_RE.sub(" ", name).strip().lower()
    return name


# Canonical key -> every variant spelling seen in the wild (ESPN listings,
# Xtream category dumps, M3U tvg-name fields). Keep ESPN's sub-brands as
# distinct canonical keys — they are different channels with different
# schedules, not noise on top of "ESPN".
NETWORK_ALIASES: dict[str, list[str]] = {
    "espn": ["espn", "espn usa", "espn national", "espn hd"],
    "espn2": ["espn2", "espn 2"],
    "espnu": ["espnu", "espn u"],
    "espnews": ["espnews", "espn news"],
    "espn deportes": ["espn deportes"],
    "espn+": ["espn+", "espn plus"],
    "sec network": ["sec network", "secn"],
    "acc network": ["acc network", "accn"],
    "big ten network": ["big ten network", "btn"],
    "pac-12 network": ["pac 12 network", "pac12 network"],
    "fox": ["fox", "fox broadcasting"],
    "fs1": ["fs1", "fox sports 1"],
    "fs2": ["fs2", "fox sports 2"],
    "nbc": ["nbc"],
    "abc": ["abc"],
    "cbs": ["cbs"],
    "cbs sports network": ["cbs sports network", "cbssn"],
    "tnt": ["tnt"],
    "tbs": ["tbs"],
    "trutv": ["trutv", "tru tv"],
    "usa network": ["usa network", "usa"],
    "nfl network": ["nfl network"],
    "nfl redzone": ["nfl redzone", "nfl red zone"],
    "nba tv": ["nba tv", "nbatv"],
    "mlb network": ["mlb network", "mlbn"],
    "nhl network": ["nhl network"],
    "golf channel": ["golf channel"],
    "tennis channel": ["tennis channel"],
    "peacock": ["peacock"],
    "paramount+": ["paramount+", "paramount plus"],
    "apple tv": ["apple tv", "apple tv+"],
    "amazon prime video": ["amazon prime video", "prime video"],
}


class NetworkNormalizer:
    """
    Resolves a raw channel/broadcast name to (canonical_key, display_label).
    Exact alias match wins; a strict fuzzy match is the fallback for minor
    typos or unseen suffixes, never for genuinely different channels
    (ESPN vs ESPN2 have a similarity ratio too low for the default cutoff
    to conflate them, unlike plain substring checks).
    """

    # Explicit display labels for keys where automatic title-casing would
    # look wrong (e.g. "nba tv".title() -> "Nba Tv" instead of "NBA TV").
    _DISPLAY_OVERRIDES: dict[str, str] = {
        "espnu": "ESPNU", "espnews": "ESPN News", "espn deportes": "ESPN Deportes",
        "espn+": "ESPN+", "sec network": "SEC Network", "acc network": "ACC Network",
        "big ten network": "Big Ten Network", "pac-12 network": "Pac-12 Network",
        "cbs sports network": "CBS Sports Network", "trutv": "truTV",
        "usa network": "USA Network", "nfl redzone": "NFL RedZone",
        "nba tv": "NBA TV", "mlb network": "MLB Network", "nhl network": "NHL Network",
        "nfl network": "NFL Network", "paramount+": "Paramount+",
        "amazon prime video": "Prime Video",
    }

    def __init__(self) -> None:
        self._alias_to_key: dict[str, str] = {}
        self._key_to_label: dict[str, str] = {}
        for key, variants in NETWORK_ALIASES.items():
            self._key_to_label[key] = self._DISPLAY_OVERRIDES.get(
                key, key.upper() if len(key) <= 5 else key.title()
            )
            for variant in variants:
                self._alias_to_key[variant] = key
            self._alias_to_key[key] = key

    def resolve(self, raw_name: str) -> tuple[str | None, str]:
        """Returns (canonical_key or None if unrecognized, cleaned display name)."""
        cleaned = strip_iptv_noise(raw_name)
        if not cleaned:
            return None, raw_name

        if cleaned in self._alias_to_key:
            key = self._alias_to_key[cleaned]
            return key, self._key_to_label[key]

        close = difflib.get_close_matches(cleaned, self._alias_to_key.keys(), n=1, cutoff=0.9)
        if close:
            key = self._alias_to_key[close[0]]
            return key, self._key_to_label[key]

        # Not a recognized national network — fine, it's likely a regional
        # sports network handled separately by RSN_TEAM_MAP, or a channel
        # we simply don't have listings logic for yet.
        return None, cleaned
