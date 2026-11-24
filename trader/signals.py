"""Pure signal math. No network calls here, so it can be tested offline."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from . import config as C


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _rsi_bucket(v: float) -> int:
    for i, edge in enumerate((30, 45, 55, 70)):
        if v < edge:
            return i
    return 4


def features(close: pd.Series, price: float) -> dict | None:
    """Indicators using daily closes plus the live price as 'today'."""
    s = pd.concat([close.astype(float), pd.Series([price])], ignore_index=True)
    if len(s) < 210:
        return None
    sma20, sma50, sma200 = (s.rolling(n).mean().iloc[-1] for n in (20, 50, 200))
    sma50_s = s.rolling(50).mean()
    r = rsi(s)
    ret20 = price / s.iloc[-21] - 1
    ret60 = price / s.iloc[-61] - 1
    vol20 = s.pct_change().iloc[-20:].std() * math.sqrt(252)

    # Historical analogs: past days with the same setup -> what happened next?
    ret20_s = s / s.shift(20) - 1
    state = pd.DataFrame({
        "above50": s > sma50_s,
        "rsi_b": r.map(_rsi_bucket),
        "mom_up": ret20_s > 0,
    })
    fwd = s.shift(-C.FORWARD_DAYS) / s - 1
    now = state.iloc[-1]
    past = state.iloc[50:-C.FORWARD_DAYS]
    mask = (past == now).all(axis=1)
    outcomes = fwd.iloc[50:-C.FORWARD_DAYS][mask].dropna()

    return {
        "price": price, "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "ret20": ret20, "ret60": ret60, "rsi": float(r.iloc[-1]), "vol20": vol20,
        "analog_n": int(len(outcomes)),
        "analog_mean": float(outcomes.mean()) if len(outcomes) else 0.0,
        "analog_win": float((outcomes > 0).mean()) if len(outcomes) else 0.5,
    }


def score_all(feats: dict[str, dict], news: dict[str, dict]) -> dict[str, dict]:
    """Combine features into a composite score in roughly [-1, 1] with reasons."""
    # Cross-sectional momentum rank (risk-adjusted)
    mom_raw = {s: (0.5 * f["ret20"] + 0.5 * f["ret60"]) / max(f["vol20"], 0.10)
               for s, f in feats.items()}
    ranked = sorted(mom_raw, key=mom_raw.get)
    n = max(len(ranked) - 1, 1)
    mom_rank = {s: 2 * i / n - 1 for i, s in enumerate(ranked)}

    out = {}
    for s, f in feats.items():
        p = f["price"]
        t = sum([p > f["sma20"], p > f["sma50"], p > f["sma200"], f["sma50"] > f["sma200"]])
        trend = (t - 2) / 2

        if f["analog_n"] >= C.MIN_ANALOGS:
            hist = 0.5 * np.clip((f["analog_win"] - 0.5) * 4, -1, 1) \
                 + 0.5 * np.clip(f["analog_mean"] / 0.02, -1, 1)
        else:
            hist = 0.0

        r = f["rsi"]
        if r > 80:
            rsi_s = -1.0
        elif r > 70:
            rsi_s = -0.4
        elif r < 25 and trend > 0:
            rsi_s = 1.0          # oversold dip inside an uptrend
        elif r < 30:
            rsi_s = -0.3         # falling knife in a downtrend
        else:
            rsi_s = 0.2 if 50 <= r <= 65 else 0.0

        nw = news.get(s, {"score": 0.0, "count": 0, "top": ""})
        news_s = float(np.clip(nw["score"] * 1.5, -1, 1)) if nw["count"] else 0.0

        parts = {"momentum": mom_rank[s], "trend": trend, "history": float(hist),
                 "news": news_s, "rsi": rsi_s}
        total = sum(C.WEIGHTS[k] * v for k, v in parts.items())
        shock = nw["count"] >= 2 and nw["score"] <= C.NEWS_SHOCK

        reasons = [
            f"20d {f['ret20']:+.1%}, 60d {f['ret60']:+.1%} (momentum rank {mom_rank[s]:+.2f})",
            f"trend {t}/4 (price vs 20/50/200-day avg)",
            f"RSI {r:.0f}",
            (f"history: {f['analog_n']} similar setups, {f['analog_win']:.0%} rose, "
             f"avg {f['analog_mean']:+.2%} over next {C.FORWARD_DAYS}d")
            if f["analog_n"] >= C.MIN_ANALOGS else "history: too few similar setups",
            (f"news {nw['score']:+.2f} from {nw['count']} headlines" +
             (f" — \"{nw['top'][:90]}\"" if nw["top"] else "")) if nw["count"] else "news: none in 24h",
        ]
        out[s] = {"score": round(float(total), 4), "parts": {k: round(float(v), 3) for k, v in parts.items()},
                  "shock": shock, "reasons": reasons, "price": p}
    return out
