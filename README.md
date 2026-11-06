# Personal Trading Bot

A local, free, rule-based signal bot. It scans Forex, gold and other instruments
on five timeframes (1m, 3m, 5m, 10m, 15m), and gives one decision per symbol:
**BUY, SELL or NO TRADE**, with the reason, a calculated position size, and the
planned loss and profit after costs.

**It never trades by itself.** With MetaTrader 5 connected you can switch on
**Order buttons** and send a signal's trade to MT5 yourself: one click on
**Place order**, then **Confirm**. Demo accounts only, unless you allow real
money in `settings.json` (section 8). You decide whether to trade. No set of rules can guarantee accuracy or profit,
so treat every signal as a setup to evaluate, not a promise.

---

## 1. Quick start (Windows)

1. Install **Python 3.12** from https://www.python.org/downloads/ and tick
   **"Add python.exe to PATH"** during installation.
2. Double-click **`setup.bat`**. It creates a private Python environment in
   `.venv`, installs the packages, and creates `settings.json`.
3. Double-click **`run_tests.bat`**. All tests should pass.
4. Double-click **`run_dashboard.bat`**. The dashboard opens in your browser.

It starts in **demo mode**, which uses synthetic prices so you can explore the
software safely. Demo prices are random and are not the real market.

## 2. Connect live data (MetaTrader 5, free)

1. Install MetaTrader 5 from your broker and log in, using a **demo account** first.
2. Keep the MT5 terminal open.
3. In the dashboard sidebar choose **MetaTrader 5 (live)**, or set
   `"source": "mt5"` in `settings.json`.
4. If your broker uses different symbol names (for example `EURUSD.m` or
   `XAUUSD+`), set `broker_symbol` for that instrument in `settings.json`.
   Oil and indices especially vary between brokers (`USOIL`, `XTIUSD`, `WTI`...).

With MT5 connected, the bot uses your broker's real contract size, tick value,
volume limits and account equity. Candle times are on broker server time.
The bot detects the server's offset from UTC automatically from a fresh tick.
If it cannot, it stops issuing signals. You can then set
`mt5_server_utc_offset_hours` yourself.

## 3. How a decision is made

Only **closed** candles are used. 3m and 10m candles are built from 1m candles.
Every timeframe check must pass, or the result is NO TRADE.

| Timeframe | Role | BUY conditions (SELL is the mirror image) |
|---|---|---|
| 15m | Main trend | Close above EMA50; EMA20 above EMA50 and rising; higher swing low; price above the last swing low |
| 10m | Trend agreement | Close above EMA20; EMA20 above EMA50; RSI above 50; structure intact |
| 5m | Trade setup | Pullback to EMA20 and resume, **or** breakout-retest, **or** a clean breakout (not overextended); price above EMA50 |
| 3m | Confirmation | Bullish candle closing above EMA20, plus RSI above 50 and rising **or** a bullish rejection wick |
| 1m | Entry timing | Candle closed above the previous candle's high and above EMA20; not a spike candle |

Then the trade plan and risk rules are checked:

- **Stop-loss:** beyond the recent 5m swing, plus a buffer. It must be 0.5 to 3 ATR away.
- **Target:** 2R (twice the stop distance).
- **Room:** an opposing 15m swing level must leave at least 1.5R of room.
- **Spread:** below the instrument maximum, and at most 20% of the stop distance.
- **Size:** calculated from your risk %. If even the minimum size would exceed the
  risk budget, the trade is rejected.
- **Net reward-to-risk:** at least 1.6 after commission and slippage.
- **Daily limits:** daily loss limit, max trades per day, and consecutive losses.
- **Data checks:** stale candles, stale prices, recent gaps and broken candles stop signals.
- **Emergency stop:** if it is on, every symbol shows NO TRADE.

These rules are a starting point to **test**. They are not proven to be profitable.

## 4. Position sizing

```
Risk budget   = Equity × Risk% / 100
Loss per lot  = (stop distance + slippage) × value of 1.0 price move per lot + round-trip commission
Position size = Risk budget / Loss per lot   → rounded DOWN to the broker's volume step
```

Example: equity $1,000, risk 0.25% gives a $2.50 budget. A EURUSD 10-pip stop
is about $100 per lot plus costs, so the bot sizes 0.02 lots.

With small accounts, gold and indices are often **rejected**. The broker's
minimum size (0.01 lot) would risk more than your budget, and that is correct
behaviour. The dashboard shows the exact amount.

## 5. Dashboard tabs

