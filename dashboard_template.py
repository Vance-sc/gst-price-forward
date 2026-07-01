# -*- coding: utf-8 -*-
"""HTML template for the GST forward-buy dashboard.
The token /*__DATA__*/ is replaced at build time with `window.APP_DATA = {...}`.
Single self-contained file; Chart.js loaded from CDN.
"""

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GST Meat Co. — Forward-Buy Signal Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Roboto+Condensed:wght@300;400;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{
    --green:#117b53; --red:#a6152e; --amber:#d68a12;
    --ink:#1b1b1b; --muted:#666; --line:#e4e4e4; --bg:#f6f5f2; --card:#fff;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:'Roboto Condensed',system-ui,sans-serif;
       background:var(--bg);color:var(--ink);}
  a{color:var(--green)}
  .wrap{max-width:1180px;margin:0 auto;padding:0 20px 60px}
  header.top{background:#fff;border-bottom:4px solid var(--green);
       box-shadow:0 1px 0 var(--red) inset;}
  .topinner{max-width:1180px;margin:0 auto;padding:18px 20px;
       display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
  .brand{display:flex;align-items:center;gap:14px}
  .shield{width:46px;height:54px;border-radius:6px;flex:none;
       background:linear-gradient(180deg,var(--green) 0 46%,#fff 46% 54%,var(--red) 54% 100%);
       border:2px solid #222;box-shadow:0 2px 5px rgba(0,0,0,.15)}
  h1{font-size:22px;margin:0;font-weight:700;letter-spacing:.3px}
  .sub{font-size:13px;color:var(--muted);font-weight:300;margin-top:2px}
  .gen{font-size:12px;color:var(--muted);text-align:right;font-weight:300}
  .demo{background:var(--amber);color:#fff;text-align:center;font-weight:700;
       padding:7px;font-size:14px;letter-spacing:.4px}
  .warn{background:#fdf3e3;border:1px solid var(--amber);color:#7a5410;
       border-radius:8px;padding:9px 14px;margin:16px 0 0;font-size:13px}
  .board{background:#fff;border:1px solid var(--line);border-radius:10px;
       padding:16px 18px;margin:20px 0;display:flex;gap:22px;flex-wrap:wrap;align-items:center}
  .board .legendttl{font-weight:700;font-size:15px}
  .pill{display:inline-flex;align-items:center;gap:7px;font-weight:700;
       font-size:12px;padding:4px 10px;border-radius:20px;color:#fff}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
  .b-LOCK{background:var(--red)} .b-SPLIT{background:var(--amber)} .b-HOLD{background:var(--green)}
  .legend-item{font-size:13px;color:#444;display:flex;align-items:center;gap:7px}
  .cutout{margin-left:auto;font-size:13px;color:#444;text-align:right}
  .cutout b{font-size:16px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(540px,1fr));gap:20px}
  @media(max-width:600px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
       overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.05)}
  .card-h{padding:14px 16px;border-bottom:1px solid var(--line);
       display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
  .pname{font-weight:700;font-size:18px}
  .punit{font-size:12px;color:var(--muted);font-weight:300}
  .price{text-align:right}
  .price .now{font-size:26px;font-weight:700;line-height:1}
  .price .chg{font-size:12px;font-weight:400;margin-top:3px}
  .up{color:var(--red)} .down{color:var(--green)}
  .gstbar{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;
       background:#f3f7f5;border-bottom:1px solid var(--line);
       padding:8px 16px;font-size:12px;color:#3a4a44}
  .gstbar b{color:var(--green)}
  .gstbar .exp{color:var(--red);font-weight:700}
  .horizons{display:grid;grid-template-columns:1fr 1fr;gap:0}
  .hz{padding:14px 16px}
  .hz+.hz{border-left:1px solid var(--line)}
  .hz-ttl{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.5px;text-transform:uppercase}
  .hz-sig{display:flex;align-items:center;gap:10px;margin:8px 0 6px}
  .score{font-size:30px;font-weight:700;line-height:1}
  .hz-msg{font-size:13px;color:#444;min-height:34px}
  .conf{font-size:11px;color:var(--muted);margin-top:4px}
  .comp{margin-top:10px}
  .comprow{display:flex;align-items:center;gap:8px;font-size:11px;margin:4px 0}
  .comprow .lbl{width:74px;color:var(--muted)}
  .comprow .val{width:30px;text-align:right;color:var(--muted)}
  .bar{flex:1;height:7px;background:#eee;border-radius:4px;overflow:hidden;position:relative}
  .bar i{position:absolute;top:0;bottom:0;width:2px;background:#bbb;left:50%}
  .bar b{display:block;height:100%;border-radius:4px}
  .chartbox{padding:10px 12px 14px}
  canvas{width:100%!important;height:210px!important}
  .meta{font-size:11px;color:var(--muted);padding:0 16px 14px;display:flex;
       justify-content:space-between;flex-wrap:wrap;gap:6px}
  details.method{margin-top:26px;background:#fff;border:1px solid var(--line);
       border-radius:10px;padding:6px 18px}
  details.method summary{cursor:pointer;font-weight:700;padding:10px 0}
  details.method p,details.method li{font-size:13px;color:#444;line-height:1.55}
  .foot{margin-top:24px;font-size:12px;color:var(--muted);line-height:1.6;text-align:center}
</style>
</head>
<body>
<header class="top">
  <div class="topinner">
    <div class="brand">
      <div class="shield" title="GST Meat Co."></div>
      <div>
        <h1>Forward-Buy Signal Board</h1>
        <div class="sub">GST's top 5 beef products &middot; USDA boxed-beef quotes &rarr; 30 / 60-day lock guidance</div>
      </div>
    </div>
    <div class="gen" id="gen"></div>
  </div>
</header>
<div id="demoBanner"></div>

<div class="wrap">
  <div id="warnings"></div>
  <div class="board">
    <span class="legendttl">How to read it:</span>
    <span class="legend-item"><span class="pill b-LOCK"><span class="dot" style="background:#fff"></span>LOCK</span> Upward pressure — locking looks favorable</span>
    <span class="legend-item"><span class="pill b-SPLIT"><span class="dot" style="background:#fff"></span>SPLIT</span> Mixed — lock part of the volume</span>
    <span class="legend-item"><span class="pill b-HOLD"><span class="dot" style="background:#fff"></span>HOLD</span> Soft — little urgency to lock</span>
    <span class="cutout" id="cutout"></span>
  </div>

  <div class="grid" id="grid"></div>

  <details class="method">
    <summary>How the v2 score works (and what it can't do)</summary>
    <p><b>The Lock Score (0-100)</b> blends four signals, each measured against
    its own trailing 250-day history. Each product tracks <b>one exact USDA
    item and grade</b> (shown on its card) — no averaging across cuts or grades.</p>
    <ul>
      <li><b>Relative value (40%)</b> — this cut's price relative to the Choice
      cutout, vs its own norm. Cheap vs the cutout = high score. The strongest
      validated signal for every product.</li>
      <li><b>Momentum (25%, contrarian)</b> — these cuts mean-revert over 30-60
      days, so a recent run-up LOWERS the score and a dip raises it. (A v1
      trend-following version backtested inverted and was replaced.)</li>
      <li><b>Volume (20%)</b> — heavier-than-usual negotiated volume has
      preceded price strength, especially for chuck roll and knuckle.</li>
      <li><b>Choice/Select spread (15%)</b> — an unusually wide quality spread
      has preceded softness.</li>
    </ul>
    <p><b>Validation:</b> weights and thresholds were calibrated on 2019-2023
    and tested out-of-sample on 2024-2026 (walk-forward, no lookahead). Pooled
    test results: 30-day LOCK days preceded +2.4% average moves vs −0.7% on
    HOLD days; 60-day LOCK +4.6% vs HOLD −2.6%. Each card shows the validated
    hit rate for its current bucket — that figure, not the score itself, is
    the confidence measure. Past behavior is no guarantee; supply shocks and
    demand swings can override any signal.</p>
    <p><b>Basis note:</b> USDA quotes are the packer&rarr;wholesale price. Your
    vendor cost tracks them with a lag and a spread, so read the <i>direction</i>,
    not the dollar figure, as your cost signal.</p>
  </details>

  <div class="foot" id="foot"></div>
</div>

<script>/*__DATA__*/</script>
<script>
const D = window.APP_DATA;
const fmt = (n)=> n==null? '—' : n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const compColor = (v)=> v>=62?'var(--red)': v>=45?'var(--amber)':'var(--green)';
const esc = (s)=> String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

document.getElementById('gen').innerHTML =
  'Built '+esc(D.meta.generated_utc)+
  (D.meta.last_market_date? '<br>Market date: <b>'+esc(D.meta.last_market_date)+'</b>':'')+
  '<br>Source: USDA AMS LM_XB403';

if(D.meta.is_demo){
  document.getElementById('demoBanner').innerHTML =
    '<div class="demo" role="alert">SAMPLE DATA — not real market prices. '+
    'The live USDA fetch failed or demo mode was forced; see the Actions log.</div>';
}
if(D.meta.warnings && D.meta.warnings.length){
  document.getElementById('warnings').innerHTML = D.meta.warnings.map(w=>
    '<div class="warn" role="alert">&#9888;&nbsp; '+esc(w)+'</div>').join('');
}
if(D.meta.cutout){
  const c=D.meta.cutout;
  const chg = c.choice_chg_1d==null?'':(' <span class="'+(c.choice_chg_1d>=0?'up':'down')+'">('+
    (c.choice_chg_1d>=0?'+':'')+c.choice_chg_1d+')</span>');
  document.getElementById('cutout').innerHTML =
    'Choice Cutout <b>'+fmt(c.choice)+'</b>'+chg+
    (c.select? '<br>Select '+fmt(c.select):'')+
    '<br><span style="font-size:11px">'+esc(c.date)+'</span>';
}

const grid = document.getElementById('grid');
const order = Object.keys(D.products);

function bar(v,label){
  return '<div class="bar" role="img" aria-label="'+label+' '+v+' of 100"><i></i><b style="width:'+v+'%;background:'+compColor(v)+'"></b></div>';
}
const sigColor = s => s==='LOCK'?'var(--red)': s==='SPLIT'?'var(--amber)':'var(--green)';
function hz(h,d){
  const c=d.components;
  const v=d.validation;
  const vline = v ? '<div class="conf">Out-of-sample ’24-’26: this bucket averaged <b>'+
    (v.mean>=0?'+':'')+v.mean+'%</b> fwd, price rose '+Math.round(v.hit*100)+'% of days (n='+v.n+')</div>' : '';
  return '<div class="hz">'+
    '<div class="hz-ttl">'+h+'-Day Outlook</div>'+
    '<div class="hz-sig"><span class="score" style="color:'+sigColor(d.signal)+'">'+d.score+'</span>'+
       '<span class="pill b-'+d.signal+'">'+d.signal+'</span></div>'+
    '<div class="hz-msg">'+esc(d.message)+'</div>'+
    '<div class="conf">Confidence: <b>'+esc(d.confidence)+'</b> (validated hit rate)</div>'+
    vline+
    '<div class="comp">'+
      '<div class="comprow"><span class="lbl">Rel value</span>'+bar(c.rel_value,'Relative value')+'<span class="val">'+c.rel_value+'</span></div>'+
      '<div class="comprow"><span class="lbl">Momentum&#8224;</span>'+bar(c.momentum,'Contrarian momentum')+'<span class="val">'+c.momentum+'</span></div>'+
      '<div class="comprow"><span class="lbl">Volume</span>'+bar(c.volume,'Volume trend')+'<span class="val">'+c.volume+'</span></div>'+
      '<div class="comprow"><span class="lbl">C/S sprd</span>'+bar(c.cs_spread,'Choice-Select spread')+'<span class="val">'+c.cs_spread+'</span></div>'+
    '</div></div>';
}

order.forEach((key,idx)=>{
  const p=D.products[key];
  const h30=p.horizons['30'], h60=p.horizons['60'];
  const chg30=p.change_30d_pct;
  const chgCls = chg30>=0?'up':'down';
  const chgSign = chg30>=0?'+':'';
  const g=p.gst||{};
  const money=(n)=> n>=1e6? '$'+(n/1e6).toFixed(1)+'M' : '$'+Math.round(n).toLocaleString();
  const hasMoney = g.sales!=null;
  const gstbar = g.lbs ?
    '<div class="gstbar">'+
      '<span><b>'+g.lbs.toLocaleString()+' lbs/yr</b>'+(hasMoney? ' · '+money(g.sales)+' sales · '+g.gp_pct+'% GP':'')+'</span>'+
      (hasMoney && p.exposure_5pct!=null? '<span class="exp">±5% cost ≈ '+money(p.exposure_5pct)+'/yr at stake</span>':'')+
    '</div>' : '';
  const el=document.createElement('div'); el.className='card';
  el.innerHTML =
    '<div class="card-h">'+
      '<div><div class="pname">'+esc(p.name)+'</div>'+
        '<div class="punit">'+esc(p.unit)+' &middot; '+esc(p.spec)+'</div></div>'+
      '<div class="price"><div class="now">'+fmt(p.current)+'</div>'+
        '<div class="chg '+chgCls+'">'+chgSign+chg30+'% / 30 cal days &nbsp;·&nbsp; vol '+p.volatility_ann_pct+'%</div></div>'+
    '</div>'+
    gstbar+
    '<div class="horizons">'+hz(30,h30)+hz(60,h60)+'</div>'+
    '<div class="chartbox"><canvas id="c'+idx+'" role="img" aria-label="Price history chart for '+esc(p.name)+'"></canvas></div>'+
    '<div class="meta"><span>Vs cutout: '+(p.rv_pct>=0?'+':'')+p.rv_pct+'% against its 250-day norm'+
      (p.rv_pct>1.5?' (rich)':p.rv_pct<-1.5?' (cheap)':'')+'</span>'+
      '<span>&#8224; momentum is contrarian &middot; engine v2</span></div>';
  grid.appendChild(el);

  // chart: show last ~180 pts
  const s=p.series.slice(-180);
  const labels=s.map(x=>x[0]);
  const price=s.map(x=>x[1]);
  const sma=(arr,n)=>arr.map((_,i)=> i<n-1?null: arr.slice(i-n+1,i+1).reduce((a,b)=>a+b,0)/n);
  if(window.Chart){
    new Chart(document.getElementById('c'+idx),{
      type:'line',
      data:{labels,datasets:[
        {label:'Price',data:price,borderColor:'#1b1b1b',borderWidth:1.6,
          pointRadius:0,tension:.15},
        {label:'SMA10',data:sma(price,10),borderColor:'var(--red)',borderWidth:1.2,
          pointRadius:0,borderDash:[4,3],tension:.2},
        {label:'SMA40',data:sma(price,40),borderColor:'var(--green)',borderWidth:1.2,
          pointRadius:0,tension:.2},
      ]},
      options:{responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{legend:{display:true,labels:{boxWidth:16,font:{size:10}}},
          tooltip:{callbacks:{title:(t)=>t[0].label}}},
        scales:{x:{ticks:{maxTicksLimit:6,font:{size:9}},grid:{display:false}},
          y:{ticks:{font:{size:9}},grid:{color:'#f0f0f0'}}}}
    });
  }
});

document.getElementById('foot').innerHTML =
  'Data: USDA Agricultural Marketing Service, Livestock Market News '+
  '(report LM_XB403, LMR DataMart). Decision-support tool — not a price '+
  'forecast or financial advice. v2 signal validated out-of-sample 2024-26; '+
  'past behavior is no guarantee (see backtest.py). Built for GST Meat Co.';
</script>
</body>
</html>
"""
