import yaml
from datetime import timedelta

from pydantic import BaseSettings, BaseModel, DirectoryPath, validator


class Settings(BaseSettings):
    # 330GiB = 322122547200 Bytes
    MAX_STORAGE_SIZE_BYTES: int = 322_122_547_200

    PROWLARR_API_KEY: str
    PROWLARR_URL: str

    # 300 seconds = 5 minutes:
    DAEMON_SLEEP_INTERVAL_SECONDS: int = 300

    QBITTORRENT_HOST: str
    QBITTORRENT_PORT: int
    QBITTORRENT_USERNAME: str
    QBITTORRENT_PASSWORD: str

    QBITTORRENT_CATEGORY = "givemeratio"

    TORRENT_FILE_LOCATION: DirectoryPath = "./torrents/"

    MIN_SEEDING_TIME: timedelta = timedelta(days=7)
    # 500M Bytes = 500 MB:
    MIN_TORRENT_SIZE_BYTES: int = 500_000_000
    # 100 GB:
    MAX_TORRENT_SIZE_BYTES: int = 100_000_000_000

    MIN_PUBLISHED_RECENCY: timedelta = timedelta(hours=2)

    TRACKER_CONFIG = "./trackers.yaml"

    class Config:
        env_file = ".env"


settings = Settings()  # type: ignore


class Tracker(BaseModel):
    name: str
    prowlarr_id: int
    min_seeding_time: timedelta = settings.MIN_SEEDING_TIME
    max_torrent_size_bytes: int = settings.MAX_TORRENT_SIZE_BYTES
    min_torrent_size_bytes: int = settings.MIN_TORRENT_SIZE_BYTES
    max_storage_size_bytes: int = settings.MAX_STORAGE_SIZE_BYTES
    min_published_recency: timedelta = settings.MIN_PUBLISHED_RECENCY

    @classmethod
    def from_file(cls, filepath: str) -> list["Tracker"]:
        with open(filepath) as f:
            config = yaml.safe_load(f)
        output = []
        for tracker, info in config.items():
            output.append(cls(name=tracker, **info))
        return output

    @validator("min_seeding_time", "min_published_recency")
    def parse_timedeltas(cls, v):
        if isinstance(v, timedelta):
            return v
        return timedelta(seconds=v)


_trackers = Tracker.from_file(settings.TRACKER_CONFIG)

AVAILABLE_TRACKERS = {t.name: t for t in _trackers}
