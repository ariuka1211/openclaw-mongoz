"""Lighter REST API auth token manager with auto-refresh."""
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(__file__).parent / ".auth_token.json"
REFRESH_BUFFER_SECONDS = 30 * 60  # refresh 30 min before expiry (token max is 6h)
DEFAULT_TOKEN_LIFETIME_SECONDS = 6 * 60 * 60  # 6 hours (Lighter max)


class LighterAuthManager:
    """Manages long-lived Lighter REST API auth tokens with auto-refresh."""

    def __init__(self, signer, account_index: int,
                 token_lifetime_seconds: int = DEFAULT_TOKEN_LIFETIME_SECONDS,
                 refresh_buffer_seconds: int = REFRESH_BUFFER_SECONDS):
        self.signer = signer
        self.account_index = account_index
        self.token_lifetime = token_lifetime_seconds
        self.refresh_buffer = refresh_buffer_seconds
        self._cached_token: str | None = None
        self._expires_at: int = 0

    def get_auth_token(self) -> str:
        """Get a valid auth token, generating a new one if needed."""
        now = int(time.time())

        # Check in-memory cache first
        if self._cached_token and (self._expires_at - now) > self.refresh_buffer:
            return self._cached_token

        # Check disk cache
        disk = self._load_from_disk()
        if disk and (disk["expires_at"] - now) > self.refresh_buffer:
            self._cached_token = disk["token"]
            self._expires_at = disk["expires_at"]
            return self._cached_token

        # Generate new token
        return self._generate_and_store()

    def _generate_and_store(self) -> str:
        """Generate a new long-lived auth token and persist it."""
        auth, err = self.signer.create_auth_token_with_expiry(
            deadline=self.token_lifetime
        )
        if err:
            raise RuntimeError(f"Failed to create auth token: {err}")
        if not auth:
            raise RuntimeError(
                "create_auth_token_with_expiry returned empty auth token "
                "(C signer likely failed silently)"
            )

        now = int(time.time())
        expires_at = now + self.token_lifetime

        self._cached_token = auth
        self._expires_at = expires_at

        self._save_to_disk(auth, now, expires_at)
        logger.info(f"Generated new Lighter auth token, expires in {self.token_lifetime // 3600}h")
        return auth

    def _load_from_disk(self) -> dict | None:
        try:
            if TOKEN_FILE.exists():
                data = json.loads(TOKEN_FILE.read_text())
                if data.get("account_index") == self.account_index:
                    return data
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Could not load cached token: {e}")
        return None

    def _save_to_disk(self, token: str, created_at: int, expires_at: int):
        data = {
            "token": token,
            "expires_at": expires_at,
            "created_at": created_at,
            "account_index": self.account_index,
        }
        TOKEN_FILE.write_text(json.dumps(data, indent=2))
        TOKEN_FILE.chmod(0o600)
