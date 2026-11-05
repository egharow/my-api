"""HTML pages. Every function takes an open connection and returns a string, so they are easy to test."""
import json
import sqlite3
from urllib.parse import quote
from dataclasses import dataclass, field
from html import escape as esc

from datetime import date

from . import balances, charts, commits, discrepancies, expected, fx, goals, reconcile, sheetsync, summary
from . import BUILD
from .charts import money


@dataclass
class Ctx:
    today: str
    who: str = ""
    csrf: str = ""
    flash: str = ""
    flash_kind: str = "ok"
    owners: list[str] = field(default_factory=list)
    inbox_count: int = 0
    data_root: str = ""
    starter: bool = False
    can_shortcut: bool = False
    readonly: bool = False          # a phone on the home network: look, never change
    phone_url: str = ""
    phone_pin: str = ""
    phone_anywhere: str = ""
    closed: frozenset = frozenset()  # months you closed
    tags: tuple = ()                 # (id, name) of your trips and tags
    screens_waiting: tuple = ()      # balance screenshots waiting to be checked
    server: bool = False             # the always-on server: no Quit, a Sign out instead
    user: str = ""                   # who is signed in (server mode)


CSS = """
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#6b6a65;--grid:#e1e0d9;--axis:#c3c2b7;
--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--good:#0ca30c;--goodtext:#006300;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--accent:#2a78d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#a3a198;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--goodtext:#0ca30c;--accent:#3987e5}}
:root[data-theme="dark"]{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#a3a198;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--goodtext:#0ca30c;--accent:#3987e5}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent)}header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 16px}
nav{display:flex;gap:4px;flex-wrap:wrap;align-items:center;max-width:1100px;margin:0 auto}
nav a{padding:12px 10px;color:var(--ink2);text-decoration:none;border-bottom:2px solid transparent}
nav a.on{color:var(--ink);border-color:var(--accent)}nav .sp{flex:1}
nav .badge{background:var(--s2);color:#fff;border-radius:9px;padding:0 6px;font-size:12px;margin-left:4px}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0 12px}h2{font-size:16px;margin:0 0 8px}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;min-width:0}
.tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:16px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .v{font-size:26px;font-weight:600}.tile .l{color:var(--ink2);font-size:13px}.tile .d{font-size:13px;color:var(--muted)}
.muted{color:var(--muted)}small{color:var(--muted)}.chart{width:100%;height:auto}.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .baseline{stroke:var(--axis)}.chart .axis{fill:var(--muted);font-size:11px}.chart .direct{fill:var(--ink);font-size:12px;font-weight:600}
.legend{display:flex;gap:14px;font-size:13px;color:var(--ink2);margin-bottom:6px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.hrow{display:grid;grid-template-columns:minmax(90px,150px) 1fr 110px;gap:8px;align-items:center;padding:3px 0;font-size:14px}
.htrack{background:var(--grid);border-radius:4px;height:10px;overflow:hidden}.hbar{display:block;height:10px;background:var(--s1);border-radius:4px}
.hval{text-align:right}.hval small{margin-left:6px}.hlabel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pbar{background:var(--grid);border-radius:5px;height:10px;overflow:hidden;margin:6px 0}.pfill{display:block;height:10px;background:var(--s1)}
.pfill.on_track,.pfill.reached{background:var(--good)}.pfill.behind{background:var(--serious)}
.chip{display:inline-block;border:1px solid var(--border);border-radius:12px;padding:0 9px;font-size:13px}
.chip.good b{color:var(--goodtext)}.chip.serious b{color:var(--serious)}.chip.warning b{color:var(--warning)}.chip.critical b{color:var(--critical)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}details.tableview{margin-top:8px;font-size:13px}details summary{cursor:pointer;color:var(--ink2)}
form.inline{display:inline}input,select,textarea,button{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--axis);border-radius:6px;padding:5px 8px}
button{cursor:pointer;background:var(--accent);color:#fff;border-color:var(--accent)}button.quiet{background:transparent;color:var(--ink);border-color:var(--axis)}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:12px;border:1px solid var(--border);background:var(--surface)}.flash.err{border-color:var(--critical)}
.banner{border-left:4px solid var(--warning);background:var(--surface);padding:10px 14px;border-radius:6px;margin-bottom:12px}
.item{padding:10px 0;border-bottom:1px solid var(--grid)}.sev{font-weight:600}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.comment{border-left:3px solid var(--axis);padding:2px 10px;margin:8px 0}.comment b{font-size:13px}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);padding:5px 9px;border-radius:6px;font-size:13px;display:none;z-index:9;max-width:320px}

.modes{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.seg{display:inline-flex;border:1px solid var(--axis);border-radius:8px;overflow:hidden}.seg a{padding:5px 11px;font-size:14px;color:var(--ink2);text-decoration:none;white-space:nowrap}
.seg a.on{background:var(--ink);color:var(--surface)}
.monthnav{display:inline-flex;align-items:center;gap:8px}.monthnav .mt{font-size:18px}
.nb{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border:1px solid var(--axis);border-radius:8px;color:var(--ink);text-decoration:none;font-size:18px}
.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:8px 0 6px}
.trio>div{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 14px}
.trio small{color:var(--ink2);display:block;font-size:13px}.trio b{font-size:24px}.trio small.k{color:var(--muted);font-size:12px;margin-top:2px}
.cats{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:4px 14px}
.cat{border-bottom:1px solid var(--grid)}
.cat>summary,.cat.head,.cat.foot{display:grid;grid-template-columns:22px minmax(110px,1fr) minmax(90px,170px) 100px 120px;gap:10px;align-items:center;padding:9px 0;font-size:14px}
.cat>summary{cursor:pointer;list-style:none}.cat>summary::-webkit-details-marker{display:none}
.cat.head{font-size:12px;color:var(--muted)}.cat.foot{font-weight:600;border-bottom:0}
.cat .tw{color:var(--muted);transition:transform .15s}.cat[open]>summary .tw{transform:rotate(90deg)}
.cat .lines{margin:0 0 10px 32px;font-size:13px}
.cat .lines>div{display:grid;grid-template-columns:44px minmax(0,1fr) 80px max-content;gap:10px;padding:5px 0;border-top:1px dashed var(--grid);align-items:start}
.share{display:flex;align-items:center;gap:8px}.share .t{flex:1;height:8px;background:var(--grid);border-radius:4px;min-width:50px}.share .t span{display:block;height:8px;background:var(--s2);border-radius:4px}
.share em{font-style:normal;color:var(--muted);font-size:12px;width:34px;text-align:right}
.n{text-align:right;font-variant-numeric:tabular-nums}.cap{font-size:13px;color:var(--muted)}
.up{color:#b5461c;font-weight:600}.down{color:var(--goodtext);font-weight:600}
.srcrow{display:flex;justify-content:space-between;padding:6px 0;border-top:1px dashed var(--grid)}
.todo{display:flex;gap:8px;flex-wrap:wrap}.todo .chip{background:var(--surface);text-decoration:none;color:var(--ink);padding:5px 12px}
details.chg summary{cursor:pointer;color:var(--accent);font-size:13px}details.chg form{margin-top:4px}
@media (max-width:640px){.hidem{display:none}.trio{grid-template-columns:1fr}.cat>summary,.cat.head,.cat.foot{grid-template-columns:20px minmax(80px,1fr) 90px 100px}.cat .lines>div{grid-template-columns:40px minmax(0,1fr) 70px}.cat .lines>div>span:last-child{grid-column:2/4}}
.acc{list-style:none;margin:0;padding:0}.acc li{display:grid;grid-template-columns:24px minmax(120px,1fr) minmax(120px,200px) 90px minmax(60px,auto);gap:10px;align-items:center;padding:7px 4px;border-bottom:1px solid var(--grid);background:var(--surface)}
.acc li.dragging{opacity:.4}.acc .grab{cursor:grab;color:var(--muted);font-size:18px}.acc li.foldline{display:block;border:2px dashed var(--axis);border-radius:8px;margin:8px 0;padding:6px 12px;cursor:grab;color:var(--ink2);font-size:13px}
.acc .ed summary{cursor:pointer;color:var(--accent);font-size:13px}.acc .ed{grid-column:2/6}.oddrow{display:grid;grid-template-columns:40px minmax(0,1fr) 150px max-content;gap:10px;padding:6px 0;border-top:1px dashed var(--grid);font-size:14px;align-items:start}
@media (max-width:640px){.oddrow{grid-template-columns:36px minmax(0,1fr) 110px}.oddrow>span:last-child{grid-column:2/4}}
@media (max-width:600px){.hrow{grid-template-columns:90px 1fr 88px}.tile .v{font-size:22px}}

html{-webkit-text-size-adjust:100%}
header{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--surface) 92%,transparent);backdrop-filter:blur(8px)}
nav{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}nav::-webkit-scrollbar{display:none}
nav a{white-space:nowrap;font-weight:500;border-bottom-width:3px;padding:13px 12px}nav a:hover{color:var(--ink)}
nav .quit{margin-left:6px;padding:4px 10px;font-size:13px;white-space:nowrap}
h1{font-size:26px;letter-spacing:-.01em;margin:10px 0 14px}h2{font-size:17px}
a{text-underline-offset:2px}.card,.cats,.trio>div,.tile{box-shadow:0 1px 2px rgba(0,0,0,.04)}
.card{border-radius:12px}
button:hover{filter:brightness(1.08)}button.quiet:hover{background:var(--page);filter:none}
button:active{transform:translateY(1px)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
tbody tr:hover td{background:color-mix(in srgb,var(--accent) 4%,transparent)}
.cat>summary:hover{background:color-mix(in srgb,var(--accent) 5%,transparent)}
input:not([type=checkbox]):not([type=radio]),select{min-height:34px}
.flash{border-left:4px solid var(--accent)}.flash.err{border-left-color:var(--critical)}
.foot{text-align:center;color:var(--muted);font-size:12px;margin:24px 0 16px}
.stickybar{position:sticky;top:50px;z-index:4;box-shadow:0 4px 10px rgba(0,0,0,.06)}
.dataline{color:var(--muted);font-size:13px;margin:0 0 10px}
.dataline b{color:var(--ink2);font-weight:600}
.bud{display:grid;grid-template-columns:minmax(90px,150px) 1fr 130px;gap:10px;align-items:center;padding:7px 0;border-top:1px dashed var(--grid);font-size:14px}
.bud .track{position:relative;height:10px;background:var(--grid);border-radius:5px}.bud .fill{position:absolute;left:0;top:0;height:10px;background:var(--s1);border-radius:5px}
.bud .fill.over{background:var(--serious)}.bud .mark{position:absolute;top:-4px;width:2px;height:18px;background:var(--ink2)}
@media (max-width:640px){.bud{grid-template-columns:90px 1fr 100px}h1{font-size:22px}main{padding:12px}}
/* chart palette */
.simgrid{display:grid;gap:16px;grid-template-columns:minmax(300px,420px) minmax(0,1fr);align-items:start}@media (max-width:860px){.simgrid{grid-template-columns:1fr}}
.simrow{display:grid;grid-template-columns:1fr auto;gap:4px 10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--grid)}.simrow input[type=range]{grid-column:1/3;width:100%;accent-color:var(--ink)}.simrow small{grid-column:1/3}.simnum input[type=number]{width:110px;text-align:right}.simnum label{display:block}.simgrid .trio b{font-size:21px}.simgrid .trio>div{padding:14px 14px}
.tripgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}.tripcard{display:flex;flex-direction:column;gap:2px;text-decoration:none;color:var(--ink)}.tripcard:hover{border-color:var(--ink)}.tripamt{font-family:var(--num);font-size:22px;font-weight:600;margin-top:6px}
.alert{display:grid;grid-template-columns:14px 1fr auto;gap:10px;align-items:start;padding:8px 0;border-top:1px dashed var(--grid)}.alert:first-of-type{border-top:0}.alert .dot{width:9px;height:9px;border-radius:50%;margin-top:6px;background:var(--axis)}.alert.warning .dot{background:var(--warning)}
.yeartable{font-size:13px}.yeartable th,.yeartable td{padding:5px 6px;white-space:nowrap}.yeartable td.heat a{color:var(--ink);text-decoration:none}.yeartable tr.tot td{border-top:2px solid var(--rule)}
.shotgrid{display:grid;gap:16px;grid-template-columns:minmax(240px,400px) minmax(0,1fr);align-items:start}@media (max-width:860px){.shotgrid{grid-template-columns:1fr}}.shotimg{position:sticky;top:64px}.shotimg img{max-width:100%;border:1px solid var(--border);border-radius:2px}
details.chg summary{white-space:nowrap}
.legend a{color:inherit;text-decoration:none;display:inline-flex;align-items:center;cursor:pointer}.legend a.off{opacity:.4;text-decoration:line-through}.legend a:hover{text-decoration:underline}
svg a{cursor:pointer}svg a:hover rect[fill^="var(--c"]{opacity:.82}svg a:focus-visible{outline:2px solid var(--accent)}
.legend{flex-wrap:wrap;row-gap:4px;column-gap:12px}.legend span{white-space:nowrap}
:root{--c1:#2a78d6;--c2:#eb6834;--c3:#18a999;--c4:#8a5cd6;--c5:#d9a21b;--c6:#d6498e;--cx:#a3a198}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--c1:#3987e5;--c2:#e8743f;--c3:#2bbfae;--c4:#9d75e0;--c5:#e5b53a;--c6:#e063a0;--cx:#6e6d66}}
:root[data-theme="dark"]{--c1:#3987e5;--c2:#e8743f;--c3:#2bbfae;--c4:#9d75e0;--c5:#e5b53a;--c6:#e063a0;--cx:#6e6d66}

/* look and feel */
body{font-feature-settings:"tnum" 1,"cv11" 1;letter-spacing:.005em}
nav a{border-bottom:0;border-radius:8px;margin:7px 1px;padding:6px 13px}
nav a.on{background:color-mix(in srgb,var(--accent) 12%,transparent);color:var(--accent);font-weight:600}
nav a:hover{background:color-mix(in srgb,var(--ink) 5%,transparent)}
nav .badge{background:var(--s2);font-weight:600;min-width:20px;text-align:center}
h1{font-weight:700}h3{letter-spacing:-.005em}
.trio>div{position:relative;overflow:hidden;padding:14px 18px}
.trio>div::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--axis)}
.trio>div:nth-child(1)::before{background:var(--s1)}.trio>div:nth-child(2)::before{background:var(--s2)}.trio>div:nth-child(3)::before{background:var(--good)}
.trio b{font-size:28px;font-weight:700;letter-spacing:-.01em}
.card,.cats,.trio>div,.tile{box-shadow:0 1px 3px rgba(20,20,10,.06),0 0 0 1px rgba(20,20,10,.02)}
.cats{padding:6px 18px}.cat>summary{padding:12px 0}.cat>summary:hover{background:transparent}.cat>summary:hover .n{color:var(--accent)}
.cat.head{padding-top:12px;text-transform:uppercase;letter-spacing:.04em;font-size:11px}
.share .t{height:9px;border-radius:5px}.share .t span{height:9px;border-radius:5px;background:linear-gradient(90deg,var(--s2),color-mix(in srgb,var(--s2) 70%,#fff))}
.cat .lines{background:color-mix(in srgb,var(--page) 70%,transparent);border-radius:8px;padding:2px 12px;margin:0 0 12px 22px}
.todo .chip{border-radius:999px;transition:background .15s,border-color .15s}.todo .chip:hover{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 7%,var(--surface))}
.todo .chip b{color:var(--accent)}
button{border-radius:8px;padding:6px 14px;font-weight:500}
button.quiet{padding:5px 12px}
input:not([type=checkbox]):not([type=radio]),select{border-radius:8px;border-color:var(--axis);background:var(--surface)}
input:focus,select:focus{border-color:var(--accent);outline:0;box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 20%,transparent)}
.banner{border-radius:10px;border:1px solid var(--border);border-left:4px solid var(--warning);box-shadow:0 1px 3px rgba(20,20,10,.05)}
.banner details summary{font-weight:600;color:var(--ink)}
.adjust{margin-top:12px;background:var(--surface);border:1px dashed var(--axis);border-radius:12px;padding:10px 14px}
.adjust form{margin:0}.adjust ul{margin:8px 0 0;padding-left:18px}
.chk{display:grid;grid-template-columns:30px 1fr auto;gap:10px;align-items:center;padding:12px 2px;border-bottom:1px solid var(--grid)}
.chk:last-child{border-bottom:0}
.chk .mark{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;font-weight:700;font-size:14px;color:#fff;background:var(--good)}
.chk.todo .mark{background:var(--warning);color:#3a2a00}
.chk .cap{display:block}
.btnlink{display:inline-block;padding:4px 14px;border:1px solid var(--accent);border-radius:999px;text-decoration:none;font-size:14px}
.btnlink:hover{background:var(--accent);color:#fff}
.chip.good{background:color-mix(in srgb,var(--good) 10%,transparent);border-color:color-mix(in srgb,var(--good) 40%,transparent)}
@media (max-width:640px){.trio b{font-size:24px}.cats{padding:4px 12px}}
/* design pass: neutrals tinted toward the accent, flat surfaces, one summary strip, tone shown as tint not as a side rail */
:root{--page:#f4f5f8;--surface:#ffffff;--ink:#12151b;--ink2:#444b57;--muted:#677080;--grid:#e3e6ec;--axis:#c3c9d3;--border:rgba(18,21,27,.09);--accent:#2a6fd0;
--tone-warn:#fff6e0;--tone-good:#eaf6ec;--tone-bad:#fdeceb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#0d0f13;--surface:#161a20;--ink:#f1f3f7;--ink2:#c2c8d2;--muted:#979faa;--grid:#252a32;--axis:#38404a;--border:rgba(255,255,255,.09);--accent:#5a9bf0;
--tone-warn:#2b2412;--tone-good:#14261a;--tone-bad:#2d1818}}
:root[data-theme="dark"]{--page:#0d0f13;--surface:#161a20;--ink:#f1f3f7;--ink2:#c2c8d2;--muted:#979faa;--grid:#252a32;--axis:#38404a;--border:rgba(255,255,255,.09);--accent:#5a9bf0;
--tone-warn:#2b2412;--tone-good:#14261a;--tone-bad:#2d1818}
body{font-variant-numeric:tabular-nums}
h1,h2,h3,h4{text-wrap:balance}
.card,.cats,.tile{box-shadow:none;border-color:var(--border)}
header{box-shadow:0 1px 0 var(--border)}
.trio{gap:0;background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;margin:10px 0 8px}
.trio>div{background:transparent;border:0;border-radius:0;box-shadow:none;padding:16px 20px;border-left:1px solid var(--grid)}
.trio>div:first-child{border-left:0}.trio>div::before{display:none}
.trio small{display:flex;align-items:center;gap:8px;text-transform:uppercase;letter-spacing:.06em;font-size:11.5px;font-weight:600}
.trio small.k{text-transform:none;letter-spacing:0;font-weight:400;display:block}
.trio>div:nth-child(1)>small:first-child::before,.trio>div:nth-child(2)>small:first-child::before,.trio>div:nth-child(3)>small:first-child::before{content:"";width:9px;height:9px;border-radius:50%}
.trio>div:nth-child(1)>small:first-child::before{background:var(--s1)}.trio>div:nth-child(2)>small:first-child::before{background:var(--s2)}.trio>div:nth-child(3)>small:first-child::before{background:var(--good)}
.trio b{display:block;font-size:30px;font-weight:650;letter-spacing:-.015em;margin-top:2px}.trio b.neg{color:var(--critical)}
@media (max-width:640px){.trio>div{border-left:0;border-top:1px solid var(--grid);padding:12px 16px}.trio>div:first-child{border-top:0}.trio b{font-size:24px}}
.banner,.flash{border:1px solid var(--border);border-left:1px solid var(--border);background:var(--tone-warn);box-shadow:none;border-radius:10px}
.banner.good{background:var(--tone-good)}.flash{background:var(--surface)}.flash.err{background:var(--tone-bad);border-left-color:var(--border)}
.adjust{border-style:solid;border-color:var(--border);background:transparent}
.cats{border-radius:14px}.cat.head{color:var(--muted)}
.todo .chip{background:var(--surface);border-color:var(--border)}
.todo .chip b{background:var(--accent);color:#fff;border-radius:999px;padding:0 7px;margin-right:4px;font-size:12px}
button:not(.quiet){background:var(--accent);border-color:var(--accent)}
nav a.on{background:color-mix(in srgb,var(--accent) 13%,transparent)}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}

/* ledger: a printed statement. Square corners, rules instead of boxes, figures in a monospaced face, black buttons, one ink-blue for links. */
:root{--page:#efefee;--surface:#ffffff;--ink:#111111;--ink2:#3c3c3e;--muted:#6b6b6e;--grid:#d9d9db;--axis:#b4b4b8;--border:#cfcfd2;--rule:#111111;--accent:#1f3a93;
--good:#1b7a3d;--goodtext:#17652f;--critical:#b3261e;--tone-warn:#ffffff;--tone-good:#ffffff;--tone-bad:#ffffff;
--num:ui-monospace,"SF Mono","Cascadia Mono",Consolas,"Liberation Mono",monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--page:#121212;--surface:#1b1b1c;--ink:#ededed;--ink2:#c9c9cc;--muted:#9a9a9f;--grid:#2c2c2e;--axis:#444447;--border:#38383b;--rule:#ededed;--accent:#8fa8ff;
--good:#4cc27a;--goodtext:#4cc27a;--critical:#ff7a70;--tone-warn:#1b1b1c;--tone-good:#1b1b1c;--tone-bad:#1b1b1c}}
:root[data-theme="dark"]{--page:#121212;--surface:#1b1b1c;--ink:#ededed;--ink2:#c9c9cc;--muted:#9a9a9f;--grid:#2c2c2e;--axis:#444447;--border:#38383b;--rule:#ededed;--accent:#8fa8ff;
--good:#4cc27a;--goodtext:#4cc27a;--critical:#ff7a70;--tone-warn:#1b1b1c;--tone-good:#1b1b1c;--tone-bad:#1b1b1c}
body{font-size:15px}
.n,td.n,th.n,.hval,.tile .v,.trio b,.cat .n,.oddrow .n,.srcrow .n,.cat .lines .n{font-family:var(--num);font-variant-numeric:tabular-nums}
header{background:var(--surface);backdrop-filter:none;box-shadow:none;border-bottom:2px solid var(--rule)}
nav::before{content:"Finance";font-weight:800;letter-spacing:.14em;text-transform:uppercase;font-size:13px;margin-right:22px;color:var(--ink)}
nav a{border-radius:0;margin:0;padding:15px 12px 13px;border-bottom:3px solid transparent;font-weight:500;color:var(--ink2)}
nav a.on{background:transparent;color:var(--ink);font-weight:700;border-bottom-color:var(--rule)}
nav a:hover{background:transparent;color:var(--ink);border-bottom-color:var(--axis)}
nav .badge{border-radius:2px;background:var(--ink);color:var(--surface);font-family:var(--num);font-size:11.5px;padding:0 5px}
@media (max-width:640px){nav::before{display:none}}
h1{font-size:30px;font-weight:800;letter-spacing:-.025em;margin:18px 0 14px}
h2,h4{font-weight:700}
h3{text-transform:uppercase;letter-spacing:.09em;font-size:12.5px!important;color:var(--ink2);border-top:2px solid var(--rule);padding-top:8px;margin-top:26px!important}
.card,.cats,.tile,.adjust,.banner,.flash,.trio,.trio>div,.cat .lines{border-radius:2px}
.card,.cats,.tile{background:var(--surface);border:1px solid var(--border);box-shadow:none}
.card{padding:16px 18px}
.trio{border:1px solid var(--border);border-top:2px solid var(--rule);background:var(--surface)}
.trio>div{border-left:1px solid var(--grid)}
.trio small{color:var(--muted);letter-spacing:.1em;font-size:11px}
.trio>div:nth-child(n)>small:first-child::before{content:none}
.trio b{font-size:30px;font-weight:600;letter-spacing:-.02em}.trio b.neg{color:var(--critical)}
.cat.head{border-bottom:2px solid var(--rule);font-size:11px;letter-spacing:.09em}
.cat>summary:hover{background:color-mix(in srgb,var(--ink) 4%,transparent)}
.cat.foot{border-top:2px solid var(--rule);margin-top:2px}
.share .t,.share .t span,.htrack,.hbar,.pbar,.pfill{border-radius:0}.share .t span{background:var(--ink)}
.cat .lines{background:transparent;border-left:2px solid var(--grid);border-radius:0}
.banner,.flash{background:var(--surface);border:1px solid var(--border);box-shadow:none}
.banner{border-top:2px solid var(--rule)}.flash.err{border-top:2px solid var(--critical);background:var(--surface)}
.adjust{border:1px solid var(--border);border-top:2px solid var(--rule);background:var(--surface)}
button,button:not(.quiet){border-radius:2px;background:var(--ink);border-color:var(--ink);color:var(--surface);font-weight:600;padding:6px 14px}
button.quiet{background:transparent;color:var(--ink);border-color:var(--axis)}
button.quiet:hover{border-color:var(--ink);background:transparent}
input,select,textarea{border-radius:2px}
input:focus,select:focus{box-shadow:none;outline:2px solid var(--accent);outline-offset:0;border-color:var(--accent)}
.seg{border-radius:2px;border-color:var(--ink)}.seg a.on{background:var(--ink);color:var(--surface)}
.nb{border-radius:2px}
.chip,.todo .chip{border-radius:2px;border:1px solid var(--axis);background:var(--surface);color:var(--ink)}
.todo .chip b{background:transparent;color:var(--ink);padding:0;font-family:var(--num);font-size:14px;margin-right:6px;border-radius:0}
.todo .chip:hover{border-color:var(--ink);background:var(--surface)}
.chip.good{background:transparent;border-color:var(--good)}
.chk .mark{border-radius:2px;background:transparent;color:var(--good);border:2px solid var(--good)}.chk.todo .mark{color:var(--ink);border-color:var(--ink);background:transparent}
.btnlink{border-radius:2px;border-color:var(--ink);color:var(--ink)}.btnlink:hover{background:var(--ink);color:var(--surface)}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);border-bottom:2px solid var(--rule)}
a{color:var(--accent)}
.up{color:var(--critical)}.down{color:var(--goodtext)}
.foot{letter-spacing:.06em;text-transform:uppercase;font-size:11px}
@media print{.side,.tabs,.todo,button,.modes,#tip{display:none}body{background:#fff}}

/* shell (design A): a sidebar on a computer, a tab bar on a phone */
.shell{display:flex;min-height:100vh;align-items:stretch}
.content{flex:1 1 0;min-width:0}
.side{flex:0 0 232px;width:232px;box-sizing:border-box;background:var(--surface);border-right:1px solid var(--border);padding:20px 12px;display:flex;flex-direction:column;gap:2px;position:sticky;top:0;height:100vh;overflow:auto}
.side .brand{display:flex;align-items:center;gap:10px;padding:4px 12px 18px}
.side .brand b{font-weight:600;font-size:15px}
.side .mark{width:28px;height:28px;background:var(--ink);color:var(--surface);display:flex;align-items:center;justify-content:center;font-family:var(--num);font-size:13px;font-weight:600}
.side a{display:flex;align-items:center;gap:12px;min-height:44px;padding:0 12px;color:var(--ink2);text-decoration:none;font-weight:500}
.side a:hover{background:color-mix(in srgb,var(--ink) 5%,transparent);color:var(--ink)}
.side a.on{background:color-mix(in srgb,var(--accent) 13%,transparent);color:var(--accent);font-weight:600}
.side a .lbl{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.side .badge{white-space:nowrap;flex:0 0 auto}
.side .badge{font-family:var(--num);font-size:12px;padding:1px 6px;background:var(--ink);color:var(--surface);border-radius:2px}
.side .badge.soft{background:transparent;color:#8a5a00;padding:0;font-size:11px}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .side .badge.soft{color:#e0b060}}
:root[data-theme="dark"] .side .badge.soft{color:#e0b060}
.side .sg{display:flex;flex-direction:column;gap:1px;margin-bottom:10px}.side .gt{padding:6px 12px 4px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.side a{min-height:40px!important;font-size:14px}
.sidefoot{margin-top:auto;padding:16px 12px 0;border-top:1px solid var(--grid);display:flex;flex-direction:column;gap:10px}
.theme{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid var(--border)}
.theme button{height:32px;border:0;border-left:1px solid var(--border);border-radius:0;padding:0;font-size:13px;font-weight:400;background:var(--surface);color:var(--ink2)}
.theme button:first-child{border-left:0}
.theme button[aria-pressed=true]{background:var(--ink);color:var(--surface)}
.sidefoot .bk{font-size:12px;color:var(--muted);line-height:1.35}
.tabs{display:none}
.moregrid{display:grid;gap:0;background:var(--surface);border-top:2px solid var(--rule)}
.moregrid a{display:flex;align-items:center;gap:14px;min-height:56px;padding:0 16px;border-bottom:1px solid var(--grid);color:var(--ink);text-decoration:none}
/* page head and the Review screen */
.pagehead{display:flex;flex-wrap:wrap;align-items:center;gap:10px 24px;margin:6px 0 16px}
.pagehead h1{margin:0;font-size:22px;font-weight:600;letter-spacing:0}
.pagehead .sub{flex:1 1 auto;color:var(--muted)}
.mono{font-family:var(--num);font-variant-numeric:tabular-nums}
.rv{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}
.rvlist{flex:1 1 440px;min-width:0;background:var(--surface)}
.rvh{display:flex;justify-content:space-between;padding:12px 20px 8px;border-bottom:1px solid var(--rule);font-size:13px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--ink2)}
.rvrow{display:grid;grid-template-columns:48px minmax(0,1fr) auto 100px;gap:12px;align-items:center;min-height:44px;padding:0 20px;border-bottom:1px solid var(--grid);color:var(--ink);text-decoration:none}
.rvrow:hover{background:color-mix(in srgb,var(--ink) 4%,transparent)}
.rvrow.on{background:color-mix(in srgb,var(--accent) 10%,transparent);box-shadow:inset 0 0 0 1px var(--accent)}
.rvrow .d{font-size:13px;color:var(--muted)}.rvrow .m{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rvrow .gc{font-size:13px;border:1px dashed var(--axis);padding:1px 8px;white-space:nowrap}.rvrow .gc.none{border:0;color:#8a5a00}
.rvrow .n{text-align:right}
.rvmore{padding:10px 20px;font-size:13px;border-bottom:1px solid var(--grid)}
.rvdet{flex:1 1 480px;min-width:0;background:var(--surface);border-top:2px solid var(--rule);padding:20px 24px 24px;position:sticky;top:12px}
.rvdet .rvtop{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:14px}
.rvdet h2{margin:8px 0 0;font-size:24px;font-weight:600}
.rvdet .amt{font-size:34px;font-weight:500;letter-spacing:-.02em}
.rvdet .why{margin:14px 0 4px;color:var(--ink2);font-size:14px}
.cgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:10px}
.cbtn{display:flex;align-items:center;gap:10px;height:44px;padding:0 12px;text-align:left;font-weight:400;font-size:15px;background:var(--surface)!important;color:var(--ink)!important;border:1px solid var(--border)!important}
.cbtn.guess{border:1px dashed var(--axis)!important}
.cbtn:hover{border-color:var(--ink)!important}
.cbtn .k{font-family:var(--num);font-size:12px;color:var(--muted)}
.cother select{width:100%;height:44px}
.learn{display:flex;align-items:center;gap:10px;margin-top:14px;font-size:14px}
.rvact{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.rvact .primary{height:44px;padding:0 20px;flex:1 1 200px}
.rvact .btnlink{height:44px;display:inline-flex;align-items:center;padding:0 16px}
.rvlines{margin-top:16px}
.keys kbd{font-family:var(--num);border:1px solid var(--axis);padding:0 5px}
@media (max-width:820px){.rvdet{position:static}.cgrid{grid-template-columns:repeat(2,minmax(0,1fr))}.rvrow{grid-template-columns:44px minmax(0,1fr) 88px}.rvrow .gc{display:none}}
/* overview: a main column and a column of things to look at */
.ov{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}
.ovmain{flex:999 1 560px;min-width:0}
.ovside{flex:1 1 300px;min-width:0;display:flex;flex-direction:column;gap:16px}
.ovside .card,.ovside .banner{margin:0!important}
.sidecard h4{margin:0 0 8px;font-size:15px;font-weight:600}
.strow{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--grid);font-size:14px}
.strow .ok{color:var(--goodtext)}.strow .warn{color:#8a5a00;font-weight:600}
.nwcard{display:block;color:var(--ink);text-decoration:none}.nwcard .big{font-size:26px;font-weight:500;letter-spacing:-.02em}
.nwcard:hover{border-color:var(--ink)}
.band{display:flex;flex-wrap:wrap;align-items:center;gap:8px 24px;padding:12px 18px;background:var(--surface);border:1px solid var(--border);border-top:0;font-size:13px;color:var(--ink2)}
.band .bar{flex:1 1 240px;height:6px;background:var(--grid)}.band .bar span{display:block;height:6px;background:var(--ink)}
/* transactions */
.tfilter{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}.tfilter input[type=search]{flex:1 1 260px;min-width:0;height:40px}.tfilter select,.tfilter button{height:40px}
.tsum{display:flex;flex-wrap:wrap;gap:8px 32px;padding:12px 20px;background:var(--surface);border-top:2px solid var(--rule);font-size:14px;color:var(--ink2)}
.tsum .good,.tbl .good{color:var(--goodtext)}.tkey{display:flex;gap:16px;margin-left:auto;font-size:13px}.tkey span{display:flex;align-items:center;gap:6px}
.btnlink.strong{background:var(--ink);color:var(--surface);border-color:var(--ink);height:40px;display:inline-flex;align-items:center;padding:0 16px;font-weight:600}
.mk{display:inline-block;width:8px;height:8px;box-sizing:border-box}.mk.ok{background:var(--ink)}.mk.guess{border:1px dashed var(--muted)}
.tbl{background:var(--surface);margin-top:12px;overflow-x:auto}
.thead,.trow>summary{display:grid;grid-template-columns:16px minmax(0,1.6fr) minmax(0,1fr) 150px 130px;gap:16px;align-items:center;padding:0 20px;min-width:760px}
.thead{padding:10px 20px;border-bottom:1px solid var(--rule);font-size:12px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.tday{display:flex;justify-content:space-between;padding:14px 20px 6px;font-size:13px;font-weight:600;color:var(--ink2);border-bottom:1px solid var(--grid);min-width:760px;box-sizing:border-box}
.trow>summary{min-height:48px;border-bottom:1px solid var(--grid);cursor:pointer;list-style:none}.trow>summary::-webkit-details-marker{display:none}
.trow>summary:hover{background:color-mix(in srgb,var(--ink) 4%,transparent)}
.trow .mer{min-width:0}.trow .mer .cap{display:block}.trow .acc{font-size:14px;color:var(--ink2)}.trow .r,.thead .r{text-align:right}
.trow .gchip{border:1px dashed var(--muted);padding:1px 8px;justify-self:start}.trow .nocat{color:#8a5a00}.trow.lo .mer,.trow.lo .mono{opacity:.55}
.tchg{padding:10px 20px 14px 52px;background:color-mix(in srgb,var(--ink) 3%,transparent);border-bottom:1px solid var(--grid)}
.tmore{padding:14px 20px}
@media (max-width:820px){
 .thead{display:none}.tday{min-width:0}.trow>summary{grid-template-columns:12px minmax(0,1fr) auto;min-width:0;padding:8px 16px;gap:2px 12px}
 .trow>summary .mk{grid-row:1/3;align-self:start;margin-top:8px}.trow>summary .mer{grid-column:2}.trow>summary .r{grid-column:3;grid-row:1}
 .trow>summary .gchip,.trow>summary .nocat,.trow>summary>span:nth-of-type(2){grid-column:2;grid-row:2;font-size:13px}.trow .acc{display:none}.tchg{padding-left:16px}
 .tfilter select{flex:1 1 40%}.tbl{overflow-x:visible}
 .side{display:none}
 .tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));position:fixed;left:0;right:0;bottom:0;z-index:20;background:var(--surface);border-top:1px solid var(--border);padding:6px 4px calc(10px + env(safe-area-inset-bottom))}
 .tabs a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:52px;font-size:11px;color:var(--ink2);text-decoration:none}
 .tabs a.on{color:var(--accent);font-weight:600}
 .tabs .ti{position:relative;display:inline-flex}
 .tabs .tb{position:absolute;top:-4px;left:16px;min-width:18px;height:18px;padding:0 4px;box-sizing:border-box;background:var(--ink);color:var(--surface);font-family:var(--num);font-size:11px;line-height:18px;text-align:center}
 .content{padding-bottom:84px}
}
"""

