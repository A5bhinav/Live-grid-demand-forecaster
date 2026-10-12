from datetime import UTC, datetime

from gridcast.ingest.eia import EiaRow
from gridcast.jobs.smoke import summarize


def test_summarize_counts_rows_and_missing_values_per_type() -> None:
    rows = [
        EiaRow("CISO", "D", datetime(2026, 10, 9, 0, tzinfo=UTC), 42502.0),
        EiaRow("CISO", "D", datetime(2026, 10, 9, 1, tzinfo=UTC), None),
        EiaRow("CISO", "DF", datetime(2026, 10, 9, 0, tzinfo=UTC), 43071.0),
    ]

    demand, forecast = summarize(rows)

    assert demand.split() == [
        "D",
        "rows=2",
        "with_value=1",
        "first=2026-10-09T00",
        "last=2026-10-09T01",
    ]
    assert forecast.split()[:3] == ["DF", "rows=1", "with_value=1"]


def test_summarize_handles_a_type_with_no_rows() -> None:
    demand, forecast = summarize([])

    assert demand.split() == ["D", "rows=0", "with_value=0"]
    assert forecast.split() == ["DF", "rows=0", "with_value=0"]
