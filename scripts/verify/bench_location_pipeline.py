"""Measure the real cost of the real-time position pipeline.

Why this exists
---------------
"Will live tracking scale?" is usually answered with a guess. The guess for this
codebase would be "CPU, because of the polygon test" — and it would be wrong.
This script measures each stage of one GPS tick separately so the answer comes
from numbers, and so a future change can be re-measured instead of re-argued.

What one tick actually costs (from `app/api/ws.py::handle_push`):

  1. JSON parse + float coercion          — pure CPU
  2. `is_in_hong_kong(lat, lng)`          — pure CPU, point-in-polygon
  3. a DB session: SELECT profile, UPDATE current_location, COMMIT
  4. a Redis PUBLISH to the order channel — one round trip, plus server-side
                                            fan-out to every subscriber

Stages 1-3 need a live DB; stage 4 needs Redis. Each is skipped with a note if
its dependency is unreachable, so the script is still useful on a bare checkout.

Run:
    ./.venv/Scripts/python.exe scripts/verify/bench_location_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time

# A downtown Hong Kong coordinate — inside the service-area polygon.
LAT, LNG = 22.3193, 114.1694
PAYLOAD = json.dumps({"lat": LAT, "lng": LNG})

# The configured per-connection tick ceiling (app/core/config.py).
TICKS_PER_SECOND = 2


def _best_of(fn, iterations: int, rounds: int = 5) -> float:
    """Fastest mean over `rounds`, in microseconds per call.

    Best-of rather than mean-of-means: we want the achievable cost, not the cost
    on a machine that was also doing something else. The scaling question is
    about the floor.
    """
    best = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        for _ in range(iterations):
            fn()
        best = min(best, (time.perf_counter() - t0) / iterations)
    return best * 1e6


def measure_cpu() -> dict[str, float]:
    from app.core.hk_bounds import is_in_hong_kong

    def parse():
        d = json.loads(PAYLOAD)
        return float(d["lat"]), float(d["lng"])

    def geo():
        return is_in_hong_kong(LAT, LNG)

    def whole():
        d = json.loads(PAYLOAD)
        return is_in_hong_kong(float(d["lat"]), float(d["lng"]))

    return {
        "parse": _best_of(parse, 200_000),
        "geo": _best_of(geo, 200_000),
        "cpu_total": _best_of(whole, 200_000),
    }


async def measure_db() -> float | None:
    """One tick's DB work, in ms. None when there is no reachable DB."""
    from sqlalchemy import select, text, update

    from app.core.db import get_session_factory
    from app.models import DriverProfile

    factory = get_session_factory()
    try:
        async with factory() as s:
            driver_id = (await s.execute(text("SELECT id FROM driver_profiles LIMIT 1"))).scalar()
    except Exception as exc:
        print(f"  [skip] DB unreachable: {type(exc).__name__}", file=sys.stderr)
        return None
    if driver_id is None:
        print("  [skip] no driver_profiles row to measure against", file=sys.stderr)
        return None

    async def one_tick() -> None:
        async with factory() as ops:
            row = (
                await ops.execute(
                    select(DriverProfile.id, DriverProfile.status).where(
                        DriverProfile.id == driver_id
                    )
                )
            ).first()
            if row is None:
                return
            await ops.execute(
                update(DriverProfile)
                .where(DriverProfile.id == driver_id)
                .values(current_location=f"POINT({LNG} {LAT})")
            )
            await ops.commit()

    for _ in range(5):
        await one_tick()
    samples = []
    for _ in range(30):
        t0 = time.perf_counter()
        for _ in range(10):
            await one_tick()
        samples.append((time.perf_counter() - t0) / 10)
    return statistics.median(samples) * 1000


async def measure_redis() -> float | None:
    """One PUBLISH round trip, in microseconds. None when Redis is unreachable."""
    from app.core.db import get_redis

    client = get_redis()
    try:
        await client.ping()
    except Exception as exc:
        print(f"  [skip] Redis unreachable: {type(exc).__name__}", file=sys.stderr)
        return None

    async def one_publish() -> None:
        await client.publish("realtaxi:bench:chan", PAYLOAD)

    for _ in range(5):
        await one_publish()
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(200):
            await one_publish()
        samples.append((time.perf_counter() - t0) / 200)
    return min(samples) * 1e6


def report(cpu: dict[str, float], db_ms: float | None, redis_us: float | None) -> None:
    total_us = cpu["cpu_total"] + (db_ms * 1000 if db_ms else 0) + (redis_us or 0)

    db_line = f"  DB select+update+commit {db_ms * 1000:9.2f} us" if db_ms else "  DB (skipped)"
    redis_line = f"  Redis publish           {redis_us:9.2f} us" if redis_us else "  Redis (skip)"
    print()
    print("cost of ONE GPS tick")
    print(f"  json parse + float      {cpu['parse']:9.2f} us")
    print(f"  HK polygon test         {cpu['geo']:9.2f} us")
    print(db_line)
    print(redis_line)
    print(f"  {'-' * 38}")
    print(f"  TOTAL                   {total_us:9.2f} us")

    if db_ms and redis_us:
        share = redis_us / total_us
        print()
        print(f"  Redis publish is {share * 100:.0f}% of the whole tick.")
        print(f"  CPU work is {(cpu['cpu_total'] / total_us) * 100:.1f}% of the whole tick.")

    print()
    print(f"scaling at the {TICKS_PER_SECOND} tps per-connection cap")
    header = f"  {'drivers':>8}  {'ticks/s':>8}  {'CPU cores':>10}  {'DB conns':>9}  {'Redis':>6}"
    print(header)
    for n in (100, 500, 1_000, 5_000, 10_000):
        ticks = n * TICKS_PER_SECOND
        cores = ticks * cpu["cpu_total"] / 1e6
        db_conns = ticks * (db_ms / 1000) if db_ms else 0
        # A publish occupies a pooled Redis connection for ~redis_us.
        redis_conns = ticks * (redis_us / 1e6) if redis_us else 0
        print(f"  {n:>8}  {ticks:>8}  {cores:>10.2f}  {db_conns:>9.1f}  {redis_conns:>6.1f}")

    print()
    print("Compare against the configured limits:")
    print("  DB pool      : pool_size=10 + max_overflow=20 = 30 connections")
    print("  Redis pool   : redis-py default max_connections=100")
    print("  WS per-user  : ws_max_connections_per_user=5")
    print("  WS per-proc  : ws_max_connections_total=2000")


def main() -> None:
    print("benchmarking the location pipeline (best-of-5, microseconds)")
    cpu = measure_cpu()
    print(f"  cpu ok: {cpu['cpu_total']:.2f} us/tick")
    db_ms = asyncio.run(measure_db())
    redis_us = asyncio.run(measure_redis())
    report(cpu, db_ms, redis_us)


if __name__ == "__main__":
    main()
