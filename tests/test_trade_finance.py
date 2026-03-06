"""Trade finance system tests (GenLayer direct mode).

Covers:
- StableCoin mint/transfer/approve/transfer_from
- TradeFinanceDeal full lifecycle
- Access control
- Dispute flow with mocked AI responses
- Edge cases and invalid state transitions

Notes:
- Do NOT import from conftest — pytest fixtures are injected automatically.
- Import genlayer types only after at least one direct_deploy() has happened.
"""

import json
import pytest


class TestStableCoin:
    def test_mint_and_balance(self, deploy_stablecoin, direct_vm):
        coin, exporter, importer, stranger = deploy_stablecoin

        assert coin.balance_of(exporter.as_hex) == "0"

        with direct_vm.prank(stranger):
            coin.mint(exporter.as_hex, "1000")

        assert coin.balance_of(exporter.as_hex) == "1000"

        info = json.loads(coin.get_info())
        assert info["symbol"] == "sBOB"
        assert info["name"] == "Synthetic Boliviano"
        assert info["total_supply"] == "1000"

    def test_transfer(self, deploy_stablecoin, direct_vm):
        coin, exporter, importer, stranger = deploy_stablecoin

        with direct_vm.prank(exporter):
            coin.mint(exporter.as_hex, "1000")

        with direct_vm.prank(exporter):
            coin.transfer(importer.as_hex, "250")

        assert coin.balance_of(exporter.as_hex) == "750"
        assert coin.balance_of(importer.as_hex) == "250"

    def test_transfer_insufficient_balance_reverts(self, deploy_stablecoin, direct_vm):
        coin, exporter, importer, stranger = deploy_stablecoin

        with direct_vm.expect_revert("Insufficient balance"):
            with direct_vm.prank(exporter):
                coin.transfer(importer.as_hex, "1")

    def test_approve_and_transfer_from(self, deploy_stablecoin, direct_vm):
        coin, exporter, importer, stranger = deploy_stablecoin

        # Exporter gets tokens
        with direct_vm.prank(exporter):
            coin.mint(exporter.as_hex, "1000")

        # Exporter approves importer to spend 300
        with direct_vm.prank(exporter):
            coin.approve(importer.as_hex, "300")

        assert coin.allowance(exporter.as_hex, importer.as_hex) == "300"

        # Importer spends 200 via transfer_from
        with direct_vm.prank(importer):
            coin.transfer_from(exporter.as_hex, stranger.as_hex, "200")

        assert coin.balance_of(exporter.as_hex) == "800"
        assert coin.balance_of(stranger.as_hex) == "200"
        assert coin.allowance(exporter.as_hex, importer.as_hex) == "100"

    def test_transfer_from_insufficient_allowance_reverts(self, deploy_stablecoin, direct_vm):
        coin, exporter, importer, stranger = deploy_stablecoin

        with direct_vm.prank(exporter):
            coin.mint(exporter.as_hex, "1000")

        # No approval
        with direct_vm.expect_revert("Insufficient allowance"):
            with direct_vm.prank(importer):
                coin.transfer_from(exporter.as_hex, stranger.as_hex, "1")


class TestTradeFinanceDealLifecycle:
    def test_initial_state(self, deploy_deal):
        deal, exporter, importer, stranger = deploy_deal
        assert deal.status == "created"
        assert deal.invoice_currency == "BOB"
        assert deal.settlement_currency == "PEN"
        assert deal.invoice_amount == "500000"

    def test_importer_funds_escrow(self, deploy_deal, direct_vm):
        deal, exporter, importer, stranger = deploy_deal

        with direct_vm.prank(importer):
            deal.fund_escrow("200000")

        assert deal.status == "funded"
        assert deal.escrow_amount == "200000"

    def test_exporter_submits_shipment(self, funded_deal, direct_vm):
        deal, exporter, importer, stranger = funded_deal

        with direct_vm.prank(exporter):
            deal.submit_shipment("{\"bl\":\"TEST\"}")

        assert deal.status == "shipped"
        assert deal.delivery_proof != ""

    def test_importer_confirms_delivery(self, shipped_deal, direct_vm):
        deal, exporter, importer, stranger = shipped_deal

        with direct_vm.prank(importer):
            deal.confirm_delivery("{\"confirmed\":true}")

        assert deal.status == "delivered"
        assert deal.receipt_confirmation != ""

    def test_settle_with_mocked_ai(self, delivered_deal, direct_vm):
        deal, exporter, importer, stranger = delivered_deal

        # Web fetch MUST be mocked in direct mode
        direct_vm.mock_web(
            r"open\.er-api\.com/v6/latest/BOB",
            {
                "status": 200,
                "body": json.dumps({"result": "success", "rates": {"PEN": 0.412}}),
            },
        )

        with direct_vm.prank(exporter):
            deal.settle()

        assert deal.status == "settled"
        assert deal.settlement_rate == "0.412"
        assert deal.final_amount == "206000.0"
        assert deal.rate_source == "open.er-api.com"