- **Desk**: the **trade box** at the top shows one market (pick it with the buttons at its top):
  the live bid/ask (updated every 3 seconds), and either
  - a live **BUY or SELL** signal with its stop-loss and target on a bar, the money at each, how far
    the price is from each, and a **BUY / SELL** button (with Order buttons switched on), or
  - your **open desk trade** with its live profit, stop-loss, target and a Close trade button, or
  - **No trade yet**, with how many of the five checks pass and why there is no trade.

  The desk scans again 3 seconds after every candle closes. **Pop-ups:** a new signal opens a
  "New signal" window with the stop-loss, target and price; when a desk trade reaches its time limit,
  a "Time to close a trade" window offers Close trade now or Keep it open; when MT5 closes a desk
  trade at its stop-loss or target, a "Trade closed" window shows the result; and when the first half
  of a split trade takes profit, a "First half took profit" window confirms the stop-loss moved to entry.
  A new signal also opens a **full-screen alert on top of everything on your PC**, even when Chrome is minimised
  (section 9).

  **Latest signals** (right column, at the top): the newest BUY or SELL the desk gave, large, with its time,
  strategy, buy/sell price, stop-loss, target and live price, and below it the rest of today's signals. This is
  the same list as the Journal's signal history, followed live: Running (with the live result in R), Target,
  Stop or the time-limit exit. It also shows signals that Last 3 days leaves out: that replay takes one trade at a
  time per market and strategy, as in testing, so a signal that comes while an earlier one is still running is
  only here. While the newest signal is still valid, a green line says until when.

  Below the trade box: every market in one line each, **Open trades** and the **recent signals**, and a
  chart with the full checks for any market. Open trades and recent signals refresh every 5 seconds and show
  the buy/sell price, stop-loss, target and live price. Stop-loss and target are read from MT5, so if you move
  them in MT5 the desk shows the new level, with the old one marked "was". Running signals update their
  result live. All times are on your PC's clock, written like 28-10-2026 5:31pm.

  **Results by day** (below Open trades): pick a day (today or any of the 13 before) and a market, and see how
  many signals that day had, how many **won** (closed in profit), how many **lost** (closed at a loss), how many
  are still running, and the day's result in R and money. Below it, the last two weeks one line per day, with
  a green square for each win and a red one for each loss. These are the same replayed signals as Last 3 days.

  **How many signals to expect:** Gold 5-minute swing is strict on purpose (five timeframes, ADX 20+ and every
  risk rule must agree). On 3 years of gold it gave about 2 signals a week; most 3-day spells had none.
  Gold 3-minute gives about 4.5 a week (about one a trading day), and both together (the default) about 6.4.
  Small accounts get fewer: if even 0.01 lot would risk more than your risk %
  allows, the signal is skipped (about 20,000 USD or more is needed at 0.25% risk).
- **Calculator**: a profit/loss scenario calculator, and a position size calculator for any instrument.
- **Backtest**: runs the same engine on history, with an in-sample/out-of-sample split and walk-forward windows.
- **Journal**: record the trades you actually take. **The daily loss limit and
  trade limits use these records**, so record every trade. It also holds the
  signal history and a **Reproduce** button. The trade list and signal history show **only today's trading
  day**: they start empty again after the market's daily close (the MT5 server's midnight, about 3am in
  Pakistan in winter). Older days are kept, not deleted, because the limits and Reproduce need them; switch on
  **Show older days** to see them.
- **System**: safety status, the settings in use, MT5 account info, and the event log.

## 6. Backtesting on real history

1. Connect MT5 (step 2) and choose MetaTrader 5 in the sidebar.
2. In the **Backtest** tab, pick a symbol and days, then click **Download history from MT5**.
   This saves the history to `data/csv/SYMBOL.csv`.
3. Choose **CSV file** and click **Run backtest**.

What the backtest assumes, conservatively:

- Entries fill at the **next** candle's open. BUY orders fill at the ask, SELL at the bid.
- The spread is never assumed to be lower than the instrument's normal spread. Some demo servers record a
  spread of 0, which would make results look better than real trading.
- Commission and slippage are charged on entries, stops and time exits.
- If one candle touches both the stop and the target, the stop is assumed hit first.
- Positions held into a weekend or data gap take the gap loss.
- No new trades are opened within 30 minutes after a data gap.
- There is a time stop after 240 minutes.

Look at expectancy, profit factor, maximum drawdown, and especially the
**out-of-sample** and **walk-forward** rows. Do not keep tweaking settings until
history looks perfect. That is overfitting, and it usually fails on new data.

Any CSV file with the columns `time, open, high, low, close` (and optionally
`spread` in price units and `volume`) can be placed in `data/csv/`.

## 7. What 3 months of real MT5 data showed (October 2026)

Test data: about 3 months of 1-minute MetaQuotes-Demo history (22 July to 27 October 2026) for EURUSD,
GBPUSD, USDJPY and XAUUSD, with realistic costs. Every change was chosen on the first 60% of the data
and checked on the last 40%, which it was never tuned on.

- **No strategy is 100% accurate.** With a 2:1 target, about 35–45% of trades win, and that is normal.
- **The original rules had no edge before costs** (+0.01R per trade over 879 trades), and they lost
  about 0.1–0.16R per trade after costs. On FX the stop is only 8–13 spreads wide, so costs take a big
  share of every trade.
