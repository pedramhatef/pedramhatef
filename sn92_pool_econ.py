#!/usr/bin/env python3
"""SN92 (REAX) per-pool pay tally + rent check. Read-only: no wallet, no signing, no transactions.

Answers the one number the public kit cannot: what one hotkey slot is paid per day in each inference pool
(s1-pro, s1-fast, s1-lite, s1-cpu), right now, and whether a rented card covers it.
It also shows the status of one UID (--uid).

Pool of a UID = its on-chain commitment:
  reax-s1/1:<pool>:<manifest_id>               one pool
  reax-s1m/1:<pool>:<id>,<pool>:<id>[,...]     several pools on one hotkey (listed separately; pay cannot be split)
Current manifest ids (kit 1.2.0rc11.post8.1): s1-fast ff6e01b983d48024, s1-pro 3f3558e583b0a1d6 (mammoth-1),
s1-lite 8c7297e14e5e89bf, s1-cpu f2c9981f0766f2a6.

Usage (any venv with the bittensor SDK, e.g. ~/vast-venv):
  python3 sn92_pool_econ.py --tao-usd 213 --uid <your uid> --rent 3090=0.13,4090=0.30,5090=0.38
  python3 sn92_pool_econ.py --pool-usd-day 15200 ...   # skip the on-chain $ estimate, use your own anchor
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict

NETUID = 92
CURRENT = {"s1-fast": "ff6e01b983d48024", "s1-pro": "3f3558e583b0a1d6",
           "s1-lite": "8c7297e14e5e89bf", "s1-cpu": "f2c9981f0766f2a6"}
POOLS = ("s1-pro", "s1-fast", "s1-lite", "s1-cpu")
DR_POOLS = {"s1-lite", "s1-cpu"}  # per-coldkey diminishing returns (tiers.py COLDKEY_DR_POOLS)
RE_ONE = re.compile(r"reax-s1/1:(s1-[a-z]+):([0-9a-f]{16})")
RE_MULTI = re.compile(r"reax-s1m/1:((?:s1-[a-z]+:[0-9a-f]{16},?)+)")


# ----------------------------------------------------------------------------------------------- chain access
def _first(*fns):
    err = None
    for fn in fns:
        try:
            v = fn()
            if v is not None:
                return v
        except Exception as e:  # noqa: BLE001 - SDK versions differ; try the next spelling
            err = e
    if err:
        print(f"  (lookup failed: {err!r})", file=sys.stderr)
    return None


def _f(x) -> float:
    """Number from a float/int or an SDK Balance (TAO or subnet alpha; .tao raises on alpha in SDK 11)."""
    if isinstance(x, (int, float)):
        return float(x)
    for attr in ("tao", "alpha"):
        try:
            return float(getattr(x, attr))
        except Exception:  # noqa: BLE001 - wrong unit or missing attribute: try the next one
            continue
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return 0.0


def _commit_text(c) -> str:
    """Plaintext of an SDK NeuronCommitment (value/data and hex Raw fields), or a plain string."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    parts = []
    v = getattr(c, "value", None)
    try:
        v = v() if callable(v) else v
    except Exception:  # noqa: BLE001
        v = None
    for x in (v, getattr(c, "data", None)):
        if isinstance(x, str):
            parts.append(x)
    fields = getattr(c, "fields", None)
    if isinstance(fields, (list, tuple)):
        for entry in fields:
            if isinstance(entry, dict):
                for val in entry.values():
                    if isinstance(val, str):
                        try:
                            parts.append(bytes.fromhex(val.removeprefix("0x")).decode("utf-8", "replace"))
                        except ValueError:
                            pass
    parts.append(str(c))
    return " ".join(parts)