class TestAccessControl:
    def test_only_importer_can_fund(self, deploy_deal, direct_vm):
        deal, exporter, importer, stranger = deploy_deal

        with direct_vm.expect_revert("Only importer can fund escrow"):
            with direct_vm.prank(exporter):
                deal.fund_escrow("1")

        with direct_vm.expect_revert("Only importer can fund escrow"):
            with direct_vm.prank(stranger):
                deal.fund_escrow("1")

    def test_only_exporter_can_submit_shipment(self, funded_deal, direct_vm):
        deal, exporter, importer, stranger = funded_deal

        with direct_vm.expect_revert("Only exporter can submit shipment proof"):
            with direct_vm.prank(importer):
                deal.submit_shipment("proof")

    def test_only_importer_can_confirm_delivery(self, shipped_deal, direct_vm):
        deal, exporter, importer, stranger = shipped_deal

        with direct_vm.expect_revert("Only importer can confirm delivery"):
            with direct_vm.prank(exporter):
                deal.confirm_delivery("ok")


class TestDisputeFlow:
    def test_cannot_dispute_before_shipment(self, deploy_deal, direct_vm):
        deal, exporter, importer, stranger = deploy_deal

        with direct_vm.expect_revert("Can only dispute after shipment"):
            with direct_vm.prank(importer):
                deal.raise_dispute("too early")

    def test_dispute_and_link_court(self, disputed_deal, direct_vm):
        deal, exporter, importer, stranger = disputed_deal

        # Link a court case
        fake_court_addr = "0x" + "aa" * 20
        with direct_vm.prank(exporter):
            deal.link_court_case(fake_court_addr)

        assert deal.court_contract_address == fake_court_addr
        assert deal.status == "disputed"

    def test_resolve_from_court_requires_linked_case(self, disputed_deal, direct_vm):
        deal, exporter, importer, stranger = disputed_deal

        with direct_vm.expect_revert("No court case linked"):
            deal.resolve_from_court()

    def test_resolve_from_court_exporter_wins(self, court_linked_deal, monkeypatch):
        deal, exporter, importer, stranger = court_linked_deal

        # In direct mode, cross-contract calls (CallContract) are not executed unless a hook is installed.
        # We patch gl.get_contract_at to return a mock court proxy.
        import genlayer.gl as gl_mod

        mock_verdict = json.dumps({
            "verdict": "TRUE",
            "reasoning": "Exporter provided valid shipping documents and quality certificates.",
            "status": "resolved",
        })

        class _MockCourt:
            def view(self):
                return self

            def get_verdict(self):
                return mock_verdict

        monkeypatch.setattr(gl_mod, "get_contract_at", lambda addr: _MockCourt())

        deal.resolve_from_court()

        assert deal.status == "resolved"
        assert deal.court_verdict == "TRUE"
        assert deal.court_reasoning != ""


class TestEdgeCases:
    def test_double_fund_reverts(self, deploy_deal, direct_vm):
        deal, exporter, importer, stranger = deploy_deal

        with direct_vm.prank(importer):
            deal.fund_escrow("200000")

        with direct_vm.expect_revert("Deal not in created state"):
            with direct_vm.prank(importer):
                deal.fund_escrow("1")

    def test_wrong_state_transitions_revert(self, deploy_deal, direct_vm):
        deal, exporter, importer, stranger = deploy_deal

        # Can't confirm delivery before shipment
        with direct_vm.expect_revert("Goods not shipped yet"):
            with direct_vm.prank(importer):
                deal.confirm_delivery("ok")

        # Can't submit shipment before funding
        with direct_vm.expect_revert("Deal not funded yet"):
            with direct_vm.prank(exporter):
                deal.submit_shipment("proof")

        # Can't settle before delivery confirmation
        with direct_vm.expect_revert("Delivery not confirmed"):
            with direct_vm.prank(exporter):
                deal.settle()

    def test_cancel_only_exporter_before_funding(self, deploy_deal, direct_vm):
        deal, exporter, importer, stranger = deploy_deal

        with direct_vm.expect_revert("Only exporter can cancel"):
            with direct_vm.prank(importer):
                deal.cancel()

        with direct_vm.prank(exporter):
            deal.cancel()

        assert deal.status == "cancelled"

    def test_cancel_after_funding_reverts(self, funded_deal, direct_vm):
        deal, exporter, importer, stranger = funded_deal

        with direct_vm.expect_revert("Can only cancel before funding"):
            with direct_vm.prank(exporter):
                deal.cancel()
