"""A quote-only refresh of one ticker, shared by the web's quote job (``webapp.jobs``, Task 10) and ``stock-data
refresh``'s quotes step (docs/superpowers/specs/2026-10-25-universe-automation-design.md, "stock-data refresh").

Yahoo's quote (``YahooHandler.fetch_quote``, one request, with Yahoo's split history) is checked against what is
stored: after a split the stored valuations don't reflect, the price would sit beside them on another basis, which
only a full collection brings over, so the quote isn't saved (BUG-093, BUG-102); any other factor since (a stock
dividend, a spin-off) is stored with the price (``SQLiteStore.upsert_quote``), and the caller values the ticker on
it (BUG-128). The function returns what happened and never raises for Yahoo's failures, which ``fetch_quote``
reports as an ``"error"`` key.
"""
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Any, Dict, List, Optional

from ..exporters.sqlite_store import SQLiteStore
from ..valuation.inputs import _simple_ratio

#: What a refresh did: "stored" (the quote saved), "rebased" (saved with a stock dividend's or a spin-off's factor
#: since the last collection, which the caller values the ticker on), "split" (refused: a split since the last
#: collection, for a full collection to bring over) or "error" (Yahoo's fetch failed).
OUTCOMES = ("stored", "rebased", "split", "error")


@dataclass
class QuoteRefresh:
    """One ticker's refresh: its *outcome* (``OUTCOMES``), why the quote wasn't saved (*message*: Yahoo's error or
    the split refusal), the *splits* behind a refusal or the factors stored with a rebased price, and the *price*
    saved."""

    ticker: str
    outcome: str
    message: Optional[str] = None
    splits: List[Dict[str, Any]] = field(default_factory=list)
    price: Optional[float] = None

    @property
    def saved(self) -> bool:
        """Whether the quote was stored."""
        return self.outcome in ("stored", "rebased")


def _split_name(ratio: float) -> str:
    """A share split by its ratio ("a 5-for-4 split"), any other factor as the price adjustment Yahoo records for a
    spin-off or a merger (BUG-106)."""
    if not _simple_ratio(ratio):
        return f"a {ratio:g} price adjustment (a spin-off or merger)"
    fraction = Fraction(ratio if ratio >= 1 else 1.0 / ratio).limit_denominator(4)
    new, old = (fraction.numerator, fraction.denominator) if ratio >= 1 else (fraction.denominator, fraction.numerator)
    return f"a {new}-for-{old} split"


def describe_splits(splits: List[Dict[str, Any]]) -> str:
    """*splits* (Yahoo's ``{date, ratio}`` records) named, each with its day: "a 10-for-1 split on 2024-07-01"."""
    return ", ".join(f"{_split_name(float(s['ratio']))} on {str(s['date'])[:10]}" for s in splits)


def split_refusal(ticker: str, splits: List[Dict[str, Any]]) -> str:
    """Why a quote after *splits* the stored valuations don't reflect isn't saved (BUG-093, BUG-102)."""
    return (f"Yahoo shows {describe_splits(splits)} that the stored valuations don't reflect, so the quote wasn't "
            f"saved. Collect {ticker} in full to update them.")


def collected_at_now() -> str:
    """A refresh's ``collected_at``: the full pipeline's naive-local format. The column is TEXT-ordered and mixed
    offsets break latest-row resolution: ``StockData.collected_at`` is ``datetime.now()`` (naive local) serialised
    with ``.isoformat()``, and a UTC-aware string here would sort lower than a same-day naive-local one from a full
    collection, so ``Reader.quote`` (MAX collected_at) would keep serving the full run's row instead of this one."""
    return datetime.now().isoformat()


def refresh_quote(handler: Any, store: SQLiteStore, ticker: str, collected_at: Optional[str] = None) -> QuoteRefresh:
    """Fetch *ticker*'s quote with *handler* (``fetch_quote``) and store it in *store* at *collected_at* (now by
    default, ``collected_at_now``), unless Yahoo shows a split since the last collection.

    A split the stored valuations don't reflect (``SQLiteStore.splits_since_collection``) leaves everything as it is:
    the price would sit beside them on another basis, which only a full collection brings over (BUG-093, BUG-102).
    Any other factor since (``factors_since_collection``: a stock dividend, a spin-off) is stored with the price, the
    split history with it, and the outcome is "rebased": the caller values the ticker on it (BUG-128)."""
    quote = handler.fetch_quote(ticker)
    if "error" in quote:
        return QuoteRefresh(ticker, "error", str(quote["error"]))
    splits = quote.get("splits") or []
    factors = store.factors_since_collection(ticker, splits)
    new_splits = store.splits_since_collection(ticker, splits) if factors else []
    if new_splits:
        return QuoteRefresh(ticker, "split", split_refusal(ticker, new_splits), new_splits)
    store.upsert_quote(ticker, quote, collected_at or collected_at_now(), splits=splits if factors else None)
    return QuoteRefresh(ticker, "rebased" if factors else "stored", splits=factors,
                        price=(quote.get("market_data") or {}).get("current_price"))
