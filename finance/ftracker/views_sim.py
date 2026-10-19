"""The wealth simulator: what your money could become, with a yearly return, a number of years, saving, fees, inflation and tax."""
import json
import sqlite3
from html import escape as esc

from . import balances, summary
from .views import Ctx, _form, layout

SIM_JS = r"""
(function(){
var $=function(id){return document.getElementById(id)};
var D=JSON.parse($('simdata').textContent);
var NUM=function(v){return Math.round(v).toLocaleString('en-US')};
var money=function(v){return (v<0?'-':'')+'₪'+NUM(Math.abs(v))};
var compact=function(v){var a=Math.abs(v);return '₪'+(a>=1e6?(a/1e6).toFixed(a>=1e7?0:1)+'M':a>=1e3?Math.round(a/1e3)+'k':Math.round(a))};
function val(id){var v=parseFloat($(id).value);return isFinite(v)?v:0}
function params(){return {start:Math.max(0,val('start')),save:Math.max(0,val('save')),grow:val('grow'),fee:Math.max(0,val('fee')),
  inf:val('inf'),taxp:Math.max(0,Math.min(100,val('tax'))),real:$('real').checked,years:Math.max(1,Math.round(val('years'))),target:Math.max(0,val('target'))}}
function run(rate,p){
  var m=Math.pow(1+Math.max(-99,rate-p.fee)/100,1/12)-1, im=Math.pow(1+p.inf/100,1/12)-1;
  var bal=p.start,put=p.start,adj=p.start,c=p.save,out=[{y:0,bal:bal,put:put,adj:adj}];
  for(var y=1;y<=p.years;y++){for(var k=0;k<12;k++){bal=bal*(1+m)+c;put+=c;adj=adj*(1+im)+c}
    c=c*(1+p.grow/100);out.push({y:y,bal:bal,put:put,adj:adj})}
  return out}
function after(row,p){var basis=p.real?row.adj:row.put;var tax=Math.max(0,row.bal-basis)*p.taxp/100;return {tax:tax,net:row.bal-tax}}
function svg(low,mid,hi,p){
  var W=640,H=280,L=60,R=14,T=14,B=30,n=p.years;
  var top=0;hi.forEach(function(r){top=Math.max(top,r.bal)});top=top||1;
  var mag=Math.pow(10,Math.floor(Math.log10(top/4)));var step=[1,2,2.5,5,10].map(function(s){return s*mag}).filter(function(s){return s>=top/4})[0];top=Math.ceil(top/step)*step;
  var sx=function(y){return L+(W-L-R)*y/n},sy=function(v){return T+(H-T-B)*(1-v/top)};
  var o='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Simulated wealth over the years" class="chart">';
  for(var t=0;t<=top+1e-9;t+=step){o+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+sy(t).toFixed(1)+'" y2="'+sy(t).toFixed(1)+'" class="grid"/><text x="'+(L-8)+'" y="'+(sy(t)+4).toFixed(1)+'" text-anchor="end" class="axis">'+compact(t)+'</text>'}
  var poly=function(a,b){var u=a.map(function(r,i){return sx(i).toFixed(1)+','+sy(b?b(r):r.put).toFixed(1)}).join(' ');return u};
  var putLine=mid.map(function(r,i){return sx(i).toFixed(1)+','+sy(r.put).toFixed(1)}).join(' ');
  var balLine=mid.map(function(r,i){return sx(i).toFixed(1)+','+sy(r.bal).toFixed(1)}).join(' ');
  o+='<polygon points="'+putLine+' '+sx(n).toFixed(1)+','+sy(0)+' '+sx(0).toFixed(1)+','+sy(0)+'" fill="var(--c1)" opacity=".85"/>';
  o+='<polygon points="'+balLine+' '+putLine.split(' ').reverse().join(' ')+'" fill="var(--c3)" opacity=".85"/>';
  o+='<polyline points="'+poly(hi,function(r){return r.bal})+'" fill="none" stroke="var(--ink2)" stroke-width="1.5" stroke-dasharray="5 4"/>';
  o+='<polyline points="'+poly(low,function(r){return r.bal})+'" fill="none" stroke="var(--ink2)" stroke-width="1.5" stroke-dasharray="2 4"/>';
  o+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+sy(0)+'" y2="'+sy(0)+'" class="baseline"/>';
  var every=Math.max(1,Math.ceil(n/8));
  for(var y=0;y<=n;y++){var r=mid[y],a=after(r,p);
    o+='<rect x="'+(sx(y)-(W-L-R)/n/2).toFixed(1)+'" y="'+T+'" width="'+((W-L-R)/n).toFixed(1)+'" height="'+(H-T-B)+'" fill="transparent" data-tip="'+
      'Year '+y+': '+money(r.bal)+' ('+money(r.put)+' put in, '+money(r.bal-r.put)+' growth). After tax '+money(a.net)+'"/>';
    if(y%every===0||y===n)o+='<text x="'+sx(y).toFixed(1)+'" y="'+(H-8)+'" text-anchor="middle" class="axis">'+y+'</text>'}
  if(p.target>0&&p.target<=top){o+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+sy(p.target).toFixed(1)+'" y2="'+sy(p.target).toFixed(1)+'" stroke="var(--critical)" stroke-width="1.5"/><text x="'+(W-R)+'" y="'+(sy(p.target)-5).toFixed(1)+'" text-anchor="end" class="direct">Target '+compact(p.target)+'</text>'}
  return o+'</svg>'}
function render(){
  var p=params(),rate=val('rate'),spread=val('spread');
  var mid=run(rate,p),low=run(rate-spread,p),hi=run(rate+spread,p),last=mid[mid.length-1],a=after(last,p);
  var real=a.net/Math.pow(1+p.inf/100,p.years);
  var T=function(id,v){$(id).textContent=v};
  T('o_final',money(last.bal));T('o_tax',money(a.tax));T('o_net',money(a.net));T('o_real',money(real));
  T('o_put',money(last.put));T('o_growth',money(last.bal-last.put));
  T('o_range','With '+(rate-spread).toFixed(1)+'% a year: '+money(after(low[low.length-1],p).net)+' after tax. With '+(rate+spread).toFixed(1)+'%: '+money(after(hi[hi.length-1],p).net)+' after tax.');
  var hit=null;if(p.target>0){for(var i=0;i<mid.length;i++){if(after(mid[i],p).net>=p.target){hit=i;break}}}
  var need=function(){var lo=-5,hi=80;for(var i=0;i<50;i++){var mid2=(lo+hi)/2,r=run(mid2,p),e=after(r[r.length-1],p).net;if(e>=p.target)hi=mid2;else lo=mid2}return hi};
  T('o_target',p.target>0?((hit===null?'The goal is not reached in '+p.years+' years at '+rate.toFixed(1)+'%.':(hit===0?'You are already there.':'After tax, you reach it in year '+hit+'.'))+
    (hit!==null?'':' To reach it in '+p.years+' years you would need about '+(need()>=80?'more than 80':need().toFixed(1))+'% a year (before fees).')):'');
  $('g_wrap').hidden=!(p.target>0);
  if(p.target>0){var dt=new Date();dt.setFullYear(dt.getFullYear()+p.years);$('g_amount').value=Math.round(p.target);$('g_by').value=dt.toISOString().slice(0,10);$('g_return').value=rate}
  $('chart').innerHTML='<div class="legend"><span><i style="background:var(--c1)"></i>What you put in</span><span><i style="background:var(--c3)"></i>Growth</span>'+
    '<span><i style="background:var(--ink2)"></i>Dashed: '+(rate+spread).toFixed(1)+'% and '+(rate-spread).toFixed(1)+'%</span></div>'+svg(low,mid,hi,p);
  var rows='';mid.forEach(function(r,y){var x=after(r,p);rows+='<tr><td>'+y+'</td><td class="n">'+money(r.put)+'</td><td class="n">'+money(r.bal-r.put)+'</td><td class="n">'+money(r.bal)+'</td><td class="n">'+money(x.net)+'</td><td class="n">'+money(x.net/Math.pow(1+p.inf/100,y))+'</td></tr>'});
  $('rows').innerHTML=rows;
  try{localStorage.setItem('sim',JSON.stringify(read()))}catch(e){}
}
var IDS=['start','rate','years','save','grow','fee','inf','tax','target','spread'];
function read(){var o={real:$('real').checked};IDS.forEach(function(i){o[i]=$(i).value});return o}
function pair(a,b){var A=$(a),B=$(b);A.addEventListener('input',function(){B.value=A.value;render()});B.addEventListener('input',function(){var v=parseFloat(B.value);if(isFinite(v))A.value=v;render()})}
pair('rate_r','rate');pair('years_r','years');pair('grow_r','grow');pair('tax_r','tax');
['start','save','fee','inf','target','spread'].forEach(function(i){$(i).addEventListener('input',render)});
$('real').addEventListener('change',render);
document.querySelectorAll('input[name=from]').forEach(function(r){r.addEventListener('change',function(){if(r.checked){$('start').value=Math.round(D[r.value]);render()}})});
$('reset').addEventListener('click',function(){try{localStorage.removeItem('sim')}catch(e){}location.reload()});
try{var s=JSON.parse(localStorage.getItem('sim')||'null');if(s){IDS.forEach(function(i){if(s[i]!==undefined)$(i).value=s[i]});$('real').checked=!!s.real;
  $('rate_r').value=$('rate').value;$('years_r').value=$('years').value;$('grow_r').value=$('grow').value;$('tax_r').value=$('tax').value}}catch(e){}
render();
})();
"""


