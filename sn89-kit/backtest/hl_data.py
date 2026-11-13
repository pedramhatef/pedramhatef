#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026 the sn89-kit authors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""hl_data.py -- fetch and cache Hyperliquid perp candles for SN89 trade pairs.

Stdlib only, Python 3.10+. Read-only: it only calls the public
``POST https://api.hyperliquid.xyz/info`` endpoint (``candleSnapshot``,
``meta``, ``perpDexs``). No keys, no wallets.

Hyperliquid retention (measured 2026-11-13): the API keeps roughly the last
5,000 candles per (coin, interval), so the finest interval reaches back only so
far:

    1m ~3.5 d   3m ~10.4 d   5m ~17.4 d   15m ~52 d   30m ~104 d   1h ~208 d
    2h/4h/8h/12h/1d: longer

A request is answered with at most ~5,000 candles; ``fetch_candles`` pages
forward from ``start`` until it reaches ``end`` or the API stops returning new
rows. Anything older than retention is simply not served -- build your own
archive by running ``fetch`` on a timer (e.g. 1m every 2 days) if you want 1m
history to accumulate.

Cache format (one CSV per coin+interval, ``<cache>/<coin>__<interval>.csv``,
``:`` in builder-dex coin names becomes ``_``)::

    t,T,o,h,l,c,v,n        t/T = candle open/close time in epoch ms (UTC)

Only CLOSED candles (``T`` < fetch time) are written, so a partial live candle
never poisons the cache. Rows are merged by ``t``; re-running is idempotent.

SN89 pair -> HL coin mapping
----------------------------
Taken from the subnet's own ``config.MARKETS_ORACLE_MAP_HISTORY`` (repo HEAD
2e48ae8), which names the HL coin each pair settles on, plus HL builder-dex
proxies for the FX majors. Crypto on the main dex (``BTCUSD -> BTC``,
``KPEPEUSD -> kPEPE``); stocks/indices/WTI/metals on the ``xyz`` (trade.xyz)
HIP-3 dex (``NVDAUSD -> xyz:NVDA``, ``SP500USD -> xyz:SP500``,
``WTIUSD -> xyz:CL``, ``XAUUSD -> xyz:GOLD``, ``XAGUSD -> xyz:SILVER``).
``EURUSD -> xyz:EUR`` (quoted as EURUSD ~1.10) is a PROXY: SN89 grades FX on
its own (Polygon) quote line, not on HL. ``AUDUSD`` and ``USDCAD`` have no HL
market on any dex -> unsupported.

CLI
---
    python hl_data.py pairs  [--bands sn89_bands.json] [--check]
    python hl_data.py fetch  --pairs BTCUSD,ETHUSD|board|all --intervals 1m,5m,15m,1h
                             [--days 30 | --start 2026-10-01 --end 2026-11-01]
                             [--cache ./hl_cache]
    python hl_data.py coverage [--cache ./hl_cache]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
USER_AGENT = "sn89-kit-backtest/1.0"

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}
# finest first; used by auto interval selection
INTERVAL_ORDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"]

# --- SN89 pair -> Hyperliquid coin ------------------------------------------
_MAIN_DEX = ("AAVE", "ADA", "ALGO", "ARB", "ASTER", "AVAX", "BCH", "BNB", "BTC",
             "CRV", "DOGE", "DOT", "ENA", "ETH", "HYPE", "LINK", "LTC", "NEAR",
             "PUMP", "SOL", "SUI", "TAO", "TRX", "UNI", "WLD", "XMR", "XRP",
             "ZEC", "ZRO")
_XYZ_STOCKS = ("AMD", "COIN", "CRCL", "INTC", "META", "MRVL", "MSTR", "MU",
               "NVDA", "SNDK", "TSLA")

PAIR_TO_COIN: dict[str, str] = {
    **{a + "USD": a for a in _MAIN_DEX},
    "KPEPEUSD": "kPEPE",                      # HL unit = 1000 PEPE
    "XAUUSD": "xyz:GOLD", "XAGUSD": "xyz:SILVER", "WTIUSD": "xyz:CL",
    "SP500USD": "xyz:SP500", "XYZ100USD": "xyz:XYZ100", "EWYUSD": "xyz:EWY",
    **{a + "USD": "xyz:" + a for a in _XYZ_STOCKS},
    # FX proxies (NOT the graded SN89 FX line -- see module docstring)
    "EURUSD": "xyz:EUR", "GBPUSD": "xyz:GBP", "USDJPY": "xyz:JPY",
}

