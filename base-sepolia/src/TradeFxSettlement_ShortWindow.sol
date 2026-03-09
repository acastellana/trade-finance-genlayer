// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

// InternetCourt integration
interface IInternetCourtFactory {
    function registerCase() external returns (uint256 id);
}

interface IResolutionTarget {
    function setResolution(uint8 verdict, string calldata reasoning) external;
    function getOracleType() external view returns (bytes32);
    function getOracleArgs() external view returns (bytes memory);
}

/**
 * @title TradeFxSettlement v3
 * @notice Benchmark-based FX settlement engine with token settlement rail.
 *
 * Lifecycle:
 *   DRAFT → RATE_PENDING → RATE_LOCKED → FUNDED → SETTLED
 *                                ↓
 *                         ROLL_PENDING → ROLLED → FUNDED → SETTLED
 *
 *   Any non-SETTLED state → CANCELLED (with refund if funded)
 *
 * Shipment check (inline exception branch):
 *   FUNDED → acceptShipment() → settle()
 *   FUNDED → contestShipment() → [InternetCourt evaluates] →
 *     resolveShipmentVerdict(1: ON_TIME)    → finalizeAfterShipment() (100% to exporter)
 *     resolveShipmentVerdict(2-4: LATE_X)   → auto-settle with penalty
 *     resolveShipmentVerdict(5: VERY_LATE) → enter RETURN_REQUIRED state
 *     resolveShipmentVerdict(6: UNDET)      → resolveManualReview(arbitrator)
 *
 * Contest deadline:
 *   contestShipment() reverts after contestDeadline (fundSettlement time + 7 days).
 *   acceptShipment() is callable by anyone (not just importer) after contestDeadline,
 *   allowing either party to unblock settlement if the importer does not act.
 *
 * Verdict source:
 *   resolveShipmentVerdict() is callable by courtContract (the deployed GenLayer
 *   ShipmentDeadlineCourt address, relayed by the oracle relayer) OR by oracleRelayer
 *   directly (testnet fallback). Use setCourtContract() to wire the production court.
 *
 * Manual review escape hatch:
 *   When verdict is UNDETERMINED, the designated arbitrator calls
 *   resolveManualReview(timeliness, reason) to deliver a binding human finding.
 *   If contestDeadline + MANUAL_REVIEW_WINDOW elapses, anyone can call
 *   timeoutManualReview() to default to LATE (importer bears evidence burden).
 */