- **A wider stop buffer (0.5 ATR instead of 0.2) was better in both halves** and is now the default.
- **Gold was the only market that made money in both halves** with the wider buffer: +0.16R per trade
  (104 trades), then +0.10R (72 trades). That is promising, but too few trades to call it proven.
- **Break-even stops, hour blocking and the 1-hour trend filter** cut stop-outs or trades but made overall
  results worse or no better on unseen data. They stay off, but are available in settings.
- **Moving the stop and target closer does not help.** Replaying all 1,037 signals: a target of 0.5x the stop
  wins 55–63% of the time but still loses after costs; a tighter stop is worse in every combination.
- **Running the rules 5x or 15x slower, a 1-hour trend breakout, a London open breakout and 15-minute
  mean reversion** were all tested too. None was reliably profitable across all markets on 3 months of data.
- **Gold is stable across settings:** all 15 nearby stop-buffer/target combinations were profitable, and the
  default (0.5 ATR, 2R) was profitable in all 4 separate time windows (+0.13R per trade, 176 trades).

**What the bot does with this:** only gold (XAUUSD) is ticked by default. Other markets can still be ticked in
the sidebar, but they are **watch only**: their signals are marked Untested, show as a small notice instead of a
pop-up, and have no order button (unless `"allow_untested_markets": true` is set under `"trading"`).

### 7b. What 1.4 years of 5-minute history showed (28 October 2026)

Test data: MT5 5-minute history, May/June 2025 to 27 October 2026, for XAUUSD, EURUSD, GBPUSD and USDJPY.
Same cost model. Judged on the first 60% vs the last 40% of the period, and in 6 equal time windows.
Gold rose from about 3,300 to 5,280 (Feb 2026) and then fell back to about 4,050, so both halves are
very different markets, which makes this a hard test.

- **FX: nothing worked.** Five strategy families with textbook settings (1-hour breakout, 15-minute
  mean reversion, London-open range breakout, New York-open range breakout, 15-minute pullback in a
  1-hour trend) and the bot's own rules on slower candles all lost money on EURUSD, GBPUSD and USDJPY.
- **Gold, other families:** the 1-hour breakout made money only while gold was rising (first 60%), then
  about nothing. Mean reversion and the New York breakout were about break-even. The London breakout
  lost in the first part and made money in the second, so it is not consistent.
- **Gold, the bot's own rules on 5-minute candles ("Gold 5-minute swing" in the sidebar)** were the only
  strategy profitable in both parts: all 20 stop-buffer/target combinations tried made money in both halves.
  Chosen setting, from the middle of that group: stop buffer 0.7 ATR, target 2.5R (changed to 1.0 ATR in 7c).
  - Where the 25/50/75-minute candles start changes results, so the test was repeated for 15 different
    candle start times. Typical result: **+0.16R per trade** (median; worst +0.03, best +0.29), positive in
    all 15. About 170 trades in 17 months (about 10 a month), 41% winners.
  - Usually profitable in 4 of 6 time windows. **It lost in the most recent one (Aug to Oct 2026)** and in the first one.
  - Longest losing run: 8 to 12 trades in a row. Largest drop: about 8 to 18R (2–4.5% of the account at 0.25% risk).
  - Stops are wider in price (median about $25 on gold) and trades last about 9 hours, so position sizes are
    smaller for the same money at risk. With small accounts the minimum lot (0.01) can be too big, and the
    trade is skipped: at 0.25% risk the account needs roughly $20,000 or more.
  - The dashboard uses one fixed candle start time. On this history that one was among the weaker ones:
    175 trades, +0.10R per trade, +4.2% over 17 months at 0.25% risk on a 100,000 account, largest drop 3.4%.
  - With about 170 trades, a +0.16R average could still be partly luck. It is the best-supported option,
    not a proven one.
- **Time limit:** the backtests close a trade after 4 hours (1-minute rules) or 20 hours (swing) if neither
  the stop-loss nor the target is hit. MT5 cannot do this by itself, so the desk shows a "close by" time on
  each signal and open trade and flags it when it passes. Without the time limit, results were similar
  overall (1-minute rules +0.16R instead of +0.13R; swing +0.21R instead of +0.15R, but weaker in the
  second half), so missing it now and then is not a disaster.
- **The 1-minute rules could not be checked on 1.4 years at the time**, because MT5 only gave 3 months of
  1-minute candles. In those 3 months (Jul–Oct 2026) they made +0.13R per trade over 172 trades. On 2.2
  years of 1-minute history they lose money (section 7d).

### 7c. Fewer stop-loss hits (2–3 November 2026)

Question: the stop-loss is hit too often; can technical analysis make more trades reach the target?
(These are 1.4-year results. On 3 years the gains were smaller: see section 7d.)
Data: the same 1.4 years of 5-minute gold, and 3.5 months of 1-minute gold (MT5 gave no older 1-minute
history). Every idea was judged on the first 60% and checked on the last 40%, repeated for 5 candle start
times, in the full backtest with costs.

