"""Aggregate the tracked data into a compact JSON a dashboard renders.

Inputs (any subset): a leaderboard scores dump or live fetch, our prediction record, and the
rounds/metagraph JSONL time series written by collect.py. Output: dashboard_data.json with the
views that matter for development — the CF-vs-base field, cutoff trends, winner anatomy, our UID
history, and the honest gap analysis.
"""
import os, sys, json, statistics, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))
from track import leaderboard as LB


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def build(rows, our_uid=96, our_hotkey=None, prediction=None, meta_records=None, recent_rounds=40):
    rounds = LB.group_rounds(rows)
    analyses = [LB.round_analytics(r, our_uid=our_uid, our_hotkey=our_hotkey) for r in rounds.values()]
    analyses.sort(key=lambda a: a["created_at"])
    recent = analyses[-recent_rounds:]

    # cutoff trend
    trend = [{"t": a["created_at"][:19], "task": a["task_id"][:8], "n": a["n_miners"],
              "top1": round(a["top1"], 2), "cut5": round(a["cut5"], 2), "cut10": round(a["cut10"], 2),
              "winner_cf": a["winner"]["CF"], "winner_base": a["winner"]["base"],
              "our_rank": (a["our"] or {}).get("rank"), "our_final": (a["our"] or {}).get("final")}
             for a in recent]

    # winner anatomy (recent)
    wc = [a["winner"] for a in recent]
    winner_anat = {"median_CF": round(_med([w["CF"] for w in wc]), 3),
                   "median_DFF": round(_med([w["DFF"] for w in wc]), 3),
                   "median_TWS": round(_med([w["TWS"] for w in wc]), 1),
                   "median_base": round(_med([w["base"] for w in wc]), 1)}

    # the field for the most recent round: every miner as (CF, base, final, rewarded)
    latest = analyses[-1]
    field = []
    for r in sorted(rounds[latest["task_id"]]["rows"], key=lambda r: -r["final_score"]):
        field.append({"uid": r["miner_uid"], "cf": round(LB._bd(r, "consistency_factor", 0.0), 3),
                      "base": round(LB._bd(r, "total_weighted_score", 0.0) * LB._bd(r, "distribution_fidelity_factor", 0.0), 1),
                      "final": round(r["final_score"], 2)})
    for i, f in enumerate(field):
        f["rank"] = i + 1

    # our UID history across ALL rounds (note: pre-registration rows belong to the prior occupant)
    our_hist = [{"t": a["created_at"][:19], "task": a["task_id"][:8], "rank": a["our"]["rank"],
                 "final": a["our"]["final"], "cf": a["our"]["CF"], "base": a["our"]["base"]}
                for a in analyses if a["our"]]

    # honest gap analysis vs the latest cutoffs, using our predicted base + CF
    gap = None
    if prediction:
        base = prediction.get("base")
        cf = prediction.get("cf", {})
        gap = {"our_base": base, "our_cf_mean": cf.get("mean"), "our_cf_max": cf.get("max"),
               "our_final_mean": prediction.get("final", {}).get("mean"),
               "latest_cut10": round(latest["cut10"], 2), "latest_cut5": round(latest["cut5"], 2),
               "cf_needed_cut10": round(LB.cf_needed(base, latest["cut10"]), 3) if base else None,
               "cf_needed_cut5": round(LB.cf_needed(base, latest["cut5"]), 3) if base else None,
               "n_valid": prediction.get("n_valid"), "TWS": prediction.get("TWS"), "DFF": prediction.get("DFF")}

    meta = None
    if meta_records:
        m = meta_records[-1]
        meta = {"utc": m.get("utc"), "block": m.get("block"), "ours": m.get("ours"),
                "cut10_incentive": m.get("analytics", {}).get("cut10_incentive"),
                "n_rewarded": m.get("analytics", {}).get("n_rewarded")}

    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "n_rows": len(rows), "n_rounds": len(rounds), "our_uid": our_uid,
            "validators": sorted({r.get("validator_hotkey") for r in rows if r.get("validator_hotkey")}),
            "reward_distribution": LB.SCORE_DISTRIBUTION,
            "trend": trend, "winner_anatomy": winner_anat, "latest_field": field,
            "latest_task": latest["task_id"], "our_history": our_hist, "gap": gap, "metagraph": meta}


def _read_jsonl(path):
    if not path or not os.path.exists(path): return []
    out = []
    for line in open(path):
        try: out.append(json.loads(line))
        except Exception: pass
    return out


if __name__ == "__main__":
    # report.py scores.json [out.json] [our_uid]
    src = sys.argv[1]
    rows = json.load(open(src))["items"] if src.endswith(".json") else LB.fetch_scores()
    our_uid = int(sys.argv[3]) if len(sys.argv) > 3 else 96
    pred_path = os.environ.get("SN55_PREDICTION_JSON")
    prediction = json.load(open(pred_path)) if pred_path and os.path.exists(pred_path) else None
    data = build(rows, our_uid=our_uid, prediction=prediction,
                 meta_records=_read_jsonl(os.environ.get("SN55_METAGRAPH_JSONL")))
    out = sys.argv[2] if len(sys.argv) > 2 else "dashboard_data.json"
    json.dump(data, open(out, "w"), indent=1, default=str)
    print(f"wrote {out}: {data['n_rounds']} rounds, {len(data['trend'])} in trend, "
          f"{len(data['latest_field'])} miners in latest field")
