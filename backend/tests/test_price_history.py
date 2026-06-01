"""PriceHistory — bounded, in-memory recent-mid series per market."""
from __future__ import annotations

from src.services.price_history import PriceHistory


def test_records_in_order():
    ph = PriceHistory()
    for mid in (30, 35, 40, 47):
        ph.record("TIE", mid)
    mids = [m for _, m in ph.series("TIE")]
    assert mids == [30, 35, 40, 47]  # oldest first


def test_caps_at_maxlen():
    ph = PriceHistory(max_samples=3)
    for mid in (10, 20, 30, 40, 50):
        ph.record("X", mid)
    mids = [m for _, m in ph.series("X")]
    assert mids == [30, 40, 50]  # oldest evicted


def test_unknown_ticker_empty():
    assert PriceHistory().series("NOPE") == []


def test_mids_stay_integer_cents():
    ph = PriceHistory()
    ph.record("X", 47)
    (_, mid), = ph.series("X")
    assert isinstance(mid, int)


def test_retain_only_drops_absent_tickers():
    """The bounded-growth guard: tickers not in the subscribed set are pruned."""
    ph = PriceHistory()
    ph.record("A", 50)
    ph.record("B", 50)
    ph.record("C", 50)
    ph.retain_only({"A", "C"})
    assert ph.series("B") == []
    assert [m for _, m in ph.series("A")] == [50]
    assert [m for _, m in ph.series("C")] == [50]


def test_retain_only_empty_set_clears_all():
    ph = PriceHistory()
    ph.record("A", 50)
    ph.retain_only(set())
    assert ph.series("A") == []