def fetch(network: str) -> dict:
    import bittensor as bt

    st = bt.Subtensor(network=network) if hasattr(bt, "Subtensor") else bt.subtensor(network=network)
    mg = _first(lambda: st.subnets.metagraph(NETUID, commitments=True), lambda: st.metagraph(NETUID))
    if mg is None:
        sys.exit("could not read the metagraph")
    commits = {}
    if not any(getattr(n, "commitment", None) for n in mg.neurons):
        commits = _first(lambda: st.get_all_commitments(NETUID), lambda: st.commitments.get_all_commitments(NETUID)) or {}
    rows = []
    for n in mg.neurons:
        uid = int(n.uid)
        inc = _first(lambda: n.incentive, lambda: mg.I[uid])
        em = _first(lambda: n.emission, lambda: mg.E[uid])
        text = _commit_text(getattr(n, "commitment", None)) + " " + _commit_text(commits.get(str(n.hotkey)))
        rows.append(dict(uid=uid, hotkey=str(n.hotkey), coldkey=str(n.coldkey), incentive=_f(inc),
                         emission=_f(em), vpermit=bool(getattr(n, "validator_permit", False)),
                         reg_block=int(getattr(n, "block_at_registration", 0) or 0), commit=text))
    price = _first(lambda: st.subnet(NETUID).price, lambda: st.subnets.subnet(NETUID).price)
    burn = _first(lambda: st.recycle(NETUID), lambda: st.subnets.recycle(NETUID), lambda: st.burn(NETUID))
    hp = _first(lambda: st.get_subnet_hyperparameters(NETUID), lambda: st.subnets.get_subnet_hyperparameters(NETUID))
    return dict(block=int(getattr(mg, "block", 0)), rows=rows, price=_f(price) if price is not None else None,
                burn=_f(burn) if burn is not None else None,
                tempo=int(getattr(hp, "tempo", 360) or 360) if hp else 360,
                immunity=int(getattr(hp, "immunity_period", 0) or 0) if hp else None)


# ----------------------------------------------------------------------------------------------- analysis (pure)
def pools_of(commit: str) -> dict[str, bool]:
    """{pool: manifest is current} from a commitment string."""
    out = {}
    m = RE_MULTI.search(commit)
    if m:
        for part in m.group(1).strip(",").split(","):
            p, mid = part.split(":")
            out[p] = CURRENT.get(p) == mid
        return out
    m = RE_ONE.search(commit)
    if m:
        out[m.group(1)] = CURRENT.get(m.group(1)) == m.group(2)
    return out


def dr_effective(slots: list[tuple[str, float]]) -> float:
    """Sum of coldkey factors (first 2 slots of a coldkey 1.0, then x0.7 each) over paid slots."""
    by_ck = defaultdict(list)
    for ck, share in slots:
        by_ck[ck].append(share)
    eff = 0.0
    for shares in by_ck.values():
        for k, _ in enumerate(sorted(shares, reverse=True), start=1):
            eff += 1.0 if k <= 2 else 0.7 ** (k - 2)
    return eff


def analyze(data: dict, usd_per_share: float) -> dict:
    rows = data["rows"]
    total_inc = sum(r["incentive"] for r in rows) or 1.0
    single = defaultdict(list)  # pool -> [(uid, coldkey, share)] single-pool UIDs on the current manifest
    multi = []
    stale = 0
    for r in rows:
        share = r["incentive"] / total_inc
        ps = pools_of(r["commit"])
        if not ps:
            continue
        if not all(ps.values()):
            stale += 1
        if len(ps) == 1:
            (p, ok), = ps.items()
            if ok:
                single[p].append((r["uid"], r["coldkey"], share))
        else:
            multi.append((r["uid"], r["coldkey"], share, sorted(ps)))
    out = {"pools": {}, "multi": multi, "stale": stale}
    for p in POOLS:
        s = single.get(p, [])
        paid = [x for x in s if x[2] > 0]
        usd = sorted(x[2] * usd_per_share for x in paid)
        ck_count = defaultdict(int)
        for _, ck, _ in paid:
            ck_count[ck] += 1
        solo = sorted(x[2] * usd_per_share for x in paid if ck_count[x[1]] <= 2)  # not reduced by the DR rule
        out["pools"][p] = dict(
            committed=len(s), paid=len(paid), coldkeys=len(ck_count),
            effective=dr_effective([(ck, sh) for _, ck, sh in paid]) if p in DR_POOLS else float(len(paid)),
            share=sum(x[2] for x in paid), usd_day=sum(usd),
            median=statistics.median(usd) if usd else 0.0,
            p75=usd[int(0.75 * (len(usd) - 1))] if usd else 0.0, max=usd[-1] if usd else 0.0,
            solo_p75=solo[int(0.75 * (len(solo) - 1))] if solo else 0.0,
            top=sorted(paid, key=lambda x: -x[2])[:5], ck_count=dict(ck_count))
    return out


