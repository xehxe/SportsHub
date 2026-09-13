# SportsHub

A small live-schedule aggregator: pulls scoreboard data from ESPN's public
site-API JSON endpoints, normalizes team names and kickoff times, stores
everything in SQLite via SQLAlchemy, and renders it as a Tailwind dashboard
through FastAPI.

## Layout

```
app/
  config.py           League endpoints, throttle knobs, team/RSN alias tables, sports-channel keywords
  normalization.py    Team-name fuzzy matching + UTC time normalization
  database.py         SQLAlchemy engine/session/Base
  models.py           Team, Game, Broadcast, IPTVSource, Channel ORM models
  fetchers.py         ESPN httpx client, backoff/retry, throttling, upsert logic
  iptv_client.py      Xtream Codes API client + M3U playlist parser
  channel_matching.py Links IPTV channels to games (exact network identity + RSN mapping)
  network_normalizer.py Canonical network identity resolution (ESPN vs ESPN2 vs ESPNU, etc.)
  matchup_image.py    Generates diagonal team-color/logo cards (Pillow)
  main.py             FastAPI app, background refresh loop, routes
  templates/          Jinja2 + Tailwind (CDN) dashboard + settings page
  static/             Fallback CSS + cached matchup images
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The app syncs all configured leagues on
startup and then every 90 seconds in the background (`REFRESH_INTERVAL_SECONDS`
in `app/main.py`). You can also trigger a sync manually:

```bash
curl -X POST http://127.0.0.1:8000/api/sync
```

Raw JSON for any league is available at `/api/games?league=nba`.

## Connecting an Xtream Code or M3U playlist

Visit `/settings` and enter either:
- an **Xtream Codes** panel URL + username + password, or
- a plain **M3U playlist URL** (what most "M3U code" links resolve to).

On save, SportsHub pulls the channel list, keeps only entries that look
like sports channels (`SPORTS_CHANNEL_KEYWORDS` in `config.py`), and tries
to match each one to games already synced from ESPN:

1. **National networks** — resolved by exact identity, not substring: the
   game's ESPN broadcast (e.g. "ESPN2") and every IPTV channel name are
   both run through `network_normalizer.py`'s `NetworkNormalizer`, which
   maps ~30 known networks (ESPN and its sub-brands, FOX/FS1/FS2, the
   conference networks, TNT/TBS/truTV, league networks, streaming
   partners) to one canonical key each. This is deliberately exact —
   an earlier substring-based version of this logic would match "ESPN2"
   against a plain "ESPN" channel, which is wrong often enough to matter.
2. **Regional/team-owned networks** — channels like YES Network,
   Marquee, or BravesVision don't appear in ESPN's broadcast list at all;
   `RSN_TEAM_MAP` in `config.py` maps them to the teams they carry.

   **This table needs your attention.** Regional sports TV collapsed in
   2026: FanDuel Sports Network (formerly Bally Sports) shut down
   entirely in April 2026 after all 9 MLB teams, 13 NBA teams, and most
   NHL teams it carried left. Several teams launched their own networks
   (Braves → BravesVision, Rangers → Rangers Sports Network, Tigers/Red
   Wings → Detroit SportsNet — all included below); many other NBA/NHL
   teams' 2026-27 local rights were still being finalized as this was
   written and aren't in the table. Check what's actually in your
   provider's lineup and extend `RSN_TEAM_MAP` for your market —
   `Channel.cleaned_name` is stored on every synced channel specifically
   so you can inspect what came through before adding it.

Matched games get a **Watch** link on their card pointing straight at the
IPTV stream URL. IPTV sources re-sync every 20 refresh cycles (~30 min by
default) since channel lineups change far less often than scores —
adjust `IPTV_REFRESH_EVERY_N_CYCLES` in `main.py` if you want it tighter.

## Matchup images

Each game gets a generated 1280×720 PNG at `/matchup/{game_id}.png`: a
diagonal split colored with each team's ESPN-reported primary color, with
that team's logo composited on its half — the same shape broadcasters use
for pre-game bumpers. Images are generated once on first request and
cached to `app/static/matchups/`; call `matchup_image.invalidate(game_id)`
if a game's teams are ever corrected and the art needs to regenerate.

## Design notes

- **Data source**: `app/config.py` lists ESPN's undocumented-but-stable
  `site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard`
  endpoints. These are read-only, public, and used by ESPN's own web/mobile
  clients — no API key. Adding a league is a one-line addition to `LEAGUES`.
- **Normalization**: `TeamNormalizer` keeps a canonical name per team slug
  and resolves any incoming variant (full name, short name, abbreviation)
  against it, falling back to `difflib` fuzzy matching for near-misses, with
  a manual alias table (`TEAM_ALIASES`) for known ambiguous cases like
  "NY Giants" vs "NY Jets". All kickoff times are converted to UTC on the
  way into the database (`to_utc_iso`); the browser converts back to local
  time client-side so the server never needs to know the viewer's timezone.
- **Resiliency**: `Throttler` caps global concurrency and enforces a minimum
  spacing between requests to the same host; `tenacity` retries transient
  failures (429/5xx/transport errors) with exponential backoff and jitter,
  and gives up cleanly (logging + skipping that league for the cycle)
  rather than blocking the rest of the sync.
- **Storage**: `teams`, `games` (unique on `(league_key, source_event_id)`
  so re-syncs upsert instead of duplicating), `broadcasts` (network +
  matched stream URL, replaced wholesale on each ESPN sync), plus
  `iptv_sources` and `channels` for whatever playlist you connect.

## Known limitations / next steps

- ESPN's site API is undocumented and can change shape without notice —
  treat `fetchers.py`'s JSON parsing as the most likely thing to need
  updating if a league's payload shifts.
- Channel matching for national networks is now exact (canonical key
  resolution in `network_normalizer.py`), but regional/team-owned network
  coverage is only as good as `RSN_TEAM_MAP` — see the IPTV section above
  for why that table needs regular attention in the current landscape.
- Xtream credentials and M3U URLs are stored in plaintext in SQLite
  (`iptv_sources` table) since this is designed to run locally for one
  user. If you ever expose this beyond localhost, encrypt that table or
  move credentials to environment variables/secrets storage first.
- This project's schema changed since the first version (added
  `Broadcast.stream_url`/`channel_logo_url`, `IPTVSource`, `Channel`).
  SQLite's `create_all()` only creates missing *tables*, not missing
  *columns* on existing ones — if you're upgrading from an earlier run,
  delete `sportshub.db` and let it resync rather than hitting column
  errors.
- No auth/rate-limit key is required for ESPN today, but if you deploy
  this publicly, consider caching responses and lowering sync frequency
  to stay a good citizen of a free public endpoint.
- `%-d` in the date-grouping header (`main.py`) is a Linux/macOS strftime
  extension; swap to `%d` (or `str(day)` without zero-padding) if running
  on Windows.

