"""Smoke check: pull the last 48 hours of CISO demand and operator forecast, print row counts.

Run with `uv run python -m gridcast.jobs.smoke`.
"""

import sys
from datetime import UTC, datetime, timedelta

import httpx

from gridcast import config
from gridcast.ingest.eia import PERIOD_FORMAT, EiaRow, fetch_region_data

WINDOW_HOURS = 48
TYPES = (config.EIA_TYPE_DEMAND, config.EIA_TYPE_OPERATOR_FORECAST)


def summarize(rows: list[EiaRow]) -> list[str]:
    lines = []
    for type_ in TYPES:
        of_type = [row for row in rows if row.type == type_]
        with_value = [row for row in of_type if row.value is not None]
        line = f"  {type_:<2}  rows={len(of_type):<3} with_value={len(with_value):<3}"
        if of_type:
            first, last = of_type[0].period_utc, of_type[-1].period_utc
            line += f" first={first:{PERIOD_FORMAT}} last={last:{PERIOD_FORMAT}}"
        lines.append(line)
    return lines


def main() -> int:
    api_key = config.Settings().require_eia_api_key()
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=WINDOW_HOURS - 1)

    with httpx.Client(timeout=30) as client:
        rows = fetch_region_data(client, api_key, start, end)

    print(
        f"{config.EIA_RESPONDENT} {start:{PERIOD_FORMAT}} to {end:{PERIOD_FORMAT}} UTC "
        f"({WINDOW_HOURS} hourly periods requested)"
    )
    print("\n".join(summarize(rows)))

    missing = [type_ for type_ in TYPES if not any(row.type == type_ for row in rows)]
    if missing:
        print(f"FAIL: no rows returned for {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
