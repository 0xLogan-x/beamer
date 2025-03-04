import brownie
import pytest
from brownie import chain, web3
from brownie.convert import to_bytes
from eth_utils import to_hex

from beamer.agent.typing import ClaimId, FillId, RequestId, Termination
from beamer.tests.agent.utils import make_address
from beamer.tests.constants import (
    FILL_ID,
    RM_C_FIELD_TERMINATION,
    RM_R_FIELD_VALID_UNTIL,
    RM_T_FIELD_TRANSFER_LIMIT,
)
from beamer.tests.util import (
    alloc_accounts,
    alloc_whitelisted_accounts,
    earnings,
    make_request,
    update_token,
)


def test_request_invalid_target_chain(request_manager, token):
    (requester,) = alloc_accounts(1)
    with brownie.reverts("Target rollup not supported"):
        make_request(
            request_manager,
            target_chain_id=999,
            token=token,
            requester=requester,
            target_address=requester,
            amount=1,
        )

    assert request_manager.currentNonce() == 0
    make_request(
        request_manager,
        target_chain_id=web3.eth.chain_id,
        token=token,
        requester=requester,
        target_address=requester,
        amount=1,
    )
    assert request_manager.currentNonce() == 1


def test_claim(token, request_manager, claim_stake, deployer):
    """Test that making a claim creates correct claim and emits event"""
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(
        request_manager, token=token, requester=requester, target_address=requester, amount=1
    )

    with brownie.reverts("Ownable: caller is not the owner"):
        request_manager.addAllowedLp(claimer, {"from": requester})

    whitelist_tx = request_manager.addAllowedLp(claimer, {"from": deployer})
    assert "LpAdded" in whitelist_tx.events

    claim_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim_tx.return_value
    expected_termination = (
        request_manager.claimPeriod() + web3.eth.get_block("latest")["timestamp"]
    )

    assert "ClaimMade" in claim_tx.events
    claim_event = claim_tx.events["ClaimMade"]
    assert RequestId(claim_event["requestId"]) == request_id
    assert claim_event["claimId"] == claim_id
    assert claim_event["claimer"] == claimer
    assert claim_event["claimerStake"] == claim_stake
    assert claim_event["lastChallenger"] == brownie.ZERO_ADDRESS
    assert claim_event["challengerStakeTotal"] == 0
    assert claim_event["termination"] == expected_termination
    assert claim_event["fillId"] == to_hex(FILL_ID)

    blacklist_tx = request_manager.removeAllowedLp(claimer, {"from": deployer})
    assert "LpRemoved" in blacklist_tx.events

    with brownie.reverts("Not allowed"):
        request_manager.claimRequest(request_id, FILL_ID, {"from": claimer, "value": claim_stake})


def test_claim_with_different_stakes(token, request_manager, claim_stake):
    """Test that only claims with the correct stake can be submitted"""
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 1)

    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    assert "ClaimMade" in claim.events

    with brownie.reverts("Invalid stake amount"):
        request_manager.claimRequest(
            request_id, FILL_ID, {"from": claimer, "value": claim_stake - 1}
        )

    with brownie.reverts("Invalid stake amount"):
        request_manager.claimRequest(
            request_id, FILL_ID, {"from": claimer, "value": claim_stake + 1}
        )

    with brownie.reverts("Invalid stake amount"):
        request_manager.claimRequest(request_id, FILL_ID, {"from": claimer})


def test_claim_on_behalf_of_other(token, request_manager, claim_stake, claim_period):
    """
    Test that making a claim on behalf of others creates correct claim
    and claimer can withdraw afterwards
    """
    (
        requester,
        initiator,
    ) = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})

    transfer_amount = 92
    initiator_eth_balance = web3.eth.get_balance(initiator.address)
    claimer_eth_balance = web3.eth.get_balance(claimer.address)

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(initiator) == 0
    assert token.balanceOf(claimer) == 0

    request_id = make_request(
        request_manager,
        token=token,
        requester=requester,
        target_address=requester,
        amount=transfer_amount,
    )

    claim_tx = request_manager.claimRequest(
        claimer, request_id, FILL_ID, {"from": initiator, "value": claim_stake}
    )
    claim_id = claim_tx.return_value
    expected_termination = (
        request_manager.claimPeriod() + web3.eth.get_block("latest")["timestamp"]
    )

    assert "ClaimMade" in claim_tx.events
    claim_event = claim_tx.events["ClaimMade"]
    assert RequestId(claim_event["requestId"]) == request_id
    assert claim_event["claimId"] == claim_id
    assert claim_event["claimer"] == claimer
    assert claim_event["claimerStake"] == claim_stake
    assert claim_event["lastChallenger"] == brownie.ZERO_ADDRESS
    assert claim_event["challengerStakeTotal"] == 0
    assert claim_event["termination"] == expected_termination
    assert claim_event["fillId"] == to_hex(FILL_ID)

    assert web3.eth.get_balance(request_manager.address) == claim_stake
    assert web3.eth.get_balance(initiator.address) == initiator_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance

    chain.mine(timedelta=claim_period)
    withdraw_tx = request_manager.withdraw(claim_id, {"from": claimer})

    assert "DepositWithdrawn" in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert request_manager.isWithdrawn(request_id)

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(initiator) == 0
    assert token.balanceOf(claimer) == transfer_amount

    assert web3.eth.get_balance(request_manager.address) == 0
    assert web3.eth.get_balance(initiator.address) == initiator_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance + claim_stake