- **About 1 in 5 trades that hit the full stop-loss had first been 1R in profit.** Those are the trades
  that taking some profit early can save.
- **Filters that did not help:** time of day / trading session, RSI levels, distance from the moving
  average and candle size removed about as many winners as losers. **Stricter trend filters made it worse:**
  ADX 30, 35 or 40 hit the full stop-loss just as often (40–45%), with fewer trades and less profit.
- **What helped on the swing rules:**
  1. **Trend strength:** only trade when ADX(14) on the main-trend candles (75-minute in the swing rules) is
     25 or more. Weak, sideways markets cause many of the quick stop-outs.
  2. **Take half early:** the order goes in as two halves with the same stop-loss. The first half takes its
     profit at 0.5R; the desk then moves the other half's stop-loss to the entry price, and that half stays
     open for the full 2.5R target.
  3. **A little more room:** stop buffer 1.0 ATR instead of 0.7, so ordinary wiggles hit the stop less.

| Gold 5-minute swing (medians of 5 start times) | Won (closed in profit) | Hit the full stop-loss | Average per trade | Largest drop |
|---|---|---|---|---|
| Original (0.7 ATR, no filter, no half) | 41% | 52% | +0.15R | 3.4% (dashboard start time) |
| ADX 25+, half at 1R (2 Nov) | 58% | 40% | +0.17R | 1.3% |
| **ADX 25+, half at 0.5R, buffer 1.0 ATR (now the default)** | **74%** | **25%** | **+0.15R** | **0.8%** |

The default was checked against its neighbours: all 15 combinations of stop buffer 0.8–1.2 ATR and first
half at 0.4–0.6R were profitable in both the first 60% and the last 40% for every start time (19–30% full
stop-losses). It was also profitable in 4 or 5 of 6 time windows, including the most recent (Aug–Oct 2026,
+0.06R per trade, where the 2 Nov setting lost −0.27R). With the dashboard's own start time: 118 trades in
17 months (about 6 a month), 70% won, 27% hit the full stop-loss, +0.13R per trade, +3.6% at 0.25% risk,
largest drop 0.9% of the account. Most winners are small: about 4 in 10 trades take the half at 0.5R and
then close at entry, making about +0.2R after costs.

**Why not 10% stop-losses?** Taking the first half even earlier gets there, but most of the profit goes,
and the most recent data is about break-even (all with stop buffer 1.0 ATR and ADX 25+):

| First half at | Hit the full stop-loss | Average per trade | Last 40% of the data |
|---|---|---|---|
| 0.5R (default) | 25% | +0.15R | +0.19R, positive for all 5 start times |
| 0.3R | 15% | +0.10R | +0.09R, a loss for 1 of 5 |
| 0.2R | 12% | +0.09R | +0.03R, a loss for 1 of 5 |
| 0.15R | 11% | +0.07R | +0.01R, a loss for 1 of 5 |
| 0.1R | 7% | +0.06R | about 0, a loss for 3 of 5 |

Moving the stop to entry early (at 0.3R, without taking profit) also gave about 17% full stop-losses, but
most of those trades then closed at entry for a small loss after costs.

| Gold 1-minute (Jul–Oct 2026) | Trades | Won | Hit the full stop-loss | Total |
|---|---|---|---|---|
| Default (no half) | 170 | 44% | 52% | +26R |
| Half at 1R switched on | 184 | 55% | 43% | +10.6R |
| Half at 0.5R | 208 | 67% | 32% | −1.2R (a loss) |

On the 1-minute rules, taking half early wins more often but cuts the big winners, and total profit falls
(at 0.5R it lost money). So it is **off** for Gold 1-minute and **on** for Gold 5-minute swing. The sidebar
switch (**Take half profit at 0.5R** for swing, **at 1R** for 1-minute) changes it. The ADX filter did not help
the 1-minute rules in the full backtest, so they do not use it.

What this does not change:
- **The stop-loss is still hit on about 1 trade in 4.** No setting removes stop-losses; they are what
  keeps each loss small.
- **More winners does not mean more money.** The average per trade is about the same as before; the gain
  is a smoother account (smaller drops). The swing rules trade less often (about 6 a month).
- **The stop move needs the dashboard open.** If it is closed when the first half takes profit, the other
  half keeps its original stop-loss until you open the dashboard again.

### 7d. Three years of history (3 November 2026)

After "Max bars in chart" was set to Unlimited, MT5 gave 1-minute gold from August 2024 and 5-minute gold
from November 2023. The swing settings were chosen on May 2025–Oct 2026, and the 1-minute rules on
Jul–Oct 2026, so everything before that is history the settings had **never seen**: the fairest test so far.