def _sources(conn: sqlite3.Connection, today: str) -> dict:
    """Where the starting amount can come from, and what you typically keep each month."""
    out = {"all": 0.0, "liquid": 0.0, "save": 0.0}
    try:
        nw = balances.net_worth(conn, today, "ILS")
        out["all"] = round(nw["total"], 0)
        out["liquid"] = round(sum(l["value"] for l in nw["lines"] if l["kind"] not in ("real_estate", "loan")), 0)
    except LookupError:
        pass
    months = [m for m in summary.months_available(conn) if m < today[:7]][-6:]
    if months:
        kept = [summary.month_overview(conn, m) for m in months]
        kept = [k["income"] - k["spending"] for k in kept if k["income"]]
        if kept:
            out["save"] = max(0.0, round(sum(kept) / len(kept) / 100) * 100)
    return out


def _slider(key: str, label: str, lo: float, hi: float, step: float, value: float, unit: str, hint: str = "") -> str:
    return (f'<div class="simrow"><label for="{key}">{esc(label)}</label>'
            f'<input type="range" id="{key}_r" min="{lo}" max="{hi}" step="{step}" value="{value}" aria-label="{esc(label)} slider">'
            f'<span class="simnum"><input type="number" id="{key}" step="{step}" value="{value}" inputmode="decimal"> {esc(unit)}</span>'
            f'{f"<small class=cap>{esc(hint)}</small>" if hint else ""}</div>')


