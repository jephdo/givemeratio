import time
import click
import rich
import httpx

from datetime import timedelta

from givemeratio.givemeratio import RatioManager, Tracker
from givemeratio.formatters import humanize, sizeof_fmt
from givemeratio.settings import settings, AVAILABLE_TRACKERS


manager = RatioManager(AVAILABLE_TRACKERS["ggn"])


@click.group()
def cli():
    pass


@cli.command()
@click.option("-f", "--freeleech", is_flag=True)
def fetch(freeleech, limit=25):
    items = manager.get_rss(freeleech_only=freeleech)

    table = rich.table.Table(title="Freeleech Torrents")
    table.add_column("Tracker")
    table.add_column("ID")
    table.add_column("Size")
    table.add_column("Publish Date")
    table.add_column("Title")
    table.add_column("Freeleech")
    table.add_column("Seeders")
    table.add_column("Peers")

    for item in items[:limit]:
        table.add_row(
            item.tracker.name,
            str(item.id),
            item.sizeof_fmt,
            f"{humanize(item.publish_date)}",  # type: ignore
            item.title,
            "Y" if item.freeleech else "N",
            str(item.torrent.seeders),
            str(item.torrent.peers),
        )

    console = rich.console.Console()
    console.print(table)


@cli.command()
def add():
    items = manager.get_rss(freeleech_only=True)
    items = manager.validate_items(items)

    table = rich.table.Table(title="Freeleech Torrents")
    table.add_column("Tracker")
    table.add_column("ID")
    table.add_column("Size")
    table.add_column("Publish Date")
    table.add_column("Title")
    table.add_column("Added")
    table.add_column("Verification Errors")
    for item, errors in items.items():
        if not errors:
            torrent = manager.add(item)
        else:
            torrent = None

        add_info = str(torrent.filepath) if torrent is not None else ""

        table.add_row(
            item.tracker.name,
            str(item.id),
            item.sizeof_fmt,
            f"{humanize(item.publish_date)}",
            item.title,
            add_info,
            ",".join(e.name for e in errors),
        )
    console = rich.console.Console()
    console.print(table)


@cli.command()
def status():
    items = manager.check_seed_times()
    items = list(sorted(items.items(), key=lambda x: x[1] if x is not None else -1))

    table = rich.table.Table(title="Freeleech Torrents")
    table.add_column("Tracker")
    table.add_column("ID")
    table.add_column("Size")
    table.add_column("Title")
    table.add_column("Seeding Time")
    table.add_column("Time Left")
    for item, seed_time in items:
        time_left = item.tracker.min_seeding_time - seed_time
        table.add_row(
            item.tracker.name,
            str(item.id),
            sizeof_fmt(item.size),
            item.name,
            str(seed_time),
            str(time_left),
        )
    console = rich.console.Console()
    console.print(table)


@cli.command()
def clean():
    items = manager.clean_up()
    items = list(sorted(items.items(), key=lambda x: x[1]))

    table = rich.table.Table(title="Freeleech Torrents")
    table.add_column("Tracker")
    table.add_column("ID")
    table.add_column("Size")
    table.add_column("Title")
    table.add_column("Removed")
    for item, is_deleted in items:
        table.add_row(
            item.tracker.name,
            str(item.id),
            sizeof_fmt(item.size),
            item.name,
            "Y" if is_deleted else "N",
        )
    console = rich.console.Console()
    console.print(table)


@cli.command()
def run():
    # optional args: sleep time,
    while True:
        try:
            items = manager.get_rss(freeleech_only=True)
        except httpx.ReadTimeout:
            time.sleep(settings.DAEMON_SLEEP_INTERVAL_SECONDS)
            continue

        for item in items:
            errors = manager.validate(item)
            if not errors:
                print(f"Adding {item.title}")
                try:
                    torrent = manager.add(item)
                except httpx.ReadTimeout:
                    continue

        manager.clean_up()

        print(f"Sleeping {settings.DAEMON_SLEEP_INTERVAL_SECONDS} seconds")
        time.sleep(settings.DAEMON_SLEEP_INTERVAL_SECONDS)


if __name__ == "__main__":
    cli()
