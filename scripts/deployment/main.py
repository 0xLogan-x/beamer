import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence, Union, cast

import click
from eth_account.signers.local import LocalAccount
from eth_utils import encode_hex, to_wei
from web3 import Web3
from web3.contract import Contract
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.types import TxParams, Wei

from beamer.agent.util import account_from_keyfile, make_web3, transact

GANACHE_CHAIN_ID = 1337


class DeployedContract(Contract):
    deployment_block: int
    deployment_args: list[Any]
    name: str


def load_contracts_info(contracts_path: Path) -> dict[str, tuple]:
    contracts: dict[str, tuple] = {}
    for path in contracts_path.glob("*.json"):
        with path.open() as fp:
            info = json.load(fp)
        contracts[info["contractName"]] = (info["abi"], info["bytecode"])
    return contracts


def get_commit_id() -> str:
    output = subprocess.check_output(["git", "rev-parse", "HEAD"])
    return output.decode("utf-8").strip()


CONTRACTS_PATH = Path("contracts/build/contracts")
CONTRACTS: dict[str, tuple] = load_contracts_info(CONTRACTS_PATH)


def collect_contracts_info(contracts: dict[str, DeployedContract]) -> dict:
    return {
        name: dict(
            address=contract.address,
            deployment_block=contract.deployment_block,
            deployment_args=contract.deployment_args,
        )
        for name, contract in contracts.items()
    }


def deploy_contract(web3: Web3, constructor_spec: Union[str, Sequence]) -> DeployedContract:
    # constructor_spec is either
    # 1) a contract name e.g. "Foo", or
    # 2) a sequence containing the contract name and
    #    constructor arguments, e.g. ["Foo", "0x1CEE82EEd89Bd5Be5bf2507a92a755dcF1D8e8dc"]
    if isinstance(constructor_spec, str):
        name = constructor_spec
        args: Sequence = ()
    else:
        name = constructor_spec[0]
        args = constructor_spec[1:]

    data = CONTRACTS[name]
    print(f"Deploying {name}")
    ContractFactory = web3.eth.contract(abi=data[0], bytecode=data[1])

    receipt = transact(ContractFactory.constructor(*args), timeout=600)

    address = receipt.contractAddress
    deployed = cast(DeployedContract, web3.eth.contract(address=address, abi=data[0]))
    deployed.deployment_block = receipt.blockNumber
    deployed.deployment_args = list(args)
    deployed.name = name

    print(f"Deployed {name} at {address} in {encode_hex(receipt.transactionHash)}")
    return deployed


def deploy_beamer(
    account: LocalAccount,
    config: dict,
    l2_config: dict,
    resolver: Contract,
    allow_same_chain: bool,
    deploy_mintable_token: bool,
) -> tuple[dict[str, DeployedContract], dict[str, DeployedContract]]:

    web3 = make_web3(l2_config["rpc"], account)
    assert web3.eth.chain_id == l2_config["chain_id"]

    deployed_contracts = []
    token = None
    if deploy_mintable_token:
        token = deploy_contract(web3, ("MintableToken", int(1e18)))
        deployed_contracts.append(token)

    l1_messenger = deploy_contract(resolver.web3, l2_config["l1_messenger"])
    l2_messenger = deploy_contract(web3, l2_config["l2_messenger"])

    request_manager_arguments = l2_config["request_manager_arguments"]
    claim_stake = to_wei(request_manager_arguments["claim_stake"], "ether")
    claim_request_extension = request_manager_arguments["claim_request_extension"]
    claim_period = request_manager_arguments["claim_period"]
    challenge_period_extension = request_manager_arguments["challenge_period_extension"]

    request_manager = deploy_contract(
        web3,
        (
            "RequestManager",
            claim_stake,
            claim_request_extension,
            claim_period,
            challenge_period_extension,
        ),
    )

    # Configure finality period for each supported target chain
    for other_l2_config in config["L2"]:
        if (
            allow_same_chain
            or other_l2_config is not l2_config
            or other_l2_config["chain_id"] == GANACHE_CHAIN_ID
        ):
            transact(
                request_manager.functions.setFinalityPeriod(
                    other_l2_config["chain_id"], other_l2_config["finality_period"]
                )
            )

    fill_manager = deploy_contract(web3, ("FillManager", l2_messenger.address))
    transact(fill_manager.functions.setResolver(resolver.address))

    # Authorize call chain
    transact(l2_messenger.functions.addCaller(fill_manager.address))
    transact(
        resolver.functions.addCaller(
            l2_config["chain_id"],
            l2_messenger.address,
            l1_messenger.address,
        ),
        timeout=600,
    )
    transact(
        resolver.functions.addRequestManager(
            l2_config["chain_id"], request_manager.address, l1_messenger.address
        ),
        timeout=600,
    )
    transact(l1_messenger.functions.addCaller(resolver.address), timeout=600)
    transact(
        request_manager.functions.addCaller(
            resolver.web3.eth.chain_id,
            l1_messenger.address,
            l2_messenger.address,
        )
    )

    tokens = l2_config.get("tokens", [])

    for token_arguments in tokens:
        token_address = token_arguments["token_address"]
        if token_address == "mintable_token":
            if token is None:
                raise ValueError(
                    "Expecting mintable token to be deployed. (option --deploy-mintable-token)"
                )
            token_address = token.address
        decimals = (
            web3.eth.contract(address=token_address, abi=CONTRACTS["MintableToken"][0])
            .functions.decimals()
            .call()
        )
        token_arguments = (
            token_address,
            int(float(token_arguments["transfer_limit"]) * pow(10, decimals)),
            int(float(token_arguments["min_lp_fee"]) * pow(10, decimals)),
            token_arguments["lp_fee_ppm"],
            token_arguments["protocol_fee_ppm"],
        )
        transact(request_manager.functions.updateToken(*token_arguments))

    l1_contracts = {l1_messenger.name: l1_messenger}
    deployed_contracts.extend(
        [
            request_manager,
            fill_manager,
            l2_messenger,
        ]
    )

    l2_contracts = {deployed.name: deployed for deployed in deployed_contracts}
    return l1_contracts, l2_contracts


