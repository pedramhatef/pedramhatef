"""SN55 leaderboard client + per-round analytics.

Source of truth for ACTUAL scored outcomes: the validator posts every miner's scored submission to
    GET https://niome-leaderboard.genomes.io/api/v3/miners/scores
(the pinned repo's MINER_SCORE_URL @0c03fef). Each row is one miner's score for one task (=round):

  {task_id, miner_uid, miner_hotkey, validator_hotkey, validator_uid, final_score, weight,
   created_at, breakdown:{consistency_factor, distribution_fidelity_factor, total_weighted_score,
   n_valid_experiments, ...}, log, id}

final_score = TWS * CF * DFF. Emissions split across the top-10 by rank per the pinned repo's
SCORE_DISTRIBUTION. This module only READS the public leaderboard and computes analytics; it never
touches wallets or chain state. Signed fetch is optional (used on the box, which holds the hotkey).
"""
import json, time, urllib.request

BASE_URL = "https://niome-leaderboard.genomes.io"
SCORE_URL = f"{BASE_URL}/api/v3/miners/scores"
# Pinned repo SCORE_DISTRIBUTION: fraction of the subnet's miner emission each rank earns.
SCORE_DISTRIBUTION = [0.30, 0.20, 0.20, 0.15, 0.05, 0.03, 0.025, 0.02, 0.015, 0.01]


def fetch_scores(url=SCORE_URL, wallet=None, timeout=60, per_page=100000):
    """GET the leaderboard. If a bittensor wallet is given, sign the request the way the validator
    client does (hotkey signature, no receiver binding); otherwise try an unsigned GET."""
    full = f"{url}?per_page={per_page}"
    headers = {"Accept": "application/json", "User-Agent": "sn55-tracker"}
    if wallet is not None:
        import bittensor as bt
        headers.update(bt.http_auth.sign(wallet, method="GET", path="/api/v3/miners/scores", body=b""))
    req = urllib.request.Request(full, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["items"] if isinstance(data, dict) and "items" in data else data


def _bd(row, key, default=None):
    b = row.get("breakdown") or {}
    v = b.get(key, default)
    return v if v is not None else default


def group_rounds(rows):
    """{task_id: {created_at, validator_hotkey, rows:[...]}} sorted newest-first by created_at."""
    by = {}
    for r in rows:
        if r.get("final_score") is None:
            continue
        tid = r["task_id"]
        g = by.setdefault(tid, {"task_id": tid, "rows": [], "created_at": r.get("created_at", ""),
                                "validator_hotkey": r.get("validator_hotkey")})
        g["rows"].append(r)
        if r.get("created_at", "") > g["created_at"]:
            g["created_at"] = r["created_at"]
    return dict(sorted(by.items(), key=lambda kv: kv[1]["created_at"], reverse=True))


def round_analytics(rnd, our_uid=None, our_hotkey=None):
    """Rank a round, locate us, and compute the top-10 cutoffs and the reward each rank earns."""
    rs = sorted(rnd["rows"], key=lambda r: -r["final_score"])
    n = len(rs)
    def cut(k):
        return rs[k - 1]["final_score"] if n >= k else (rs[-1]["final_score"] if rs else 0.0)
    out = {
        "task_id": rnd["task_id"], "created_at": rnd["created_at"],
        "validator_hotkey": rnd["validator_hotkey"], "n_miners": n,
        "top1": cut(1), "cut3": cut(3), "cut5": cut(5), "cut10": cut(10),
        "winner": _profile(rs[0]) if rs else None,
        "our_uid": our_uid,
    }
    us = None
    for i, r in enumerate(rs):
        if (our_uid is not None and r["miner_uid"] == our_uid) or \
           (our_hotkey is not None and r.get("miner_hotkey") == our_hotkey):
            us = (i + 1, r); break
    if us:
        rank, r = us
        out["our"] = {**_profile(r), "rank": rank,
                      "reward_share": SCORE_DISTRIBUTION[rank - 1] if rank <= 10 else 0.0,
                      "gap_to_cut10": out["cut10"] - r["final_score"],
                      "gap_to_cut5": out["cut5"] - r["final_score"]}
    else:
        out["our"] = None
    return out


def _profile(r):
    return {"uid": r["miner_uid"], "hotkey": (r.get("miner_hotkey") or "")[:12],
            "final": round(r["final_score"], 3),
            "CF": round(_bd(r, "consistency_factor", 0.0), 4),
            "DFF": round(_bd(r, "distribution_fidelity_factor", 0.0), 4),
            "TWS": round(_bd(r, "total_weighted_score", 0.0), 2),
            "n_valid": _bd(r, "n_valid_experiments", 0),
            "base": round(_bd(r, "total_weighted_score", 0.0) * _bd(r, "distribution_fidelity_factor", 0.0), 2)}


def cf_needed(base, target_score):
    """CF our submission (with structural base = TWS*DFF) would need to reach target_score."""
    return (target_score / base) if base > 0 else float("inf")


if __name__ == "__main__":
    import sys, statistics
    rows = json.load(open(sys.argv[1]))["items"] if sys.argv[1].endswith(".json") else fetch_scores()
    rounds = group_rounds(rows)
    our_uid = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(f"{len(rows)} rows  {len(rounds)} rounds  validators={len({r.get('validator_hotkey') for r in rows})}")
    for tid, rnd in list(rounds.items())[:8]:
        a = round_analytics(rnd, our_uid=our_uid)
        w = a["winner"]
        line = (f"{tid[:8]} {a['created_at'][:19]} n={a['n_miners']:3d} "
                f"top1={a['top1']:6.2f} cut5={a['cut5']:6.2f} cut10={a['cut10']:6.2f} | "
                f"winner CF={w['CF']:.3f} base={w['base']:.0f}")
        if a["our"]:
            o = a["our"]; line += f" | US rank={o['rank']} final={o['final']:.2f} gap10={o['gap_to_cut10']:+.1f}"
        print(line)