def test_claimer_not_allowed(token, request_manager, claim_stake):
    """Test that making a claim cannot be done for addresses which are not whitelisted"""
    (requester, initiator, claimer) = alloc_accounts(3)
    request_id = make_request(
        request_manager, token=token, requester=requester, target_address=requester, amount=1
    )
    with brownie.reverts("Not allowed"):
        request_manager.claimRequest(
            claimer, request_id, FILL_ID, {"from": initiator, "value": claim_stake}
        )


def test_claim_challenge(request_manager, token, claim_stake):
    """Test challenging a claim"""
    requester, challenger = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 1)

    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )

    with brownie.reverts("Not enough stake provided"):
        request_manager.challengeClaim(
            claim.return_value, {"from": challenger, "value": claim_stake}
        )

    with brownie.reverts("Cannot challenge own claim"):
        request_manager.challengeClaim(
            claim.return_value, {"from": claimer, "value": claim_stake + 1}
        )

    with brownie.reverts("Not enough stake provided"):
        request_manager.challengeClaim(claim.return_value, {"from": challenger})

    # Do a proper challenge
    challenge = request_manager.challengeClaim(
        claim.return_value, {"from": challenger, "value": claim_stake + 1}
    )
    assert "ClaimMade" in challenge.events

    with brownie.reverts("Not eligible to outbid"):
        request_manager.challengeClaim(
            claim.return_value, {"from": challenger, "value": claim_stake + 1}
        )


def test_claim_counter_challenge(request_manager, token, claim_stake):
    """Test counter-challenging a challenge"""
    challenger, requester = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 1)

    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim.return_value

    with brownie.reverts("Not enough stake provided"):
        request_manager.challengeClaim(claim_id, {"from": requester, "value": claim_stake})

    # Do a proper challenge
    request_manager.challengeClaim(claim_id, {"from": challenger, "value": claim_stake + 1})

    # Only the claimer is eligible to outbid the challengers
    with brownie.reverts("Not eligible to outbid"):
        request_manager.challengeClaim(claim_id, {"from": requester})

    # The sender of the last challenge must not be able to challenge again
    with brownie.reverts("Not eligible to outbid"):
        request_manager.challengeClaim(claim_id, {"from": challenger})

    # The other party, in this case the claimer, must be able to re-challenge
    with brownie.reverts("Not enough stake provided"):
        request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake})
    outbid = request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})
    assert "ClaimMade" in outbid.events

    # Check that claimer is leading and cannot challenge own claim
    with brownie.reverts("Cannot challenge own claim"):
        request_manager.challengeClaim(claim_id, {"from": claimer, "value": 1})

    # The challenger must be able to re-challenge, but must increase the stake
    with brownie.reverts("Not enough stake provided"):
        request_manager.challengeClaim(claim_id, {"from": challenger, "value": claim_stake})
    outbid = request_manager.challengeClaim(
        claim_id, {"from": challenger, "value": claim_stake + 1}
    )
    assert "ClaimMade" in outbid.events


def test_claim_two_challengers(request_manager, token, claim_stake):
    """Test that two different challengers can challenge"""
    first_challenger, second_challenger, requester = alloc_accounts(3)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 1)

    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim.return_value

    # First challenger challenges
    outbid = request_manager.challengeClaim(
        claim_id, {"from": first_challenger, "value": claim_stake + 1}
    )
    assert "ClaimMade" in outbid.events

    # Claimer outbids again
    outbid = request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})
    assert "ClaimMade" in outbid.events

    # Check that claimer cannot be second challenger
    with brownie.reverts("Cannot challenge own claim"):
        request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})

    # Second challenger challenges
    outbid = request_manager.challengeClaim(
        claim_id, {"from": second_challenger, "value": claim_stake + 1}
    )
    assert "ClaimMade" in outbid.events


def test_claim_period_extension(
    request_manager,
    token,
    claim_stake,
    claim_period,
    finality_period,
    challenge_period_extension,
):
    """Test the extension of the claim/challenge period"""
    challenger, requester = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 1)

    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim.return_value

    def _get_claim_termination(_claim_id: ClaimId) -> Termination:
        return request_manager.claims(_claim_id)[RM_C_FIELD_TERMINATION]

    assert claim.timestamp + claim_period == _get_claim_termination(claim_id)

    challenge = request_manager.challengeClaim(
        claim_id, {"from": challenger, "value": claim_stake + 1}
    )
    challenge_period = finality_period + challenge_period_extension

    claim_termination = _get_claim_termination(claim_id)
    assert challenge.timestamp + challenge_period == claim_termination

    # Another challenge with big margin to the end of the termination
    # shouldn't increase the termination
    request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})

    assert claim_termination == _get_claim_termination(claim_id)

    # Another challenge by challenger also shouldn't increase the end of termination
    request_manager.challengeClaim(claim_id, {"from": challenger, "value": claim_stake + 1})
    assert claim_termination == _get_claim_termination(claim_id)

    # Timetravel close to end of challenge period
    chain.mine(timestamp=_get_claim_termination(claim_id) - 10)

    old_claim_termination = claim_termination
    # Claimer challenges close to the end of challenge
    # Should increase the challenge termination
    challenge = request_manager.challengeClaim(
        claim_id, {"from": claimer, "value": claim_stake + 1}
    )

    new_claim_termination = _get_claim_termination(claim_id)
    assert challenge.timestamp + challenge_period_extension == new_claim_termination
    assert new_claim_termination > old_claim_termination

    # Timetravel close to end of challenge period
    chain.mine(timestamp=_get_claim_termination(claim_id) - 10)

    old_claim_termination = new_claim_termination
    rechallenge = request_manager.challengeClaim(
        claim_id, {"from": challenger, "value": claim_stake + 1}
    )
    new_claim_termination = _get_claim_termination(claim_id)
    assert rechallenge.timestamp + challenge_period_extension == new_claim_termination
    assert new_claim_termination > old_claim_termination

    # Timetravel over the end of challenge period
    chain.mine(timestamp=_get_claim_termination(claim_id) + 1)

    with brownie.reverts("Claim expired"):
        request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})