**Gold 1-minute rules: they lose money.** Aug 2024–Jul 2026: −0.11R per trade over 1,246 trades (−134R).
The whole 2.2 years: −0.07R per trade over 1,430 trades; the largest drop was about 140R (about a third of
the account at 0.25% risk). Taking half early, ADX and a wider stop did not fix it. The good Jul–Oct 2026
result was those months only. **The 1-minute rules are now watch only** (no order buttons, no pop-ups), and
Gold 5-minute swing is the default strategy.

**Gold 5-minute swing (medians of 5 candle start times, Nov 2023–Nov 2026):**

| Setting | Trades | Won | Hit the full stop-loss | Average per trade | Unseen part (Nov 2023–May 2025) | Largest drop |
|---|---|---|---|---|---|---|
| Original (0.7 ATR, no filter, no half) | 353 | 39% | 52% | +0.08R | about 0 | 16R |
| **Default on 3 Nov: ADX 25+, 1.0 ATR, half at 0.5R** (ADX 20 since 4 Nov, see 7e) | 237 | 69% | 28% | +0.07R | about 0 (−0.005R) | 8R |
| Same with the half switched off | 200 | 42% | 48% | +0.15R | +0.11R | 11R |

- Over the 3 years every version was profitable for all 5 candle start times, but only slightly: the
  default made about +16R (about +4% at 0.25% risk); with the half switched off about +31R (about +8%).
- **Taking half early is a trade-off.** It cuts full stop-losses from about half of trades to about a
  quarter, but it also cuts the big winners, and in the unseen period it was about break-even. With the
  half switched off there are more stop-losses but about twice the profit, and it stayed profitable in the
  unseen period (for 3 of 5 candle start times; the median start time made +0.11R per trade).
- The sidebar switch **Take half profit at 0.5R** chooses between the two. It is on by default because
  fewer stop-losses was the goal; switch it off if total profit matters more to you.
- About 7 trades a month. Results this small could still be luck; keep testing on demo.

### 7e. More signals and better performance (4 November 2026)

Question: more signals a day, and more profit. Tested on gold, Aug 2024–Nov 2026 (1-minute history built into
2- and 3-minute candles) and Nov 2023–Nov 2026 (5-minute), 5 candle start times each, costs included.

- **10 or more signals a day is not possible with a profitable version of these rules.** Loosened 1-minute
  rules gave at most about 5 a day and lost about 77% of the account over 2 years at 0.25% risk.
- **2-minute candles:** every setting tried lost money.
- **3-minute candles ("Gold 3-minute", new):** with the plain rules (stop buffer 0.5 ATR, target 2R), about
  4.5 signals a week, roughly 3 times the swing rules. All 6 nearby settings also made a small profit
  overall, but each lost money in 2026 for at least one candle start time. Taking half at 1R cut its
  profit by more than half, so it is off. The trend filter (ADX) made it worse, so it is not used.
- **Swing with ADX 20 instead of 25:** about 20% more signals. With the half switched on it also made more
  profit; with it off, about the same total profit (less per trade, more trades). So ADX 20 is now the
  swing default.

| Aug 2024 – Nov 2026, medians of 5 start times | Signals a week | Won | Full stop-loss | Average per trade | Total | Largest drop |
|---|---|---|---|---|---|---|
| Swing before (ADX 25, half at 0.5R) | 1.6 | 69% | 28% | +0.07R | +14R | 7.5R |
| **Swing now (ADX 20, half at 0.5R)** | 1.9 | 68% | 28% | +0.08R | +19R | 8.7R |
| Swing now, half switched off | 1.6 | 42% | 49% | +0.17R | +33R | 11R |
| **Gold 3-minute (new)** | 4.5 | 40% | 53% | +0.06R | +32R | 19R |

Over the full 3 years the swing now made +0.08R per trade with the half on (+22R) and +0.13R with it off
(+32R), against +0.07R (+16R) and +0.15R (+31R) at ADX 25.

**Which to use:**
- **Most profit per trade:** Gold 5-minute swing with **Take half profit at 0.5R** switched off.
- **Fewest stop-losses:** Gold 5-minute swing with the switch on (the default).
- **More signals:** Gold 3-minute, about one a trading day, with about the same total profit but less per trade,
  more stop-losses and drops about twice as deep. Costs matter more at this speed: a wider spread than the
  test assumed would remove most of the profit.

### 7f. All ten markets (5 November 2026)

Question: more trades from the other markets in the bot (currencies, silver, oil, indices, shares).
Data: 2-3 years of MT5 history for every market (8-10 years for Apple and Microsoft), downloaded
5 November 2026. The same rules, costs and checks as gold: 5 candle start times, first 60% vs last 40%.

