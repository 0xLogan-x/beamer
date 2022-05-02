import brownie
from brownie import chain

from beamer.tests.util import alloc_accounts, make_request

RM_FIELD_LP_FEE = 9
RM_FIELD_PROTOCOL_FEE = 10


# Using this makes sure that we get nonzero fees when making requests.
_NONZERO_FEE_DATA = 14_000, 15_000, 5e18


def test_fee_split_works(deployer, request_manager, token, claim_stake, claim_period):
    requester, claimer = alloc_accounts(2)
    transfer_amount = 23_000_000

    request_manager.updateFeeData(*_NONZERO_FEE_DATA)
    request_id = make_request(
        request_manager, token, requester, requester, transfer_amount, fee_data="standard"
    )

    lp_fee = request_manager.lpFee(transfer_amount)
    assert lp_fee > 0

    protocol_fee = request_manager.protocolFee(transfer_amount)
    assert protocol_fee > 0

    assert lp_fee + protocol_fee == request_manager.totalFee(transfer_amount)

    # The request is not claimed yet, so no beamer fee has been collected yet
    assert request_manager.collectedProtocolFees(token) == 0
    assert request_manager.requests(request_id)[RM_FIELD_LP_FEE] == lp_fee
    assert request_manager.requests(request_id)[RM_FIELD_PROTOCOL_FEE] == protocol_fee

    claim_tx = request_manager.claimRequest(request_id, 0, {"from": claimer, "value": claim_stake})
    claim_id = claim_tx.return_value

    # Update fees, which should not have any effect on the fee amounts that
    # were computed when the request was made.
    request_manager.updateFeeData(17e9, 145_000, 21_000, {"from": deployer})

    # Timetravel after claim period
    chain.mine(timedelta=claim_period)

    # Even if the requester calls withdraw, the funds go to the claimer
    withdraw_tx = request_manager.withdraw(claim_id, {"from": requester})
    assert "ClaimWithdrawn" in withdraw_tx.events
    assert request_manager.collectedProtocolFees(token) == protocol_fee
    assert token.balanceOf(request_manager) == protocol_fee
    assert token.balanceOf(claimer) == transfer_amount + lp_fee


def test_protocol_fee_is_zero(request_manager):
    # For the time being, the protocol fee percentage should be zero.
    assert request_manager.protocolFeePPM() == 0
    assert request_manager.protocolFee(23_000_000) == 0


def test_protocol_fee_withdrawable_by_owner(
    deployer, request_manager, token, claim_stake, claim_period
):
    owner = deployer
    requester, claimer = alloc_accounts(2)
    amount = 23_000_000
    request_id = make_request(
        request_manager, token, requester, requester, amount, fee_data=_NONZERO_FEE_DATA
    )
    protocol_fee = request_manager.requests(request_id)[RM_FIELD_PROTOCOL_FEE]

    with brownie.reverts("Ownable: caller is not the owner"):
        request_manager.withdrawProtocolFees(token, requester, {"from": requester})

    with brownie.reverts("Protocol fee is zero"):
        request_manager.withdrawProtocolFees(token, owner, {"from": owner})

    claim_tx = request_manager.claimRequest(request_id, 0, {"from": claimer, "value": claim_stake})
    claim_id = claim_tx.return_value

    chain.mine(timedelta=claim_period)

    with brownie.reverts("Protocol fee is zero"):
        request_manager.withdrawProtocolFees(token, owner, {"from": owner})

    request_manager.withdraw(claim_id, {"from": claimer})

    owner_token = token.balanceOf(owner)
    request_manager.withdrawProtocolFees(token.address, owner, {"from": owner})
    assert token.balanceOf(owner) == owner_token + protocol_fee


def test_fee_data_updatable_by_owner(deployer, request_manager):
    (requester,) = alloc_accounts(1)

    new_protocol_fee_ppm = 12_000
    new_lp_fee_ppm = 13_000
    new_min_lp_fee = 179e18

    with brownie.reverts("Ownable: caller is not the owner"):
        request_manager.updateFeeData(
            new_protocol_fee_ppm, new_lp_fee_ppm, new_min_lp_fee, {"from": requester}
        )

    request_manager.updateFeeData(
        new_protocol_fee_ppm, new_lp_fee_ppm, new_min_lp_fee, {"from": deployer}
    )
    assert request_manager.protocolFeePPM() == new_protocol_fee_ppm
    assert request_manager.lpFeePPM() == new_lp_fee_ppm
    assert request_manager.minLpFee() == new_min_lp_fee


def test_fee_reimbursed_on_expiration(request_manager, token):
    (requester,) = alloc_accounts(1)
    transfer_amount = 23_000_000
    validity_period = 60 * 5

    request_manager.updateFeeData(*_NONZERO_FEE_DATA)
    request_id = make_request(
        request_manager,
        token,
        requester,
        requester,
        transfer_amount,
        fee_data="standard",
        validity_period=validity_period,
    )

    total_fee = request_manager.totalFee(transfer_amount)
    assert total_fee > 0

    # Timetravel after validity period
    chain.mine(timedelta=validity_period)

    request_manager.withdrawExpiredRequest(request_id, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount + total_fee


def test_insufficient_lp_fee(request_manager, token):
    (requester,) = alloc_accounts(1)
    amount = 23_000_000
    validity_period = 60 * 5

    assert request_manager.lpFee(amount) > 0
    token.mint(requester, amount, {"from": requester})

    # Approve just the amount, ignoring the LP fee and the protocol fee.
    # This must cause createRequest to fail.
    token.approve(request_manager.address, amount, {"from": requester})

    with brownie.reverts("Insufficient allowance"):
        request_manager.createRequest(
            brownie.chain.id,
            token.address,
            token.address,
            requester,
            amount,
            validity_period,
            {"from": requester},
        )
