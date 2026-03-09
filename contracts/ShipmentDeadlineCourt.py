# v0.1.1
# { "Depends": "py-genlayer:latest" }
"""ShipmentDeadlineCourt — Granular shipment delay court for GenLayer.

Evaluates shipment delay against a deadline using court sheet images.
Categorizes the delay into 6 possible buckets for varying settlement outcomes.

Returns verdict to Base Sepolia via the InternetCourt bridge:
    ON_TIME      (1) — crossed on or before deadline
    LATE_1_4     (2) — crossed 1-4 days after deadline
    LATE_5_6     (3) — crossed 5-6 days after deadline
    LATE_7_8     (4) — crossed 7-8 days after deadline
    VERY_LATE    (5) — crossed more than 8 days after deadline
    UNDETERMINED (6) — evidence insufficient or contradictory

Evidence: exactly two composite court sheet images (IPFS CIDs).
    court_sheet_a: contract summary snippet + exporter evidence (ANB customs exit)
    court_sheet_b: contract summary snippet + importer evidence (SUNAT border gate)

Guideline is frozen and versioned.
Current version: shipment-deadline-v1

On construction:
    1. Fetches both court sheet images from IPFS
    2. AI jury evaluates the statement against the images
    3. Encodes verdict and calls BridgeSender.send_message() → bridge → Base Sepolia
"""

from genlayer import *
import json

genvm_eth = gl.evm

# ─── Frozen guideline versions ────────────────────────────────────────────────

GUIDELINES = {
    "shipment-deadline-v1": (
        "Evaluate the statement using only the two submitted court sheet images. "
        "Confirm that the shipment reference and truck plate match across both documents. "
        "Determine whether the evidence shows that the shipment crossed Bolivian export "
        "customs at Desaguadero on or before the stated deadline. "
        "Calculate the number of days between the crossing date and the deadline. "
        "Classify the delay into one of the following buckets: "
        "- ON_TIME: Crossed on or before deadline (0 or negative days late). "
        "- LATE_1_4: Crossed 1 to 4 days after deadline. "
        "- LATE_5_6: Crossed 5 to 6 days after deadline. "
        "- LATE_7_8: Crossed 7 to 8 days after deadline. "
        "- VERY_LATE: Crossed more than 8 days after deadline. "
        "- UNDETERMINED: Evidence is insufficient, unreadable, or contradictory. "
        "Evidence hierarchy: official customs exit records showing the vehicle physically "
        "crossing the border are the most authoritative evidence of crossing time. "
        "Secondary gate or administrative records (e.g. arrival scans, issue dates, "
        "pre-clearance stamps) are weaker and should only be preferred if the primary "
        "customs exit record is missing, unreadable, or clearly inapplicable. "
        "Apply this hierarchy when evaluating conflicting timestamps. "
        "The importer bears the burden of proof: if the importer's evidence is missing or "
        "cannot be verified, and the exporter's customs exit record is clear and timely, "
        "return ON_TIME."
    )
}

IPFS_GATEWAY = "https://ipfs.io/ipfs/"

# Verdict uint8 codes — must match TradeFxSettlement.sol resolveShipmentVerdict()
VERDICT_ON_TIME      = 1
VERDICT_LATE_1_4     = 2
VERDICT_LATE_5_6     = 3
VERDICT_LATE_7_8     = 4
VERDICT_VERY_LATE    = 5
VERDICT_UNDETERMINED = 6