def test_withdraw_nonexistent_claim(request_manager):
    """Test withdrawing a non-existent claim"""
    with brownie.reverts("claimId not valid"):
        request_manager.withdraw(1234, {"from": alloc_accounts(1)[0]})


def test_claim_nonexistent_request(request_manager):
    """Test claiming a non-existent request"""
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    with brownie.reverts("requestId not valid"):
        request_manager.claimRequest(1234, FILL_ID, {"from": claimer})


def test_claim_request_extension(request_manager, token, claim_stake):
    """
    Test that claiming is allowed around expiry
    and will revert after validUntil + claimRequestExtension
    """
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    token.mint(requester, 1, {"from": requester})
    request_id = make_request(request_manager, token, requester, requester, 1)

    valid_until = request_manager.requests(request_id)[RM_R_FIELD_VALID_UNTIL]
    claim_request_extension = request_manager.claimRequestExtension()
    # test that request expiration does not prevent claiming
    timestamps = [valid_until - 1, valid_until, valid_until + claim_request_extension - 1]

    for timestamp in timestamps:
        chain.sleep(timestamp - chain.time())
        request_manager.claimRequest(request_id, FILL_ID, {"from": claimer, "value": claim_stake})

    # validUntil + claimRequestExtension
    chain.sleep(1)
    with brownie.reverts("Request cannot be claimed anymore"):
        request_manager.claimRequest(request_id, FILL_ID, {"from": claimer, "value": claim_stake})


def test_withdraw_without_challenge(request_manager, token, claim_stake, claim_period):
    """Test withdraw when a claim was not challenged"""
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})

    transfer_amount = 23

    claimer_eth_balance = web3.eth.get_balance(claimer.address)

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(claimer) == 0

    assert web3.eth.get_balance(request_manager.address) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)
    claim_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim_tx.return_value

    assert web3.eth.get_balance(request_manager.address) == claim_stake
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance - claim_stake

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id, {"from": claimer})

    # Timetravel after claim period
    chain.mine(timedelta=claim_period)

    withdraw_tx = request_manager.withdraw(claim_id, {"from": claimer})
    assert "DepositWithdrawn" in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert request_manager.isWithdrawn(request_id)

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer) == transfer_amount

    assert web3.eth.get_balance(request_manager.address) == 0
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance

    # Another withdraw must fail
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim_id, {"from": claimer})


def test_withdraw_with_challenge(
    request_manager, token, claim_stake, finality_period, challenge_period_extension
):
    """Test withdraw when a claim was challenged, and the challenger won.
    In that case, the request funds must not be paid out to the challenger."""

    requester, challenger = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    transfer_amount = 23

    claimer_eth_balance = web3.eth.get_balance(claimer.address)
    challenger_eth_balance = web3.eth.get_balance(challenger.address)

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(claimer) == 0
    assert token.balanceOf(challenger) == 0

    assert web3.eth.get_balance(request_manager.address) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)
    claim_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim_tx.return_value

    assert token.balanceOf(request_manager.address) == transfer_amount

    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance

    request_manager.challengeClaim(claim_id, {"from": challenger, "value": claim_stake + 1})

    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance - claim_stake - 1

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id, {"from": claimer})

    # Timetravel after challenge period
    chain.mine(timedelta=finality_period + challenge_period_extension)

    assert web3.eth.get_balance(request_manager.address) == 2 * claim_stake + 1

    # The challenger sent the last bid
    # Even if the requester calls withdraw, the challenge stakes go to the challenger
    # However, the request funds stay in the contract
    withdraw_tx = request_manager.withdraw(claim_id, {"from": challenger})
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert "DepositWithdrawn" not in withdraw_tx.events
    assert not request_manager.isWithdrawn(request_id)

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer) == 0
    assert token.balanceOf(challenger) == 0
    assert token.balanceOf(request_manager.address) == transfer_amount

    assert web3.eth.get_balance(request_manager.address) == 0
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance + claim_stake

    # Another withdraw must fail
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim_id, {"from": claimer})


def test_withdraw_with_two_claims(deployer, request_manager, token, claim_stake, claim_period):
    """Test withdraw when a request was claimed twice"""
    (requester,) = alloc_accounts(1)
    claimer1, claimer2 = alloc_whitelisted_accounts(2, {request_manager})
    transfer_amount = 23

    claimer1_eth_balance = web3.eth.get_balance(claimer1.address)
    claimer2_eth_balance = web3.eth.get_balance(claimer2.address)

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(claimer1) == 0
    assert token.balanceOf(claimer2) == 0

    assert web3.eth.get_balance(request_manager.address) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    claim1_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer1, "value": claim_stake}
    )
    claim1_id = claim1_tx.return_value

    claim2_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer2, "value": claim_stake}
    )
    claim2_id = claim2_tx.return_value

    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim1_id, {"from": claimer1})

    # Timetravel after claim period
    chain.mine(timedelta=claim_period)

    assert web3.eth.get_balance(request_manager.address) == 2 * claim_stake

    # The first claim gets withdrawn first
    withdraw1_tx = request_manager.withdraw(claim1_id, {"from": claimer1})
    assert "DepositWithdrawn" in withdraw1_tx.events
    assert "ClaimStakeWithdrawn" in withdraw1_tx.events

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer1) == transfer_amount
    assert token.balanceOf(claimer2) == 0

    assert web3.eth.get_balance(request_manager.address) == claim_stake
    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake

    # Another withdraw must fail
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim1_id, {"from": claimer1})

    # The other claim must be withdrawable, but the claim stakes go to the
    # contract owner as it is a false claim but no challenger exists.
    with earnings(web3, deployer) as owner_earnings:
        withdraw2_tx = request_manager.withdraw(claimer2, claim2_id, {"from": requester})
    assert "DepositWithdrawn" not in withdraw2_tx.events
    assert "ClaimStakeWithdrawn" in withdraw2_tx.events

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer1) == transfer_amount
    assert token.balanceOf(claimer2) == 0

    # Since there was no challenger, but claim2 was a false claim,
    # stakes go to the contract owner.
    assert owner_earnings() == claim_stake
    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake


