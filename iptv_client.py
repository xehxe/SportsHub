"""
IPTV ingestion.

Supports the two common ways people get a live-TV channel list:
  1. Xtream Codes API — a username/password/base-URL panel that exposes
     JSON endpoints (`player_api.php`) for live categories/streams and
     short-term EPG per channel.
  2. Plain M3U / M3U8 playlists — a flat text file of #EXTINF entries,
     which is what most "M3U code" links resolve to.

Both are normalized into the same ChannelEntry shape so the rest of the
app (channel_matching.py) doesn't need to know which one is in use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.config import THROTTLE


@dataclass
class ChannelEntry:
    source_id: str          # stream_id (Xtream) or tvg-id/name (M3U)
    name: str
    logo_url: str
    group_title: str        # category, e.g. "USA - SPORTS"
    stream_url: str


class XtreamClient:
    """Thin wrapper over the Xtream Codes `player_api.php` panel API."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    def _api_url(self, action: str, **params: str) -> str:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = (
            f"{self.base_url}/player_api.php"
            f"?username={self.username}&password={self.password}&action={action}"
        )
        return f"{url}&{query}" if query else url

    def _stream_url(self, stream_id: str, ext: str = "ts") -> str:
        return f"{self.base_url}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    async def get_live_categories(self, client: httpx.AsyncClient) -> list[dict]:
        resp = await client.get(self._api_url("get_live_categories"), timeout=THROTTLE.request_timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    async def get_live_streams(self, client: httpx.AsyncClient, category_id: str | None = None) -> list[ChannelEntry]:
        url = self._api_url("get_live_streams", category_id=category_id) if category_id else self._api_url("get_live_streams")
        resp = await client.get(url, timeout=THROTTLE.request_timeout_seconds)
        resp.raise_for_status()
        raw = resp.json()

        entries = []
        for item in raw:
            stream_id = str(item.get("stream_id"))
            entries.append(
                ChannelEntry(
                    source_id=stream_id,
                    name=item.get("name", ""),
                    logo_url=item.get("stream_icon", ""),
                    group_title=item.get("category_name", ""),
                    stream_url=self._stream_url(stream_id),
                )
            )
        return entries

    async def get_short_epg(self, client: httpx.AsyncClient, stream_id: str, limit: int = 4) -> list[dict]:
        """Returns upcoming EPG listings the provider has for one channel, if any."""
        url = self._api_url("get_short_epg", stream_id=stream_id, limit=str(limit))
        resp = await client.get(url, timeout=THROTTLE.request_timeout_seconds)
        resp.raise_for_status()
        return resp.json().get("epg_listings", [])


_EXTINF_ATTR_RE = re.compile(r'(\S+?)="([^"]*)"')


def parse_m3u(text: str) -> list[ChannelEntry]:
    """
    Parse a standard IPTV M3U playlist:

        #EXTM3U
        #EXTINF:-1 tvg-id="ESPN.us" tvg-logo="http://.../espn.png" group-title="Sports",ESPN
        http://provider.example/live/user/pass/12345.ts

    Tolerant of missing attributes; a channel with no resolvable stream
    URL on the following non-comment line is skipped.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    entries: list[ChannelEntry] = []

    pending_attrs: dict[str, str] = {}
    pending_name = ""

    for line in lines:
        if line.startswith("#EXTINF"):
            pending_attrs = dict(_EXTINF_ATTR_RE.findall(line))
            # Display name is whatever follows the last comma on the EXTINF line.
            pending_name = line.rsplit(",", 1)[-1].strip()
        elif line.startswith("#"):
            continue  # other directives (#EXTVLCOPT, #EXTGRP, etc.) — not needed here
        else:
            if pending_name or pending_attrs:
                entries.append(
                    ChannelEntry(
                        source_id=pending_attrs.get("tvg-id", pending_name),
                        name=pending_name,
                        logo_url=pending_attrs.get("tvg-logo", ""),
                        group_title=pending_attrs.get("group-title", ""),
                        stream_url=line,
                    )
                )
            pending_attrs, pending_name = {}, ""

    return entries


async def fetch_m3u(client: httpx.AsyncClient, m3u_url: str) -> list[ChannelEntry]:
    resp = await client.get(m3u_url, timeout=30.0)
    resp.raise_for_status()
    return parse_m3u(resp.text)
