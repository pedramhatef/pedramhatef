"""Find which inbound TCP ports are reachable from outside, using check-host.net's external
relay nodes. Runs entirely from the box (outbound HTTPS only). For each candidate port that is
FREE on the box, it briefly binds a listener, asks several external nodes to connect, and reports
OPEN (Hosteons allows it) vs BLOCKED/timeout (Hosteons filters it).

Usage:  python3 deploy/port_probe.py [IP] [port ...]
  IP defaults to the box's public IP. Ports default to a common set.
Nothing is installed; stdlib only. Listeners are torn down after each probe."""
import json, socket, sys, threading, time, urllib.request

def public_ip():
    for u in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            return urllib.request.urlopen(u, timeout=10).read().decode().strip()
        except Exception:
            continue
    return None

def is_free(p):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", p)); return True
    except OSError:
        return False
    finally:
        s.close()

def listener(p, stop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", p)); srv.listen(32); srv.settimeout(0.4)
    while not stop.is_set():
        try:
            c, _ = srv.accept(); c.close()
        except socket.timeout:
            continue
        except OSError:
            break
    srv.close()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def _get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=25))

def check_host(ipport):
    r = _get(f"https://check-host.net/check-tcp?host={ipport}&max_nodes=6")
    rid = r.get("request_id")
    if not rid:
        return None
    res = {}
    for _ in range(12):
        time.sleep(3)
        res = _get(f"https://check-host.net/check-result/{rid}")
        if res and all(v is not None for v in res.values()):
            break
    out = {"open": 0, "refused": 0, "timeout": 0, "other": 0, "pending": 0}
    for v in res.values():
        if v is None:
            out["pending"] += 1; continue
        item = v[0] if isinstance(v, list) and v else v
        if isinstance(item, dict):
            if "time" in item and not item.get("error"):
                out["open"] += 1
            else:
                e = str(item.get("error", "")).lower()
                out["refused" if "refus" in e else "timeout" if "time" in e else "other"] += 1
        elif isinstance(item, list) and item and item[0]:
            out["open"] += 1
        else:
            out["other"] += 1
    return out

def main():
    ip = sys.argv[1] if len(sys.argv) > 1 and "." in sys.argv[1] else public_ip()
    ports = [int(x) for x in sys.argv[2:]] or [443, 8080, 80, 3000, 9933, 30333, 8091, 9944]
    print(f"IP={ip}")
    print(f"{'port':>6}  {'on-box':>7}  verdict")
    for p in ports:
        if not is_free(p):
            print(f"{p:>6}  {'IN USE':>7}  (skip — something on the box already listens; this is likely your miner on 8091)")
            continue
        stop = threading.Event()
        t = threading.Thread(target=listener, args=(p, stop), daemon=True); t.start()
        time.sleep(1)
        try:
            c = check_host(f"{ip}:{p}")
            if c is None:
                verdict = "check-host error"
            elif c["open"] > 0:
                verdict = f"OPEN  ✓  ({c['open']} nodes connected)  <-- usable axon port"
            elif c["timeout"] > 0 and c["refused"] == 0:
                verdict = f"BLOCKED (filtered/timeout on {c['timeout']} nodes) — Hosteons drops inbound here"
            else:
                verdict = f"unclear {c}"
            print(f"{p:>6}  {'free':>7}  {verdict}")
        except Exception as e:
            print(f"{p:>6}  {'free':>7}  error: {e}")
        finally:
            stop.set(); time.sleep(0.4)

if __name__ == "__main__":
    main()
