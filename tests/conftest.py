"""
conftest.py — Shared fixtures for trade finance GenLayer tests.

Key patterns:
- MUST patch prompt_non_comparative after direct_deploy loads the SDK
- Import genlayer types AFTER calling direct_deploy (SDK lazy-loaded)
- Use direct_vm.prank() to impersonate different senders
- Use direct_vm.mock_llm() to mock AI validator responses
"""
import json
import pytest
from pathlib import Path


def _ensure_py_genlayer_test_runner_alias() -> None:
    """Ensure contracts using header `py-genlayer:test` can be loaded in gltest-direct.

    gltest-direct treats the dependency string after `py-genlayer:` as a runner hash
    inside the cached `genvm-universal-<version>.tar.xz` artifact.

    Some local caches don't ship a runner hash literally named `test`. To keep
    contract headers aligned with the reference repos (`py-genlayer:test`) *and*
    still allow local direct-mode tests to run, we create an alias directory:

        ~/.cache/gltest-direct/extracted/<version>/py-genlayer/test

    pointing at an already-extracted runner.
    """
    try:
        from gltest.direct import sdk_loader
    except Exception:
        return

    # Ensure we have at least one cached version and runner extracted.
    cached = []
    try:
        cached = sdk_loader.list_cached_versions()
    except Exception:
        cached = []

    version = cached[0] if cached else sdk_loader.get_latest_version()

    try:
        tarball = sdk_loader.download_artifacts(version)
        runner_dir = sdk_loader.extract_runner(tarball, sdk_loader.RUNNER_TYPE, None, version)
    except Exception:
        return

    extract_base = sdk_loader.CACHE_DIR / "extracted" / version / sdk_loader.RUNNER_TYPE
    test_dir = extract_base / "test"
    if test_dir.exists():
        return

    try:
        test_dir.parent.mkdir(parents=True, exist_ok=True)
        test_dir.symlink_to(runner_dir, target_is_directory=True)
    except Exception:
        # Fallback: copy
        import shutil
        try:
            shutil.copytree(runner_dir, test_dir)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Test addresses (raw bytes — used with direct_vm in Python test framework)
# ──────────────────────────────────────────────────────────────────────────────

# Bolivian exporter (Minera Andina SRL)
EXPORTER_BYTES = b'\x01' * 20
# Peruvian importer
IMPORTER_BYTES = b'\x02' * 20
# Unauthorized third party
STRANGER_BYTES = b'\x03' * 20
# Mock InternetCourt contract address (used as a hex string)
MOCK_COURT_ADDRESS = "0x" + "ab" * 20

# Default deal parameters (Bolivia → Peru battery-grade lithium carbonate)
DEFAULT_GOODS = "Battery-grade lithium carbonate (Li2CO3), 50 metric tons, ISO 6206 certified, INCOTERMS CIF Callao port"
DEFAULT_INVOICE_CURRENCY = "BOB"
DEFAULT_SETTLEMENT_CURRENCY = "PEN"
DEFAULT_INVOICE_AMOUNT = "500000"
DEFAULT_ESTIMATED_RATE = "0.40"
DEFAULT_RATE_TOLERANCE_BPS = 100
DEFAULT_DELIVERY_DEADLINE = "2026-04-30T00:00:00+00:00"

# Token amounts (as strings, no decimals — test env uses plain integers)
MINT_BOB_AMOUNT = "500000"     # 500,000 sBOB
MINT_PEN_AMOUNT = "100000"     # 100,000 sPEN
ESCROW_PEN_AMOUNT = "200000"   # 200,000 PEN (500k BOB × 0.40)

# Mock forex rates for testing
MOCK_SETTLEMENT_RATE = "0.412"
MOCK_FINAL_AMOUNT = "206000.0"
MOCK_DEVIATION_BPS = "30"

# Mock settlement LLM response
MOCK_SETTLEMENT_RESPONSE = json.dumps({
    "rate": 0.412,
    "source": "Mock forex API",
    "final_amount": 206000.0,
    "within_tolerance": True,
    "rate_deviation_bps": 30,
})

# Mock InternetCourt verdict responses
MOCK_IC_VERDICT_TRUE = json.dumps({
    "verdict": "TRUE",
    "reasoning": "Exporter's SGS Chile certificate confirms 99.1% Li2CO3 purity, meeting ISO 6206 minimum. Delivery weight within tolerance.",
    "status": "resolved",
})

MOCK_IC_VERDICT_FALSE = json.dumps({
    "verdict": "FALSE",
    "reasoning": "Bureau Veritas Peru's independent analysis (98.2% purity) contradicts exporter's certificate. Goods below ISO 6206 standard.",
    "status": "resolved",
})

