> **Validation status (2026-06-13)** — measured on 500 symbols × 1 year of
> verified 1-min data, net of full costs, CPCV temporal folds. See PLAN.md for
> the realignment these results forced.
>
> The strategy engines built for this validation were **removed from the
> codebase on 2026-06-13** after their verdicts were recorded (only the
> pre-existing `orb_vwap` benchmark remains); this table is the record.
>
> | # | Strategy | Validated | Verdict |
> |---|---|---|---|
> | 1 | VWAP+RVOL+Breakout | ✅ (since removed) | gross-positive, **net-negative in every variant** (best: +₹61k gross / −₹84k net; CPCV fail). |
> | 2 | CPR+VWAP Trend Day | ✅ (since removed) | ≈ breakeven gross on indices; re-judge only with a futures cost model (PLAN.md P3). |
> | 4 | RS Leader | ✅ (since removed) | standalone screen rejected (−₹2.7M net); the weak-tape condition survives as a measured *filter* idea. |
> | 5 | ORB Professional | benchmark (`orb_vwap`, kept) | unfiltered: −₹18.9k/yr on one symbol. |
> | 8 | Result-Day Momentum | **next (PLAN.md P1)** | the priority — only family whose per-trade moves clear retail costs. |
> | 3/10 | OI / Confluence | blocked / redesigned | OI needs a real feed; confluence becomes confidence-engine inputs, not a standalone (the "Nifty above VWAP" leg measured *backwards*). |
>
> Key measured lessons: costs (~₹140/₹1L round trip) are the binding
> constraint, not win rate; sub-50% win rates are *expected* at 2R payoffs;
> market-alignment filtering hurt this setup while weak-tape helped.

1. VWAP + Relative Volume + Breakout Strategy
This strategy is built to catch stocks that are already in motion — powered by news, results, or institutional activity — and entering a clean breakout from a compressed range.
VWAP (Volume Weighted Average Price) is the single most important intraday reference level. It tells you the average price at which all transactions have occurred, weighted by volume. Institutions use it as a benchmark — they buy below VWAP and sell above it. When price is above VWAP, it means buyers are in control and the stock is trading at a premium to the day's average. This is your first filter.
Relative Volume (RVOL > 2) means the stock is trading at more than twice its average volume for that time of day. This is crucial — it tells you that the interest in the stock today is genuinely abnormal. Without RVOL confirmation, a breakout can be a false one driven by low participation.
Consolidation for 15–30 minutes after the initial move is what creates the setup. After a big gap-up or news spike, the stock will often pause and form a tight range — this is smart money absorbing supply and retail traders getting shaken out. The longer and tighter this base, the more explosive the eventual breakout.
Entry on breakout candle close means you wait for a full candle to close above the consolidation high — not a wick, not a 1-minute spike, but a confirmed close. This avoids false breakouts.
Stop below the consolidation low is logical structure-based risk management. If price breaks down through the base that formed, the thesis is invalidated.
Target of 1:2 or 1:3 RR — if your stop is ₹5 wide, your minimum target is ₹10 to ₹15 away. This ensures you win even if you're only right 40% of the time.
Why it works on result-day and news-driven stocks: These stocks have genuine catalysts that attract institutional volume. RVOL > 2 is your confirmation that the move is real.

2. CPR + VWAP Trend Day Strategy
This strategy is about identifying trend days before they fully develop — which is where the biggest intraday returns come from.
CPR (Central Pivot Range) is calculated from the previous day's high, low, and close. It gives you three levels: TC (Top Central), Pivot, and BC (Bottom Central). The width of CPR is the key insight here. A narrow CPR means the previous day had a small range — the market was undecided. Historically, narrow CPR days are followed by trend days because price has coiled energy. A wide CPR means the previous day was already volatile — expect chop or mean reversion.
Opening above CPR tells you that the market has immediately taken a bullish stance. This is not just a support/resistance test — it's a directional commitment at the open.
Price above VWAP as the session develops confirms that the buyers are dominating the volume-weighted average. Every pullback to VWAP that holds is another confirmation.
First pullback entry toward VWAP is the professional entry. Amateurs chase the open. Professionals wait for the first retracement back toward VWAP — this gives a far better risk/reward entry with a clear stop.
Stop below VWAP — if VWAP fails to hold as support, the trend day thesis is broken.
Trail with 20 EMA — on trend days, price hugs the 20 EMA on a 5 or 15-minute chart. As long as each candle closes above it, you stay in. The moment the 20 EMA is broken decisively, you exit.
Why Bank Nifty and Nifty specifically: Index futures are the most institutional instruments in Indian markets. CPR is widely tracked by professional traders on these instruments, which creates self-fulfilling support/resistance dynamics.

3. Open Interest Buildup Strategy
OI data is the closest thing to an X-ray of institutional positioning in the F&O market. This strategy follows the money by reading what big players are doing, not just where price is.
Understanding the OI matrix is fundamental:

