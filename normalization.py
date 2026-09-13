"""
Normalization layer.

Public sports feeds are inconsistent: the same team shows up as a full
name ("Kansas City Chiefs"), a short name ("Chiefs"), or an abbreviation
("KC") depending on endpoint and sport. Kickoff times arrive as ISO-8601
strings in varying precision. This module gives the rest of the app one
canonical shape for both.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

from app.config import TEAM_ALIASES

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[.\-']")


def _clean(text: str) -> str:
    text = text.strip().lower()
    text = _PUNCT_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text


class TeamNormalizer:
    """
    Resolves any spelling/abbreviation of a team to one canonical display
    name, keyed by a stable slug. Canonical names are learned incrementally
    as full team records are ingested (ESPN's /teams endpoint gives us the
    authoritative full name + abbreviation + short name up front), then
    fuzzy-matched against for later, noisier references (e.g. scoreboard
    payloads that only include a short name).
    """

    def __init__(self) -> None:
        # slug -> canonical display name
        self._canonical: dict[str, str] = {}
        # cleaned alias text -> slug
        self._alias_index: dict[str, str] = {}
        for alias, canonical in TEAM_ALIASES.items():
            slug = self._slug(canonical)
            self._canonical[slug] = canonical
            self._alias_index[_clean(alias)] = slug
            self._alias_index[_clean(canonical)] = slug

    @staticmethod
    def _slug(name: str) -> str:
        return _clean(name).replace(" ", "-")

    def register(self, full_name: str, short_name: str = "", abbreviation: str = "") -> str:
        """Register a team's known name variants. Returns its slug."""
        slug = self._slug(full_name)
        self._canonical[slug] = full_name
        for variant in (full_name, short_name, abbreviation):
            if variant:
                self._alias_index[_clean(variant)] = slug
        return slug

    def resolve(self, name: str) -> tuple[str, str]:
        """
        Resolve any known variant to (slug, canonical_display_name).
        Falls back to fuzzy matching against known aliases, then to the
        input itself if nothing is close enough to trust.
        """
        cleaned = _clean(name)
        if cleaned in self._alias_index:
            slug = self._alias_index[cleaned]
            return slug, self._canonical[slug]

        candidates = difflib.get_close_matches(
            cleaned, self._alias_index.keys(), n=1, cutoff=0.82
        )
        if candidates:
            slug = self._alias_index[candidates[0]]
            return slug, self._canonical[slug]

        # Unknown team: register it as its own canonical entry so it's at
        # least stable and queryable, rather than silently dropped.
        slug = self.register(name)
        return slug, name


def to_utc_iso(raw: str | datetime) -> str:
    """
    Normalize any incoming timestamp (ESPN gives ISO-8601 with a trailing
    'Z', but we defend against offset-naive or locale-formatted strings
    from other sources) into a UTC ISO-8601 string with an explicit
    offset, e.g. '2026-09-12T18:00:00+00:00'.
    """
    if isinstance(raw, datetime):
        dt = raw
    else:
        dt = dateparser.parse(raw)

    if dt.tzinfo is None:
        # Assume naive timestamps from a feed are already UTC; this is the
        # documented ESPN behavior, but is called out explicitly here
        # since it's a silent assumption.
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).isoformat()


def normalize_status(raw_state: str, raw_detail: str = "") -> str:
    """
    Collapse the many status vocabularies used across ESPN sports
    ('STATUS_SCHEDULED', 'pre', 'in', 'STATUS_FINAL', 'post', ...)
    into one small enum: 'scheduled' | 'live' | 'final' | 'postponed'.
    """
    state = raw_state.lower()
    detail = raw_detail.lower()

    if "postpon" in detail or "cancel" in detail:
        return "postponed"
    if state in {"pre", "status_scheduled"}:
        return "scheduled"
    if state in {"in", "status_in_progress"}:
        return "live"
    if state in {"post", "status_final"}:
        return "final"
    return "scheduled"