@click.command()
@click.option(
    "--keystore-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    metavar="FILE",
    help="The file that stores the key for the account to be used.",
)
@click.option(
    "--password", type=str, required=True, help="The password needed to unlock the account."
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    metavar="DIR",
    help="The output directory.",
)
@click.option(
    "--config-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    required=True,
    metavar="FILE",
    help="The file containing deployment information.",
)
@click.option(
    "--allow-same-chain/--disallow-same-chain",
    default=False,
    show_default=True,
    help="Whether to allow source and target chains to be the same.",
)
@click.option(
    "--deploy-mintable-token",
    is_flag=True,
    help="Whether to deploy MintableToken.sol on the rollups or not",
)
def main(
    keystore_file: Path,
    password: str,
    output_dir: Path,
    config_file: Path,
    allow_same_chain: bool,
    deploy_mintable_token: bool,
) -> None:

    with open(config_file) as f:
        config = json.load(f)

    account = account_from_keyfile(keystore_file, password)
    print("Deployer:", account.address)

    def _margin_gas_price_strategy(web3: Web3, transaction_params: TxParams) -> Wei:
        return Wei(int(rpc_gas_price_strategy(web3, transaction_params) * 1.5))

    web3_l1 = make_web3(config["L1"]["rpc"], account, _margin_gas_price_strategy)

    resolver = deploy_contract(web3_l1, "Resolver")

    deployment_data: dict = {"beamer_commit": get_commit_id(), "deployer": account.address}
    deployment_data["L1"] = collect_contracts_info({"Resolver": resolver})
    deployment_data["L2"] = {}

    output_dir.mkdir(parents=True, exist_ok=True)
    for l2_config in config["L2"]:
        name = l2_config["name"]
        chain_id = l2_config["chain_id"]
        print(f"Deployment for {name}")

        l1_data, l2_data = deploy_beamer(
            account, config, l2_config, resolver, allow_same_chain, deploy_mintable_token
        )
        deployment_data["L1"].update(collect_contracts_info(l1_data))
        deployment_data["L2"][chain_id] = collect_contracts_info(l2_data)
        for contract_name in l2_data:
            shutil.copy(CONTRACTS_PATH / f"{contract_name}.json", output_dir)

    for contract_name in deployment_data["L1"]:
        shutil.copy(CONTRACTS_PATH / f"{contract_name}.json", output_dir)

    with output_dir.joinpath("deployment.json").open("w") as f:
        json.dump(deployment_data, f, indent=2)
    print(f"Deployment data stored at {output_dir}")


if __name__ == "__main__":
    main()