MOCK_IC_VERDICT_UNDETERMINED = json.dumps({
    "verdict": "UNDETERMINED",
    "reasoning": "Conflicting laboratory analyses from both parties without sufficient chain-of-custody documentation.",
    "status": "resolved",
})

# Shipment proof data
SAMPLE_SHIPMENT_PROOF = json.dumps({
    "type": "bill_of_lading",
    "bl_number": "TEST-BOL-2026-001",
    "vessel": "MV Test Ship",
    "port_of_loading": "Antofagasta, Chile",
    "port_of_discharge": "Callao, Peru",
    "goods": "Battery-grade lithium carbonate (Li2CO3), ISO 6206 certified",
    "weight": "50 metric tons",
    "container_ids": ["TEST-U-123456-7"],
    "certificates": ["ISO 6206 — lot TEST-LI50"],
})

# Delivery confirmation
SAMPLE_DELIVERY_CONFIRMATION = json.dumps({
    "confirmed": True,
    "inspector": "SGS Peru S.A.",
    "findings": "50 metric tons battery-grade Li2CO3 received. ISO 6206 purity confirmed: 99.1%.",
    "quantity_received": "50 metric tons",
})


# ──────────────────────────────────────────────────────────────────────────────
# SDK Patch
# ──────────────────────────────────────────────────────────────────────────────

def _patch_prompt_non_comparative():
    """Patch prompt_non_comparative to use strict_eq in direct test mode.

    prompt_non_comparative uses ExecPromptTemplate gl_calls internally,
    which the direct test WASI mock doesn't handle. Since tests mock LLM
    responses to return identical results anyway, strict_eq gives the same
    behavior. On studionet, the real prompt_non_comparative is used.

    MUST be called AFTER direct_deploy() has loaded the genlayer SDK.
    """
    import genlayer.gl.eq_principle as eq_mod
    import genlayer.gl.vm as vm_mod
    from genlayer.gl._internal import _lazy_api
    from genlayer.py.types import Lazy
    import typing

    @_lazy_api
    def patched_prompt_non_comparative(
        fn: typing.Callable[[], str], *, task: str, criteria: str
    ) -> Lazy[str]:
        # In direct test mode, run through run_nondet_unsafe which is already
        # patched by gltest to call leader_fn() inside the nondet context
        # (enabling exec_prompt mocks to work). The validator is a no-op.
        def validator_fn(leaders_res) -> bool:
            return True
        result = vm_mod.run_nondet_unsafe(fn, validator_fn)
        return Lazy(lambda: result)

    eq_mod.prompt_non_comparative = patched_prompt_non_comparative
    import genlayer.gl as gl_mod
    gl_mod.eq_principle.prompt_non_comparative = patched_prompt_non_comparative


# ──────────────────────────────────────────────────────────────────────────────
# Core Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def deploy_stablecoin(direct_vm, direct_deploy):
    """Deploy a StableCoin contract (sBOB) with exporter as deployer."""
    _ensure_py_genlayer_test_runner_alias()
    direct_vm.sender = EXPORTER_BYTES
    contract = direct_deploy("contracts/StableCoin.py", "Synthetic Boliviano", "sBOB", 18)
    _patch_prompt_non_comparative()
    from genlayer import Address
    exporter = Address(EXPORTER_BYTES)
    importer = Address(IMPORTER_BYTES)
    stranger = Address(STRANGER_BYTES)
    return contract, exporter, importer, stranger


@pytest.fixture
def deploy_spen(direct_vm, direct_deploy):
    """Deploy a sPEN StableCoin with exporter as deployer."""
    _ensure_py_genlayer_test_runner_alias()
    direct_vm.sender = EXPORTER_BYTES
    contract = direct_deploy("contracts/StableCoin.py", "Synthetic Sol", "sPEN", 18)
    _patch_prompt_non_comparative()
    from genlayer import Address
    exporter = Address(EXPORTER_BYTES)
    importer = Address(IMPORTER_BYTES)
    return contract, exporter, importer


@pytest.fixture
def deploy_deal(direct_vm, direct_deploy):
    """Deploy TradeFinanceDeal with Bolivia→Peru defaults."""
    _ensure_py_genlayer_test_runner_alias()
    direct_vm.sender = EXPORTER_BYTES
    contract = direct_deploy(
        "contracts/TradeFinanceDeal.py",
        IMPORTER_BYTES,
        DEFAULT_GOODS,
        DEFAULT_INVOICE_CURRENCY,
        DEFAULT_SETTLEMENT_CURRENCY,
        DEFAULT_INVOICE_AMOUNT,
        DEFAULT_ESTIMATED_RATE,
        DEFAULT_RATE_TOLERANCE_BPS,
        DEFAULT_DELIVERY_DEADLINE,
    )
    _patch_prompt_non_comparative()
    from genlayer import Address
    exporter = Address(EXPORTER_BYTES)
    importer = Address(IMPORTER_BYTES)
    stranger = Address(STRANGER_BYTES)
    return contract, exporter, importer, stranger


