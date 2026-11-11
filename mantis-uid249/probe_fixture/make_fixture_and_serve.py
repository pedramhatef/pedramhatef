import json, os, sqlite3, sys, threading, numpy as np, email.utils, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, sys.argv[1]); import ledger_schema as L  # noqa
db = sys.argv[2]; lm = sys.argv[3]
if os.path.exists(db): os.remove(db)
c = sqlite3.connect(db); c.executescript(L.SCHEMA)
U, P = "5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD", "5Eq8YZzyhUATm9wrU1HyynJyykRmAUKYrgkGjsist4EX2bb1"
peers = [f"5Peer{i:043d}" for i in range(256)] + [U, P]
rng = np.random.default_rng(0)
def pack(emb):
    hk = sorted(emb); return json.dumps(hk).encode() + b"\x00" + np.array([emb[h] for h in hk], dtype=np.float16).tobytes()
for i in range(60000):  # filler blocks
    c.execute("INSERT INTO blocks VALUES (?,?)", (i, 9_100_000 + i * 5))
for s in range(1879900, 1880400):
    for t, d in (("ETHLBFGS", 17), ("FUNDINGXSEC", 20)):
        emb = {h: rng.uniform(-1, 1, d) for h in peers}
        if s >= 1880131: emb[U] = np.zeros(d)          # known answer
        c.execute("INSERT INTO challenge_data (ticker,sidx,price,hotkeys,embeddings) VALUES (?,?,?,?,?)",
                  (t, s, 1.0, json.dumps(peers), pack(emb)))
for ts in range(59000, 60000):
    for h in peers[:40] + [U]:
        pl = {"v": 2, "round": 123 + ts, "hk": h, "owner_pk": "fbfe185ded7a4e6865effceb23cbac32894170587674e751ac237a06f72b3067", "C": {"ct": "00" * 300}}
        c.execute("INSERT INTO raw_payloads VALUES (?,?,?)", (ts, h, json.dumps(pl).encode()))
c.commit(); c.close()
size = os.path.getsize(db); data = open(db, "rb").read()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _hdr(self): self.send_header("Last-Modified", lm); self.send_header("ETag", '"fx1"'); self.send_header("Accept-Ranges", "bytes")
    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Length", str(size)); self._hdr(); self.end_headers()
    def do_GET(self):
        lo, hi = (int(x) for x in self.headers["Range"].split("=")[1].split("-"))
        body = data[lo:hi + 1]; self.send_response(206); self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Range", f"bytes {lo}-{hi}/{size}"); self._hdr(); self.end_headers(); self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[4])), H)
print("SIZE", size, flush=True); srv.serve_forever()