@pytest.mark.parametrize("second_fill_id", [FILL_ID, FillId(b"wrong_fill_id")])
def test_withdraw_second_claim_same_claimer_different_fill_ids(
    request_manager, token, claim_stake, claim_period, second_fill_id
):
    """
    Test withdraw with two claims by the same address. First one is successful.
    If the second fill id is also equal to the first, this is an identical claim.
    The claimer should also win.
    If the fill id is different the challenger must win,
    even though the claimer was successful with a different claim and fill id.
    """
    requester, challenger = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    transfer_amount = 23

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(claimer) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    claim1_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim1_id = claim1_tx.return_value

    claim2_tx = request_manager.claimRequest(
        request_id, second_fill_id, {"from": claimer, "value": claim_stake}
    )
    claim2_id = claim2_tx.return_value

    challenger_eth_balance = web3.eth.get_balance(challenger.address)
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance
    request_manager.challengeClaim(claim2_id, {"from": challenger, "value": claim_stake + 1})
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance - claim_stake - 1

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim1_id, {"from": claimer})
    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim2_id, {"from": claimer})

    # Timetravel after claim period
    chain.mine(timedelta=claim_period)

    # Withdraw must fail because it was challenged
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim2_id, {"from": claimer})

    current_claimer_eth_balance = web3.eth.get_balance(claimer.address)

    withdraw_tx = request_manager.withdraw(claim1_id, {"from": claimer})
    assert "DepositWithdrawn" in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    assert web3.eth.get_balance(claimer.address) == current_claimer_eth_balance + claim_stake
    assert token.balanceOf(claimer) == transfer_amount

    claim_winner = claimer if second_fill_id == FILL_ID else challenger
    claim_loser = challenger if claimer == claim_winner else claimer

    claim_winner_balance = web3.eth.get_balance(claim_winner.address)
    claim_loser_balance = web3.eth.get_balance(claim_loser.address)

    # Even though the challenge period of claim2 isn't over, the claim can be resolved now.
    withdraw_tx = request_manager.withdraw(claim2_id, {"from": claim_winner})
    assert "DepositWithdrawn" not in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    assert web3.eth.get_balance(claim_winner.address) == claim_winner_balance + 2 * claim_stake + 1
    assert web3.eth.get_balance(claim_loser.address) == claim_loser_balance


def test_withdraw_with_two_claims_and_challenge(request_manager, token, claim_stake, claim_period):
    """Test withdraw when a request was claimed twice and challenged"""
    requester, challenger = alloc_accounts(2)
    claimer1, claimer2 = alloc_whitelisted_accounts(2, {request_manager})
    transfer_amount = 23

    claimer1_eth_balance = web3.eth.get_balance(claimer1.address)
    claimer2_eth_balance = web3.eth.get_balance(claimer2.address)
    challenger_eth_balance = web3.eth.get_balance(challenger.address)

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(claimer1) == 0
    assert token.balanceOf(claimer2) == 0

    assert web3.eth.get_balance(request_manager.address) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    claim1_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer1, "value": claim_stake}
    )
    claim1_id = claim1_tx.return_value

    claim2_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer2, "value": claim_stake}
    )
    claim2_id = claim2_tx.return_value

    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance

    request_manager.challengeClaim(claim2_id, {"from": challenger, "value": claim_stake + 1})

    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance - claim_stake - 1

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim1_id, {"from": claimer1})

    # Timetravel after claim period
    chain.mine(timedelta=claim_period)

    assert web3.eth.get_balance(request_manager.address) == 3 * claim_stake + 1

    # The first claim gets withdrawn first
    withdraw1_tx = request_manager.withdraw(claim1_id, {"from": claimer1})
    assert "DepositWithdrawn" in withdraw1_tx.events
    assert "ClaimStakeWithdrawn" in withdraw1_tx.events
    assert request_manager.isWithdrawn(request_id)

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer1) == transfer_amount
    assert token.balanceOf(claimer2) == 0

    assert web3.eth.get_balance(request_manager.address) == 2 * claim_stake + 1
    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance - claim_stake - 1

    # Another withdraw must fail
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim1_id, {"from": claimer1})

    # The other claim must be withdrawable, but must not transfer tokens again
    request_manager.withdraw(claim2_id, {"from": challenger})

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer1) == transfer_amount
    assert token.balanceOf(claimer2) == 0

    assert web3.eth.get_balance(request_manager.address) == 0
    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance + claim_stake


