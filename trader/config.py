"""All tunable settings live here. Change these, not the logic."""

# ---- Universe: high-beta S&P 500 names + ETFs (incl. 3x leveraged) ----
STOCKS = [
    "NVDA", "AMD", "AVGO", "MU", "ANET", "MSFT", "AAPL", "AMZN", "META",
    "GOOGL", "TSLA", "NFLX", "CRM", "ORCL", "PLTR", "UBER", "COIN",
    "JPM", "GS", "LLY", "UNH", "XOM", "CAT", "GE", "COST",
]
ETFS = ["SPY", "QQQ", "IWM", "SMH", "XLK", "XLF", "XLE", "XLV", "ARKK", "GLD", "TLT"]
LEVERAGED_ETFS = ["TQQQ", "SOXL", "UPRO"]  # 3x daily leverage: very volatile
UNIVERSE = STOCKS + ETFS + LEVERAGED_ETFS
BENCHMARK = "SPY"

# ---- Risk profile: HIGH ----
MAX_POSITIONS = 6            # concentrated portfolio
KEEP_RANK = 10               # hold an existing name while it stays in the top 10
INVEST_FRACTION = 0.96       # keep ~4% cash buffer, never use margin
MAX_WEIGHT = 0.20            # cap any single stock at 20% of equity
MAX_WEIGHT_LEVERAGED = 0.15  # cap each 3x ETF at 15%
MIN_ENTRY_SCORE = 0.15       # composite score needed to open a position
MIN_HOLD_SCORE = 0.0         # sell when score drops below this
STOP_LOSS = 0.08             # sell if down 8% from entry
TRAILING_STOP = 0.12         # sell if down 12% from highest price since entry
NEWS_SHOCK = -0.45           # avg headline sentiment at/below this (2+ headlines) forces exit
TRIM_ABOVE = 1.6             # trim a winner only once it's 1.6x its target weight
MIN_TRADE_USD = 500

# ---- Signal weights (sum to 1) ----
WEIGHTS = {
    "momentum": 0.30,
    "trend": 0.25,
    "history": 0.20,
    "news": 0.15,
    "rsi": 0.10,
}

# ---- Data ----
HISTORY_DAYS = 420           # calendar days of daily bars to pull
FORWARD_DAYS = 5             # horizon for "what happened next" historical stats
MIN_ANALOGS = 15             # need this many similar past setups to trust history
NEWS_LOOKBACK_HOURS = 24
