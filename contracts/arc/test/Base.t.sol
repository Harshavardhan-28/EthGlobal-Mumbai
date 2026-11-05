// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {WorkingCapitalFacility} from "../src/WorkingCapitalFacility.sol";
import {TestUSDC} from "../src/mocks/TestUSDC.sol";

abstract contract FacilityBase is Test {
    WorkingCapitalFacility internal facility;
    TestUSDC internal usdc;

    uint256 internal coordinatorKey = 0xC0FFEE;
    address internal coordinator;
    address internal operator = makeAddr("operator");
    address internal treasury = makeAddr("treasury");
    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");
    address internal charlie = makeAddr("charlie");
    address internal customer = makeAddr("retailco");
    address internal research = makeAddr("research-agent");
    address internal compute = makeAddr("compute-agent");
    address internal dataAgent = makeAddr("data-agent");

    bytes32 internal constant FACILITY = keccak256("retailco-net30");
    bytes32 internal constant RECEIVABLE = keccak256("RetailCo:INV-2048");
    bytes32 internal constant MERCHANT = keccak256("hedera:0.0.5005:briefly-data");
    uint128 internal constant USD = 1e6;

    function setUp() public virtual {
        vm.warp(1_790_000_000);
        coordinator = vm.addr(coordinatorKey);
        usdc = new TestUSDC(address(this));
        facility = new WorkingCapitalFacility(address(usdc), coordinator, address(this));
        for (uint256 i; i < 3; ++i) {
            address lender = [alice, bob, charlie][i];
            facility.setLenderApproval(lender, true); // KYC-approved lenders
            usdc.mint(lender, 10_000 * USD);
            vm.prank(lender);
            usdc.approve(address(facility), type(uint256).max);
        }
        usdc.mint(customer, 20_000 * USD);
        vm.prank(customer);
        usdc.approve(address(facility), type(uint256).max);
    }

    // ------------------------------------------------------------ attestations
    function receivableAttestation() internal view returns (WorkingCapitalFacility.ReceivableAttestation memory) {
        return WorkingCapitalFacility.ReceivableAttestation({
            facilityId: FACILITY,
            receivableId: RECEIVABLE,
            operator: operator,
            treasury: treasury,
            faceValue: 10_000 * USD,
            advanceRateBps: 7_500,
            maturity: uint64(block.timestamp + 30 days),
            verificationExpiry: uint64(block.timestamp + 30 days),
            hederaTxHash: keccak256("0.0.7001@1790000000.000000001"),
            hcsSequence: 1,
            expiry: uint64(block.timestamp + 10 minutes)
        });
    }

    function fundingAttestation(address lender, uint128 principal, uint16 aprBps)
        internal
        view
        returns (WorkingCapitalFacility.FundingAttestation memory)
    {
        return WorkingCapitalFacility.FundingAttestation({
            facilityId: FACILITY,
            lender: lender,
            principal: principal,
            aprBps: aprBps,
            minTermDays: 30,
            strategyHash: keccak256(abi.encode(lender, aprBps)),
            expiry: uint64(block.timestamp + 10 minutes)
        });
    }

    function spendAttestation(bytes32 intentId, uint128 amount)
        internal
        view
        returns (WorkingCapitalFacility.SpendAttestation memory)
    {
        return WorkingCapitalFacility.SpendAttestation({
            facilityId: FACILITY,
            intentId: intentId,
            amount: amount,
            hederaTxHash: keccak256(abi.encode("x402", intentId)),
            hcsSequence: 7,
            expiry: uint64(block.timestamp + 10 minutes)
        });
    }

    function sign(bytes32 digest) internal view returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(coordinatorKey, digest);
        return abi.encodePacked(r, s, v);
    }

    function signReceivable(WorkingCapitalFacility.ReceivableAttestation memory a)
        internal
        view
        returns (bytes memory)
    {
        return sign(this.hashReceivableExt(a));
    }

    function signFunding(WorkingCapitalFacility.FundingAttestation memory a) internal view returns (bytes memory) {
        return sign(this.hashFundingExt(a));
    }

    function signSpend(WorkingCapitalFacility.SpendAttestation memory a) internal view returns (bytes memory) {
        return sign(this.hashSpendExt(a));
    }

    // calldata adapters so memory structs can use the facility's calldata hashers
    function hashReceivableExt(WorkingCapitalFacility.ReceivableAttestation calldata a)
        external
        view
        returns (bytes32)
    {
        return facility.hashReceivable(a);
    }

    function hashFundingExt(WorkingCapitalFacility.FundingAttestation calldata a) external view returns (bytes32) {
        return facility.hashFunding(a);
    }

    function hashSpendExt(WorkingCapitalFacility.SpendAttestation calldata a) external view returns (bytes32) {
        return facility.hashSpend(a);
    }

    // ------------------------------------------------------------ flows
    function openFacility() internal {
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        facility.openFacility(a, signReceivable(a));
    }

    function fund(address lender, uint128 principal, uint16 aprBps) internal {
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(lender, principal, aprBps);
        bytes memory sig = signFunding(a);
        vm.prank(lender);
        facility.fundDirect(a, sig);
    }

    function openAndFund() internal {
        openFacility();
        fund(alice, 4_000 * USD, 700);
        fund(bob, 2_000 * USD, 850);
    }

    function assignDemoBudgets() internal {
        vm.startPrank(operator);
        facility.assignBudget(FACILITY, research, 1_000 * USD, USD / 2, 25 * USD);
        facility.assignBudget(FACILITY, compute, 3_000 * USD, 6_000 * USD, 6_000 * USD);
        facility.assignBudget(FACILITY, dataAgent, 1_000 * USD, 25 * USD, 100 * USD);
        facility.setMerchant(FACILITY, research, MERCHANT, true);
        facility.setMerchant(FACILITY, compute, MERCHANT, true);
        vm.stopPrank();
    }

    function reserve(bytes32 intentId, address agent, uint128 amount) internal {
        vm.prank(coordinator);
        facility.reserveSpend(FACILITY, intentId, agent, MERCHANT, amount, 5 minutes);
    }

    function commit(bytes32 intentId, uint128 amount) internal {
        WorkingCapitalFacility.SpendAttestation memory a = spendAttestation(intentId, amount);
        facility.commitSpend(a, signSpend(a));
    }
}
