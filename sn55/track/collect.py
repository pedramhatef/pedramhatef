"""Tracker daemon: persist leaderboard + metagraph time series, and score our own captures.

Writes newline-delimited JSON under TRACK_DIR (default ~/sn55/data/track):
  rounds.jsonl     one record per (task_id) seen on the leaderboard: full round_analytics for our
                   UID, plus the winner profile and the top-10/5 cutoffs.
  metagraph.jsonl  one record per poll: our on-chain incentive/emission/rank + rewarded cutoffs.
  predictions.jsonl one record per captured task we solved: predicted base + CF distribution.

Idempotent: a task_id already in rounds.jsonl is not re-appended unless its score changed. Safe to
run under nohup/watchdog. Read-only against the chain and the public leaderboard. It does NOT use
any wallet by default: the leaderboard GET is unsigned, and signing is strictly opt-in via
SN55_SIGN=1 (or SN55_SIGN_WALLET/SN55_SIGN_HOTKEY) — never triggered merely by SN55_OUR_HOTKEY,
which only matches our rows. Default operation touches no wallet and submits nothing.
"""
import os, sys, json, time, glob, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))
from track import leaderboard as LB
from track import metagraph as MG

TRACK_DIR = os.environ.get("SN55_TRACK_DIR", os.path.expanduser("~/sn55/data/track"))
CAPTURE_DIR = os.environ.get("NIOME_CAPTURE_DIR", os.path.expanduser("~/niome-data/captures"))
CHR11_FA = os.environ.get("CHR11_FA", os.path.expanduser("~/sn55/data/chr11.fa"))
OUR_UID = int(os.environ.get("SN55_OUR_UID", "96"))
OUR_HOTKEY = os.environ.get("SN55_OUR_HOTKEY")  # optional, more robust than uid across re-registration
POLL_S = int(os.environ.get("SN55_TRACK_POLL_S", "900"))


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _load_ids(path, key):
    seen = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line); seen[r.get(key)] = r
            except Exception:
                pass
    return seen


