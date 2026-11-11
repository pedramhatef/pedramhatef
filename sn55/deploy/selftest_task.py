"""End-to-end self-test of the live miner, without waiting for a real validator.

Signs a /forward request exactly the way the validator does (bt.http_auth.sign with no
receiver binding), serves a synthetic contract + reference over a local HTTP server, and
captures the submission the miner PUTs back. Proves the whole chain:

    auth -> download contract/reference -> solve -> upload

Run ON THE BOX while the miner is running:
    cd ~/sn55/pkg && ~/sn55/venv/bin/python deploy/selftest_task.py

The contract served here carries "_selftest": true, so any capture file it produces under
~/sn55/data/captures is clearly distinguishable from a real task's contract.
"""
import json, os, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bittensor as bt
from harness import synth

MINER = os.environ.get("SELFTEST_MINER", "http://127.0.0.1:8080")
SRV_PORT = int(os.environ.get("SELFTEST_SRV_PORT", "8777"))
WALLET = os.environ.get("SELFTEST_WALLET", "niome_cold")
HOTKEY = os.environ.get("SELFTEST_HOTKEY", "niome_hot")
WAIT = int(os.environ.get("SELFTEST_WAIT", "90"))

contract = dict(synth.BASE_CONTRACT); contract["_selftest"] = True
reference = synth.reference()
received = {"body": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, payload: bytes, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/contract"):
            self._send(json.dumps(contract).encode())
        elif self.path.startswith("/reference"):
            self._send(json.dumps(reference).encode())
        else:
            self._send(b"{}", 404)

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0))
        received["body"] = self.rfile.read(n)
        self._send(b"{}")


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", SRV_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{SRV_PORT}"
    print(f"local task server on {base}")

    wallet = bt.Wallet(name=WALLET, hotkey=HOTKEY)
    print(f"signing as hotkey {wallet.hotkey.ss58_address}")

    payload = {
        "task": {"id": f"selftest-{int(time.time())}",
                 "contract_url": f"{base}/contract.json",
                 "hbb_ref_url": f"{base}/reference.json"},
        "presigned_url": f"{base}/submission",
        "timeout": 20,
    }
    body = json.dumps(payload).encode()
    headers = bt.http_auth.sign(wallet, method="POST", path="/forward", body=body)
    headers["Content-Type"] = "application/json"
    print(f"headers sent: {sorted(headers)}")

    req = urllib.request.Request(f"{MINER}/forward", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"POST /forward -> {r.status} {r.read()[:200]!r}")
    except urllib.error.HTTPError as e:
        print(f"POST /forward -> {e.code} {e.read()[:300]!r}")
        print("\nFAIL: the miner rejected the request. 401 = auth still broken; 403 = blacklisted.")
        return 1
    except Exception as e:
        print(f"POST /forward failed: {e}"); return 1

    print(f"waiting up to {WAIT}s for the miner to solve and upload ...")
    for i in range(WAIT):
        if received["body"]:
            break
        time.sleep(1)

    if not received["body"]:
        print("\nFAIL: no submission uploaded. Check the miner log for process_task errors.")
        return 1

    try:
        rows = json.loads(received["body"])
    except Exception as e:
        print(f"\nFAIL: uploaded body is not JSON: {e}"); return 1

    print(f"\nSUCCESS: miner uploaded {len(rows)} rows ({len(received['body'])} bytes)")
    if rows:
        r0 = rows[0]
        print(f"  first row keys: {sorted(r0)}")
        print(f"  sample: experiment_id={r0.get('experiment_id')} mutation={r0.get('mutation')} "
              f"cas={r0.get('cas_system')} strand={r0.get('strand')} guide={r0.get('guideRNA')}")
        muts = {}
        for r in rows:
            muts[r.get("mutation")] = muts.get(r.get("mutation"), 0) + 1
        print(f"  rows per mutation: {muts}")
    print("\nThe full live path works: auth -> download -> solve -> upload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