HEARTBEAT = """
(function(){function p(){fetch('/ping',{cache:'no-store'}).catch(function(){})}p();setInterval(p,15000)})();
"""

JS = """
(function(){var t=document.getElementById('tip');
document.addEventListener('mouseover',function(e){var el=e.target.closest('[data-tip]');if(!el){t.style.display='none';return}
t.textContent=el.getAttribute('data-tip');t.style.display='block'});
document.addEventListener('mousemove',function(e){t.style.left=Math.min(e.clientX+14,innerWidth-t.offsetWidth-8)+'px';t.style.top=(e.clientY+16)+'px'});
document.addEventListener('mouseout',function(e){if(!e.relatedTarget||!e.relatedTarget.closest||!e.relatedTarget.closest('[data-tip]'))t.style.display='none'});})();
"""

THEME_JS = """(function(){try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}
document.addEventListener('DOMContentLoaded',function(){var cur='auto';try{cur=localStorage.getItem('theme')||'auto'}catch(e){}
var bs=document.querySelectorAll('[data-theme-set]');bs.forEach(function(b){b.setAttribute('aria-pressed',b.getAttribute('data-theme-set')===cur?'true':'false');
b.addEventListener('click',function(){var v=b.getAttribute('data-theme-set');try{if(v==='auto')localStorage.removeItem('theme');else localStorage.setItem('theme',v)}catch(e){}
if(v==='auto')document.documentElement.removeAttribute('data-theme');else document.documentElement.setAttribute('data-theme',v);
bs.forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')})})})})})();"""