# How faithful the HL series is to what SN89 actually grades for that pair.
#   "graded"  -- SN89 records this very HL coin on its tick line (crypto since
#                2026-08-20, HIP-3 equities/indices/WTI since 2026-10-07)
#   "proxy"   -- HL market tracks the same underlying but SN89 grades another
#                feed (FX: Polygon quotes; metals: see README fidelity table)
PAIR_FIDELITY: dict[str, str] = {p: "graded" for p in PAIR_TO_COIN}
for _p in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD"):
    PAIR_FIDELITY[_p] = "proxy"

UNSUPPORTED: dict[str, str] = {
    "AUDUSD": "no AUD market on any Hyperliquid dex (checked meta for all 10 dexes)",
    "USDCAD": "no CAD market on any Hyperliquid dex (checked meta for all 10 dexes)",
}


def hl_coin(pair: str) -> str | None:
    """HL coin name for an SN89 pair (``BTCUSD`` -> ``BTC``), or None."""
    return PAIR_TO_COIN.get(pair.strip().upper())


# --- HTTP ------------------------------------------------------------------
_last_call = [0.0]
MIN_CALL_SPACING_S = 0.25          # <= 4 requests/second, polite


def post_info(body: dict, retries: int = 5, timeout: float = 30.0):
    """POST a JSON body to the HL info endpoint, paced and retried."""
    data = json.dumps(body).encode()
    delay = 1.0
    for attempt in range(retries):
        wait = _last_call[0] + MIN_CALL_SPACING_S - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        req = urllib.request.Request(
            HL_INFO_URL, data=data,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return json.loads(fh.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt + 1 < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """All candles for ``coin``/``interval`` with open time in [start_ms, end_ms].

    Pages forward around the ~5,000-candle response cap. Returns dicts with
    int t/T/n and float o/h/l/c/v, sorted by t, de-duplicated."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"unknown interval {interval!r}")
    iv = INTERVAL_MS[interval]
    out: dict[int, dict] = {}
    cursor = int(start_ms)
    end_ms = int(end_ms)
    while cursor <= end_ms:
        rows = post_info({"type": "candleSnapshot",
                          "req": {"coin": coin, "interval": interval,
                                  "startTime": cursor, "endTime": end_ms}})
        if not rows:
            break
        new_max = None
        for r in rows:
            t = int(r["t"])
            if t < start_ms or t > end_ms:
                continue
            out[t] = {"t": t, "T": int(r["T"]), "o": float(r["o"]), "h": float(r["h"]),
                      "l": float(r["l"]), "c": float(r["c"]), "v": float(r["v"]),
                      "n": int(r.get("n", 0))}
            new_max = t if new_max is None else max(new_max, t)
        if new_max is None or new_max + iv <= cursor:
            break
        nxt = new_max + iv
        if nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < 4000:          # short page => reached the end of the data
            break
    return [out[t] for t in sorted(out)]


# --- cache -------------------------------------------------------------------
CSV_FIELDS = ["t", "T", "o", "h", "l", "c", "v", "n"]


def cache_path(cache_dir: str, coin: str, interval: str) -> str:
    safe = coin.replace(":", "_").replace("/", "_")
    return os.path.join(cache_dir, f"{safe}__{interval}.csv")


def read_csv_candles(path: str) -> list[dict]:
    """Read a cache CSV (any OHLC CSV with at least t,o,h,l,c; t in epoch ms)."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append({"t": int(float(r["t"])),
                            "T": int(float(r["T"])) if r.get("T") else None,
                            "o": float(r["o"]), "h": float(r["h"]),
                            "l": float(r["l"]), "c": float(r["c"]),
                            "v": float(r["v"]) if r.get("v") else 0.0,
                            "n": int(float(r["n"])) if r.get("n") else 0})
            except (KeyError, ValueError):
                continue
    out.sort(key=lambda d: d["t"])
    return out


