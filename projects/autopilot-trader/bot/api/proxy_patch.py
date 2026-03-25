"""
SOCKS5 Proxy Patch for lighter SDK.

Patches lighter.rest.RESTClientObject to support SOCKS5 proxies via aiohttp-socks.
Must be imported before any lighter ApiClient is created.
"""

import lighter.rest as _lighter_rest
import aiohttp_socks as _aiohttp_socks
import aiohttp as _aiohttp

_original_rest_client_init = _lighter_rest.RESTClientObject.__init__


def _patched_rest_client_init(self, configuration):
    """Patched __init__ that uses ProxyConnector for socks5:// proxy URLs."""
    import ssl as _ssl

    proxy = getattr(configuration, "proxy", None)

    if proxy and proxy.startswith("socks5://"):
        # Build SSL context (same logic as original)
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

        # Use ProxyConnector for SOCKS5 — it handles the proxy at connector level
        connector = _aiohttp_socks.ProxyConnector.from_url(
            proxy, ssl=ssl_context
        )

        # Store proxy info — clear self.proxy since connector handles it
        self.proxy = None
        self.proxy_headers = configuration.proxy_headers

        # Create pool manager
        self.pool_manager = _aiohttp.ClientSession(
            connector=connector, trust_env=True
        )

        # Set up retry client if configured
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
        # HTTP proxy or no proxy — use original behavior
        _original_rest_client_init(self, configuration)


_lighter_rest.RESTClientObject.__init__ = _patched_rest_client_init
