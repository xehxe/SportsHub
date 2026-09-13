"""
Central configuration: data sources, throttling knobs, and static
lookup tables used by the normalization layer.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LeagueSource:
    key: str                 # internal short key, e.g. "nfl"
    label: str                # display name, e.g. "NFL"
    espn_sport: str           # ESPN sport segment, e.g. "football"
    espn_league: str          # ESPN league segment, e.g. "nfl"

    @property
    def scoreboard_url(self) -> str:
        return (
            f"https://site.api.espn.com/apis/site/v2/sports/"
            f"{self.espn_sport}/{self.espn_league}/scoreboard"
        )


# Public ESPN "site API" endpoints. These are undocumented but widely used
# read-only JSON feeds behind ESPN's own web/mobile clients.
LEAGUES: list[LeagueSource] = [
    LeagueSource("nfl", "NFL", "football", "nfl"),
    LeagueSource("nba", "NBA", "basketball", "nba"),
    LeagueSource("mlb", "MLB", "baseball", "mlb"),
    LeagueSource("nhl", "NHL", "hockey", "nhl"),
    LeagueSource("epl", "Premier League", "soccer", "eng.1"),
]

LEAGUES_BY_KEY = {lg.key: lg for lg in LEAGUES}


@dataclass
class ThrottleConfig:
    # Minimum seconds between two requests to the *same* host.
    min_interval_seconds: float = 1.5
    # Maximum concurrent in-flight requests across all leagues.
    max_concurrency: int = 3
    # Retry/backoff
    max_retries: int = 5
    backoff_base_seconds: float = 0.75
    backoff_max_seconds: float = 20.0
    request_timeout_seconds: float = 10.0


THROTTLE = ThrottleConfig()

DATABASE_URL = "sqlite:///./sportshub.db"

# Manual alias table for teams whose ESPN short/abbreviation names drift
# across sports (e.g. "NY" alone is ambiguous). Keys are lowercased.
# This backstops the fuzzy matcher in normalization.py for known trouble spots.
TEAM_ALIASES: dict[str, str] = {
    "ny giants": "New York Giants",
    "ny jets": "New York Jets",
    "ny yankees": "New York Yankees",
    "ny mets": "New York Mets",
    "man utd": "Manchester United",
    "man city": "Manchester City",
    "gsw": "Golden State Warriors",
    "wsh": "Washington Commanders",
}

# Keywords used to decide whether an IPTV playlist entry is even worth
# considering as a sports channel before we try to match it to a game.
# Keeps a 10,000-channel IPTV list from being scanned team-by-team.
# Deliberately broad/lowercase-substring here (unlike NETWORK_ALIASES,
# which is exact) — this list only gates "is this worth a closer look",
# the actual identity resolution happens in network_normalizer.py.
SPORTS_CHANNEL_KEYWORDS: list[str] = [
    "espn", "fox sports", "fs1", "fs2", "tnt", "tbs", "trutv", "nbc sports", "usa network",
    "nfl network", "nfl redzone", "nba tv", "mlb network", "nhl network",
    "cbs sports", "abc", "peacock", "acc network", "sec network",
    "big ten network", "btn", "pac-12 network", "golf channel", "tennis channel", "willow",
    "root sports", "marquee", "yes network", "nesn", "msg network", "spectrum sportsnet",
    "altitude", "paramount+", "apple tv", "amazon prime", "prime video",
    # Team-owned local networks that replaced regional-sports-network deals
    # after the 2026 FanDuel Sports Network shutdown (see RSN_TEAM_MAP note).
    "bravesvision", "rangers sports network", "detroit sportsnet", "angels broadcast",
]

# Regional/team-owned local network -> team display name(s) (as normalized
# by TeamNormalizer). These channels don't appear in ESPN's own broadcast
# list, so national-network matching can't find them; this table fills
# that gap for the networks that are effectively team-exclusive.
#
# IMPORTANT — accurate as of September 2026, and this landscape is moving
# fast: FanDuel Sports Network (formerly Bally Sports, formerly the Fox
# Sports regional networks) shut down entirely in April 2026 after all 9
# MLB teams, 13 NBA teams, and most NHL teams it carried left the network.
# Several of those teams built their own local networks (Braves, Rangers,
# Tigers/Red Wings shown below); many NBA/NHL teams' 2026-27 local rights
# were still being finalized as of this writing and aren't listed here —
# check your own market and extend this table rather than trusting it
# blindly for teams not shown.
RSN_TEAM_MAP: dict[str, list[str]] = {
    "yes network": ["New York Yankees", "Brooklyn Nets"],
    "marquee sports network": ["Chicago Cubs"],
    "nesn": ["Boston Red Sox", "Boston Bruins"],
    "msg network": ["New York Knicks", "New York Rangers"],
    "altitude sports": ["Denver Nuggets", "Colorado Avalanche"],
    "spectrum sportsnet": ["Los Angeles Lakers", "Los Angeles Dodgers"],
    "root sports northwest": ["Seattle Mariners", "Portland Trail Blazers"],
    "root sports pittsburgh": ["Pittsburgh Pirates"],
    # Post-FanDuel-shutdown replacements (2026 season onward):
    "bravesvision": ["Atlanta Braves"],
    "rangers sports network": ["Texas Rangers"],
    "detroit sportsnet": ["Detroit Tigers", "Detroit Red Wings"],
}


