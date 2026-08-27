# Phase 1 — rule extraction & discrepancies (source: `21/38 Hangers Simulation` HTML)

This rebuilds the simulation logic embedded in the uploaded HTML
(`21-38-hangers-overlap-corrected.html`, id `shellroom-two-day-sim`) as SimPy,
per the build doc. This file is the extracted-rules record; refer back to it
instead of re-reading the HTML on future changes.

## What the HTML tool actually does (decoded from its minified JS)

- Prime capacity = `batch` (21 or 38, selector default 21). Backup = `batch*2`.
  "Final Dry" select is 0 or `batch*2` — this **is** the doc's "dedicated Seal
  conveyor," just named differently in the HTML.
- `cycles = [primeCycle, primeCycle, backupCycle, backupCycle, backupCycle,
  backupCycle, sealCycle]` — **7 stages**: coat1, coat2 (Prime) + coat3-6
  (Backup, 4 stages) + SEAL.
- Dry times: `dry[1..6]` default 4h each (coats 1-6), `sealDry` default 24h.
- Shared-robot (1-robot) candidate sort: `b.stage - a.stage` (**descending** —
  prefers higher-numbered/Backup-family stages over Prime).
- A per-(lot, stage) "lock": once the robot starts a lot/stage, `eligible()`
  excludes every other lot/stage — including a different family — until the
  *entire* batch at that stage is done.
- Backup capacity is only checked at the coat2→coat3 transition
  (`hanger.stage===2 && firstInStage && backupHangers-backupCount-backupReserved<batch`),
  never before coat 1.

## Confirmed matches to the doc (carried over as-is)

- Batch/Backup/FinalDry capacity relationships, per-coat dry defaults (4h),
  uptime dividing cycle time, lots moving as a group, auto-starting new lots
  to fill Prime.

## Discrepancies — flagged, not silently resolved

1. **Coat count: 7, not 8.** Doc's authoritative sequence needs coat3-7 (5
   Backup stages) + SEAL = 8 total. Building the 8-stage version. Added a
   `dry_hours[6]` (coat 7) param defaulting to 4h — **the doc itself doesn't
   state a coat-7 dry default** (it only says "coat 1 through 6"), so this is
   an extrapolation of the stated pattern, not a value from either source.
2. **SEAL dry: HTML defaults 24h; doc says 12h is the real rule.** Using 12h
   as the default (matches doc's explicit authority), keeping 24h available
   as stress-test variation 8, per doc's own instruction to double-check
   this rather than assume.
3. **Rule A: the HTML does the opposite of what Shani specified.** Its
   shared-robot sort prefers Backup/SEAL over Prime by default — the exact
   failure mode Rule A exists to prevent. Its lock also blocks Prime from
   interjecting until an entire Backup batch finishes, not just the current
   hanger's dip. Implemented Rule A literally: Prime coat1/2 outranks
   Backup/SEAL at every task boundary (single-hanger granularity, no
   mid-dip preemption), same-family lot-batching continuity preserved.
4. **Rule B doesn't exist in the HTML.** Backup capacity is only checked at
   the coat2→coat3 transition. Traced through whether this matters given
   Prime capacity is always exactly one batch (so only one lot ever occupies
   Prime): it does, for the 2-robot PROPOSED case — without an up-front
   reservation, the Prime-dedicated robot could sink a full coat1+coat2+dry
   cycle (~8h) into a lot that then sits blocked with nowhere to go,
   idling that robot for nothing. Implemented Rule B as a real reservation:
   `backup_cap.get(batch)` before coat 1 starts, held until the lot actually
   consumes it at coat 3. Applied the same principle to the analogous
   Backup→Seal transition (reserve Final Dry capacity before the first
   hanger's SEAL dip) since it's the identical stranding risk, one stage over.
5. **Prime-slot release timing.** HTML frees a Prime slot when a hanger's
   coat-3 *dip* completes. This model frees it when the hanger becomes ready
   for coat 3 (right after coat 2's *dry* completes) — matches the doc's own
   wording ("after coat 2, hanger moves from Prime to Backup") more directly.
   Minor timing difference (~one backup cycle time), noted for completeness.
6. **No randomness anywhere** in the extracted rules (cycle times, dry times,
   uptime are all fixed inputs) — confirmed via the `simpy` skill's
   methodology guidance that independent-replication statistics don't apply
   here; only warm-up deletion does. Phase 4 uses a single deterministic run
   per configuration, discarding the initial `warmup_days`.

## Open questions the doc asks to confirm with Shani (not blocking this build)

- Real coat count: 8 vs the HTML's 6+seal.
- Real final dry time: 12h vs the HTML's 24h.
- Real robot count on the production line today (doc assumes 1, matching
  Scenario CURRENT).
