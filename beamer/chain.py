import json
import os
import pathlib
import sys
import threading
import time
import traceback
from typing import Any, Callable

import structlog
import web3
from eth_utils import is_checksum_address
from hexbytes import HexBytes
from statemachine.exceptions import TransitionNotAllowed
from web3.contract import Contract
from web3.types import Wei

import beamer.events
from beamer.events import ClaimMade, Event, EventFetcher
from beamer.request import Request, RequestData, RequestTracker
from beamer.typing import BlockNumber, ChainId
from beamer.util import TokenMatchChecker


def _load_ERC20_abi() -> list[Any]:
    path = pathlib.Path(__file__)
    path = path.parent.joinpath("data/abi/StandardToken.json")
    with path.open("rt") as fp:
        return json.load(fp)["abi"]


_ERC20_ABI = _load_ERC20_abi()

# The time we're waiting for our thread in stop(), in seconds.
# This is also the maximum time a call to stop() would block.
_STOP_TIMEOUT = 2


def _wrap_thread_func(func: Callable) -> Callable:
    def wrapper(*args, **kwargs):  # type: ignore
        try:
            return func(*args, **kwargs)
        except Exception:
            traceback.print_exception(*sys.exc_info())
            os._exit(1)
            # should never be reached
            return None

    return wrapper


