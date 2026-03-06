# v1.1.0
# { "Depends": "py-genlayer:latest" }
"""FxBenchmarkOracle — Benchmark FX lock oracle (GenLayer)

Design goal: institutional legibility.

Benchmark hierarchy:
  Primary:  BCRP × BCB cross (PEN/USD official / 6.96 BOB/USD peg)
  Fallback: open.er-api aggregated market data
  Fail:     no lock

Critical constraint for GenLayer consensus:
  Do NOT mix primary+fallback sources in the same strict_eq run.
  If some validators can reach BCRP and others cannot, strict_eq will disagree.

So we expose two explicit entrypoints:
  - request_rate_lock_primary()  (BCRP×BCB only)
  - request_rate_lock_fallback() (open.er-api only)

Relayer policy:
  Try primary; if tx result is not SUCCESS/AGREE, try fallback.

Outputs (stored per trade):
  rate_str, rate_18, benchmark_type, benchmark_id, as_of_timestamp, sources_used

Roll support mirrors rate lock:
  request_roll_primary() / request_roll_fallback()
  get_rolled_rate()

Roll cost:
  roll_cost_18 = 0 (spot re-lock; no claim of forwards).
"""

from genlayer import *
import json
import datetime

BCRP_URL    = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api/PD04638PD/json"
OPEN_ER_URL = "https://open.er-api.com/v6/latest/BOB"

BCB_PEG    = 6.96
RATE_MIN   = 0.20
RATE_MAX   = 2.00
STALE_DAYS = 3


