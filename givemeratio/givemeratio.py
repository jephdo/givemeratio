import os
import io
import re
import enum
import yaml
import unicodedata
import string
import pathlib

from urllib.parse import urljoin
from datetime import datetime, timedelta

import torf
import httpx
import xmltodict
import qbittorrentapi
import pytz

from pydantic import BaseModel, HttpUrl, validator, FilePath

from .settings import settings, Tracker, AVAILABLE_TRACKERS
from .formatters import sizeof_fmt


def retrieve_torrent(filepath: pathlib.Path) -> torf.Torrent:
    torrent = torf.Torrent.read(filepath)
    return torrent


def generate_filepath(tracker: Tracker, torrent_id: int) -> pathlib.Path:
    filename = f"[{tracker.name}]{torrent_id}.torrent"
    directory = pathlib.Path(settings.TORRENT_FILE_LOCATION)
    return directory / filename


class TorznabAttr(BaseModel):
    seeders: int
    files: int
    grabs: int
    peers: int
    downloadvolumefactor: int
    uploadvolumefactor: int

    @classmethod
    def from_rss(cls, attributes):
        vals = {}
        for attr in attributes:
            vals[attr["@name"]] = attr["@value"]
        return cls(**vals)


class RSSItem(BaseModel):
    id: int
    title: str
    size: int
    publish_date: datetime
    download_url: HttpUrl
    torrent: TorznabAttr
    tracker: Tracker

    @validator("publish_date", pre=True)
    def parse_datetime(cls, date_string):
        dt = datetime.strptime(date_string, "%a, %d %b %Y %H:%M:%S %z").astimezone(
            pytz.timezone("America/Los_Angeles")
        )
        # IDK why GGN is reporting their timestamps 8 hours ahead?
        dt -= timedelta(hours=8)
        return dt

    @property
    def sizeof_fmt(self):
        return sizeof_fmt(self.size)

    @property
    def filepath(self):
        return generate_filepath(self.tracker, self.id)

    @property
    def freeleech(self):
        return self.torrent.downloadvolumefactor == 0

    def __hash__(self):
        return self.id

    def __eq__(self, other):
        return self.id == other.id


class ProwlarrAPI:
    def __init__(self, baseurl: str, apikey: str):
        self.baseurl = baseurl
        self.apikey = apikey

    def fetch(self, client: httpx.Client, tracker: Tracker) -> httpx.Response:
        path = f"/api/v1/indexer/{tracker.prowlarr_id}/newznab/"
        url = urljoin(self.baseurl, path)
        response = client.get(
            url, params={"apikey": self.apikey, "t": "search", "q": ""}
        )
        return response

    def retrieve_rss(self, tracker: Tracker) -> list[RSSItem]:
        with httpx.Client() as client:
            response = self.fetch(client, tracker)
        content = response.content
        data = xmltodict.parse(content)
        return self.parse(data, tracker)

    def parse(self, json: dict, tracker: Tracker) -> list[RSSItem]:
        torrents = []
        items = json["rss"]["channel"]["item"]
        for item in items:
            torrent = TorznabAttr.from_rss(item["torznab:attr"])
            match = re.search(r"torrentid=(\d+)", item["guid"])
            if match is None:
                raise ValueError("Can not parse id from RSS item: {item['guid]}")
            id = int(match.group(1))

            torrents.append(
                RSSItem(
                    id=id,
                    title=item["title"],
                    size=item["size"],
                    publish_date=item["pubDate"],
                    download_url=item["link"],
                    torrent=torrent,
                    tracker=tracker,
                )
            )

        return torrents


class ValidationReasons(enum.Enum):
    FILE_EXISTS_ALREADY = 1
    FILE_SEEDING_ALREADY = 2
    TORRENT_TOO_LARGE = 3
    TORRENT_TOO_SMALL = 4
    TRACKER_BUDGET_EXCEEDED = 5
    GLOBAL_BUDGET_EXCEEDED = 6
    NOT_RECENT_ENOUGH = 7


# https://gist.github.com/wassname/1393c4a57cfcbf03641dbc31
def clean_filename(filename, replace=" "):
    valid_filename_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    for r in replace:
        filename = filename.replace(r, "_")

    cleaned_filename = (
        unicodedata.normalize("NFKD", filename).encode("ASCII", "ignore").decode()
    )
    cleaned_filename = "".join(c for c in cleaned_filename if c in valid_filename_chars)
    return cleaned_filename.lower()


class LocalTorrentFile(BaseModel):
    id: int
    tracker: Tracker
    infohash: str
    name: str
    contents: bytes
    size: int

    @property
    def filepath(self):
        return generate_filepath(self.tracker, self.id)

    def to_file(self):
        with open(self.filepath, "wb") as f:
            f.write(self.contents)

    @classmethod
    def from_rss_item(cls, item: RSSItem):
        response = httpx.get(item.download_url)
        return cls.from_bytes(item.id, item.tracker, contents=response.content)

    @classmethod
    def from_filepath(cls, filepath: str):
        path = pathlib.Path(filepath)
        match = re.match(r"\[(.*)\]([0-9]+).torrent", path.name)
        if match is None:
            raise ValueError(f"Filename not recognizable: {path.name}")
        tracker, torrent_id = match.groups()
        torrent_id = int(torrent_id)
        with open(filepath, "rb") as f:
            contents = f.read()

        return cls.from_bytes(torrent_id, AVAILABLE_TRACKERS[tracker], contents)

    @classmethod
    def from_bytes(cls, id: int, tracker: Tracker, contents: bytes):
        torrent = torf.Torrent.read_stream(io.BytesIO(contents))
        return cls(
            id=id,
            tracker=tracker,
            infohash=torrent.infohash,
            name=torrent.name,
            contents=contents,
            size=torrent.size,
        )

    def __hash__(self):
        return hash(self.infohash)

    def __eq__(self, other):
        return self.infohash == other.infohash