def write_csv_candles(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_FIELDS)
        for r in rows:
            w.writerow([r["t"], r.get("T") if r.get("T") is not None else "",
                        repr(r["o"]), repr(r["h"]), repr(r["l"]), repr(r["c"]),
                        repr(r.get("v", 0.0)), r.get("n", 0)])
    os.replace(tmp, path)


def update_cache(cache_dir: str, coin: str, interval: str,
                 start_ms: int, end_ms: int | None = None) -> list[dict]:
    """Fetch [start_ms, end_ms] (default: now), merge into the cache, return all rows.

    Only closed candles are kept. Already-cached spans are not re-requested
    except the tail after the newest cached candle."""
    now_ms = int(time.time() * 1000)
    end_ms = now_ms if end_ms is None else min(int(end_ms), now_ms)
    path = cache_path(cache_dir, coin, interval)
    have = {r["t"]: r for r in read_csv_candles(path)}
    iv = INTERVAL_MS[interval]
    spans = []
    if have:
        lo, hi = min(have), max(have)
        if start_ms < lo:
            spans.append((start_ms, lo - iv))
        if end_ms > hi:
            spans.append((hi + iv, end_ms))
    else:
        spans.append((start_ms, end_ms))
    for a, b in spans:
        if b < a:
            continue
        for r in fetch_candles(coin, interval, a, b):
            if r["T"] is not None and r["T"] >= now_ms:
                continue                      # still-forming candle
            have[r["t"]] = r
    rows = [have[t] for t in sorted(have)]
    write_csv_candles(path, rows)
    return rows


class CandleSeries:
    """Sorted candle arrays for fast window lookups (used by sn89_label)."""

    __slots__ = ("interval_ms", "t", "o", "h", "l", "c", "n", "source")

    def __init__(self, rows: list[dict], interval_ms: int, source: str = ""):
        rows = sorted(rows, key=lambda d: d["t"])
        self.interval_ms = int(interval_ms)
        self.t = [int(r["t"]) for r in rows]
        self.o = [float(r["o"]) for r in rows]
        self.h = [float(r["h"]) for r in rows]
        self.l = [float(r["l"]) for r in rows]
        self.c = [float(r["c"]) for r in rows]
        self.n = [int(r.get("n", 0) or 0) for r in rows]
        self.source = source

    def __len__(self) -> int:
        return len(self.t)

    @property
    def first_ms(self) -> int | None:
        return self.t[0] if self.t else None

    @property
    def last_end_ms(self) -> int | None:
        return self.t[-1] + self.interval_ms if self.t else None

    def covers(self, start_ms: int, end_ms: int) -> bool:
        return bool(self.t) and self.t[0] <= start_ms and self.last_end_ms >= end_ms


def load_series(cache_dir: str, pair_or_coin: str, interval: str) -> CandleSeries:
    """Load a cached series by SN89 pair (``BTCUSD``) or HL coin (``BTC``)."""
    coin = hl_coin(pair_or_coin) or pair_or_coin
    path = cache_path(cache_dir, coin, interval)
    return CandleSeries(read_csv_candles(path), INTERVAL_MS[interval], source=path)


def finest_series(cache_dir: str, pair: str, start_ms: int, end_ms: int,
                  intervals: list[str] | None = None,
                  _memo: dict | None = None) -> CandleSeries | None:
    """The finest cached series that covers [start_ms, end_ms], or None."""
    for iv in (intervals or INTERVAL_ORDER):
        key = (pair, iv)
        if _memo is not None and key in _memo:
            s = _memo[key]
        else:
            s = load_series(cache_dir, pair, iv)
            if _memo is not None:
                _memo[key] = s
        if len(s) and s.covers(start_ms, end_ms):
            return s
    return None


