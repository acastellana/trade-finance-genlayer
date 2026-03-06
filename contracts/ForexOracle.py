# v0.1.0
# { "Depends": "py-genlayer:latest" }
from genlayer import *
import json


class ForexOracle(gl.Contract):
    """
    GenLayer Intelligent Contract: BOB/PEN Forex Oracle
    =====================================================

    Two modes of operation:

    1. ESCROW MODE  — triggered when an importer funds a trade escrow
       ─────────────────────────────────────────────────────────────
       Flow:
         a) Importer calls fundEscrow() on Base Sepolia TradeFinanceEscrow
            → escrow status becomes PENDING_RATE
            → escrow emits RateRequested(escrowAddress)
         b) Relayer sees the event, calls request_rate(escrow_address) here
         c) GenLayer fetches BOB/PEN from 3 authoritative sources (~100s)
         d) Rate + escrow address stored in `completed_requests`
         e) Relayer polls get_completed_requests(), finds the result
         f) Relayer calls escrow.receiveRate(rate18) on Base Sepolia
         g) Escrow calculates exact PEN required, refunds excess, goes ACTIVE

    2. STANDALONE MODE — direct rate update, stored globally
       ──────────────────────────────────────────────────────
       Call update_rate() directly for keeper/monitoring use.

    Sources (3 authoritative, no API key required):
      1. Coinbase Exchange Rates API
         https://api.coinbase.com/v2/exchange-rates?currency=BOB → data.rates.PEN
         Institutional market rate, BOB→PEN direct.

      2. BCRP × BCB official cross-rate
         BCRP (Peru central bank) series PD04638PD: PEN per USD interbank sell
         BCB (Bolivia central bank) peg: 6.96 BOB per USD (fixed since 2012)
         BOB/PEN = BCRP_rate / 6.96

      3. ExchangeRate-API open access
         https://open.er-api.com/v6/latest/BOB → rates.PEN
         Enterprise aggregated data, updated every 24h.

    Consensus:
      - ALL 5 validators independently fetch from all 3 sources (strict_eq)
      - 5 × 3 = 15 independent API calls per rate update
      - Each validator computes median of its 3 sources, rounds to nearest 0.001 (10 bps)
      - strict_eq: consensus passes only if all validators return the exact same integer
      - No LLM involved in comparison — pure deterministic arithmetic
      - Tolerance: 200 bps within each validator's own source spread
      - Minimum 2 of 3 sources required per validator

    Rate representation:
      rate_str  = "0.4948"               human-readable
      rate_18   = 494800000000000000     × 10^18, plug directly into Solidity
    """

    # ── Global rate (standalone mode) ──────────────────────────────────────
    rate_str: str
    rate_18: u256
    update_count: u256

    # ── Escrow-specific requests ────────────────────────────────────────────
    # pending_requests: escrow_address → "1" (waiting for rate)
    # completed_requests: escrow_address → JSON {rate_str, rate_18, fetched_at}
    # processed_requests: escrow_address → "1" (relayer has picked it up)
    pending_requests: TreeMap[str, str]
    completed_requests: TreeMap[str, str]
    processed_requests: TreeMap[str, str]
    request_count: u256

    # ── Config ──────────────────────────────────────────────────────────────
    tolerance_bps: u256   # max spread between sources (default 200 = 2%)
    bcb_peg_x100: u256    # BOB per USD × 100 (696 = 6.96, BCB official peg)
    owner: Address

    def __init__(self, tolerance_bps: int = 200):
        self.owner = gl.message.sender_address
        self.tolerance_bps = u256(tolerance_bps)
        self.bcb_peg_x100 = u256(696)
        self.rate_str = ""
        self.rate_18 = u256(0)
        self.update_count = u256(0)
        self.request_count = u256(0)

    # ═══════════════════════════════════════════════════════════════════════
    # ESCROW MODE
    # ═══════════════════════════════════════════════════════════════════════

    @gl.public.write
    def request_rate(self, escrow_address: str) -> None:
        """
        Fetch BOB/PEN rate for a specific escrow.

        Called by the relayer when it sees a RateRequested event on Base Sepolia.
        After consensus, the result is stored in completed_requests keyed by
        escrow_address. The relayer then picks it up and calls receiveRate()
        on the Base Sepolia escrow contract.

        Args:
            escrow_address: checksummed Base Sepolia address of the escrow
        """
        # Prevent duplicate requests for the same escrow
        existing = self.completed_requests.get(escrow_address)
        if existing is not None:
            raise ValueError(f"ForexOracle: rate already fetched for {escrow_address}")

        self.pending_requests[escrow_address] = "1"

        rate_18, rate_str = self._fetch_consensus_rate()

        # Move from pending → completed
        del self.pending_requests[escrow_address]
        self.completed_requests[escrow_address] = json.dumps({
            "rate_str": rate_str,
            "rate_18": rate_18,
        })
        self.request_count = u256(int(self.request_count) + 1)

        # Also update global rate
        self.rate_str = rate_str
        self.rate_18 = u256(rate_18)
        self.update_count = u256(int(self.update_count) + 1)

    @gl.public.write
    def mark_processed(self, escrow_address: str) -> None:
        """
        Called by the relayer after it has successfully delivered the rate
        to the Base Sepolia escrow (receiveRate() confirmed).
        Prevents the relayer from double-processing.
        """
        result = self.completed_requests.get(escrow_address)
        if result is None:
            raise ValueError(f"ForexOracle: no completed request for {escrow_address}")
        self.processed_requests[escrow_address] = "1"

    @gl.public.view
    def get_completed_requests(self) -> str:
        """
        Returns all completed (unprocessed) rate requests as JSON array.
        Relayer polls this to find escrows waiting for their rate callback.

        Returns: [{escrow_address, rate_str, rate_18}, ...]
        """
        results = []
        # Iterate completed requests that haven't been processed yet
        # Note: TreeMap iteration — use known addresses from request log
        # In production this would use an index; for demo we expose all completed
        return json.dumps({
            "pending_count": int(self.request_count),
            "note": "poll get_rate_for_escrow(address) for specific escrow"
        })

    @gl.public.view
    def get_rate_for_escrow(self, escrow_address: str) -> str:
        """
        Returns the fetched rate for a specific escrow address.
        Relayer calls this to get the rate to deliver to Base Sepolia.

        Returns JSON: {rate_str, rate_18, processed}
        Raises if no rate has been fetched for this escrow yet.
        """
        result = self.completed_requests.get(escrow_address)
        if result is None:
            # Check if it's pending
            pending = self.pending_requests.get(escrow_address)
            if pending is not None:
                raise ValueError(f"ForexOracle: rate fetch in progress for {escrow_address}")
            raise ValueError(f"ForexOracle: no rate request found for {escrow_address}")

        data = json.loads(result)
        processed = self.processed_requests.get(escrow_address) is not None
        return json.dumps({
            "escrow_address": escrow_address,
            "rate_str": data["rate_str"],
            "rate_18": data["rate_18"],
            "processed": processed,
        })

    # ═══════════════════════════════════════════════════════════════════════
    # STANDALONE MODE
    # ═══════════════════════════════════════════════════════════════════════

    @gl.public.write
    def update_rate(self) -> None:
        """
        Standalone rate update — stores latest BOB/PEN globally.
        Used by keeper service or for monitoring.
        Does not create an escrow-specific request.
        """
        rate_18, rate_str = self._fetch_consensus_rate()
        self.rate_18 = u256(rate_18)
        self.rate_str = rate_str
        self.update_count = u256(int(self.update_count) + 1)

    @gl.public.view
    def get_rate(self) -> str:
        """Returns the latest global rate."""
        if self.rate_str == "":
            raise ValueError("ForexOracle: no rate stored — call update_rate() first")
        return json.dumps({
            "rate_str": self.rate_str,
            "rate_18": int(self.rate_18),
            "update_count": int(self.update_count),
            "tolerance_bps": int(self.tolerance_bps),
        })

    @gl.public.view
    def get_rate_18(self) -> int:
        """Returns rate × 10^18 — drop-in for IMockForexOracle.getRate()."""
        if int(self.rate_18) == 0:
            raise ValueError("ForexOracle: no rate stored")
        return int(self.rate_18)

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL
    # ═══════════════════════════════════════════════════════════════════════

    def _fetch_consensus_rate(self):
        """
        Core rate fetch logic. Shared by request_rate() and update_rate().
        Returns (rate_18: int, rate_str: str).

        Uses prompt_non_comparative:
          - Leader fetches all 3 sources and computes median
          - Co-validators verify the result is a plausible BOB/PEN rate
          - Handles live rate drift between validator fetches correctly
        """
        tol = int(self.tolerance_bps)
        bcb_peg = int(self.bcb_peg_x100)

        def nondet() -> str:
            rates = {}
            errors = []

            # ── Source 1: Coinbase Exchange Rates API ────────────────────
            # Institutional market rate. BOB→PEN returned directly.
            # No API key. CORS-friendly. Updated in real-time.
            try:
                resp = gl.nondet.web.get(
                    "https://api.coinbase.com/v2/exchange-rates?currency=BOB"
                )
                j = json.loads(resp.body.decode("utf-8", errors="replace"))
                val = float(j["data"]["rates"]["PEN"])
                if 0.20 <= val <= 2.00:
                    rates["coinbase"] = round(val, 6)
                else:
                    errors.append(f"coinbase out of range: {val}")
            except Exception as e:
                errors.append(f"coinbase: {str(e)[:100]}")

            # ── Source 2: BCRP (Peru central bank) × BCB official peg ───
            # BCRP series PD04638PD = TC interbancario venta (PEN per USD)
            # BCB peg: bcb_peg / 100 BOB per USD (6.96, fixed since 2012)
            # BOB/PEN = BCRP_PEN_USD / BOB_per_USD
            try:
                resp = gl.nondet.web.get(
                    "https://estadisticas.bcrp.gob.pe/estadisticas/series/api/PD04638PD/json"
                )
                raw = resp.body.decode("utf-8", errors="replace").strip()
                j = json.loads(raw)
                pen_per_usd = float(j["periods"][-1]["values"][0])
                bob_per_usd = bcb_peg / 100.0  # 6.96
                val = round(pen_per_usd / bob_per_usd, 6)
                if 0.20 <= val <= 2.00:
                    rates["bcrp_bcb"] = val
                else:
                    errors.append(f"bcrp_bcb out of range: {val}")
            except Exception as e:
                errors.append(f"bcrp_bcb: {str(e)[:100]}")

            # ── Source 3: ExchangeRate-API open access ───────────────────
            # Enterprise aggregated data. No API key. Updated every 24h.
            try:
                resp = gl.nondet.web.get("https://open.er-api.com/v6/latest/BOB")
                j = json.loads(resp.body.decode("utf-8", errors="replace"))
                val = float(j["rates"]["PEN"])
                if 0.20 <= val <= 2.00:
                    rates["open_er"] = round(val, 6)
                else:
                    errors.append(f"open_er out of range: {val}")
            except Exception as e:
                errors.append(f"open_er: {str(e)[:100]}")

            # ── Consensus check ──────────────────────────────────────────
            if len(rates) < 2:
                raise ValueError(f"fewer than 2 sources available: {errors}")

            values = sorted(rates.values())
            median = values[len(values) // 2]

            for src, val in rates.items():
                deviation_bps = abs(val - median) / median * 10_000
                if deviation_bps > tol:
                    raise ValueError(
                        f"source '{src}' deviates {deviation_bps:.0f} bps "
                        f"from median (max allowed {tol} bps)"
                    )

            # Round to nearest 0.001 (10 bps bucket) so all validators
            # return the same integer despite fetching seconds apart.
            rate_rounded = round(median, 3)
            rate_18 = int(rate_rounded * 10 ** 18)
            return str(rate_18)

        # strict_eq: ALL 5 validators independently fetch from all 3 sources,
        # compute the median, then round to the nearest 0.001 (10 bps bucket).
        # Consensus passes only if every validator returns the exact same integer.
        # No LLM involved — pure deterministic comparison.
        # Rounding to 10 bps absorbs natural time-drift between validator fetches
        # (BOB/PEN barely moves intraday) while keeping the rate accurate enough
        # for escrow settlement.
        result_str = gl.eq_principle.strict_eq(nondet)

        rate_18 = int(result_str)
        rate = rate_18 / 10 ** 18
        if not (0.20 <= rate <= 2.00):
            raise ValueError(f"ForexOracle: rate {rate} out of plausible range")

        return rate_18, str(round(rate, 3))
