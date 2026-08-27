"""Generate a standalone HTML dashboard from the validated SimPy model.

This module does not alter simulation logic. It runs the same CURRENT and PROPOSED
configurations used in Phase 4 and renders summary metrics plus a compact activity
view from the model's recorded events.

Run:
    python3 -m sim.visualize

Output:
    shellroom_dashboard.html
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from sim.shellroom_sim import Config, STAGE_NAMES, run

OUT = Path("shellroom_dashboard.html")
SIM_DAYS = 120
WARMUP_DAYS = 30


def scenario(name: str, *, batch_size: int, robots: int, final_dry_enabled: bool):
    cfg = Config(
        batch_size=batch_size,
        robots=robots,
        robot_uptime=0.85,
        prime_cycle=7.0,
        backup_cycle=6.0,
        seal_cycle=4.0,
        dry_hours=[4.0] * 7,
        seal_dry_hours=12.0,
        final_dry_enabled=final_dry_enabled,
        sim_days=SIM_DAYS,
        warmup_days=WARMUP_DAYS,
    )
    return name, run(cfg)


def pct(v: float) -> str:
    return f"{100.0 * v:.0f}%"


def metric_card(title: str, value: str, note: str = "") -> str:
    return f'''<div class="card"><div class="label">{escape(title)}</div>
    <div class="value">{escape(value)}</div><div class="note">{escape(note)}</div></div>'''


def bar(label: str, value: float, maximum: float, suffix: str = "") -> str:
    width = 0.0 if maximum <= 0 else min(100.0, 100.0 * value / maximum)
    return f'''<div class="bar-row"><div class="bar-label">{escape(label)}</div>
    <div class="bar-track"><div class="bar-fill" style="width:{width:.1f}%"></div></div>
    <div class="bar-value">{value:.1f}{escape(suffix)}</div></div>'''


def utilization_rows(result) -> str:
    rows = []
    for name, value in result.robot_utilization.items():
        rows.append(bar(name, value * 100.0, 100.0, "%"))
    return "".join(rows) or "<div class='muted'>No robot utilization data</div>"


def saturation_rows(result) -> str:
    labels = {"prime": "Prime", "backup": "Backup", "final": "Seal / Final Dry"}
    rows = []
    for key, value in result.conveyor_saturation.items():
        if key == "final" and not result.cfg.final_dry_enabled:
            continue
        rows.append(bar(labels.get(key, key), value * 100.0, 100.0, "%"))
    return "".join(rows)


def timeline_svg(result, hours: int = 72) -> str:
    start = result.cfg.warmup_cutoff
    end = start + hours * 60.0
    events = [e for e in result.stats.dip_events if e[3] >= start and e[2] <= end]
    if not events:
        return "<div class='muted'>No dip events in visualization window.</div>"

    width, left, top, row_h = 1120, 115, 30, 28
    chart_w = width - left - 20
    height = top + len(STAGE_NAMES) * row_h + 36
    parts = [f'<svg viewBox="0 0 {width} {height}" class="timeline" role="img">']

    for h in range(0, hours + 1, 12):
        x = left + chart_w * h / hours
        parts.append(f'<line x1="{x:.1f}" y1="20" x2="{x:.1f}" y2="{height-20}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="16" class="tick" text-anchor="middle">+{h}h</text>')

    for stage, label in enumerate(STAGE_NAMES):
        y = top + stage * row_h
        parts.append(f'<text x="8" y="{y+17}" class="stage">{escape(label)}</text>')
        parts.append(f'<line x1="{left}" y1="{y+row_h}" x2="{width-20}" y2="{y+row_h}" class="lane"/>')

    for lot_id, stage, t0, t1, robot in events:
        x0 = left + chart_w * max(0.0, t0 - start) / (hours * 60.0)
        x1 = left + chart_w * min(hours * 60.0, t1 - start) / (hours * 60.0)
        w = max(1.5, x1 - x0)
        y = top + stage * row_h + 5
        title = escape(f"Lot {lot_id}, {STAGE_NAMES[stage]}, {robot}, {(t1-t0):.1f} min")
        parts.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{w:.1f}" height="18" rx="3" class="event"><title>{title}</title></rect>')

    parts.append("</svg>")
    return "".join(parts)


def scenario_panel(name: str, result) -> str:
    cfg = result.cfg
    capacities = f"Prime {cfg.batch_size} / Backup {cfg.backup_capacity}"
    if cfg.final_dry_enabled:
        capacities += f" / Seal {cfg.final_capacity}"
    else:
        capacities += " / no dedicated Seal conveyor"

    return f'''<section class="scenario">
      <div class="scenario-head"><div><h2>{escape(name)}</h2><div class="muted">{escape(capacities)}</div></div>
      <div class="pill">{cfg.robots} robot{'s' if cfg.robots != 1 else ''}</div></div>
      <div class="cards">
        {metric_card("Steady throughput", f"{result.steady_hangers_per_day:.1f} hangers/day", f"{result.steady_lots_per_day:.2f} lots/day")}
        {metric_card("Bottleneck", result.bottleneck)}
        {metric_card("Completed", f"{result.total_unloaded:,} hangers", f"{result.total_lots_completed} lots in full run")}
      </div>
      <div class="two-col">
        <div><h3>Robot utilization</h3>{utilization_rows(result)}</div>
        <div><h3>Conveyor saturation</h3>{saturation_rows(result)}</div>
      </div>
      <h3>72-hour activity window after warm-up</h3>
      {timeline_svg(result)}
    </section>'''


def render(current, proposed) -> str:
    cur = current[1]
    pro = proposed[1]
    gain = 100.0 * (pro.steady_hangers_per_day / cur.steady_hangers_per_day - 1.0)
    max_tp = max(cur.steady_hangers_per_day, pro.steady_hangers_per_day)

    comparison = (
        bar("CURRENT", cur.steady_hangers_per_day, max_tp, "/day")
        + bar("PROPOSED", pro.steady_hangers_per_day, max_tp, "/day")
    )

    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Shellroom Simulation Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17212b;--muted:#687482;--line:#dce2e8;--accent:#1f6feb;--soft:#e8f1ff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Arial,sans-serif}}
main{{max-width:1240px;margin:auto;padding:28px}} h1{{margin:0 0 6px;font-size:30px}} h2{{margin:0;font-size:22px}} h3{{margin:22px 0 10px;font-size:14px;text-transform:uppercase;letter-spacing:.05em}}
.hero,.scenario{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.04)}}
.hero-top,.scenario-head{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}} .muted,.note{{color:var(--muted)}}
.gain{{font-size:34px;font-weight:700}} .pill{{background:var(--soft);padding:6px 10px;border-radius:999px;font-weight:700}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}} .card{{border:1px solid var(--line);border-radius:10px;padding:14px}}
.label{{font-size:12px;color:var(--muted);text-transform:uppercase}} .value{{font-size:23px;font-weight:700;margin-top:5px}} .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}
.bar-row{{display:grid;grid-template-columns:150px 1fr 76px;gap:10px;align-items:center;margin:9px 0}} .bar-track{{height:16px;background:#edf1f5;border-radius:8px;overflow:hidden}} .bar-fill{{height:100%;background:var(--accent)}} .bar-value{{text-align:right;font-variant-numeric:tabular-nums}}
.timeline{{width:100%;border:1px solid var(--line);border-radius:8px;background:#fbfcfd}} .grid{{stroke:#e5e9ee;stroke-width:1}} .lane{{stroke:#eef1f4;stroke-width:1}} .tick,.stage{{font-size:11px;fill:#687482}} .event{{fill:#1f6feb;opacity:.72}}
@media(max-width:760px){{main{{padding:12px}}.cards,.two-col{{grid-template-columns:1fr}}.bar-row{{grid-template-columns:95px 1fr 64px}}}}
</style></head><body><main>
<section class="hero"><div class="hero-top"><div><h1>Shellroom conveyor simulation</h1>
<div class="muted">Phase 5 visualization generated directly from the validated SimPy model.</div></div>
<div><div class="label">PROPOSED vs CURRENT</div><div class="gain">+{gain:.1f}%</div></div></div>
<h3>Steady-state throughput comparison</h3>{comparison}
<div class="note">CURRENT: 1 shared robot, 38/76, no dedicated Seal conveyor. PROPOSED: 2 robots, 21/42/42.</div></section>
{scenario_panel(current[0], cur)}
{scenario_panel(proposed[0], pro)}
</main></body></html>'''


def main():
    current = scenario("CURRENT", batch_size=38, robots=1, final_dry_enabled=False)
    proposed = scenario("PROPOSED", batch_size=21, robots=2, final_dry_enabled=True)
    OUT.write_text(render(current, proposed), encoding="utf-8")
    print(f"Wrote {OUT.resolve()}")
    print(f"CURRENT: {current[1].steady_hangers_per_day:.1f} hangers/day")
    print(f"PROPOSED: {proposed[1].steady_hangers_per_day:.1f} hangers/day")


if __name__ == "__main__":
    main()