def test_withdraw_with_two_claims_first_unsuccessful_then_successful(
    request_manager, token, claim_stake, claim_period, finality_period
):
    """Test withdraw when a request was claimed twice. The first claim fails, while the second
    is successful and should pay out the request funds."""
    requester, challenger = alloc_accounts(2)
    claimer1, claimer2 = alloc_whitelisted_accounts(2, {request_manager})
    transfer_amount = 23

    claimer1_eth_balance = web3.eth.get_balance(claimer1.address)
    claimer2_eth_balance = web3.eth.get_balance(claimer2.address)
    challenger_eth_balance = web3.eth.get_balance(challenger.address)

    token.mint(requester, transfer_amount, {"from": requester})
    assert token.balanceOf(requester) == transfer_amount
    assert token.balanceOf(claimer1) == 0
    assert token.balanceOf(claimer2) == 0
    assert token.balanceOf(request_manager.address) == 0

    assert web3.eth.get_balance(request_manager.address) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    claim1_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer1, "value": claim_stake}
    )
    claim1_id = claim1_tx.return_value

    claim2_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer2, "value": claim_stake}
    )
    claim2_id = claim2_tx.return_value

    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance

    request_manager.challengeClaim(claim1_id, {"from": challenger, "value": claim_stake + 1})

    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance - claim_stake - 1

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim1_id, {"from": claimer1})

    # Timetravel after claim period
    chain.mine(timedelta=claim_period + finality_period)

    assert token.balanceOf(request_manager.address) == transfer_amount
    assert web3.eth.get_balance(request_manager.address) == 3 * claim_stake + 1

    # The first claim gets withdrawn first
    # As the challenger wins, no requests funds must be paid out
    withdraw1_tx = request_manager.withdraw(claim1_id, {"from": challenger})
    assert "DepositWithdrawn" not in withdraw1_tx.events

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer1) == 0
    assert token.balanceOf(claimer2) == 0
    assert token.balanceOf(request_manager.address) == transfer_amount

    assert web3.eth.get_balance(request_manager.address) == claim_stake
    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance - claim_stake
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance + claim_stake

    # Another withdraw must fail
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim1_id, {"from": claimer1})

    # The other claim must be withdrawable and should pay out the funds
    withdraw2_tx = request_manager.withdraw(claim2_id, {"from": claimer2})
    assert "ClaimStakeWithdrawn" in withdraw2_tx.events
    assert "DepositWithdrawn" in withdraw2_tx.events

    assert token.balanceOf(requester) == 0
    assert token.balanceOf(claimer1) == 0
    assert token.balanceOf(claimer2) == transfer_amount
    assert token.balanceOf(request_manager.address) == 0

    assert web3.eth.get_balance(request_manager.address) == 0
    assert web3.eth.get_balance(claimer1.address) == claimer1_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == claimer2_eth_balance
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance + claim_stake


def test_claim_after_withdraw(request_manager, token, claim_stake, claim_period):
    """Test that the same account can not claim a already withdrawn fill again"""
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 23)

    claim_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim_tx.return_value

    # Timetravel after claim period
    chain.mine(timedelta=claim_period)
    withdraw_tx = request_manager.withdraw(claim_id, {"from": claimer})
    assert "DepositWithdrawn" in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    # Claiming the same request again must fail
    with brownie.reverts("Deposit already withdrawn"):
        request_manager.claimRequest(request_id, FILL_ID, {"from": claimer, "value": claim_stake})


def test_second_claim_after_withdraw(deployer, request_manager, token, claim_stake, claim_period):
    """Test that one can withdraw a claim immediately after the request
    deposit has been withdrawn via another claim."""
    (requester,) = alloc_accounts(1)
    claimer1, claimer2 = alloc_whitelisted_accounts(2, {request_manager})
    request_id = make_request(request_manager, token, requester, requester, 23)

    claimer1_eth_balance = web3.eth.get_balance(claimer1.address)
    claimer2_eth_balance = web3.eth.get_balance(claimer2.address)

    claim1_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer1, "value": claim_stake}
    )
    claim1_id = claim1_tx.return_value

    # Timetravel after claim period / 2.
    chain.mine(timedelta=claim_period / 2)
    claim2_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer2, "value": claim_stake}
    )
    claim2_id = claim2_tx.return_value

    # Another claim from the future depositReceiver
    claim3_tx = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer1, "value": claim_stake}
    )
    claim3_id = claim3_tx.return_value

    # Timetravel after claim period / 2. At this point claim 1 can be
    # withdrawn (its claim period is over), but not claim 2 (its claim period
    # is not over yet).
    chain.mine(timedelta=claim_period / 2)
    withdraw_tx = request_manager.withdraw(claim1_id, {"from": claimer1})
    assert "DepositWithdrawn" in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert claimer1_eth_balance - claim_stake == web3.eth.get_balance(claimer1.address)

    # Withdrawing the second claim must now succeed immediately because the
    # deposit has been withdrawn and we do not need to wait for the claim
    # period. The stakes go to the contract owner.
    with earnings(web3, deployer) as owner_earnings:
        withdraw_tx = request_manager.withdraw(claim2_id, {"from": claimer2})
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert claimer2_eth_balance - claim_stake == web3.eth.get_balance(claimer2.address)
    assert owner_earnings() == claim_stake

    # Withdrawing the third claim must also succeed immediately.
    # Since the claimer is also the depositReceiver stakes go back to the claimer
    withdraw_tx = request_manager.withdraw(claim3_id, {"from": claimer1})
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert claimer1_eth_balance == web3.eth.get_balance(claimer1.address)


