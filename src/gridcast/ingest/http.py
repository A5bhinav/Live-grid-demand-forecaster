"""HTTP GET with retries, shared by the ingest clients."""

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

Params = Mapping[str, str | int | float] | Sequence[tuple[str, str | int | float]]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, max=60),
    reraise=True,
)
def get_json(
    client: httpx.Client, url: str, params: Params, headers: Mapping[str, str] | None = None
) -> dict[str, Any]:
    response = client.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()