# ----------------------------------------------------------------------------------------------- report
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="finney")
    ap.add_argument("--uid", type=int, required=True, help="your UID (status line, burn needed or not)")
    ap.add_argument("--tao-usd", type=float, required=True, help="TAO price in USD (not on chain)")
    ap.add_argument("--pool-usd-day", type=float, default=None,
                    help="USD/day paid to 100%% of incentive; default = from on-chain emission x alpha price")
    ap.add_argument("--rent", default="3090=0.13,4090=0.30,5090=0.38",
                    help="card=USD/h list for the s1-lite+s1-cpu stack check")
    a = ap.parse_args()

    d = fetch(a.network)
    rows = d["rows"]
    blocks_day = 7200
    if a.pool_usd_day:
        pool_usd = a.pool_usd_day
        how = "given"
    else:
        miner_alpha_tempo = sum(r["emission"] for r in rows if r["incentive"] > 0)
        pool_usd = miner_alpha_tempo * blocks_day / (d["tempo"] + 1) * (d["price"] or 0) * a.tao_usd
        how = (f"on-chain: sum(emission of incentive UIDs) {miner_alpha_tempo:.3f} alpha/tempo x {blocks_day}/"
               f"{d['tempo'] + 1} x price {d['price']} TAO x ${a.tao_usd}")
    print(f"block {d['block']}  UIDs {len(rows)}  alpha price {d['price']} TAO  burn {d['burn']} TAO  "
          f"immunity {d['immunity']} blocks")
    print(f"100% of incentive = ${pool_usd:,.0f}/day ({how})")
    print("  if this looks wrong, rerun with --pool-usd-day <USD/day of the whole miner pool>\n")

    me = [r for r in rows if r["uid"] == a.uid]
    if me:
        r = me[0]
        tot = sum(x["incentive"] for x in rows) or 1
        zeros = sum(1 for x in rows if x["incentive"] == 0 and not x["vpermit"])
        print(f"UID {r['uid']}: hotkey {r['hotkey'][:8]}.. (reg block {r['reg_block']}), incentive "
              f"{r['incentive'] / tot:.4%}; {zeros} other non-validator UIDs at zero incentive compete for pruning.")
        print("  => a new pool commitment on this hotkey costs no new burn.\n")
    else:
        print(f"UID {a.uid} not found. A new hotkey costs the burn: {d['burn']} TAO "
              f"= ${(d['burn'] or 0) * a.tao_usd:,.0f}.\n")

    res = analyze(d, pool_usd)
    print(f"{'pool':8} {'commit':>6} {'paid':>5} {'ckeys':>5} {'eff':>6} {'share':>7} {'$/day':>8} "
          f"{'median':>7} {'p75':>7} {'max':>7} {'solo75':>7}   (single-pool UIDs on the current manifest)")
    for p, s in res["pools"].items():
        print(f"{p:8} {s['committed']:6d} {s['paid']:5d} {s['coldkeys']:5d} {s['effective']:6.1f} {s['share']:7.2%} "
              f"{s['usd_day']:8.0f} {s['median']:7.2f} {s['p75']:7.2f} {s['max']:7.2f} {s['solo_p75']:7.2f}")
    print("  solo75 = p75 $/day of UIDs whose coldkey has <= 2 hotkeys in that pool (what a new 1-hotkey slot "
          "can expect if it hits the pool's cap)")
    for p, s in res["pools"].items():
        if s["top"]:
            tops = ", ".join(f"uid{u} {sh:.3%} (ck x{s['ck_count'][ck]})" for u, ck, sh in s["top"])
            print(f"  top {p}: {tops}")
    if res["multi"]:
        print(f"\nmulti-pool hotkeys ({len(res['multi'])}), pay not attributable per pool:")
        for uid, ck, sh, ps in sorted(res["multi"], key=lambda x: -x[2])[:15]:
            print(f"  uid{uid} {'+'.join(ps)} {sh:.3%} = ${sh * pool_usd:.2f}/day")
    print(f"UIDs on a stale manifest: {res['stale']}")

    lite, cpu = res["pools"]["s1-lite"], res["pools"]["s1-cpu"]
    rev = lite["solo_p75"] + cpu["solo_p75"]
    print(f"\ns1-lite + s1-cpu on ONE hotkey, both at cap (32/s and 4/s): expected ~${rev:.2f}/day "
          f"(lite ${lite['solo_p75']:.2f} + cpu ${cpu['solo_p75']:.2f}, solo p75)")
    for item in a.rent.split(","):
        card, usd_h = item.split("=")
        rent = float(usd_h) * 24
        net = rev - rent
        burn_usd = 0.0 if me else (d["burn"] or 0) * a.tao_usd
        pay = f"payback of burn {burn_usd / net:.0f} days" if (burn_usd and net > 0) else ""
        print(f"  {card:>6} @ ${float(usd_h):.3f}/h: rent ${rent:5.2f}/day  net ${net:+6.2f}/day  {pay}")
    print("\nGo only if net > +$2/day with the p75 figure AND the pools' paid counts are not rising fast "
          "(rerun daily; rule changes need only 24 h notice).")


if __name__ == "__main__":
    main()
