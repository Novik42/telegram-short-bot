import pytest

from app.utils.symbols import normalize_asset_symbol, to_usdt_pair


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("kaito", "KAITO"), (" KAITOUSDT ", "KAITO"), ("btc-usdt", "BTC")],
)
def test_normalize_asset_symbol(raw: str, expected: str) -> None:
    assert normalize_asset_symbol(raw) == expected


def test_to_usdt_pair() -> None:
    assert to_usdt_pair("lista") == "LISTAUSDT"


def test_invalid_symbol_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_asset_symbol("../KAITO")