NAV = [("/", "Overview"), ("/upload", "Upload"), ("/review", "Review"), ("/recurring", "Recurring"), ("/wealth", "Wealth"), ("/trips", "Trips"), ("/setup", "Settings")]
# The sidebar: the screens you use most, then the rest. Each has a small stroke icon (24px grid).
ICONS = {
    "/": "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
    "/review": "M4 12l5 5L20 6",
    "/transactions": "M9 6h11M9 12h11M9 18h11M4 6h1M4 12h1M4 18h1",
    "/alerts": "M6 17v-6a6 6 0 0 1 12 0v6l2 2H4zM10 21h4",
    "/upload": "M12 15V4M7 9l5-5 5 5M5 20h14",
    "/files": "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7",
    "/balances": "M4 20h16M6 16V9M10 16V5M14 16v-6M18 16V8",
    "/cash": "M3 7h18v10H3zM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
    "/close": "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
    "/budgets": "M12 3v9h9M12 3a9 9 0 1 0 9 9",
    "/recurring": "M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4",
    "/goals": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
    "/trips": "M4 8h16v12H4zM9 8V5h6v3M4 13h16",
    "/simulator": "M4 20l6-7 4 3 6-9M15 7h5v5",
    "/wealth": "M4 19h16M5 15l4-4 4 3 6-7",
    "/setup": "M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4",
    "/more": "M5 12h1M12 12h1M19 12h1",
}
# the sidebar, in groups (a title of None is a group without a heading)
SIDE_GROUPS = [
    (None, [("/", "Overview"), ("/review", "Review"), ("/transactions", "Transactions"), ("/alerts", "Alerts")]),
    ("Bring in", [("/upload", "Add statements"), ("/files", "Statements"), ("/balances", "Balances"), ("/cash", "Cash payments"), ("/close", "Close the month")]),
    ("Plan", [("/budgets", "Budgets"), ("/recurring", "Recurring"), ("/goals", "Goals"), ("/trips", "Trips"), ("/simulator", "Simulator"), ("/wealth", "Wealth")]),
    (None, [("/setup", "Settings")]),
]
SIDE_MAIN = SIDE_GROUPS[0][1]
SIDE_MORE = [n for _, items in SIDE_GROUPS[1:] for n in items]
PHONE_TABS = [("/", "Overview"), ("/review", "Review"), ("/transactions", "Activity"), ("/wealth", "Wealth"), ("/more", "More")]