| Market | Gold 5-minute swing, half on | Half off | Gold 3-minute rules | Result |
|---|---|---|---|---|
| **Gold (XAUUSD)** | **+0.08R** | **+0.13R** | **+0.06R** | **Passed: order buttons and pop-ups** |
| Silver (XAGUSD) | −0.12R | −0.15R | −0.12R | Lost |
| US500 | −0.07R | −0.16R | −0.07R | Lost |
| NAS100 | −0.05R | −0.14R | +0.02R, losing in 2026 | Lost / break-even |
| Apple (AAPL) | −0.20R | −0.18R | −0.17R | Lost |
| Microsoft (MSFT) | −0.42R | −0.43R | −0.18R | Lost |
| "Oil" (USOIL)* | −0.14R | −0.05R | −0.08R | Lost |
| EURUSD | −0.12R | −0.19R | −0.25R | Lost |
| GBPUSD | −0.14R | −0.19R | −0.17R | Lost |
| USDJPY | −0.16R | −0.12R | −0.21R | Lost |

Average profit per trade, after costs, median of 5 candle start times. Costs assumed were at or above the real
spreads in the data. *On MetaQuotes-Demo the symbol the bot found for oil trades at about 3, so it is not WTI
crude oil; check the oil symbol in MT5's Market Watch before trusting anything about oil.

**Only gold passed.** The other nine markets lost money with both speeds, so they stay watch only (their
signals show as information, without order buttons or pop-ups). Adding them would add trades, but mostly
losing ones. Running the swing and 3-minute rules on gold at the same time gave about 4.9 signals a week
instead of 4.5, because the two compete for the same moments, so that is not worth it either. The way to get
more gold signals is **Gold 3-minute** (about one a trading day).

### 7g. Both strategies at once (5 November 2026)

The 5-minute swing and 3-minute rules see gold on different candle sizes, so they often signal at different
moments. Run side by side, each with its own trade (so up to two gold trades can be open at once):

| Gold, Aug 2024 – Nov 2026, medians of 5 start times | Signals a week | Average per trade | Total | Largest drop | Total ÷ drop |
|---|---|---|---|---|---|
| 5-minute swing only | 1.9 | +0.08R | +19R | 9R | 2.1 |
| 3-minute only | 4.5 | +0.06R | +32R | 19R | 1.7 |
| **Both at once (the default since 5 Nov)** | **6.4** | **+0.07R** | **+53R** | **20R** | **2.6** |

Profitable in both the first 60% and the last 40% for every start time (worst start time +31R in total).
About 28% of the time both trades are open together, so the risk on gold is then twice your risk per trade.
Allowing only one trade at a time instead gave just 4.9 signals a week, because the two compete for the same
moments. At 0.25% risk per trade, +53R is about +13% over 2.2 years; the typical largest drop about 5%.

**How it works on the desk (Strategy: "Gold swing + 3-minute (both)")**
- The trade box has two parts, one per strategy, each with its own signal, BUY/SELL button or open trade.
- Each strategy may hold one gold trade; the desk still allows at most `max_open_positions` (2) desk trades.
- Orders are tagged in MT5 ("Signal desk BUY swing" or "Signal desk BUY medium"), so each trade keeps its own
  time limit (20 hours swing, 12 hours 3-minute) and shows in the right part of the box.
- Pop-ups, Last 3 days, Results by day and Open trades show which strategy each signal or trade came from.
- The **Take half profit at 0.5R** switch is about the 5-minute swing only; the 3-minute rules keep their
  tested choice (no half).
- `run_scanner.bat` runs both too, each scanning when its own candles close.

**Do not trade real money with this bot.** No strategy wins every trade, and none of these results is proof.
Forward-test on a demo account for several weeks first.

**More 1-minute history:** in MT5 open **Tools → Options → Charts**, set **Max bars in chart** to
**Unlimited**, press OK, close and reopen MT5, then use **System → Download history for research**
(or `download_history.bat`). It then tries to save up to 1.5 years of 1-minute and 2 years of 5-minute prices.

## 8. Safety features

- **Emergency stop:** use the red button in the dashboard, or `emergency_stop.bat`.
  To turn it off, use **Resume** or `clear_emergency_stop.bat`. It is stored as a
  file, so it survives restarts.
- **Restart-safe:** signals, the journal and events are kept in `data/trading_bot.sqlite3`.
  The 15-minute repeat-alert protection also survives restarts.
- **Reproducible signals:** every BUY/SELL saves the exact candles and inputs to
  `data/snapshots/`. **Journal → Reproduce** re-runs the engine on them and confirms the result matches.
- **No automatic trading:** the bot never sends an order by itself. Automatic orders are not implemented.
- **Manual orders (Order buttons):** see below.

### Placing an order from the desk

1. Choose **MetaTrader 5 (live)** as the data source, then switch on **Order buttons** in the sidebar.
   The switch is off every time the dashboard opens.
2. In MT5, click the **Algo Trading** button on the toolbar so it turns green. Without it MT5
   refuses orders from Python, and the desk tells you so.
