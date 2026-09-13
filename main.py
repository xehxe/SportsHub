from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.channel_matching import match_channels_to_games, upsert_channels
from app.config import LEAGUES, LEAGUES_BY_KEY
from app.database import SessionLocal, init_db
from app.fetchers import sync_all_leagues
from app.iptv_client import XtreamClient, fetch_m3u
from app.matchup_image import generate_matchup_image
from app.models import Game, IPTVSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sportshub")

REFRESH_INTERVAL_SECONDS = 90
IPTV_REFRESH_EVERY_N_CYCLES = 20  # IPTV playlists change far less often than scores
_last_refresh: datetime | None = None
_cycle_count = 0


async def sync_iptv_source(session, source: IPTVSource) -> int:
    """Pull one IPTV source's channel list and fold in anything sports-relevant."""
    async with httpx.AsyncClient(headers={"User-Agent": "sportshub/1.0"}) as client:
        if source.kind == "xtream":
            xtream = XtreamClient(source.base_url, source.username, source.password)
            entries = await xtream.get_live_streams(client)
        else:
            entries = await fetch_m3u(client, source.m3u_url)

    saved = upsert_channels(session, source.id, entries)
    source.last_synced_at = datetime.now(timezone.utc)
    source.channel_count = len(saved)
    session.commit()
    return len(saved)


async def _refresh_loop() -> None:
    """Background task: keeps the local DB warm without a request in flight."""
    global _last_refresh, _cycle_count
    while True:
        session = SessionLocal()
        try:
            counts = await sync_all_leagues(session, LEAGUES)
            _last_refresh = datetime.now(timezone.utc)
            logger.info("Synced leagues: %s", counts)

            if _cycle_count % IPTV_REFRESH_EVERY_N_CYCLES == 0:
                for source in session.query(IPTVSource).all():
                    try:
                        n = await sync_iptv_source(session, source)
                        logger.info("Synced IPTV source %s: %s channels", source.label, n)
                    except Exception:
                        logger.exception("IPTV sync failed for source %s", source.label)
                matched = match_channels_to_games(session)
                if matched:
                    logger.info("Matched %s broadcasts to live IPTV streams", matched)
            _cycle_count += 1
        except Exception:
            logger.exception("Background refresh failed; will retry next cycle")
        finally:
            session.close()
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="SportsHub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, league: str = "all"):
    session = SessionLocal()
    try:
        query = session.query(Game).order_by(Game.kickoff_utc.asc())
        if league != "all" and league in LEAGUES_BY_KEY:
            query = query.filter(Game.league_key == league)
        games = query.limit(80).all()
    finally:
        session.close()

    grouped: dict[str, list[Game]] = {}
    for game in games:
        day_key = game.kickoff_utc.strftime("%A, %B %-d")
        grouped.setdefault(day_key, []).append(game)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "grouped_games": grouped,
            "leagues": LEAGUES,
            "leagues_by_key": LEAGUES_BY_KEY,
            "active_league": league,
            "last_refresh": _last_refresh,
        },
    )


@app.post("/api/sync")
async def manual_sync():
    """Trigger an immediate sync outside the regular refresh cadence."""
    session = SessionLocal()
    try:
        counts = await sync_all_leagues(session, LEAGUES)
    finally:
        session.close()
    return {"synced": counts}


@app.get("/api/games")
def api_games(league: str = "all"):
    session = SessionLocal()
    try:
        query = session.query(Game).order_by(Game.kickoff_utc.asc())
        if league != "all" and league in LEAGUES_BY_KEY:
            query = query.filter(Game.league_key == league)
        games = query.limit(100).all()
        return [
            {
                "league": g.league_key,
                "home": g.home_team.display_name,
                "away": g.away_team.display_name,
                "home_score": g.home_score,
                "away_score": g.away_score,
                "kickoff_utc": g.kickoff_utc.isoformat(),
                "status": g.status,
                "status_detail": g.status_detail,
                "broadcasts": [
                    {"network": b.network, "stream_url": b.stream_url} for b in g.broadcasts
                ],
            }
            for g in games
        ]
    finally:
        session.close()


@app.get("/matchup/{game_id}.png")
async def matchup_image(game_id: int):
    """Serves (and lazily generates) the diagonal team-color/logo card for one game."""
    session = SessionLocal()
    try:
        game = session.get(Game, game_id)
        if game is None:
            return HTMLResponse("Game not found", status_code=404)
        path = await generate_matchup_image(
            game_id=game.id,
            away_name=game.away_team.display_name,
            home_name=game.home_team.display_name,
            away_color=game.away_team.primary_color,
            home_color=game.home_team.primary_color,
            away_logo_url=game.away_team.logo_url,
            home_logo_url=game.home_team.logo_url,
        )
    finally:
        session.close()
    return FileResponse(path, media_type="image/png")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    session = SessionLocal()
    try:
        sources = session.query(IPTVSource).all()
    finally:
        session.close()
    return templates.TemplateResponse("settings.html", {"request": request, "sources": sources})


@app.post("/settings/iptv")
async def add_iptv_source(
    kind: str = Form(...),
    label: str = Form("My IPTV"),
    base_url: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    m3u_url: str = Form(""),
):
    """
    Accepts either an Xtream Codes login (panel URL + username + password)
    or a plain M3U playlist URL — whichever the user has. Runs an initial
    sync immediately so channels show up without waiting for the next
    background cycle.
    """
    session = SessionLocal()
    try:
        source = IPTVSource(
            kind=kind, label=label, base_url=base_url.rstrip("/"),
            username=username, password=password, m3u_url=m3u_url,
        )
        session.add(source)
        session.commit()
        try:
            await sync_iptv_source(session, source)
            match_channels_to_games(session)
        except Exception:
            logger.exception("Initial IPTV sync failed for new source %s", label)
    finally:
        session.close()
    return RedirectResponse("/settings", status_code=303)
