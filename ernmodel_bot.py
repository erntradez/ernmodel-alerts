"""
ernmodel alert bot - sends Telegram messages when a zone forms, a position
opens, and TP / SL is hit. Replicates the "ernmodel - Trend Range Retest +
Bullish FVG (Auto Pair)" TradingView indicator using free Yahoo Finance data.

Setup:
    pip install yfinance pandas requests
    python ernmodel_bot.py --test     (sends a test Telegram message)
    python ernmodel_bot.py            (runs forever; keep the computer on)
"""
import sys
import time
import pandas as pd
import requests

# ---------------------------------------------------------------------
# 1. PUT YOUR TELEGRAM DETAILS HERE
# ---------------------------------------------------------------------
TELEGRAM_TOKEN = "8914828586:AAGmY4mVzaHxRWsmI3cPPP4vLjsMO5xoONc"
TELEGRAM_CHAT_ID = "7702419995"
CHECK_EVERY_SECONDS = 60

# ---------------------------------------------------------------------
# 2. PAIR SETTINGS (same as your TradingView presets)
# ---------------------------------------------------------------------
PAIRS = {
    "XAUUSD": dict(yf="GC=F", tf="15m", minutes=15, rr=5.0, left=51, right=1,
                   maxwait=400, fvg=True, disp=False, mintick=0.01, digits=2),
    "USDJPY": dict(yf="USDJPY=X", tf="15m", minutes=15, rr=6.0, left=51, right=1,
                   maxwait=25, fvg=False, disp=False, mintick=0.001, digits=3),
    "EURJPY": dict(yf="EURJPY=X", tf="15m", minutes=15, rr=5.0, left=49, right=1,
                   maxwait=150, fvg=True, disp=True, mintick=0.001, digits=3),
    "GBPUSD": dict(yf="GBPUSD=X", tf="30m", minutes=30, rr=5.0, left=39, right=1,
                   maxwait=150, fvg=False, disp=False, mintick=0.00001, digits=5),
}

MAX_ZONES = 2
DISP_ATR = 5.0
FVG_PIV_LEN = 5
FVG_IMP_ATR = 1.0


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------
def send(msg):
    print(msg)
    print("-" * 40)
    if TELEGRAM_TOKEN.startswith("PASTE"):
        return
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
    except Exception as e:
        print("Telegram error:", e)


# ---------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------
def atr_series(h, l, c, n=14):
    out = [None] * len(h)
    trs = []
    prev = None
    for i in range(len(h)):
        if i == 0:
            tr = h[0] - l[0]
        else:
            tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
        if i == n - 1:
            prev = sum(trs) / n
            out[i] = prev
        elif i >= n:
            prev = (prev * (n - 1) + tr) / n
            out[i] = prev
    return out


def pivot_high(h, i, left, right):
    c = i - right
    if c - left < 0:
        return None
    v = h[c]
    for j in range(c - left, c):
        if h[j] >= v:
            return None
    for j in range(c + 1, c + right + 1):
        if h[j] > v:
            return None
    return v


def pivot_low(l, i, left, right):
    c = i - right
    if c - left < 0:
        return None
    v = l[c]
    for j in range(c - left, c):
        if l[j] <= v:
            return None
    for j in range(c + 1, c + right + 1):
        if l[j] < v:
            return None
    return v


