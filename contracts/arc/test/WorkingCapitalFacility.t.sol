// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {FacilityBase} from "./Base.t.sol";
import {WorkingCapitalFacility} from "../src/WorkingCapitalFacility.sol";
import {LenderNote} from "../src/LenderNote.sol";

contract WorkingCapitalFacilityTest is FacilityBase {
    // ------------------------------------------------------------ verify
    function test_OpenFacilitySetsBorrowingBase() public {
        openFacility();
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertEq(f.borrowingBase, 7_500 * USD);
        assertEq(uint8(f.status), uint8(WorkingCapitalFacility.Status.Open));
        assertTrue(facility.usedReceivables(RECEIVABLE));
    }

    function test_RevertWhen_ReceivableFinancedTwice() public {
        openFacility();
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        a.facilityId = keccak256("second-facility");
        a.hcsSequence = 2; // fresh attestation, same receivable
        bytes memory sig = signReceivable(a);
        vm.expectRevert(WorkingCapitalFacility.DuplicateReceivable.selector);
        facility.openFacility(a, sig);
    }

    function test_RevertWhen_AttestationReplayed() public {
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        bytes memory sig = signReceivable(a);
        facility.openFacility(a, sig);
        vm.expectRevert(WorkingCapitalFacility.AttestationReplayed.selector);
        facility.openFacility(a, sig);
    }

    function test_RevertWhen_AttestationExpired() public {
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        bytes memory sig = signReceivable(a);
        vm.warp(a.expiry + 1);
        vm.expectRevert(WorkingCapitalFacility.AttestationExpired.selector);
        facility.openFacility(a, sig);
    }

    function test_RevertWhen_SignerIsNotCoordinator() public {
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, this.hashReceivableExt(a));
        vm.expectRevert(WorkingCapitalFacility.InvalidSignature.selector);
        facility.openFacility(a, abi.encodePacked(r, s, v));
    }

    function test_RevertWhen_VerificationIsStale() public {
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        a.verificationExpiry = uint64(block.timestamp);
        bytes memory sig = signReceivable(a);
        vm.expectRevert(WorkingCapitalFacility.StaleReceivable.selector);
        facility.openFacility(a, sig);
    }

    // ------------------------------------------------------------ fund
    function test_FundDirectIssuesNotesAndActivates() public {
        openAndFund();
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertEq(f.funded, 6_000 * USD);
        assertEq(uint8(f.status), uint8(WorkingCapitalFacility.Status.Active));
        LenderNote note = facility.note();
        assertEq(note.balanceOf(alice), 4_000 * USD);
        assertEq(note.balanceOf(bob), 2_000 * USD);
        assertEq(usdc.balanceOf(address(facility)), 6_000 * USD);
    }

    function test_RevertWhen_FundingExceedsBorrowingBase() public {
        openAndFund();
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(charlie, 1_501 * USD, 1_300);
        bytes memory sig = signFunding(a);
        vm.prank(charlie);
        vm.expectRevert(WorkingCapitalFacility.BorrowingBaseExceeded.selector);
        facility.fundDirect(a, sig);
    }

    function test_RevertWhen_FundingCallerIsNotAttestedLender() public {
        openFacility();
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(alice, 4_000 * USD, 700);
        bytes memory sig = signFunding(a);
        vm.prank(bob);
        vm.expectRevert(WorkingCapitalFacility.Unauthorized.selector);
        facility.fundDirect(a, sig);
    }

    function test_NotesAreNotTransferableBetweenLenders() public {
        openAndFund();
        LenderNote note = facility.note();
        vm.prank(alice);
        vm.expectRevert(LenderNote.NonTransferable.selector);
        note.transfer(bob, 1);
    }

    function test_RevertWhen_FundViaAquaWithoutRouter() public {
        openFacility();
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(alice, 4_000 * USD, 700);
        bytes memory sig = signFunding(a);
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.AquaNotConfigured.selector);
        facility.fundViaAqua(a, sig, "", 4_001 * USD);
    }

    // ------------------------------------------------------------ budgets and spend
    function test_BudgetsCannotExceedFundedPrincipal() public {
        openAndFund();
        assignDemoBudgets();
        vm.prank(operator);
        vm.expectRevert(WorkingCapitalFacility.BudgetCeilingExceeded.selector);
        facility.assignBudget(FACILITY, makeAddr("extra"), 1_000 * USD + 1, 0, 0);
    }

    function test_ResearchPaymentReservesThenCommits() public {
        openAndFund();
        assignDemoBudgets();
        bytes32 intent = keccak256("research-0.25");
        reserve(intent, research, USD / 4);
        assertEq(facility.availableCredit(FACILITY), 6_000 * USD - USD / 4);
        commit(intent, USD / 4);
        WorkingCapitalFacility.Budget memory b = facility.budget(FACILITY, research);
        assertEq(b.spent, USD / 4);
        assertEq(b.reserved, 0);
        assertEq(usdc.balanceOf(treasury), USD / 4);
    }

    function test_RevertWhen_CommitReplayed() public {
        openAndFund();
        assignDemoBudgets();
        bytes32 intent = keccak256("once");
        reserve(intent, research, USD / 4);
        WorkingCapitalFacility.SpendAttestation memory a = spendAttestation(intent, USD / 4);
        bytes memory sig = signSpend(a);
        facility.commitSpend(a, sig);
        vm.expectRevert(WorkingCapitalFacility.AttestationReplayed.selector);
        facility.commitSpend(a, sig);
    }

    function test_ComputeOverspendRejectedAsAgentBudgetExceeded() public {
        openAndFund();
        assignDemoBudgets();
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.AgentBudgetExceeded.selector);
        facility.reserveSpend(FACILITY, keccak256("compute-5500"), compute, MERCHANT, 5_500 * USD, 5 minutes);
    }

    function test_LeadAgentIsBoundByFacilityCeiling() public {
        openAndFund();
        assignDemoBudgets();
        vm.prank(operator);
        facility.setMerchant(FACILITY, operator, MERCHANT, true);
        reserve(keccak256("r"), research, USD / 4);
        commit(keccak256("r"), USD / 4);
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.GlobalCreditLimitExceeded.selector);
        facility.reserveSpend(FACILITY, keccak256("lead"), operator, MERCHANT, 6_000 * USD, 5 minutes);
        // The same lead agent can still draw what is left under the ceiling.
        vm.prank(coordinator);
        facility.reserveSpend(FACILITY, keccak256("lead-ok"), operator, MERCHANT, 6_000 * USD - USD / 4, 5 minutes);
        assertEq(facility.availableCredit(FACILITY), 0);
    }

    function test_PerTransactionDailyAndMerchantLimits() public {
        openAndFund();
        assignDemoBudgets();
        vm.startPrank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.AgentBudgetExceeded.selector);
        facility.reserveSpend(FACILITY, keccak256("ab"), dataAgent, MERCHANT, 1_001 * USD, 5 minutes);
        vm.expectRevert(WorkingCapitalFacility.TransactionLimitExceeded.selector);
        facility.reserveSpend(FACILITY, keccak256("tx"), research, MERCHANT, USD, 5 minutes);
        vm.expectRevert(WorkingCapitalFacility.MerchantNotAllowed.selector);
        facility.reserveSpend(FACILITY, keccak256("m"), research, keccak256("other-merchant"), USD / 4, 5 minutes);
        vm.stopPrank();
        for (uint256 i; i < 50; ++i) {
            reserve(keccak256(abi.encode("day", i)), research, USD / 2);
        }
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.DailyLimitExceeded.selector);
        facility.reserveSpend(FACILITY, keccak256("day-51"), research, MERCHANT, USD / 2, 5 minutes);
        vm.warp(block.timestamp + 1 days);
        reserve(keccak256("next-day"), research, USD / 2);
    }

    function test_TimeoutReleaseRestoresBudget() public {
        openAndFund();
        assignDemoBudgets();
        bytes32 intent = keccak256("stuck");
        reserve(intent, research, USD / 4);
        vm.expectRevert(WorkingCapitalFacility.ReservationNotExpired.selector);
        facility.releaseSpend(FACILITY, intent);
        vm.warp(block.timestamp + 5 minutes);
        facility.releaseSpend(FACILITY, intent); // anyone may release after the timeout
        assertEq(facility.availableCredit(FACILITY), 6_000 * USD);
        WorkingCapitalFacility.SpendAttestation memory a = spendAttestation(intent, USD / 4);
        bytes memory sig = signSpend(a);
        vm.expectRevert(WorkingCapitalFacility.ReservationNotPending.selector);
        facility.commitSpend(a, sig);
    }

    function test_CoordinatorCanReleaseImmediatelyOnConfirmedFailure() public {
        openAndFund();
        assignDemoBudgets();
        bytes32 intent = keccak256("failed");
        reserve(intent, research, USD / 4);
        vm.prank(coordinator);
        facility.releaseSpend(FACILITY, intent);
        assertEq(facility.budget(FACILITY, research).reserved, 0);
    }

    function test_PauseBlocksNewReservations() public {
        openAndFund();
        assignDemoBudgets();
        facility.pause();
        vm.prank(coordinator);
        vm.expectRevert();
        facility.reserveSpend(FACILITY, keccak256("p"), research, MERCHANT, USD / 4, 5 minutes);
    }

    // ------------------------------------------------------------ repay
    function test_DemoWaterfallPaysLendersBeforeOperator() public {
        openAndFund();
        assignDemoBudgets();
        reserve(keccak256("r"), research, USD / 4);
        commit(keccak256("r"), USD / 4);
        uint256 aliceBefore = usdc.balanceOf(alice);
        uint256 bobBefore = usdc.balanceOf(bob);

        vm.prank(customer);
        facility.repay(FACILITY, 10_000 * USD);

        assertEq(usdc.balanceOf(alice) - aliceBefore, 4_023_013_698); // 4,000 + 7% x 30/365
        assertEq(usdc.balanceOf(bob) - bobBefore, 2_013_972_602); // 2,000 + 8.5% x 30/365
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertEq(f.lenderPaid, f.lenderDue);
        assertEq(f.operatorPaid, 10_000 * USD - f.lenderDue);
        assertEq(uint8(f.status), uint8(WorkingCapitalFacility.Status.Repaid));
        // Operator receives the remainder plus the undrawn facility balance on close.
        assertEq(usdc.balanceOf(operator), f.operatorPaid + (6_000 * USD - USD / 4));
        assertEq(usdc.balanceOf(address(facility)), 0);
        assertEq(facility.note().balanceOf(alice), 0);
        assertEq(facility.note().totalSupply(), 0);
    }

    function test_PartialRepaymentPaysNothingToOperator() public {
        openAndFund();
        vm.prank(customer);
        facility.repay(FACILITY, 3_000 * USD);
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertEq(f.lenderPaid, 3_000 * USD);
        assertEq(f.operatorPaid, 0);
        assertEq(uint8(f.status), uint8(WorkingCapitalFacility.Status.Active));
        // Pro rata: Alice holds ~2/3 of the dues.
        assertApproxEqAbs(usdc.balanceOf(alice), 6_000 * USD + 1_999_183_118, 1);

        vm.prank(customer);
        facility.repay(FACILITY, 7_000 * USD);
        f = facility.facility(FACILITY);
        assertEq(f.lenderPaid, f.lenderDue);
        assertEq(f.operatorPaid, 10_000 * USD - f.lenderDue);
        assertEq(uint8(f.status), uint8(WorkingCapitalFacility.Status.Repaid));
    }

    function test_FeeUsesActualTermWhenLongerThanMinimum() public {
        openFacility();
        fund(alice, 4_000 * USD, 700);
        vm.warp(block.timestamp + 45 days);
        assertEq(facility.lenderOutstanding(FACILITY), 4_000 * USD + 34_520_547);
    }

    // ------------------------------------------------------------ fuzz
    function testFuzz_ReservationsNeverExceedBudgetOrCeiling(uint128 a, uint128 b, uint128 c) public {
        openAndFund();
        vm.startPrank(operator);
        facility.assignBudget(FACILITY, compute, 3_000 * USD, 0, 0);
        facility.setMerchant(FACILITY, compute, MERCHANT, true);
        vm.stopPrank();
        uint128[3] memory amounts = [a, b, c];
        for (uint256 i; i < 3; ++i) {
            uint128 amount = uint128(bound(amounts[i], 1, 4_000 * USD));
            vm.prank(coordinator);
            try facility.reserveSpend(FACILITY, keccak256(abi.encode(i)), compute, MERCHANT, amount, 5 minutes) {}
                catch {}
        }
        WorkingCapitalFacility.Budget memory bud = facility.budget(FACILITY, compute);
        assertLe(bud.reserved + bud.spent, bud.allocation);
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertLe(uint256(f.totalReserved) + f.totalSpent, f.funded);
    }

    function testFuzz_RepaymentConservesValue(uint128 first, uint128 second) public {
        openAndFund();
        first = uint128(bound(first, 1, 12_000 * USD));
        second = uint128(bound(second, 1, 8_000 * USD));
        uint256 before = usdc.balanceOf(alice) + usdc.balanceOf(bob) + usdc.balanceOf(operator);
        vm.startPrank(customer);
        facility.repay(FACILITY, first);
        if (uint8(facility.facility(FACILITY).status) == uint8(WorkingCapitalFacility.Status.Active)) {
            facility.repay(FACILITY, second);
        }
        vm.stopPrank();
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        uint256 afterBalances = usdc.balanceOf(alice) + usdc.balanceOf(bob) + usdc.balanceOf(operator);
        uint256 undrawnReturned = f.status == WorkingCapitalFacility.Status.Repaid ? f.funded - f.totalSpent : 0;
        assertEq(afterBalances - before, f.customerPaid + undrawnReturned);
        assertLe(f.lenderPaid, f.lenderDue);
        if (f.operatorPaid > 0) assertEq(f.lenderPaid, f.lenderDue); // operator is paid only after lenders
    }
}
