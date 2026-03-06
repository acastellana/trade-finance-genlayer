# { "Depends": "py-genlayer:test" }
from genlayer import *
import json
import datetime


class TradeFinanceDeal(gl.Contract):
    """Trade Finance Deal with AI-Powered Forex Settlement on GenLayer.

    Scenario: Bolivia → Peru battery-grade lithium carbonate export
    - Exporter: Bolivian lithium carbonate producer (Minera Andina SRL)
    - Importer: Peruvian buyer
    - Invoice currency: BOB (Bolivian Boliviano)
    - Settlement currency: PEN (Peruvian Sol)
    - AI validators fetch live BOB/PEN rate at settlement
    - Dispute resolution via InternetCourt (external AI jury)

    Flow:
    1. Exporter creates deal specifying: importer, invoice currency, settlement currency,
       invoice amount, goods description, and delivery deadline.
    2. Importer accepts and funds escrow (in settlement currency at estimated rate).
    3. Exporter ships goods and submits proof of delivery (bill of lading, tracking).
    4. Importer confirms receipt (or disputes).
    5. At settlement, AI validators fetch the live BOB/PEN forex rate and calculate final amount.
    6. If parties disagree on delivery, they create an InternetCourt case,
       link it here, and resolve from the court verdict.
    """

    # --- Parties ---
    exporter: Address
    importer: Address

    # --- Deal terms ---
    goods_description: str
    invoice_currency: str
    settlement_currency: str
    invoice_amount: str
    estimated_rate: str
    rate_tolerance_bps: u256
    delivery_deadline: str

    # --- State ---
    status: str  # created|funded|shipped|delivered|settled|disputed|resolved|cancelled
    escrow_amount: str
    delivery_proof: str
    receipt_confirmation: str
    settlement_rate: str
    final_amount: str
    rate_source: str

    # --- InternetCourt Integration ---
    court_contract_address: str
    court_verdict: str
    court_reasoning: str

    # --- Timestamps ---
    created_at: str
    funded_at: str
    shipped_at: str
    delivered_at: str
    settled_at: str

    def __init__(
        self,
        importer: Address,
        goods_description: str,
        invoice_currency: str,
        settlement_currency: str,
        invoice_amount: str,
        estimated_rate: str,
        rate_tolerance_bps: int,
        delivery_deadline: str,
    ):
        self.exporter = gl.message.sender_address

        # Handle Address input formats (str from JS SDK, bytes from Python tests)
        if isinstance(importer, str):
            importer = Address(importer)
        elif isinstance(importer, bytes):
            importer = Address(importer)
        self.importer = importer

        self.goods_description = goods_description
        self.invoice_currency = invoice_currency.upper()
        self.settlement_currency = settlement_currency.upper()
        self.invoice_amount = invoice_amount
        self.estimated_rate = estimated_rate
        self.rate_tolerance_bps = u256(rate_tolerance_bps)
        self.delivery_deadline = delivery_deadline

        self.status = "created"
        self.escrow_amount = ""
        self.delivery_proof = ""
        self.receipt_confirmation = ""
        self.settlement_rate = ""
        self.final_amount = ""
        self.rate_source = ""

        self.court_contract_address = ""
        self.court_verdict = ""
        self.court_reasoning = ""

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.created_at = now
        self.funded_at = ""
        self.shipped_at = ""
        self.delivered_at = ""
        self.settled_at = ""

    # ──────────────────────────────────────────────
    # LIFECYCLE: Fund → Ship → Deliver → Settle
    # ──────────────────────────────────────────────

    @gl.public.write
    def fund_escrow(self, escrow_amount: str) -> None:
        """Importer funds the escrow with settlement currency (PEN) amount."""
        if self.status != "created":
            raise gl.vm.UserError("Deal not in created state")
        if gl.message.sender_address != self.importer:
            raise gl.vm.UserError("Only importer can fund escrow")

        self.escrow_amount = escrow_amount
        self.status = "funded"
        self.funded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    @gl.public.write
    def submit_shipment(self, delivery_proof: str) -> None:
        """Exporter submits proof of shipment (bill of lading, container tracking, etc.)."""
        if self.status != "funded":
            raise gl.vm.UserError("Deal not funded yet")
        if gl.message.sender_address != self.exporter:
            raise gl.vm.UserError("Only exporter can submit shipment proof")

        self.delivery_proof = delivery_proof
        self.status = "shipped"
        self.shipped_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    @gl.public.write
    def confirm_delivery(self, confirmation: str) -> None:
        """Importer confirms goods received."""
        if self.status != "shipped":
            raise gl.vm.UserError("Goods not shipped yet")
        if gl.message.sender_address != self.importer:
            raise gl.vm.UserError("Only importer can confirm delivery")

        self.receipt_confirmation = confirmation
        self.status = "delivered"
        self.delivered_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    @gl.public.write
    def settle(self) -> None:
        """AI-powered settlement.

        Validators fetch a live forex rate using nondet web GET. The nondet
        block returns ONLY the rate string (e.g. "0.4931") — minimal surface
        for consensus disagreement. All computation happens deterministically
        after the eq_principle call.

        Bolivia→Peru lithium carbonate trade:
        - Invoice in BOB, Settlement in PEN
        - Estimated rate: ~0.40 PEN/BOB
        - Tolerance: `rate_tolerance_bps` basis points
        """
        if self.status != "delivered":
            raise gl.vm.UserError("Delivery not confirmed")

        # Copy storage to locals for non-det block
        inv_currency = self.invoice_currency
        set_currency = self.settlement_currency

        url = f"https://open.er-api.com/v6/latest/{inv_currency}"

        def nondet():
            web_res = gl.nondet.web.get(url)
            if int(web_res.status) != 200 or web_res.body is None:
                return "ERROR: forex api unreachable"

            body_text = web_res.body.decode("utf-8", errors="replace")

            try:
                data = json.loads(body_text)
            except Exception:
                return "ERROR: invalid json from api"

            if data.get("result") != "success":
                return "ERROR: api returned non-success"

            rates = data.get("rates", {})
            raw_rate = rates.get(set_currency)
            if raw_rate is None:
                return f"ERROR: {set_currency} not in rates"

            # Round to 4 decimals — all validators should agree on this
            return str(round(float(raw_rate), 4))

        rate_str = gl.eq_principle.prompt_non_comparative(
            nondet,
            task=f"Fetch the current {inv_currency} to {set_currency} exchange rate from open.er-api.com",
            criteria=(
                "The output must be a single decimal number representing the exchange rate "
                f"for 1 {inv_currency} in {set_currency}. Numbers that round to the same "
                "value at 4 decimal places should be considered equivalent."
            ),
        )

        # Validate
        if not isinstance(rate_str, str) or rate_str.startswith("ERROR"):
            raise gl.vm.UserError(f"Settlement failed: {rate_str}")

        try:
            rate_f = float(rate_str)
        except Exception:
            raise gl.vm.UserError(f"Settlement failed: non-numeric rate '{rate_str}'")

        if rate_f <= 0:
            raise gl.vm.UserError("Settlement failed: rate must be positive")

        # Deterministic computation (same for all validators)
        inv_amount_f = float(self.invoice_amount)
        final_amount_f = round(inv_amount_f * rate_f, 2)

        self.settlement_rate = rate_str
        self.rate_source = "open.er-api.com"
        self.final_amount = str(final_amount_f)
        self.settled_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.status = "settled"

    # ──────────────────────────────────────────────
    # DISPUTE PATH — InternetCourt Integration
    # ──────────────────────────────────────────────

    @gl.public.write
    def raise_dispute(self, reason: str) -> None:
        """Either party can dispute after shipment."""
        if self.status not in ("shipped", "delivered"):
            raise gl.vm.UserError("Can only dispute after shipment")
        sender = gl.message.sender_address
        if sender != self.exporter and sender != self.importer:
            raise gl.vm.UserError("Not a party to this deal")

        self.status = "disputed"
        # Store the reason in court_reasoning temporarily (overwritten by IC verdict)
        self.court_reasoning = reason

    @gl.public.write
    def link_court_case(self, court_address: str) -> None:
        """Link an InternetCourt contract to this dispute."""
        if self.status != "disputed":
            raise gl.vm.UserError("No active dispute — call raise_dispute() first")
        sender = gl.message.sender_address
        if sender != self.exporter and sender != self.importer:
            raise gl.vm.UserError("Not a party to this deal")
        if self.court_contract_address != "":
            raise gl.vm.UserError("Court case already linked")

        self.court_contract_address = court_address

    @gl.public.write
    def resolve_from_court(self) -> None:
        """Read InternetCourt verdict and mark the dispute resolved.

        This is a deterministic cross-contract view call.
        """
        if self.status != "disputed":
            raise gl.vm.UserError("No active dispute")
        court_addr = self.court_contract_address
        if court_addr == "":
            raise gl.vm.UserError("No court case linked — call link_court_case() first")

        court_contract = gl.get_contract_at(Address(court_addr))
        verdict_raw = court_contract.view().get_verdict()

        if isinstance(verdict_raw, str):
            verdict_data = json.loads(verdict_raw)
        elif isinstance(verdict_raw, dict):
            verdict_data = verdict_raw
        else:
            verdict_data = json.loads(str(verdict_raw))

        self.court_verdict = str(verdict_data.get("verdict", "UNDETERMINED"))
        self.court_reasoning = str(verdict_data.get("reasoning", ""))
        self.settled_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.status = "resolved"

        # Note: On-chain token movement would happen here in a full implementation.
        # TRUE  → release escrow_amount to exporter
        # FALSE → refund escrow_amount to importer
        # UNDETERMINED → escrow held, parties must negotiate further

    # ──────────────────────────────────────────────
    # CANCEL
    # ──────────────────────────────────────────────

    @gl.public.write
    def cancel(self) -> None:
        """Exporter can cancel before importer funds escrow."""
        if self.status != "created":
            raise gl.vm.UserError("Can only cancel before funding")
        if gl.message.sender_address != self.exporter:
            raise gl.vm.UserError("Only exporter can cancel")
        self.status = "cancelled"

    # ──────────────────────────────────────────────
    # READ METHODS
    # ──────────────────────────────────────────────

    @gl.public.view
    def get_deal_status(self) -> str:
        return json.dumps({
            "status": self.status,
            "exporter": self.exporter.as_hex,
            "importer": self.importer.as_hex,
            "goods": self.goods_description,
            "invoice": f"{self.invoice_amount} {self.invoice_currency}",
            "settlement_currency": self.settlement_currency,
            "estimated_rate": self.estimated_rate,
            "escrow": self.escrow_amount,
            "settlement_rate": self.settlement_rate,
            "final_amount": self.final_amount,
            "court_contract_address": self.court_contract_address,
            "court_verdict": self.court_verdict,
        })

    @gl.public.view
    def get_full_details(self) -> str:
        return json.dumps({
            "status": self.status,
            "exporter": self.exporter.as_hex,
            "importer": self.importer.as_hex,
            "goods_description": self.goods_description,
            "invoice_currency": self.invoice_currency,
            "settlement_currency": self.settlement_currency,
            "invoice_amount": self.invoice_amount,
            "estimated_rate": self.estimated_rate,
            "rate_tolerance_bps": int(self.rate_tolerance_bps),
            "delivery_deadline": self.delivery_deadline,
            "escrow_amount": self.escrow_amount,
            "delivery_proof": self.delivery_proof,
            "receipt_confirmation": self.receipt_confirmation,
            "settlement_rate": self.settlement_rate,
            "final_amount": self.final_amount,
            "rate_source": self.rate_source,
            "court_contract_address": self.court_contract_address,
            "court_verdict": self.court_verdict,
            "court_reasoning": self.court_reasoning,
            "created_at": self.created_at,
            "funded_at": self.funded_at,
            "shipped_at": self.shipped_at,
            "delivered_at": self.delivered_at,
            "settled_at": self.settled_at,
        })

    @gl.public.view
    def get_forex_details(self) -> str:
        return json.dumps({
            "invoice_currency": self.invoice_currency,
            "settlement_currency": self.settlement_currency,
            "invoice_amount": self.invoice_amount,
            "estimated_rate": self.estimated_rate,
            "rate_tolerance_bps": int(self.rate_tolerance_bps),
            "settlement_rate": self.settlement_rate,
            "final_amount": self.final_amount,
            "rate_source": self.rate_source,
        })

    @gl.public.view
    def get_court_details(self) -> str:
        return json.dumps({
            "status": self.status,
            "court_contract_address": self.court_contract_address,
            "court_verdict": self.court_verdict,
            "court_reasoning": self.court_reasoning,
        })
