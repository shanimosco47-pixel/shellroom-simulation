"""
Shellroom conveyor simulation, rebuilt in SimPy from the hand-rolled JS event loop in
`21/38 Hangers Simulation` (uploaded HTML). See PHASE1_NOTES.md for the full rule
extraction and every discrepancy this rebuild resolves against Shani's authoritative doc.

All parameters live in `Config`, at the top of this file - nothing is hardcoded deeper
in the logic. No visualization here: plain text/log output only (Phase 1-4 constraint).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import simpy

# ---------------------------------------------------------------------------
# Config - every adjustable parameter, in one place.
# ---------------------------------------------------------------------------

N_STAGES = 8          # coat 1, coat 2, coat 3, coat 4, coat 5, coat 6, coat 7, SEAL
PRIME_STAGES = (0, 1)  # coat 1, coat 2
SEAL_STAGE = 7         # coat 8 / SEAL
STAGE_NAMES = ["coat 1", "coat 2", "coat 3", "coat 4", "coat 5", "coat 6", "coat 7", "SEAL"]


@dataclass
class Config:
    batch_size: int = 21          # Prime conveyor capacity (hangers per lot)
    robots: int = 2               # 1 (shared) or 2 (Prime-dedicated + Backup/Seal-dedicated)
    robot_uptime: float = 0.85    # effective_duration = cycle_time / uptime

    prime_cycle: float = 7.0      # minutes/hanger, coat 1 & 2
    backup_cycle: float = 6.0     # minutes/hanger, coat 3-7
    seal_cycle: float = 4.0       # minutes/hanger, SEAL

    # Dry time after each coat, hours. Index 0..6 = coat 1..coat 7.
    # Doc only specifies defaults for coats 1-6 (4h each); coat 7's default is not
    # stated anywhere in the source doc (a real gap, not my invention) - extended here
    # to 4h, following the same pattern as every other non-final coat. Flagged in
    # PHASE1_NOTES.md.
    dry_hours: list = field(default_factory=lambda: [4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0])
    seal_dry_hours: float = 12.0  # authoritative per Shani, overriding the HTML's 24h default

    final_dry_enabled: bool = True   # dedicated Seal conveyor (doc's "PROPOSED"); off = SEAL dries in place on Backup ("CURRENT")

    sim_days: int = 60
    warmup_days: int = 15

    @property
    def backup_capacity(self) -> int:
        return self.batch_size * 2

    @property
    def final_capacity(self) -> int:
        return self.batch_size * 2 if self.final_dry_enabled else 0

    @property
    def horizon(self) -> float:
        return self.sim_days * 1440.0

    @property
    def warmup_cutoff(self) -> float:
        return self.warmup_days * 1440.0


def cycle_time(stage: int, cfg: Config) -> float:
    if stage in PRIME_STAGES:
        return cfg.prime_cycle
    if stage == SEAL_STAGE:
        return cfg.seal_cycle
    return cfg.backup_cycle


def effective_duration(stage: int, cfg: Config) -> float:
    return cycle_time(stage, cfg) / cfg.robot_uptime


def dry_time_minutes(stage: int, cfg: Config) -> float:
    if stage < SEAL_STAGE:
        return cfg.dry_hours[stage] * 60.0
    return cfg.seal_dry_hours * 60.0


# ---------------------------------------------------------------------------
# Robot dispatcher - a custom priority queue (SimPy's PriorityResource fixes a
# request's priority at request time, which can't express "Prime always wins even
# if it was the last to become ready"; this dispatcher re-evaluates the full
# waiting queue every time the robot frees up, which is what Rule A needs.)
#
# Rule A: Prime coat1/2 always outranks Backup/Seal at the next decision point
# (never mid-dip). Encoded as: filter to Prime jobs first if any exist.
#
# Same-family "lot batching": once the robot starts a lot at a stage, it prefers
# staying on that exact (family, lot, stage) until no more hangers are waiting for
# it - it does not hop between two different Backup lots hanger-by-hanger. This
# preference dissolves naturally (no manual clearing needed): once no waiting job
# matches the lock, `_pick_next` falls through to the full pool.
#
# Highest-stage / longest-waiting tiebreak matches the source tool's sort order.
# ---------------------------------------------------------------------------


class _Job:
    __slots__ = ("hanger", "stage", "family", "lot", "event", "ready_time")

    def __init__(self, hanger, stage, family, lot, event, ready_time):
        self.hanger = hanger
        self.stage = stage
        self.family = family
        self.lot = lot
        self.event = event
        self.ready_time = ready_time


class Robot:
    def __init__(self, env: simpy.Environment, name: str, stats: "Stats"):
        self.env = env
        self.name = name
        self.stats = stats
        self.busy = False
        self.waiting: list[_Job] = []
        self.current_lock: Optional[tuple] = None

    def request(self, hanger: "Hanger", stage: int, family: str):
        ev = self.env.event()
        job = _Job(hanger, stage, family, hanger.lot_id, ev, self.env.now)
        self.waiting.append(job)
        self._dispatch()
        return ev

    def release(self):
        self.busy = False
        self._dispatch()

    def _dispatch(self):
        if self.busy or not self.waiting:
            return
        job = self._pick_next()
        self.waiting.remove(job)
        self.busy = True
        self.current_lock = (job.family, job.lot, job.stage)
        job.event.succeed()

    def _pick_next(self) -> _Job:
        prime_jobs = [j for j in self.waiting if j.family == "prime"]
        pool = prime_jobs if prime_jobs else self.waiting
        if self.current_lock:
            locked = [j for j in pool if (j.family, j.lot, j.stage) == self.current_lock]
            if locked:
                pool = locked
        pool.sort(key=lambda j: (-j.stage, j.ready_time, j.lot, j.hanger.index))
        return pool[0]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class Hanger:
    __slots__ = ("lot_id", "index")

    def __init__(self, lot_id: int, index: int):
        self.lot_id = lot_id
        self.index = index


class Lot:
    def __init__(self, env: simpy.Environment, lot_id: int, batch_size: int):
        self.id = lot_id
        self.batch_size = batch_size
        self.start_time = env.now
        self.backup_reserved = env.event()      # Rule B gate
        self.hanger0_ready_for_seal = env.event()
        self.final_dry_ready = env.event()
        self.seal_dry_done_count = 0
        self.unload_time: Optional[float] = None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self):
        self.dip_events: list[tuple] = []       # (lot_id, stage, start, end, robot_name)
        self.unload_events: list[tuple] = []    # (lot_id, time, count)
        self.lot_starts: list[tuple] = []       # (lot_id, time)
        self.occupancy: dict[str, list[tuple]] = {"prime": [], "backup": [], "final": []}

    def lot_started(self, lot_id, t):
        self.lot_starts.append((lot_id, t))

    def record_dip(self, lot_id, stage, t_start, t_end, robot_name):
        self.dip_events.append((lot_id, stage, t_start, t_end, robot_name))

    def record_unload(self, lot_id, t, count):
        self.unload_events.append((lot_id, t, count))

    def record_level(self, name, t, occupied):
        self.occupancy[name].append((t, occupied))


def _tracked_get(env, container: simpy.Container, amount: int, stats: Stats, name: str):
    yield container.get(amount)
    stats.record_level(name, env.now, container.capacity - container.level)


def _tracked_put(env, container: simpy.Container, amount: int, stats: Stats, name: str):
    yield container.put(amount)
    stats.record_level(name, env.now, container.capacity - container.level)


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


def pick_robot(robots: dict, cfg: Config, family: str) -> Robot:
    if cfg.robots == 1:
        return robots["shared"]
    return robots["prime"] if family == "prime" else robots["backup"]


def reserve_backup(env, backup_cap, lot: Lot, stats: Stats):
    """Rule B: reserve a full batch of Backup capacity before coat 1 may start."""
    yield from _tracked_get(env, backup_cap, lot.batch_size, stats, "backup")
    lot.backup_reserved.succeed()


def reserve_final_dry(env, final_cap, lot: Lot, cfg: Config, stats: Stats):
    """Analogous reservation for the Seal/Final-Dry conveyor, applying Rule B's
    principle to the Backup->Seal transition: don't let a hanger finish its SEAL
    dip with nowhere to dry. Only meaningful when a dedicated Final Dry conveyor
    exists; otherwise SEAL just stays on Backup and there is nothing to reserve."""
    if not cfg.final_dry_enabled:
        lot.final_dry_ready.succeed()
        return
    yield lot.hanger0_ready_for_seal
    yield from _tracked_get(env, final_cap, lot.batch_size, stats, "final")
    lot.final_dry_ready.succeed()


def hanger_process(env, hanger: Hanger, lot: Lot, cfg: Config, robots: dict,
                    prime_cap, backup_cap, final_cap, stats: Stats):
    yield lot.backup_reserved  # Rule B gate: nobody starts coat 1 until Backup room is guaranteed

    for stage in range(N_STAGES):
        family = "prime" if stage in PRIME_STAGES else "backup"
        robot = pick_robot(robots, cfg, family)

        if stage == 2:
            # Hanger physically leaves Prime the moment it's ready for coat 3 (right
            # after coat 2's dry) and occupies its already-reserved Backup slot.
            yield from _tracked_put(env, prime_cap, 1, stats, "prime")

        if stage == SEAL_STAGE:
            if hanger.index == 0 and not lot.hanger0_ready_for_seal.triggered:
                lot.hanger0_ready_for_seal.succeed()
            yield lot.final_dry_ready

        req = robot.request(hanger, stage, family)
        yield req
        t_start = env.now
        yield env.timeout(effective_duration(stage, cfg))
        robot.release()
        stats.record_dip(lot.id, stage, t_start, env.now, robot.name)

        if stage == SEAL_STAGE and cfg.final_dry_enabled:
            # This hanger's SEAL dip is done: it has physically moved off Backup
            # onto its pre-reserved Final Dry slot.
            yield from _tracked_put(env, backup_cap, 1, stats, "backup")

        yield env.timeout(dry_time_minutes(stage, cfg))

        if stage == SEAL_STAGE:
            lot.seal_dry_done_count += 1
            if lot.seal_dry_done_count == lot.batch_size:
                lot.unload_time = env.now
                stats.record_unload(lot.id, env.now, lot.batch_size)
                if cfg.final_dry_enabled:
                    yield from _tracked_put(env, final_cap, lot.batch_size, stats, "final")
                else:
                    yield from _tracked_put(env, backup_cap, lot.batch_size, stats, "backup")


def lot_generator(env, cfg: Config, prime_cap, backup_cap, final_cap, robots: dict, stats: Stats):
    lot_id = 0
    while True:
        yield from _tracked_get(env, prime_cap, cfg.batch_size, stats, "prime")
        lot_id += 1
        lot = Lot(env, lot_id, cfg.batch_size)
        stats.lot_started(lot_id, env.now)
        env.process(reserve_backup(env, backup_cap, lot, stats))
        env.process(reserve_final_dry(env, final_cap, lot, cfg, stats))
        for i in range(cfg.batch_size):
            h = Hanger(lot_id, i)
            env.process(hanger_process(env, h, lot, cfg, robots, prime_cap, backup_cap, final_cap, stats))


# ---------------------------------------------------------------------------
# Results / analysis
# ---------------------------------------------------------------------------


@dataclass
class Results:
    cfg: Config
    stats: Stats
    steady_hangers_per_day: float
    steady_lots_per_day: float
    total_unloaded: int
    total_lots_completed: int
    lots_in_flight_at_horizon: int
    bottleneck: str
    robot_utilization: dict
    conveyor_saturation: dict

    def one_line(self, label: str) -> str:
        return (f"{label}: {self.steady_hangers_per_day:.1f} hangers/day "
                f"({self.steady_lots_per_day:.2f} lots/day) | bottleneck: {self.bottleneck}")

    def report(self) -> str:
        cfg = self.cfg
        lines = [
            f"batch={cfg.batch_size} robots={cfg.robots} uptime={cfg.robot_uptime:.0%} "
            f"prime_cycle={cfg.prime_cycle} backup_cycle={cfg.backup_cycle} seal_cycle={cfg.seal_cycle} "
            f"seal_dry={cfg.seal_dry_hours}h final_dry={'on' if cfg.final_dry_enabled else 'off'} "
            f"sim_days={cfg.sim_days} warmup_days={cfg.warmup_days}",
            f"Steady-state throughput: {self.steady_hangers_per_day:.1f} hangers/day, "
            f"{self.steady_lots_per_day:.2f} lots/day",
            f"Total unloaded (whole run): {self.total_unloaded} hangers in {self.total_lots_completed} lots "
            f"({self.lots_in_flight_at_horizon} lots still in flight at horizon)",
            f"Bottleneck: {self.bottleneck}",
            "Robot utilization (post-warmup): " + ", ".join(
                f"{k}={v:.1%}" for k, v in self.robot_utilization.items()),
            "Conveyor saturation (post-warmup, time-avg %% full): " + ", ".join(
                f"{k}={v:.1%}" for k, v in self.conveyor_saturation.items()),
        ]
        return "\n".join(lines)


def _time_weighted_avg(samples: list[tuple], capacity: int, t_from: float, t_to: float) -> float:
    """samples: list of (time, occupied_count), assumed already sorted by time.
    Returns time-weighted average occupied/capacity over [t_from, t_to)."""
    if capacity <= 0 or t_to <= t_from:
        return 0.0
    samples = sorted(samples, key=lambda s: s[0])
    # value just before t_from
    level = 0
    idx = 0
    for i, (t, occ) in enumerate(samples):
        if t <= t_from:
            level = occ
            idx = i + 1
        else:
            break
    area = 0.0
    cursor = t_from
    for t, occ in samples[idx:]:
        if t >= t_to:
            break
        if t > cursor:
            area += level * (t - cursor)
            cursor = t
        level = occ
    if cursor < t_to:
        area += level * (t_to - cursor)
    return area / (capacity * (t_to - t_from))


def analyze(cfg: Config, stats: Stats) -> Results:
    t_from, t_to = cfg.warmup_cutoff, cfg.horizon
    window_days = (t_to - t_from) / 1440.0

    steady_unloads = [(lot, t, n) for (lot, t, n) in stats.unload_events if t_from <= t < t_to]
    steady_hangers = sum(n for _, _, n in steady_unloads)
    steady_lots = len(steady_unloads)

    total_unloaded = sum(n for _, _, n in stats.unload_events)
    total_lots_completed = len(stats.unload_events)
    lots_in_flight = len(stats.lot_starts) - total_lots_completed

    # Robot utilization: busy time / window, per robot name.
    busy_by_robot: dict[str, float] = {}
    for (lot_id, stage, t_start, t_end, robot_name) in stats.dip_events:
        s = max(t_start, t_from)
        e = min(t_end, t_to)
        if e > s:
            busy_by_robot[robot_name] = busy_by_robot.get(robot_name, 0.0) + (e - s)
    robot_utilization = {k: v / (t_to - t_from) for k, v in busy_by_robot.items()}

    conveyor_saturation = {
        "prime": _time_weighted_avg(stats.occupancy["prime"], cfg.batch_size, t_from, t_to),
        "backup": _time_weighted_avg(stats.occupancy["backup"], cfg.backup_capacity, t_from, t_to),
    }
    if cfg.final_dry_enabled:
        conveyor_saturation["final"] = _time_weighted_avg(
            stats.occupancy["final"], cfg.final_capacity, t_from, t_to)

    # Bottleneck: highest-utilized robot if any robot is near-saturated; otherwise
    # the most-saturated conveyor (implies the pipeline is drying-time-bound there).
    bottleneck = "undetermined (window too short)"
    if robot_utilization:
        busiest_robot, busiest_val = max(robot_utilization.items(), key=lambda kv: kv[1])
        busiest_conv, busiest_conv_val = (
            max(conveyor_saturation.items(), key=lambda kv: kv[1]) if conveyor_saturation else (None, 0.0)
        )
        if busiest_val >= 0.90:
            bottleneck = f"robot '{busiest_robot}' ({busiest_val:.0%} utilized)"
        elif busiest_conv_val >= 0.90:
            tied = [name for name, val in conveyor_saturation.items()
                    if val >= 0.90 and (busiest_conv_val - val) <= 0.05]
            if len(tied) > 1:
                joined = " & ".join(f"{name} ({conveyor_saturation[name]:.0%})" for name in tied)
                bottleneck = (f"{joined} conveyors jointly saturated -> capacity/drying bound "
                               f"(Rule B couples them: a full Backup blocks new Prime lots from starting)")
            else:
                bottleneck = f"{busiest_conv} conveyor saturated ({busiest_conv_val:.0%} full) -> drying time bound"
        else:
            bottleneck = (f"none saturated - robot '{busiest_robot}' {busiest_val:.0%}, "
                           f"{busiest_conv} conveyor {busiest_conv_val:.0%}")

    return Results(
        cfg=cfg,
        stats=stats,
        steady_hangers_per_day=steady_hangers / window_days if window_days > 0 else 0.0,
        steady_lots_per_day=steady_lots / window_days if window_days > 0 else 0.0,
        total_unloaded=total_unloaded,
        total_lots_completed=total_lots_completed,
        lots_in_flight_at_horizon=lots_in_flight,
        bottleneck=bottleneck,
        robot_utilization=robot_utilization,
        conveyor_saturation=conveyor_saturation,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(cfg: Config) -> Results:
    env = simpy.Environment()
    stats = Stats()
    prime_cap = simpy.Container(env, capacity=cfg.batch_size, init=cfg.batch_size)
    backup_cap = simpy.Container(env, capacity=cfg.backup_capacity, init=cfg.backup_capacity)
    final_cap = simpy.Container(env, capacity=max(cfg.final_capacity, 1), init=max(cfg.final_capacity, 1))

    if cfg.robots == 1:
        robots = {"shared": Robot(env, "shared", stats)}
    else:
        robots = {"prime": Robot(env, "prime", stats), "backup": Robot(env, "backup", stats)}

    env.process(lot_generator(env, cfg, prime_cap, backup_cap, final_cap, robots, stats))
    env.run(until=cfg.horizon)

    return analyze(cfg, stats)


if __name__ == "__main__":
    default_cfg = Config()
    result = run(default_cfg)
    print(result.report())