class RatioManager:
    def __init__(self, tracker: Tracker):
        self.tracker = tracker
        self.prowlarr = ProwlarrAPI(settings.PROWLARR_URL, settings.PROWLARR_API_KEY)
        self.qbt = qbittorrentapi.Client(
            host=settings.QBITTORRENT_HOST,
            port=settings.QBITTORRENT_PORT,
            username=settings.QBITTORRENT_USERNAME,
            password=settings.QBITTORRENT_PASSWORD,
        )
        # self.qbt.auth_log_in()

    def get_rss(self, freeleech_only: bool = True) -> list[RSSItem]:
        items = self.prowlarr.retrieve_rss(self.tracker)

        results = []
        for item in items:
            if freeleech_only:
                if item.freeleech:
                    results.append(item)
            else:
                results.append(item)

        def key(item):
            return item.publish_date.strftime("%Y-%m-%d %H:%M:%S")

        return list(sorted(results, key=key)[::-1])

    def validate(self, item: RSSItem) -> list[ValidationReasons]:
        errors = []
        if os.path.exists(item.filepath):
            errors.append(ValidationReasons.FILE_EXISTS_ALREADY)

            torrent = retrieve_torrent(item.filepath)
            infohash = torrent.infohash
            if self.check_exists(infohash):
                errors.append(ValidationReasons.FILE_SEEDING_ALREADY)

        if item.size > self.tracker.max_torrent_size_bytes:
            errors.append(ValidationReasons.TORRENT_TOO_LARGE)

        if item.size < self.tracker.min_torrent_size_bytes:
            errors.append(ValidationReasons.TORRENT_TOO_SMALL)

        seeding_size = self.check_seeding_size()
        if seeding_size + item.size > self.tracker.max_storage_size_bytes:
            errors.append(ValidationReasons.TRACKER_BUDGET_EXCEEDED)

        seeding_size = self.check_seeding_size()
        if seeding_size + item.size > settings.MAX_STORAGE_SIZE_BYTES:
            errors.append(ValidationReasons.GLOBAL_BUDGET_EXCEEDED)

        now = datetime.now(pytz.timezone("America/Los_Angeles"))
        if now - item.publish_date > self.tracker.min_published_recency:
            errors.append(ValidationReasons.NOT_RECENT_ENOUGH)

        return errors

    def add(self, item: RSSItem) -> LocalTorrentFile:
        torrent = LocalTorrentFile.from_rss_item(item)
        self.qbt.torrents_add(
            torrent_files=torrent.contents,
            # is_paused=True,
            category=settings.QBITTORRENT_CATEGORY,
        )
        torrent.to_file()
        return torrent

    def validate_items(
        self, items: list[RSSItem]
    ) -> dict[RSSItem, list[ValidationReasons]]:
        results = {}
        for item in items:
            errors = self.validate(item)

            results[item] = errors
        return results

    def check_seeding_size(self) -> int:
        torrents_info = self.qbt.torrents_info(category=settings.QBITTORRENT_CATEGORY)

        return sum(torrent.size for torrent in torrents_info)  # type: ignore

    def check_exists(self, infohash: str) -> bool:
        try:
            self.qbt.torrents_properties(infohash)
        except qbittorrentapi.NotFound404Error:
            return False
        return True

    def check_seed_times(self) -> dict[LocalTorrentFile, timedelta]:
        results = {}
        files = os.listdir(settings.TORRENT_FILE_LOCATION)

        torrents = []
        for filename in files:
            filepath = os.path.join(settings.TORRENT_FILE_LOCATION, filename)
            torrent = LocalTorrentFile.from_filepath(filepath)
            torrents.append(torrent)

        torrent_hashes = "|".join(torrent.infohash for torrent in torrents)
        raw_qbittorrent_info = self.qbt.torrents_info(torrent_hashes=torrent_hashes)
        qbittorrent_info = {info.infohash_v1: info for info in raw_qbittorrent_info}

        for torrent in torrents:
            info = qbittorrent_info[torrent.infohash]
            seeding_time = timedelta(seconds=info.seeding_time)
            results[torrent] = seeding_time
        return results

    def clean_up(self) -> dict[LocalTorrentFile, bool]:
        torrents = self.check_seed_times()
        results = {}
        for torrent, seed_time in torrents.items():
            if seed_time < torrent.tracker.min_seeding_time:
                results[torrent] = False
            else:
                self.qbt.torrents_delete(
                    torrent_hashes=torrent.infohash, delete_files=True
                )

                if os.path.exists(torrent.filepath):
                    os.remove(torrent.filepath)
                results[torrent] = True
        return results