def _form(ctx: Ctx, action: str, inner: str, cls: str = "") -> str:
    return (f'<form method="post" action="{esc(action)}" class="{cls}"><input type="hidden" name="csrf" value="{esc(ctx.csrf)}">'
            f"{inner}</form>")


def counts(conn: sqlite3.Connection) -> dict:
    return {
        "review": conn.execute("""SELECT COUNT(DISTINCT t.description_norm) FROM transactions t JOIN batches b ON b.id = t.batch_id
                                  WHERE t.category_status = 'proposed' AND b.status != 'committed'""").fetchone()[0],
        "items": conn.execute("SELECT COUNT(*) FROM discrepancies WHERE status = 'open' AND type != 'manual'").fetchone()[0],
    }


def _icon(path: str, size: int = 20) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            f'stroke-linecap="square" aria-hidden="true"><path d="{ICONS.get(path, ICONS["/more"])}"></path></svg>')


def _missing_statements(conn, ctx: Ctx) -> int:
    try:
        cl = expected.checklist(conn, ctx.today)
    except Exception:                      # a badge must never stop a page from opening
        return 0
    return sum(1 for g in cl["groups"] for i in g["items"] if i["status"] == "todo" and not i.get("children"))


def _badges(conn, ctx: Ctx, c: dict) -> dict:
    out = {"/review": (str(c["review"]) if c["review"] else "")}
    if ctx.readonly:
        return out
    miss = _missing_statements(conn, ctx)
    out["/files"] = f"{miss} missing" if miss else ""
    try:
        from . import alerts, closing
        n = len(alerts.build(conn, ctx.today))
        out["/alerts"] = str(n) if n else ""
        nxt = closing.to_close(conn, ctx.today)
        if nxt:
            out["/close"] = MONTH_ABBR[int(nxt[5:7]) - 1]
    except Exception:
        pass
    return out


# pages that belong under another sidebar item
PAGE_HOME = {"/spending": "/", "/trends": "/", "/dashboard": "/", "/imports": "/files", "/history": "/files", "/leftout": "/transactions",
             "/screens": "/balances", "/items": "/alerts", "/networth": "/wealth"}
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PHONE_ALLOWED = {"/", "/wealth"}


