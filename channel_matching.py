"""
Links ingested IPTV channels to the games already synced from ESPN, so
the dashboard can offer a direct "watch here" stream per game.

Two matching strategies, tried in order:
  1. National network identity: both the game's ESPN-reported broadcast
     name ("ESPN2") and the IPTV channel's name are resolved through
     NetworkNormalizer to the same canonical key before comparing --
     exact identity, not substring containment. This is what stops an
     ESPN2 broadcast from silently matching a plain ESPN channel (and
     vice versa), which a naive `"espn" in "espn2"` check would allow.
  2. Regional/team-owned network match: channels like "BravesVision" or
     "YES Network" don't appear in ESPN's broadcast list at all -- they're
     effectively team-exclusive. RSN_TEAM_MAP (config.py) says which
     teams' games they carry; matching here is on the channel's cleaned
     name against that table's keys, which are already exact strings.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import RSN_TEAM_MAP, SPORTS_CHANNEL_KEYWORDS
from app.iptv_client import ChannelEntry
from app.models import Broadcast, Channel, Game
from app.network_normalizer import NetworkNormalizer, strip_iptv_noise


def is_sports_channel(name: str) -> bool:
    cleaned = strip_iptv_noise(name)
    return any(keyword in cleaned for keyword in SPORTS_CHANNEL_KEYWORDS)


def upsert_channels(session: Session, source_id: int, entries: list[ChannelEntry]) -> list[Channel]:
    """Persist only the subset of a (potentially huge) playlist that looks sports-relevant."""
    normalizer = NetworkNormalizer()
    saved: list[Channel] = []
    for entry in entries:
        if not is_sports_channel(entry.name):
            continue
        channel = (
            session.query(Channel)
            .filter_by(iptv_source_id=source_id, source_channel_id=entry.source_id)
            .one_or_none()
        )
        if channel is None:
            channel = Channel(iptv_source_id=source_id, source_channel_id=entry.source_id)
            session.add(channel)

        network_key, _ = normalizer.resolve(entry.name)
        channel.name = entry.name
        channel.cleaned_name = strip_iptv_noise(entry.name)
        channel.network_key = network_key
        channel.logo_url = entry.logo_url
        channel.group_title = entry.group_title
        channel.stream_url = entry.stream_url
        saved.append(channel)
    session.commit()
    return saved


def match_channels_to_games(session: Session) -> int:
    """
    Walks every unresolved broadcast + every RSN-mapped game and attaches
    a stream_url where a sports channel matches. Returns match count.
    Safe to re-run: only fills gaps, never overwrites an existing match.
    """
    normalizer = NetworkNormalizer()
    channels = session.query(Channel).all()
    channels_by_network_key: dict[str, list[Channel]] = {}
    for c in channels:
        if c.network_key:
            channels_by_network_key.setdefault(c.network_key, []).append(c)

    matches = 0

    # Strategy 1: exact national-network identity match.
    broadcasts = session.query(Broadcast).filter(Broadcast.stream_url.is_(None)).all()
    for broadcast in broadcasts:
        key, _ = normalizer.resolve(broadcast.network)
        if not key:
            continue
        candidates = channels_by_network_key.get(key)
        if candidates:
            hit = candidates[0]
            broadcast.stream_url = hit.stream_url
            broadcast.channel_logo_url = hit.logo_url
            matches += 1

    # Strategy 2: RSN / team-owned network -> team mapping, for games with
    # no broadcast match yet (these channels resolve to network_key=None
    # since they're not in NETWORK_ALIASES, so Strategy 1 can't see them).
    games = session.query(Game).all()
    for game in games:
        if any(b.stream_url for b in game.broadcasts):
            continue
        for channel in channels:
            teams_for_rsn = RSN_TEAM_MAP.get(channel.cleaned_name)
            if not teams_for_rsn:
                continue
            if game.home_team.display_name in teams_for_rsn or game.away_team.display_name in teams_for_rsn:
                game.broadcasts.append(
                    Broadcast(
                        network=channel.name,
                        region="regional",
                        stream_url=channel.stream_url,
                        channel_logo_url=channel.logo_url,
                    )
                )
                matches += 1
                break

    session.commit()
    return matches