class FxBenchmarkOracle(gl.Contract):
    # ── Per-trade results ────────────────────────────────────────────────
    completed_locks: TreeMap[str, str]   # trade_address → JSON BenchmarkResult
    completed_rolls: TreeMap[str, str]   # trade_address → JSON RollResult (latest)

    # ── Global (monitoring) ──────────────────────────────────────────────
    last_benchmark: str
    last_rate_18:   u256
    update_count:   u256

    # ── Config ───────────────────────────────────────────────────────────
    tolerance_bps: u256
    owner: Address

    def __init__(self, tolerance_bps: int = 200):
        self.owner = gl.message.sender_address
        self.tolerance_bps = u256(tolerance_bps)
        self.last_benchmark = ""
        self.last_rate_18 = u256(0)
        self.update_count = u256(0)

    # ═══════════════════════════════════════════════════════════════════════
    # RATE LOCK — PRIMARY
    # ═══════════════════════════════════════════════════════════════════════

    @gl.public.write
    def request_rate_lock_primary(self, trade_address: str) -> None:
        existing = self.completed_locks.get(trade_address)
        if existing is not None:
            raise ValueError(f"FxBenchmarkOracle: lock already exists for {trade_address}")

        result = self._fetch_primary_bcrp_bcb()
        self.completed_locks[trade_address] = json.dumps(result)
        self._update_global(result)

    # ═══════════════════════════════════════════════════════════════════════
    # RATE LOCK — FALLBACK
    # ═══════════════════════════════════════════════════════════════════════

    @gl.public.write
    def request_rate_lock_fallback(self, trade_address: str) -> None:
        existing = self.completed_locks.get(trade_address)
        if existing is not None:
            raise ValueError(f"FxBenchmarkOracle: lock already exists for {trade_address}")

        result = self._fetch_fallback_open_er()
        self.completed_locks[trade_address] = json.dumps(result)
        self._update_global(result)

    @gl.public.view
    def get_locked_rate(self, trade_address: str) -> str:
        result = self.completed_locks.get(trade_address)
        if result is None:
            raise ValueError(f"FxBenchmarkOracle: no lock found for {trade_address}")
        return result

    # ═══════════════════════════════════════════════════════════════════════
    # ROLL — PRIMARY / FALLBACK
    # ═══════════════════════════════════════════════════════════════════════

    @gl.public.write
    def request_roll_primary(self, trade_address: str, new_due_date: str) -> None:
        result = self._fetch_primary_bcrp_bcb()
        payload = dict(result)
        payload["new_due_date"] = new_due_date
        payload["roll_cost_18"] = 0
        self.completed_rolls[trade_address] = json.dumps(payload)
        self._update_global(result)

    @gl.public.write
    def request_roll_fallback(self, trade_address: str, new_due_date: str) -> None:
        result = self._fetch_fallback_open_er()
        payload = dict(result)
        payload["new_due_date"] = new_due_date
        payload["roll_cost_18"] = 0
        self.completed_rolls[trade_address] = json.dumps(payload)
        self._update_global(result)

    @gl.public.view
    def get_rolled_rate(self, trade_address: str) -> str:
        result = self.completed_rolls.get(trade_address)
        if result is None:
            raise ValueError(f"FxBenchmarkOracle: no roll found for {trade_address}")
        return result

    # ═══════════════════════════════════════════════════════════════════════
    # STANDALONE / MONITORING
    # ═══════════════════════════════════════════════════════════════════════

    @gl.public.write
    def update_rate_primary(self) -> None:
        result = self._fetch_primary_bcrp_bcb()
        self._update_global(result)

    @gl.public.write
    def update_rate_fallback(self) -> None:
        result = self._fetch_fallback_open_er()
        self._update_global(result)

    @gl.public.view
    def get_rate(self) -> str:
        if self.last_benchmark == "":
            raise ValueError("FxBenchmarkOracle: no rate stored")
        return self.last_benchmark

    @gl.public.view
    def get_rate_18(self) -> int:
        if int(self.last_rate_18) == 0:
            raise ValueError("FxBenchmarkOracle: no rate stored")
        return int(self.last_rate_18)

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL
    # ═══════════════════════════════════════════════════════════════════════

    def _fetch_primary_bcrp_bcb(self) -> dict:
        tol = int(self.tolerance_bps)

        def nondet() -> str:
            now_utc = datetime.datetime.utcnow()

            resp = gl.nondet.web.get(BCRP_URL)
            raw = resp.body.decode("utf-8", errors="replace").strip()

            # Tolerate trailing garbage (some validators see extra bytes)
            dec = json.JSONDecoder()
            j, _ = dec.raw_decode(raw)

            periods = j.get("periods", [])
            if not periods:
                raise ValueError("BCRP: empty periods")

            latest = periods[-1]
            period_name = latest.get("name", "")
            pen_per_usd = float(latest["values"][0])

            # Stale guard
            try:
                period_dt = datetime.datetime.strptime(period_name, "%d.%b.%y")
                age_days = (now_utc - period_dt).days
                if age_days > STALE_DAYS:
                    raise ValueError(f"BCRP: stale ({age_days}d)")
                bench_date = period_dt.strftime("%Y%m%d")
            except Exception:
                bench_date = now_utc.strftime("%Y%m%d")

            cross = pen_per_usd / BCB_PEG
            if not (RATE_MIN <= cross <= RATE_MAX):
                raise ValueError(f"BCRP/BCB cross out of range: {cross}")

            # Deterministic rounding
            rate_rounded = round(cross, 3)
            rate_18 = int(rate_rounded * 10**18)

            benchmark_type = "BCRP_BCB_CROSS"
            benchmark_id = f"BCRP-BCB-{bench_date}"
            as_of = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            result = {
                "rate_str": str(rate_rounded),
                "rate_18": rate_18,
                "benchmark_type": benchmark_type,
                "benchmark_id": benchmark_id,
                "as_of_timestamp": as_of,
                "sources_used": {
                    "bcrp": {"pen_per_usd": pen_per_usd, "period": period_name},
                    "bcb":  {"bob_per_usd": BCB_PEG},
                    "computed": {"bob_per_pen": round(cross, 6)},
                },
                "tolerance_bps": tol,
            }
            return json.dumps(result)

        result_json = gl.eq_principle.strict_eq(nondet)
        return json.loads(result_json)

    def _fetch_fallback_open_er(self) -> dict:
        tol = int(self.tolerance_bps)

        def nondet() -> str:
            now_utc = datetime.datetime.utcnow()
            resp = gl.nondet.web.get(OPEN_ER_URL)
            j = json.loads(resp.body.decode("utf-8", errors="replace"))
            val = float(j["rates"]["PEN"])
            if not (RATE_MIN <= val <= RATE_MAX):
                raise ValueError(f"open_er out of range: {val}")

            rate_rounded = round(val, 3)
            rate_18 = int(rate_rounded * 10**18)

            benchmark_type = "MARKET_AGGREGATE"
            benchmark_id = f"MARKET-{now_utc.strftime('%Y%m%d')}"
            as_of = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            result = {
                "rate_str": str(rate_rounded),
                "rate_18": rate_18,
                "benchmark_type": benchmark_type,
                "benchmark_id": benchmark_id,
                "as_of_timestamp": as_of,
                "sources_used": {
                    "open_er": {"bob_per_pen": round(val, 6), "updated": j.get("time_last_update_utc", "")},
                },
                "tolerance_bps": tol,
            }
            return json.dumps(result)

        result_json = gl.eq_principle.strict_eq(nondet)
        return json.loads(result_json)

    def _update_global(self, result: dict) -> None:
        self.last_benchmark = json.dumps(result)
        self.last_rate_18 = u256(result["rate_18"])
        self.update_count = u256(int(self.update_count) + 1)
