"""
Generates a broadcast-style "matchup card": two team colors divided by a
diagonal, each half carrying that team's logo — the same shape ESPN/Fox
use for pre-game bumpers. Rendered once per game and cached to disk;
regenerated only if the game's teams or scores change enough to matter
(in practice: never, since a matchup's teams/colors are static — only
the dashboard's live score overlay needs to refresh, and that's drawn
in HTML, not baked into this image).
"""
from __future__ import annotations

import io
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

CACHE_DIR = Path("app/static/matchups")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT = 1280, 720
SLANT = 70          # horizontal pixels the diagonal leans, top vs bottom
DEFAULT_COLOR = "2A333B"  # matches the app's own hairline gray as a neutral fallback


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = (hex_color or "").lstrip("#") or DEFAULT_COLOR
    if len(hex_color) != 6:
        hex_color = DEFAULT_COLOR
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


async def _fetch_logo(client: httpx.AsyncClient, url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        resp = await client.get(url, timeout=10.0)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except (httpx.HTTPError, OSError):
        return None


def _paste_centered(base: Image.Image, logo: Image.Image, center_x: int, center_y: int, max_w: int, max_h: int) -> None:
    ratio = min(max_w / logo.width, max_h / logo.height, 1.5)
    resized = logo.resize((max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio))))
    top_left = (center_x - resized.width // 2, center_y - resized.height // 2)
    base.alpha_composite(resized, dest=top_left)


async def generate_matchup_image(
    game_id: int,
    away_name: str,
    home_name: str,
    away_color: str,
    home_color: str,
    away_logo_url: str,
    home_logo_url: str,
) -> Path:
    """Builds (or reuses) a cached PNG for one game and returns its path."""
    out_path = CACHE_DIR / f"{game_id}.png"
    if out_path.exists():
        return out_path

    base = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))
    draw = ImageDraw.Draw(base)
    mid = WIDTH // 2

    left_color = _hex_to_rgb(away_color)
    right_color = _hex_to_rgb(home_color)

    draw.polygon(
        [(0, 0), (mid + SLANT, 0), (mid - SLANT, HEIGHT), (0, HEIGHT)],
        fill=(*left_color, 255),
    )
    draw.polygon(
        [(mid + SLANT, 0), (WIDTH, 0), (WIDTH, HEIGHT), (mid - SLANT, HEIGHT)],
        fill=(*right_color, 255),
    )

    async with httpx.AsyncClient(headers={"User-Agent": "sportshub/1.0"}) as client:
        away_logo, home_logo = await _fetch_logo(client, away_logo_url), await _fetch_logo(client, home_logo_url)

    box_w, box_h = int(WIDTH * 0.32), int(HEIGHT * 0.55)
    if away_logo:
        _paste_centered(base, away_logo, mid // 2, HEIGHT // 2, box_w, box_h)
    if home_logo:
        _paste_centered(base, home_logo, mid + mid // 2, HEIGHT // 2, box_w, box_h)

    base.convert("RGB").save(out_path, "PNG")
    return out_path


def invalidate(game_id: int) -> None:
    """Call if a game's teams are ever corrected and the cached art is stale."""
    path = CACHE_DIR / f"{game_id}.png"
    if path.exists():
        path.unlink()
