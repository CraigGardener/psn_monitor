"""Tests for the optional Prometheus exporter: the title statistics gauges and how the startup summary reports them."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

prometheus_client = pytest.importorskip("prometheus_client", reason="the exporter tests need the optional prometheus-client extra")


# Stands in for a psnawp title statistics row, with the attributes the exporter reads
def title(name, category, play_count, play_seconds, last_played):
    return SimpleNamespace(
        name=name,
        category=SimpleNamespace(name=category),
        play_count=play_count,
        play_duration=timedelta(seconds=play_seconds),
        last_played_date_time=last_played,
    )


# Reads one published sample back from the exporter's registry
def sample(metric, **labels):
    return prometheus_client.REGISTRY.get_sample_value(metric, labels)


# The exporter publishes each title's cumulative figures under the same game label the presence metrics use
def test_title_statistics_are_published_per_title(pm_module):
    last_played = datetime(2026, 10, 24, 12, 32, 3, tzinfo=timezone.utc)
    psn_user = SimpleNamespace(title_stats=lambda limit, page_size: iter([
        title("Rocket League®", "PS4", 567, 8 * 86400 + 11 * 3600, last_played),
        title("Minecraft", "PS4", 13, 12280, None),
        None,
    ]))

    assert pm_module.refresh_title_stats_metrics(psn_user, "stats-player") is True

    labels = {"user": "stats-player", "game": "Rocket League", "platform": "PS4"}
    assert sample("psn_title_play_seconds", **labels) == 8 * 86400 + 11 * 3600
    assert sample("psn_title_play_count", **labels) == 567
    assert sample("psn_title_last_played_timestamp_seconds", **labels) == last_played.timestamp()
    # A title PSN has no last-played date for keeps its totals and simply has no timestamp sample
    assert sample("psn_title_play_seconds", user="stats-player", game="Minecraft", platform="PS4") == 12280
    assert sample("psn_title_last_played_timestamp_seconds", user="stats-player", game="Minecraft", platform="PS4") is None
    assert sample("psn_title_stats_polls_total", user="stats-player") == 1


# A refresh that fails is counted and reported as failed, and leaves the presence poll to explain the outage
def test_a_failed_title_statistics_refresh_is_counted(pm_module):
    def failing(limit, page_size):
        raise RuntimeError("PSN said no")

    before = sample("psn_title_stats_errors_total", user="failing-player") or 0

    assert pm_module.refresh_title_stats_metrics(SimpleNamespace(title_stats=failing), "failing-player") is False
    assert sample("psn_title_stats_errors_total", user="failing-player") == before + 1


# Title statistics are fetched only when there is an exporter to publish them and the interval is not zero
@pytest.mark.parametrize("port, interval, enabled", [
    (9595, 300, True),
    (None, 300, False),
    (9595, 0, False),
])
def test_title_statistics_follow_the_exporter_and_the_interval(pm_module, monkeypatch, port, interval, enabled):
    monkeypatch.setattr(pm_module, "PROMETHEUS_PORT", port)
    monkeypatch.setattr(pm_module, "TITLE_STATS_INTERVAL", interval)

    assert pm_module.title_stats_enabled() is enabled


# The command line interval lands in the module setting the loop reads
def test_the_title_statistics_interval_option_applies(pm_module, monkeypatch):
    monkeypatch.setattr(pm_module, "TITLE_STATS_INTERVAL", 300)

    pm_module.apply_runtime_cli_overrides(SimpleNamespace(title_stats_interval=900))

    assert pm_module.TITLE_STATS_INTERVAL == 900


# The summary names the endpoint and the refresh cadence only once the exporter is switched on
def test_the_summary_reports_the_exporter_once_it_is_on(pm_module, monkeypatch):
    monkeypatch.setattr(pm_module, "PROMETHEUS_PORT", 9595)
    monkeypatch.setattr(pm_module, "TITLE_STATS_INTERVAL", 300)
    rows = {row.label: row for row in pm_module.build_startup_summary("stats-player")}

    assert rows["Prometheus metrics"].value == "http://0.0.0.0:9595/metrics"
    assert rows["Prometheus metrics"].concise is True
    assert rows["Title statistics"].value.startswith("Every ")
    assert rows["Title statistics"].concise is True

    monkeypatch.setattr(pm_module, "PROMETHEUS_PORT", None)
    rows = {row.label: row for row in pm_module.build_startup_summary("stats-player")}

    assert rows["Prometheus metrics"].value == "Disabled"
    assert rows["Prometheus metrics"].concise is False
    assert rows["Title statistics"].value == "Disabled"
