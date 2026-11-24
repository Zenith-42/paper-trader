"""One trading cycle. GitHub Actions runs this every ~15 minutes while the market is open."""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from .signals import features, score_all
from .portfolio import decide

ET = ZoneInfo("America/New_York")
DATA = Path(__file__).resolve().parent.parent / "docs" / "data"


# ---------------- storage helpers ----------------
def load(name, default):
    p = DATA / name
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def save(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, indent=1, default=str))


# ---------------- market data ----------------
def get_closes(data_client, symbols, now):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed, Adjustment

    def fetch(feed, end):
        req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
                               start=now - timedelta(days=C.HISTORY_DAYS), end=end,
                               adjustment=Adjustment.ALL, feed=feed)
        return data_client.get_stock_bars(req).df

    try:  # free plan allows SIP history older than 15 minutes
        df = fetch(DataFeed.SIP, now - timedelta(minutes=16))
    except Exception as e:
        print("SIP bars failed, falling back to IEX:", e)
        df = fetch(DataFeed.IEX, now)

    today = now.astimezone(ET).date()
    closes = {}
    for sym in symbols:
        try:
            sub = df.xs(sym, level=0)["close"]
        except KeyError:
            continue
        idx_dates = [ts.astimezone(ET).date() for ts in sub.index]
        sub = sub[[d < today for d in idx_dates]]  # drop today's partial bar; live price stands in
        closes[sym] = sub.reset_index(drop=True)
    return closes


def get_prices(data_client, symbols):
    from alpaca.data.requests import StockLatestTradeRequest
    from alpaca.data.enums import DataFeed
    res = data_client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX))
    return {s: float(t.price) for s, t in res.items()}


def get_news(news_client, symbols, now):
    from alpaca.data.requests import NewsRequest
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    sia = SentimentIntensityAnalyzer()
    agg = {s: {"w": 0.0, "sum": 0.0, "count": 0, "top": "", "topabs": 0.0} for s in symbols}
    seen = set()
    for i in range(0, len(symbols), 8):
        chunk = symbols[i:i + 8]
        try:
            res = news_client.get_news(NewsRequest(symbols=",".join(chunk), start=now - timedelta(
                hours=C.NEWS_LOOKBACK_HOURS), limit=50, sort="desc", exclude_contentless=False))
            items = res.data.get("news", []) if hasattr(res, "data") else []
        except Exception as e:
            print("news error", chunk, e)
            continue
        for it in items:
            if it.id in seen:
                continue
            seen.add(it.id)
            text = (it.headline or "") + ". " + (it.summary or "")[:240]
            comp = sia.polarity_scores(text)["compound"]
            age_h = max((now - it.created_at).total_seconds() / 3600, 0)
            w = math.exp(-age_h / 12)
            tagged = [s for s in (it.symbols or []) if s in agg]
            if len(tagged) > 4:      # market-wide roundups: weak signal per name
                w *= 0.3
            for s in tagged:
                a = agg[s]
                a["w"] += w
                a["sum"] += w * comp
                a["count"] += 1
                if abs(comp) > a["topabs"]:
                    a["topabs"], a["top"] = abs(comp), it.headline or ""
    return {s: {"score": (a["sum"] / a["w"]) if a["w"] else 0.0, "count": a["count"], "top": a["top"]}
            for s, a in agg.items()}


# ---------------- execution ----------------
def execute(trading, orders, prices, fractionable):
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    results = []
    sells = [o for o in orders if o["side"] == "sell"]
    buys = [o for o in orders if o["side"] == "buy"]
    for i, o in enumerate(sells + buys):
        if i == len(sells) and sells:
            time.sleep(4)  # let sells fill before buying with the freed cash
        sym, side = o["symbol"], o["side"]
        try:
            if side == "sell" and o.get("all"):
                od = trading.close_position(sym)
            else:
                req = dict(symbol=sym, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                           time_in_force=TimeInForce.DAY)
                if fractionable.get(sym, False):
                    req["notional"] = round(o["usd"], 2)
                else:
                    qty = math.floor(o["usd"] / prices[sym])
                    if qty < 1:
                        raise ValueError("amount below one share")
                    req["qty"] = qty
                od = trading.submit_order(MarketOrderRequest(**req))
            results.append({**o, "status": str(getattr(od, "status", "submitted")).split(".")[-1].lower(),
                            "order_id": str(getattr(od, "id", ""))})
        except Exception as e:
            results.append({**o, "status": "error", "error": str(e)[:200]})
    return results


