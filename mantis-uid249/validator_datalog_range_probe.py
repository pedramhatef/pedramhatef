"""Read-only probe of the MANTIS owner-published validator datalog over HTTP Range.

Purpose: settle whether the paying-validator code path matured, decrypted and
backfilled UID249's payloads around the Oct 31 lock switch. It reads only the
SQLite B-tree pages needed, and never downloads the ~144 GB file.

Source: Barbariandev/MANTIS@ace548a config.DATALOG_ARCHIVE_URL. Schema and
embedding packing are copied from ledger.py (blob 7f07120d).

Safety:
  * GET with Range header only. Nothing is written remotely; the local DB is opened read-only.
  * Hard byte budget (default 64 MiB). The probe aborts when it is exceeded.
  * Refuses to run unless Last-Modified is after --min-last-modified (default:
    sidx 1880131 + 50,400 blocks ~ 2026-11-07 02:00 UTC), because an older file
    cannot contain the matured rows at all.
  * Prints no ciphertext. For raw_payloads it prints only round / hk / owner_pk match / length.

Usage (Paris, needs `pip install apsw`; Python 3.10+):
  python -I validator_datalog_range_probe.py \
      --hotkey 5EnMAEVGrPUxS35GWTokr7zaA8YkjcAhgyrPadhgw6TBz8dD \
      --peer 5Eq8YZzyhUATm9wrU1HyynJyykRmAUKYrgkGjsist4EX2bb1 \
      --sidx 1880125:1880140 --sidx 1879000:1879000 --sidx 1884000:1884000 \
      --out probe_result.json
"""
from __future__ import annotations

import argparse
import email.utils
import json
import sys
import urllib.request
from datetime import datetime, timezone

import apsw
import numpy as np

DEFAULT_URL = "https://pub-879ad825983e43529792665f4f510cd6.r2.dev/datalog.db"
OWNER_PK = "fbfe185ded7a4e6865effceb23cbac32894170587674e751ac237a06f72b3067"
CHUNK = 64 * 1024  # overridden by --chunk-kib
TICKERS = ["ETHLBFGS", "BTCLBFGS", "ETH", "ETHHITFIRST", "FUNDINGXSEC", "MULTIXSEC", "MULTIBREAKOUT"]


class Budget(Exception):
    pass


class RangeReader:
    def __init__(self, url: str, budget: int):
        self.url, self.budget, self.fetched, self.requests = url, budget, 0, 0
        self.cache: dict[int, bytes] = {}
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
            self.last_modified = r.headers.get("Last-Modified")
            self.etag = r.headers.get("ETag")
            self.accept_ranges = r.headers.get("Accept-Ranges")

    def _chunk(self, i: int) -> bytes:
        if i not in self.cache:
            lo = i * CHUNK
            hi = min(self.size, lo + CHUNK) - 1
            if self.fetched + (hi - lo + 1) > self.budget:
                raise Budget(f"byte budget {self.budget} exceeded")
            req = urllib.request.Request(self.url, headers={"Range": f"bytes={lo}-{hi}"})
            if self.etag:
                req.add_header("If-Match", self.etag)  # fail rather than mix versions
            with urllib.request.urlopen(req, timeout=120) as r:
                if r.status != 206:
                    raise RuntimeError(f"server ignored Range (HTTP {r.status})")
                data = r.read()
            self.fetched += len(data)
            self.requests += 1
            self.cache[i] = data
        return self.cache[i]

    def read(self, n: int, off: int) -> bytes:
        if off >= self.size:
            return b""
        n = min(n, self.size - off)
        out = bytearray()
        while n > 0:
            i, o = divmod(off, CHUNK)
            piece = self._chunk(i)[o:o + n]
            out += piece
            off += len(piece)
            n -= len(piece)
        return bytes(out)


def make_vfs(reader: RangeReader):
    class File:  # plain object: apsw accepts any object with the xMethods
        def __init__(self, name, flags):
            pass

        def xRead(self, amount, offset):
            return reader.read(amount, offset)

        def xFileSize(self):
            return reader.size

        def xClose(self):
            pass

        def xLock(self, level):
            pass

        def xUnlock(self, level):
            pass

        def xCheckReservedLock(self):
            return False

        def xFileControl(self, op, ptr):
            return False

        def xSectorSize(self):
            return 4096

        def xDeviceCharacteristics(self):
            return 0

        def xWrite(self, data, offset):
            raise apsw.ReadOnlyError("read-only probe")

        def xTruncate(self, size):
            raise apsw.ReadOnlyError("read-only probe")

        def xSync(self, flags):
            pass

    class VFS(apsw.VFS):
        def __init__(self):
            super().__init__("httprange", "")

        def xOpen(self, name, flags):
            return File(name, flags)

        def xAccess(self, pathname, flags):
            return False  # no -journal / -wal files exist remotely

        def xFullPathname(self, name):
            return name

    return VFS()


def unpack(blob: bytes, dtype=np.float16):
    if not blob:
        return {}
    sep = blob.index(b"\x00")
    hks = json.loads(blob[:sep].decode())
    raw = np.frombuffer(blob[sep + 1:], dtype=dtype)
    if not hks or raw.size % len(hks):
        return {"__malformed__": None}
    return dict(zip(hks, raw.reshape(len(hks), -1)))


