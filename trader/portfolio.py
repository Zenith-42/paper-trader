"""Turn scores + current holdings into a list of trades. Pure logic, testable offline."""
from __future__ import annotations

from . import config as C


def target_weight(sym: str) -> float:
    base = C.INVEST_FRACTION / C.MAX_POSITIONS
    cap = C.MAX_WEIGHT_LEVERAGED if sym in C.LEVERAGED_ETFS else C.MAX_WEIGHT
    return min(base, cap)


def decide(scores: dict, positions: dict, equity: float, cash: float, highs: dict) -> list[dict]:
    """
    positions: {sym: {"qty", "value", "entry", "price"}}
    highs: {sym: highest price seen since entry}
    Returns orders: {"side": "sell"|"buy", "symbol", "usd" or "qty"/"all", "why"}
    """
    orders: list[dict] = []
    ranked = sorted(scores, key=lambda s: scores[s]["score"], reverse=True)
    rank = {s: i + 1 for i, s in enumerate(ranked)}

    keep = []
    for sym, pos in positions.items():
        sc = scores.get(sym)
        price = pos["price"]
        hi = max(highs.get(sym, price), price)
        why = None
        if price <= pos["entry"] * (1 - C.STOP_LOSS):
            why = f"Stop-loss: {price / pos['entry'] - 1:+.1%} from entry ${pos['entry']:.2f}"
        elif price <= hi * (1 - C.TRAILING_STOP):
            why = f"Trailing stop: {price / hi - 1:+.1%} from high ${hi:.2f}"
        elif sc is None:
            why = "No longer in universe / no data"
        elif sc["shock"]:
            why = "Negative news shock"
        elif sc["score"] < C.MIN_HOLD_SCORE:
            why = f"Score fell to {sc['score']:+.2f}"
        elif rank[sym] > C.KEEP_RANK:
            why = f"Dropped to rank #{rank[sym]} (keep if top {C.KEEP_RANK})"
        if why:
            orders.append({"side": "sell", "symbol": sym, "all": True, "why": why,
                           "usd": pos["value"], "score": sc["score"] if sc else None})
            cash += pos["value"]
        else:
            keep.append(sym)
            tw = target_weight(sym)
            if pos["value"] > equity * tw * C.TRIM_ABOVE:
                trim = pos["value"] - equity * tw
                if trim >= C.MIN_TRADE_USD:
                    orders.append({"side": "sell", "symbol": sym, "usd": round(trim, 2),
                                   "why": f"Trim winner: {pos['value'] / equity:.0%} of portfolio vs {tw:.0%} target",
                                   "score": sc["score"]})
                    cash += trim

    slots = C.MAX_POSITIONS - len(keep)
    candidates = [s for s in ranked
                  if s not in positions and scores[s]["score"] >= C.MIN_ENTRY_SCORE
                  and not scores[s]["shock"]]
    for sym in candidates[:max(slots, 0)]:
        usd = min(equity * target_weight(sym), cash - equity * (1 - C.INVEST_FRACTION))
        if usd < C.MIN_TRADE_USD:
            break
        orders.append({"side": "buy", "symbol": sym, "usd": round(usd, 2), "score": scores[sym]["score"],
                       "why": f"Rank #{rank[sym]} with score {scores[sym]['score']:+.2f}"})
        cash -= usd

    # Top up kept positions that are well under target (e.g. after a partial fill or drop)
    for sym in keep:
        tw = equity * target_weight(sym)
        gap = tw - positions[sym]["value"]
        avail = cash - equity * (1 - C.INVEST_FRACTION)
        if gap > max(C.MIN_TRADE_USD, 0.35 * tw) and scores[sym]["score"] >= C.MIN_ENTRY_SCORE and avail >= C.MIN_TRADE_USD:
            usd = round(min(gap, avail), 2)
            orders.append({"side": "buy", "symbol": sym, "usd": usd, "score": scores[sym]["score"],
                           "why": f"Top up to target weight ({positions[sym]['value'] / equity:.0%} → {target_weight(sym):.0%})"})
            cash -= usd
    return orders
