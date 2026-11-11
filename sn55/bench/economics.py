"""SN55 unit economics from live chain + market observations (2026-11-11).
Per-rank payout is read from the on-chain emission of the currently rewarded UIDs."""
import json, re, sys
MG = sys.argv[1] if len(sys.argv) > 1 else "/home/user/pedramhatef/sn55/evidence/metagraph_now.json"
TAO_USD = 219.88          # coingecko 2026-11-11
ALPHA_TAO = 0.0019589     # chain price netuid 55
BURN_TAO = 0.0005         # hotkey registration burn
ALPHA_USD = ALPHA_TAO * TAO_USD
f = lambda s: float(re.sub(r"[^0-9.]", "", s)) if isinstance(s, str) else float(s or 0)

d = json.load(open(MG)); tempo = d["meta"]["tempo"]
N = d["neurons"]
owner_hk = d["meta"]["owner_hotkey"]
rew = sorted([n for n in N if n["incentive"] > 0 and n["hotkey"] != owner_hk],
             key=lambda n: -n["incentive"])
print(f"alpha=${ALPHA_USD:.4f}  TAO=${TAO_USD:.2f}  burn=${BURN_TAO*TAO_USD:.2f}  tempo={tempo}")
print(f"\n{'rank':>4} {'UID':>4} {'share':>7} {'alpha/day':>9} {'TAO/day':>8} {'USD/day':>8} {'USD/mo':>8} {'USD/mo*':>8}")
HAIRCUT = 0.85  # realizable after unstake/sell slippage + fees
rows = []
for i, n in enumerate(rew[:10]):
    apd = f(n["emission"]) * 7200 / tempo
    usd = apd * ALPHA_USD
    rows.append((i+1, n["uid"], n["incentive"], apd, usd))
    print(f"{i+1:>4} {n['uid']:>4} {n['incentive']*100:>6.2f}% {apd:>9.1f} {apd*ALPHA_TAO:>8.3f} {usd:>8.2f} {usd*30:>8.0f} {usd*30*HAIRCUT:>8.0f}")
pool = sum(r[3] for r in rows)
print(f"\nminer pool ~{pool:.0f} alpha/day = ${pool*ALPHA_USD:.0f}/day = ${pool*ALPHA_USD*30:.0f}/mo (realizable* x{HAIRCUT})")

print("\n--- Break-even entry probability (infra-only cost; earn 0 when out of top-10) ---")
for infra_mo in (0, 5, 12, 40):
    print(f" infra ${infra_mo}/mo:")
    for rank, uid, share, apd, usd in [rows[0], rows[2], rows[4], rows[9]]:
        need = infra_mo / (usd*30) if usd else float('inf')
        print(f"   rank {rank:>2} (${usd*30*HAIRCUT:>5.0f}/mo real): break-even if held >= {need*100:>5.2f}% of rounds")

print("\n--- 30-day net scenarios (realizable USD, infra $5/mo, held fraction h) ---")
for label, rank, usd in [("rank1-2", 1, rows[0][4]), ("rank3-4", 3, rows[2][4]),
                          ("rank5-10", 6, rows[5][4]), ("miss top10", 0, 0.0)]:
    for h in (1.0, 0.5, 0.25):
        gross = usd*30*HAIRCUT*h
        print(f" {label:>9} held {h*100:>3.0f}%: gross ${gross:>6.0f} - infra $5 - reg churn ~$3 = net ${gross-8:>6.0f}/mo")
    if rank == 0: print(f" {'miss':>9}        : gross $0 - infra $5 = net $-5/mo (plus wasted registrations)")
