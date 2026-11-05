// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {WorkingCapitalFacility} from "../src/WorkingCapitalFacility.sol";
import {TestUSDC} from "../src/mocks/TestUSDC.sol";

/// @notice Deploys the facility. On Arc testnet pass ARC_USDC_ADDRESS (0x3600...0000).
///         Leave it unset on a local fork to deploy the labelled TestUSDC stand-in.
///         ARC_APPROVED_LENDERS is a comma-separated list of KYC-approved lender addresses.
///
///   forge script script/Deploy.s.sol --rpc-url arc_testnet --broadcast --private-key $ARC_PRIVATE_KEY
contract Deploy is Script {
    function run() external {
        address coordinator = vm.envAddress("ARC_COORDINATOR_ADDRESS");
        address usdc = vm.envOr("ARC_USDC_ADDRESS", address(0));
        address router = vm.envOr("AQUA_ROUTER_ADDRESS", address(0));
        address registry = vm.envOr("AQUA_REGISTRY_ADDRESS", address(0));
        address[] memory lenders = vm.envOr("ARC_APPROVED_LENDERS", ",", new address[](0));

        vm.startBroadcast();
        address deployer = msg.sender;
        if (usdc == address(0)) {
            usdc = address(new TestUSDC(deployer));
            console2.log("TestUSDC (fork only)", usdc);
        }
        WorkingCapitalFacility facility = new WorkingCapitalFacility(usdc, coordinator, deployer);
        if (router != address(0)) facility.setAqua(router, registry);
        for (uint256 i; i < lenders.length; ++i) {
            facility.setLenderApproval(lenders[i], true);
            console2.log("Approved lender", lenders[i]);
        }
        vm.stopBroadcast();

        console2.log("WorkingCapitalFacility", address(facility));
        console2.log("LenderNote", address(facility.note()));
        console2.log("USDC", usdc);
    }
}
