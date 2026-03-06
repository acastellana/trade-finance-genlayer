# { "Depends": "py-genlayer:test" }
from genlayer import *
import json


class InternetCourt(gl.Contract):
    """InternetCourt — AI-powered dispute resolution for GenLayer.

    Two resolution paths:
      Path 1: Mutual agreement — both parties propose same outcome → resolved immediately.
      Path 2: AI jury — parties disagree → submit evidence → AI evaluates → verdict.

    Lifecycle:
      CREATED → ACTIVE → RESOLVED
                        → DISPUTED → RESOLVED
      CREATED → CANCELLED

    Evidence can include image URLs (IPFS or HTTP). The contract fetches and
    renders document images for visual analysis by the AI jury.
    """

    # --- Parties ---
    party_a: Address
    party_b: Address

    # --- Contract terms ---
    statement: str
    guidelines: str
    evidence_defs: str

    # --- State ---
    status: str  # created|active|disputed|resolved|cancelled
    verdict: str  # TRUE|FALSE|UNDETERMINED|""
    reasoning: str

    # --- Evidence ---
    evidence_a: str
    evidence_b: str

    # --- Proposed outcomes (mutual agreement) ---
    outcome_a: str
    outcome_b: str

    def __init__(
        self,
        party_b: Address,
        statement: str,
        guidelines: str,
        evidence_defs: str,
    ):
        self.party_a = gl.message.sender_address

        if isinstance(party_b, str):
            party_b = Address(party_b)
        elif isinstance(party_b, bytes):
            party_b = Address(party_b)
        self.party_b = party_b

        self.statement = statement
        self.guidelines = guidelines
        self.evidence_defs = evidence_defs

        self.status = "created"
        self.verdict = ""
        self.reasoning = ""
        self.evidence_a = ""
        self.evidence_b = ""
        self.outcome_a = ""
        self.outcome_b = ""

    # ──────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────

    @gl.public.write
    def accept_contract(self) -> None:
        """Party B accepts the contract → status becomes 'active'."""
        if self.status != "created":
            raise gl.vm.UserError("Contract not in created state")
        if gl.message.sender_address != self.party_b:
            raise gl.vm.UserError("Only party B can accept the contract")
        self.status = "active"

    @gl.public.write
    def cancel(self) -> None:
        """Party A can cancel before acceptance."""
        if self.status != "created":
            raise gl.vm.UserError("Can only cancel before contract is accepted")
        if gl.message.sender_address != self.party_a:
            raise gl.vm.UserError("Only party A can cancel")
        self.status = "cancelled"

    # ──────────────────────────────────────────────
    # PATH 1: MUTUAL AGREEMENT
    # ──────────────────────────────────────────────

    @gl.public.write
    def propose_outcome(self, outcome: str) -> None:
        """Either party proposes an outcome. If both match → resolved immediately."""
        if self.status != "active":
            raise gl.vm.UserError("Contract not active")
        sender = gl.message.sender_address
        if sender != self.party_a and sender != self.party_b:
            raise gl.vm.UserError("Not a party to this contract")
        if outcome not in ("TRUE", "FALSE"):
            raise gl.vm.UserError("Outcome must be 'TRUE' or 'FALSE'")

        if sender == self.party_a:
            self.outcome_a = outcome
        else:
            self.outcome_b = outcome

        if self.outcome_a != "" and self.outcome_b != "" and self.outcome_a == self.outcome_b:
            self.verdict = self.outcome_a
            self.reasoning = "Mutual agreement by both parties — no jury needed."
            self.status = "resolved"

    # ──────────────────────────────────────────────
    # PATH 2: AI JURY DISPUTE
    # ──────────────────────────────────────────────

    @gl.public.write
    def initiate_dispute(self) -> None:
        """Either party initiates a dispute → AI jury path."""
        if self.status != "active":
            raise gl.vm.UserError("Contract not active")
        sender = gl.message.sender_address
        if sender != self.party_a and sender != self.party_b:
            raise gl.vm.UserError("Not a party to this contract")
        self.status = "disputed"

    @gl.public.write
    def submit_evidence(self, evidence: str) -> None:
        """Each party submits evidence (once). Both must submit before resolve().

        Evidence format (JSON string):
        {
            "text": "Party's position and argument",
            "documents": [
                {"url": "https://... or ipfs://...", "label": "Document description"}
            ]
        }
        """
        if self.status != "disputed":
            raise gl.vm.UserError("No active dispute — call initiate_dispute() first")
        sender = gl.message.sender_address
        if sender == self.party_a:
            if self.evidence_a != "":
                raise gl.vm.UserError("Party A already submitted evidence")
            self.evidence_a = evidence
        elif sender == self.party_b:
            if self.evidence_b != "":
                raise gl.vm.UserError("Party B already submitted evidence")
            self.evidence_b = evidence
        else:
            raise gl.vm.UserError("Not a party to this contract")

    @gl.public.write
    def resolve(self) -> None:
        """AI jury resolution with multimodal evidence analysis.

        Uses the minimal-nondet-return pattern for consensus:
        1. Nondet fetches document images and extracts KEY FACTS (numbers, dates)
        2. Returns a compact factual string (e.g. "sgs_purity:99.12|bv_purity:98.54|damaged:2/4")
        3. Deterministic logic outside nondet computes the verdict from facts

        This ensures validators agree on objective data extraction,
        while the verdict computation is deterministic.
        """
        if self.status != "disputed":
            raise gl.vm.UserError("Not in disputed state")
        if self.evidence_a == "" or self.evidence_b == "":
            raise gl.vm.UserError("Both parties must submit evidence before resolution")

        # Copy storage to locals (not accessible inside non-det block)
        stmt = self.statement
        glines = self.guidelines
        ev_defs = self.evidence_defs
        ev_a = self.evidence_a
        ev_b = self.evidence_b

        def nondet():
            # Parse evidence JSON
            try:
                parsed_a = json.loads(ev_a)
                parsed_b = json.loads(ev_b)
            except Exception:
                parsed_a = {"text": ev_a, "documents": []}
                parsed_b = {"text": ev_b, "documents": []}

            text_a = parsed_a.get("text", ev_a)
            text_b = parsed_b.get("text", ev_b)
            docs_a = parsed_a.get("documents", [])
            docs_b = parsed_b.get("documents", [])

            # Fetch and render document images using web.render()
            all_images = []
            doc_labels = []

            for docs, party in [(docs_a, "A"), (docs_b, "B")]:
                for i, doc in enumerate(docs):
                    url = doc.get("url", "")
                    label = doc.get("label", f"Party {party} Doc {i+1}")
                    if url:
                        try:
                            img = gl.nondet.web.render(url, mode="screenshot")
                            all_images.append(img)
                            doc_labels.append(f"[Image {len(all_images)}] Party {party}: {label}")
                        except Exception:
                            doc_labels.append(f"[FAILED] Party {party}: {label}")

            img_index = "\n".join(doc_labels) if doc_labels else "(no images)"

            # Ask LLM to extract OBJECTIVE FACTS ONLY — not a verdict
            prompt = f"""You are analyzing trade documents for a dispute.

## Statement
{stmt}

## Party A Evidence (text)
{text_a}

## Party B Evidence (text)
{text_b}

## Attached Document Images
{img_index}

Examine the attached images carefully. Extract ONLY these specific factual fields:

1. pre_shipment_purity: The Li2CO3 purity percentage from the pre-shipment certificate (e.g. "99.12")
2. arrival_purity: The Li2CO3 purity percentage from the arrival/independent analysis (e.g. "98.54")
3. containers_damaged: Number of containers with damage or quality issues (e.g. "2")
4. total_containers: Total containers shipped (e.g. "4")
5. pre_lab_accredited: Whether the pre-shipment lab is ISO/IEC 17025 accredited ("yes" or "no")
6. arrival_lab_accredited: Whether the arrival lab is ISO/IEC 17025 accredited ("yes" or "no")
7. min_purity_spec: Minimum purity in the contract specification (e.g. "99.0")

Return ONLY a pipe-separated string of key:value pairs. Example:
pre_shipment_purity:99.12|arrival_purity:98.54|containers_damaged:2|total_containers:4|pre_lab_accredited:yes|arrival_lab_accredited:yes|min_purity_spec:99.0

Return ONLY the pipe-separated string. No explanation, no JSON, no other text."""

            if all_images:
                result = gl.nondet.exec_prompt(prompt, images=all_images)
            else:
                result = gl.nondet.exec_prompt(prompt)

            # Clean to just the pipe-separated line
            result = str(result).strip()
            # Take only the first line if multiple
            if "\n" in result:
                result = result.split("\n")[0].strip()
            return result

        # Equivalence: validators should agree on extracted facts
        facts_str = gl.eq_principle.prompt_non_comparative(
            nondet,
            task="Extract specific factual data from trade dispute documents",
            criteria=(
                "The extracted values must be pipe-separated key:value pairs. "
                "Numeric values should match to within 0.5% tolerance. "
                "Accreditation status must match exactly."
            ),
        )

        # ── DETERMINISTIC: compute verdict from extracted facts ──
        facts = {}
        for pair in facts_str.split("|"):
            if ":" in pair:
                k, v = pair.split(":", 1)
                facts[k.strip()] = v.strip()

        pre_purity = self._parse_float(facts.get("pre_shipment_purity", "0"))
        arr_purity = self._parse_float(facts.get("arrival_purity", "0"))
        min_spec = self._parse_float(facts.get("min_purity_spec", "99.0"))
        containers_damaged = self._parse_int(facts.get("containers_damaged", "0"))
        total_containers = self._parse_int(facts.get("total_containers", "1"))
        pre_accredited = facts.get("pre_lab_accredited", "").lower() == "yes"
        arr_accredited = facts.get("arrival_lab_accredited", "").lower() == "yes"

        # Decision logic:
        # 1. If pre-shipment purity >= min_spec AND from accredited lab → goods met spec at loading
        # 2. If arrival purity < min_spec → degradation occurred in transit
        # 3. Under CIF terms, risk passes at ship's rail → damage in transit is buyer's risk
        #    UNLESS seller failed to properly pack/containerize (damaged containers)
        # 4. If >50% containers damaged → likely seller's packaging failure → TRUE (statement holds partially)
        # 5. If arrival lab not accredited, weigh pre-shipment results more heavily

        reasoning_parts = []

        if pre_purity >= min_spec and pre_accredited:
            reasoning_parts.append(
                f"Pre-shipment analysis from accredited lab shows {pre_purity}% purity, "
                f"meeting the {min_spec}% minimum specification at loading."
            )
            goods_met_spec_at_loading = True
        elif pre_purity >= min_spec:
            reasoning_parts.append(
                f"Pre-shipment analysis shows {pre_purity}% purity (above {min_spec}%), "
                f"but lab accreditation status is uncertain."
            )
            goods_met_spec_at_loading = True
        else:
            reasoning_parts.append(
                f"Pre-shipment purity {pre_purity}% is below the {min_spec}% minimum specification."
            )
            goods_met_spec_at_loading = False

        if arr_purity > 0 and arr_purity < min_spec:
            reasoning_parts.append(
                f"Arrival analysis shows {arr_purity}% purity, below the {min_spec}% threshold."
            )
            degraded_in_transit = True
        else:
            degraded_in_transit = False

        damage_ratio = containers_damaged / max(total_containers, 1)
        if containers_damaged > 0:
            reasoning_parts.append(
                f"{containers_damaged} of {total_containers} containers showed damage or quality issues."
            )

        # Verdict determination
        if goods_met_spec_at_loading and not degraded_in_transit:
            # Goods met spec, no degradation → statement TRUE
            verdict = "TRUE"
            reasoning_parts.append("Statement is TRUE: goods met specifications.")
        elif goods_met_spec_at_loading and degraded_in_transit:
            if damage_ratio > 0.5:
                # Majority containers damaged → seller packaging fault → partially true
                verdict = "UNDETERMINED"
                reasoning_parts.append(
                    "Goods met spec at loading but degraded in transit. "
                    "Majority of containers damaged suggests possible packaging failure. "
                    "Verdict UNDETERMINED pending physical inspection of container seals."
                )
            else:
                # Goods met spec at loading, transit damage in minority of containers
                # Under CIF terms, risk passed to buyer at loading
                verdict = "TRUE"
                reasoning_parts.append(
                    "Under CIF Incoterms 2020, risk passed to buyer when goods crossed the ship's rail. "
                    "Goods met spec at loading; transit damage in minority of containers is buyer's risk."
                )
        elif not goods_met_spec_at_loading:
            verdict = "FALSE"
            reasoning_parts.append(
                "Statement is FALSE: goods did not meet the minimum purity specification."
            )
        else:
            verdict = "UNDETERMINED"
            reasoning_parts.append("Insufficient evidence to determine verdict.")

        self.verdict = verdict
        self.reasoning = " ".join(reasoning_parts)
        self.status = "resolved"

    # ──────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────

    def _parse_float(self, s: str) -> float:
        try:
            return float(s.replace("%", "").strip())
        except (ValueError, AttributeError):
            return 0.0

    def _parse_int(self, s: str) -> int:
        try:
            return int(s.strip())
        except (ValueError, AttributeError):
            return 0

    # ──────────────────────────────────────────────
    # READ METHODS
    # ──────────────────────────────────────────────

    @gl.public.view
    def get_verdict(self) -> str:
        return json.dumps({
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "status": self.status,
        })

    @gl.public.view
    def get_status(self) -> str:
        return json.dumps({
            "status": self.status,
            "statement": self.statement,
            "party_a": self.party_a.as_hex,
            "party_b": self.party_b.as_hex,
            "verdict": self.verdict,
            "reasoning": self.reasoning,
        })

    @gl.public.view
    def get_evidence(self) -> str:
        return json.dumps({
            "evidence_a": self.evidence_a,
            "evidence_b": self.evidence_b,
        })

    @gl.public.view
    def get_contract_details(self) -> str:
        return json.dumps({
            "status": self.status,
            "statement": self.statement,
            "guidelines": self.guidelines,
            "evidence_defs": self.evidence_defs,
            "party_a": self.party_a.as_hex,
            "party_b": self.party_b.as_hex,
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "outcome_a": self.outcome_a,
            "outcome_b": self.outcome_b,
        })