def layout(conn, ctx: Ctx, path: str, title: str, body: str) -> str:
    c = counts(conn)
    badges = _badges(conn, ctx, c)
    here = PAGE_HOME.get(path, path)

    def side_link(href: str, name: str) -> str:
        b = badges.get(href, "")
        on = here == href
        bcls = "badge" if b.isdigit() else "badge soft"
        return (f'<a href="{href}" class="{"on" if on else ""}"{" aria-current=page" if on else ""}>{_icon(href)}<span class="lbl">{name}</span>'
                + (f'<span class="{bcls}">{b}</span>' if b else "") + "</a>")

    groups = []
    for title_, items in SIDE_GROUPS:
        links = [n for n in items if not ctx.readonly or n[0] in PHONE_ALLOWED]
        if links:
            groups.append(f'<div class="sg">{"<div class=gt>" + title_ + "</div>" if title_ else ""}{"".join(side_link(*n) for n in links)}</div>')
    flash = f'<div class="flash {"err" if ctx.flash_kind == "err" else ""}" role="status">{esc(ctx.flash)}</div>' if ctx.flash else ""
    quit_form = "" if ctx.readonly else _form(ctx, "/quit", "<button class='quiet quit' title='Close the app'>Quit</button>", "inline")
    if ctx.server:
        quit_form = (f'<div class="bk">Signed in as {esc(ctx.user)}</div>'
                     + _form(ctx, "/logout", "<button class='quiet quit'>Sign out</button>", "inline"))
    theme = ('<div class="theme" role="group" aria-label="Theme">'
             '<button type="button" class="quiet" data-theme-set="light" aria-pressed="false">Light</button>'
             '<button type="button" class="quiet" data-theme-set="dark" aria-pressed="false">Dark</button>'
             '<button type="button" class="quiet" data-theme-set="auto" aria-pressed="true">Auto</button></div>')
    backup = ""
    if not ctx.readonly:
        from . import cloudbackup
        try:
            last = cloudbackup.last_result(conn)
        except Exception:
            last = None
        if last:
            backup = f'<div class="bk">{esc(last)}</div>'
    where = ""
    if not ctx.readonly:
        from . import appmode
        where = f" · running from {esc(str(appmode.app_dir()))}"          # shows which copy of the app is open, when you have more than one
    side = ('<aside class="side" aria-label="Main"><div class="brand"><span class="mark" aria-hidden="true">₪</span><b>Household ledger</b></div>'
            + "".join(groups) + f'<div class="sidefoot">{theme}{backup}{quit_form}</div></aside>')
    tabs_src = [t for t in PHONE_TABS if not ctx.readonly or t[0] in PHONE_ALLOWED]
    tabs = "".join(
        f'<a href="{h}" class="{"on" if (here == h or (h == "/more" and here in dict(SIDE_MORE))) else ""}">'
        f'<span class="ti">{_icon(h, 24)}' + (f'<span class="tb">{badges[h]}</span>' if badges.get(h) and badges[h].isdigit() else "")
        + f'</span><span>{n}</span></a>' for h, n in tabs_src)
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)} · Finance</title><style>{CSS}</style><script>{THEME_JS}</script></head><body>"
            f"<div class=\"shell\">{side}<div class=\"content\"><main>{flash}{body}</main><p class=\"foot\">Version {esc(BUILD)}{where}</p></div></div>"
            f"<div class=\"tabs\" role=\"navigation\" aria-label=\"Main\">{tabs}</div><div id=\"tip\"></div><script>{JS}{HEARTBEAT}</script></body></html>")


def dmy(iso: str | None) -> str:
    """2026-10-02 -> 02/10/2026 (dates as you write them)."""
    if not iso or len(iso) < 10 or iso[4] != "-":
        return iso or ""
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def _tile(label: str, value: str, detail: str = "") -> str:
    return f'<div class="tile"><div class="l">{esc(label)}</div><div class="v">{value}</div><div class="d">{detail}</div></div>'


def _net_worth_series(conn, currency: str):
    """Every snapshot valued at today's dollar rate. Returns (points, no_rate)."""
    pts = []
    for d in [r[0] for r in conn.execute("SELECT DISTINCT as_of FROM balances ORDER BY as_of")]:
        try:
            pts.append((d, balances.net_worth(conn, d, currency)["total"]))
        except LookupError:
            return [], True
    return pts, False


def fx_note(conn, today: str) -> str:
    cur = fx.current_rate(conn)
    if not cur:
        return "no dollar rate yet"
    age = (date.fromisoformat(today) - date.fromisoformat(cur["date"])).days
    stale = f" ▲ {age} days old" if age > 3 else ""
    typed = " · typed by you, press “Fetch the current rate automatically” to replace it" if cur["source"] == "manual" else ""
    return f"₪{cur['rate']:.3f} per $ · {cur['source']} · {cur['date']}{stale}{typed}"