@pytest.mark.parametrize("invalidate", [True, False])
@pytest.mark.parametrize("l1_filler", [make_address(), None])
def test_withdraw_without_challenge_with_resolution(
    request_manager, token, claim_stake, contracts, invalidate, l1_filler
):
    """
    Test withdraw when a claim was not challenged, but L1 resolved
    It tests the combination of L1 resolution

    fill (invalid, valid)
            X
    l1 filler (honest claimer, dishonest claimer)

    In the invalid - dishonest claimer case, stakes go to the contract
    owner as there is no challenger
    In the invalid - honest claimer case, honest claimer reverts the
    invalidation in request.invalidFillIds
    """
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    transfer_amount = 23

    if l1_filler is None:
        l1_filler = claimer.address

    token.mint(requester, transfer_amount, {"from": requester})

    # Initial balances
    claimer_eth_balance = web3.eth.get_balance(claimer.address)
    owner_eth_balance = web3.eth.get_balance(request_manager.owner())
    request_manager_balance = web3.eth.get_balance(request_manager.address)

    requester_token_balance = token.balanceOf(requester)
    claimer_token_balance = token.balanceOf(claimer)

    # If no claims exist or are fully withdrawn
    # there should be no ETH on the request manager contract
    assert web3.eth.get_balance(request_manager.address) == 0

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    # Claim
    fill_id = to_bytes(b"123")
    claim_tx = request_manager.claimRequest(
        request_id, fill_id, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim_tx.return_value

    assert web3.eth.get_balance(request_manager.address) == request_manager_balance + claim_stake
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance - claim_stake

    # Start L1 resolution
    contracts.l1_messenger.setLastSender(contracts.resolver.address)

    if invalidate:
        request_manager.invalidateFill(
            request_id, fill_id, chain.id, {"from": contracts.l1_messenger}
        )
    # Assert that invalidation works
    assert request_manager.isInvalidFill(request_id, fill_id) == invalidate

    # Register a L1 resolution
    request_manager.resolveRequest(
        request_id, fill_id, web3.eth.chain_id, l1_filler, {"from": contracts.l1_messenger}
    )

    # Assert that correct filler is resolved, it reverts the false invalidation
    if invalidate and l1_filler == claimer:
        assert not request_manager.isInvalidFill(request_id, fill_id)

    # The claim period is not over, but the resolution must allow withdrawal now
    withdraw_tx = request_manager.withdraw(claim_id, {"from": claimer})

    if claimer == l1_filler:
        assert "DepositWithdrawn" in withdraw_tx.events
        assert token.balanceOf(requester) == requester_token_balance - transfer_amount
        assert token.balanceOf(claimer) == claimer_token_balance + transfer_amount

    else:
        claimer_eth_balance -= claim_stake
        owner_eth_balance += claim_stake

    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    assert web3.eth.get_balance(request_manager.owner()) == owner_eth_balance
    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance
    assert web3.eth.get_balance(request_manager.address) == request_manager_balance

    # Another withdraw must fail
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim_id, {"from": claimer})


def test_withdraw_l1_resolved_muliple_claims(contracts, request_manager, token, claim_stake):
    (requester,) = alloc_accounts(1)
    claimer1, claimer2 = alloc_whitelisted_accounts(2, {request_manager})
    transfer_amount = 23
    token.mint(requester, transfer_amount, {"from": requester})

    # Initial balances
    first_claimer_eth_balance = web3.eth.get_balance(claimer1.address)
    second_claimer_eth_balance = web3.eth.get_balance(claimer2.address)
    owner_eth_balance = web3.eth.get_balance(request_manager.owner())

    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    # Creating 4 Claims
    fill_id = FILL_ID

    # Claim 1: valid claim
    claim_tx_1 = request_manager.claimRequest(
        request_id, fill_id, {"from": claimer1, "value": claim_stake}
    )
    claim_id_1 = claim_tx_1.return_value

    # Claim 2: claimer is not the filler, invalid claim
    claim_tx_2 = request_manager.claimRequest(
        request_id, fill_id, {"from": claimer2, "value": claim_stake}
    )
    claim_id_2 = claim_tx_2.return_value

    # Claim 3: another valid claim
    claim_tx_3 = request_manager.claimRequest(
        request_id, fill_id, {"from": claimer1, "value": claim_stake}
    )
    claim_id_3 = claim_tx_3.return_value

    # Claim 4: claimer is the filler but fill id is wrong, invalid claim
    claim_tx_4 = request_manager.claimRequest(
        request_id, b"wrong fill id", {"from": claimer1, "value": claim_stake}
    )
    claim_id_4 = claim_tx_4.return_value

    contracts.l1_messenger.setLastSender(contracts.resolver.address)

    # Before L1 resolution, all claims are still running and cannot be withdrawn
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id_1, {"from": claimer1})
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id_2, {"from": claimer2})
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id_3, {"from": claimer1})
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id_4, {"from": claimer1})

    # Start L1 resolution
    # Register a L1 resolution
    request_manager.resolveRequest(
        request_id, fill_id, web3.eth.chain_id, claimer1, {"from": contracts.l1_messenger}
    )

    # The claim period is not over, but the resolution must allow withdrawal now
    # Valid claim will result in payout
    withdraw_tx = request_manager.withdraw(claim_id_1, {"from": claimer1})
    assert "DepositWithdrawn" in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    # Wrong claimer, since it is not challenged stakes go to the contract owner
    withdraw_tx = request_manager.withdraw(claim_id_2, {"from": claimer2})
    assert "DepositWithdrawn" not in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    # Another valid claim, deposit is already withdrawn but stakes go back to claimer
    withdraw_tx = request_manager.withdraw(claim_id_3, {"from": claimer1})
    assert "DepositWithdrawn" not in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    # Wrong fill id, since it is not challenged stakes go to the contract owner
    withdraw_tx = request_manager.withdraw(claim_id_4, {"from": claimer1})
    assert "DepositWithdrawn" not in withdraw_tx.events
    assert "ClaimStakeWithdrawn" in withdraw_tx.events

    assert web3.eth.get_balance(claimer1.address) == first_claimer_eth_balance - claim_stake
    assert web3.eth.get_balance(claimer2.address) == second_claimer_eth_balance - claim_stake
    # Two of the claims were invalid, thus stakes went to the contract owner
    assert web3.eth.get_balance(request_manager.owner()) == owner_eth_balance + 2 * claim_stake