Price Up + OI Up = Long Buildup — New longs are being added. Bulls are entering fresh positions. This is the most bullish signal. Ride it.
Price Down + OI Up = Short Buildup — Bears are adding fresh short positions. Do not fight this — it signals institutional conviction to the downside.
Price Up + OI Down = Short Covering — Shorts are closing positions, which pushes price up. This can be violent but is less reliable — it's not new buying, just panic closing. The move may exhaust quickly.
Price Down + OI Down = Long Unwinding — Longs are exiting, depressing price. Avoid longs until OI stabilises.

Entry on breakout — you want to combine the long buildup signal with a technical breakout above a key level. OI alone is not enough; you need price confirmation.
Exit when OI starts decreasing — this is the most important and most ignored exit signal. When price is up but OI starts falling, it means the longs who drove the move are now booking profits. The institutional fuel is exhausted. Get out.
Where to find OI data in India: NSE website, Sensibull, Opstra, or your broker's option chain. Look at the total OI in futures, not just options.

4. Relative Strength Leader Strategy
This is a powerful institutional detection strategy. When the broad market is weak or flat but a specific stock keeps making higher highs, it tells you that large players are actively accumulating it despite the headwinds.
The logic: Fund managers and institutions don't wait for the market to be perfect. When they want to build a position in a stock — because of upcoming earnings, a sector rotation thesis, or insider knowledge of a catalyst — they buy systematically regardless of Nifty's direction. This buying shows up as relative outperformance.
The example in the strategy: Nifty at -0.2% but stock at +3%. This is a 3.2% differential. This doesn't happen randomly — it requires active buying. The stock is absorbing selling pressure that would normally push it down.
Entry at intraday high breakout — once you've identified the RS leader, you enter when it clears its intraday high. This is the moment when retail momentum also joins institutional buying.
Stop at VWAP — if the stock falls below VWAP on a day when the market is weak, the institutional support is failing. Exit.
How to screen for RS leaders: On any platform, filter for stocks that are up > 1.5% when Nifty is flat or down. Cross-check with volume above average. Shortlist 2-3 and then apply VWAP + breakout filter.

5. Opening Range Breakout (Professional Version)
The ORB is one of the most well-researched intraday strategies globally. The professional version adds filters that dramatically reduce false breakouts.
Why the first 15 minutes matter: The opening range captures the battle between overnight gap buyers, short-sellers fading the gap, and fresh directional players. By the end of 15 minutes, a range is established — the high and low of that range represent the key levels where buyers and sellers have shown their hand.
The standard ORB failure is entering every breakout of the first 15-minute high/low. Without filters, roughly 50–60% of these are false. The professional filters change this:
RVOL > 2 — if the stock isn't attracting abnormal volume, there's no institutional participation to sustain the move.
Price above VWAP — a breakout above the 15-minute high while below VWAP is a weaker signal. When breakout AND VWAP alignment coincide, the probability increases significantly.
Sector strong — a stock breaking out while its sector is weak is fighting an uphill battle. When the sector is also bullish, there's a macro tailwind reinforcing the individual breakout.
ATR-based target — Average True Range gives you a realistic measure of how far the stock can move in a day. A common approach is 1× to 1.5× the daily ATR as a target from entry. This prevents unrealistic targets on low-volatility stocks.
Avoid weak volume — if the breakout candle itself has below-average volume, it's a trap. Smart money was not behind that move.

6. Volume Profile + VWAP Strategy
Volume Profile shows you where the most trading activity has occurred at each price level over a given period. This reveals institutional zones — areas where large orders were filled and where they are likely to defend their positions.
High Volume Node (HVN) is a price level where an unusually large amount of volume has traded. Think of it as a price zone where many institutional trades occurred. When price pulls back into an HVN, it is entering a zone where institutions previously bought — and they often defend these levels by buying again, creating a natural bounce.
Low Volume Node (LVN) is the opposite — a price zone with very little activity. Price tends to move quickly through LVNs because there's no significant support/resistance there. LVNs are where your targets should be — the market moves fast to the next HVN.
The setup: Price has been above VWAP (bullish bias). It pulls back into an HVN (institutional support zone). A bullish reversal candle forms — a hammer, bullish engulfing, or pin bar. You enter on the next candle's open.
Stop below HVN — if the HVN fails to hold, the institutional support is broken and the probability of a deeper move down increases.
Target at next volume node — use the volume profile to identify where the next significant HVN is above the current price. That's your target, because price often stalls or reverses at these nodes.
Tools: Tradingview's Volume Profile (VPFR or VPSV), Chartink for screening. Zerodha Kite also shows VWAP but not Volume Profile natively.

