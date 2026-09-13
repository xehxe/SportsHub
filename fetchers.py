"""
Data fetching layer.

Talks to ESPN's public "site API" scoreboard endpoints, respects a
per-host rate limit, retries transient failures with exponential
backoff + jitter, and normalizes+persists the result via SQLAlchemy.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import THROTTLE, LeagueSource
from app.models import Broadcast, Game, Team
from app.normalization import TeamNormalizer, normalize_status, to_utc_iso

logger = logging.getLogger("sportshub.fetchers")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class UpstreamError(Exception):
    """Raised for HTTP responses that are worth retrying."""


class Throttler:
    """
    Simple async gatekeeper: caps global concurrency and enforces a
    minimum spacing between requests to the same host, so a burst of
    league syncs doesn't hammer ESPN.
    """

    def __init__(self, min_interval: float, max_concurrency: int) -> None:
        self._min_interval = min_interval
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._last_request_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait_turn(self, host: str) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            last = self._last_request_at.get(host, 0.0)
            elapsed = time.monotonic() - last
            delay = max(0.0, self._min_interval - elapsed)
        if delay:
            await asyncio.sleep(delay)
        async with self._lock:
            self._last_request_at[host] = time.monotonic()

    def release(self) -> None:
        self._semaphore.release()


throttler = Throttler(THROTTLE.min_interval_seconds, THROTTLE.max_concurrency)


@retry(
    reraise=True,
    stop=stop_after_attempt(THROTTLE.max_retries),
    wait=wait_exponential_jitter(
        initial=THROTTLE.backoff_base_seconds, max=THROTTLE.backoff_max_seconds
    ),
    retry=retry_if_exception_type(UpstreamError),
)
async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    host = httpx.URL(url).host
    await throttler.wait_turn(host)
    try:
        response = await client.get(url, timeout=THROTTLE.request_timeout_seconds)
    except httpx.TransportError as exc:
        raise UpstreamError(f"transport error calling {url}: {exc}") from exc
    finally:
        throttler.release()

    if response.status_code in _RETRYABLE_STATUS:
        raise UpstreamError(f"{response.status_code} from {url}")
    response.raise_for_status()
    return response.json()


def _team_logo(team_json: dict) -> str:
    logos = team_json.get("logos") or []
    if logos:
        # ESPN lists multiple resolutions; take the first (typically the
        # high-res default) rather than guessing dimensions.
        return logos[0].get("href", "")
    return team_json.get("logo", "")


def _upsert_team(session: Session, normalizer: TeamNormalizer, league_key: str, team_json: dict) -> Team:
    full_name = team_json.get("displayName", "")
    short_name = team_json.get("shortDisplayName", "")
    abbreviation = team_json.get("abbreviation", "")
    slug, canonical_name = normalizer.resolve(full_name or short_name or abbreviation)

    team = session.query(Team).filter_by(slug=slug, league_key=league_key).one_or_none()
    if team is None:
        team = Team(slug=slug, league_key=league_key, display_name=canonical_name)
        session.add(team)

    team.display_name = canonical_name
    team.abbreviation = abbreviation
    team.logo_url = _team_logo(team_json) or team.logo_url
    team.primary_color = team_json.get("color", team.primary_color)
    return team


def _upsert_game(session: Session, league_key: str, normalizer: TeamNormalizer, event: dict) -> Game | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    competitors = competition.get("competitors") or []

    home_json = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away_json = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home_json or not away_json:
        return None

    home_team = _upsert_team(session, normalizer, league_key, home_json.get("team", {}))
    away_team = _upsert_team(session, normalizer, league_key, away_json.get("team", {}))
    session.flush()  # populate team ids for the FK below

    source_event_id = str(event.get("id"))
    game = (
        session.query(Game)
        .filter_by(league_key=league_key, source_event_id=source_event_id)
        .one_or_none()
    )
    if game is None:
        game = Game(league_key=league_key, source_event_id=source_event_id)
        session.add(game)

    status_json = event.get("status", {}).get("type", {})
    game.home_team = home_team
    game.away_team = away_team
    game.home_score = _safe_int(home_json.get("score"))
    game.away_score = _safe_int(away_json.get("score"))
    game.kickoff_utc = datetime.fromisoformat(to_utc_iso(event.get("date")))
    game.status = normalize_status(status_json.get("state", ""), status_json.get("description", ""))
    game.status_detail = status_json.get("shortDetail", "")
    game.venue = (competition.get("venue") or {}).get("fullName", "")
    game.last_synced_at = datetime.now(timezone.utc)

    game.broadcasts.clear()
    for broadcast in competition.get("broadcasts", []):
        for name in broadcast.get("names", []):
            game.broadcasts.append(Broadcast(network=name))

    return game


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def sync_league(client: httpx.AsyncClient, session: Session, league: LeagueSource, normalizer: TeamNormalizer) -> int:
    """Fetch one league's scoreboard and upsert it. Returns count of games synced."""
    try:
        payload = await _get_json(client, league.scoreboard_url)
    except (UpstreamError, httpx.HTTPStatusError) as exc:
        logger.warning("Giving up on %s after retries: %s", league.key, exc)
        return 0

    events = payload.get("events", [])
    synced = 0
    for event in events:
        game = _upsert_game(session, league.key, normalizer, event)
        if game is not None:
            synced += 1
    session.commit()
    return synced


async def sync_all_leagues(session: Session, leagues: list[LeagueSource]) -> dict[str, int]:
    normalizer = TeamNormalizer()
    results: dict[str, int] = {}
    async with httpx.AsyncClient(headers={"User-Agent": "sportshub/1.0"}) as client:
        tasks = [sync_league(client, session, league, normalizer) for league in leagues]
        counts = await asyncio.gather(*tasks, return_exceptions=False)
    for league, count in zip(leagues, counts):
        results[league.key] = count
    return results
