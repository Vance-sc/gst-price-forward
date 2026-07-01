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
  .board{background:#fff;border:1px solid var(--line);border-radius:10px;
       padding:16px 18px;margin:20px 0;display:flex;gap:22px;flex-wrap:wrap;align-items:center}
  .board .legendttl{font-weight:700;font-size:15px}
  .pill{display:inline-flex;align-items:center;gap:7px;font-weight:700;
       font-size:12px;padding:4px 10px;border-radius:20px;color:#fff}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
  .b-LOCK{background:var(--red)} .b-SPLIT{background:var(--amber)} .b-HOLD{background:var(--green)}
  .legend-item{font-size:13px;color:#444;display:flex;align-items:center;gap:7px}
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
        <div class="sub">GST's top 5 beef products &middot; USDA cutout trends &rarr; 30 / 60-day lock guidance</div>
      </div>
    </div>
    <div class="gen" id="gen"></div>
  </div>
</header>
<div id="demoBanner"></div>

<div class="wrap">
  <div class="board">
    <span class="legendttl">How to read it:</span>
    <span class="legend-item"><span class="pill b-LOCK"><span class="dot" style="background:#fff"></span>LOCK</span> Upward pressure — locking looks favorable</span>
    <span class="legend-item"><span class="pill b-SPLIT"><span class="dot" style="background:#fff"></span>SPLIT</span> Mixed — lock part of the volume</span>
    <span class="legend-item"><span class="pill b-HOLD"><span class="dot" style="background:#fff"></span>HOLD</span> Soft — little urgency to lock</span>
  </div>

  <div class="grid" id="grid"></div>

  <details class="method">
    <summary>How the score works (and what it can't do)</summary>
    <p><b>The Lock Score (0-100)</b> blends three signals. Above ~62 leans LOCK,
    45-62 is SPLIT, below 45 is HOLD.</p>
    <ul>
      <li><b>Momentum (40%)</b> — is the short-term average above the longer-term
      average, and is price rising over the last 20 days? Rising = favors locking.</li>
      <li><b>Seasonality (30%)</b> — how the calendar has historically moved into
      the next 30/60 days at this time of year (grilling season, holidays).</li>
      <li><b>Range position (30%)</b> — where today's price sits in its recent
      range. Low in the range = more room to rise = better time to lock.</li>
    </ul>
    <p><b>What this is not:</b> a forecast. It measures pressure and calendar
    tendency, not the future. Cattle supply shocks, packer margins, and demand
    swings can override any of this. Use it as one input to a Cargill / Zant
    lock conversation, alongside your read of the market. Confidence flags
    "Low" when the three signals disagree or volatility is high.</p>
    <p><b>Basis note:</b> the USDA cutout is the packer&rarr;wholesale price. Your
    vendor cost tracks it with a lag and a spread, so read the <i>direction</i>,
    not the dollar figure, as your cost signal. The "$ at stake" figure uses
    GST's actual annual volume for that product.</p>
  </details>

  <div class="foot" id="foot"></div>
</div>

<script>/*__DATA__*/</script>
<script>
const D = window.APP_DATA;
const fmt = (n)=> n==null? '—' : n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const compColor = (v)=> v>=62?'var(--red)': v>=45?'var(--amber)':'var(--green)';

document.getElementById('gen').innerHTML =
  'Updated '+D.meta.generated+'<br>Source: USDA AMS LM_XB403';

if(D.meta.is_demo){
  document.getElementById('demoBanner').innerHTML =
    '<div class="demo">SAMPLE DATA — not real market prices. '+
    'Add your free USDA API key to show live figures (see README).</div>';
}

const grid = document.getElementById('grid');
const order = Object.keys(D.products);

function bar(v){
  return '<div class="bar"><i></i><b style="width:'+v+'%;background:'+compColor(v)+'"></b></div>';
}
function hz(h,d){
  const c=d.components;
  return '<div class="hz">'+
    '<div class="hz-ttl">'+h+'-Day Outlook</div>'+
    '<div class="hz-sig"><span class="score" style="color:'+compColor(d.score)+'">'+d.score+'</span>'+
       '<span class="pill b-'+d.signal+'">'+d.signal+'</span></div>'+
    '<div class="hz-msg">'+d.message+'</div>'+
    '<div class="conf">Confidence: <b>'+d.confidence+'</b></div>'+
    '<div class="comp">'+
      '<div class="comprow"><span class="lbl">Momentum</span>'+bar(c.momentum)+'</div>'+
      '<div class="comprow"><span class="lbl">Seasonality</span>'+bar(c.seasonality)+'</div>'+
      '<div class="comprow"><span class="lbl">Range room</span>'+bar(c.range)+'</div>'+
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
      (hasMoney? '<span class="exp">±5% cost ≈ '+money(p.exposure_5pct)+'/yr at stake</span>':'')+
    '</div>' : '';
  const el=document.createElement('div'); el.className='card';
  el.innerHTML =
    '<div class="card-h">'+
      '<div><div class="pname">'+p.name+'</div><div class="punit">'+p.unit+'</div></div>'+
      '<div class="price"><div class="now">'+fmt(p.current)+'</div>'+
        '<div class="chg '+chgCls+'">'+chgSign+chg30+'% / 30d &nbsp;·&nbsp; vol '+p.volatility_ann_pct+'%</div></div>'+
    '</div>'+
    gstbar+
    '<div class="horizons">'+hz(30,h30)+hz(60,h60)+'</div>'+
    '<div class="chartbox"><canvas id="c'+idx+'"></canvas></div>'+
    '<div class="meta"><span>Range (120d): '+fmt(p.range_detail.low)+' – '+fmt(p.range_detail.high)+
      '  ·  now at '+p.range_detail.range_pctile+'% of range</span>'+
      '<span>SMA10 '+fmt(p.momentum_detail.sma10)+' vs SMA40 '+fmt(p.momentum_detail.sma40)+'</span></div>';
  grid.appendChild(el);

  // chart: show last ~180 pts
  const s=p.series.slice(-180);
  const labels=s.map(x=>x[0]);
  const price=s.map(x=>x[1]);
  const sma=(arr,n)=>arr.map((_,i)=> i<n-1?null: arr.slice(i-n+1,i+1).reduce((a,b)=>a+b,0)/n);
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
});

document.getElementById('foot').innerHTML =
  'Data: USDA Agricultural Marketing Service, Livestock Market News '+
  '(report LM_XB403). Decision-support tool — not a price forecast or '+
  'financial advice. Built for GST Meat Co.';
</script>
</body>
</html>
"""