7. EMA + VWAP + RSI Momentum Strategy
This strategy layers three different types of indicators — trend (EMA), price average (VWAP), and momentum (RSI) — to create a high-probability confluence entry.
20 EMA above 50 EMA establishes the trend on the timeframe you're trading. The 20 EMA responds faster to price changes; the 50 EMA is slower. When the faster EMA is above the slower one, the short-term trend is bullish. This is your structural filter — you only take long entries in this condition.
RSI between 55–70 is the momentum sweet spot. RSI below 50 means momentum is weak. RSI above 70 means the stock is overbought and a pullback is imminent — entering here is chasing. RSI in the 55–70 zone means momentum is strong but not exhausted. This is the zone where breakouts tend to sustain.
Volume spike confirms institutional participation. A breakout without a volume spike is low-conviction.
Entry after a pullback — you wait for the stock to pull back slightly (to the 20 EMA or VWAP), show a rejection, and then resume the breakout. This is far more reliable than buying the initial spike.
Stop at 20 EMA — in strong trending markets, price rarely closes below the 20 EMA during a healthy uptrend. A close below it signals the trend is weakening.
Exit on RSI divergence — when price makes a new high but RSI makes a lower high, it's a bearish divergence. This is an early warning that the momentum is fading even though price looks strong. This is when professionals start exiting.

8. Result-Day Momentum Strategy
Earnings season creates some of the most powerful and cleanest intraday moves of the year. This strategy exploits those moves with precision.
Why results create opportunity: Quarterly earnings releases cause significant repricing of stocks in a short window. A positive surprise compresses years of future expectation change into a single trading session. Institutions who underweighted the stock now scramble to build positions, creating sustained buying pressure.
Positive earnings surprise — the trigger. The actual EPS, revenue, or margins must beat analyst consensus estimates meaningfully. A beat of 5–10%+ on a key metric is significant.
Gap-up opening — confirms the market has already priced in the surprise at the open. The gap represents overnight demand from institutional pre-market orders.
Price holds VWAP after the gap — this is the key filter. After a gap-up, there's always a wave of profit-booking from overnight traders. If the stock absorbs this selling and holds above VWAP, it signals that fresh buyers are continuously stepping in. If it loses VWAP, the gap may fill.
Volume > 5× average — result day moves require massive participation to sustain. 5× volume means the entire market is watching and reacting.
Entry on first consolidation breakout — after the initial gap spike, a consolidation forms (usually within the first 30–45 minutes). This is your ORB setup on result day. The consolidation high becomes the entry trigger.
Trail aggressively — result day moves can go 5–15% in a single session. Don't give back gains by holding with a fixed target. Trail your stop with each new consolidation or use the VWAP as a trailing stop.

9. Sector Rotation Strategy
Money in markets doesn't disappear — it rotates. When one sector falls out of favour, institutional money moves into another. This strategy lets you catch those inflows early.
The concept: Fund managers have mandates to remain invested. When they reduce exposure to, say, IT, they simultaneously increase exposure to Pharma or FMCG. This rotation shows up in the relative performance of sector indices before it becomes obvious in individual stocks.
How to detect rotation: Compare sector index performance daily. If Nifty IT has fallen 2% over the last 5 sessions but Nifty Pharma has risen 3% over the same period, money is rotating from IT to Pharma. The strategy is to trade the receiving sector.
The TCS example in the strategy: Nifty IT index up 2% but TCS up 4%. This means TCS is attracting disproportionate buying within the sector. This could indicate an upcoming catalyst, institutional accumulation, or TCS being the specific vehicle institutions use to gain IT exposure. These are the highest-probability setups.
Entry on consolidation breakout — once you've identified the sector and the outperforming stock, apply standard breakout entry rules. The sector rotation gives you the why; the technical breakout gives you the when.
Screening approach: Use NSE's sector index data daily. Tools like Chartink, Trendlyne, or Nifty Indices page. Look for sectors with 3–5 session relative outperformance vs Nifty.

10. Institutional Confluence Strategy
This is the master strategy — it requires the most conditions to align, but when they do, the probability and reward are maximized. Each filter removes a category of risk.
Price above VWAP → intraday buyers are in control; institutional benchmark is being respected.
Above CPR → the stock has declared its directional bias for the day from the open itself; it's not fighting any structural resistance from yesterday's range.
RVOL > 2 → there is genuinely abnormal institutional interest today; this is not a random drift day.
OI increasing → fresh longs are being added in F&O; this is not just spot market speculation but derivatives positioning, which is how institutions take large positions.
Sector strong → the macro tailwind from the sector is aligned; you're not fighting sector headwinds.
Nifty above VWAP → the broader market is bullish on an intraday basis; even if Nifty reverses, you have a buffer before it hits your stock.
Higher highs and higher lows → the intraday price structure is intact; each pullback is finding buyers at higher levels.
When all seven conditions align, you are effectively trading with: institutional money (RVOL + OI), market alignment (Nifty + Sector), structural bias (CPR + VWAP), and price confirmation (HH/HL structure). The only remaining variable is timing — and that's the consolidation breakout entry.
Stop below structure low — this is the most recent higher low in the HH/HL sequence. A break below it invalidates the entire bullish structure.
Trail using VWAP or 20 EMA — since this strategy fires on the highest-probability days, targets should be extended. Don't book at 1:1. Trail aggressively and let the move run as long as the structure holds.
The practical reality: You will rarely see all seven conditions align in a single session. On most days, 4–5 will align. The more conditions that align, the larger you can size the trade relative to your standard position size.