@pytest.fixture
def deploy_internet_court(direct_vm, direct_deploy):
    """Deploy an InternetCourt contract with exporter as party A, importer as party B."""
    _ensure_py_genlayer_test_runner_alias()
    from genlayer import Address
    direct_vm.sender = EXPORTER_BYTES
    statement = (
        "Minera Andina SRL delivered 50 metric tons of battery-grade lithium carbonate "
        "meeting ISO 6206 purity standards to Lima port by the agreed deadline"
    )
    guidelines = (
        "Evaluate shipping documentation, quality certificates, customs clearance records, "
        "and port inspection reports. The delivery is valid if: (1) quantity matches within "
        "2% tolerance, (2) purity meets ISO 6206 minimum 99.0% Li2CO3, (3) delivery timestamp "
        "is before the deadline, (4) customs clearance was completed."
    )
    evidence_defs = json.dumps({
        "party_a": {"max_chars": 10000, "description": "Shipping docs, quality certs, customs records"},
        "party_b": {"max_chars": 10000, "description": "Inspection reports, rejection notices, quality analysis"},
    })
    contract = direct_deploy(
        "contracts/InternetCourt.py",
        IMPORTER_BYTES,
        statement,
        guidelines,
        evidence_defs,
    )
    _patch_prompt_non_comparative()
    exporter = Address(EXPORTER_BYTES)
    importer = Address(IMPORTER_BYTES)
    stranger = Address(STRANGER_BYTES)
    return contract, exporter, importer, stranger


@pytest.fixture
def funded_deal(deploy_deal, direct_vm):
    """Deal in 'funded' state — importer has funded escrow."""
    contract, exporter, importer, stranger = deploy_deal
    with direct_vm.prank(importer):
        contract.fund_escrow(ESCROW_PEN_AMOUNT)
    return contract, exporter, importer, stranger


@pytest.fixture
def shipped_deal(funded_deal, direct_vm):
    """Deal in 'shipped' state — exporter has submitted shipment proof."""
    contract, exporter, importer, stranger = funded_deal
    with direct_vm.prank(exporter):
        contract.submit_shipment(SAMPLE_SHIPMENT_PROOF)
    return contract, exporter, importer, stranger


@pytest.fixture
def delivered_deal(shipped_deal, direct_vm):
    """Deal in 'delivered' state — importer has confirmed delivery."""
    contract, exporter, importer, stranger = shipped_deal
    with direct_vm.prank(importer):
        contract.confirm_delivery(SAMPLE_DELIVERY_CONFIRMATION)
    return contract, exporter, importer, stranger


@pytest.fixture
def disputed_deal(shipped_deal, direct_vm):
    """Deal in 'disputed' state — importer raised a dispute."""
    contract, exporter, importer, stranger = shipped_deal
    with direct_vm.prank(importer):
        contract.raise_dispute("Purity analysis shows 98.2% Li2CO3 — below ISO 6206 minimum of 99.0%")
    return contract, exporter, importer, stranger


@pytest.fixture
def court_linked_deal(disputed_deal, direct_vm):
    """Deal in 'disputed' state with an InternetCourt address linked."""
    contract, exporter, importer, stranger = disputed_deal
    with direct_vm.prank(exporter):
        contract.link_court_case(MOCK_COURT_ADDRESS)
    return contract, exporter, importer, stranger


@pytest.fixture
def active_internet_court(deploy_internet_court, direct_vm):
    """InternetCourt in 'active' state — party B has accepted."""
    contract, exporter, importer, stranger = deploy_internet_court
    with direct_vm.prank(importer):
        contract.accept_contract()
    return contract, exporter, importer, stranger


@pytest.fixture
def disputed_internet_court(active_internet_court, direct_vm):
    """InternetCourt in 'disputed' state — both submitted evidence."""
    contract, exporter, importer, stranger = active_internet_court
    with direct_vm.prank(exporter):
        contract.initiate_dispute()
    exporter_evidence = json.dumps({
        "certificate": "SGS Chile lab: 99.1% Li2CO3 — ISO 6206 PASS",
        "bl_number": "TEST-BOL-2026-001",
    })
    importer_evidence = json.dumps({
        "inspection": "Bureau Veritas Peru: 98.2% Li2CO3 — ISO 6206 FAIL",
        "rejection_notice": "RN-2026-CAL-0472",
    })
    with direct_vm.prank(exporter):
        contract.submit_evidence(exporter_evidence)
    with direct_vm.prank(importer):
        contract.submit_evidence(importer_evidence)
    return contract, exporter, importer, stranger