def simulator_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    src = _sources(conn, ctx.today)
    data = json.dumps({"all": src["all"], "liquid": src["liquid"]})
    start = src["liquid"] or src["all"]
    sliders = (_slider("rate", "Return per year", 0, 20, 0.1, 6.0, "%", "Before fees. Try a few values.")
               + _slider("years", "For how many years", 1, 60, 1, 20, "years")
               + _slider("grow", "Raise the monthly saving each year by", 0, 15, 0.5, 0.0, "%", "For example 3% if your pay and saving rise.")
               + _slider("tax", "Capital gains tax at the end", 0, 50, 1, 25, "%", "Taken from the gain only, not from what you put in."))
    body = f"""
<h1>Wealth simulator</h1>
<p class="muted">What your money could be worth if it grows by a steady percentage each year. These are not predictions. Real returns go up and down.</p>
<script type="application/json" id="simdata">{data}</script>
<div class="simgrid">
<section class="card"><h2>Your numbers</h2>
<div class="simrow"><span>Start from</span><span class="simnum">
<label><input type="radio" name="from" value="liquid" checked> Without property ({'₪{:,.0f}'.format(src['liquid'])})</label>
<label><input type="radio" name="from" value="all"> Everything counted ({'₪{:,.0f}'.format(src['all'])})</label></span></div>
<div class="simrow"><label for="start">Starting amount</label><span class="simnum">₪ <input type="number" id="start" step="1000" value="{start:.0f}" inputmode="decimal"></span></div>
<div class="simrow"><label for="save">Saved each month</label><span class="simnum">₪ <input type="number" id="save" step="100" value="{src['save']:.0f}" inputmode="decimal"></span>
<small class="cap">Starts at what you kept on average in your last months.</small></div>
{sliders}
<div class="simrow"><label for="fee">Fees per year</label><span class="simnum"><input type="number" id="fee" step="0.1" value="0.5" inputmode="decimal"> %</span>
<small class="cap">Fund and management fees. They reduce the return.</small></div>
<div class="simrow"><label for="inf">Inflation per year</label><span class="simnum"><input type="number" id="inf" step="0.1" value="2.5" inputmode="decimal"> %</span>
<small class="cap">Used to show the result in today's money.</small></div>
<div class="simrow"><span></span><label class="simnum"><input type="checkbox" id="real" checked> Tax only the gain above inflation (as capital gains tax works in Israel)</label></div>
<div class="simrow"><label for="spread">Show a range of ±</label><span class="simnum"><input type="number" id="spread" step="0.5" value="2" inputmode="decimal"> % a year</span></div>
<div class="simrow"><label for="target">My goal</label><span class="simnum">₪ <input type="number" id="target" step="10000" value="" placeholder="optional" inputmode="decimal"></span>
<small class="cap" id="o_target"></small></div>
<div id="g_wrap" hidden>{_form(ctx, "/goals", '<input type="hidden" name="back" value="/wealth"><input type="hidden" name="currency" value="ILS"><input type="hidden" name="name" id="g_name" value="Simulator goal">'
  '<input type="hidden" name="amount" id="g_amount"><input type="hidden" name="by" id="g_by"><input type="hidden" name="return" id="g_return"><button type="submit">Save my goal on the Wealth page</button>', "")}</div>
<p><button class="quiet" id="reset" type="button">Start over</button></p>
</section>
<section>
<div class="trio" style="margin-top:0"><div><small>After tax, at the end</small><b id="o_net">–</b></div>
<div><small>In today's money</small><b id="o_real">–</b></div><div><small>Tax at the end</small><b id="o_tax">–</b></div></div>
<p class="cap" id="o_range"></p>
<div class="card"><div id="chart"></div>
<p class="cap">Before tax: <b id="o_final">–</b>. You put in <b id="o_put">–</b> and it grew by <b id="o_growth">–</b>.</p></div>
<details class="card" style="margin-top:12px"><summary><b>Year by year</b></summary><div style="overflow-x:auto"><table><thead><tr><th>Year</th><th class="n">Put in</th><th class="n">Growth</th>
<th class="n">Total</th><th class="n">After tax</th><th class="n">After tax, today's money</th></tr></thead><tbody id="rows"></tbody></table></div></details>
</section></div>
<script>{SIM_JS}</script>"""
    return layout(conn, ctx, "/wealth", "Simulator", body)
