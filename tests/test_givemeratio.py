import io
import pathlib
import hashlib

import flatbencode
import yaml
import pytest
import httpx
import xmltodict

from pytest_httpx import HTTPXMock
from datetime import timedelta
from unittest.mock import patch

from givemeratio.givemeratio import (
    ProwlarrAPI,
    RSSItem,
    LocalTorrentFile,
    clean_filename,
    RatioManager,
    generate_filepath,
)
from givemeratio.settings import settings, Tracker

from datetime import datetime


class TestProwlarrAPI:
    def test_parse_rss_xml(
        self, valid_rss_xml, prowlarr_api: ProwlarrAPI, valid_tracker: Tracker
    ):
        data = xmltodict.parse(valid_rss_xml)
        results = prowlarr_api.parse(data, valid_tracker)

        items = data["rss"]["channel"]["item"]
        for expected, actual in zip(items, results):
            assert expected["title"] == actual.title
            assert int(expected["size"]) == actual.size
            assert (
                datetime.strptime(expected["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
                == actual.publish_date
            )
            assert expected["link"] == actual.download_url

            for attr in expected["torznab:attr"]:
                assert getattr(actual.torrent, attr["@name"]) == int(attr["@value"])

            assert actual.tracker == valid_tracker

    def test_prowlarr_api_fetch_correct_url(
        self,
        httpx_mock: HTTPXMock,
        prowlarr_api: ProwlarrAPI,
        valid_rss_xml,
        valid_tracker: Tracker,
    ):
        tracker_id = 17
        url = f"{settings.PROWLARR_URL}"
        content = xmltodict.unparse(
            xmltodict.parse(valid_rss_xml)
        ).encode()  # .encode()
        httpx_mock.add_response(content=content)

        with httpx.Client() as client:
            response = prowlarr_api.fetch(client, valid_tracker)

        assert response.status_code == 200
        assert response.content == content
        assert (
            response.url.path == f"/api/v1/indexer/{valid_tracker.prowlarr_id}/newznab/"
        )
        assert response.url.params["apikey"] == settings.PROWLARR_API_KEY
        url = response.url
        assert f"{url.scheme}://{url.host}:{url.port}" == settings.PROWLARR_URL

    def test_prowlarr_api_retrieve_returns_list_of_rssitems(
        self,
        httpx_mock: HTTPXMock,
        prowlarr_api: ProwlarrAPI,
        valid_rss_xml,
        valid_tracker: Tracker,
    ):
        tracker_id = 17
        data = xmltodict.parse(valid_rss_xml)
        content = xmltodict.unparse(data).encode()
        httpx_mock.add_response(content=content)

        results = prowlarr_api.retrieve_rss(tracker=valid_tracker)

        assert len(results) == len(data["rss"]["channel"]["item"])
        for result in results:
            assert isinstance(result, RSSItem)


class TestRSSItemBehavior:
    def test_freeleech(self, valid_rss_items: list[RSSItem]):
        for item in valid_rss_items:
            if item.torrent.downloadvolumefactor == 0:
                assert item.freeleech
            else:
                assert not item.freeleech

    def test_equality_based_on_id_only(self, valid_rss_item: RSSItem):
        A = valid_rss_item
        B = valid_rss_item.copy(deep=True)
        assert A == B
        B.id += 1
        assert A != B

    def test_hashable(self, valid_rss_item):
        dict_ = {valid_rss_item: 1}
        assert valid_rss_item in dict_


def test_parse_valid_trackers_yaml_config_file(tmp_path, valid_tracker_config):
    contents = yaml.dump(valid_tracker_config)
    file = tmp_path / "trackers.yaml"
    file.write_text(contents)

    trackers = Tracker.from_file(file)
    assert len(trackers) == 2

    A, B = trackers
    assert A.prowlarr_id == valid_tracker_config["a"]["prowlarr_id"]
    assert A.min_published_recency == timedelta(
        seconds=valid_tracker_config["a"]["min_published_recency"]
    )
    assert B.prowlarr_id == valid_tracker_config["b"]["prowlarr_id"]
    assert B.min_seeding_time == timedelta(
        seconds=valid_tracker_config["b"]["min_seeding_time"]
    )

    # Check default values are set for optional args:
    assert A.min_torrent_size_bytes == settings.MIN_TORRENT_SIZE_BYTES


class TestLocalTorrentFileSerialization:
    def test_from_bytes(self, valid_torrent_metainfo, valid_tracker):
        contents = flatbencode.encode(valid_torrent_metainfo)
        infohash = hashlib.sha1(
            flatbencode.encode(valid_torrent_metainfo[b"info"])
        ).hexdigest()
        name = valid_torrent_metainfo[b"info"][b"name"]
        torrent_id = 12345
        local_torrent_file = LocalTorrentFile.from_bytes(
            tracker=valid_tracker, id=torrent_id, contents=contents
        )

        assert local_torrent_file.id == torrent_id
        assert local_torrent_file.tracker == valid_tracker
        assert local_torrent_file.infohash == infohash
        assert local_torrent_file.name == name.decode()
        assert local_torrent_file.contents == contents

    def test_generate_filepath(self, valid_tracker):
        tracker, torrent_id = (
            valid_tracker,
            123,
        )
        filepath = generate_filepath(tracker, torrent_id)

        assert filepath.parent == settings.TORRENT_FILE_LOCATION
        assert tracker.name in filepath.name
        assert f"[{tracker.name}]{torrent_id}.torrent" == filepath.name
        assert filepath.suffix == ".torrent"

    def test_from_filepath(
        self,
        tmp_path,
        valid_torrent_metainfo,
        valid_tracker,
    ):
        contents = flatbencode.encode(valid_torrent_metainfo)
        name = valid_torrent_metainfo[b"info"][b"name"]
        torrent_id = 1234
        filename = generate_filepath(valid_tracker, torrent_id).name
        filepath = tmp_path / filename
        tmp_path.mkdir(exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(contents)

        torrent = LocalTorrentFile.from_filepath(str(filepath))

        assert torrent.tracker == valid_tracker
        assert torrent.id == torrent_id
        assert torrent.name == name.decode()
        assert torrent.contents == contents

    def test_equality_and_hashing_is_based_on_infohashes(
        self, valid_torrent_metainfo, valid_tracker: Tracker
    ):
        contents = flatbencode.encode(valid_torrent_metainfo)
        torrent_id = 12345
        A = LocalTorrentFile.from_bytes(torrent_id, valid_tracker, contents)
        B = A.copy(deep=True)
        assert A == B
        B.infohash = "".join(reversed(A.infohash))
        assert A != B


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("Equalizer, The (Europe).zip", "equalizer_the_(europe).zip"),
        (
            "Lucas Gomes - Black House (2023) [24-48]",
            "lucas_gomes_-_black_house_(2023)_24-48",
        ),
    ],
)
def test_clean_filename(filename, expected):
    assert clean_filename(filename) == expected


class TestRatioManager:
    def test_get_rss_only_freeleech(self, valid_ratio_manager, valid_rss_items):
        with patch.object(ProwlarrAPI, "retrieve_rss") as mock_retrieve:
            mock_retrieve.return_value = valid_rss_items
            items = valid_ratio_manager.get_rss()

        assert len(items) == len([i for i in valid_rss_items if i.freeleech])

        for item in items:
            assert item.freeleech

    def test_get_rss_is_sorted_by_datetime_descending(
        self, valid_ratio_manager, valid_rss_items
    ):
        with patch.object(ProwlarrAPI, "retrieve_rss") as mock_retrieve:
            mock_retrieve.return_value = valid_rss_items
            items = valid_ratio_manager.get_rss()

            mock_retrieve.assert_called_with(valid_ratio_manager.tracker)

        assert len(items) > 1

        last_timestamp = items[0].publish_date
        for item in items:
            assert item.publish_date <= last_timestamp
            last_timestamp = item.publish_date

    # @mock.patch.object()
    # def test_get_freeleech(self):
    #     with mock.patch.object(
    #         "givemeratio.givemeratio.ProwlarrAPI.retrieve_rss"
    #     ) as retrieve_rss:
    #         retri