def dashboard(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    cur = q.get("cur", "ILS") if q.get("cur") in ("ILS", "USD") else "ILS"
    owner = q.get("owner") or None
    months_all = summary.months_available(conn)
    overview = [summary.month_overview(conn, m) for m in months_all[-18:]]
    with_data = [o for o in overview if o["spending"] or o["income"]]
    window = _month_window([o["month"] for o in with_data], 18)
    by_month = {o["month"]: o for o in with_data}
    chosen = q.get("month") or (with_data[-1]["month"] if with_data else None)
    from .views_setup import dropzone
    out = ['<h1>Dashboard</h1>', dropzone(ctx, compact=True)]

    from . import firstrun
    loaded = firstrun.pending_notice(conn)
    if loaded:
        out.append('<div class="banner good"><b>Your starting data is loaded.</b><ul>' + "".join(f"<li>{esc(x)}</li>" for x in loaded)
                   + "</ul>Everything is a draft: check the <a href='/review'>Review</a> tab, then submit on the <a href='/imports'>Imports</a> tab. "
                   + _form(ctx, "/seed/dismiss", "<button class=quiet>Got it</button>", "inline") + "</div>")
    drafts = conn.execute("SELECT COUNT(*) FROM batches WHERE status != 'committed'").fetchone()[0]
    if drafts:
        out.append(f'<div class="banner">{drafts} import(s) are drafts. Numbers below include them until you submit. <a href="/imports">Review and submit</a></div>')
    st = sheetsync.status(conn)
    if not st["linked"]:
        out.append('<div class="banner">The Google Sheet is not linked yet. Set it up once and it updates itself after every submit. '
                   '<a href="/sheet">Link it</a></div>')
    elif st["last_result"] and st["last_result"].startswith("failed"):
        out.append(f'<div class="banner">The Google Sheet did not update: {esc(st["last_result"][8:])} <a href="/sheet">Details</a></div>')
    items = expected.expected_files(conn, ctx.today)
    late = [i for i in items if i["status"] in ("missing", "due")]
    if late:
        rows = "".join(f'<li>{esc(i["source"])} {esc(i["period"])}: {esc(i["detail"])} ({esc(i["status"])})</li>' for i in late[:6])
        more = f" and {len(late) - 6} more" if len(late) > 6 else ""
        out.append(f'<div class="banner"><b>Files you should have uploaded</b><ul>{rows}</ul>{more}</div>')

    pts, no_rate = _net_worth_series(conn, cur)
    nw_now = pts[-1][1] if pts else None
    nw_prev = pts[-2][1] if len(pts) > 1 else None
    change = f"{money(nw_now - nw_prev, cur)} since {pts[-2][0]}" if nw_prev is not None else ""
    if no_rate:
        out.append('<div class="banner"><b>No dollar rate yet.</b> Your dollar accounts need today\'s rate to be valued. '
                   '<a href="/balances">Enter it or fetch it on the Balances page</a>.</div>')
    ov = next((o for o in overview if o["month"] == chosen), None)
    gp = goals.all_progress(conn, ctx.today)
    on = sum(1 for g in gp if g["status"] in ("on_track", "reached"))
    out.append('<div class="tiles">'
               + _tile("Net worth", money(nw_now, cur), esc(change) or "from your balances")
               + _tile(f"Income {chosen or ''}", money(ov["income"] if ov else None), "")
               + _tile(f"Spending {chosen or ''}", money(ov["spending"] if ov else None), "")
               + _tile("Left over", money(ov["saved"] if ov else None), "")
               + _tile("Goals on track", f"{on} of {len(gp)}" if gp else "–", '<a href="/goals">Goals</a>') + "</div>")

    sw = "".join(f'<a href="/?cur={c}{"&month=" + chosen if chosen else ""}">{c}</a> ' for c in ("ILS", "USD"))
    nw_table = charts.data_table(["Date", f"Net worth {cur}"], [[d, f"{v:,.0f}"] for d, v in pts], "Net worth by snapshot date")
    rate_note = f'<p class="muted">Dollar accounts valued at {esc(fx_note(conn, ctx.today))} on every date.</p>' if pts else ""
    out.append(f'<div class="grid"><section class="card"><h2>Net worth over time <small>{sw}</small></h2>'
               f'{charts.line_chart(pts, cur, "Net worth over time")}{rate_note}{nw_table}</section>')

    mo = window
    inc = [by_month[m]["income"] if m in by_month else None for m in window]
    spent = [by_month[m]["spending"] if m in by_month else None for m in window]
    mtable = charts.data_table(["Month", "Income", "Spending", "Left over"],
                               [[o["month"], f'{o["income"]:,.0f}', f'{o["spending"]:,.0f}', f'{o["saved"]:,.0f}'] for o in with_data],
                               "Income and spending by month")
    out.append(f'<section class="card"><h2>Income and spending</h2>{charts.grouped_bars(mo, inc, spent)}{mtable}</section></div>')

    owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
    month_opts = "".join(f'<option value="{o["month"]}"{" selected" if o["month"] == chosen else ""}>{o["month"]}</option>'
                         for o in reversed(with_data))
    owner_opts = '<option value="">Household</option>' + "".join(
        f'<option{" selected" if o == owner else ""}>{esc(o)}</option>' for o in owners)
    cats = summary.spending_by_category(conn, chosen, owner) if chosen else []
    grouped: dict[str, float] = {}
    for r in cats:
        grouped[r["category"]] = grouped.get(r["category"], 0) + r["spent"]
    rows = sorted(grouped.items(), key=lambda kv: -kv[1])
    detail = {k: ", ".join(f'{r["subcategory"]} {r["spent"]:,.0f}' for r in cats if r["category"] == k) for k in grouped}
    bars = charts.hbars([(k, v, f"{k}: {detail[k]}") for k, v in rows[:14]])
    reimb = summary.reimbursed_total(conn, chosen, owner) if chosen else 0
    inc_r = summary.include_reimbursed(conn)
    reimb_html = ""
    if reimb or inc_r:
        reimb_html = _form(ctx, "/setting/reimbursed",
                           f'<span>Vituri (paid back in cash): {esc(money(reimb))} this month, '
                           f'{"counted in" if inc_r else "not counted in"} spending.</span>'
                           f'<input type="hidden" name="on" value="{0 if inc_r else 1}">'
                           f'<button>{"Leave out" if inc_r else "Include"}</button>', "row")
    ctable = charts.data_table(["Category", "Spent"], [[k, f"{v:,.2f}"] for k, v in rows], f"Spending by category, {chosen}")
    out.append(f'<section class="card" style="margin-top:16px"><h2>Where the money went</h2>'
               f'<form method="get" class="row"><select name="month" onchange="this.form.submit()">{month_opts}</select>'
               f'<select name="owner" onchange="this.form.submit()">{owner_opts}</select><input type="hidden" name="cur" value="{cur}"></form>'
               f"{reimb_html}{bars}{ctable}</section>")

    if gp:
        cards = "".join(_goal_card(g) for g in gp[:4])
        out.append(f'<section class="card" style="margin-top:16px"><h2>Goals</h2>{cards}</section>')
    return layout(conn, ctx, "/", "Dashboard", "".join(out))


def _month_window(months: list[str], size: int) -> list[str]:
    """Every calendar month from the first to the last with data (at most `size`), so gaps show as gaps."""
    if not months:
        return []
    y, m = map(int, months[-1].split("-"))
    out = []
    for _ in range(size):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out.reverse()
    first = months[0]
    return [x for x in out if x >= first]


def _goal_card(p: dict) -> str:
    cur = p["currency"]
    pct = f"{p['percent'] * 100:.0f}%" if p["percent"] is not None else "–"
    lines = [f'{esc(money(p["current"], cur))} of {esc(money(p["target"], cur))} ({pct})']
    if p["target_date"]:
        need = money(p["required_per_month"], cur)
        short = f", short by {money(p['gap_per_month'], cur)} a month" if p["gap_per_month"] else ""
        lines.append(f"By {esc(p['target_date'])}: needs {esc(need)} a month{esc(short)}")
    if p["growth_per_month"] is not None:
        lines.append(f"Growing about {esc(money(p['growth_per_month'], cur))} a month lately")
    if p["projected_date"]:
        lines.append(f"At this pace: about {esc(p['projected_date'][:7])}")
    if p.get("note"):
        lines.append(esc(p["note"]))
    return (f'<div class="item"><div class="row"><b>{esc(p["name"])}</b>{charts.status_chip(p["status"])}</div>'
            f'{charts.progress_bar(p["percent"], p["status"])}<small>{"<br>".join(lines)}</small></div>')


STRONG_BASIS = ("user rule", "learned rule", "builtin rule", "similar to", "approved by itself")


def is_strong(basis: str | None) -> bool:
    """A suggestion backed by a rule or by earlier payments, as against a loose guess."""
    b = (basis or "").lower()
    return any(k in b for k in (" rule:", "similar to", "approved by itself"))


def review(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    groups = conn.execute(
        """SELECT t.description_norm AS key, MIN(t.description) AS description, COUNT(*) AS n,
                  ROUND(SUM(-t.amount), 2) AS total, c.name AS proposed, MIN(t.proposal_basis) AS basis,
                  MIN(t.txn_date) AS first, MAX(t.txn_date) AS last
           FROM transactions t JOIN categories c ON c.id = t.category_id JOIN batches b ON b.id = t.batch_id
           WHERE t.category_status = 'proposed' AND b.status != 'committed'
           GROUP BY t.description_norm, c.name ORDER BY ABS(SUM(t.amount)) DESC""").fetchall()
    strong = [g for g in groups if g["proposed"] != "Uncategorised" and is_strong(g["basis"])]
    weak = [g for g in groups if g["proposed"] != "Uncategorised" and not is_strong(g["basis"])]
    none = [g for g in groups if g["proposed"] == "Uncategorised"]
    ordered = strong + weak + none
    lines_total = sum(g["n"] for g in groups)
    out = []
    quick = ""
    if strong and not ctx.readonly:
        quick = _form(ctx, "/approve/strong", f'<button class="quiet">Confirm {len(strong)} strong guess{"es" if len(strong) != 1 else ""}</button>', "inline")
    out.append(f'<div class="pagehead"><h1>Review</h1><div class="sub"><span class="mono">{len(groups)}</span> merchant{"s" if len(groups) != 1 else ""} to go '
               f'· <span class="mono">{lines_total}</span> line{"s" if lines_total != 1 else ""}</div>{quick}</div>')
    auto = conn.execute(
        """SELECT t.description_norm AS k, MIN(t.description) AS d, c.name AS cat, COUNT(*) AS n FROM transactions t
           JOIN categories c ON c.id = t.category_id JOIN batches b ON b.id = t.batch_id
           WHERE t.category_status = 'approved' AND t.proposal_basis LIKE 'approved by itself%' AND b.status != 'committed'
           GROUP BY t.description_norm, c.name ORDER BY n DESC""").fetchall()
    if auto:
        out.append('<details class="banner good"><summary>'
                   f'{sum(a["n"] for a in auto)} payment(s) were approved by themselves</summary>'
                   '<p class="cap">You had put these merchants in the same category at least 3 times. If one is wrong, '
                   'open its category on Overview and change it.</p><ul>'
                   + "".join(f'<li dir="auto"><bdi dir="auto">{esc(a["d"])}</bdi> → {esc(a["cat"])} <span class="cap">({a["n"]})</span></li>' for a in auto[:60])
                   + "</ul></details>")
    if not ordered:
        out.append('<div class="card">Nothing is waiting for review. ✔</div>')
        return layout(conn, ctx, "/review", "Review", "".join(out))

    keys = [g["key"] + "\x1f" + g["proposed"] for g in ordered]
    want = q.get("k") or ""
    idx = keys.index(want) if want in keys else 0
    cur = ordered[idx]
    nxt = keys[(idx + 1) % len(keys)]

    def row(g, k):
        on = k == keys[idx]
        label = ("No guess" if g["proposed"] == "Uncategorised" else g["proposed"])
        cls = "rvrow on" if on else "rvrow"
        return (f'<a class="{cls}" href="/review?k={esc(quote(k))}"{" aria-current=true" if on else ""}>'
                f'<span class="mono d">{esc(dmy(g["last"])[:5])}</span><span class="m"><bdi dir="auto">{esc(g["description"])}</bdi>'
                + (f'<span class="cap"> ×{g["n"]}</span>' if g["n"] > 1 else "") + "</span>"
                f'<span class="gc{" none" if g["proposed"] == "Uncategorised" else ""}">{esc(label)}</span>'
                f'<span class="mono n">{money(abs(g["total"]), "ILS", 2)}</span></a>')

    show_all = q.get("all") == "1"
    LIMIT = 40
    sections = []
    offset = 0
    for title, grp in (("Strong guesses", strong), ("Weak guesses", weak), ("No guess", none)):
        if not grp:
            offset += 0
            continue
        shown = grp if show_all else grp[:LIMIT]
        # always keep the selected row visible
        if not show_all and cur in grp and cur not in shown:
            shown = shown + [cur]
        rows_html = "".join(row(g, g["key"] + "\x1f" + g["proposed"]) for g in shown)
        more = (f'<div class="rvmore"><a href="/review?all=1&k={esc(quote(keys[idx]))}">{len(grp) - len(shown)} more</a></div>' if len(shown) < len(grp) else "")
        sections.append(f'<div class="rvh"><span>{title}</span><span class="mono">{len(grp)}</span></div>{rows_html}{more}')
    left = f'<section class="rvlist" aria-label="Merchants to review">{"".join(sections)}</section>'

    # the detail pane for the selected merchant
    lines = conn.execute(
        """SELECT t.txn_date, t.description, -t.amount AS spent, a.label AS account FROM transactions t
           JOIN categories c ON c.id = t.category_id JOIN batches b ON b.id = t.batch_id JOIN accounts a ON a.id = t.account_id
           WHERE t.category_status = 'proposed' AND b.status != 'committed' AND t.description_norm = ? AND c.name = ?
           ORDER BY t.txn_date DESC, t.id DESC""", (cur["key"], cur["proposed"])).fetchall()
    used = [r[0] for r in conn.execute(
        """SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'approved' AND c.name != 'Uncategorised' AND c.kind = 'expense'
           GROUP BY c.name ORDER BY COUNT(*) DESC LIMIT 12""")] if True else []
    all_cats = [r[0] for r in conn.execute("SELECT name FROM categories WHERE name != 'Uncategorised' ORDER BY kind, name")]
    guess = cur["proposed"] if cur["proposed"] != "Uncategorised" else None
    choices = ([guess] if guess else []) + [c for c in used if c != guess]
    choices = choices[:8]
    rest = [c for c in all_cats if c not in choices]
    btns = []
    for n, c in enumerate(choices, 1):
        btns.append(f'<button name="category" value="{esc(c)}" class="cbtn{" guess" if c == guess else ""}" data-n="{n}"><span class="k">{n}</span><span dir="auto">{esc(c)}</span></button>')
    other = ('<select name="other" onchange="if(this.value){var f=this.form;var h=document.createElement(\'input\');h.type=\'hidden\';h.name=\'category\';h.value=this.value;f.appendChild(h);f.submit()}">'
             '<option value="">Other…</option>' + "".join(f"<option>{esc(c)}</option>" for c in rest) + "</select>")
    form = _form(ctx, "/approve",
                 f'<input type="hidden" name="key" value="{esc(cur["key"])}">'
                 f'<div class="cgrid">{"".join(btns)}<div class="cother">{other}</div></div>'
                 '<label class="learn"><input type="checkbox" name="learn" value="1" checked> Use this category for this merchant from now on</label>'
                 + (f'<div class="rvact"><button name="category" value="{esc(guess)}" class="primary" data-n="enter">Confirm {esc(guess)} <span class="mono">↵</span></button>'
                    if guess else '<div class="rvact">')
                 + f'<a class="btnlink" href="/review?k={esc(quote(nxt))}">Later <span class="mono">L</span></a></div>')
    line_rows = "".join(f'<tr><td class="mono">{esc(dmy(l["txn_date"]))}</td><td dir="auto">{esc(l["description"])}</td>'
                        f'<td dir="auto" class="muted">{esc(l["account"])}</td><td class="n">{l["spent"]:,.2f}</td></tr>' for l in lines[:30])
    more_lines = f'<p class="cap">Showing the newest 30 of {len(lines)}.</p>' if len(lines) > 30 else ""
    why = esc(cur["basis"] or "No similar payment found yet.")
    flow = f'{money(abs(cur["total"]), "ILS", 2)} {"in" if cur["total"] < 0 else "out"}'
    sample = lines[0]["account"] if lines else ""
    detail = (f'<section class="rvdet" aria-label="Selected merchant">'
              f'<div class="rvtop"><span>{esc(dmy(cur["first"]))}{" to " + esc(dmy(cur["last"])) if cur["first"] != cur["last"] else ""}</span><span>{esc(sample)}</span></div>'
              f'<h2 dir="auto"><bdi dir="auto">{esc(cur["description"])}</bdi></h2>'
              f'<div class="amt mono">{esc(flow)}</div><div class="cap">{cur["n"]} line{"s" if cur["n"] != 1 else ""}</div>'
              f'<div class="why">{("The app guessed <b>" + esc(guess) + "</b>. " if guess else "")}{why}</div>'
              + ("" if ctx.readonly else form)
              + f'<details class="rvlines"><summary>Show the {len(lines)} line{"s" if len(lines) != 1 else ""}</summary><div style="overflow-x:auto"><table><thead><tr><th>Date</th><th>Description</th><th>Account</th><th class="n">Amount ₪</th></tr></thead>'
              f'<tbody>{line_rows}</tbody></table></div>{more_lines}</details></section>')
    out.append(f'<div class="rv">{left}{detail}</div>')
    out.append('<p class="keys cap"><kbd>J</kbd> <kbd>K</kbd> move · <kbd>1</kbd>–<kbd>9</kbd> pick a category · <kbd>↵</kbd> confirm the guess · <kbd>L</kbd> later</p>')
    out.append(f"<script>{REVIEW_JS}</script>")
    return layout(conn, ctx, "/review", "Review", "".join(out))


REVIEW_JS = """(function(){var rows=[].slice.call(document.querySelectorAll('.rvrow'));var cur=rows.findIndex(function(r){return r.classList.contains('on')});
document.addEventListener('keydown',function(e){var t=e.target&&e.target.tagName;if(t==='INPUT'||t==='SELECT'||t==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey)return;
var k=e.key;if(k==='j'||k==='J'){if(rows[cur+1])location=rows[cur+1].href}else if(k==='k'||k==='K'){if(rows[cur-1])location=rows[cur-1].href}
else if(k==='l'||k==='L'){var l=document.querySelector('.rvact .btnlink');if(l)location=l.href}
else if(k==='Enter'){var p=document.querySelector('button.primary');if(p){e.preventDefault();p.click()}}
else if(/^[1-9]$/.test(k)){var b=document.querySelector('button.cbtn[data-n="'+k+'"]');if(b)b.click()}});})();"""


def items(conn: sqlite3.Connection, ctx: Ctx, q: dict) -> str:
    show = q.get("status", "open")
    statuses = {"open": ("open",), "explained": ("explained",), "all": discrepancies.STATUSES}.get(show, ("open",))
    rows = discrepancies.list_items(conn, statuses)
    tabs = " · ".join(f'<a href="/items?status={s}">{s}</a>' if s != show else f"<b>{s}</b>" for s in ("open", "explained", "all"))
    out = [f"<h1>Needs attention</h1><p>{tabs}</p>"]
    fees = [f for f in reconcile.fee_status(conn, ctx.today) if f["state"] != "refunded"]
    for f in fees:
        out.append(f'<div class="banner">Bank fee of {esc(money(f["amount"], "ILS", 2))} on {esc(dmy(f["date"]))}: '
                   f'{"overdue, no refund yet" if f["state"] == "overdue" else "refund expected by " + esc(f["due"])}</div>')
    body = []
    for d in rows:
        icon = {"error": "✖", "warning": "▲", "info": "•"}[d["severity"]]
        body.append(f'<div class="item"><span class="sev" aria-hidden="true">{icon}</span> <a href="/item/{d["id"]}">{esc(d["title"])}</a>'
                    f' <small>{esc(d["status"])} · {d["comment_count"]} comment(s)'
                    f'{" · follow up " + esc(d["follow_up_date"]) if d["follow_up_date"] else ""}</small></div>')
    out.append(f'<div class="card">{"".join(body) or "Nothing here."}</div>')
    return layout(conn, ctx, "/items", "Needs attention", "".join(out))


def item(conn: sqlite3.Connection, ctx: Ctx, item_id: int) -> str:
    d = conn.execute("SELECT * FROM discrepancies WHERE id = ?", (item_id,)).fetchone()
    if not d:
        return layout(conn, ctx, "", "Not found", "<h1>Not found</h1>")
    subject = ""
    if d["subject_type"] == "transaction":
        t = conn.execute("""SELECT t.*, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
                            WHERE t.id = ?""", (d["subject_id"],)).fetchone()
        if t:
            subject = (f'<p dir="auto"><b>{esc(t["description"])}</b> · {esc(dmy(t["txn_date"]))} · {esc(money(t["amount"], t["currency"], 2))} · '
                       f'{esc(t["cat"])} ({esc(t["category_status"])})</p>')
    thread = "".join(f'<div class="comment"><b>{esc(c["author"])}</b> <small>{esc(c["created_at"].replace("T", " "))}'
                     f'{" · edited" if c["edited_at"] else ""}</small><div dir="auto">{esc(c["body"])}</div></div>'
                     for c in discrepancies.thread(conn, item_id))
    status_opts = "".join(f'<option{" selected" if s == d["status"] else ""}>{s}</option>' for s in discrepancies.STATUSES)
    body = (f'<p><a href="/items">← Needs attention</a></p><h1>{esc(d["title"])}</h1><div class="card">{subject}'
            f'<p>{esc(d["detail"] or "")}</p><p class="muted">Status: <b>{esc(d["status"])}</b>'
            f'{" · follow up " + esc(d["follow_up_date"]) if d["follow_up_date"] else ""}</p></div>'
            f'<div class="card" style="margin-top:12px"><h2>Comments</h2>{thread or "<p class=muted>No comments yet.</p>"}'
            + _form(ctx, f"/item/{item_id}/comment",
                    '<textarea name="body" rows="3" style="width:100%" required placeholder="What is this? What did you find out?"></textarea>'
                    f'<div class="row" style="margin-top:8px"><label>Set status <select name="status"><option value="">(unchanged)</option>{status_opts}</select></label>'
                    '<label>Follow up on <input type="date" name="follow_up"></label><button>Save comment</button></div>')
            + "</div>")
    return layout(conn, ctx, "/items", d["title"], body)


def imports(conn: sqlite3.Connection, ctx: Ctx) -> str:
    from .views_setup import dropzone
    out = ["<h1>Imports</h1>", dropzone(ctx),
           (f'<div class="card" style="margin-top:12px"><h2>Waiting</h2><p>{ctx.inbox_count} file(s) are waiting to be imported.</p>'
            + _form(ctx, "/import", "<button>Import them now</button>") + "</div>") if ctx.inbox_count else ""]
    for b in conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 15"):
        files = [r["original_name"] for r in conn.execute("SELECT original_name FROM source_files WHERE batch_id = ?", (b["id"],))]
        n = conn.execute("SELECT COUNT(*) FROM transactions WHERE batch_id = ?", (b["id"],)).fetchone()[0]
        head = (f'<h2>Import #{b["id"]} <span class="chip">{esc(b["status"])}</span></h2>'
                f'<p class="muted">Created {esc(b["created_at"].replace("T", " "))} · {n} transactions'
                f'{" · " + esc(b["note"]) if b["note"] else ""}</p>'
                f'<small>{esc(", ".join(files)) if files else "no files (sheet history or balances)"}</small>')
        if b["status"] == "committed":
            action = (f'<p>Submitted <b>{esc(b["committed_at"].replace("T", " "))}</b></p>'
                      + _form(ctx, f"/imports/{b['id']}/reopen",
                              '<input name="reason" placeholder="Why are you reopening it?" required size="40"> <button class="quiet">Reopen</button>', "row"))
        else:
            issues = commits.readiness(conn, b["id"], ctx.today)
            lst = "".join(f"<li>{esc(i)}</li>" for i in issues[:12]) + (f"<li>and {len(issues) - 12} more</li>" if len(issues) > 12 else "")
            warn = (f'<p><b>Still open:</b></p><ul>{lst}</ul><label><input type="checkbox" name="ack" value="1"> '
                    "I know about these; submit anyway (they are recorded)</label> " if issues else "<p>Nothing open. Ready to submit.</p>")
            action = _form(ctx, f"/imports/{b['id']}/submit", warn + "<button>Submit</button>")
        out.append(f'<div class="card" style="margin-top:12px">{head}{action}</div>')
    hist = commits.history(conn)
    if hist:
        rows = "".join(f'<tr><td>#{c["batch_id"]} v{c["version"]}</td><td>{esc(c["committed_at"].replace("T", " "))}</td><td>{esc(c["reason"] or "")}</td></tr>' for c in hist)
        out.append(f'<div class="card" style="margin-top:12px"><h2>Submit history</h2><table><tbody>{rows}</tbody></table></div>')
    return layout(conn, ctx, "/imports", "Imports", "".join(out))


def balances_page(conn: sqlite3.Connection, ctx: Ctx, q: dict | None = None) -> str:
    q = q or {}
    only_todo = q.get("show") == "todo"
    due, last = balances.balances_due(conn, ctx.today)
    rows, folded_rows = [], []
    accts = conn.execute("""SELECT a.id, a.label, a.kind, a.currency, o.name AS owner FROM accounts a
                            LEFT JOIN owners o ON o.id = a.owner_id
                            WHERE a.active = 1 AND a.kind != 'card' AND a.issuer NOT IN ('sheet','cash') AND (a.closed_on IS NULL OR a.closed_on > ?)
                            ORDER BY a.sort_order, a.kind, a.label""", (ctx.today,)).fetchall()
    latest = {r["account_id"]: r for r in balances.latest(conn, ctx.today)}
    fold = balances.fold_count(conn)
    above = len(accts) if fold is None else min(fold, len(accts))
    this_month = ctx.today[:7]
    done_now = {a["id"] for a in accts if latest.get(a["id"]) and latest[a["id"]]["as_of"][:7] == this_month}
    n_todo = len(accts) - len(done_now)
    for n, a in enumerate(accts):
        l = latest.get(a["id"])
        stale = l and l["as_of"] < _days_ago(ctx.today, balances.STALE_DAYS)
        if only_todo and a["id"] in done_now:
            continue
        (rows if (n < above or only_todo) else folded_rows).append(f'<tr><td dir="auto">{esc(a["label"])}</td><td>{esc(a["kind"])}</td><td>{esc(a["owner"] or "")}</td>'
                    f'<td class="n">{esc(money(l["amount"], l["currency"]) if l else "–")}</td>'
                    f'<td>{esc(l["as_of"]) if l else ""}{" ▲ old" if stale else ""}{" ✔ this month" if a["id"] in done_now else ""}</td>'
                    f'<td><input name="bal_{a["id"]}" inputmode="decimal" size="12" placeholder="new value"> {esc(a["currency"])}</td>'
                    + (f'<td><label class="cap"><input type="checkbox" name="same_{a["id"]}" value="1"> same</label></td>' if l else "<td></td>") + '</tr>')
    filt = ('<p class="seg"><a class="%s" href="/balances">All accounts (%d)</a> <a class="%s" href="/balances?show=todo">Not updated this month (%d)</a></p>'
            % ("" if only_todo else "on", len(accts), "on" if only_todo else "", n_todo))
    if only_todo and not n_todo:
        filt += '<div class="card"><p>Every account has a balance from this month. ✔</p></div>'
    banner = (f'<div class="banner">Balances are due{" (last updated " + esc(last) + ")" if last else ""}.</div>' if due else "")
    kinds = "".join(f"<option>{k}</option>" for k in ("bank", "investment", "pension", "savings", "real_estate", "loan", "other"))
    owner_opts = '<option value="">(none)</option>' + "".join(f"<option>{esc(o)}</option>" for o in ctx.owners)
    form = _form(ctx, "/balances",
                 f'<p>Date of these numbers: <input type="date" name="as_of" value="{esc(ctx.today)}"></p>'
                 f'<div style="overflow-x:auto"><table><thead><tr><th>Account</th><th>Type</th><th>Owner</th><th class="n">Last value</th><th>As of</th><th>New value</th><th title="Nothing changed since last time">Unchanged?</th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table>'
                 + (f'<details style="margin-top:6px"><summary class="muted">{len(folded_rows)} more account(s), shown only when expanded</summary>'
                    f'<table><tbody>{"".join(folded_rows)}</tbody></table></details>' if folded_rows else "")
                 + f'</div><p class="row"><button>Save</button> <button class="quiet" name="same_all" value="1" title="Type the ones that changed, then press this">'
                 f'Save, and keep every other account as it was</button></p>'
                 f'<p class="muted">Type only what changed. Tick “same” for an account that did not change, or use the second button to keep all the rest. '
                 f'Loans can be entered as positive numbers.</p>')
    add = _form(ctx, "/accounts", f'<div class="row"><input name="label" placeholder="Account name" required> <select name="kind">{kinds}</select> '
                f'<select name="currency"><option>ILS</option><option>USD</option></select> <select name="owner">{owner_opts}</select> <button class="quiet">Add account</button></div>')
    cur_rate = fx.current_rate(conn)
    rate_box = (f'<div class="card" style="margin-bottom:12px"><h2>Dollar rate</h2><p>Dollar accounts are valued at <b>{esc(fx_note(conn, ctx.today))}</b>. '
                "Expenses are never converted: your cards already charge shekels.</p>"
                + _form(ctx, "/fx/set", f'<label>Today\'s rate (₪ per $) <input name="rate" inputmode="decimal" size="8" value="{cur_rate["rate"]:.3f}" required></label> '
                        '<button>Use this rate</button>' if cur_rate else
                        '<label>Today\'s rate (₪ per $) <input name="rate" inputmode="decimal" size="8" required></label> <button>Use this rate</button>', "row")
                + _form(ctx, "/fx/refresh", '<button class="quiet">Fetch the current rate automatically</button>', "inline") + "</div>")
    items = []
    for n, a in enumerate(accts):
        arrows = (_form(ctx, "/accounts/move", f'<input type="hidden" name="id" value="{a["id"]}"><input type="hidden" name="dir" value="up">'
                        f'<button class="quiet" title="Move up" {"disabled" if n == 0 else ""}>▲</button>', "inline") + " "
                  + _form(ctx, "/accounts/move", f'<input type="hidden" name="id" value="{a["id"]}"><input type="hidden" name="dir" value="down">'
                          f'<button class="quiet" title="Move down" {"disabled" if n == len(accts) - 1 else ""}>▼</button>', "inline"))
        edit = ("<details><summary>Edit</summary>"
                + _form(ctx, "/accounts/rename", f'<input type="hidden" name="id" value="{a["id"]}"><input name="label" value="{esc(a["label"])}" required> <button class="quiet">Rename</button>', "row")
                + _form(ctx, "/accounts/close", f'<input type="hidden" name="id" value="{a["id"]}"><label>Closed on <input type="date" name="on" value="{esc(ctx.today)}"></label> '
                        '<button class="quiet">Close it</button> <span class="muted">kept in the past, left out from that date</span>', "row")
                + _form(ctx, "/accounts/delete", f'<input type="hidden" name="id" value="{a["id"]}"><button class="quiet">Delete it</button> '
                        '<span class="muted">removes it and all its balances, as if it never existed</span>', "row")
                + "</details>")
        if n == above:
            items.append('<li class="foldline" draggable="true" data-fold="1">▼ Below this line: only shown when expanded <span class="muted">(drag this line up or down)</span></li>')
        items.append(f'<li draggable="true" data-id="{a["id"]}"><span class="grab" aria-hidden="true" title="Drag to move">⠿</span>'
                     f'<span dir="auto" class="nm">{esc(a["label"])}</span><span class="muted">{esc(a["kind"])} · {esc(a["owner"] or "")}</span>'
                     f'<span class="arrows">{arrows}</span><span class="ed">{edit}</span></li>')
    if above >= len(accts):
        items.append('<li class="foldline" draggable="true" data-fold="1">▼ Below this line: only shown when expanded <span class="muted">(drag this line up to hide accounts you rarely update)</span></li>')
    arrange_js = ("(function(){var ul=document.getElementById('acc-list');if(!ul)return;var drag=null;"
                  "ul.addEventListener('dragstart',function(e){drag=e.target.closest('li');if(drag){drag.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain','x')}});"
                  "ul.addEventListener('dragover',function(e){e.preventDefault();var over=e.target.closest('li');if(!drag||!over||over===drag)return;"
                  "var r=over.getBoundingClientRect();ul.insertBefore(drag,(e.clientY-r.top)>r.height/2?over.nextSibling:over)});"
                  "ul.addEventListener('dragend',function(){if(!drag)return;drag.classList.remove('dragging');drag=null;var ids=[],above=0,seen=false;"
                  "ul.querySelectorAll('li').forEach(function(li){if(li.dataset.fold){seen=true}else{ids.push(li.dataset.id);if(!seen)above++}});"
                  "var fd=new URLSearchParams();fd.set('csrf',ul.dataset.csrf);fd.set('order',ids.join(','));fd.set('above',above);"
                  "fetch('/accounts/arrange',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:fd.toString()}).then(function(){var m=document.getElementById('saved');if(m){m.textContent='Order saved';setTimeout(function(){m.textContent=''},2000)}})})})();")
    arrange = ('<div class="card" style="margin-top:12px"><h2>Arrange your accounts</h2><p class="muted">Drag an account by its handle ⠿ to put it where you want, or use the arrows. '
               'Drag the dashed line to decide which accounts are always shown (above it) and which only appear when you expand the list (below it). '
               'The order is saved the moment you drop. Rename, close or delete an account under Edit. <span id="saved" class="chip good" style="border:0"></span></p>'
               f'<ul id="acc-list" class="acc" data-csrf="{esc(ctx.csrf)}">{"".join(items)}</ul></div><script>{arrange_js}</script>')
    return layout(conn, ctx, "/balances", "Balances",
                  f'<h1>Balances</h1>{banner}{filt}{rate_box}<div class="card">{form}</div>{arrange}<div class="card" style="margin-top:12px"><h2>New account</h2>{add}</div>')


def _days_ago(today: str, days: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(today) - timedelta(days=days)).isoformat()


def goals_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    gp = goals.all_progress(conn, ctx.today)
    cards = "".join(_goal_card(g) for g in gp) or '<p class="muted">No goals yet. Add one below.</p>'
    acct_opts = "".join(f'<label style="display:block"><input type="checkbox" name="account" value="{a["id"]}"> {esc(a["label"])}</label>'
                        for a in conn.execute("SELECT id, label FROM accounts WHERE kind NOT IN ('card') AND issuer NOT IN ('sheet','cash') ORDER BY label"))
    owner_opts = '<option value="">Household</option>' + "".join(f"<option>{esc(o)}</option>" for o in ctx.owners)
    birth = "".join(
        _form(ctx, "/birth", f'<input type="hidden" name="owner" value="{esc(o["name"])}">{esc(o["name"])}: '
              f'<input type="date" name="birth" value="{esc(o["birth_date"] or "")}" required> <button class="quiet">Save</button>', "inline")
        + " " for o in conn.execute("SELECT name, birth_date FROM owners ORDER BY name"))
    add = _form(ctx, "/goals",
                '<div class="row"><input name="name" placeholder="Goal name" required> <input name="amount" inputmode="decimal" placeholder="Target amount" required>'
                '<select name="currency"><option>ILS</option><option>USD</option></select></div>'
                '<div class="row" style="margin-top:8px"><label>By date <input type="date" name="by"></label> <b>or</b> '
                f'<label>at age <input name="age" size="3" inputmode="numeric"></label> <label>whose <select name="owner">{owner_opts}</select></label>'
                '<label>expected yearly return % <input name="return" size="3" value="0"></label></div>'
                f'<details style="margin-top:8px"><summary>Which accounts count? (none = total net worth)</summary>{acct_opts}</details>'
                '<p><button>Add goal</button></p>')
    return layout(conn, ctx, "/goals", "Goals",
                  f'<h1>Goals</h1><div class="card">{cards}</div><div class="card" style="margin-top:12px"><h2>New goal</h2>{add}'
                  f'<p class="muted">Goals by age need a birth date: {birth}</p></div>')


def sheet_body(conn: sqlite3.Connection, ctx: Ctx, back: str = "/sheet") -> str:
    st = sheetsync.status(conn)
    head = ("" 
            + (f'<div class="card"><h2>Linked <span class="chip good"><b>✔</b> on</span></h2><p>Last updated: <b>{esc((st["last_sync"] or "never").replace("T", " "))}</b>. '
               f'It updates by itself every time you submit an import.</p><p class="muted">{esc(st["last_result"] or "")}</p>'
               + _form(ctx, "/sheet/sync", "<button>Update it now</button>", "inline") + " "
               + _form(ctx, "/sheet/notes", f'<label><input type="checkbox" name="notes" value="1" {"checked" if st["notes"] else ""} onchange="this.form.submit()"> '
                       "Also share comments on items (they may be private)</label>", "inline") + " "
               + _form(ctx, "/sheet/unlink", '<button class="quiet">Unlink</button>', "inline") + "</div>"
               if st["linked"] else
               '<div class="card"><h2>Not linked yet</h2><p>Do this once. After that the sheet updates itself after every submit, '
               "and only submitted numbers are sent.</p></div>"))
    script = esc(sheetsync.script_text(conn))
    doc = (f'<a href="{esc(st["doc_url"])}" target="_blank" rel="noopener"><b>your shared sheet</b></a>' if st["doc_url"]
           else "the Google Sheet you want to share (a new blank one is best)")
    copy_js = ("var t=document.getElementById('scr');t.select();"
               "(navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).then(function(){this.textContent='Copied ✔'}.bind(this))"
               ".catch(function(){document.execCommand('copy')});")
    steps = ('<div class="card" style="margin-top:12px"><h2>Link it (3 minutes, once)</h2>'
             "<p class=muted>Google only lets a sheet's owner approve a script that writes to it, so this one step has to be yours. The same script also serves the phone page (turn it on under Settings, Open on your phone). "
             "If you linked the Sheet earlier, paste the script again, then choose <b>Deploy › Manage deployments</b>, the pencil, <b>New version</b>, <b>Deploy</b>.</p><ol>"
             f"<li>Open {doc}, then choose <b>Extensions › Apps Script</b>.</li>"
             f'<li><button onclick="{copy_js}">Copy the script</button> then delete what is in the editor, paste, and click <b>Save</b>.</li>'
             "<li><b>Deploy › New deployment</b>, type <b>Web app</b>. Execute as: <b>Me</b>. Who has access: <b>Anyone</b>. "
             "Click <b>Deploy</b> and allow it. Google warns the app is unverified because you wrote it: choose Advanced, then continue.</li>"
             "<li>Copy the <b>Web app</b> address and paste it here:</li></ol>"
             + _form(ctx, "/sheet/link", '<input name="url" style="width:100%;max-width:560px" placeholder="https://script.google.com/macros/s/…/exec" required> <button>Link and test</button>', "row")
             + '<p class="muted">The script contains a secret that only this app knows. Anyone with the address still cannot change your sheet without it.</p>'
             f'<textarea id="scr" readonly rows="8" style="width:100%;font:12px monospace" onclick="this.select()">{script}</textarea></div>')
    html = head + steps
    if back == "/sheet":
        return html
    token = f'<input type="hidden" name="csrf" value="{esc(ctx.csrf)}">'
    return html.replace(token, token + f'<input type="hidden" name="back" value="{esc(back)}">')


def sheet_page(conn: sqlite3.Connection, ctx: Ctx) -> str:
    return layout(conn, ctx, "/sheet", "Google Sheet", "<h1>Google Sheet</h1>" + sheet_body(conn, ctx))
