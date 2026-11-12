"""Detect an SN55 challenge rotation (e.g. Challenge 2 CRISPR -> Challenge 3 Drug Response).

The whole Challenge-3 strategy depends on catching the rotation EARLY — the leaderboard resets and
existing CF exploits break, so a clean solver deployed on day one can place before the metric-hackers
re-crack the new validator. This module gives the daemon three cheap, read-only rotation signals:

  1. the leaderboard breakdown keys change away from the Challenge-2 CRISPR set
     (consistency_factor / distribution_fidelity_factor / total_weighted_score),
  2. a newer API version (/api/v4, /api/v5 ... tasks/current) starts returning 200 instead of 404,
  3. a freshly captured contract no longer matches the CRISPR schema.

On any signal it writes a prominent CHALLENGE_ALERT so the dashboard and the operator see it at once.
All checks are unsigned GETs / local reads; no wallet, no submission.
"""
import os, json, time, urllib.request, datetime as dt

# Challenge 2 (CRISPR HBB) baseline — what "no rotation yet" looks like.
CRISPR_BREAKDOWN_KEYS = {"consistency_factor", "consistency_score", "distribution_fidelity_factor",
                         "distribution_fidelity_score", "n_valid_experiments", "total_weighted_score"}
CRISPR_CONTRACT_KEYS = {"active_mutations", "mutation_weights", "mutation_regions", "rules"}
BASE_URL = "https://niome-leaderboard.genomes.io"
PROBE_VERSIONS = ("v4", "v5", "v6")   # v4 PGx exists in-repo but 404s today; a 200 == something live


def _status(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "sn55-tracker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(300)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return None, b""


def detect(rows=None, capture_dir=None, probe=True):
    """Return {rotated: bool, signals: [...], breakdown_keys, live_api_versions}."""
    signals = []
    # 1) leaderboard breakdown keys
    bkeys = None
    if rows:
        for r in rows:
            if r.get("breakdown"):
                bkeys = set(r["breakdown"].keys()); break
        if bkeys and not (bkeys & CRISPR_BREAKDOWN_KEYS):
            signals.append(f"leaderboard breakdown keys changed: {sorted(bkeys)}")
    # 2) newer API versions live
    live = []
    if probe:
        for v in PROBE_VERSIONS:
            code, _ = _status(f"{BASE_URL}/api/{v}/tasks/current")
            if code == 200:
                live.append(v); signals.append(f"/api/{v}/tasks/current is LIVE (200)")
    # 3) captured contract schema
    if capture_dir and os.path.isdir(capture_dir):
        import glob
        cs = sorted(glob.glob(os.path.join(capture_dir, "contract_*.json")))
        if cs:
            try:
                c = json.load(open(cs[-1]))
                if not c.get("_selftest") and not (set(c.keys()) & CRISPR_CONTRACT_KEYS):
                    signals.append(f"latest captured contract schema changed: {sorted(c.keys())[:10]}")
            except Exception:
                pass
    return {"rotated": bool(signals), "signals": signals,
            "breakdown_keys": sorted(bkeys) if bkeys else None, "live_api_versions": live,
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def record(track_dir, state):
    os.makedirs(track_dir, exist_ok=True)
    with open(os.path.join(track_dir, "challenge_state.jsonl"), "a") as f:
        f.write(json.dumps(state) + "\n")
    if state.get("rotated"):
        alert = os.path.join(track_dir, "CHALLENGE_ALERT.txt")
        with open(alert, "w") as f:
            f.write("SN55 CHALLENGE ROTATION DETECTED\n" + state["checked_at"] + "\n\n"
                    + "\n".join(state["signals"]) + "\n\n"
                    "Action: a new challenge likely started. The leaderboard will reset and CF exploits\n"
                    "break. Deploy the clean solver for the new challenge NOW to capture early emissions.\n"
                    "Drug Response / Rare Disease -> see CHALLENGE3_READINESS.md and the v4 PGx validator.\n")
        return alert
    return None


if __name__ == "__main__":
    import sys
    rows = json.load(open(sys.argv[1]))["items"] if len(sys.argv) > 1 and sys.argv[1].endswith(".json") else None
    print(json.dumps(detect(rows=rows, probe=True), indent=1))
