from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

import httpx

from app.providers.base import (
    BorrowDataProvider,
    BorrowSnapshotItem,
    DataSourceError,
    DataSourceUnavailable,
)
from app.utils.datetime import utc_now
from app.utils.hashing import stable_payload_hash
from app.utils.retry import with_backoff

_UPDATED_RE = re.compile(
    r"upd:\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2}):(\d{2}):(\d{2})\s*\(UTC\+0\)",
    re.IGNORECASE,
)
_VALUES_RE = re.compile(
    r"BOR:\s*([0-9.,]+\s*[KMB]?)\s*\|\s*"
    r"REP:\s*([0-9.,]+\s*[KMB]?)\s*\|\s*"
    r"RATIO:\s*([0-9.,]+)",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


class _BbmHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_text: list[str] = []
        self.cards: list[tuple[dict[str, str], str]] = []
        self._card_attrs: dict[str, str] | None = None
        self._card_text: list[str] = []
        self._card_div_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "div" and self._card_attrs is None and "card" in classes:
            self._card_attrs = values
            self._card_text = []
            self._card_div_depth = 1
        elif tag == "div" and self._card_attrs is not None:
            self._card_div_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or self._card_attrs is None:
            return
        self._card_div_depth -= 1
        if self._card_div_depth == 0:
            self.cards.append((self._card_attrs, " ".join(self._card_text)))
            self._card_attrs = None
            self._card_text = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if not stripped:
            return
        self.page_text.append(stripped)
        if self._card_attrs is not None:
            self._card_text.append(stripped)


def _parse_compact_amount(value: str) -> Decimal:
    normalized = value.strip().upper().replace(" ", "").replace(",", ".")
    multiplier = Decimal("1")
    if normalized.endswith("K"):
        multiplier = Decimal("1000")
        normalized = normalized[:-1]
    elif normalized.endswith("M"):
        multiplier = Decimal("1000000")
        normalized = normalized[:-1]
    elif normalized.endswith("B"):
        multiplier = Decimal("1000000000")
        normalized = normalized[:-1]
    try:
        return Decimal(normalized) * multiplier
    except InvalidOperation as exc:
        raise DataSourceUnavailable(f"Invalid BBM amount: {value!r}") from exc


def _parse_source_timestamp(page_text: str, now: datetime) -> datetime:
    match = _UPDATED_RE.search(page_text)
    if match is None:
        raise DataSourceUnavailable("BBM update timestamp was not found")
    day, month_name, hour, minute, second = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        raise DataSourceUnavailable(f"Unknown BBM month: {month_name!r}")
    timestamp = datetime(
        now.year,
        month,
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=UTC,
    )
    if timestamp > now + timedelta(days=1):
        timestamp = timestamp.replace(year=now.year - 1)
    return timestamp


def parse_bbm_html(
    html: str,
    *,
    now: datetime | None = None,
    source_name: str = "bbm.iflint.pro",
) -> list[BorrowSnapshotItem]:
    parser = _BbmHtmlParser()
    parser.feed(html)
    current_time = now or utc_now()
    source_timestamp = _parse_source_timestamp(" ".join(parser.page_text), current_time)
    result: list[BorrowSnapshotItem] = []
    for attrs, card_text in parser.cards:
        symbol = attrs.get("data-symbol", "").strip()
        match = _VALUES_RE.search(card_text)
        if not symbol or match is None:
            continue
        displayed_borrow, displayed_repay, displayed_ratio = match.groups()
        exact_borrow = attrs.get("data-borrow") or displayed_borrow
        try:
            borrow = Decimal(exact_borrow)
            repay = _parse_compact_amount(displayed_repay)
            ratio = Decimal(displayed_ratio.replace(",", "."))
        except InvalidOperation as exc:
            raise DataSourceUnavailable(f"Invalid BBM values for {symbol}") from exc
        payload = {
            "timestamp": source_timestamp.isoformat(),
            "symbol": symbol,
            "borrow_usd": str(borrow),
            "repay_usd": str(repay),
            "ratio": str(ratio),
        }
        result.append(
            BorrowSnapshotItem(
                symbol=symbol,
                borrow_usd=borrow,
                repay_usd=repay,
                ratio=ratio,
                source_timestamp=source_timestamp,
                source_name=source_name,
                raw_payload_hash=stable_payload_hash(payload),
            )
        )
    if not result:
        raise DataSourceUnavailable("BBM page contained no parseable borrow cards")
    return result


class BbmBorrowProvider(BorrowDataProvider):
    """Near-real-time adapter for the server-rendered Binance Borrow Monitor page."""

    def __init__(
        self,
        url: str = "https://bbm.iflint.pro/",
        *,
        timeout: float = 15,
        max_retries: int = 3,
        max_age_minutes: int = 15,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.max_retries = max_retries
        self.max_age = timedelta(minutes=max_age_minutes)
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "MarginAnomalyMonitor/0.1 (+research; read-only)"},
        )
        self._owns_client = client is None
        self._last_source_timestamp: datetime | None = None

    async def fetch_snapshots(self) -> list[BorrowSnapshotItem]:
        async def operation() -> httpx.Response:
            response = await self._client.get(self.url)
            response.raise_for_status()
            return response

        try:
            response = await with_backoff(operation, attempts=self.max_retries)
        except httpx.HTTPError as exc:
            raise DataSourceError(f"BBM request failed: {exc}") from exc

        now = utc_now()
        items = parse_bbm_html(response.text, now=now)
        source_timestamp = items[0].source_timestamp
        age = now - source_timestamp
        if age > self.max_age:
            raise DataSourceUnavailable(
                f"BBM data is stale: last update was {int(age.total_seconds() // 60)} minutes ago"
            )
        if age < timedelta(minutes=-2):
            raise DataSourceUnavailable("BBM update timestamp is unexpectedly in the future")
        if self._last_source_timestamp == source_timestamp:
            return []
        if self._last_source_timestamp and source_timestamp < self._last_source_timestamp:
            raise DataSourceUnavailable("BBM update timestamp moved backwards")
        self._last_source_timestamp = source_timestamp
        return items

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