def test_challenge_after_l1_resolution(request_manager, token, claim_stake, contracts):
    (requester,) = alloc_accounts(1)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})
    transfer_amount = 23

    token.mint(requester, transfer_amount, {"from": requester})
    request_id = make_request(request_manager, token, requester, requester, transfer_amount)

    # Claim
    fill_id = to_bytes(b"123")
    claim_tx = request_manager.claimRequest(
        request_id, fill_id, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim_tx.return_value

    request_manager.invalidateFill(request_id, fill_id, chain.id, {"from": contracts.l1_messenger})
    # Assert that invalidation works
    assert request_manager.isInvalidFill(request_id, fill_id)

    with brownie.reverts("Fill already invalidated"):
        request_manager.challengeClaim(claim_id)

    request_manager.resolveRequest(
        request_id, fill_id, brownie.web3.chain_id, claimer, {"from": contracts.l1_messenger}
    )

    with brownie.reverts("Request already resolved"):
        request_manager.challengeClaim(claim_id)


def test_withdraw_on_behalf(
    request_manager, token, claim_stake, finality_period, challenge_period_extension
):
    first_challenger, second_challenger, requester, other = alloc_accounts(4)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})

    request_id = make_request(request_manager, token, requester, requester, 1)
    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim.return_value
    first_challenger_eth_balance = web3.eth.get_balance(first_challenger.address)
    second_challenger_eth_balance = web3.eth.get_balance(second_challenger.address)

    # First challenger challenges
    request_manager.challengeClaim(claim_id, {"from": first_challenger, "value": claim_stake + 1})
    # Claimer outbids again
    request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})
    # Second challenger challenges
    request_manager.challengeClaim(claim_id, {"from": second_challenger, "value": claim_stake + 1})

    # Timetravel after claim period
    chain.mine(timedelta=finality_period + challenge_period_extension)

    request_manager.withdraw(second_challenger, claim_id, {"from": first_challenger})

    # second challenger should have won claim stake which is the excess amount the
    # claimer put in
    assert (
        web3.eth.get_balance(second_challenger.address)
        == second_challenger_eth_balance + claim_stake
    )

    assert (
        web3.eth.get_balance(first_challenger.address)
        == first_challenger_eth_balance - claim_stake - 1
    )

    # After the stakes are withdrawn for the second challenger
    # he is not an active participant anymore
    with brownie.reverts("Not an active participant in this claim"):
        request_manager.withdraw(claim_id, {"from": second_challenger})

    request_manager.withdraw(first_challenger, claim_id, {"from": other})

    assert (
        web3.eth.get_balance(first_challenger.address)
        == first_challenger_eth_balance + claim_stake + 1
    )


def test_withdraw_on_behalf_of_challenger_claimer_wins(
    request_manager, token, claim_stake, finality_period, challenge_period_extension
):
    challenger, requester = alloc_accounts(2)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})

    claimer_eth_balance = web3.eth.get_balance(claimer.address)
    challenger_eth_balance = web3.eth.get_balance(challenger.address)

    request_id = make_request(request_manager, token, requester, requester, 1)
    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim.return_value

    # First challenger challenges
    request_manager.challengeClaim(claim_id, {"from": challenger, "value": claim_stake + 1})
    # Claimer outbids again
    request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 1})

    # Timetravel after claim period
    chain.mine(timedelta=finality_period + challenge_period_extension)

    withdraw_tx = request_manager.withdraw(challenger, claim_id, {"from": challenger})
    assert "ClaimStakeWithdrawn" in withdraw_tx.events
    assert "DepositWithdrawn" in withdraw_tx.events

    assert web3.eth.get_balance(claimer.address) == claimer_eth_balance + claim_stake + 1
    assert web3.eth.get_balance(challenger.address) == challenger_eth_balance - claim_stake - 1


def test_withdraw_two_challengers(
    request_manager, token, claim_stake, finality_period, challenge_period_extension
):
    first_challenger, second_challenger, requester = alloc_accounts(3)
    (claimer,) = alloc_whitelisted_accounts(1, {request_manager})

    request_id = make_request(request_manager, token, requester, requester, 1)
    claim = request_manager.claimRequest(
        request_id, FILL_ID, {"from": claimer, "value": claim_stake}
    )
    claim_id = claim.return_value
    first_challenger_eth_balance = web3.eth.get_balance(first_challenger.address)
    second_challenger_eth_balance = web3.eth.get_balance(second_challenger.address)

    # First challenger challenges
    request_manager.challengeClaim(claim_id, {"from": first_challenger, "value": claim_stake + 1})
    # Claimer outbids again
    request_manager.challengeClaim(claim_id, {"from": claimer, "value": claim_stake + 10})
    # Second challenger challenges
    request_manager.challengeClaim(
        claim_id, {"from": second_challenger, "value": claim_stake + 11}
    )

    first_challenger_reward = claim_stake + 1
    second_challenger_reward = claim_stake + 9

    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id, {"from": first_challenger})
    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id, {"from": second_challenger})
    # Withdraw must fail when claim period is not over
    with brownie.reverts("Claim period not finished"):
        request_manager.withdraw(claim_id, {"from": claimer})

    # Timetravel after claim period
    chain.mine(timedelta=finality_period + challenge_period_extension)

    # Take snapshot
    chain.snapshot()

    def _withdraw_by_order(first_withdrawer, second_withdrawer):
        request_manager.withdraw(claim_id, {"from": first_withdrawer})

        # Challenger cannot withdraw twice
        with brownie.reverts("Not an active participant in this claim"):
            request_manager.withdraw(claim_id, {"from": first_withdrawer})
        with brownie.reverts("Challenger has nothing to withdraw"):
            request_manager.withdraw(claim_id, {"from": claimer})

        request_manager.withdraw(claim_id, {"from": second_withdrawer})

        assert (
            web3.eth.get_balance(first_challenger.address)
            == first_challenger_eth_balance + first_challenger_reward
        )
        assert (
            web3.eth.get_balance(second_challenger.address)
            == second_challenger_eth_balance + second_challenger_reward
        )

    _withdraw_by_order(first_challenger, second_challenger)
    # revert to snapshot
    chain.revert()
    _withdraw_by_order(second_challenger, first_challenger)

    # All stakes are withdrawn already
    with brownie.reverts("Claim already withdrawn"):
        request_manager.withdraw(claim_id, {"from": claimer})


