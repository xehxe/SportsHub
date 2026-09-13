from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    abbreviation: Mapped[str] = mapped_column(String(16), default="")
    league_key: Mapped[str] = mapped_column(String(16), index=True)
    logo_url: Mapped[str] = mapped_column(String(500), default="")
    primary_color: Mapped[str] = mapped_column(String(16), default="")

    home_games: Mapped[list["Game"]] = relationship(
        "Game", foreign_keys="Game.home_team_id", back_populates="home_team"
    )
    away_games: Mapped[list["Game"]] = relationship(
        "Game", foreign_keys="Game.away_team_id", back_populates="away_team"
    )


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("league_key", "source_event_id", name="uq_league_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_key: Mapped[str] = mapped_column(String(16), index=True)
    source_event_id: Mapped[str] = mapped_column(String(64), index=True)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    home_team: Mapped["Team"] = relationship(
        "Team", foreign_keys=[home_team_id], back_populates="home_games"
    )
    away_team: Mapped["Team"] = relationship(
        "Team", foreign_keys=[away_team_id], back_populates="away_games"
    )

    home_score: Mapped[int | None] = mapped_column(nullable=True)
    away_score: Mapped[int | None] = mapped_column(nullable=True)

    # Always stored normalized to UTC (see app.normalization.to_utc_iso)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # One of: scheduled | live | final | postponed  (see normalize_status)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    status_detail: Mapped[str] = mapped_column(String(120), default="")
    venue: Mapped[str] = mapped_column(String(200), default="")

    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    broadcasts: Mapped[list["Broadcast"]] = relationship(
        "Broadcast", back_populates="game", cascade="all, delete-orphan"
    )


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    network: Mapped[str] = mapped_column(String(80))
    region: Mapped[str] = mapped_column(String(40), default="national")

    # Populated by channel_matching.py once an IPTV source is connected.
    stream_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    channel_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    game: Mapped["Game"] = relationship("Game", back_populates="broadcasts")


class IPTVSource(Base):
    """A user's Xtream Codes account or M3U playlist URL."""
    __tablename__ = "iptv_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # "xtream" | "m3u"
    label: Mapped[str] = mapped_column(String(120), default="My IPTV")

    # Xtream fields (blank for m3u sources)
    base_url: Mapped[str] = mapped_column(String(300), default="")
    username: Mapped[str] = mapped_column(String(120), default="")
    password: Mapped[str] = mapped_column(String(120), default="")

    # M3U fields (blank for xtream sources)
    m3u_url: Mapped[str] = mapped_column(String(500), default="")

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    channel_count: Mapped[int] = mapped_column(default=0)


class Channel(Base):
    """One sports-relevant channel pulled from an IPTVSource's playlist."""
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("iptv_source_id", "source_channel_id", name="uq_source_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    iptv_source_id: Mapped[int] = mapped_column(ForeignKey("iptv_sources.id"))
    source_channel_id: Mapped[str] = mapped_column(String(120))

    name: Mapped[str] = mapped_column(String(200), default="")
    cleaned_name: Mapped[str] = mapped_column(String(200), default="", index=True)
    network_key: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    logo_url: Mapped[str] = mapped_column(String(500), default="")
    group_title: Mapped[str] = mapped_column(String(120), default="")
    stream_url: Mapped[str] = mapped_column(String(500), default="")
