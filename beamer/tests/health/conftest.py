import pytest
from brownie import ZERO_ADDRESS
from hexbytes import HexBytes
from web3.types import Wei

from beamer.agent.events import ClaimMade, RequestCreated, RequestFilled
from beamer.agent.typing import ChainId, FillId, Nonce, Termination, TokenAmount
from beamer.health.check import Context
from beamer.health.notify import NotificationState
from beamer.tests.agent.unit.utils import (
    ADDRESS1,
    BLOCK_NUMBER,
    CLAIM_ID,
    CLAIMER_STAKE,
    REQUEST_ID,
)
from beamer.tests.agent.utils import make_address

TARGET_CHAIN_ID = ChainId(42161)
SOURCE_CHAIN_ID = ChainId(10)
TARGET_TOKEN_ADDRESS = make_address()
SOURCE_TOKEN_ADDRESS = make_address()

config = {
    "agent_address": "",
    "deployment-dir": "../deployments/mainnet",
    "notification_system": "rocketchat",
    "rpcs": {
        10: "https://example.rpc.com",
        42161: "https://example.rpc.com",
    },
    "explorers": {
        10: "https://optimistic.etherscan.io/tx/",
        42161: "https://arbiscan.io/tx/",
    },
    "notification": {
        "rocketchat": {
            "url": "https://example.com",
            "channel": "beamer",
        },
        "telegram": {
            "token": "",
            "chat_id": "",
        },
    },
}


@pytest.fixture(scope="session")
def agent_address():
    return make_address()


@pytest.fixture(scope="session")
def evil_agent_address():
    return make_address()


@pytest.fixture(autouse=True)
def get_config(monkeypatch):
    """Set default config for script."""
    monkeypatch.setattr("beamer.health.check.get_config", lambda: config)


@pytest.fixture
def ctx(tmp_path, agent_address):
    ctx = Context(agent_address=agent_address)
    ctx.notification_state = NotificationState(tmp_path)

    return ctx


@pytest.fixture(scope="session")
def transfer_request():
    return RequestCreated(
        request_id=REQUEST_ID,
        target_chain_id=TARGET_CHAIN_ID,
        source_token_address=SOURCE_TOKEN_ADDRESS,
        target_token_address=TARGET_TOKEN_ADDRESS,
        source_address=ADDRESS1,
        target_address=ADDRESS1,
        amount=TokenAmount(123),
        nonce=Nonce(123),
        valid_until=Termination(123),
        block_number=BLOCK_NUMBER,
        tx_hash=HexBytes(b"1"),
        chain_id=SOURCE_CHAIN_ID,
    )


@pytest.fixture(scope="session")
def transfer_fill(agent_address):
    return RequestFilled(
        request_id=REQUEST_ID,
        chain_id=TARGET_CHAIN_ID,
        fill_id=FillId(b"1"),
        source_chain_id=SOURCE_CHAIN_ID,
        target_token_address=TARGET_TOKEN_ADDRESS,
        filler=agent_address,
        amount=TokenAmount(100),
        block_number=BLOCK_NUMBER,
        tx_hash=HexBytes(b"1"),
    )


@pytest.fixture(scope="session")
def transfer_claim(agent_address):
    return ClaimMade(
        request_id=REQUEST_ID,
        claim_id=CLAIM_ID,
        fill_id=FillId(b"1"),
        claimer=agent_address,
        claimer_stake=CLAIMER_STAKE,
        chain_id=TARGET_CHAIN_ID,
        last_challenger=ZERO_ADDRESS,
        challenger_stake_total=Wei(0),
        termination=Termination(100),
        block_number=BLOCK_NUMBER,
        tx_hash=HexBytes(b"1"),
    )