# ---------------------------------------------------------------------
# Strategy engine (bar-by-bar replay of the Pine logic, touch-zone entry)
# ---------------------------------------------------------------------
def run_engine(df, cfg):
    o = df["Open"].tolist()
    h = df["High"].tolist()
    l = df["Low"].tolist()
    c = df["Close"].tolist()
    times = list(df.index)
    n = len(df)
    L, R = cfg["left"], cfg["right"]
    rr, maxwait = cfg["rr"], cfg["maxwait"]
    atr = atr_series(h, l, c)

    sh1 = sh0 = sl1 = sl0 = None
    shBar = slBar = None
    trend = 0
    fl1 = fl2 = None
    zones = []
    active = False
    trade = {}
    wins = losses = 0
    events = []

    for i in range(n):
        ph = pivot_high(h, i, L, R)
        pl = pivot_low(l, i, L, R)
        if ph is not None:
            sh0, sh1, shBar = sh1, ph, i - R
        if pl is not None:
            sl0, sl1, slBar = sl1, pl, i - R
        if sh0 is not None and sl0 is not None:
            if sh1 > sh0 and sl1 > sl0:
                trend = 1
            elif sh1 < sh0 and sl1 < sl0:
                trend = -1

        fpl = pivot_low(l, i, FVG_PIV_LEN, FVG_PIV_LEN)
        if fpl is not None:
            fl2, fl1 = fl1, fpl

        a = atr[i]
        if a is None or i < 8:
            continue

        # ---- new zones ----
        zTopV = h[i - R]
        zBotV = l[i - R]
        range_ok = True
        if cfg["disp"]:
            range_ok = (sh1 is not None and sl1 is not None and (sh1 - sl1) >= DISP_ATR * a)

        buy_struct = (pl is not None and trend == 1 and sl0 is not None and sl1 > sl0
                      and sh1 is not None and shBar < slBar)
        if range_ok and buy_struct:
            zones.append(dict(dir=1, top=zTopV, bot=zBotV, sl=pl, bar=i, armed=False, kind=0, name="BUY ZONE"))
            events.append(dict(type="zone", time=times[i], dir=1, name="BUY ZONE",
                               top=zTopV, bot=zBotV, sl=pl))
            if len(zones) > MAX_ZONES:
                zones.pop(0)

        sell_struct = (ph is not None and trend == -1 and sh0 is not None and sh1 < sh0
                       and sl1 is not None and slBar < shBar)
        if range_ok and sell_struct:
            zones.append(dict(dir=-1, top=zTopV, bot=zBotV, sl=ph, bar=i, armed=False, kind=0, name="SELL ZONE"))
            events.append(dict(type="zone", time=times[i], dir=-1, name="SELL ZONE",
                               top=zTopV, bot=zBotV, sl=ph))
            if len(zones) > MAX_ZONES:
                zones.pop(0)

        if cfg["fvg"] and i >= 7:
            fvg_ok = (l[i] > h[i - 2] and c[i - 1] > o[i - 1]
                      and (h[i - 1] - l[i - 1]) >= FVG_IMP_ATR * a)
            lowest_f3 = min(l[i - 7:i - 2])
            piv_base = l[i - 2] <= lowest_f3 and l[i - 2] <= l[i - 1]
            hold_hl = fl1 is None or l[i - 2] > fl1
            if fvg_ok and piv_base and hold_hl:
                zones.append(dict(dir=1, top=h[i - 2], bot=l[i - 2], sl=l[i - 2], bar=i,
                                  armed=True, kind=1, name="BULLISH FVG ZONE (A+)"))
                events.append(dict(type="zone", time=times[i], dir=1, name="BULLISH FVG ZONE (A+)",
                                   top=h[i - 2], bot=l[i - 2], sl=l[i - 2]))
                if len(zones) > MAX_ZONES:
                    zones.pop(0)

        # ---- price reaches a zone -> position opens (touch entry) ----
        for zi in range(len(zones) - 1, -1, -1):
            z = zones[zi]
            drop = False
            if i > z["bar"]:
                bull = z["dir"] == 1
                trend_bad = False if z["kind"] == 1 else ((trend != 1) if bull else (trend != -1))
                if bull:
                    invalid = l[i] < z["sl"] or c[i] < z["bot"] or trend_bad
                else:
                    invalid = h[i] > z["sl"] or c[i] > z["top"] or trend_bad
                expired = (i - z["bar"]) > maxwait
                if not z["armed"]:
                    if invalid or expired:
                        drop = True
                    elif (c[i] > z["top"]) if bull else (c[i] < z["bot"]):
                        z["armed"] = True
                else:
                    touched = (l[i] <= z["top"]) if bull else (h[i] >= z["bot"])
                    enter_now = touched and not trend_bad
                    entry = min(z["top"], o[i]) if bull else max(z["bot"], o[i])
                    if enter_now and not active:
                        slp = z["sl"]
                        risk = (entry - slp) if bull else (slp - entry)
                        if risk > cfg["mintick"]:
                            tp = entry + risk * rr if bull else entry - risk * rr
                            active = True
                            trade = dict(bull=bull, entry=entry, sl=slp, tp=tp, bar=i)
                            events.append(dict(type="open", time=times[i], bull=bull,
                                               entry=entry, sl=slp, tp=tp))
                        drop = True
                    elif invalid or expired:
                        drop = True
            if drop:
                zones.pop(zi)

        # ---- manage open position (SL assumed first if both hit) ----
        if active:
            if trade["bull"]:
                sl_hit = l[i] <= trade["sl"]
                tp_hit = i > trade["bar"] and h[i] >= trade["tp"]
            else:
                sl_hit = h[i] >= trade["sl"]
                tp_hit = i > trade["bar"] and l[i] <= trade["tp"]
            if sl_hit or tp_hit:
                if sl_hit:
                    losses += 1
                    events.append(dict(type="sl", time=times[i], bull=trade["bull"], price=trade["sl"]))
                else:
                    wins += 1
                    events.append(dict(type="tp", time=times[i], bull=trade["bull"], price=trade["tp"]))
                active = False

    return dict(events=events, wins=wins, losses=losses, pending=len(zones), active=active)


