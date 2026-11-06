"""
Look and feel for the dashboard: one stylesheet plus small HTML builders.

Palette (gold signal desk): cool paper workspace, slate ink, brass for the gold
identity, green for BUY, red for SELL. Prices use tabular figures so they line up.
"""
from __future__ import annotations

from html import escape
from typing import Iterable, List, Optional

import pandas as pd

from strategy import fmt

INK, SLATE, PAPER, PANEL, LINE = "#1C2630", "#5B6B7A", "#F5F6F8", "#FFFFFF", "#DDE2E8"
BRASS, BUY_C, SELL_C = "#9A6B12", "#157A4F", "#B4362E"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&display=swap');
:root {{
  --ink:{INK}; --slate:{SLATE}; --paper:{PAPER}; --panel:{PANEL}; --line:{LINE};
  --brass:{BRASS}; --brass-soft:#F3EBDC; --buy:{BUY_C}; --buy-soft:#E3F2EA;
  --sell:{SELL_C}; --sell-soft:#F8E5E3; --amber:#8A6100; --amber-soft:#FFF4D6; --mute:#EDF0F3;
}}
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp button, .stApp textarea,
[data-testid="stMarkdownContainer"], [data-baseweb="tab"] {{
  font-family: 'Archivo', 'Segoe UI', system-ui, sans-serif;
}}
.stApp {{ background: var(--paper); color: var(--ink); }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stAppDeployButton"], [data-testid="stDecoration"] {{ display: none; }}
.block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1500px; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ background: var(--panel); border-color: var(--line) !important; border-radius: 8px; }}
[data-testid="stMetricValue"] {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
[data-baseweb="tab"] p {{ font-weight: 600; }}
section[data-testid="stSidebar"] {{ background: #EEF0F3; border-right: 1px solid var(--line); }}
section[data-testid="stSidebar"] button[kind="primary"] {{ background: var(--sell); border-color: var(--sell); }}
section[data-testid="stSidebar"] button[kind="primary"]:hover {{ background: #9A2C25; border-color: #9A2C25; }}
:focus-visible {{ outline: 2px solid var(--brass) !important; outline-offset: 2px; }}

.num {{ font-variant-numeric: tabular-nums; }}
.strip {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px 22px; padding: 4px 2px 14px;
  border-bottom: 1px solid var(--line); margin-bottom: 16px; }}
.brand {{ display: flex; align-items: center; gap: 10px; font-size: 1.35rem; font-weight: 700; }}
.strip .grow {{ flex: 1 1 0; min-width: 8px; }}
.stripw {{ container-type: inline-size; }}
@container (max-width: 840px) {{ .strip .kv.opt {{ display: none; }} .strip {{ column-gap: 18px; }} }}
.brand i {{ width: 6px; height: 24px; background: var(--brass); border-radius: 2px; display: inline-block; }}
.kv {{ display: flex; flex-direction: column; }}
.kv .k {{ font-size: .76rem; color: var(--slate); }}
.kv .v {{ font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; }}
.v.pos {{ color: var(--buy); }} .v.neg {{ color: var(--sell); }}
.pill {{ display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px;
  font-size: .8rem; font-weight: 600; white-space: nowrap; }}
.pill i {{ width: 7px; height: 7px; border-radius: 50%; background: currentColor; display: inline-block; }}
.pill.ok {{ background: var(--buy-soft); color: var(--buy); }}
.pill.warn {{ background: var(--amber-soft); color: var(--amber); }}
.pill.bad {{ background: var(--sell-soft); color: var(--sell); }}
.pill.mute {{ background: var(--mute); color: var(--slate); }}

.ptitle {{ font-size: 1rem; font-weight: 700; margin: 2px 0 2px; }}
.psub {{ font-size: .82rem; color: var(--slate); margin: 0 0 8px; }}

.mrow {{ display: grid; grid-template-columns: minmax(92px, 1fr) minmax(96px, 1fr) minmax(170px, 2.2fr) auto;
  align-items: center; gap: 6px 18px; padding: 12px 2px 11px; border-top: 1px solid var(--line); }}
.mrow .why {{ grid-column: 1 / -1; }}
.sym {{ font-weight: 700; font-size: 1.02rem; }}
.tag {{ display: inline-block; margin-top: 3px; font-size: .7rem; font-weight: 600; padding: 1px 7px; border-radius: 4px; }}
.tag.tested {{ background: var(--brass-soft); color: var(--brass); }}
.tag.untested {{ background: var(--mute); color: var(--slate); }}
.px {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
.px small {{ display: block; color: var(--slate); font-weight: 400; font-size: .74rem; }}
.meter {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }}
.meter b {{ height: 9px; border-radius: 2px; background: #E1E5EA; }}
.meter b.buy {{ background: var(--buy); }} .meter b.sell {{ background: var(--sell); }}
.mlab {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; margin-top: 4px;
  font-size: .68rem; color: var(--slate); text-align: center; }}
.why {{ font-size: .84rem; color: var(--slate); line-height: 1.35; }}
.state {{ justify-self: end; }}

.hero {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 26px; padding: 16px 18px 18px;
  margin-bottom: 14px; border: 1px solid var(--line); border-left: 6px solid var(--buy); border-radius: 8px; background: #FBFCFD; }}
.hero.sell {{ border-left-color: var(--sell); }}
.hero .head {{ font-size: 2.05rem; font-weight: 700; letter-spacing: -.01em; line-height: 1.1; }}
.hero .head.buy {{ color: var(--buy); }} .hero .head.sell {{ color: var(--sell); }}
.hero .head span {{ color: var(--ink); }}
.hero .meta {{ color: var(--slate); font-size: .86rem; margin-top: 4px; }}
.facts {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 18px; margin-top: 16px; }}
.facts .k {{ font-size: .76rem; color: var(--slate); }}
.facts .v {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.note {{ margin-top: 12px; font-size: .84rem; padding: 8px 10px; border-radius: 6px; background: var(--amber-soft); color: var(--amber); }}
.ladder {{ font-variant-numeric: tabular-nums; align-self: center; }}
.rung {{ display: grid; grid-template-columns: 74px 1fr; align-items: baseline; gap: 0 10px;
  padding: 9px 12px; border-left: 3px solid var(--line); }}
.rung.target {{ border-left-color: var(--buy); }}
.rung.stop {{ border-left-color: var(--sell); }}
.rung.entry {{ border-left-color: var(--ink); background: #EEF1F4; }}
.rung .l {{ font-size: .8rem; color: var(--slate); }}
.rung .p {{ font-size: 1.2rem; font-weight: 700; }}
.rung .a {{ grid-column: 2; font-weight: 600; font-size: .84rem; white-space: nowrap; }}
.herow {{ container-type: inline-size; }}
@container (max-width: 640px) {{
  .hero {{ grid-template-columns: minmax(0, 1fr) minmax(0, .8fr); gap: 18px; padding: 14px 14px 16px; }}
  .facts {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .hero .head {{ font-size: 1.75rem; }}
}}
@container (max-width: 470px) {{ .hero {{ grid-template-columns: 1fr; }} }}
.a.pos {{ color: var(--buy); }} .a.neg {{ color: var(--sell); }}

.stApp table.rec {{ width: 100%; border-collapse: collapse; border: 0; font-size: .85rem; font-variant-numeric: tabular-nums; }}
.stApp table.rec tr {{ border: 0; background: transparent; }}
.stApp table.rec td {{ padding: 9px 6px 9px 0; border: 0; border-top: 1px solid var(--line); vertical-align: middle; }}
.rec .t {{ color: var(--ink); white-space: nowrap; font-weight: 600; }}
.rec .t small {{ display: block; color: var(--slate); font-weight: 400; font-size: .74rem; }}
.rec .s {{ font-weight: 700; }}
.s.buy {{ color: var(--buy); }} .s.sell {{ color: var(--sell); }}
.rec .o {{ color: var(--slate); }}
.rec .r {{ text-align: right; font-weight: 600; }}
.r.pos {{ color: var(--buy); }} .r.neg {{ color: var(--sell); }}

.checks .c {{ display: grid; grid-template-columns: 46px 18px minmax(0, 1fr); gap: 8px; padding: 9px 0;
  border-top: 1px solid var(--line); align-items: start; }}
.checks .tf {{ font-weight: 700; }}
.checks .ok {{ color: var(--buy); font-weight: 700; }} .checks .no {{ color: var(--sell); font-weight: 700; }}
.checks .role {{ font-size: .78rem; color: var(--slate); }}
.checks .d {{ font-size: .84rem; overflow-wrap: anywhere; }}
.reasons {{ margin: 10px 0 0; padding-left: 18px; font-size: .86rem; color: var(--slate); }}

.confirm {{ padding: 2px 2px 4px; }}
.confirm .ct {{ font-weight: 700; font-size: 1rem; margin-bottom: 4px; }}
.confirm .cl {{ font-size: 1.02rem; font-variant-numeric: tabular-nums; }}
.confirm .cg {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 10px 0 8px;
  font-variant-numeric: tabular-nums; font-weight: 600; }}
.confirm .cg .k {{ display: block; font-size: .76rem; color: var(--slate); font-weight: 400; }}
.confirm .cg .a {{ margin-left: 8px; font-size: .88rem; }}
.posr {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 10px; align-items: baseline;
  font-variant-numeric: tabular-nums; }}
.posr > div:first-child > span {{ display: inline-block; white-space: nowrap; }}
.posr .sym {{ font-weight: 700; font-size: .98rem; margin-right: 6px; }}
.posr .s {{ font-weight: 700; font-size: .82rem; margin-right: 6px; }}
.posr .vol {{ color: var(--slate); font-size: .82rem; }}
.posr .pl {{ font-weight: 700; text-align: right; white-space: nowrap; }}
.posr .l2 {{ grid-column: 1 / -1; font-size: .78rem; color: var(--slate); }}
.lvs {{ display: flex; flex-wrap: wrap; gap: 2px 12px; margin: 4px 0 2px; font-size: .8rem; color: var(--ink);
  font-variant-numeric: tabular-nums; font-weight: 600; }}
.lvs .lv .k {{ color: var(--slate); font-weight: 400; margin-right: 4px; }}
.lvs .lv.sl {{ color: var(--sell); }} .lvs .lv.tp {{ color: var(--buy); }}
.lvs .lv i {{ font-style: normal; font-weight: 400; color: var(--amber); margin-left: 4px; }}
.rec2 .rr {{ display: grid; grid-template-columns: 62px minmax(0, 1fr) auto; gap: 8px; padding: 9px 0;
  border-top: 1px solid var(--line); align-items: start; font-size: .85rem; font-variant-numeric: tabular-nums; }}
.rec2 .rr.run {{ background: #F6FAF7; margin: 0 -8px; padding: 9px 8px; border-radius: 6px; }}
.rec2 .t {{ font-weight: 600; white-space: nowrap; }}
.rec2 .t small {{ display: block; color: var(--slate); font-weight: 400; font-size: .72rem; }}
.rec2 .sym {{ font-weight: 600; }}
.rec2 .s {{ font-weight: 700; margin-left: 4px; }}
.rec2 .o {{ color: var(--slate); margin-left: 4px; }}
.rec2 .rr.run .o {{ color: var(--buy); font-weight: 600; }}
.rec2 .r {{ font-weight: 700; white-space: nowrap; }}
.posr .late {{ grid-column: 1 / -1; font-size: .8rem; font-weight: 600; color: var(--amber);
  background: var(--amber-soft); border-radius: 5px; padding: 3px 8px; margin-top: 3px; }}
.pl.pos {{ color: var(--buy); }} .pl.neg {{ color: var(--sell); }}
[class*="st-key-buy_"] button[kind="primary"], [class*="st-key-buy_"] [data-testid="stBaseButton-primary"] {{
  background: var(--buy); border-color: var(--buy); color: #fff; }}
[class*="st-key-sell_"] button[kind="primary"], [class*="st-key-sell_"] [data-testid="stBaseButton-primary"] {{
  background: var(--sell); border-color: var(--sell); color: #fff; }}

/* ---- trade box: one market, live price, signal, stop-loss and target on one track ---- */
.tb {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 24px; align-items: end; }}
.tb .word {{ font-size: 2.6rem; font-weight: 700; line-height: 1; letter-spacing: -.02em; }}
.tb .word.buy {{ color: var(--buy); }} .tb .word.sell {{ color: var(--sell); }}
.tb .word.wait {{ color: var(--slate); font-size: 2rem; }} .tb .word.open {{ color: var(--ink); font-size: 2rem; }}
.tb .sub {{ color: var(--slate); font-size: .88rem; margin-top: 6px; }}
.tb .quote {{ text-align: right; font-variant-numeric: tabular-nums; }}
.tb .quote .big {{ font-size: 1.9rem; font-weight: 700; line-height: 1.05; }}
.tb .quote .big span {{ color: var(--slate); font-weight: 500; }}
.tb .quote small {{ display: block; color: var(--slate); font-size: .78rem; margin-top: 3px; }}
.tb .quote .stale {{ color: var(--sell); }}
.tb .pl {{ font-size: 1.9rem; font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }}
.tb .pl.pos {{ color: var(--buy); }} .tb .pl.neg {{ color: var(--sell); }}
.track {{ position: relative; margin: 34px 4px 4px; font-variant-numeric: tabular-nums; }}
.track .bar {{ position: relative; height: 10px; border-radius: 5px;
  background: linear-gradient(90deg, var(--sell-soft), #EEF1F4 35%, #EEF1F4 65%, var(--buy-soft)); }}
.track .bar::before, .track .bar::after {{ content: ""; position: absolute; top: -5px; width: 4px; height: 20px; border-radius: 2px; }}
.track .bar::before {{ left: -2px; background: var(--sell); }}
.track .bar::after {{ right: -2px; background: var(--buy); }}
.track .fill {{ position: absolute; top: 0; height: 10px; border-radius: 5px; opacity: .55; }}
.track .fill.pos {{ background: var(--buy); }} .track .fill.neg {{ background: var(--sell); }}
.track .mk {{ position: absolute; top: -6px; width: 2px; height: 22px; transform: translateX(-1px); }}
.track .mk.entry {{ background: var(--ink); }}
.track .mk.half {{ background: var(--buy); width: 3px; }}
.track .lab.half {{ top: 20px; color: var(--buy); font-weight: 600; }}
.track .mk.now {{ top: -5px; width: 20px; height: 20px; border-radius: 50%; transform: translateX(-10px);
  background: var(--panel); border: 4px solid var(--ink); }}
.track .lab {{ position: absolute; transform: translateX(-50%); white-space: nowrap; font-size: .8rem; }}
.track .lab.now {{ top: -30px; font-weight: 700; }}
.track .lab.entry {{ top: 20px; color: var(--slate); }}
.track .ends {{ display: flex; justify-content: space-between; margin-top: 30px; }}
.track .ends > div {{ display: flex; flex-direction: column; }}
.track .ends .tp {{ align-items: flex-end; text-align: right; }}
.track .ends .k {{ font-size: .76rem; color: var(--slate); }}
.track .ends .p {{ font-size: 1.25rem; font-weight: 700; }}
.track .ends .sl .p {{ color: var(--sell); }} .track .ends .tp .p {{ color: var(--buy); }}
.track .ends small {{ color: var(--slate); font-size: .78rem; }}
.tbfacts {{ display: flex; flex-wrap: wrap; gap: 8px 26px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); }}
.tbfacts .k {{ font-size: .74rem; color: var(--slate); display: block; }}
.tbfacts .v {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.tbwhy {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); font-size: .9rem; }}
.tbwhy .meter {{ max-width: 340px; margin-bottom: 4px; }} .tbwhy .mlab {{ max-width: 340px; }}
.tbwhy p {{ margin: 10px 0 0; color: var(--slate); }}

/* ---- results by day ---- */
.dsum {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px 16px; padding: 12px 0 14px;
  border-top: 1px solid var(--line); font-variant-numeric: tabular-nums; }}
.dsum .k {{ display: block; font-size: .76rem; color: var(--slate); }}
.dsum .v {{ display: block; font-size: 1.7rem; font-weight: 700; line-height: 1.15; }}
.dsum .v.pos {{ color: var(--buy); }} .dsum .v.neg {{ color: var(--sell); }}
.dsum small {{ display: block; color: var(--slate); font-size: .76rem; }}
.dtab {{ margin-top: 6px; font-variant-numeric: tabular-nums; }}
.dtab .dr {{ display: grid; grid-template-columns: minmax(118px, 1.2fr) minmax(70px, 2fr) repeat(3, 52px) 104px;
  gap: 8px; padding: 7px 0; border-top: 1px solid var(--line); align-items: center; font-size: .85rem; }}
.dtab .dr.dh {{ color: var(--slate); font-size: .74rem; border-top: 0; padding-bottom: 3px; }}
.dtab .dr.pick {{ background: var(--brass-soft); margin: 0 -8px; padding: 7px 8px; border-radius: 6px; }}
.dtab .dr.off {{ color: var(--slate); }}
.dtab .dr.tot {{ font-weight: 700; border-top: 2px solid var(--line); }}
.dtab .d small {{ color: var(--slate); margin-left: 6px; font-family: inherit; font-size: .78rem; }}
.dtab .n {{ text-align: right; white-space: nowrap; }}
.dtab .n.pos {{ color: var(--buy); font-weight: 600; }} .dtab .n.neg {{ color: var(--sell); font-weight: 600; }}
.dots i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 3px; vertical-align: middle; }}
.dots i.w {{ background: var(--buy); }} .dots i.l {{ background: var(--sell); }}
.dots i.run {{ box-shadow: inset 0 0 0 2px var(--slate); }}
@media (max-width: 760px) {{
  .dsum {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  .dtab .dr {{ grid-template-columns: minmax(96px, 1.3fr) repeat(3, 40px) 92px; gap: 6px; }}
  .dtab .dots {{ display: none; }}
}}
/* ---- both mode: which strategy a box, row or signal belongs to ---- */
.lanehead {{ font-size: .8rem; font-weight: 700; color: var(--brass); margin: 14px 0 6px; padding-top: 10px;
  border-top: 1px solid var(--line); }}
.lanehead.first {{ margin-top: 4px; padding-top: 0; border-top: 0; }}
.lane {{ display: inline-block; margin-left: 6px; font-size: .7rem; font-weight: 600; padding: 1px 6px; border-radius: 4px;
  background: var(--brass-soft); color: var(--brass); vertical-align: middle; }}
.lane.watch {{ background: var(--mute); color: var(--slate); }}
/* ---- latest signals: the newest BUY/SELL the desk showed, large ---- */
.lsig {{ border: 1px solid var(--line); border-left: 6px solid var(--buy); border-radius: 8px; padding: 10px 12px;
  margin: 4px 0 2px; background: #FBFCFD; font-variant-numeric: tabular-nums; }}
.lsig.sell {{ border-left-color: var(--sell); }}
.lsig .lt {{ display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }}
.lsig .lw {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 0 10px; margin-top: 2px; }}
.lsig .w {{ font-size: 1.6rem; font-weight: 700; line-height: 1.15; letter-spacing: -.01em; }}
.lsig .w.buy {{ color: var(--buy); }} .lsig .w.sell {{ color: var(--sell); }}
.lsig .sym {{ font-size: 1.6rem; font-weight: 700; line-height: 1.15; letter-spacing: -.01em; }}
.lsig .lane {{ margin-left: 0; }}
.lsig .when {{ font-weight: 700; white-space: nowrap; margin-right: 2px; }}
.lsig .when small {{ color: var(--slate); font-weight: 400; }}
.lsig .lvs {{ font-size: .86rem; margin-top: 8px; }}
.lsig .lb {{ display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin-top: 4px; }}
.lsig .o {{ color: var(--slate); font-weight: 600; }} .lsig .o.run {{ color: var(--buy); }}
.lsig .r {{ font-size: 1.3rem; font-weight: 700; white-space: nowrap; }}
.lsig .live {{ margin-top: 8px; font-size: .8rem; font-weight: 600; padding: 4px 8px; border-radius: 5px;
  background: var(--buy-soft); color: var(--buy); }}
.ltail {{ margin-top: 12px; font-size: .78rem; font-weight: 700; color: var(--slate); }}
@media (max-width: 1100px) {{
  .mrow {{ grid-template-columns: 1fr 1fr; }}
  .hero {{ grid-template-columns: 1fr; }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""

def e(x) -> str:
    return escape(str(x), quote=True)


def money(x, ccy: str = "") -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{'-' if x < 0 else ''}{abs(x):,.2f} {ccy}".strip()


def pill(text: str, kind: str = "mute") -> str:
    return f'<span class="pill {kind}"><i></i>{e(text)}</span>'


def kv(label: str, value: str, cls: str = "", optional: bool = False) -> str:
    """A label over a value. optional=True hides it when the header is short of room."""
    return (f'<div class="kv{" opt" if optional else ""}"><span class="k">{e(label)}</span>'
            f'<span class="v {cls}">{e(value)}</span></div>')


def strip(feed_pill: str, items: Iterable[str], extra_pill: str = "") -> str:
    return (f'<div class="stripw"><div class="strip"><div class="brand"><i></i>Signal desk</div>{feed_pill}{extra_pill}'
            '<span class="grow"></span>' + "".join(items) + "</div></div>")


def meter(sig) -> str:
    """Five segments, slowest timeframe first, filled when that timeframe's check passed."""
    checks = list(getattr(sig, "checks", []))[:5]
    side = "buy" if sig.considered == "BUY" else "sell"
    bars = "".join(f'<b class="{side if c.passed else ""}"></b>' for c in checks)
    labels = "".join(f"<span>{e(c.timeframe)}</span>" for c in checks)
    return f'<div><div class="meter">{bars}</div><div class="mlab">{labels}</div></div>'


def spread_text(spread: float, spec) -> str:
    """FX spreads in pips (0.7 pip); other markets in price units with the instrument's decimals."""
    if spread != spread:
        return "-"
    if spec is not None and spec.asset_class == "forex":
        pip = spec.tick_size * 10
        return f"{spread / pip:.1f} pip"
    digits = spec.digits if spec is not None else 2
    return f"{spread:.{digits}f}"


def state_of(sig) -> tuple:
    """(label, pill kind) for a market's current state."""
    if sig.decision in ("BUY", "SELL"):
        return f"{sig.decision} now", "ok" if sig.decision == "BUY" else "bad"
    if sig.reason.startswith("Data error") or not sig.checks:
        return "No data", "warn"
    if sig.reason.startswith("EMERGENCY"):
        return "Stopped", "bad"
    if all(c.passed for c in sig.checks):
        return "Blocked", "warn"
    return "Waiting", "mute"


def market_row(sig, spec) -> str:
    label, kind = state_of(sig)
    tested = ('<span class="tag tested">Tested</span>' if spec is not None and spec.tested_profitable
              else '<span class="tag untested">Untested</span>')
    if sig.bid == sig.bid and sig.bid:  # not NaN
        price = f'<div class="px">{e(fmt(sig.bid))}<small>spread {e(spread_text(sig.spread, spec))}</small></div>'
    else:
        price = '<div class="px">-</div>'
    why = "All five timeframes agree. See the trade box above." if sig.actionable else sig.reason
    if len(why) > 150:
        why = why[:147] + "..."
    return (f'<div class="mrow"><div><div class="sym">{e(sig.symbol)}</div>{tested}</div>{price}'
            f'{meter(sig) if sig.checks else "<div></div>"}<div class="state">{pill(label, kind)}</div>'
            f'<div class="why">{e(why)}</div></div>')


def hero(sig, ccy: str, local_time: Optional[str], strategy: str = "", close_by: Optional[str] = None,
         hold_h: float = 0) -> str:
    p = sig.plan
    side = sig.decision.lower()
    loss, prof = p.planned_loss, p.planned_profit
    rungs = {
        "target": f'<div class="rung target"><span class="l">Target</span><span class="p">{e(fmt(p.target))}</span>'
                  f'<span class="a pos">+{e(money(prof, ccy))}</span></div>',
        "entry": f'<div class="rung entry"><span class="l">Entry</span><span class="p">{e(fmt(p.entry))}</span>'
                 f'<span class="a">{e(f"{p.quantity:g} {p.unit}")}</span></div>',
        "stop": f'<div class="rung stop"><span class="l">Stop-loss</span><span class="p">{e(fmt(p.stop))}</span>'
                f'<span class="a neg">-{e(money(loss, ccy))}</span></div>',
    }
    order = ["target", "entry", "stop"] if side == "buy" else ["stop", "entry", "target"]  # highest price on top
    facts = [("Size", f"{p.quantity:g} {p.unit}"), ("Planned loss", money(-loss, ccy)),
             ("Planned profit", money(prof, ccy)), ("Net reward:risk", f"{p.reward_risk_net:.2f} : 1"),
             ("Risk of equity", f"{p.risk_pct_of_equity:.2f}%"), ("Costs", money(p.total_costs, ccy))]
    facts_html = "".join(f'<div><div class="k">{e(k)}</div><div class="v">{e(v)}</div></div>' for k, v in facts)
    notes = []
    if close_by:
        notes.append(f"If neither level is hit by {close_by}, close the trade then "
                     f"(the {hold_h:g}-hour limit used in testing).")
    if p.breakeven_trigger is not None:
        notes.append(f"Move the stop to the entry price once price reaches {fmt(p.breakeven_trigger)}.")
    if not sig.tested_market:
        notes.append("This market lost money in testing. Treat this signal as information only.")
    note_html = "".join(f'<div class="note">{e(n)}</div>' for n in notes)
    meta = (f"{strategy}, " if strategy else "") + f"{sig.setup_type or 'trade'} setup" \
        + (f", signalled {local_time}" if local_time else "")
    meta = meta[0].upper() + meta[1:]
    return (f'<div class="herow"><div class="hero {side}"><div><div class="head {side}">{e(sig.decision)} '
            f'<span>{e(sig.symbol)}</span></div><div class="meta">{e(meta)}</div><div class="facts">{facts_html}</div>'
            f'{note_html}</div><div class="ladder">{"".join(rungs[k] for k in order)}</div></div></div>')


def px(x, digits: int) -> str:
    """A price with the instrument's decimals and thousands separators (4,057.32)."""
    if x is None or x != x:
        return "-"
    return f"{x:,.{digits}f}"


def r_text(entry: float, stop: float, level: float) -> str:
    """How far `level` is from the entry, in multiples of the stop distance, e.g. '0.5R'."""
    dist = abs(entry - stop)
    return f"{round(abs(level - entry) / dist, 1):g}R" if dist > 0 else "first take-profit"


def track_html(side: str, stop: float, entry: float, target: float, now_price: float, digits: int,
               loss_money: Optional[float] = None, profit_money: Optional[float] = None, ccy: str = "",
               half_at: Optional[float] = None) -> str:
    """Stop-loss on the left, target on the right (for BUY and SELL alike, so right always means profit).
    Marks the entry and the live price; the stretch between them is green in profit, red in loss."""
    span = target - stop

    def at(x):
        return min(max((x - stop) / span * 100.0, 0.0), 100.0) if span else 50.0

    e_pct, n_pct = at(entry), at(now_price)
    up = (now_price - entry) * (1 if side == "BUY" else -1) >= 0
    a, b = sorted((e_pct, n_pct))
    sl_far, tp_far = abs(now_price - stop), abs(target - now_price)
    if loss_money is None:
        lm = ""
    elif loss_money > 0.005:
        lm = f'<span class="a neg">-{e(money(loss_money, ccy))}</span>'
    elif loss_money < -0.005:  # the stop was moved past the entry: hitting it now locks in a profit
        lm = f'<span class="a pos">+{e(money(-loss_money, ccy))} locked in</span>'
    else:
        lm = '<span class="a">break-even</span>'
    pm = f'<span class="a pos">+{e(money(profit_money, ccy))}</span>' if profit_money is not None else ""
    return (
        '<div class="track"><div class="bar">'
        f'<span class="fill {"pos" if up else "neg"}" style="left:{a:.1f}%;width:{b - a:.1f}%"></span>'
        + (f'<i class="mk half" style="left:{at(half_at):.1f}%"></i>'
           f'<span class="lab half" style="left:{min(max(at(half_at), 12), 88):.1f}%">half at {e(r_text(entry, stop, half_at))} {e(px(half_at, digits))}</span>'
           if half_at is not None else "") +
        f'<i class="mk entry" style="left:{e_pct:.1f}%"></i><i class="mk now" style="left:{n_pct:.1f}%"></i>'
        f'<span class="lab now" style="left:{min(max(n_pct, 9), 91):.1f}%">now {e(px(now_price, digits))}</span>'
        f'<span class="lab entry" style="left:{min(max(e_pct, 18), 82):.1f}%">entry {e(px(entry, digits))}</span>'
        '</div><div class="ends">'
        f'<div class="sl"><span class="k">Stop-loss</span><span class="p">{e(px(stop, digits))}</span>{lm}'
        f'<small>{e(px(sl_far, digits))} away</small></div>'
        f'<div class="tp"><span class="k">Target</span><span class="p">{e(px(target, digits))}</span>{pm}'
        f'<small>{e(px(tp_far, digits))} away</small></div></div></div>')


def quote_html(quote, digits: int, stamp: str, stale: bool = False) -> str:
    if quote is None:
        return '<div class="quote"><div class="big">-</div><small>no live price</small></div>'
    when = f'<b class="stale">{e(stamp)}</b>' if stale else e(stamp)
    return (f'<div class="quote"><div class="big">{e(px(quote.bid, digits))} <span>/</span> '
            f'{e(px(quote.ask, digits))}</div><small>bid / ask, spread {e(px(quote.spread, digits))}, '
            f'{when}</small></div>')


def facts_html(items) -> str:
    return '<div class="tbfacts">' + "".join(
        f'<div><span class="k">{e(k)}</span><span class="v">{e(v)}</span></div>' for k, v in items) + "</div>"


def box_signal(sig, quote, digits: int, ccy: str, sub: str, close_by: str = "", stamp: str = "live",
               stale: bool = False) -> str:
    """Trade box while a signal is live: BUY/SELL, live price, stop-loss and target on a track."""
    p = sig.plan
    side = sig.decision.lower()
    now_price = (quote.bid if sig.decision == "BUY" else quote.ask) if quote is not None else p.entry
    facts = [("Size", f"{p.quantity:g} {p.unit}"), ("Risk", money(p.planned_loss, ccy))]
    if p.partial_target is not None:
        q2 = p.quantity - p.partial_quantity
        r1 = p.partial_quantity * abs(p.partial_target - p.entry) * p.value_per_unit
        r2 = q2 * p.target_distance * p.value_per_unit
        facts += [(f"Half takes profit at {r_text(p.entry, p.stop, p.partial_target)}",
                   f"{p.partial_quantity:g} lots at {px(p.partial_target, digits)}"),
                  ("If both halves hit", money(r1 + r2 - p.commission, ccy)),
                  ("If only the first half", money(r1 - p.commission, ccy))]
        profit_money = r1 + r2 - p.commission
    else:
        facts += [("Reward", money(p.planned_profit, ccy)), ("Reward : risk", f"{p.reward_risk_net:.2f} : 1")]
        profit_money = p.planned_profit
    if close_by:
        facts.append(("Close by, if neither is hit", close_by))
    return (f'<div class="tb"><div><div class="word {side}">{e(sig.decision)} {e(sig.symbol)}</div>'
            f'<div class="sub">{e(sub)}</div></div>{quote_html(quote, digits, stamp, stale)}</div>'
            + track_html(sig.decision, p.stop, p.entry, p.target, now_price, digits,
                         p.planned_loss, profit_money, ccy, p.partial_target) + facts_html(facts))


def box_open(pos: dict, quote, digits: int, ccy: str, vpu: Optional[float], close_by: str, overdue: bool,
             stamp: str = "live", stale: bool = False, moved: Optional[dict] = None) -> str:
    """Trade box while a desk trade is open: live profit, stop-loss and target on a track."""
    side = pos["side"]
    now_price = pos["price"]
    sign = 1 if side == "BUY" else -1
    # money lost if the stop-loss is hit (negative once the stop has been moved past the entry)
    loss_units = pos.get("loss_units", sign * (pos["open_price"] - pos["stop"]) * pos["volume"] if pos["stop"] else None)
    gain_units = pos.get("gain_units", abs(pos["target"] - pos["open_price"]) * pos["volume"] if pos["target"] else None)
    loss = loss_units * vpu if vpu and loss_units is not None else None
    gain = gain_units * vpu if vpu and gain_units is not None else None
    pl = pos["profit"]
    vol = f"{pos['volume']:g}"
    tone = "pos" if pl > 0 else "neg" if pl < 0 else ""
    head = (f'<div class="tb"><div><div class="word open">Open {e(side)} {e(pos["symbol"])}</div>'
            f'<div class="sub">{e(vol)} lots from {e(px(pos["open_price"], digits))}. '
            f'Close by {e(close_by)} if neither level is hit.</div></div>'
            f'<div><div class="pl {tone}">{"+" if pl > 0 else ""}{e(money(pl, ccy))}</div>'
            f'{quote_html(quote, digits, stamp, stale)}</div></div>')
    track = (track_html(side, pos["stop"], pos["open_price"], pos["target"], now_price, digits, loss, gain, ccy,
                        pos.get("half_tp"))
             if pos["stop"] and pos["target"] and sign * (pos["target"] - pos["stop"]) > 0 else "")
    late = ('<div class="note">The time limit has passed: close this trade (the tested rule).</div>'
            if overdue else "")
    names = {"stop": ("Stop-loss", pos["stop"]), "target": ("Target", pos["target"])}
    changes = "".join(f'<div class="note">{e(names[k][0])} changed in MT5: was {e(px(old, digits))}, now '
                      f'{e(px(names[k][1], digits))}.</div>' for k, old in (moved or {}).items())
    return head + levels_html(side, pos["open_price"], pos["stop"], pos["target"], now_price, digits, moved) \
        + track + changes + late


def box_wait(sig, quote, digits: int, sub: str, why: str, extra: str = "", stamp: str = "live",
             stale: bool = False) -> str:
    """Trade box with no signal: live price, how many checks pass, and why there is no trade."""
    passed = sum(c.passed for c in sig.checks) if sig.checks else 0
    meter_html = meter(sig) if sig.checks else ""
    return (f'<div class="tb"><div><div class="word wait">No trade yet</div><div class="sub">{e(sub)}</div></div>'
            f'{quote_html(quote, digits, stamp, stale)}</div>'
            f'<div class="tbwhy">{meter_html}<div class="sub">{passed} of 5 timeframe checks pass for a '
            f'{e((sig.considered or "trade").upper())}.</div><p>{e(why)}</p>{extra}</div>')


def confirm_html(chk, ccy: str, seconds: int) -> str:
    side = chk.side.lower()
    split = len(chk.legs) > 1
    if split:
        (q1, tp1), (q2, tp2) = chk.legs
        targets = (f'<div><span class="k">First half ({e(f"{q1:g}")} lots) takes profit at</span>{e(fmt(tp1))}'
                   f'<span class="a pos">+{e(money(chk.partial_profit, ccy))}</span></div>'
                   f'<div><span class="k">Second half ({e(f"{q2:g}")} lots) at the target</span>{e(fmt(tp2))}'
                   f'<span class="a pos">+{e(money(chk.planned_profit - chk.partial_profit, ccy))}</span></div>')
        how = ("Two orders with the same stop-loss. When the first half takes its profit, the desk moves the "
               "other half's stop-loss to your entry price (while the dashboard is open).")
    else:
        targets = (f'<div><span class="k">Target</span>{e(fmt(chk.target))}'
                   f'<span class="a pos">+{e(money(chk.planned_profit, ccy))}</span></div>')
        how = "The stop-loss and target go in with the order."
    return (f'<div class="confirm {side}"><div class="ct">Send this order to MetaTrader 5?</div>'
            f'<div class="cl"><b class="s {side}">{e(chk.side)}</b> {e(f"{chk.volume:g} {chk.unit}")} '
            f'{e(chk.symbol)} at market, now {e(fmt(chk.price))}</div>'
            f'<div class="cg"><div><span class="k">Stop-loss</span>{e(fmt(chk.stop))}'
            f'<span class="a neg">-{e(money(chk.planned_loss, ccy))}</span></div>{targets}</div>'
            f'<div class="psub">Confirm within {seconds} seconds. MT5 may fill slightly away from this price. '
            f'{e(how)}</div></div>')


def levels_html(side: str, entry, stop, target, now=None, digits: int = 2, moved: Optional[dict] = None) -> str:
    """Buy/Sell price, stop-loss, target (and the live price) in one compact line.
    `moved` = {"stop": old, "target": old} marks a level that was changed after the trade opened."""
    moved = moved or {}

    def cell(label, value, cls="", old=None):
        was = f'<i>was {e(px(old, digits))}</i>' if old is not None else ""
        return (f'<span class="lv {cls}"><span class="k">{e(label)}</span>'
                f'{e(px(value, digits)) if value else "none"}{was}</span>')

    parts = [cell("Buy at" if side == "BUY" else "Sell at", entry),
             cell("SL", stop, "sl", moved.get("stop")), cell("TP", target, "tp", moved.get("target"))]
    if now is not None:
        parts.append(cell("Now", now, "now"))
    return '<div class="lvs">' + "".join(parts) + "</div>"


def position_row(p: dict, ccy: str, opened: str, close_by: str = "", overdue: bool = False, digits: int = 2,
                 moved: Optional[dict] = None, strategy: str = "") -> str:
    """One open desk trade: live profit, buy/sell price, stop-loss, target and the live price, read from MT5."""
    side = p["side"].lower()
    pl = p["profit"]
    vol = f"{p['volume']:g} lots"
    detail = f"Opened {opened}." + (f" Time limit {close_by}." if close_by else "")
    late = '<div class="late">Time limit passed: close this trade (the tested rule).</div>' if overdue else ""
    return (f'<div class="posr"><div><span class="sym">{e(p["symbol"])}</span><span class="s {side}">{e(p["side"])}</span>'
            f'<span class="vol">{e(vol)}</span>'
            + (f'<span class="lane">{e(strategy)}</span>' if strategy else "") + '</div>'
            f'<div class="pl {"pos" if pl > 0 else "neg" if pl < 0 else ""}">{"+" if pl > 0 else ""}{e(money(pl, ccy))}</div>'
            f'<div class="l2">{levels_html(p["side"], p["open_price"], p["stop"], p["target"], p["price"], digits, moved)}'
            f'{e(detail)}</div>{late}</div>')


def recent_table(rows: List[dict]) -> str:
    """Recent signals, newest first, each with its buy/sell price, stop-loss and target.
    A still-running one also shows the live price, and its R updates with it."""
    out = []
    for r in rows:
        side = r["Signal"].lower()
        res = r["R"]
        running = r["Outcome"].startswith("Running")
        day = f'<small>{e(r["Day"])}</small>' if r.get("Day") else ""
        lv = levels_html(r["Signal"], r.get("entry"), r.get("stop"), r.get("target"),
                         r.get("now") if running else None, r.get("digits", 2)) if r.get("entry") else ""
        lane = f'<span class="lane">{e(r["Lane"])}</span>' if r.get("Lane") else ""
        watch = '<span class="lane watch">Watch only</span>' if r.get("watch") else ""
        out.append(f'<div class="rr{" run" if running else ""}"><div class="t">{e(r["When"])}{day}</div>'
                   f'<div class="mid"><span class="sym">{e(r["Symbol"])}</span> <span class="s {side}">{e(r["Signal"])}</span> '
                   f'<span class="o">{e(r["Outcome"])}</span>{lane}{watch}{lv}</div>'
                   f'<div class="r {r_tone(res)}">{e(r_label(res))}</div></div>')
    return f'<div class="rec2">{"".join(out)}</div>'


def r_tone(r) -> str:
    return "" if r is None else "pos" if r > 0 else "neg" if r < 0 else ""


def r_label(r) -> str:
    """+0.08R; a dash when the result is not known."""
    return "-" if r is None else f"{r:+.2f}R"


def latest_html(rows: List[dict], live_until: str = "") -> str:
    """Latest signals: the newest one large, the others of today below it. live_until: when the newest one
    stops being valid (empty once its candle has passed)."""
    if not rows:
        return ""
    r = rows[0]
    side = r["Signal"].lower()
    running = r["Outcome"].startswith("Running")
    tags = (f'<span class="lane">{e(r["Lane"])}</span>' if r.get("Lane") else "") + \
           ('<span class="lane watch">Watch only</span>' if r.get("watch") else "")
    when = e(r["When"]) + (f' <small>{e(r["Day"])}</small>' if r.get("Day") else "")
    live = (f'<div class="live">Live now: valid until {e(live_until)}. The trade box at the top has it.</div>'
            if live_until else "")
    card = (f'<div class="lsig {side}"><div class="lt"><span class="when">{when}</span>{tags}</div>'
            f'<div class="lw"><span class="w {side}">{e(r["Signal"])}</span><span class="sym">{e(r["Symbol"])}</span></div>'
            + levels_html(r["Signal"], r.get("entry"), r.get("stop"), r.get("target"),
                          r.get("now") if running else None, r.get("digits", 2))
            + f'<div class="lb"><span class="o{" run" if running else ""}">{e(r["Outcome"])}</span>'
              f'<span class="r {r_tone(r["R"])}">{e(r_label(r["R"]))}</span></div>{live}</div>')
    rest = ""
    if len(rows) > 1:
        rest = '<div class="ltail">Earlier today</div>' + recent_table(rows[1:])
    return card + rest


# ------------------------------------------------------------------ results by day
def day_counts(rows: List[dict]) -> dict:
    """Won = closed in profit, lost = closed at a loss (or at zero), running = not closed yet.
    The result adds up the closed ones only."""
    closed = [r for r in rows if r["Outcome"] != "Running"]
    won = [r for r in closed if r["R"] > 0]
    return {"n": len(rows), "won": len(won), "lost": len(closed) - len(won), "running": len(rows) - len(closed),
            "r": sum(r["R"] for r in closed), "pnl": sum(r.get("pnl", 0.0) for r in closed)}


def day_summary(rows: List[dict], ccy: str) -> str:
    c = day_counts(rows)
    closed = c["won"] + c["lost"]
    rate = f"{c['won']} of {closed} closed" if closed else "none closed yet"
    tone = "pos" if c["r"] > 0 else "neg" if c["r"] < 0 else ""
    money_line = f'{"+" if c["pnl"] > 0 else ""}{money(c["pnl"], ccy)}' if closed else ""
    cells = [("Signals", str(c["n"]), "", ""), ("Won", str(c["won"]), "pos" if c["won"] else "", rate),
             ("Lost", str(c["lost"]), "neg" if c["lost"] else "", ""),
             ("Running", str(c["running"]), "", "not counted yet" if c["running"] else ""),
             ("Result", f'{c["r"]:+.2f}R' if closed else "-", tone, money_line)]
    return '<div class="dsum">' + "".join(
        f'<div><span class="k">{e(k)}</span><span class="v {t}">{e(v)}</span>'
        + (f"<small>{e(s)}</small>" if s else "") + "</div>" for k, v, t, s in cells) + "</div>"


def days_table(days: List[dict]) -> str:
    """One line per day: a square per signal (green won, red lost, outlined running), the counts and the result.
    Each item: label, small, rows, picked, closed_market."""
    head = ('<div class="dr dh"><span>Day</span><span class="dots">Signals in order</span><span class="n">Signals</span>'
            '<span class="n">Won</span><span class="n">Lost</span><span class="n">Result</span></div>')
    out, total = [head], []
    for d in days:
        rows = sorted(d["rows"], key=lambda r: r["ts"])
        total += rows
        c = day_counts(rows)
        dots = "".join('<i class="run"></i>' if r["Outcome"] == "Running" else
                       f'<i class="{"w" if r["R"] > 0 else "l"}"></i>' for r in rows)
        closed = c["won"] + c["lost"]
        res = f'{c["r"]:+.2f}R' if closed else ("market closed" if d.get("closed_market") and not rows else "-")
        tone = "pos" if closed and c["r"] > 0 else "neg" if closed and c["r"] < 0 else ""
        cls = " pick" if d.get("picked") else " off" if not rows else ""
        out.append(f'<div class="dr{cls}"><span class="d">{e(d["label"])}<small>{e(d.get("small", ""))}</small></span>'
                   f'<span class="dots">{dots}</span><span class="n">{c["n"]}</span><span class="n">{c["won"]}</span>'
                   f'<span class="n">{c["lost"]}</span><span class="n {tone}">{e(res)}</span></div>')
    t = day_counts(total)
    closed = t["won"] + t["lost"]
    tone = "pos" if t["r"] > 0 else "neg" if t["r"] < 0 else ""
    res = f'{t["r"]:+.2f}R' if closed else "-"
    out.append(f'<div class="dr tot"><span class="d">{len(days)} days</span><span class="dots"></span>'
               f'<span class="n">{t["n"]}</span><span class="n">{t["won"]}</span><span class="n">{t["lost"]}</span>'
               f'<span class="n {tone}">{e(res)}</span></div>')
    return f'<div class="dtab">{"".join(out)}</div>'


def checks_list(sig) -> str:
    items = []
    for c in sig.checks:
        mark = '<span class="ok">✓</span>' if c.passed else '<span class="no">✕</span>'
        items.append(f'<div class="c"><div class="tf">{e(c.timeframe)}</div>{mark}'
                     f'<div><div class="role">{e(c.purpose)}</div><div class="d">{e(c.detail)}</div></div></div>')
    return f'<div class="checks">{"".join(items)}</div>'


def reasons_list(reasons: List[str]) -> str:
    return '<ul class="reasons">' + "".join(f"<li>{e(r)}</li>" for r in reasons[:5]) + "</ul>"