class ShipmentDeadlineCourt(gl.Contract):
    """Granular shipment deadline court.
    Evaluates on construction; sends result via bridge to Base Sepolia."""

    case_id:               str
    settlement_contract:   str   # TradeFxSettlement address on Base Sepolia
    guideline_version:     str
    court_sheet_a_cid:     str
    court_sheet_b_cid:     str
    bridge_sender:         str   # BridgeSender.py address on GenLayer
    target_chain_eid:      u256  # LayerZero EID for Base Sepolia (40245)
    verdict:               str
    verdict_reason:        str
    days_late:             str   # Number of days late as string ("-1" if undetermined)

    def __init__(
        self,
        case_id: str,
        settlement_contract: str,
        statement: str,
        guideline_version: str,
        court_sheet_a_cid: str,
        court_sheet_b_cid: str,
        bridge_sender: str,
        target_chain_eid: int,
        target_contract: str,  # InternetCourtFactory on Base Sepolia
    ):
        if guideline_version not in GUIDELINES:
            raise Exception(f"ShipmentCourt: unknown guideline '{guideline_version}'")

        self.case_id             = case_id
        self.settlement_contract = settlement_contract
        self.guideline_version   = guideline_version
        self.court_sheet_a_cid   = court_sheet_a_cid
        self.court_sheet_b_cid   = court_sheet_b_cid
        self.bridge_sender       = bridge_sender
        self.target_chain_eid    = u256(target_chain_eid)

        guideline = GUIDELINES[guideline_version]

        # Copy to locals for non-det block
        cid_a = court_sheet_a_cid.lstrip("ipfs://")
        cid_b = court_sheet_b_cid.lstrip("ipfs://")
        url_a = IPFS_GATEWAY + cid_a
        url_b = IPFS_GATEWAY + cid_b
        stmt  = statement

        def nondet():
            # Fetch court sheet images from IPFS
            images = []
            fetch_notes = []

            resp_a = gl.nondet.web.get(url_a)
            if resp_a and resp_a.status == 200 and resp_a.body:
                images.append(resp_a.body)
                fetch_notes.append("Court sheet A (exporter/ANB): fetched OK")
            else:
                fetch_notes.append("Court sheet A (exporter/ANB): NOT RETRIEVABLE")

            resp_b = gl.nondet.web.get(url_b)
            if resp_b and resp_b.status == 200 and resp_b.body:
                images.append(resp_b.body)
                fetch_notes.append("Court sheet B (importer/SUNAT): fetched OK")
            else:
                fetch_notes.append("Court sheet B (importer/SUNAT): NOT RETRIEVABLE")

            fetch_summary = "\n".join(fetch_notes)

            prompt = f"""You are an AI juror in the InternetCourt dispute resolution system.
You are evaluating a single disputed shipment timing fact.

STATEMENT TO EVALUATE:
{stmt}

GUIDELINE:
{guideline}

DOCUMENT FETCH STATUS:
{fetch_summary}

You have been provided the court sheet images showing:
- Court Sheet A: contract summary + exporter evidence (ANB customs exit record from Bolivia)
- Court Sheet B: contract summary + importer evidence (SUNAT border gate event record from Peru)

Examine the images carefully. Look for:
1. The stated deadline in the contract summary panel
2. The timestamp on the ANB customs exit record (exporter document)
3. The timestamp on the SUNAT border gate record (importer document)
4. Whether the truck plate and container references match between documents

Your task is to:
1. Determine the crossing date from the evidence.
2. Determine the deadline from the contract summary.
3. Calculate the number of days late (crossing date minus deadline).
4. Map to the correct bucket based on days late:
   - 0 or fewer days late → ON_TIME
   - 1-4 days late → LATE_1_4
   - 5-6 days late → LATE_5_6
   - 7-8 days late → LATE_7_8
   - More than 8 days late → VERY_LATE
   - Cannot determine → UNDETERMINED

Output ONLY valid JSON, no other text:
{{
  "verdict": "ON_TIME" | "LATE_1_4" | "LATE_5_6" | "LATE_7_8" | "VERY_LATE" | "UNDETERMINED",
  "days_late": <number or null>,
  "reason": "One concise sentence explaining the verdict referencing the specific dates and day count."
}}"""

            if images:
                result = gl.nondet.exec_prompt(prompt, images=images)
            else:
                result = gl.nondet.exec_prompt(prompt)

            if isinstance(result, str):
                return result.strip()
            return str(result).strip()

        result_str = gl.eq_principle.prompt_non_comparative(
            nondet,
            task="Evaluate a shipment customs crossing dispute using court sheet document images",
            criteria=(
                "The verdict must be exactly one of: ON_TIME, LATE_1_4, LATE_5_6, LATE_7_8, VERY_LATE, UNDETERMINED. "
                "The days_late must be an integer representing the count of days after the deadline, or null if undetermined. "
                "The reason must reference specific dates and the calculated delay. "
                "ON_TIME is for 0 or negative days late. "
                "LATE_1_4 is for 1-4 days. LATE_5_6 is for 5-6 days. LATE_7_8 is for 7-8 days. VERY_LATE is for >8 days. "
                "UNDETERMINED covers missing or conflicting evidence."
            ),
        )

        # Parse result
        try:
            if isinstance(result_str, str):
                clean = result_str.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(clean)
            elif isinstance(result_str, dict):
                parsed = result_str
            else:
                parsed = json.loads(str(result_str))

            v = parsed.get("verdict", "UNDETERMINED").strip().upper()
            r = parsed.get("reason", "").strip()
            dl = parsed.get("days_late")
            if dl is None:
                dl = "-1"
            else:
                dl = str(int(dl))
        except Exception as e:
            v = "UNDETERMINED"
            r = f"Failed to parse evaluation response: {str(e)}"
            dl = -1

        valid_verdicts = ("ON_TIME", "LATE_1_4", "LATE_5_6", "LATE_7_8", "VERY_LATE", "UNDETERMINED")
        if v not in valid_verdicts:
            v = "UNDETERMINED"
            r = f"Unexpected verdict value, defaulting to UNDETERMINED. Original reason: {r}"
            dl = "-1"

        self.verdict       = v
        self.verdict_reason = r
        self.days_late      = dl

        # Map verdict to uint8
        verdict_map = {
            "ON_TIME":      VERDICT_ON_TIME,
            "LATE_1_4":     VERDICT_LATE_1_4,
            "LATE_5_6":     VERDICT_LATE_5_6,
            "LATE_7_8":     VERDICT_LATE_7_8,
            "VERY_LATE":    VERDICT_VERY_LATE,
            "UNDETERMINED": VERDICT_UNDETERMINED
        }
        verdict_uint8 = verdict_map.get(v, VERDICT_UNDETERMINED)

        # ABI-encode the resolution payload:
        # Inner: (address settlementContract, uint8 verdict, string reason)
        resolution_encoder = genvm_eth.MethodEncoder("", [Address, u8, str], bool)
        resolution_data = resolution_encoder.encode_call(
            [Address(settlement_contract), verdict_uint8, r]
        )[4:]  # strip selector

        # Outer: (address agreementAddress, bytes resolutionData)
        wrapper_encoder = genvm_eth.MethodEncoder("", [Address, bytes], bool)
        message_bytes = wrapper_encoder.encode_call(
            [Address(settlement_contract), resolution_data]
        )[4:]  # strip selector

        # Send via bridge → zkSync Sepolia → LayerZero → Base Sepolia
        bridge = gl.get_contract_at(Address(bridge_sender))
        bridge.emit().send_message(
            int(self.target_chain_eid),
            target_contract,   # ← targets InternetCourtFactory, which dispatches to settlement
            message_bytes
        )

    # ─── Views ───────────────────────────────────────────────────────────────

    @gl.public.view
    def get_verdict(self) -> dict:
        return {
            "case_id":        self.case_id,
            "verdict":        self.verdict,
            "days_late":      self.days_late,
            "verdict_reason": self.verdict_reason,
            "guideline":      self.guideline_version,
        }

    @gl.public.view
    def get_status(self) -> str:
        return self.verdict
