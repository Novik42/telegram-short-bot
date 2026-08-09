from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.providers.base import DataSourceUnavailable
from app.providers.borrow_bbm import BbmBorrowProvider, parse_bbm_html

HTML = """
<!doctype html>
<html><body>
<span class="upd-text">upd: 09 Aug 14:03:27 (UTC+0)</span>
<span class="upd-text">total assets: 2</span>
<div class="card" data-symbol="ZBT" data-borrow="1546189.125">
  <div class="header">
    <a>ZBT</a><span>| BOR: 1.5M | REP: 720.4K | RATIO: 2.1</span>
  </div>
</div>
<div class="card" data-symbol="XRP" data-borrow="5461894.445993353">
  <div class="header">
    <a>XRP</a><span>| BOR: 5.5M | REP: 7.1M | RATIO: 0.8</span>
  </div>
</div>
</body></html>
"""


def test_parse_bbm_html_preserves_exact_borrow_and_source_time() -> None:
    items = parse_bbm_html(HTML, now=datetime(2026, 8, 9, 14, 4, tzinfo=UTC))

    assert [item.symbol for item in items] == ["ZBT", "XRP"]
    assert items[0].borrow_usd == Decimal("1546189.125")
    assert items[0].repay_usd == Decimal("720400")
    assert items[0].ratio == Decimal("2.1")
    assert items[0].source_timestamp == datetime(2026, 8, 9, 14, 3, 27, tzinfo=UTC)
    assert len(items[0].raw_payload_hash or "") == 64


@pytest.mark.asyncio
async def test_provider_returns_each_bbm_frame_only_once(monkeypatch) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BbmBorrowProvider(client=client)
    monkeypatch.setattr(
        "app.providers.borrow_bbm.utc_now",
        lambda: datetime(2026, 8, 9, 14, 4, tzinfo=UTC),
    )
    try:
        first = await provider.fetch_snapshots()
        duplicate = await provider.fetch_snapshots()
    finally:
        await client.aclose()

    assert len(first) == 2
    assert duplicate == []


def test_parse_bbm_html_fails_closed_when_layout_changes() -> None:
    with pytest.raises(DataSourceUnavailable, match="no parseable borrow cards"):
        parse_bbm_html(
            '<span>upd: 09 Aug 14:03:27 (UTC+0)</span><div class="unknown"></div>',
            now=datetime(2026, 8, 9, 14, 4, tzinfo=UTC),
        )
