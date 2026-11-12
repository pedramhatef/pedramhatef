"""Render dashboard_data.json (from report.build) into a standalone index.html.

Self-contained: the data is embedded, no network, no libraries. Charts are hand-drawn SVG. The
same file the box regenerates each round and the user opens as an artifact.
    python -m track.dashboard dashboard_data.json index.html
"""
import sys, json

TEMPLATE = r"""<title>SN55 Miner Tracker</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
/* Layout: a stacked single-column dashboard — status band, headline tiles, then the CF-vs-base
   field chart as the centrepiece, with trend, anatomy and reward panels below. */
:root{
  --bg:#f4f6f8; --surface:#ffffff; --surface-2:#eef1f4; --line:#dce1e7;
  --ink:#1a2230; --ink-2:#55606f; --muted:#8893a2;
  --us:#0f8fa6; --top10:#d9563a; --field:#aab3bf;
  --good:#1a8f5a; --warn:#c77a12; --bad:#c0392b;
  --seq1:#14497a; --seq2:#4f7fb3; --seq3:#9db8d6;
  --mono:'DM Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:'Space Grotesk',system-ui,sans-serif; --body:'Inter',system-ui,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#101316; --surface:#1a1e24; --surface-2:#22272e; --line:#2d343d;
  --ink:#eaf0f6; --ink-2:#aab6c4; --muted:#7a8696;
  --us:#1f9aa9; --top10:#d85f46; --field:#5b6675;
  --good:#3fb985; --warn:#e0a53a; --bad:#e2685a;
  --seq1:#6fa0d6; --seq2:#4f7fb3; --seq3:#36567c; color-scheme:dark;
}}
:root[data-theme="dark"]{
  --bg:#101316; --surface:#1a1e24; --surface-2:#22272e; --line:#2d343d;
  --ink:#eaf0f6; --ink-2:#aab6c4; --muted:#7a8696;
  --us:#1f9aa9; --top10:#d85f46; --field:#5b6675;
  --good:#3fb985; --warn:#e0a53a; --bad:#e2685a;
  --seq1:#6fa0d6; --seq2:#4f7fb3; --seq3:#36567c; color-scheme:dark;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--body);line-height:1.5;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto;
  padding-left:max(16px,env(safe-area-inset-left,0px));
  padding-right:max(16px,env(safe-area-inset-right,0px));
  padding-block:28px 56px}
h1,h2,h3{font-family:var(--sans);text-wrap:balance;margin:0;font-weight:600;letter-spacing:-.01em}
h1{font-size:clamp(1.5rem,4.5vw,2.1rem)}
h2{font-size:1.15rem;margin-bottom:2px}
.sub{color:var(--ink-2);font-size:.95rem}
.eyebrow{font-family:var(--mono);text-transform:uppercase;letter-spacing:.14em;font-size:.68rem;
  color:var(--muted)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;gap:14px 20px;align-items:baseline;justify-content:space-between;
  margin-bottom:6px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 26px}
.chip{display:inline-flex;align-items:center;gap:7px;background:var(--surface);border:1px solid var(--line);
  border-radius:999px;padding:5px 12px;font-size:.8rem;color:var(--ink-2);font-family:var(--mono)}
.dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.dot.live{background:var(--good);box-shadow:0 0 0 3px color-mix(in srgb,var(--good) 25%,transparent)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;
  display:flex;flex-direction:column;gap:3px;min-width:0}
.tile .k{font-size:.72rem;color:var(--muted);font-family:var(--mono);text-transform:uppercase;
  letter-spacing:.06em}
.tile .v{font-family:var(--sans);font-size:1.7rem;font-weight:600;font-variant-numeric:tabular-nums;
  line-height:1.1}
.tile .note{font-size:.76rem;color:var(--ink-2)}
.tile.flag{border-color:color-mix(in srgb,var(--top10) 45%,var(--line))}
.tile.flag .v{color:var(--top10)}
.tile.us .v{color:var(--us)}
.callout{background:color-mix(in srgb,var(--us) 8%,var(--surface));border:1px solid color-mix(in srgb,var(--us) 30%,var(--line));
  border-left:3px solid var(--us);border-radius:10px;padding:14px 16px;margin:18px 0 28px;font-size:.92rem;color:var(--ink-2)}
.callout b{color:var(--ink)}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 18px 10px;
  margin-bottom:20px;min-width:0}
.panel .head{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;justify-content:space-between;
  margin-bottom:12px}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:.8rem;color:var(--ink-2);margin:2px 0 10px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:11px;height:11px;border-radius:3px;flex:0 0 auto}
.star{width:0;height:0}
svg{display:block;width:100%;height:auto;overflow:visible}
.ax{fill:var(--muted);font-family:var(--mono);font-size:11px}
.axtitle{fill:var(--ink-2);font-family:var(--sans);font-size:12px;font-weight:500}
.grid{stroke:var(--line);stroke-width:1}
.iso{stroke:var(--line);stroke-dasharray:3 4;fill:none;stroke-width:1}
.iso.cut{stroke:var(--top10);stroke-dasharray:5 4;stroke-width:1.5;opacity:.8}
.isolabel{fill:var(--muted);font-family:var(--mono);font-size:10px}
.scroll{overflow-x:auto}
.tt{position:fixed;pointer-events:none;z-index:50;background:var(--ink);color:var(--bg);
  font-family:var(--mono);font-size:11px;padding:6px 9px;border-radius:7px;opacity:0;transition:opacity .08s;
  white-space:nowrap;box-shadow:0 4px 16px rgba(0,0,0,.3)}
table{border-collapse:collapse;width:100%;font-size:.82rem;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-family:var(--mono);font-weight:500;text-transform:uppercase;font-size:.68rem;
  letter-spacing:.05em}
tr.usrow td{color:var(--us);font-weight:600}
.frontier ul{margin:8px 0 14px;padding-left:18px;color:var(--ink-2);font-size:.9rem}
.frontier li{margin:5px 0}
.frontier li b{color:var(--ink)}
.foot{color:var(--muted);font-size:.76rem;margin-top:10px;line-height:1.6}
.bars{display:flex;align-items:flex-end;gap:6px;height:120px;margin-top:6px}
.bars .b{flex:1;background:var(--seq2);border-radius:4px 4px 0 0;min-width:0;position:relative;display:flex;
  justify-content:center;align-items:flex-start}
.bars .b.r1{background:var(--top10)}
.bars .b .lab{position:absolute;top:-16px;font-family:var(--mono);font-size:10px;color:var(--ink-2)}
.bars .b .rk{position:absolute;bottom:-18px;font-family:var(--mono);font-size:10px;color:var(--muted)}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <header>
    <div>
      <div class="eyebrow">Bittensor Subnet 55 · NIOME</div>
      <h1>SN55 Miner Tracker</h1>
    </div>
    <div class="sub mono" id="gen"></div>
  </header>
  <div class="chips" id="chips"></div>

  <div class="tiles" id="tiles"></div>
  <div class="callout" id="callout"></div>

  <div class="panel">
    <div class="head"><div><h2>The field: consistency vs base score</h2>
      <div class="sub">Every miner in the most recent round. final = base × CF.</div></div></div>
    <div class="legend">
      <span><i class="swatch" style="background:var(--field)"></i>field</span>
      <span><i class="swatch" style="background:var(--top10)"></i>rewarded top-10</span>
      <span><i class="swatch" style="background:var(--us);border-radius:2px;transform:rotate(45deg)"></i>us (predicted)</span>
      <span><i style="display:inline-block;width:16px;border-top:1.5px dashed var(--top10)"></i>rank-10 cutoff</span>
    </div>
    <div class="scroll"><svg id="scatter" viewBox="0 0 760 440" role="img"
      aria-label="Scatter of CF against base score for every miner in the latest round"></svg></div>
  </div>

  <div class="panel">
    <div class="head"><div><h2>Cutoff trend</h2>
      <div class="sub">Score needed to place, by round. Our predicted final shown for scale.</div></div></div>
    <div class="legend">
      <span><i class="swatch" style="background:var(--seq1)"></i>rank 1</span>
      <span><i class="swatch" style="background:var(--seq2)"></i>rank 5 cutoff</span>
      <span><i class="swatch" style="background:var(--seq3)"></i>rank 10 cutoff</span>
      <span><i style="display:inline-block;width:16px;border-top:2px solid var(--us)"></i>our predicted final</span>
    </div>
    <div class="scroll"><svg id="trend" viewBox="0 0 760 340" role="img"
      aria-label="Line chart of rank-1, rank-5 and rank-10 cutoff scores over recent rounds"></svg></div>
  </div>

  <div class="panel">
    <div class="head"><div><h2>Winner anatomy vs ours</h2>
      <div class="sub">Median of recent round winners. Our base is competitive; our CF is not.</div></div></div>
    <div id="anat"></div>
  </div>

  <div class="panel">
    <div class="head"><div><h2>Latest round — top 12</h2>
      <div class="sub" id="lt-task"></div></div></div>
    <div class="scroll"><table id="ltable"><thead><tr>
      <th>Rank</th><th>UID</th><th>CF</th><th>Base</th><th>Final</th><th>Reward</th></tr></thead>
      <tbody></tbody></table></div>
  </div>

  <div class="panel">
    <div class="head"><div><h2>Reward split by rank</h2>
      <div class="sub">Share of miner emission each rank earns per round (pinned SCORE_DISTRIBUTION).</div></div></div>
    <div class="bars" id="reward"></div>
    <div style="height:22px"></div>
  </div>

  <div class="panel frontier">
    <div class="head"><div><h2>Development frontier</h2>
      <div class="sub">What the tracker exists to crack.</div></div></div>
    <ul id="frontier"></ul>
    <div class="foot" id="foot"></div>
  </div>
</div>
<div class="tt" id="tt"></div>

<script>
const DATA = __DATA__;
const $=s=>document.querySelector(s), tt=$('#tt');
const fmt=(x,d=2)=>x==null?'—':Number(x).toFixed(d);
function showTT(e,html){tt.innerHTML=html;tt.style.opacity=1;
  let x=e.clientX+12,y=e.clientY+12;
  if(x+tt.offsetWidth>innerWidth)x=e.clientX-tt.offsetWidth-12;
  if(y+tt.offsetHeight>innerHeight)y=e.clientY-tt.offsetHeight-12;
  tt.style.left=x+'px';tt.style.top=y+'px';}
function hideTT(){tt.style.opacity=0;}

// ---- header ----
$('#gen').textContent='updated '+(DATA.generated_at||'').slice(0,16).replace('T',' ')+' UTC';
const g=DATA.gap||{}, m=DATA.metagraph||null;
$('#chips').innerHTML=[
  `<span class="chip"><i class="dot live"></i>UID ${DATA.our_uid} live</span>`,
  `<span class="chip">build ed8a692</span>`,
  `<span class="chip">${DATA.n_rounds} rounds tracked</span>`,
  `<span class="chip">${DATA.validators.length} scoring validator</span>`,
  m&&m.ours?`<span class="chip">on-chain rank ${m.ours.rank_among_rewarded??'—'}</span>`:''
].join('');

// ---- tiles ----
const cutNeed10=g.cf_needed_cut10, our=g.our_cf_mean;
const tiles=[
  {k:'Our base (TWS×DFF)',v:fmt(g.our_base,0),note:`TWS ${fmt(g.TWS,0)} · DFF ${fmt(g.DFF,2)}`,cls:'us'},
  {k:'Predicted final',v:fmt(g.our_final_mean,1),note:`CF ${fmt(our,3)} · stable across seeds`,cls:'us'},
  {k:'Min to place (cut10)',v:fmt(g.latest_cut10,1),note:`cut5 ${fmt(g.latest_cut5,1)}`,cls:'flag'},
  {k:'CF we produce',v:fmt(our,3),note:`max seen ${fmt(g.our_cf_max,3)}`,cls:''},
  {k:'CF needed (cut10)',v:fmt(cutNeed10,2),note:`${(cutNeed10/our).toFixed(1)}× our CF`,cls:'flag'},
];
$('#tiles').innerHTML=tiles.map(t=>`<div class="tile ${t.cls}"><div class="k">${t.k}</div>
  <div class="v mono">${t.v}</div><div class="note">${t.note}</div></div>`).join('');
$('#callout').innerHTML=`<b>The whole gap is CF.</b> Our structural base (${fmt(g.our_base,0)}) already beats many
 rewarded miners — in the latest round the rank-5 finisher had base ${fmt(DATA.latest_field.find(f=>f.rank===5)?.base,0)}.
 But consistency-factor is the dominant multiplier, and ours sits at ${fmt(our,3)} across every seed sampled, where
 placing needs ≈${fmt(cutNeed10,2)}. That multiplier — not base, not diversity — is the open problem.`;

// ---- scatter: CF (y) vs base (x) ----
(function(){
  const W=760,H=440,L=58,R=18,T=18,B=52, pw=W-L-R, ph=H-T-B;
  const F=DATA.latest_field;
  const maxBase=Math.max(300,...F.map(f=>f.base))*1.02;
  const maxCF=Math.max(0.72,...F.map(f=>f.cf))*1.05;
  const x=b=>L+b/maxBase*pw, y=c=>T+ph-c/maxCF*ph;
  let s=`<rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="none"/>`;
  // grid + axes
  for(let i=0;i<=6;i++){const gx=L+pw*i/6,bv=maxBase*i/6;
    s+=`<line class="grid" x1="${gx}" y1="${T}" x2="${gx}" y2="${T+ph}"/>`;
    s+=`<text class="ax" x="${gx}" y="${T+ph+16}" text-anchor="middle">${bv.toFixed(0)}</text>`;}
  for(let i=0;i<=6;i++){const gy=T+ph*i/6,cv=maxCF*(1-i/6);
    s+=`<line class="grid" x1="${L}" y1="${gy}" x2="${L+pw}" y2="${gy}"/>`;
    s+=`<text class="ax" x="${L-8}" y="${gy+4}" text-anchor="end">${cv.toFixed(2)}</text>`;}
  // iso-final curves cf = final/base
  [10,20,33.92,50].forEach(fin=>{
    const cut=Math.abs(fin-DATA.gap.latest_cut10)<0.01;
    let pts=[];for(let px=0;px<=pw;px+=6){const bv=px/pw*maxBase; if(bv<=0)continue;
      const cf=fin/bv; if(cf<=maxCF)pts.push(`${L+px},${y(cf)}`);}
    if(pts.length>1){s+=`<polyline class="iso ${cut?'cut':''}" points="${pts.join(' ')}"/>`;
      const last=pts[pts.length-1].split(',');
      s+=`<text class="isolabel ${cut?'cut':''}" x="${+last[0]-2}" y="${+last[1]-4}" text-anchor="end"${cut?' style="fill:var(--top10)"':''}>final ${fin===33.92?'= cut10':fin}</text>`;}
  });
  // field points
  F.forEach(f=>{const col=f.rank<=10?'var(--top10)':'var(--field)';const r=f.rank<=10?5:3.2;
    s+=`<circle cx="${x(f.base)}" cy="${y(f.cf)}" r="${r}" fill="${col}" fill-opacity="${f.rank<=10?0.95:0.5}"
      stroke="var(--surface)" stroke-width="1" data-tip="UID ${f.uid} · rank ${f.rank}&#10;final ${f.final} = base ${f.base} × CF ${f.cf}"/>`;});
  // us star
  const ux=x(DATA.gap.our_base),uy=y(DATA.gap.our_cf_mean);
  s+=`<g transform="translate(${ux},${uy})" data-tip="US (UID ${DATA.our_uid}) predicted&#10;base ${fmt(DATA.gap.our_base,0)} × CF ${fmt(DATA.gap.our_cf_mean,3)} = final ${fmt(DATA.gap.our_final_mean,1)}">
    <rect x="-6" y="-6" width="12" height="12" transform="rotate(45)" fill="var(--us)" stroke="var(--surface)" stroke-width="1.5"/></g>`;
  s+=`<text class="axtitle" x="${L+pw/2}" y="${H-6}" text-anchor="middle">base score  (TWS × DFF)</text>`;
  s+=`<text class="axtitle" transform="translate(16,${T+ph/2}) rotate(-90)" text-anchor="middle">consistency factor (CF)</text>`;
  $('#scatter').innerHTML=s;
})();

// ---- trend ----
(function(){
  const W=760,H=340,L=46,R=16,T=14,B=60, pw=W-L-R, ph=H-T-B;
  const t=DATA.trend; const n=t.length;
  const maxY=Math.max(...t.map(d=>d.top1))*1.05;
  const x=i=>L+(n<=1?pw/2:pw*i/(n-1)), y=v=>T+ph-v/maxY*ph;
  let s='';
  for(let i=0;i<=5;i++){const gy=T+ph*i/5,v=maxY*(1-i/5);
    s+=`<line class="grid" x1="${L}" y1="${gy}" x2="${L+pw}" y2="${gy}"/>`;
    s+=`<text class="ax" x="${L-8}" y="${gy+4}" text-anchor="end">${v.toFixed(0)}</text>`;}
  const series=[['top1','var(--seq1)'],['cut5','var(--seq2)'],['cut10','var(--seq3)']];
  series.forEach(([k,c])=>{const pts=t.map((d,i)=>`${x(i)},${y(d[k])}`).join(' ');
    s+=`<polyline fill="none" stroke="${c}" stroke-width="2" points="${pts}"/>`;});
  // our predicted final reference
  const yf=y(DATA.gap.our_final_mean);
  s+=`<line x1="${L}" y1="${yf}" x2="${L+pw}" y2="${yf}" stroke="var(--us)" stroke-width="2" stroke-dasharray="6 4"/>`;
  s+=`<text class="isolabel" x="${L+4}" y="${yf-5}" style="fill:var(--us)">our final ≈ ${fmt(DATA.gap.our_final_mean,0)}</text>`;
  // x ticks (every ~6th)
  t.forEach((d,i)=>{if(i%Math.ceil(n/6)===0||i===n-1){
    s+=`<text class="ax" x="${x(i)}" y="${T+ph+16}" text-anchor="middle">${d.t.slice(5,10)}</text>`;}});
  // hover hit points on cut10
  t.forEach((d,i)=>{s+=`<circle cx="${x(i)}" cy="${y(d.cut10)}" r="9" fill="transparent"
    data-tip="${d.t}&#10;top1 ${d.top1} · cut5 ${d.cut5} · cut10 ${d.cut10}&#10;${d.n} miners"/>`;});
  s+=`<text class="axtitle" x="${L+pw/2}" y="${H-6}" text-anchor="middle">round (date)</text>`;
  $('#trend').innerHTML=s;
})();

// ---- anatomy ----
(function(){
  const a=DATA.winner_anatomy,g=DATA.gap;
  const rows=[['Consistency factor',g.our_cf_mean,a.median_CF,3],
             ['Base (TWS×DFF)',g.our_base,a.median_base,0],
             ['Diversity (DFF)',g.DFF,a.median_DFF,2],
             ['Weighted score (TWS)',g.TWS,a.median_TWS,0]];
  let h='<table><thead><tr><th>Metric</th><th>Us</th><th>Winner median</th><th>Gap</th></tr></thead><tbody>';
  rows.forEach(([k,u,w,d])=>{const ok=u>=w;h+=`<tr><td>${k}</td><td class="mono">${fmt(u,d)}</td>
    <td class="mono">${fmt(w,d)}</td><td class="mono" style="color:${ok?'var(--good)':'var(--bad)'}">
    ${ok?'✓ ahead':fmt(w-u,d)+' short'}</td></tr>`;});
  $('#anat').innerHTML=h+'</tbody></table>';
})();

// ---- latest top 12 ----
(function(){
  $('#lt-task').textContent='task '+(DATA.latest_task||'').slice(0,8);
  const rd=DATA.reward_distribution;
  const tb=$('#ltable tbody');
  tb.innerHTML=DATA.latest_field.slice(0,12).map(f=>{
    const rw=f.rank<=10?(rd[f.rank-1]*100).toFixed(1)+'%':'—';
    const us=f.uid===DATA.our_uid?' class="usrow"':'';
    return `<tr${us}><td>${f.rank}</td><td>${f.uid}</td><td class="mono">${fmt(f.cf,3)}</td>
      <td class="mono">${fmt(f.base,0)}</td><td class="mono">${fmt(f.final,1)}</td><td class="mono">${rw}</td></tr>`;
  }).join('');
})();

// ---- reward bars ----
(function(){
  const rd=DATA.reward_distribution,mx=Math.max(...rd);
  $('#reward').innerHTML=rd.map((v,i)=>`<div class="b ${i===0?'r1':''}" style="height:${v/mx*100}%">
    <span class="lab">${(v*100).toFixed(0)}%</span><span class="rk">${i+1}</span></div>`).join('');
})();

// ---- frontier ----
$('#frontier').innerHTML=[
  `<li><b>Base is solved, honestly.</b> Seed-safe GC tuning (non-seed, PAM-distal, within max_mismatches) plus greedy allocation lifted our base to ${fmt(DATA.gap.our_base,0)} — above the latest rank-5 finisher — with the targeting seed and real specificity untouched.</li>`,
  `<li><b>CF is the wall.</b> Our CF is ${fmt(DATA.gap.our_cf_mean,3)} and does not move across seeds (it is not a spike we can wait for). Winners run ${fmt(DATA.winner_anatomy.median_CF,2)}.</li>`,
  `<li><b>Ruled out:</b> GC-spread, distance-spread and bimodal feature layouts — none raised CF in testing.</li>`,
  `<li><b>Next tests (offline, official scorer):</b> vary the stage-4 features we have not swept (energy via region, microhomology, consistency) and submission cardinality, to find whether any legitimate submission structure raises cross-validated CF at high DFF.</li>`,
  `<li><b>Off the table:</b> editing the targeting seed to fake uniqueness, and extra UIDs against the one-per-owner rule. Neither is an optimization; both are excluded on principle.</li>`,
].join('');
$('#foot').innerHTML=`Source: validator leaderboard /api/v3/miners/scores (${DATA.n_rows.toLocaleString()} scored rows, ${DATA.n_rounds} rounds) + on-chain metagraph. `+
  `Historical UID-${DATA.our_uid} rows predate our registration (prior occupant). Our rounds appear as the box posts them; this page refreshes from the live tracker.`;

// ---- tooltips (delegated) ----
document.addEventListener('mouseover',e=>{const el=e.target.closest('[data-tip]');if(el)showTT(e,el.dataset.tip.replace(/&#10;/g,'<br>'));});
document.addEventListener('mousemove',e=>{if(tt.style.opacity==1){const el=e.target.closest('[data-tip]');if(el)showTT(e,el.dataset.tip.replace(/&#10;/g,'<br>'));else hideTT();}});
document.addEventListener('mouseout',e=>{if(e.target.closest('[data-tip]'))hideTT();});
</script>
"""


def render(data):
    return TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    out = sys.argv[2] if len(sys.argv) > 2 else "index.html"
    open(out, "w").write(render(data))
    print(f"wrote {out} ({len(render(data))} bytes)")