def parse_ranges(specs):
    out = []
    for s in specs:
        a, b = (int(x) for x in s.split(":"))
        out.extend(range(a, b + 1))
    return sorted(set(out))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--hotkey", required=True)
    ap.add_argument("--peer", action="append", default=[])
    ap.add_argument("--sidx", action="append", required=True, help="a:b inclusive")
    ap.add_argument("--raw-ts-back", default="0,1,10,100,1000,5000",
                    help="seek raw_payloads(ts=latest_idx-k, hotkey) for each k (pending = not yet matured)")
    ap.add_argument("--budget-mib", type=int, default=64)
    ap.add_argument("--chunk-kib", type=int, default=64)
    ap.add_argument("--min-last-modified", default="2026-11-07T02:00:00Z")
    ap.add_argument("--out", default="probe_result.json")
    a = ap.parse_args(argv)
    global CHUNK
    CHUNK = a.chunk_kib * 1024

    rr = RangeReader(a.url, a.budget_mib << 20)
    lm = email.utils.parsedate_to_datetime(rr.last_modified) if rr.last_modified else None
    floor = datetime.fromisoformat(a.min_last_modified.replace("Z", "+00:00"))
    res = {"url": a.url, "size": rr.size, "last_modified": rr.last_modified, "etag": rr.etag,
           "accept_ranges": rr.accept_ranges, "probe_utc": datetime.now(timezone.utc).isoformat()}
    if lm is None or lm < floor:
        res["verdict"] = f"STALE: Last-Modified {rr.last_modified} < {a.min_last_modified}; cannot contain matured rows"
        json.dump(res, open(a.out, "w"), indent=1)
        print(json.dumps(res, indent=1))
        return 2

    vfs = make_vfs(rr)
    con = apsw.Connection(a.url, flags=apsw.SQLITE_OPEN_READONLY, vfs="httprange")
    cur = con.cursor()
    try:
        res["page_size"] = cur.execute("PRAGMA page_size").fetchone()[0]
        res["journal_mode_header"] = cur.execute("PRAGMA schema_version").fetchone()[0]
        last = cur.execute("SELECT idx, block FROM blocks ORDER BY idx DESC LIMIT 1").fetchone()
        res["latest_block_row"] = {"idx": last[0], "block": last[1], "sidx": last[1] // 5} if last else None
        res["challenge_meta"] = cur.execute("SELECT ticker, dim, blocks_ahead FROM challenge_meta").fetchall()

        rows = []
        watch = [a.hotkey] + a.peer
        for t in TICKERS:
            for s in parse_ranges(a.sidx):
                r = cur.execute("SELECT hotkeys, length(embeddings), embeddings FROM challenge_data "
                                "WHERE ticker=? AND sidx=?", (t, s)).fetchone()
                if r is None:
                    rows.append({"ticker": t, "sidx": s, "row": False})
                    continue
                emb = unpack(r[2]) if r[2] else {}
                nz = {hk for hk, v in emb.items() if v is not None and np.any(v != 0)}
                rows.append({
                    "ticker": t, "sidx": s, "row": True, "n_hotkeys_listed": len(json.loads(r[0] or "[]")),
                    "n_embedded": len(emb), "n_nonzero": len(nz), "emb_bytes": r[1],
                    "watch": {hk[:8]: {"listed": hk in (r[0] or ""), "nonzero": hk in nz} for hk in watch},
                })
        res["challenge_rows"] = rows

        pend = []
        if last:
            # Exact (ts, hotkey) primary-key seeks only. A ts-range scan would read every
            # hotkey's payload in the window (GBs on the real file).
            for back in sorted({int(x) for x in a.raw_ts_back.split(",")}):
                ts = last[0] - back
                r = cur.execute("SELECT rp.payload, b.block FROM raw_payloads rp JOIN blocks b ON rp.ts=b.idx "
                                "WHERE rp.ts=? AND rp.hotkey=?", (ts, a.hotkey)).fetchone()
                if r is None:
                    pend.append({"ts": ts, "present": False})
                    continue
                raw = r[0]
                try:
                    d = json.loads(bytes(raw).decode()) if raw else {}
                except Exception:
                    d = {"_unparseable": True}
                pend.append({"ts": ts, "block": r[1], "present": True, "len": len(raw or b""), "v": d.get("v"),
                             "round": d.get("round"), "hk_match": d.get("hk") == a.hotkey,
                             "owner_pk_match": str(d.get("owner_pk", "")).lower() == OWNER_PK})
        res["raw_payload_seeks_for_hotkey"] = pend
    except Budget as e:
        res["aborted"] = str(e)
    finally:
        res["bytes_fetched"] = rr.fetched
        res["range_requests"] = rr.requests
        json.dump(res, open(a.out, "w"), indent=1, default=str)
        print(json.dumps({k: v for k, v in res.items() if k != "challenge_rows"}, indent=1, default=str))
        print(f"challenge_rows: {len(res.get('challenge_rows', []))} written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