def snapshot_positions(trading):
    pos = {}
    for p in trading.get_all_positions():
        pos[p.symbol] = {"qty": float(p.qty), "value": float(p.market_value),
                         "entry": float(p.avg_entry_price), "price": float(p.current_price)}
    return pos


# ---------------- main cycle ----------------
def main():
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.news import NewsClient

    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        sys.exit("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY secrets")

    trading = TradingClient(key, secret, paper=True)
    clock = trading.get_clock()
    if not clock.is_open and os.environ.get("FORCE_RUN") != "1":
        print("Market closed — nothing to do.")
        return

    now = datetime.now(timezone.utc)
    data_client = StockHistoricalDataClient(key, secret)
    news_client = NewsClient(key, secret)

    account = trading.get_account()
    equity, cash = float(account.equity), float(account.cash)
    positions = snapshot_positions(trading)
    symbols = sorted(set(C.UNIVERSE) | set(positions))

    closes = get_closes(data_client, symbols, now)
    prices = get_prices(data_client, symbols)
    news = get_news(news_client, symbols, now)

    feats = {}
    for s in C.UNIVERSE:
        if s in closes and s in prices:
            f = features(closes[s], prices[s])
            if f:
                feats[s] = f
    scores = score_all(feats, news)

    state = load("state.json", {})
    highs = state.get("highs", {})
    for s, p in positions.items():
        p["price"] = prices.get(s, p["price"])

    orders = decide(scores, positions, equity, cash, highs)

    fractionable = {}
    for o in orders:
        try:
            fractionable[o["symbol"]] = bool(trading.get_asset(o["symbol"]).fractionable)
        except Exception:
            fractionable[o["symbol"]] = False
    results = execute(trading, orders, prices, fractionable) if orders else []

    # ---- refresh and record ----
    time.sleep(3 if orders else 0)
    account = trading.get_account()
    equity, cash = float(account.equity), float(account.cash)
    positions = snapshot_positions(trading)
    new_highs = {s: max(highs.get(s, p["price"]), p["price"]) for s, p in positions.items()}

    stamp = now.astimezone(ET).isoformat(timespec="seconds")
    decisions = load("decisions.json", [])
    for r in results:
        sc = scores.get(r["symbol"], {})
        decisions.insert(0, {"t": stamp, "side": r["side"], "symbol": r["symbol"],
                             "usd": round(r.get("usd", 0), 2), "price": prices.get(r["symbol"]),
                             "status": r["status"], "why": r["why"], "error": r.get("error"),
                             "score": sc.get("score"), "reasons": sc.get("reasons", [])})
    save("decisions.json", decisions[:3000])

    curve = load("equity.json", [])
    curve.append({"t": stamp, "equity": round(equity, 2), "spy": prices.get(C.BENCHMARK)})
    save("equity.json", curve)

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
    save("state.json", {
        "updated": stamp,
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "start_equity": state.get("start_equity", curve[0]["equity"]),
        "highs": new_highs,
        "positions": [{"symbol": s, **{k: round(v, 4) for k, v in p.items()},
                       "pl": round(p["price"] / p["entry"] - 1, 4),
                       "score": scores.get(s, {}).get("score"),
                       "reasons": scores.get(s, {}).get("reasons", [])}
                      for s, p in sorted(positions.items(), key=lambda kv: -kv[1]["value"])],
        "scores": [{"symbol": s, "score": v["score"], "parts": v["parts"], "price": v["price"],
                    "shock": v["shock"], "reasons": v["reasons"]} for s, v in ranked],
        "last_run": {"orders": len(results), "errors": sum(r["status"] == "error" for r in results)},
    })
    print(f"{stamp}: equity ${equity:,.2f}, {len(results)} orders")
    for r in results:
        print(" ", r["side"], r["symbol"], r.get("usd"), r["status"], "-", r["why"])


if __name__ == "__main__":
    main()