def test_withdraw_expired(token, request_manager):
    """Test that a request can be withdrawn once it is expired"""
    validity_period = request_manager.MIN_VALIDITY_PERIOD()
    (requester,) = alloc_accounts(1)

    amount = 17
    token.mint(requester, amount)

    request_id = make_request(
        request_manager, token, requester, requester, amount, validity_period=validity_period
    )

    assert token.balanceOf(requester) == 0

    chain.mine(timedelta=validity_period)
    tx = request_manager.withdrawExpiredRequest(request_id, {"from": requester})
    assert "DepositWithdrawn" in tx.events
    assert request_manager.isWithdrawn(request_id)
    assert token.balanceOf(requester) == amount


def test_withdraw_before_expiration(token, request_manager):
    """Test that a request cannot be withdrawn before it is expired"""
    validity_period = request_manager.MIN_VALIDITY_PERIOD()
    (requester,) = alloc_accounts(1)

    request_id = make_request(
        request_manager, token, requester, requester, 1, validity_period=validity_period
    )

    chain.mine(timedelta=validity_period / 2)
    with brownie.reverts("Request not expired yet"):
        request_manager.withdrawExpiredRequest(request_id, {"from": requester})


def test_withdrawal_state_of_new_request(token, request_manager):
    """Test that a new request is not withdrawn"""
    (requester,) = alloc_accounts(1)

    request_id = make_request(request_manager, token, requester, requester, 1)

    assert not request_manager.isWithdrawn(request_id)


def test_contract_pause(deployer, request_manager, token):
    """Test that a contract can be paused"""
    (requester,) = alloc_accounts(1)
    amount = 17
    token.mint(requester, 2 * amount)

    make_request(
        request_manager,
        token,
        requester,
        requester,
        amount,
    )
    with brownie.reverts("Ownable: caller is not the owner"):
        request_manager.pause({"from": requester.address})

    assert not request_manager.paused()
    request_manager.pause({"from": deployer.address})
    assert request_manager.paused()

    with brownie.reverts("Pausable: paused"):
        request_manager.pause({"from": deployer.address})

    with brownie.reverts("Pausable: paused"):
        make_request(
            request_manager,
            token,
            requester,
            requester,
            amount,
        )


def test_contract_unpause(deployer, request_manager, token):
    """Test that a contract can be unpaused"""
    (requester,) = alloc_accounts(1)
    amount = 17
    token.mint(requester, 2 * amount)

    with brownie.reverts("Ownable: caller is not the owner"):
        request_manager.unpause({"from": requester.address})

    with brownie.reverts("Pausable: not paused"):
        request_manager.unpause({"from": deployer.address})

    request_manager.pause({"from": deployer.address})
    assert request_manager.paused()
    request_manager.unpause({"from": deployer.address})
    assert not request_manager.paused()

    make_request(
        request_manager,
        token,
        requester,
        requester,
        amount,
    )


def test_transfer_limit_update_only_owner(deployer, request_manager, token):
    (random_guy,) = alloc_accounts(1)
    original_transfer_limit = request_manager.tokens(token.address)[RM_T_FIELD_TRANSFER_LIMIT]
    new_transfer_limit = original_transfer_limit + 1

    with brownie.reverts("Ownable: caller is not the owner"):
        update_token(
            request_manager,
            token,
            dict(transfer_limit=new_transfer_limit),
            {"from": random_guy.address},
        )

    assert (
        request_manager.tokens(token.address)[RM_T_FIELD_TRANSFER_LIMIT] == original_transfer_limit
    )
    update_token(
        request_manager,
        token,
        dict(transfer_limit=new_transfer_limit),
        {"from": deployer.address},
    )
    assert request_manager.tokens(token.address)[RM_T_FIELD_TRANSFER_LIMIT] == new_transfer_limit
    # Also show that transfer limit can be decreased again
    update_token(
        request_manager,
        token,
        dict(transfer_limit=original_transfer_limit),
        {"from": deployer.address},
    )
    assert (
        request_manager.tokens(token.address)[RM_T_FIELD_TRANSFER_LIMIT] == original_transfer_limit
    )


def test_transfer_limit_requests(deployer, request_manager, token):
    (requester,) = alloc_accounts(1)
    transfer_limit = request_manager.tokens(token.address)[RM_T_FIELD_TRANSFER_LIMIT]

    assert token.balanceOf(requester) == 0
    token.mint(requester, transfer_limit)

    make_request(request_manager, token, requester, requester, transfer_limit)

    assert token.balanceOf(requester) == 0
    token.mint(requester, transfer_limit + 1)

    with brownie.reverts("Amount exceeds transfer limit"):
        make_request(request_manager, token, requester, requester, transfer_limit + 1)

    update_token(
        request_manager,
        token,
        dict(transfer_limit=transfer_limit + 1),
        {"from": deployer.address},
    )
    make_request(request_manager, token, requester, requester, transfer_limit + 1)

    assert token.balanceOf(requester) == 0
