# Paper Trader

A high-risk paper-trading bot for Alpaca. It runs every ~15 minutes while the US market is open, using GitHub Actions. It also has a dashboard you can add to your phone's home screen.

**Paper money only.** `TradingClient(..., paper=True)` is hard-coded, so it cannot trade a live account.

## How it decides
Each run scores ~39 symbols (25 S&P 500 stocks, 11 ETFs, 3 leveraged 3x ETFs) from −1 to +1:

| Signal | Weight | What it measures |
|---|---|---|
| Momentum | 30% | 20- and 60-day return, adjusted for volatility and ranked against the other symbols |
| Trend | 25% | Price vs. its 20/50/200-day averages, and 50-day vs. 200-day |
| History | 20% | The past days that looked like today (trend, RSI band, momentum direction), and what happened over the next 5 days |
| News | 15% | Sentiment of Alpaca news headlines from the last 24h, with newer headlines counting more |
| RSI | 10% | Penalizes overbought stocks and favors dips within an uptrend |

**Rules (high risk):** it holds the top 6 at about 16% each, with leveraged ETFs capped at 15%. It needs a score of at least +0.15 to buy. It sells when any of these happens:
- the score drops below 0
- the symbol falls out of the top 10
- the price is down 8% from entry (stop-loss)
- the price is down 12% from its high since entry (trailing stop)
- a negative news shock

It trims winners above 1.6x their target size and never uses margin. You can change all of this in `trader/config.py`.

## Setup (one time)
1. **Secrets:** go to repo **Settings → Secrets and variables → Actions → New repository secret** and add `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Use the keys from your Alpaca **Paper** dashboard.
2. **Dashboard:** go to **Settings → Pages**, pick *Deploy from a branch*, branch `main`, folder `/docs`.
3. **Test:** go to **Actions → trade → Run workflow**. During market hours it will place its first trades.
4. **Phone:** open `https://<your-username>.github.io/<repo>/` in Safari, tap **Share → Add to Home Screen**.

## Notes
- GitHub's scheduler can run 5–20 minutes late at busy times. That's fine for this strategy.
- Prices come from IEX for the live price and SIP daily bars for history (both free on Alpaca).
- `python -m tests.test_offline` runs a full simulated cycle with fake data and no keys.
