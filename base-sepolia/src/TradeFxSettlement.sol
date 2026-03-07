// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

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
 *     resolveShipmentVerdict(TIMELY)      → finalizeAfterShipment()
 *     resolveShipmentVerdict(LATE)        → auto cancelAndRefund
 *     resolveShipmentVerdict(UNDETERMINED) → resolveManualReview(arbitrator)
 *
 * Contest deadline:
 *   contestShipment() reverts after contestDeadline.
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
    ///         Set to dueDate + 30 days at deployment.
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
        UNDETERMINED    // 5 — insufficient evidence; escalated to arbitrator
    }

    ShipmentStatus public shipmentStatus;
    string  public shipmentCaseId;
    string  public shipmentManifestCid;
    string  public shipmentGuidelineVersion;
    string  public shipmentStatement;
    uint256 public shipmentVerdictAt;
    string  public shipmentVerdictReason;
    bool    public shipmentReviewRequired;

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
        string memory _invoiceRef
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

        // Contest window: 30 days after due date
        contestDeadline    = _dueDate + 30 days;

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
     * @param manifestCid      IPFS CID of evidence manifest
     * @param statement        Factual statement submitted to the court
     * @param guidelineVersion Frozen evaluation guideline version
     */
    function contestShipment(
        string calldata manifestCid,
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

        shipmentStatus           = ShipmentStatus.CONTESTED;
        shipmentManifestCid      = manifestCid;
        shipmentStatement        = statement;
        shipmentGuidelineVersion = guidelineVersion;
        shipmentReviewRequired   = true;
        exceptionPaused          = true;

        emit ShipmentContested(msg.sender, manifestCid, statement, contestDeadline, block.timestamp);
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
    function resolveShipmentVerdict(
        uint8 verdict,
        string calldata caseId,
        string calldata reasonSummary
    )
        external onlyVerdictSource
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
            // TRUE — TIMELY
            shipmentStatus  = ShipmentStatus.TIMELY;
            exceptionPaused = false;

        } else if (verdict == 2) {
            // FALSE — LATE — cancel and refund immediately
            shipmentStatus  = ShipmentStatus.LATE;
            exceptionPaused = false;
            _cancelAndRefundImporter();

        } else if (verdict == 3) {
            // UNDETERMINED — escalate to arbitrator
            shipmentStatus         = ShipmentStatus.UNDETERMINED;
            shipmentReviewRequired = true;
            // exceptionPaused remains true
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