class ContractEventMonitor:
    def __init__(
        self,
        name: str,
        contract: web3.contract.Contract,
        deployment_block: BlockNumber,
        on_new_events: Callable[[list[Event]], None],
        on_sync_done: Callable[[], None],
    ):
        self._name = name
        self._contract = contract
        self._deployment_block = deployment_block
        self._stop = False
        self._on_new_events = on_new_events
        self._on_sync_done = on_sync_done
        self._log = structlog.get_logger(type(self).__name__).bind(contract=name)

    def start(self) -> None:
        self._thread = threading.Thread(
            name=self._name, target=_wrap_thread_func(self._thread_func)
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._thread.join(_STOP_TIMEOUT)

    def _thread_func(self) -> None:
        chain_id = ChainId(self._contract.web3.eth.chain_id)
        self._log.info(
            "ContractEventMonitor started", chain_id=chain_id, address=self._contract.address
        )
        fetcher = EventFetcher(self._name, self._contract, self._deployment_block)
        events = fetcher.fetch()
        if events:
            self._on_new_events(events)
        self._on_sync_done()
        self._log.info("Sync done", chain_id=chain_id)
        while not self._stop:
            events = fetcher.fetch()
            if events:
                self._on_new_events(events)
            # TODO: wait for new block instead of the sleep here
            time.sleep(1)
        self._log.info("ContractEventMonitor stopped", chain_id=chain_id)


class EventProcessor:
    def __init__(
        self,
        tracker: RequestTracker,
        request_manager: Contract,
        fill_manager: Contract,
        match_checker: TokenMatchChecker,
        fill_wait_time: int,
    ):
        # This lock protects the following objects:
        #   - self._events
        #   - self._num_syncs_done
        self._lock = threading.Lock()
        self._have_new_events = threading.Event()
        self._events: list[Event] = []
        self._tracker = tracker
        self._request_manager = request_manager
        self._fill_manager = fill_manager
        self._match_checker = match_checker
        self._stop = False
        self._log = structlog.get_logger(type(self).__name__)
        assert is_checksum_address(request_manager.web3.eth.default_account)
        self._address = request_manager.web3.eth.default_account
        # The number of times we synced with a chain:
        # 0 = we're still waiting for sync to complete for both chains
        # 1 = one of the chains was synced, waiting for the other one
        # 2 = both chains synced
        self._num_syncs_done = 0

        self._fill_wait_time = fill_wait_time

        if not self._fill_manager.functions.allowedLPs(self._address).call():
            raise RuntimeError("Agent address is not whitelisted")

    def mark_sync_done(self) -> None:
        with self._lock:
            assert self._num_syncs_done < 2
            self._num_syncs_done += 1

    def start(self) -> None:
        self._thread = threading.Thread(
            name="EventProcessor", target=_wrap_thread_func(self._thread_func)
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._thread.join(_STOP_TIMEOUT)

    def add_events(self, events: list[Event]) -> None:
        with self._lock:
            self._events.extend(events)
            self._log.debug("New events", events=events)
        self._have_new_events.set()

    def _thread_func(self) -> None:
        self._log.info("EventProcessor started")
        while not self._stop:
            if self._have_new_events.wait(1):
                self._have_new_events.clear()
                self._process_events()
            self._process_requests()
        self._log.info("EventProcessor stopped")

    def _process_events(self) -> None:
        iteration = 0
        while True:
            with self._lock:
                events = self._events[:]

            unprocessed = []
            any_state_changed = False
            for event in events:
                state_changed = self._process_event(event)
                any_state_changed |= state_changed
                if not state_changed:
                    unprocessed.append(event)

            # Return the unprocessed events to the event list.
            # Note that the event list might have been changed in the meantime
            # by one of the event monitors. Placing unprocessed events at the
            # back of the list, as opposed to the front, may avoid an extra
            # iteration over all events.
            with self._lock:
                del self._events[: len(events)]
                self._events.extend(unprocessed)

            self._log.debug(
                "Finished iteration",
                iteration=iteration,
                any_state_changed=any_state_changed,
                num_events=len(self._events),
            )
            iteration += 1
            if not any_state_changed:
                break

    def _process_event(self, event: Event) -> bool:
        if isinstance(event, beamer.events.RequestCreated):
            is_valid_request = self._match_checker.is_valid_pair(
                event.chain_id,
                event.source_token_address,
                event.target_chain_id,
                event.target_token_address,
            )
            if not is_valid_request:
                self._log.debug("Invalid token pair in request", _event=event)
                return False

            data = self._request_manager.functions.requests(event.request_id).call()
            request_data = RequestData.from_chain_data(data)

            req = Request(
                request_id=event.request_id,
                source_chain_id=event.chain_id,
                target_chain_id=request_data.targetChainId,
                source_token_address=request_data.sourceTokenAddress,
                target_token_address=request_data.targetTokenAddress,
                target_address=request_data.targetAddress,
                amount=request_data.amount,
                valid_until=request_data.validUntil,
            )
            self._tracker.add(req)
            return True

        elif isinstance(event, beamer.events.RequestFilled):
            request = self._tracker.get(event.request_id)
            if request is None:
                return False

            try:
                request.fill(filler=event.filler, fill_id=event.fill_id)
            except TransitionNotAllowed:
                return False
            self._log.info("Request filled", request=request)
            return True

        elif isinstance(event, beamer.events.ClaimMade):
            request = self._tracker.get(event.request_id)
            if request is None:
                return False

            try:
                request.claim(event=event, fill_wait_time=self._fill_wait_time)
            except TransitionNotAllowed:
                return False
            self._log.info("Request claimed", request=request, claim_id=event.claim_id)
            return True

        elif isinstance(event, beamer.events.ClaimWithdrawn):
            request = self._tracker.get(event.request_id)
            if request is None:
                return False

            try:
                request.withdraw()
            except TransitionNotAllowed:
                return False
            self._log.info("Claim withdrawn", request=request)
            return True
        else:
            raise RuntimeError("Unrecognized event type")

    def _process_requests(self) -> None:
        with self._lock:
            if self._num_syncs_done < 2:
                # We need to wait until we are synced with both chains.
                return

        to_remove = []
        for request in self._tracker:
            self._log.debug("Processing request", request=request)
            if request.is_pending:
                self._fill_request(request)
            elif request.is_filled and request.filler == self._address:
                self._claim_request(request)
            elif request.is_claimed:
                self._check_claims(request)
            elif request.is_unfillable:
                self._log.debug("Removing unfillable request", request=request)
                to_remove.append(request.id)
            elif request.is_withdrawn:
                self._log.debug("Removing withdrawn request", request=request)
                to_remove.append(request.id)

        for request_id in to_remove:
            self._tracker.remove(request_id)

    def _check_claims(self, request: Request) -> None:
        for claim in request.iter_claims():
            self._maybe_challenge(
                request, claim, request.get_challenge_back_off_timestamp(claim.claim_id)
            )

            if claim.claimer == self._address:
                self._try_withdraw(claim)

    def _compute_challenge_stake(self, claim: ClaimMade) -> Wei:  # pylint:disable=no-self-use
        stake_increase = 1
        if claim.challenger_stake == 0:
            # we challenge with enough stake for L1 resolution
            stake_increase = 10 ** 15
        return Wei(max(claim.claimer_stake, claim.challenger_stake) + stake_increase)

    def _maybe_challenge(self, request: Request, claim: ClaimMade, back_off_until: int) -> bool:
        # We need to challenge if either of the following is true:
        #
        # 1) the claim is dishonest AND nobody challenged it yet
        #
        # 2) we participate in the game AND it is our turn

        if int(time.time()) < back_off_until:
            return False

        unchallenged = claim.challenger_stake == 0
        own_claim = claim.claimer == self._address
        dishonest_claim = claim.claimer != request.filler or claim.fill_id != request.fill_id

        our_turn = (
            claim.challenger == self._address and claim.claimer_stake > claim.challenger_stake
        ) or (own_claim and claim.claimer_stake < claim.challenger_stake)

        should_challenge = dishonest_claim and unchallenged and not own_claim or our_turn
        if not should_challenge:
            return False

        stake = self._compute_challenge_stake(claim)

        try:
            txn_hash = self._request_manager.functions.challengeClaim(claim.claim_id).transact(
                dict(value=stake)
            )
        except web3.exceptions.ContractLogicError as exc:
            self._log.error("challengeClaim failed", claim=claim, exc_args=exc.args, stake=stake)
            return False

        w3 = self._request_manager.web3
        w3.eth.wait_for_transaction_receipt(txn_hash)

        self._log.debug(
            "Challenged claim",
            claim=claim,
            txn_hash=txn_hash.hex(),
        )

        return True

    def _fill_request(self, request: Request) -> None:
        w3 = self._fill_manager.web3

        # Check if the address points to a valid token
        if w3.eth.get_code(request.target_token_address) == HexBytes("0x"):
            self._log.info(
                "Request unfillable, invalid token contract",
                request=request,
                token_address=request.target_token_address,
            )
            request.ignore()
            return

        block = self._request_manager.web3.eth.get_block("latest")
        if block["timestamp"] >= request.valid_until:
            self._log.info("Request expired, ignoring", request=request)
            request.ignore()
            return

        token = w3.eth.contract(abi=_ERC20_ABI, address=request.target_token_address)
        address = w3.eth.default_account
        balance = token.functions.balanceOf(address).call()
        if balance < request.amount:
            self._log.debug(
                "Unable to fill request", balance=balance, request_amount=request.amount
            )
            return

        try:
            token.functions.approve(self._fill_manager.address, request.amount).transact()
        except web3.exceptions.ContractLogicError as exc:
            self._log.error("approve failed", request_id=request.id, exc_args=exc.args)
            return

        try:
            txn_hash = self._fill_manager.functions.fillRequest(
                requestId=request.id,
                sourceChainId=request.source_chain_id,
                targetTokenAddress=request.target_token_address,
                targetReceiverAddress=request.target_address,
                amount=request.amount,
            ).transact()
        except web3.exceptions.ContractLogicError as exc:
            self._log.error("fillRequest failed", request_id=request.id, exc_args=exc.args)
            return

        w3.eth.wait_for_transaction_receipt(txn_hash)

        request.fill_unconfirmed()
        self._log.debug(
            "Filled request",
            request=request,
            txn_hash=txn_hash.hex(),
            token=token.functions.symbol().call(),
        )

    def _claim_request(self, request: Request) -> None:
        w3 = self._request_manager.web3
        stake = self._request_manager.functions.claimStake().call()

        try:
            txn_hash = self._request_manager.functions.claimRequest(
                request.id, request.fill_id
            ).transact(dict(value=stake))
        except web3.exceptions.ContractLogicError as exc:
            self._log.error(
                "claimRequest failed",
                request_id=request.id,
                fill_id=request.fill_id,
                exc_args=exc.args,
                stake=stake,
            )
            return

        w3.eth.wait_for_transaction_receipt(txn_hash)

        request.claim_unconfirmed()
        self._log.debug(
            "Claimed request",
            request=request,
            txn_hash=txn_hash.hex(),
        )

    def _try_withdraw(self, claim: ClaimMade) -> None:
        w3 = self._request_manager.web3
        # check whether the claim period expired
        # TODO: avoid making these calls every time
        block = w3.eth.get_block(w3.eth.block_number)
        if block["timestamp"] < claim.termination:
            return

        try:
            txn_hash = self._request_manager.functions.withdraw(claim.claim_id).transact()
        except web3.exceptions.ContractLogicError as exc:
            self._log.error("withdraw failed", claim, exc_args=exc.args)
            return

        w3.eth.wait_for_transaction_receipt(txn_hash)
        self._log.debug("Withdrew", claim=claim, txn_hash=txn_hash.hex())