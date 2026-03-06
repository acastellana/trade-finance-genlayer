// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/**
 * @title TradeFxSettlement v2
 * @notice Benchmark-based FX settlement engine with token settlement rail.
 *
 * Lifecycle:
 *   DRAFT → RATE_PENDING → RATE_LOCKED → FUNDED → SETTLED
 *                                ↓
 *                         ROLL_PENDING → ROLLED → FUNDED → SETTLED
 *
 *   Any non-SETTLED state → CANCELLED (with refund if funded)
 *
 * Resize (partial shipment): callable from RATE_LOCKED or ROLLED.
 * Preserves locked rate; recomputes settlement amount. Status unchanged.
 *
 * Exception side channel: orthogonal bool flags; do not change status.
 * Admin can pause lifecycle via exceptionPaused.
 */
contract TradeFxSettlement {
    using SafeERC20 for IERC20;

    // ─── Status ───────────────────────────────────────────────────────────────

    enum Status {
        DRAFT,          // 0 — created, rate not yet requested
        RATE_PENDING,   // 1 — rate lock requested, awaiting oracle
        RATE_LOCKED,    // 2 — rate locked, settlement amount fixed, awaiting funding
        FUNDED,         // 3 — importer has deposited settlement tokens
        ROLL_PENDING,   // 4 — roll requested, awaiting oracle
        ROLLED,         // 5 — roll delivered, awaiting funding or settlement
        SETTLED,        // 6 — settlement tokens transferred to exporter
        CANCELLED       // 7 — cancelled; funded amount returned to importer
    }

    // ─── Data structures ─────────────────────────────────────────────────────

    struct RateInfo {
        uint256 rate;           // settlement per source unit, scaled 1e18
        bytes32 benchmarkType;  // e.g. bytes32("MARKET_AGGREGATE")
        bytes32 benchmarkId;    // unique rate observation ID
        uint256 asOfTimestamp;
    }

    struct RollRecord {
        uint256 priorRate;
        uint256 rolledRate;
        uint256 rollCost;       // 0 = spot re-lock (no forward points)
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

    // ─── Settlement token ─────────────────────────────────────────────────────

    IERC20 public settlementToken;   // MockPEN — the token importer funds and exporter receives

    // ─── Invoice ─────────────────────────────────────────────────────────────

    uint256 public invoiceAmount;       // original invoice, source currency, 1e18
    bytes32 public sourceCurrency;      // keccak256("BOB")
    bytes32 public settlementCurrency;  // keccak256("PEN")
    string  public invoiceRef;

    // ─── Rate & settlement ───────────────────────────────────────────────────

    RateInfo public lockedRate;
    uint256  public settlementAmount;   // PEN required, recomputed on resize/roll, 1e18

    // ─── Notional (resize) ───────────────────────────────────────────────────

    uint256 public currentNotional;     // BOB notional after any resizes, 1e18
    uint256 public fulfilledBps;        // basis points fulfilled (10000 = 100%)

    // ─── Funding ─────────────────────────────────────────────────────────────

    uint256 public fundedAmount;        // MockPEN held in contract right now

    // ─── Timing ──────────────────────────────────────────────────────────────

    uint256 public currentDueDate;

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

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlyExporter()  { require(msg.sender == exporter,      "TFX: not exporter");  _; }
    modifier onlyImporter()  { require(msg.sender == importer,      "TFX: not importer");  _; }
    modifier onlyRelayer()   { require(msg.sender == oracleRelayer, "TFX: not relayer");   _; }
    modifier onlyAdmin()     { require(msg.sender == admin,         "TFX: not admin");     _; }

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

    modifier notPaused()  { require(!exceptionPaused, "TFX: paused by exception"); _; }
    modifier notTerminal() {
        require(status != Status.SETTLED && status != Status.CANCELLED, "TFX: terminal state");
        _;
    }
    modifier inStatus(Status s) { require(status == s, "TFX: invalid state"); _; }

    // ─── Constructor ─────────────────────────────────────────────────────────

    /**
     * @param _exporter           Exporter wallet
     * @param _importer           Importer wallet
     * @param _oracleRelayer      Relayer wallet (only one that can deliver rates)
     * @param _admin              Admin (0x0 = deployer)
     * @param _settlementToken    MockPEN token address
     * @param _invoiceAmount      Invoice BOB amount, 1e18
     * @param _sourceCurrency     keccak256("BOB")
     * @param _settlementCurrency keccak256("PEN")
     * @param _dueDate            Unix timestamp
     * @param _invoiceRef         Invoice reference string
     */
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
        require(_exporter != address(0),       "TFX: zero exporter");
        require(_importer != address(0),       "TFX: zero importer");
        require(_oracleRelayer != address(0),  "TFX: zero relayer");
        require(_settlementToken != address(0),"TFX: zero token");
        require(_invoiceAmount > 0,            "TFX: zero invoice");
        require(_dueDate > block.timestamp,    "TFX: due date in past");

        exporter          = _exporter;
        importer          = _importer;
        oracleRelayer     = _oracleRelayer;
        admin             = _admin != address(0) ? _admin : msg.sender;
        settlementToken   = IERC20(_settlementToken);

        invoiceAmount     = _invoiceAmount;
        currentNotional   = _invoiceAmount;
        sourceCurrency    = _sourceCurrency;
        settlementCurrency = _settlementCurrency;
        currentDueDate    = _dueDate;
        invoiceRef        = _invoiceRef;
        fulfilledBps      = 10_000;
        status            = Status.DRAFT;

        emit TradeCreated(_exporter, _importer, _invoiceAmount, _dueDate, _invoiceRef);
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

    // ─── Resize (partial shipment) ────────────────────────────────────────────

    /**
     * @notice Reduce invoice notional due to partial shipment.
     *         Locked rate is preserved — only the amount changes.
     *         Call from RATE_LOCKED or ROLLED (before funding).
     *
     * @param newFulfilledBps  Basis points of original invoice fulfilled (1-9999)
     */
    function resize(uint256 newFulfilledBps)
        external onlyExporter notPaused
    {
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

    /**
     * @notice Importer deposits exactly settlementAmount of MockPEN.
     *         Transitions RATE_LOCKED or ROLLED → FUNDED.
     *         Importer must approve this contract before calling.
     */
    function fundSettlement()
        external onlyImporter notPaused
    {
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

    /**
     * @notice Request a date extension. Callable from RATE_LOCKED or ROLLED.
     *         If FUNDED, funding remains; settlement amount may change on delivery.
     */
    function requestRoll(uint256 newDueDate)
        external onlyParty notPaused
    {
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

        lockedRate.rate         = newRate;
        lockedRate.benchmarkId  = benchmarkId;
        lockedRate.asOfTimestamp = asOfTimestamp;
        currentDueDate          = newDueDate;
        rollCount++;

        settlementAmount = _computeSettlement(currentNotional, newRate);
        // If previously funded, tokens remain; importer may need to top up
        // (out of scope for v2 — same rate in demo scenarios)
        status = Status.ROLLED;
    }

    // ─── Settle ───────────────────────────────────────────────────────────────

    /**
     * @notice Transfer settlement tokens to exporter. Callable by either party.
     *         Transitions FUNDED → SETTLED.
     */
    function settle()
        external onlyParty notPaused inStatus(Status.FUNDED)
    {
        uint256 amount = fundedAmount;
        fundedAmount = 0;
        status = Status.SETTLED;

        settlementToken.safeTransfer(exporter, amount);

        emit Settled(exporter, amount, block.timestamp);
    }

    // ─── Cancel & refund ──────────────────────────────────────────────────────

    /**
     * @notice Cancel the trade. If funded, returns MockPEN to importer.
     * @param reasonCode 1=mutual, 2=timeout, 3=admin/testnet, 99=other
     */
    function cancelAndRefund(uint8 reasonCode)
        external onlyAuthorized notTerminal
    {
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
        uint256 _rollCount,
        bool    _exceptionFlagged
    ) {
        return (
            status,
            invoiceAmount,
            currentNotional,
            settlementAmount,
            fundedAmount,
            currentDueDate,
            rollCount,
            exceptionFlagged
        );
    }

    // ─── Internal ─────────────────────────────────────────────────────────────

    function _computeSettlement(uint256 notional, uint256 rate)
        internal pure returns (uint256)
    {
        return (notional * rate) / 1e18;
    }
}
