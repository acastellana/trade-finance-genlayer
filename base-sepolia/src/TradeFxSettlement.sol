// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title TradeFxSettlement
 * @notice Benchmark-based FX settlement engine for cross-border invoices.
 *
 * This contract manages one question: given a commercial invoice denominated
 * in currency A and payment in currency B, what is the final settlement amount
 * when shipment, documents, and payment timing change?
 *
 * It does NOT adjudicate commercial truth. It does NOT embed delivery
 * confirmation or dispute logic. Those are handled off-chain or via
 * InternetCourt as an exception path.
 *
 * State machine:
 *   DRAFT → RATE_PENDING → RATE_LOCKED → SETTLED
 *                                ↓
 *                        PARTIAL_RESIZED → SETTLED
 *                                ↓
 *                         ROLL_PENDING → ROLLED → SETTLED
 *
 * Exception side channel (non-blocking by default):
 *   any state → flagException() → sets exceptionFlagged
 *   admin can set exceptionPaused to block lifecycle until resolved
 */
contract TradeFxSettlement {

    // ─── State machine ───────────────────────────────────────────────────────

    enum Status {
        DRAFT,            // created, rate not yet requested
        RATE_PENDING,     // rate lock requested, awaiting oracle
        RATE_LOCKED,      // rate locked, settlement amount calculated, awaiting payment
        PARTIAL_RESIZED,  // notional reduced post-lock, locked rate preserved, awaiting payment
        ROLL_PENDING,     // roll requested, awaiting oracle
        ROLLED,           // rolled rate locked, awaiting payment
        SETTLED,          // settlement confirmed, trade closed
        CANCELLED         // cancelled before settlement
    }

    // ─── Data structures ─────────────────────────────────────────────────────

    struct RateInfo {
        uint256 rate;           // settlement per source currency, scaled 1e18
        bytes32 benchmarkType;  // e.g. keccak256("BCRP_BCB_CROSS")
        bytes32 benchmarkId;    // unique identifier for this rate observation
        uint256 asOfTimestamp;  // timestamp of rate observation
    }

    struct RollRecord {
        uint256 priorRate;        // rate before roll, scaled 1e18
        uint256 rolledRate;       // new locked rate, scaled 1e18
        uint256 rollCost;         // cost/premium in settlement currency, scaled 1e18 (0 if mocked)
        uint256 priorDueDate;     // previous due date
        uint256 newDueDate;       // new due date after roll
        bytes32 benchmarkId;      // benchmark reference for rolled rate
        uint256 asOfTimestamp;    // timestamp of rolled rate observation
    }

    // ─── Roles ───────────────────────────────────────────────────────────────

    address public exporter;
    address public importer;
    address public oracleRelayer;
    address public admin;

    // ─── Invoice ─────────────────────────────────────────────────────────────

    uint256 public invoiceAmount;       // original invoice in source currency, scaled 1e18
    bytes32 public sourceCurrency;      // e.g. keccak256("BOB")
    bytes32 public settlementCurrency;  // e.g. keccak256("PEN")
    string  public invoiceRef;          // off-chain invoice identifier

    // ─── Rate & settlement ───────────────────────────────────────────────────

    RateInfo public lockedRate;         // immutable once locked (roll creates new record)
    uint256 public settlementAmount;    // current settlement amount in settlement currency, 1e18

    // ─── Notional tracking ───────────────────────────────────────────────────

    uint256 public currentNotional;     // invoice amount after any resizes, scaled 1e18
    uint256 public fulfilledBps;        // basis points fulfilled (10000 = 100%)

    // ─── Timing ──────────────────────────────────────────────────────────────

    uint256 public expectedPaymentDate; // original expected payment date
    uint256 public currentDueDate;      // current due date (may change on roll)

    // ─── Roll history ────────────────────────────────────────────────────────

    uint256 public rollCount;
    RollRecord[] private _rolls;

    // pending roll state (set in requestRoll, cleared in receiveRolledRate)
    uint256 private _pendingNewDueDate;

    // ─── Proof references ────────────────────────────────────────────────────

    bytes32 public paymentProofRef;     // set by markPaymentPending
    bytes32 public settlementProofRef;  // set by confirmSettlement

    // ─── Exception side channel ──────────────────────────────────────────────

    bool    public exceptionFlagged;
    bool    public exceptionPaused;     // admin-controlled; blocks lifecycle when true
    uint8   public exceptionReasonCode;
    bytes32 public exceptionRef;

    // ─── Status ──────────────────────────────────────────────────────────────

    Status public status;

    // ─── Events ──────────────────────────────────────────────────────────────

    event TradeCreated(
        address indexed exporter,
        address indexed importer,
        uint256 invoiceAmount,
        bytes32 sourceCurrency,
        bytes32 settlementCurrency,
        uint256 expectedPaymentDate,
        string  invoiceRef
    );

    event RateLockRequested(
        address indexed requester,
        uint256 timestamp
    );

    event RateLocked(
        uint256 rate,
        bytes32 benchmarkType,
        bytes32 benchmarkId,
        uint256 asOfTimestamp,
        uint256 settlementAmount
    );

    event NotionalResized(
        uint256 oldNotional,
        uint256 newNotional,
        uint256 newSettlementAmount,
        uint256 fulfilledBps
    );

    event RollRequested(
        address indexed requester,
        uint256 currentDueDate,
        uint256 requestedNewDueDate,
        uint256 timestamp
    );

    event RateRolled(
        uint256 priorRate,
        uint256 rolledRate,
        uint256 rollCost,
        uint256 oldDueDate,
        uint256 newDueDate,
        bytes32 benchmarkId,
        uint256 asOfTimestamp
    );

    event PaymentMarked(
        bytes32 proofRef,
        address indexed marker,
        uint256 timestamp
    );

    event SettlementConfirmed(
        bytes32 proofRef,
        uint256 finalSettlementAmount,
        address indexed confirmer
    );

    event TradeCancelled(
        uint8   reasonCode,
        address indexed canceller
    );

    event ExceptionFlagged(
        uint8   reasonCode,
        bytes32 evidenceRef,
        address indexed flagger
    );

    event ExceptionPauseSet(
        bool    paused,
        address indexed admin
    );

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlyExporter() {
        require(msg.sender == exporter, "TFX: not exporter");
        _;
    }

    modifier onlyImporter() {
        require(msg.sender == importer, "TFX: not importer");
        _;
    }

    modifier onlyRelayer() {
        require(msg.sender == oracleRelayer, "TFX: not relayer");
        _;
    }

    modifier onlyAdmin() {
        require(msg.sender == admin, "TFX: not admin");
        _;
    }

    modifier onlyParty() {
        require(
            msg.sender == exporter || msg.sender == importer,
            "TFX: not a party"
        );
        _;
    }

    modifier notPaused() {
        require(!exceptionPaused, "TFX: paused by exception");
        _;
    }

    modifier inStatus(Status s) {
        require(status == s, "TFX: invalid state");
        _;
    }

    // ─── Constructor ─────────────────────────────────────────────────────────

    /**
     * @param _exporter         Exporter wallet address
     * @param _importer         Importer wallet address
     * @param _oracleRelayer    Oracle relayer address (bridges GenLayer ↔ Base)
     * @param _admin            Admin address for exception recovery (0x0 = deployer)
     * @param _invoiceAmount    Invoice amount in source currency, scaled 1e18
     * @param _sourceCurrency   keccak256 of ISO currency code (e.g. "BOB")
     * @param _settlementCurrency keccak256 of ISO currency code (e.g. "PEN")
     * @param _expectedPaymentDate Unix timestamp of expected payment
     * @param _invoiceRef       Off-chain invoice reference string
     */
    constructor(
        address _exporter,
        address _importer,
        address _oracleRelayer,
        address _admin,
        uint256 _invoiceAmount,
        bytes32 _sourceCurrency,
        bytes32 _settlementCurrency,
        uint256 _expectedPaymentDate,
        string memory _invoiceRef
    ) {
        require(_exporter != address(0),   "TFX: invalid exporter");
        require(_importer != address(0),   "TFX: invalid importer");
        require(_oracleRelayer != address(0), "TFX: invalid relayer");
        require(_invoiceAmount > 0,         "TFX: zero invoice amount");
        require(_expectedPaymentDate > block.timestamp, "TFX: due date in past");

        exporter          = _exporter;
        importer          = _importer;
        oracleRelayer     = _oracleRelayer;
        admin             = _admin != address(0) ? _admin : msg.sender;

        invoiceAmount     = _invoiceAmount;
        currentNotional   = _invoiceAmount;
        sourceCurrency    = _sourceCurrency;
        settlementCurrency = _settlementCurrency;
        expectedPaymentDate = _expectedPaymentDate;
        currentDueDate    = _expectedPaymentDate;
        invoiceRef        = _invoiceRef;
        fulfilledBps      = 10_000; // 100%

        status = Status.DRAFT;

        emit TradeCreated(
            _exporter, _importer,
            _invoiceAmount, _sourceCurrency, _settlementCurrency,
            _expectedPaymentDate, _invoiceRef
        );
    }

    // ─── Lifecycle: rate lock ─────────────────────────────────────────────────

    /**
     * @notice Either party requests a benchmark rate lock from the oracle.
     *         Transitions DRAFT → RATE_PENDING.
     */
    function requestRateLock()
        external
        onlyParty
        notPaused
        inStatus(Status.DRAFT)
    {
        status = Status.RATE_PENDING;
        emit RateLockRequested(msg.sender, block.timestamp);
    }

    /**
     * @notice Oracle relayer delivers the benchmark rate.
     *         Transitions RATE_PENDING → RATE_LOCKED.
     *         Settlement amount is computed here and immutable unless explicitly rolled.
     *
     * @param rate            Rate (settlement per source), scaled 1e18
     * @param benchmarkType   Benchmark methodology identifier
     * @param benchmarkId     Unique reference for this rate observation
     * @param asOfTimestamp   Timestamp of the rate observation
     */
    function receiveRate(
        uint256 rate,
        bytes32 benchmarkType,
        bytes32 benchmarkId,
        uint256 asOfTimestamp
    )
        external
        onlyRelayer
        notPaused
        inStatus(Status.RATE_PENDING)
    {
        require(rate > 0, "TFX: zero rate");

        lockedRate = RateInfo({
            rate:          rate,
            benchmarkType: benchmarkType,
            benchmarkId:   benchmarkId,
            asOfTimestamp: asOfTimestamp
        });

        settlementAmount = _computeSettlement(currentNotional, rate);
        status = Status.RATE_LOCKED;

        emit RateLocked(rate, benchmarkType, benchmarkId, asOfTimestamp, settlementAmount);
    }

    // ─── Lifecycle: partial fulfillment ──────────────────────────────────────

    /**
     * @notice Resize notional due to partial shipment.
     *         The locked benchmark rate is PRESERVED — only the notional changes.
     *         Can be called from RATE_LOCKED or ROLLED.
     *
     * @param newFulfilledBps Basis points of original invoice fulfilled (1–9999)
     */
    function applyPartialFulfillment(uint256 newFulfilledBps)
        external
        onlyExporter
        notPaused
    {
        require(
            status == Status.RATE_LOCKED || status == Status.ROLLED,
            "TFX: cannot resize in current state"
        );
        require(newFulfilledBps > 0 && newFulfilledBps < 10_000, "TFX: invalid bps");
        require(newFulfilledBps < fulfilledBps, "TFX: bps must be less than current");

        uint256 oldNotional   = currentNotional;
        currentNotional       = (invoiceAmount * newFulfilledBps) / 10_000;
        fulfilledBps          = newFulfilledBps;
        settlementAmount      = _computeSettlement(currentNotional, lockedRate.rate);
        status                = Status.PARTIAL_RESIZED;

        emit NotionalResized(oldNotional, currentNotional, settlementAmount, newFulfilledBps);
    }

    // ─── Lifecycle: hedge roll ────────────────────────────────────────────────

    /**
     * @notice Request a hedge roll due to payment delay.
     *         Transitions to ROLL_PENDING; oracle will deliver new rate.
     *         A roll is a distinct economic event — not a simple date edit.
     *
     * @param newDueDate New expected payment date (must be later than current)
     */
    function requestRoll(uint256 newDueDate)
        external
        onlyParty
        notPaused
    {
        require(
            status == Status.RATE_LOCKED   ||
            status == Status.PARTIAL_RESIZED ||
            status == Status.ROLLED,
            "TFX: cannot roll in current state"
        );
        require(newDueDate > currentDueDate, "TFX: new date must be later");

        _pendingNewDueDate = newDueDate;
        status = Status.ROLL_PENDING;

        emit RollRequested(msg.sender, currentDueDate, newDueDate, block.timestamp);
    }

    /**
     * @notice Oracle relayer delivers the rolled benchmark rate.
     *         Transitions ROLL_PENDING → ROLLED.
     *         Stores the full roll record for auditability.
     *         Settlement amount is recomputed at the new rate.
     *
     * @param newRate         New locked rate, scaled 1e18
     * @param rollCost        Roll cost/premium in settlement currency, scaled 1e18 (0 if mocked)
     * @param benchmarkId     Unique reference for the rolled rate observation
     * @param asOfTimestamp   Timestamp of rolled rate observation
     */
    function receiveRolledRate(
        uint256 newRate,
        uint256 rollCost,
        bytes32 benchmarkId,
        uint256 asOfTimestamp
    )
        external
        onlyRelayer
        notPaused
        inStatus(Status.ROLL_PENDING)
    {
        require(newRate > 0, "TFX: zero rate");

        uint256 newDueDate = _pendingNewDueDate;
        delete _pendingNewDueDate;

        _rolls.push(RollRecord({
            priorRate:      lockedRate.rate,
            rolledRate:     newRate,
            rollCost:       rollCost,
            priorDueDate:   currentDueDate,
            newDueDate:     newDueDate,
            benchmarkId:    benchmarkId,
            asOfTimestamp:  asOfTimestamp
        }));

        emit RateRolled(
            lockedRate.rate, newRate, rollCost,
            currentDueDate, newDueDate,
            benchmarkId, asOfTimestamp
        );

        lockedRate.rate          = newRate;
        lockedRate.benchmarkId   = benchmarkId;
        lockedRate.asOfTimestamp = asOfTimestamp;
        currentDueDate           = newDueDate;
        rollCount++;

        settlementAmount = _computeSettlement(currentNotional, newRate);
        status = Status.ROLLED;
    }

    // ─── Lifecycle: payment & settlement ─────────────────────────────────────

    /**
     * @notice Importer signals payment has been sent off-chain.
     *         Stores the proof reference; does not change contract state.
     *
     * @param proofRef Hash or reference to off-chain payment proof
     */
    function markPaymentPending(bytes32 proofRef)
        external
        onlyImporter
        notPaused
    {
        require(_isAwaitingPayment(), "TFX: not awaiting payment");
        paymentProofRef = proofRef;
        emit PaymentMarked(proofRef, msg.sender, block.timestamp);
    }

    /**
     * @notice Confirm settlement and close the trade.
     *         Either party may confirm (typically exporter on receipt).
     *         Transitions to SETTLED — terminal state.
     *
     * @param proofRef Hash or reference to final settlement proof
     */
    function confirmSettlement(bytes32 proofRef)
        external
        onlyParty
        notPaused
    {
        require(_isAwaitingPayment(), "TFX: not in a settleable state");
        settlementProofRef = proofRef;
        status = Status.SETTLED;
        emit SettlementConfirmed(proofRef, settlementAmount, msg.sender);
    }

    // ─── Lifecycle: cancel ────────────────────────────────────────────────────

    /**
     * @notice Cancel the trade. Any party or admin may cancel before settlement.
     * @param reasonCode Numeric reason code:
     *   1 = mutual agreement
     *   2 = payment timeout
     *   3 = admin / testnet recovery
     *   4 = rate lock expired
     *   99 = other
     */
    function cancelTrade(uint8 reasonCode) external {
        require(
            msg.sender == exporter || msg.sender == importer || msg.sender == admin,
            "TFX: not authorized"
        );
        require(status != Status.SETTLED,   "TFX: already settled");
        require(status != Status.CANCELLED, "TFX: already cancelled");
        status = Status.CANCELLED;
        emit TradeCancelled(reasonCode, msg.sender);
    }

    // ─── Exception side channel ───────────────────────────────────────────────

    /**
     * @notice Flag an exception on this trade (non-blocking by default).
     *         Does NOT change the main status. Admin may set exceptionPaused
     *         to block lifecycle functions until resolved.
     *
     * @param reasonCode  Numeric reason code (1=fraud, 2=document conflict, 3=disputed amount)
     * @param evidenceRef Hash or reference to exception evidence
     */
    function flagException(uint8 reasonCode, bytes32 evidenceRef)
        external
        onlyParty
    {
        require(status != Status.SETTLED,   "TFX: already settled");
        require(status != Status.CANCELLED, "TFX: already cancelled");
        exceptionFlagged    = true;
        exceptionReasonCode = reasonCode;
        exceptionRef        = evidenceRef;
        emit ExceptionFlagged(reasonCode, evidenceRef, msg.sender);
    }

    /**
     * @notice Admin sets or clears exception pause.
     *         When paused, all lifecycle functions are blocked.
     *
     * @param paused True to pause; false to resume
     */
    function setExceptionPause(bool paused) external onlyAdmin {
        require(exceptionFlagged, "TFX: no exception flagged");
        exceptionPaused = paused;
        emit ExceptionPauseSet(paused, msg.sender);
    }

    // ─── Views ────────────────────────────────────────────────────────────────

    function getRollHistory() external view returns (RollRecord[] memory) {
        return _rolls;
    }

    function getLockedRate() external view returns (RateInfo memory) {
        return lockedRate;
    }

    /**
     * @notice Returns true if the trade is in a state that accepts payment.
     */
    function isAwaitingPayment() external view returns (bool) {
        return _isAwaitingPayment();
    }

    /**
     * @notice High-level summary for relayer and frontend.
     */
    function getSummary() external view returns (
        Status  _status,
        uint256 _invoiceAmount,
        uint256 _currentNotional,
        uint256 _settlementAmount,
        uint256 _currentDueDate,
        uint256 _rollCount,
        bool    _exceptionFlagged
    ) {
        return (
            status,
            invoiceAmount,
            currentNotional,
            settlementAmount,
            currentDueDate,
            rollCount,
            exceptionFlagged
        );
    }

    // ─── Internal helpers ─────────────────────────────────────────────────────

    function _computeSettlement(uint256 notional, uint256 rate)
        internal
        pure
        returns (uint256)
    {
        return (notional * rate) / 1e18;
    }

    function _isAwaitingPayment() internal view returns (bool) {
        return (
            status == Status.RATE_LOCKED     ||
            status == Status.PARTIAL_RESIZED ||
            status == Status.ROLLED
        );
    }
}