contract TradeFxSettlement {
    using SafeERC20 for IERC20;

    uint256 public constant MANUAL_REVIEW_WINDOW = 14 days;

    // ─── Status ───────────────────────────────────────────────────────────────

    enum Status {
        DRAFT,          // 0
        RATE_PENDING,   // 1
        RATE_LOCKED,    // 2
        FUNDED,         // 3
        ROLL_PENDING,   // 4
        ROLLED,         // 5
        SETTLED,        // 6
        CANCELLED       // 7
    }

    // ─── Data structures ─────────────────────────────────────────────────────

    struct RateInfo {
        uint256 rate;
        bytes32 benchmarkType;
        bytes32 benchmarkId;
        uint256 asOfTimestamp;
    }

    struct RollRecord {
        uint256 priorRate;
        uint256 rolledRate;
        uint256 rollCost;
        uint256 priorDueDate;
        uint256 newDueDate;
        bytes32 benchmarkId;
        uint256 asOfTimestamp;
    }

    // ─── Roles ───────────────────────────────────────────────────────────────

    address public exporter;
    address public importer;
    address public oracleRelayer;
    address public admin;

    /// @notice Address of the deployed GenLayer ShipmentDeadlineCourt contract.
    ///         Set via setCourtContract(). When non-zero, resolveShipmentVerdict
    ///         accepts calls from this address. The relayer remains a fallback for testnet.
    address public courtContract;

    /// @notice InternetCourt BridgeReceiver address on Base Sepolia.
    ///         Authorised to call processBridgeMessage() with LayerZero-delivered verdicts.
    ///         Set once at construction; immutable after that.
    address public bridgeReceiver;

    /// @notice Designated human arbitrator for UNDETERMINED verdicts.
    ///         Defaults to admin. Can be updated via setArbitrator().
    address public arbitrator;

    // ─── Settlement token ─────────────────────────────────────────────────────

    IERC20 public settlementToken;

    // ─── Invoice ─────────────────────────────────────────────────────────────

    uint256 public invoiceAmount;
    bytes32 public sourceCurrency;
    bytes32 public settlementCurrency;
    string  public invoiceRef;

    // ─── Rate & settlement ───────────────────────────────────────────────────

    RateInfo public lockedRate;
    uint256  public settlementAmount;

    // ─── Notional ────────────────────────────────────────────────────────────

    uint256 public currentNotional;
    uint256 public fulfilledBps;

    // ─── Funding ─────────────────────────────────────────────────────────────

    uint256 public fundedAmount;

    // ─── Timing ──────────────────────────────────────────────────────────────

    uint256 public currentDueDate;

    /// @notice Deadline after which contestShipment() is rejected.
    ///         Set to shipmentCheckTime + 7 days when fundSettlement() is called.
    ///         After this deadline, anyone (not just the importer) can call acceptShipment().
    uint256 public contestDeadline;

    // ─── Roll history ────────────────────────────────────────────────────────

    uint256 public rollCount;
    RollRecord[] private _rolls;
    uint256 private _pendingNewDueDate;

    // ─── Status ──────────────────────────────────────────────────────────────

    Status public status;

    // ─── Exception side channel ──────────────────────────────────────────────

    bool    public exceptionFlagged;
    bool    public exceptionPaused;
    uint8   public exceptionReasonCode;
    bytes32 public exceptionRef;

    // ─── Shipment dispute ─────────────────────────────────────────────────────

    enum ShipmentStatus {
        NONE,           // 0 — not yet reviewed
        ACCEPTED,       // 1 — accepted as timely (importer or auto-accept after deadline)
        CONTESTED,      // 2 — court case in progress
        TIMELY,         // 3 — verdict TRUE
        LATE,           // 4 — verdict FALSE
        UNDETERMINED,   // 5 — insufficient evidence; escalated to arbitrator
        RETURN_REQUIRED // 6 — VERY_LATE; buyer must submit return proof
    }

    ShipmentStatus public shipmentStatus;
    string  public shipmentCaseId;
    string  public shipmentManifestCid;
    string  public shipmentGuidelineVersion;
    string  public shipmentStatement;
    string  public shipmentCourtSheetACid;  // IPFS CID — exporter evidence
    string  public shipmentCourtSheetBCid;  // IPFS CID — importer evidence
    uint256 public shipmentVerdictAt;
    string  public shipmentVerdictReason;
    bool    public shipmentReviewRequired;

    uint256 public constant RETURN_PROOF_WINDOW = 60; // 60 seconds for demo
    uint256 public returnProofDeadline;
    string  public returnProofSheetACid;
    string  public returnProofSheetBCid;

    // ─── InternetCourt integration ────────────────────────────────────────────

    /// @notice InternetCourtFactory address. Set at construction; zero = IC disabled.
    address public courtFactory;

    /// @notice Oracle type identifier for the relay's ORACLE_REGISTRY.
    bytes32 public constant ORACLE_TYPE = keccak256("TRADE_FINANCE_V1");

    /// @notice IC case ID assigned by the factory when registerCase() is called.
    uint256 public icCaseId;

    // ─── Events ──────────────────────────────────────────────────────────────

    event TradeCreated(address indexed exporter, address indexed importer,
        uint256 invoiceAmount, uint256 dueDate, string invoiceRef);
    event RateLockRequested(address indexed requester, uint256 timestamp);
    event RateLocked(uint256 rate, bytes32 benchmarkType, bytes32 benchmarkId,
        uint256 asOfTimestamp, uint256 settlementAmount);
    event NotionalResized(uint256 oldNotional, uint256 newNotional,
        uint256 newSettlementAmount, uint256 fulfilledBps);
    event RollRequested(address indexed requester,
        uint256 currentDueDate, uint256 requestedNewDueDate, uint256 timestamp);
    event RateRolled(uint256 priorRate, uint256 rolledRate, uint256 rollCost,
        uint256 oldDueDate, uint256 newDueDate, bytes32 benchmarkId, uint256 asOfTimestamp);
    event Funded(address indexed funder, uint256 amount, uint256 timestamp);
    event Settled(address indexed exporter, uint256 amount, uint256 timestamp);
    event Cancelled(uint8 reasonCode, address indexed by,
        uint256 refundAmount, address indexed refundTo);
    event ExceptionFlagged(uint8 reasonCode, bytes32 evidenceRef, address indexed flagger);
    event ExceptionPauseSet(bool paused, address indexed by);

    // Shipment dispute events
    event ShipmentAccepted(address indexed by, bool afterDeadline, uint256 timestamp);
    event ShipmentContested(address indexed contestant, string manifestCid,
        string statement, uint256 contestDeadline, uint256 timestamp);
    event ShipmentVerdictReceived(uint8 verdict, string caseId, string reasonSummary,
        address indexed deliveredBy, bool fromCourtContract, uint256 timestamp);
    event SettlementCancelledByVerdict(address indexed importer, uint256 refundAmount);
    event SettlementManualReview(string caseId, uint256 reviewDeadline, uint256 timestamp);
    event ManualReviewResolved(address indexed arbitrator, bool timeliness,
        string reason, uint256 timestamp);
    event ManualReviewTimedOut(address indexed caller, uint256 timestamp);
    event CourtContractSet(address indexed courtContract, address indexed by);
    event ArbitratorSet(address indexed arbitrator, address indexed by);

    event ShipmentSettledWithPenalty(address indexed exporter, address indexed importer,
        uint256 exporterAmount, uint256 importerAmount, uint256 penaltyBps);
    event ReturnRequired(uint256 deadline, uint256 timestamp);
    event ReturnProofSubmitted(address indexed importer, string sheetACid, string sheetBCid, uint256 timestamp);
    event ReturnProofTimeout(address indexed caller, uint256 timestamp);

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlyExporter()  { require(msg.sender == exporter,      "TFX: not exporter");  _; }
    modifier onlyImporter()  { require(msg.sender == importer,      "TFX: not importer");  _; }
    modifier onlyRelayer()   { require(msg.sender == oracleRelayer, "TFX: not relayer");   _; }
    modifier onlyAdmin()     { require(msg.sender == admin,         "TFX: not admin");     _; }
    modifier onlyArbitrator(){ require(msg.sender == arbitrator,    "TFX: not arbitrator"); _; }

    modifier onlyParty() {
        require(msg.sender == exporter || msg.sender == importer, "TFX: not a party");
        _;
    }
    modifier onlyAuthorized() {
        require(
            msg.sender == exporter || msg.sender == importer || msg.sender == admin,
            "TFX: not authorized"
        );
        _;
    }

    /// @dev Verdict delivery: accept from the registered GenLayer court contract
    ///      OR from the oracle relayer (testnet fallback when court not yet wired).
    modifier onlyVerdictSource() {
        require(
            msg.sender == oracleRelayer ||
            (courtContract != address(0) && msg.sender == courtContract),
            "TFX: not verdict source"
        );
        _;
    }

    modifier notPaused()   { require(!exceptionPaused, "TFX: paused by exception"); _; }
    modifier notTerminal() {
        require(status != Status.SETTLED && status != Status.CANCELLED, "TFX: terminal state");
        _;
    }
    modifier inStatus(Status s) { require(status == s, "TFX: invalid state"); _; }

    function _inStatus(Status s) internal view {
        require(status == s, "TFX: invalid state");
    }

    // ─── Constructor ─────────────────────────────────────────────────────────

    constructor(
        address _exporter,
        address _importer,
        address _oracleRelayer,
        address _admin,
        address _settlementToken,
        uint256 _invoiceAmount,
        bytes32 _sourceCurrency,
        bytes32 _settlementCurrency,
        uint256 _dueDate,
        string memory _invoiceRef,
        address _bridgeReceiver,
        address _courtFactory
    ) {
        require(_exporter != address(0),        "TFX: zero exporter");
        require(_importer != address(0),        "TFX: zero importer");
        require(_oracleRelayer != address(0),   "TFX: zero relayer");
        require(_settlementToken != address(0), "TFX: zero token");
        require(_invoiceAmount > 0,             "TFX: zero invoice");
        require(_dueDate > block.timestamp,     "TFX: due date in past");

        exporter           = _exporter;
        importer           = _importer;
        oracleRelayer      = _oracleRelayer;
        admin              = _admin != address(0) ? _admin : msg.sender;
        arbitrator         = _admin != address(0) ? _admin : msg.sender;
        settlementToken    = IERC20(_settlementToken);

        invoiceAmount      = _invoiceAmount;
        currentNotional    = _invoiceAmount;
        sourceCurrency     = _sourceCurrency;
        settlementCurrency = _settlementCurrency;
        currentDueDate     = _dueDate;
        invoiceRef         = _invoiceRef;
        fulfilledBps       = 10_000;
        status             = Status.DRAFT;

        // Contest window is anchored to shipment check (funding time) + 7 days.
        // Set to max-uint initially; updated to block.timestamp + 7 days when importer funds.
        contestDeadline    = type(uint256).max;

        bridgeReceiver     = _bridgeReceiver; // may be address(0) for relayer-only mode
        courtFactory       = _courtFactory;   // InternetCourtFactory; zero = IC disabled

        emit TradeCreated(_exporter, _importer, _invoiceAmount, _dueDate, _invoiceRef);
    }

    // ─── Admin setters ────────────────────────────────────────────────────────

    /// @notice Wire the GenLayer ShipmentDeadlineCourt contract address.
    ///         Once set, verdicts must come from this address (relayer remains fallback).
    function setCourtContract(address _court) external onlyAdmin {
        require(_court != address(0), "TFX: zero court");
        courtContract = _court;
        emit CourtContractSet(_court, msg.sender);
    }

    /// @notice Update the arbitrator for UNDETERMINED manual reviews.
    function setArbitrator(address _arb) external onlyAdmin {
        require(_arb != address(0), "TFX: zero arbitrator");
        arbitrator = _arb;
        emit ArbitratorSet(_arb, msg.sender);
    }

    // ─── Rate lock ────────────────────────────────────────────────────────────

    function requestRateLock()
        external onlyParty notPaused inStatus(Status.DRAFT)
    {
        status = Status.RATE_PENDING;
        emit RateLockRequested(msg.sender, block.timestamp);
    }

    function receiveRate(
        uint256 rate,
        bytes32 benchmarkType,
        bytes32 benchmarkId,
        uint256 asOfTimestamp
    )
        external onlyRelayer notPaused inStatus(Status.RATE_PENDING)
    {
        require(rate > 0, "TFX: zero rate");
        lockedRate = RateInfo(rate, benchmarkType, benchmarkId, asOfTimestamp);
        settlementAmount = _computeSettlement(currentNotional, rate);
        status = Status.RATE_LOCKED;
        emit RateLocked(rate, benchmarkType, benchmarkId, asOfTimestamp, settlementAmount);
    }

    // ─── Resize ───────────────────────────────────────────────────────────────

    function resize(uint256 newFulfilledBps) external onlyExporter notPaused {
        require(
            status == Status.RATE_LOCKED || status == Status.ROLLED,
            "TFX: resize only before funding"
        );
        require(newFulfilledBps > 0 && newFulfilledBps < 10_000, "TFX: bps out of range");
        require(newFulfilledBps < fulfilledBps, "TFX: bps must decrease");

        uint256 oldNotional = currentNotional;
        currentNotional  = (invoiceAmount * newFulfilledBps) / 10_000;
        fulfilledBps     = newFulfilledBps;
        settlementAmount = _computeSettlement(currentNotional, lockedRate.rate);
        emit NotionalResized(oldNotional, currentNotional, settlementAmount, newFulfilledBps);
    }

    // ─── Funding ──────────────────────────────────────────────────────────────

    function fundSettlement() external onlyImporter notPaused {
        require(
            status == Status.RATE_LOCKED || status == Status.ROLLED,
            "TFX: cannot fund in current state"
        );
        require(settlementAmount > 0, "TFX: settlement amount not set");

        fundedAmount = settlementAmount;
        settlementToken.safeTransferFrom(msg.sender, address(this), settlementAmount);
        status = Status.FUNDED;
        // Anchor contest window to shipment-check time (when importer locks funds).
        contestDeadline = block.timestamp + 7 days;
        emit Funded(msg.sender, settlementAmount, block.timestamp);
    }

    // ─── Roll ─────────────────────────────────────────────────────────────────

    function requestRoll(uint256 newDueDate) external onlyParty notPaused {
        require(
            status == Status.RATE_LOCKED ||
            status == Status.ROLLED      ||
            status == Status.FUNDED,
            "TFX: cannot roll in current state"
        );
        require(newDueDate > currentDueDate, "TFX: new date must be later");
        _pendingNewDueDate = newDueDate;
        status = Status.ROLL_PENDING;
        emit RollRequested(msg.sender, currentDueDate, newDueDate, block.timestamp);
    }

    function receiveRolledRate(
        uint256 newRate,
        uint256 rollCost,
        bytes32 benchmarkId,
        uint256 asOfTimestamp
    )
        external onlyRelayer notPaused inStatus(Status.ROLL_PENDING)
    {
        require(newRate > 0, "TFX: zero rate");

        uint256 newDueDate = _pendingNewDueDate;
        delete _pendingNewDueDate;

        _rolls.push(RollRecord({
            priorRate:     lockedRate.rate,
            rolledRate:    newRate,
            rollCost:      rollCost,
            priorDueDate:  currentDueDate,
            newDueDate:    newDueDate,
            benchmarkId:   benchmarkId,
            asOfTimestamp: asOfTimestamp
        }));

        emit RateRolled(lockedRate.rate, newRate, rollCost,
            currentDueDate, newDueDate, benchmarkId, asOfTimestamp);

        lockedRate.rate          = newRate;
        lockedRate.benchmarkId   = benchmarkId;
        lockedRate.asOfTimestamp = asOfTimestamp;
        currentDueDate           = newDueDate;
        rollCount++;
        settlementAmount         = _computeSettlement(currentNotional, newRate);
        status                   = Status.ROLLED;
    }

    // ─── Settle ───────────────────────────────────────────────────────────────

    function settle() external onlyParty notPaused inStatus(Status.FUNDED) {
        uint256 amount = fundedAmount;
        fundedAmount = 0;
        status = Status.SETTLED;
        settlementToken.safeTransfer(exporter, amount);
        emit Settled(exporter, amount, block.timestamp);
    }

    // ─── Cancel & refund ──────────────────────────────────────────────────────

    function cancelAndRefund(uint8 reasonCode) external onlyAuthorized notTerminal {
        uint256 refund = fundedAmount;
        address refundTo = importer;
        fundedAmount = 0;
        status = Status.CANCELLED;
        if (refund > 0) {
            settlementToken.safeTransfer(refundTo, refund);
        }
        emit Cancelled(reasonCode, msg.sender, refund, refundTo);
    }

    // ─── Exception side channel ───────────────────────────────────────────────

    function flagException(uint8 reasonCode, bytes32 evidenceRef)
        external onlyParty notTerminal
    {
        exceptionFlagged    = true;
        exceptionReasonCode = reasonCode;
        exceptionRef        = evidenceRef;
        emit ExceptionFlagged(reasonCode, evidenceRef, msg.sender);
    }

    function setExceptionPause(bool paused) external onlyAdmin {
        require(exceptionFlagged, "TFX: no exception flagged");
        exceptionPaused = paused;
        emit ExceptionPauseSet(paused, msg.sender);
    }

    // ─── Shipment dispute ─────────────────────────────────────────────────────

    /**
     * @notice Accept the shipment as timely.
     *         Before contestDeadline: only the importer can call.
     *         After contestDeadline: anyone can call — prevents indefinite hold-up.
     *         Short-circuits the court path. Settlement can proceed immediately via settle().
     */
    function acceptShipment() external notTerminal {
        require(
            shipmentStatus == ShipmentStatus.NONE,
            "TFX: shipment review already initiated"
        );
        require(
            status == Status.RATE_LOCKED || status == Status.ROLLED || status == Status.FUNDED,
            "TFX: cannot accept shipment in current state"
        );

        bool afterDeadline = block.timestamp > contestDeadline;

        if (!afterDeadline) {
            require(msg.sender == importer, "TFX: only importer before deadline");
        }

        shipmentStatus = ShipmentStatus.ACCEPTED;
        emit ShipmentAccepted(msg.sender, afterDeadline, block.timestamp);
    }

    /**
     * @notice Importer contests shipment timing. Pauses settlement.
     *         Reverts after contestDeadline — buyer cannot hold up indefinitely.
     *
     * @param courtSheetACid   IPFS CID of exporter court sheet (ANB customs exit)
     * @param courtSheetBCid   IPFS CID of importer court sheet (SUNAT border gate)
     * @param statement        Factual statement submitted to the court
     * @param guidelineVersion Frozen evaluation guideline version
     */
    function contestShipment(
        string calldata courtSheetACid,
        string calldata courtSheetBCid,
        string calldata statement,
        string calldata guidelineVersion
    )
        external onlyImporter notTerminal
    {
        require(
            shipmentStatus == ShipmentStatus.NONE,
            "TFX: shipment review already initiated"
        );
        require(
            status == Status.RATE_LOCKED || status == Status.ROLLED || status == Status.FUNDED,
            "TFX: cannot contest in current state"
        );
        require(
            block.timestamp <= contestDeadline,
            "TFX: contest deadline passed - shipment deemed accepted"
        );

        shipmentStatus            = ShipmentStatus.CONTESTED;
        shipmentCourtSheetACid    = courtSheetACid;
        shipmentCourtSheetBCid    = courtSheetBCid;
        shipmentStatement         = statement;
        shipmentGuidelineVersion  = guidelineVersion;
        shipmentReviewRequired    = true;
        exceptionPaused           = true;

        emit ShipmentContested(msg.sender, courtSheetACid, statement, contestDeadline, block.timestamp);

        // Register with InternetCourt if factory is configured.
        // This emits DisputeRequested on the factory, triggering the relay to deploy
        // the GenLayer oracle. The relay reads getOracleType() + getOracleArgs() from
        // this contract to determine which oracle to deploy and with what arguments.
        if (courtFactory != address(0)) {
            icCaseId = IInternetCourtFactory(courtFactory).registerCase();
        }
    }

    /**
     * @notice Deliver the court verdict.
     *         Callable by the registered GenLayer courtContract address,
     *         OR by oracleRelayer as a testnet fallback (until court contract is wired).
     *
     * @param verdict       1=TIMELY, 2=LATE, 3=UNDETERMINED
     * @param caseId        Court case identifier
     * @param reasonSummary One-sentence reasoning summary
     */
    /**
     * @notice Callback for LayerZero-delivered verdicts from GenLayer InternetCourt bridge.
     *         Decodes the nested message and dispatches to resolveShipmentVerdict().
     */
    function processBridgeMessage(
        uint32 /* _srcChainId */,
        address /* _srcSender */,
        bytes calldata _message
    ) external {
        require(msg.sender == bridgeReceiver, "TFX: only bridge");

        // Decode outer wrapper (matches CaseResolution.py/ShipmentDeadlineCourt.py)
        (address agreementAddress, bytes memory resolutionData) = abi.decode(_message, (address, bytes));
        require(agreementAddress == address(this), "TFX: wrong agreement");

        // Decode inner payload: (address target, uint8 verdict, string reasoning)
        (address target, uint8 verdict, string memory reasoning) = abi.decode(resolutionData, (address, uint8, string));
        require(target == address(this), "TFX: wrong target");

        // Execute resolution logic
        _resolveShipmentVerdict(verdict, "LZ-GENLAYER", reasoning);
    }

    // ── IResolutionTarget — called by InternetCourtFactory ───────────────────

    /**
     * @notice Verdict delivery from InternetCourtFactory after GenLayer oracle finalizes.
     *         Only the registered courtFactory may call this.
     *         Verdict codes (set by ShipmentDeadlineCourt.py): 1=ON_TIME, 2=LATE_1_4, 3=LATE_5_6, 4=LATE_7_8, 5=VERY_LATE, 6=UNDETERMINED.
     */
    function setResolution(uint8 verdict, string calldata reasoning) external {
        require(msg.sender == courtFactory, "TFX: only court factory");
        require(courtFactory != address(0), "TFX: IC not configured");
        _resolveShipmentVerdict(verdict, invoiceRef, reasoning);
    }

    /**
     * @notice Oracle type for the relay's ORACLE_REGISTRY. Identifies ShipmentDeadlineCourt.py.
     */
    function getOracleType() external pure returns (bytes32) {
        return ORACLE_TYPE; // keccak256("TRADE_FINANCE_V1")
    }

    /**
     * @notice ABI-encoded constructor args for ShipmentDeadlineCourt.py.
     *         Schema (matches relay's ORACLE_REGISTRY["TRADE_FINANCE_V1"].decode):
     *           (string case_id, address settlement_contract, string statement,
     *            string guideline_version, string court_sheet_a_cid, string court_sheet_b_cid)
     *         The relay appends bridge_sender, target_chain_eid, target_contract from its config.
     */
    function getOracleArgs() external view returns (bytes memory) {
        return abi.encode(
            invoiceRef,               // case_id
            address(this),            // settlement_contract (for message encoding)
            shipmentStatement,        // statement
            shipmentGuidelineVersion, // guideline_version
            shipmentCourtSheetACid,   // court_sheet_a_cid
            shipmentCourtSheetBCid    // court_sheet_b_cid
        );
    }

    // ── Relayer fallback (oracleRelayer or courtContract direct call) ─────────

    function resolveShipmentVerdict(
        uint8 verdict,
        string calldata caseId,
        string calldata reasonSummary
    )
        external onlyVerdictSource
    {
        _resolveShipmentVerdict(verdict, caseId, reasonSummary);
    }

    function _resolveShipmentVerdict(
        uint8 verdict,
        string memory caseId,
        string memory reasonSummary
    )
        internal
    {
        require(
            shipmentStatus == ShipmentStatus.CONTESTED,
            "TFX: no active shipment contest"
        );

        bool fromCourt = (courtContract != address(0) && msg.sender == courtContract);

        shipmentCaseId        = caseId;
        shipmentVerdictReason = reasonSummary;
        shipmentVerdictAt     = block.timestamp;

        emit ShipmentVerdictReceived(verdict, caseId, reasonSummary,
            msg.sender, fromCourt, block.timestamp);

        if (verdict == 1) {
            // ON_TIME
            shipmentStatus  = ShipmentStatus.TIMELY;
            exceptionPaused = false;
        } else if (verdict == 2) {
            // LATE_1_4: 99.5% exporter, 0.5% importer
            exceptionPaused = false;
            _settleWithPenalty(50);
        } else if (verdict == 3) {
            // LATE_5_6: 99.0% exporter, 1.0% importer
            exceptionPaused = false;
            _settleWithPenalty(100);
        } else if (verdict == 4) {
            // LATE_7_8: 98.5% exporter, 1.5% importer
            exceptionPaused = false;
            _settleWithPenalty(150);
        } else if (verdict == 5) {
            // VERY_LATE
            shipmentStatus      = ShipmentStatus.RETURN_REQUIRED;
            returnProofDeadline = block.timestamp + RETURN_PROOF_WINDOW;
            emit ReturnRequired(returnProofDeadline, block.timestamp);
        } else if (verdict == 6) {
            // UNDETERMINED — keep existing manual review logic
            shipmentStatus         = ShipmentStatus.UNDETERMINED;
            shipmentReviewRequired = true;
            uint256 reviewDeadline = block.timestamp + MANUAL_REVIEW_WINDOW;
            emit SettlementManualReview(caseId, reviewDeadline, block.timestamp);
        } else {
            revert("TFX: invalid verdict code");
        }
    }

    /**
     * @notice Arbitrator delivers a binding human finding after UNDETERMINED verdict.
     *         This is the defined escape hatch for insufficient-evidence cases.
     *
     * @param timeliness  true = shipment was timely (release); false = late (refund)
     * @param reason      Human-readable explanation of the finding
     */
    function resolveManualReview(bool timeliness, string calldata reason)
        external onlyArbitrator
    {
        require(
            shipmentStatus == ShipmentStatus.UNDETERMINED,
            "TFX: no manual review pending"
        );

        emit ManualReviewResolved(msg.sender, timeliness, reason, block.timestamp);

        if (timeliness) {
            shipmentStatus  = ShipmentStatus.TIMELY;
            exceptionPaused = false;
            // Caller then invokes finalizeAfterShipment() to release funds
        } else {
            shipmentStatus  = ShipmentStatus.LATE;
            exceptionPaused = false;
            _cancelAndRefundImporter();
        }
    }

    /**
     * @notice Default UNDETERMINED to LATE after MANUAL_REVIEW_WINDOW has elapsed.
     *         Callable by anyone — prevents permanent freeze if arbitrator is unavailable.
     *         Importer bears the evidence burden: insufficient evidence defaults to refund.
     */
    function timeoutManualReview() external {
        require(
            shipmentStatus == ShipmentStatus.UNDETERMINED,
            "TFX: no manual review pending"
        );
        require(
            shipmentVerdictAt > 0 &&
            block.timestamp > shipmentVerdictAt + MANUAL_REVIEW_WINDOW,
            "TFX: review window not elapsed"
        );

        emit ManualReviewTimedOut(msg.sender, block.timestamp);

        shipmentStatus  = ShipmentStatus.LATE;
        exceptionPaused = false;
        _cancelAndRefundImporter();
    }

    /**
     * @notice Anyone can call this to settle with a penalty if the return proof window expires.
     */
    function timeoutReturnProof() external {
        require(shipmentStatus == ShipmentStatus.RETURN_REQUIRED, "TFX: not return required");
        require(block.timestamp >= returnProofDeadline, "TFX: window not elapsed");

        emit ReturnProofTimeout(msg.sender, block.timestamp);
        _settleWithPenalty(150); // highest penalty tier
    }

    /**
     * @notice Importer submits proof of return/rejection.
     *         Placeholder for Phase 2 evaluation.
     */
    function submitReturnProof(string calldata returnSheetACid, string calldata returnSheetBCid) external {
        require(shipmentStatus == ShipmentStatus.RETURN_REQUIRED, "TFX: not return required");
        require(block.timestamp < returnProofDeadline, "TFX: window elapsed");

        returnProofSheetACid = returnSheetACid;
        returnProofSheetBCid = returnSheetBCid;

        emit ReturnProofSubmitted(msg.sender, returnSheetACid, returnSheetBCid, block.timestamp);
    }

    /**
     * @notice Finalize settlement after shipment is confirmed TIMELY or ACCEPTED.
     *         Cannot proceed if LATE (funds were refunded inline) or UNDETERMINED (frozen).
     */
    function finalizeAfterShipment() external onlyParty notPaused {
        require(
            shipmentStatus == ShipmentStatus.ACCEPTED ||
            shipmentStatus == ShipmentStatus.TIMELY,
            "TFX: shipment not confirmed as timely"
        );
        require(status == Status.FUNDED, "TFX: must be funded to finalize");

        uint256 amount = fundedAmount;
        fundedAmount = 0;
        status = Status.SETTLED;
        settlementToken.safeTransfer(exporter, amount);
        emit Settled(exporter, amount, block.timestamp);
    }

    // ─── Internal ─────────────────────────────────────────────────────────────

    function _settleWithPenalty(uint256 penaltyBps) internal {
        uint256 totalAmount = fundedAmount;
        require(totalAmount > 0, "TFX: nothing to settle");

        uint256 penalty = (totalAmount * penaltyBps) / 10000;
        uint256 exporterAmount = totalAmount - penalty;

        fundedAmount = 0;
        status = Status.SETTLED;

        if (exporterAmount > 0) {
            settlementToken.safeTransfer(exporter, exporterAmount);
        }
        if (penalty > 0) {
            settlementToken.safeTransfer(importer, penalty);
        }

        emit ShipmentSettledWithPenalty(exporter, importer, exporterAmount, penalty, penaltyBps);
        emit Settled(exporter, exporterAmount, block.timestamp);
    }

    function _cancelAndRefundImporter() internal {
        uint256 refund = fundedAmount;
        fundedAmount   = 0;
        status         = Status.CANCELLED;
        if (refund > 0) {
            settlementToken.safeTransfer(importer, refund);
        }
        emit SettlementCancelledByVerdict(importer, refund);
    }

    function _computeSettlement(uint256 notional, uint256 rate)
        internal pure returns (uint256)
    {
        return (notional * rate) / 1e18;
    }

    // ─── Views ────────────────────────────────────────────────────────────────

    function getRollHistory() external view returns (RollRecord[] memory) {
        return _rolls;
    }

    function getLockedRate() external view returns (RateInfo memory) {
        return lockedRate;
    }

    function getSummary() external view returns (
        Status  _status,
        uint256 _invoiceAmount,
        uint256 _currentNotional,
        uint256 _settlementAmount,
        uint256 _fundedAmount,
        uint256 _currentDueDate,
        uint256 _contestDeadline,
        uint256 _rollCount,
        bool    _exceptionFlagged,
        address _courtContract,
        address _arbitrator
    ) {
        return (
            status,
            invoiceAmount,
            currentNotional,
            settlementAmount,
            fundedAmount,
            currentDueDate,
            contestDeadline,
            rollCount,
            exceptionFlagged,
            courtContract,
            arbitrator
        );
    }
}
