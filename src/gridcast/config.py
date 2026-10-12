"""Configuration: secrets from the environment, protocol constants from CLAUDE.md."""

from datetime import date
from typing import NamedTuple
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- EIA-930 (CLAUDE.md §4.1) ---
EIA_REGION_DATA_URL = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
EIA_RESPONDENT = "CISO"
EIA_TYPE_DEMAND = "D"
EIA_TYPE_OPERATOR_FORECAST = "DF"
EIA_PAGE_SIZE = 5000  # hard cap on rows per JSON response
EIA_MIN_REQUEST_INTERVAL_S = 1.0  # keys get suspended if the API is hammered
EIA_BACKFILL_START = date(2019, 1, 1)  # the v2 API holds no CISO rows before this
EIA_CHUNK_DAYS = 90  # 90 days x 24 h x 2 types fits in one page
EIA_REVISION_WINDOW_DAYS = 7  # every ingest re-pulls this trailing window

# --- Open-Meteo Previous Runs (CLAUDE.md §4.2) ---
WEATHER_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
WEATHER_MODEL = "gfs_global"  # pinned to one model; never "best match" or a blend
WEATHER_LEAD_DAYS = 2  # `_previous_day2` is the only leakage-safe offset
WEATHER_START = date(2024, 1, 20)  # day-2 archive begins 2024-01-20T12Z for every v1 variable
WEATHER_HORIZON_DAYS = 2  # fetch far enough ahead to cover every local hour of D+1
WEATHER_MIN_REQUEST_INTERVAL_S = 0.5
WEATHER_VARIABLES = (
    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "shortwave_radiation",
    "wind_speed_10m",
    "precipitation",
)


class Location(NamedTuple):
    name: str
    latitude: float
    longitude: float
    utility: str


# All inside CAISO territory; LADWP, SMUD/BANC and IID are separate balancing authorities.
LOCATIONS = (
    Location("oakland", 37.80, -122.27, "PG&E"),
    Location("san_jose", 37.34, -121.89, "PG&E"),
    Location("fresno", 36.74, -119.79, "PG&E"),
    Location("long_beach", 33.77, -118.19, "SCE"),
    Location("riverside", 33.95, -117.40, "SCE"),
    Location("san_diego", 32.72, -117.16, "SDG&E"),
)

# --- Forecast protocol (CLAUDE.md §5) ---
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
ISSUE_HOUR_LOCAL = 9  # simulated issue time on day D
# Demand is usable through the hour ending at this local hour on day D (0 = end of D-1).
DEMAND_CUTOFF_HOUR_LOCAL = 0
SCORING_DELAY_HOURS = 72  # wait for revisions to settle before scoring a target day
FEATURE_LOOKBACK_DAYS = 90  # demand history screened and exposed to a forecast


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    eia_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None

    def require_eia_api_key(self) -> str:
        if self.eia_api_key is None:
            raise RuntimeError(
                "EIA_API_KEY is not set. Register a free key at "
                "https://www.eia.gov/opendata/register.php and add it to .env."
            )
        return self.eia_api_key.get_secret_value()

    def require_database_url(self) -> str:
        if self.database_url is None:
            raise RuntimeError(
                "DATABASE_URL is not set. Run `docker compose up -d` and copy the local "
                "value from .env.example, or use the Supabase connection string."
            )
        return self.database_url.get_secret_value()
