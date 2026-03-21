#!/usr/bin/env python3
"""Shared config and API setup for CLI scripts."""
import yaml
import lighter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

def load_config():
    return yaml.safe_load((BASE / "config.yml").read_text())

def make_api_client(cfg):
    config = lighter.Configuration(host=cfg["lighter_url"])
    config.proxy = cfg["proxy_url"]
    return lighter.ApiClient(configuration=config)

async def make_signer(cfg):
    """Create a signer with proxy support."""
    import types

    original_init = lighter.SignerClient.__init__

    def patched_init(self_signer, url, account_index, api_private_keys, **kwargs):
        original_init(self_signer, url, account_index, api_private_keys, **kwargs)
        proxy_url = cfg.get("proxy_url")
        if proxy_url:
            proxy_config = lighter.Configuration(host=url)
            proxy_config.proxy = proxy_url
            self_signer.api_client = lighter.ApiClient(configuration=proxy_config)
            self_signer.tx_api = lighter.TransactionApi(self_signer.api_client)
            self_signer.order_api = lighter.OrderApi(self_signer.api_client)
            self_signer.nonce_manager = lighter.nonce_manager.nonce_manager_factory(
                lighter.nonce_manager.NonceManagerType.OPTIMISTIC,
                account_index=account_index,
                api_client=self_signer.api_client,
                api_keys_list=list(api_private_keys.keys()),
            )

    lighter.SignerClient.__init__ = patched_init
    signer = lighter.SignerClient(
        url=cfg["lighter_url"],
        account_index=cfg["account_index"],
        api_private_keys={cfg["api_key_index"]: cfg["api_key_private"]},
    )
    lighter.SignerClient.__init__ = original_init
    return signer
