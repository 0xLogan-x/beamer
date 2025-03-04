import brownie
import pytest
from brownie import web3
from brownie.convert.datatypes import HexString
from web3.constants import ADDRESS_ZERO

from beamer.agent.typing import RequestId
from beamer.tests.agent.utils import make_address
from beamer.tests.constants import FILL_ID, FILL_ID_EMPTY, RM_R_FIELD_FILL_ID, RM_R_FIELD_FILLER
from beamer.tests.util import alloc_accounts, alloc_whitelisted_accounts, create_request_id


@pytest.mark.parametrize("amount", [100, 99, 101])
@pytest.mark.parametrize("forward_state", [True])
def test_l1_resolution_correct_id(request_manager, fill_manager, token, amount):
    requested_amount = 100
    nonce = 23
    (receiver,) = alloc_accounts(1)
    (filler,) = alloc_whitelisted_accounts(1, {fill_manager})
    chain_id = brownie.web3.eth.chain_id

    token.mint(filler, amount, {"from": filler})
    token.approve(fill_manager.address, amount, {"from": filler})

    fill_tx = fill_manager.fillRequest(
        chain_id, token.address, receiver, amount, nonce, {"from": filler}
    )
    fill_id = fill_tx.return_value

    request_id = create_request_id(
        chain_id,
        chain_id,
        token.address,
        receiver.address,
        requested_amount,
        nonce,
    )

    expected_address = ADDRESS_ZERO
    expected_fill_id = FILL_ID_EMPTY

    if amount == requested_amount:
        expected_address = filler
        expected_fill_id = fill_id

    request = request_manager.requests(request_id)

    assert request[RM_R_FIELD_FILLER] == expected_address
    assert request[RM_R_FIELD_FILL_ID] == HexString(expected_fill_id, "bytes32")


@pytest.mark.parametrize("forward_state", [True])
def test_l1_non_fill_proof(fill_manager, request_manager):
    request_id = "1234" + "00" * 30
    fill_id = "5678" + "00" * 30
    chain_id = brownie.web3.eth.chain_id

    fill_manager.invalidateFill(request_id, fill_id, chain_id)
    assert request_manager.isInvalidFill(request_id, fill_id)


def test_invalidation_before_and_after_resolution(contracts, request_manager):
    contracts.l1_messenger.setLastSender(contracts.resolver.address)

    address = make_address()
    request_id = RequestId(31 * b"0" + b"1")
    chain_id = web3.eth.chain_id

    assert not request_manager.isInvalidFill(request_id, FILL_ID)
    request_manager.invalidateFill(request_id, FILL_ID, chain_id, {"from": contracts.l1_messenger})

    # Fill must be invalidated
    assert request_manager.isInvalidFill(request_id, FILL_ID)

    assert request_manager.requests(request_id)[RM_R_FIELD_FILLER] == ADDRESS_ZERO

    request_manager.resolveRequest(
        request_id, FILL_ID, chain_id, address, {"from": contracts.l1_messenger}
    )

    # Resolution validates fill again
    assert not request_manager.isInvalidFill(request_id, FILL_ID)
    assert request_manager.requests(request_id)[RM_R_FIELD_FILLER] == address

    # Invalidation of a resolved request should fail
    with brownie.reverts("Cannot invalidate resolved fills"):
        request_manager.invalidateFill(
            request_id, FILL_ID, chain_id, {"from": contracts.l1_messenger}
        )


def test_restricted_calls(contracts, resolver, request_manager):
    """Test that important contract calls cannot be invoked by a random caller."""
    (caller,) = alloc_accounts(1)

    # fill_manager -> messenger1 -> L1 resolver ->
    # messenger2 -> request manager

    with brownie.reverts("RestrictedCalls: call disallowed"):
        contracts.l2_messenger.sendMessage(resolver.address, b"")

    with brownie.reverts("RestrictedCalls: call disallowed"):
        contracts.resolver.resolve(
            0, 0, brownie.chain.id, brownie.chain.id, caller, {"from": caller}
        )

    with brownie.reverts("RestrictedCalls: call disallowed"):
        contracts.resolver.resolve(
            0, 0, brownie.chain.id, brownie.chain.id, ADDRESS_ZERO, {"from": caller}
        )

    with brownie.reverts("RestrictedCalls: call disallowed"):
        contracts.l1_messenger.sendMessage(request_manager.address, b"")

    with brownie.reverts("RestrictedCalls: call disallowed"):
        contracts.request_manager.resolveRequest(0, 0, brownie.chain.id, caller, {"from": caller})

    with brownie.reverts("RestrictedCalls: messenger not set"):
        contracts.request_manager.resolveRequest(0, 0, 0, caller, {"from": caller})

    with brownie.reverts("RestrictedCalls: messenger not set"):
        contracts.request_manager.invalidateFill(0, 0, 0, {"from": caller})
