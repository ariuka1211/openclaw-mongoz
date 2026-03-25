#!/usr/bin/env python3
"""Fetch current positions from Lighter exchange."""

import asyncio
import json
import os
import sys

# Load env FIRST before importing lighter
from dotenv import load_dotenv
load_dotenv("/root/.openclaw/workspace/.env")

# Setup path
sys.path.insert(0, "/root/.openclaw/workspace/projects/autopilot-trader/executor")

import lighter.rest as _lighter_rest
import aiohttp_socks as _aiohttp_socks
import aiohttp as _aiohttp
import ssl as _ssl

_original_rest_client_init = _lighter_rest.RESTClientObject.__init__

def _patched_rest_client_init(self, configuration):
    """Patched __init__ that uses ProxyConnector for socks5:// proxy URLs."""
    proxy = getattr(configuration, "proxy", None)

    if proxy and proxy.startswith("socks5://"):
        ssl_context = _ssl.create_default_context(
            cafile=configuration.ssl_ca_cert
        )
        if configuration.cert_file:
            ssl_context.load_cert_chain(
                configuration.cert_file, keyfile=configuration.key_file
            )
        if not configuration.verify_ssl:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = _ssl.CERT_NONE

        connector = _aiohttp_socks.ProxyConnector.from_url(
            proxy, ssl=ssl_context
        )

        self.proxy = None
        self.proxy_headers = configuration.proxy_headers

        self.pool_manager = _aiohttp.ClientSession(
            connector=connector, trust_env=True
        )

        retries = configuration.retries
        if retries is not None:
            import aiohttp_retry as _aiohttp_retry
            self.retry_client = _aiohttp_retry.RetryClient(
                client_session=self.pool_manager,
                retry_options=_aiohttp_retry.ExponentialRetry(
                    attempts=retries,
                    factor=0.0,
                    start_timeout=0.0,
                    max_timeout=120.0,
                ),
            )
        else:
            self.retry_client = None
    else:
        _original_rest_client_init(self, configuration)

_lighter_rest.RESTClientObject.__init__ = _patched_rest_client_init

import lighter

# Config
ACCOUNT_INDEX = 719758
API_KEY_INDEX = 3
LIGHTER_URL = "https://mainnet.zklighter.elliot.ai"

async def fetch_positions():
    config = lighter.Configuration(host=LIGHTER_URL)
    
    # Add proxy
    proxy_user = os.environ.get("LIGHTER_PROXY_USER", "")
    proxy_pass = os.environ.get("LIGHTER_PROXY_PASS", "")
    proxy_host = os.environ.get("LIGHTER_PROXY_HOST", "")
    proxy_port = os.environ.get("LIGHTER_PROXY_PORT", "")
    if proxy_user and proxy_pass and proxy_host and proxy_port:
        config.proxy = f"socks5://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}/"
    
    client = lighter.ApiClient(config)
    account_api = lighter.AccountApi(client)
    
    result = await account_api.account(
        by="index", value=str(ACCOUNT_INDEX),
        _request_timeout=30,
    )
    
    positions = []
    for account in result.accounts:
        for pos in account.positions:
            if hasattr(pos, 'position') and pos.position:
                size = float(pos.position)
                if size == 0:
                    continue
                entry = float(pos.avg_entry_price) if hasattr(pos, 'avg_entry_price') and pos.avg_entry_price else 0
                market_id = pos.market_id if hasattr(pos, 'market_id') else (pos.market_index if hasattr(pos, 'market_index') else 0)
                margin_fraction = float(pos.initial_margin_fraction) if hasattr(pos, 'initial_margin_fraction') and pos.initial_margin_fraction else 0
                if margin_fraction > 0:
                    effective_leverage = round(min(100.0 / margin_fraction, 10.0), 1)
                else:
                    effective_leverage = 10.0
                sign = int(pos.sign) if hasattr(pos, 'sign') else 1
                side = "long" if sign > 0 else "short"
                
                symbol = getattr(pos, 'symbol', None) or f"MARKET_{market_id}"
                
                positions.append({
                    "market_id": market_id,
                    "symbol": symbol,
                    "size": size,
                    "side": side,
                    "entry_price": entry,
                    "leverage": effective_leverage,
                })
    
    print(json.dumps(positions, indent=2))
    return positions

if __name__ == "__main__":
    asyncio.run(fetch_positions())