3. When a signal appears, click **Place BUY order** (or SELL), check the details, then click
   **Confirm** within 30 seconds. The order goes to MT5 at market, with the stop-loss and target attached,
   so MT5 closes it at one of them even if the dashboard is closed.

**Half early (on for Gold 5-minute swing, at 0.5R):** Confirm sends **two orders** with the same stop-loss: the
first half with its take-profit at 0.5R, the second half with the full target (comments "Signal desk ... 1/2" and
"2/2"). They count as one trade for the daily and open-trade limits, and the trade box shows them together.
When the first half closes in profit, the desk moves the second half's stop-loss to your entry price
(`"move_stop_after_half": false` under `"trading"` turns this off). If the size is too small to split into
two halves of at least the minimum lot, one order goes in with the full target.

The desk refuses the order, and says why, if:
- the MT5 login is not a demo account and `"allow_real_account": true` is not set under `"trading"` in `settings.json`,
- the emergency stop is on, or today's loss limit, trade count or losing streak is reached,
- a trade from the same strategy is already open on that market, or `max_open_positions` desk trades are open (default 2),
- the prices are more than 2 minutes old, or price has moved more than 25% of the stop distance since the signal,
- MT5's own order check fails (for example not enough margin, or the market is closed).

The size is recalculated from the live price and is never larger than the signal's plan.
Open desk trades are listed on the right with a **Close** button. When MT5 closes a desk trade,
its result is written to the Journal automatically, so the daily loss limit sees it.
The desk only manages its own trades (tagged with magic number 260427); trades you open in MT5
yourself are left alone.

**Please stay on a demo account.** The gold rules were profitable in 3 months of history, which
is promising but not proof. Run them on demo for several weeks before you consider real money.

## 9. Alerts: a full-screen pop-up, and Telegram (optional)

**Full-screen alert (on by default).** Every new signal you can trade (gold, with a tested strategy) covers your
whole screen, on top of every other program, with a sound, even when Chrome is minimised. The screen is green
for BUY and red for SELL and shows the market, the strategy, the entry, stop-loss, target, the half-profit level,
the size and the risk, and how long the signal stays valid (until its next candle closes; then it says the
signal has ended). **Open Signal desk** opens the dashboard to place the order; **Close** or the **Esc** key
closes it. Each signal pops up once. A watch-only signal (only if you switch those on) gets a dark grey screen
with a "Watch only" warning, so it never looks like one you can trade.

The sidebar has an **Alerts** section: switch the pop-up on or off, choose **Full screen** (off = a small
Windows message box instead), and **Also for watch-only markets**. These switches are saved in `settings.json`,
so they stay after a restart. **Show a test alert** shows what it looks like, marked "Test alert: this is not a
real signal". If the full-screen window cannot open on your PC (Python installed without its window
toolkit, Tcl/Tk), the small message box is shown instead and the reason is written to `data/alert_errors.log`.
You can also try it without the dashboard: double-click `test_alert.bat`.

It works while either of these is running on your PC:
- **the dashboard** (`run_dashboard.bat`) open in Chrome. Chrome can put a tab it thinks is unused to sleep,
  and then the dashboard stops scanning. To stop that: in Chrome open **Settings → Performance**, find
  **Always keep these sites active**, click **Add** and type `localhost`.
- **the scanner** (`run_scanner.bat`), a separate black window that scans without Chrome. Keep it open
  (minimised is fine). Use it when the dashboard is closed.

Nothing pops up while the PC is asleep or the screen is locked by sleep. Signals from watch-only markets do not
pop up unless you switch on **Also for watch-only markets**; they still show on the desk and in Latest signals.

**Telegram (optional)**