def _append(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _wallet():
    """Return a signing wallet ONLY when signing is explicitly opted into via SN55_SIGN=1 (or a
    SN55_SIGN_WALLET / SN55_SIGN_HOTKEY env var). Knowing our own hotkey (SN55_OUR_HOTKEY, used only
    to match our rows on the public leaderboard) must NEVER cause wallet use. The leaderboard GET is
    public; default operation is strictly unsigned and touches no wallet."""
    if os.environ.get("SN55_SIGN") != "1" and not os.environ.get("SN55_SIGN_WALLET") \
       and not os.environ.get("SN55_SIGN_HOTKEY"):
        return None
    try:
        import bittensor as bt
        return bt.Wallet(name=os.environ.get("SN55_SIGN_WALLET", "niome_cold"),
                         hotkey=os.environ.get("SN55_SIGN_HOTKEY", "niome_hot"))
    except Exception:
        return None


def poll_leaderboard(rows=None):
    path = os.path.join(TRACK_DIR, "rounds.jsonl")
    seen = _load_ids(path, "task_id")
    if rows is None:
        try:
            rows = LB.fetch_scores(wallet=_wallet())
        except Exception as e:
            hint = " (if this is a 401, the endpoint wants a signature; signing is opt-in only — " \
                   "set SN55_SIGN=1 deliberately, it is never enabled automatically)" if "401" in str(e) else ""
            print(f"{_now()} leaderboard fetch failed: {e}{hint}"); return 0
    rounds = LB.group_rounds(rows)
    added = 0
    for tid, rnd in rounds.items():
        a = LB.round_analytics(rnd, our_uid=OUR_UID, our_hotkey=OUR_HOTKEY)
        prev = seen.get(tid)
        # append if new, or if our score for this round changed (e.g. late post)
        sig = (a.get("our") or {}).get("final"), a["n_miners"]
        if prev is not None and (prev.get("our") or {}).get("final") == sig[0] and prev.get("n_miners") == sig[1]:
            continue
        a["_recorded_at"] = _now()
        _append(path, a); added += 1
    print(f"{_now()} leaderboard: {len(rounds)} rounds, {added} new/changed appended")
    return added


def poll_metagraph():
    path = os.path.join(TRACK_DIR, "metagraph.jsonl")
    try:
        snap = MG.snapshot(our_uid=OUR_UID, our_hotkey=OUR_HOTKEY)
    except Exception as e:
        print(f"{_now()} metagraph snapshot failed: {e}"); return
    rec = {"utc": snap["utc"], "block": snap["head_block"], "metagraph_block": snap["metagraph_block"],
           "analytics": snap["analytics"], "ours": snap["ours"]}
    _append(path, rec)
    o = snap["ours"]
    print(f"{_now()} metagraph block {snap['head_block']}: "
          + (f"UID{o['uid']} incentive={o['incentive']} rank={o['rank_among_rewarded']} "
             f"top10={o['in_top10']} em/day={o['em_per_day']}" if o else f"UID {OUR_UID} not found"))


def score_captures():
    """Run predict() on any captured real task we have not scored yet."""
    path = os.path.join(TRACK_DIR, "predictions.jsonl")
    seen = _load_ids(path, "capture")
    contracts = sorted(glob.glob(os.path.join(CAPTURE_DIR, "contract_*.json")))
    done = 0
    for cpath in contracts:
        ts = os.path.basename(cpath)[len("contract_"):-len(".json")]
        if ts in seen:
            continue
        rpath = os.path.join(CAPTURE_DIR, f"reference_{ts}.json")
        if not os.path.exists(rpath):
            continue
        try:
            from track import predict as P
            from miner.solve import solve
            contract = json.load(open(cpath)); reference = json.load(open(rpath))
            if contract.get("_selftest"):
                continue
            sub = solve(contract, reference, CHR11_FA)
            pred = P.predict(sub, contract, reference, CHR11_FA)
            pred.pop("_cf_samples", None)
            rec = {"capture": ts, "recorded_at": _now(),
                   "contract_shape": {"mutations": contract.get("active_mutations"),
                                      "rules": contract.get("rules"), "cell_type": contract.get("cell_type")},
                   "prediction": pred}
            _append(path, rec); done += 1
            print(f"{_now()} scored capture {ts}: base={pred.get('base')} final~{pred.get('final',{}).get('mean')}")
        except Exception as e:
            print(f"{_now()} scoring capture {ts} failed: {e}")
    return done


def poll_challenge(rows=None):
    """Watch for a challenge rotation (Challenge 2 -> 3). The whole Challenge-3 plan hinges on
    catching this early, so it runs every poll: cheap API-version probe + breakdown-key + captured
    contract schema. On any signal it writes CHALLENGE_ALERT.txt and logs loudly."""
    from track import challenge as CH
    state = CH.detect(rows=rows, capture_dir=CAPTURE_DIR, probe=True)
    alert = CH.record(TRACK_DIR, state)
    if state["rotated"]:
        print(f"{_now()} *** CHALLENGE ROTATION DETECTED *** {state['signals']} -> {alert}")
    else:
        print(f"{_now()} challenge watch: no rotation (still Challenge 2 CRISPR); "
              f"live newer APIs={state['live_api_versions']}")
    return state


def loop():
    os.makedirs(TRACK_DIR, exist_ok=True)
    print(f"{_now()} tracker up: TRACK_DIR={TRACK_DIR} UID={OUR_UID} poll={POLL_S}s")
    while True:
        try:
            rows = None
            try:
                rows = LB.fetch_scores(wallet=_wallet())
            except Exception as e:
                print(f"{_now()} leaderboard fetch failed: {e}")
            poll_leaderboard(rows); poll_metagraph(); poll_challenge(rows); score_captures()
        except Exception as e:
            print(f"{_now()} loop error: {e}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        rows = None
        try:
            rows = LB.fetch_scores(wallet=_wallet())
        except Exception as e:
            print(f"{_now()} leaderboard fetch failed: {e}")
        poll_leaderboard(rows); poll_metagraph(); poll_challenge(rows); score_captures()
    else:
        loop()
