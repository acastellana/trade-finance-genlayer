# { "Depends": "py-genlayer:test" }
from genlayer import *
import json


class StableCoin(gl.Contract):
    """Generic ERC20-like stablecoin for GenLayer testnet.

    Notes:
    - Anyone can mint on testnet (intentionally permissionless for demos).
    - Amounts are passed/stored as *decimal strings* to avoid any ambiguity.
    - Address-like parameters accept `str` (0x...), `bytes` (20 bytes), or `Address`.
    """

    name: str
    symbol: str
    decimals: u256
    total_supply: u256
    balances: TreeMap[str, str]        # address_hex -> amount as string
    allowances: TreeMap[str, str]      # "owner:spender" -> amount as string
    owner: Address

    def __init__(self, name: str, symbol: str, decimals: int = 18):
        self.name = name
        self.symbol = symbol
        self.decimals = u256(decimals)
        self.total_supply = u256(0)
        self.owner = gl.message.sender_address

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _addr_hex(self, a) -> str:
        """Normalize address-like input into a hex string."""
        if isinstance(a, Address):
            return a.as_hex
        if isinstance(a, bytes):
            return Address(a).as_hex
        # assume str
        return a

    def _get_balance_int(self, account_hex: str) -> int:
        b = self.balances.get(account_hex)
        return int(b) if b is not None else 0

    # ──────────────────────────────────────────────
    # ERC20-like methods
    # ──────────────────────────────────────────────

    @gl.public.write
    def mint(self, to, amount: str) -> None:
        """Mint tokens to an address. Anyone can mint on testnet."""
        to_hex = self._addr_hex(to)
        current_int = self._get_balance_int(to_hex)
        amt_int = int(amount)
        self.balances[to_hex] = str(current_int + amt_int)
        self.total_supply = u256(int(self.total_supply) + amt_int)

    @gl.public.write
    def transfer(self, to, amount: str) -> None:
        """Transfer tokens from sender to recipient."""
        to_hex = self._addr_hex(to)
        sender_hex = gl.message.sender_address.as_hex

        sender_int = self._get_balance_int(sender_hex)
        amt_int = int(amount)
        if sender_int < amt_int:
            raise gl.vm.UserError(f"Insufficient balance: {sender_int} < {amt_int}")

        self.balances[sender_hex] = str(sender_int - amt_int)
        to_int = self._get_balance_int(to_hex)
        self.balances[to_hex] = str(to_int + amt_int)

    @gl.public.write
    def approve(self, spender, amount: str) -> None:
        owner_hex = gl.message.sender_address.as_hex
        spender_hex = self._addr_hex(spender)
        key = f"{owner_hex}:{spender_hex}"
        self.allowances[key] = amount

    @gl.public.write
    def transfer_from(self, from_addr, to, amount: str) -> None:
        spender_hex = gl.message.sender_address.as_hex
        from_hex = self._addr_hex(from_addr)
        to_hex = self._addr_hex(to)

        key = f"{from_hex}:{spender_hex}"
        allowance = self.allowances.get(key)
        allowance_int = int(allowance) if allowance is not None else 0
        amt_int = int(amount)
        if allowance_int < amt_int:
            raise gl.vm.UserError("Insufficient allowance")

        from_int = self._get_balance_int(from_hex)
        if from_int < amt_int:
            raise gl.vm.UserError("Insufficient balance")

        self.balances[from_hex] = str(from_int - amt_int)
        to_int = self._get_balance_int(to_hex)
        self.balances[to_hex] = str(to_int + amt_int)
        self.allowances[key] = str(allowance_int - amt_int)

    @gl.public.view
    def balance_of(self, account) -> str:
        account_hex = self._addr_hex(account)
        return str(self._get_balance_int(account_hex))

    @gl.public.view
    def allowance(self, owner_addr, spender) -> str:
        owner_hex = self._addr_hex(owner_addr)
        spender_hex = self._addr_hex(spender)
        key = f"{owner_hex}:{spender_hex}"
        a = self.allowances.get(key)
        return a if a is not None else "0"

    @gl.public.view
    def get_info(self) -> str:
        return json.dumps({
            "name": self.name,
            "symbol": self.symbol,
            "decimals": int(self.decimals),
            "total_supply": str(self.total_supply),
            "owner": self.owner.as_hex,
        })