1. In Telegram, talk to **@BotFather**, create a bot, and copy the token.
2. Send your bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates`
   to find your chat id.
3. In `settings.json`, set `"telegram_enabled": true`, the token and the chat id.
   You can also use the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables.
4. Run `run_scanner.bat`, or keep the dashboard open with auto-refresh on.

## 10. Settings

Edit `settings.json` (it is created from `settings.example.json`). The main values:

| Setting | Default | Meaning |
|---|---|---|
| `risk.risk_per_trade_pct` | 0.25 | % of equity risked per trade (max allowed 2) |
| `risk.daily_loss_limit_pct` | 1.0 | Stop for the day after losing this % |
| `risk.max_trades_per_day` | 5 | |
| `risk.max_consecutive_losses` | 3 | Stop for the day after this many losses in a row |
| `risk.target_reward_risk` | 2.0 | Target distance as a multiple of the stop |
| `risk.min_net_reward_risk` | 1.6 | Minimum R:R after costs |
| `risk.account_equity` | 1000 | Used when MT5 is not connected |
| `instruments.X.enabled` | XAUUSD only | Which symbols are scanned |
| `instruments.X.broker_symbol` | | Your broker's name for the symbol |
| `strategy.stop_buffer_atr` | 0.5 (1.0 in Gold 5-minute swing) | Extra room beyond the 5m swing for the stop (in ATR) |
| `risk.breakeven_at_r` | 0 (off) | Move the stop to the entry price at +X R |
| `risk.blocked_hours` | [] (off) | Broker-clock hours with no new trades, e.g. `[22, 23]` |
| `strategy.use_hourly_trend` | false | Also require the 1-hour trend to agree |
| `strategy.min_trend_strength_atr` | 0 (off) | Minimum gap between the 15m EMAs, in ATR |
| `strategy.min_adx` | 0 (off; 20 in Gold 5-minute swing) | Minimum ADX(14) trend strength on the main-trend candles |
| `risk.partial_at_r` | 0 (off; 0.5 in Gold 5-minute swing) | Take part of the profit at +X R, then stop to entry |
| `risk.partial_fraction` | 0.5 | Share of the position closed at that first take-profit |
| `risk.take_half_at_1r` | null | Set by the sidebar switch: true = half at the strategy's level (1R if it has none), false = off |
| `trading.move_stop_after_half` | true | Move the second half's stop-loss to entry after the first half's profit |
| `trading.allow_untested_markets` | false | Allow order buttons on markets and strategies that lost money in testing |
| `alerts.desktop_popup` | true | Windows pop-up on top of your screen for each new signal you can trade |
| `alerts.popup_watch_only` | false | Also pop up for watch-only signals (untested markets or strategies) |
| `alerts.fullscreen_popup` | true | The pop-up covers the whole screen until you close it; false = small message box |
| `data.strategy_profile` | both | `both` = Gold 5-minute swing and Gold 3-minute together, `swing`, `medium` = Gold 3-minute, `fast` = Gold 1-minute (watch only) |

Strategy parameters (EMA lengths, ATR limits, lookbacks and so on) are in
`config.py` under `StrategySettings`, and can also be overridden in a
`"strategy"` section of `settings.json`. An unknown or out-of-range setting
stops the app with a clear message.

## 11. Project structure

```
personal_trading_bot/
├── app.py              Streamlit dashboard
├── scanner.py          Scan loop (CLI) used by the dashboard and run_scanner.bat
├── engine.py           One evaluate() used by live scanning, backtests and replays
├── config.py           Defaults and settings.json loading
├── market_data.py      MT5 / CSV / demo data connectors (read-only)
├── broker.py           Manual MT5 orders: checks, Place order / Confirm, Close, journal sync
├── profiles.py         Strategy speeds: Gold 5-minute swing (default), Gold 3-minute, Gold 1-minute (watch only)
├── ui.py               Dashboard look: stylesheet and HTML pieces
├── candles.py          1m → 3/5/10/15m candles, closed-candle filter, data validation
├── indicators.py       EMA, RSI, ATR, ADX, causal swing highs/lows, ranges
├── strategy.py         Per-timeframe rules
├── confirmation.py     Combines the five checks into one setup decision
├── risk_manager.py     Risk gates and the emergency stop
├── position_sizing.py  Quantity, trade plan, P&L and scenario maths
├── backtest.py         Event-driven backtester, out-of-sample, walk-forward
├── trade_logger.py     SQLite: signals with snapshots, journal, events
├── alerts.py           Pop-up (full screen or message box) and Telegram alerts
├── fullscreen_alert.py The full-screen alert window for a new signal (--test to try it)
├── signal_status.py    Follows a saved signal on the prices: running, target, stop or time limit
├── diagnose.py         Health check against live MT5 (check_mt5.bat)
├── download_history.py  Saves MT5 1-minute and 5-minute history (download_history.bat)
├── tests/              candles, signals, risk, MT5 connector and order tests
└── data/               database, snapshots, CSV history (created automatically)
```

## 12. Acceptance criteria: status

| Criterion | Status |
|---|---|
| Every signal can be reproduced from saved market data | Built in. Tested by `test_backtest_trade_reproduced_by_live_engine_and_logger` |
| No trade can bypass the risk engine | Built in. BUY/SELL is only produced after every gate passes |
| Quantity and P&L match broker contract specs | Uses MT5 specs when connected. **You should check a few signals against your broker's own calculator** |
| Stale data and connection failures stop trading | Built in. Tested |
| Backtests include costs and avoid future data | Built in. Tested by the look-ahead tests |
| Demo performance across market conditions | **Your step:** run on a demo account for several weeks and record results in the Journal |
| Emergency stop and safe restart | Built in |

## 13. Recommended next steps

1. Connect a **demo** MT5 account. Check that the candles and prices match the MT5 charts.
2. Download 2–3 months of history for EURUSD and XAUUSD and backtest them.
   Read the out-of-sample and walk-forward results honestly.
3. Run the scanner during real sessions for several weeks. Compare each signal's
   entry and exit with prices you could actually have traded at.
4. Only after that, consider whether any changes are justified. Use the Order
   buttons on the demo account only until the demo results are in.
