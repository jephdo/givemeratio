from collections import OrderedDict

import pytest
import pytz
import xmltodict
import yaml

from givemeratio.givemeratio import ProwlarrAPI, RatioManager, Tracker
from givemeratio.settings import settings, AVAILABLE_TRACKERS


@pytest.fixture
def prowlarr_api():
    return ProwlarrAPI(settings.PROWLARR_URL, settings.PROWLARR_API_KEY)


@pytest.fixture
def valid_tracker_config() -> dict:
    return {
        "a": {"prowlarr_id": 5, "min_published_recency": 100},
        "b": {"prowlarr_id": 23, "min_seeding_time": 15},
    }


@pytest.fixture
def valid_rss_xml(faker):
    N = 20
    faker.seed_instance(17)
    items = []
    for _ in range(N):
        item = {
            "title": faker.name(),
            "guid": faker.numerify(
                "https://gazellegames.net/torrents.php?id=73372&amp;torrentid=#####"
            ),
            "pubDate": faker.date_time()
            .astimezone(pytz.utc)
            .strftime("%a, %d %b %Y %H:%M:%S %z"),
            "size": faker.random_int(),
            "link": faker.url(),
            "torznab:attr": [
                {"@name": "seeders", "@value": faker.random_int()},
                {"@name": "files", "@value": faker.random_int()},
                {"@name": "grabs", "@value": faker.random_int()},
                {"@name": "peers", "@value": faker.random_int()},
                {
                    "@name": "downloadvolumefactor",
                    "@value": faker.random_element(elements=(0, 1)),
                },
                {
                    "@name": "uploadvolumefactor",
                    "@value": faker.random_element(elements=(0, 1)),
                },
            ],
        }
        items.append(item)
    rss = {
        "rss": {
            "channel": {"item": items},
        }
    }
    return xmltodict.unparse(rss)


@pytest.fixture
def valid_rss_items(prowlarr_api: ProwlarrAPI, valid_rss_xml, valid_tracker: Tracker):
    data = xmltodict.parse(valid_rss_xml)
    return prowlarr_api.parse(data, valid_tracker)


@pytest.fixture
def valid_rss_item(valid_rss_items):
    return valid_rss_items[0]


@pytest.fixture
def valid_torrent_metainfo(faker):
    faker.seed_instance(37)
    return OrderedDict(
        [
            (b"announce", faker.url().encode()),
            (b"comment", faker.sentence().encode()),
            (b"created by", faker.name().encode()),
            (b"creation date", int(faker.date_time().timestamp())),
            (
                b"info",
                OrderedDict(
                    [
                        (b"length", 500000),
                        (b"name", b"Torrent for testing"),
                        (b"piece length", 32768),
                        (b"pieces", b"\x00" * 20 * 16),
                        (b"private", 1),
                    ]
                ),
            ),
        ]
    )


@pytest.fixture
def valid_tracker():
    return next(iter(AVAILABLE_TRACKERS.values()))


@pytest.fixture
def valid_ratio_manager(valid_tracker):
    return RatioManager(valid_tracker)