# ---------------------------------------------------------------------
# Data + messages
# ---------------------------------------------------------------------
def fetch(cfg):
    import yfinance as yf
    df = yf.Ticker(cfg["yf"]).history(period="60d", interval=cfg["tf"])
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("UTC")
    now = pd.Timestamp.now(tz="UTC")
    # drop the candle that is still forming
    df = df[df.index + pd.Timedelta(minutes=cfg["minutes"]) <= now]
    return df


def fmt(ev, pair, cfg):
    d = cfg["digits"]
    head = pair + " (" + cfg["tf"] + ")"
    p = lambda x: ("{:." + str(d) + "f}").format(x)
    when = ev["time"].strftime("%Y-%m-%d %H:%M UTC") + " (candle open)"
    if ev["type"] == "zone":
        return (ev["name"] + " identified - " + head + "\nZone: " + p(ev["bot"]) + " - " + p(ev["top"])
                + "\nSL level: " + p(ev["sl"]) + "\n" + when)
    side = "BUY" if ev.get("bull") else "SELL"
    if ev["type"] == "open":
        return ("POSITION OPENED - " + side + " " + head + "\nEntry " + p(ev["entry"])
                + "\nSL " + p(ev["sl"]) + "\nTP " + p(ev["tp"]) + "\n" + when)
    if ev["type"] == "tp":
        return "TP HIT - " + side + " " + head + "\nTarget reached at " + p(ev["price"]) + "\n" + when
    return "SL HIT - " + side + " " + head + "\nStopped out at " + p(ev["price"]) + "\n" + when


def main():
    if "--test" in sys.argv:
        send("Test message from your ernmodel bot. If you see this, Telegram is set up correctly.")
        return

    last = {}
    send("ernmodel bot started. Watching: " + ", ".join(PAIRS.keys()))
    while True:
        for pair, cfg in PAIRS.items():
            try:
                df = fetch(cfg)
                if len(df) < 300:
                    print(pair, "not enough data yet:", len(df), "candles")
                    continue
                res = run_engine(df, cfg)
                latest = df.index[-1]
                if pair not in last:
                    last[pair] = latest
                    print(pair + ": last 60 days -> wins " + str(res["wins"]) + ", losses " + str(res["losses"])
                          + ", pending zones " + str(res["pending"]) + ", position open: " + str(res["active"]))
                    continue
                for ev in res["events"]:
                    if ev["time"] > last[pair]:
                        send(fmt(ev, pair, cfg))
                last[pair] = latest
            except Exception as e:
                print(pair, "error:", e)
        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main()