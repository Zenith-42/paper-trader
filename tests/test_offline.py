"""End-to-end dry run with fake Alpaca clients and synthetic prices (no network).
Run:  python -m tests.test_offline   (writes sample data into docs/data for previewing)"""
from __future__ import annotations

import os
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np
import pandas as pd

from trader import config as C
from trader import run as R

rng = np.random.default_rng(7)
NOW = datetime.now(timezone.utc)
DRIFT = {s: rng.normal(0.0006, 0.0012) for s in C.UNIVERSE}
VOL = {s: (0.035 if s in C.LEVERAGED_ETFS else rng.uniform(0.01, 0.03)) for s in C.UNIVERSE}
PATHS = {s: 100 * np.exp(np.cumsum(rng.normal(DRIFT[s], VOL[s], 300))) for s in C.UNIVERSE}


class FakeTrading:
    def __init__(self, *a, **k):
        self.cash, self.pos = 100_000.0, {}
        self.price = {s: PATHS[s][-1] for s in C.UNIVERSE}

    def get_clock(self):
        return types.SimpleNamespace(is_open=True)

    def get_account(self):
        eq = self.cash + sum(q * self.price[s] for s, (q, _) in self.pos.items())
        return types.SimpleNamespace(equity=eq, cash=self.cash)

    def get_all_positions(self):
        return [types.SimpleNamespace(symbol=s, qty=q, market_value=q * self.price[s],
                                      avg_entry_price=e, current_price=self.price[s])
                for s, (q, e) in self.pos.items()]

    def get_asset(self, s):
        return types.SimpleNamespace(fractionable=s not in C.LEVERAGED_ETFS)

    def close_position(self, s):
        q, _ = self.pos.pop(s)
        self.cash += q * self.price[s]
        return types.SimpleNamespace(status="filled", id=uuid.uuid4())

    def submit_order(self, req):
        s, p = req.symbol, self.price[req.symbol]
        q = req.qty if req.qty else req.notional / p
        sign = 1 if str(req.side).endswith("BUY") or str(req.side) == "buy" else -1
        oq, oe = self.pos.get(s, (0, p))
        nq = oq + sign * q
        self.pos[s] = (nq, (oq * oe + q * p) / nq if sign > 0 else oe)
        self.cash -= sign * q * p
        return types.SimpleNamespace(status="filled", id=uuid.uuid4())


class FakeData:
    def __init__(self, *a, **k): pass

    def get_stock_bars(self, req):
        days = pd.bdate_range(end=NOW.date() - timedelta(days=1), periods=299, tz="UTC")
        rows = [(s, d, PATHS[s][i]) for s in req.symbol_or_symbols for i, d in enumerate(days)]
        df = pd.DataFrame(rows, columns=["symbol", "timestamp", "close"]).set_index(["symbol", "timestamp"])
        return types.SimpleNamespace(df=df)

    def get_stock_latest_trade(self, req):
        return {s: types.SimpleNamespace(price=PATHS[s][-1]) for s in req.symbol_or_symbols}


class FakeNews:
    def __init__(self, *a, **k): pass

    def get_news(self, req):
        syms = req.symbols.split(",")
        heads = ["{} beats estimates, raises guidance", "{} shares slump after downgrade",
                 "{} announces new product launch", "Regulators probe {} over lawsuit"]
        items = [types.SimpleNamespace(id=f"{s}{i}", headline=heads[(i + len(s)) % 4].format(s), summary="",
                                       symbols=[s], created_at=NOW - timedelta(hours=i * 3))
                 for s in syms for i in range(rng.integers(0, 3))]
        return types.SimpleNamespace(data={"news": items})


def run_once():
    os.environ.update(APCA_API_KEY_ID="x", APCA_API_SECRET_KEY="y")
    with mock.patch("alpaca.trading.client.TradingClient", lambda *a, **k: FAKE), \
         mock.patch("alpaca.data.historical.StockHistoricalDataClient", FakeData), \
         mock.patch("alpaca.data.historical.news.NewsClient", FakeNews), \
         mock.patch("time.sleep", lambda *_: None):
        R.main()


FAKE = FakeTrading()

if __name__ == "__main__":
    for f in ("state.json", "equity.json", "decisions.json"):
        (R.DATA / f).unlink(missing_ok=True)
    run_once()
    # simulate a stop-loss: crash the biggest holding 15%
    big = max(FAKE.pos, key=lambda s: FAKE.pos[s][0] * FAKE.price[s])
    FAKE.price[big] *= 0.85
    PATHS[big][-1] *= 0.85
    run_once()
    import json
    st = json.loads((R.DATA / "state.json").read_text())
    dec = json.loads((R.DATA / "decisions.json").read_text())
    assert len(st["positions"]) <= C.MAX_POSITIONS, st["positions"]
    assert st["cash"] >= -1, "used margin"
    assert any(d["symbol"] == big and d["side"] == "sell" for d in dec), "stop-loss did not fire"
    print("OK:", len(st["positions"]), "positions,", len(dec), "decisions, stop-loss fired on", big)
