import re

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def normalize_asset_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("-", "").replace("_", "")
    if normalized.endswith("USDT"):
        normalized = normalized[:-4]
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ValueError(f"Invalid asset symbol: {symbol!r}")
    return normalized


def to_usdt_pair(symbol: str) -> str:
    return f"{normalize_asset_symbol(symbol)}USDT"

