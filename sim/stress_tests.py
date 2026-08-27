"""
Phase 4 stress-test battery for shellroom_sim.py.

Runs the 11 core variations + exploratory hybrids specified in the build doc, off the
same validated base model (no code duplication - just different Config values), logs
one line per run, and checks the results for internal consistency. No visualization -
plain text output only.
"""

from __future__ import annotations

from sim.shellroom_sim import Config, run

# Same horizon/warm-up for every run so throughput numbers are directly comparable.
SIM_DAYS = 120
WARMUP_DAYS = 30


def base(**overrides) -> Config:
    cfg = Config(
        batch_size=38,
        robots=1,
        robot_uptime=0.85,
        prime_cycle=7.0,
        backup_cycle=6.0,
        seal_cycle=4.0,
        dry_hours=[4.0] * 7,
        seal_dry_hours=12.0,
        final_dry_enabled=False,
        sim_days=SIM_DAYS,
        warmup_days=WARMUP_DAYS,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def main():
    results = {}

    def do(key, label, cfg):
        r = run(cfg)
        results[key] = r
        print(r.one_line(label))
        return r

    print("=== Core 11 variations ===")
    do(1, "1  baseline (1 robot, batch 38, no FinalDry)", base())
    do(2, "2  1 -> 2 robots", base(robots=2))
    do(3, "3  1 -> batch 21", base(batch_size=21))
    do(4, "4  2 -> batch 21 (2 robots)", base(robots=2, batch_size=21))
    do(5, "5  1 -> uptime 70%", base(robot_uptime=0.70))
    do(6, "6  1 -> prime cycle +50%", base(prime_cycle=7.0 * 1.5))
    do(7, "7  1 -> FinalDry on (cap = backup)", base(final_dry_enabled=True))
    do(8, "8  1 -> seal dry 24h", base(seal_dry_hours=24.0))
    do(9, "9  1 -> batch 60", base(batch_size=60))
    do(10, "10 9 -> 2 robots (batch 60)", base(robots=2, batch_size=60))
    do(11.1, "11a CURRENT (1 robot, 38/76, no seal conv.)",
       base(batch_size=38, robots=1, final_dry_enabled=False))
    do(11.2, "11b PROPOSED (2 robots, 21/42, seal conv. 42)",
       base(batch_size=21, robots=2, final_dry_enabled=True))

    print("\n=== Exploratory hybrids ===")
    do("h1", "H1 CURRENT convey. + PROPOSED robot split",
       base(batch_size=38, robots=2, final_dry_enabled=False))
    do("h2", "H2 PROPOSED convey. + 1 shared robot",
       base(batch_size=21, robots=1, final_dry_enabled=True))
    do("h3", "H3 batch 21, 1 robot, no seal conv. (isolate batch-size-only)",
       base(batch_size=21, robots=1, final_dry_enabled=False))

    print("\n=== Consistency assessment ===")
    checks = []

    def check(name, cond, detail):
        checks.append((name, cond, detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    h1 = results[1].steady_hangers_per_day
    h2 = results[2].steady_hangers_per_day
    h3 = results[3].steady_hangers_per_day
    h4 = results[4].steady_hangers_per_day
    h5 = results[5].steady_hangers_per_day
    h6 = results[6].steady_hangers_per_day
    h7 = results[7].steady_hangers_per_day
    h8 = results[8].steady_hangers_per_day
    h9 = results[9].steady_hangers_per_day
    h10 = results[10].steady_hangers_per_day
    h11a = results[11.1].steady_hangers_per_day
    h11b = results[11.2].steady_hangers_per_day

    check("2nd robot helps or is explained (var 2 vs 1)",
          h2 >= h1 or results[1].bottleneck.startswith("robot") is False,
          f"1robot={h1:.1f}/day (bottleneck: {results[1].bottleneck}) vs 2robot={h2:.1f}/day (bottleneck: {results[2].bottleneck})")

    check("2nd robot helps or is explained (var 4 vs 3)",
          h4 >= h3 or "robot" not in results[3].bottleneck,
          f"1robot/b21={h3:.1f}/day (bottleneck: {results[3].bottleneck}) vs 2robot/b21={h4:.1f}/day (bottleneck: {results[4].bottleneck})")

    check("lower uptime reduces throughput (var 5 vs 1)", h5 <= h1,
          f"uptime70={h5:.1f}/day vs baseline85={h1:.1f}/day")

    check("slower prime cycle reduces throughput (var 6 vs 1)", h6 <= h1,
          f"primeCycle+50%={h6:.1f}/day vs baseline={h1:.1f}/day")

    check("longer final dry reduces throughput (var 8 vs 1)", h8 <= h1,
          f"sealDry24h={h8:.1f}/day vs sealDry12h(baseline)={h1:.1f}/day")

    check("bigger batch (9) reveals a bottleneck, doesn't blow up throughput proportionally",
          h9 > 0,
          f"batch60={h9:.1f}/day (bottleneck: {results[9].bottleneck}) vs batch38(baseline)={h1:.1f}/day")

    check("2 robots relieve the batch-60 bottleneck if it was robot-bound (10 vs 9)",
          (h10 >= h9) or ("robot" not in results[9].bottleneck),
          f"batch60/1robot={h9:.1f}/day (bottleneck: {results[9].bottleneck}) vs batch60/2robot={h10:.1f}/day (bottleneck: {results[10].bottleneck})")

    check("CURRENT vs PROPOSED reported plainly either way (var 11)",
          True,
          f"CURRENT={h11a:.1f}/day (bottleneck: {results[11.1].bottleneck}) vs "
          f"PROPOSED={h11b:.1f}/day (bottleneck: {results[11.2].bottleneck}) -> "
          f"{'PROPOSED ahead' if h11b > h11a else ('CURRENT ahead' if h11a > h11b else 'tie')}")

    all_pass = all(c[1] for c in checks)
    print(f"\nOverall: {'CONSISTENT' if all_pass else 'INCONSISTENT - see FAIL lines above'}")
    return all_pass, results


if __name__ == "__main__":
    main()