# --- board -------------------------------------------------------------------
def load_board(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _parse_day(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _resolve_pairs(arg: str, bands_path: str | None) -> list[str]:
    if arg in ("board", "all"):
        if arg == "board":
            if not bands_path or not os.path.exists(bands_path):
                raise SystemExit("--pairs board needs --bands sn89_bands.json")
            pairs = sorted(load_board(bands_path)["bands"])
        else:
            pairs = sorted(PAIR_TO_COIN)
    else:
        pairs = [p.strip().upper() for p in arg.split(",") if p.strip()]
    return pairs


def cmd_pairs(a) -> int:
    pairs = (_resolve_pairs("board", a.bands) if a.bands and os.path.exists(a.bands)
             else sorted(set(PAIR_TO_COIN) | set(UNSUPPORTED)))
    live = None
    if a.check:
        live = set()
        for dex in ("", "xyz"):
            m = post_info({"type": "meta", "dex": dex} if dex else {"type": "meta"})
            for u in m.get("universe", []):
                if not u.get("isDelisted"):
                    live.add(u["name"])
    for p in pairs:
        coin = hl_coin(p)
        if coin is None:
            print(f"{p:11s} UNSUPPORTED  {UNSUPPORTED.get(p, 'no mapping')}")
            continue
        flag = ""
        if live is not None:
            flag = "listed" if coin in live else "NOT LISTED"
        print(f"{p:11s} {coin:12s} {PAIR_FIDELITY.get(p, ''):7s} {flag}")
    return 0


def cmd_fetch(a) -> int:
    pairs = _resolve_pairs(a.pairs, a.bands)
    now_ms = int(time.time() * 1000)
    if a.start:
        start_ms = _parse_day(a.start)
        end_ms = _parse_day(a.end) + 86_400_000 if a.end else now_ms
    else:
        start_ms = now_ms - int(a.days * 86_400_000)
        end_ms = now_ms
    ivs = [x.strip() for x in a.intervals.split(",") if x.strip()]
    for p in pairs:
        coin = hl_coin(p)
        if coin is None:
            print(f"skip {p}: {UNSUPPORTED.get(p, 'no HL mapping')}", file=sys.stderr)
            continue
        for iv in ivs:
            try:
                rows = update_cache(a.cache, coin, iv, start_ms, end_ms)
            except Exception as e:  # noqa: BLE001 -- report and continue
                print(f"{p} {coin} {iv}: ERROR {e}", file=sys.stderr)
                continue
            if rows:
                f = datetime.fromtimestamp(rows[0]["t"] / 1000, tz=timezone.utc)
                l = datetime.fromtimestamp(rows[-1]["t"] / 1000, tz=timezone.utc)
                print(f"{p:11s} {coin:12s} {iv:4s} {len(rows):6d} rows "
                      f"{f:%Y-%m-%d %H:%M} -> {l:%Y-%m-%d %H:%M} UTC")
            else:
                print(f"{p:11s} {coin:12s} {iv:4s} no data")
    return 0


def cmd_coverage(a) -> int:
    if not os.path.isdir(a.cache):
        print("no cache dir", a.cache)
        return 1
    for fn in sorted(os.listdir(a.cache)):
        if not fn.endswith(".csv"):
            continue
        rows = read_csv_candles(os.path.join(a.cache, fn))
        if not rows:
            continue
        f = datetime.fromtimestamp(rows[0]["t"] / 1000, tz=timezone.utc)
        l = datetime.fromtimestamp(rows[-1]["t"] / 1000, tz=timezone.utc)
        print(f"{fn:32s} {len(rows):7d} {f:%Y-%m-%d %H:%M} -> {l:%Y-%m-%d %H:%M}")
    return 0


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", default=os.path.join(here, "hl_cache"))
    ap.add_argument("--bands", default=os.path.join(here, "sn89_bands.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pairs", help="show SN89 pair -> HL coin mapping")
    p.add_argument("--check", action="store_true", help="verify listing via HL meta")
    p.set_defaults(fn=cmd_pairs)
    p = sub.add_parser("fetch", help="fetch + cache candles")
    p.add_argument("--pairs", default="board",
                   help="comma list, 'board' (sn89_bands.json) or 'all' mapped pairs")
    p.add_argument("--intervals", default="1m,5m,15m,1h")
    p.add_argument("--days", type=float, default=30.0)
    p.add_argument("--start", help="YYYY-MM-DD (UTC); overrides --days")
    p.add_argument("--end", help="YYYY-MM-DD (UTC, inclusive)")
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("coverage", help="list cached series and their spans")
    p.set_defaults(fn=cmd_coverage)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
