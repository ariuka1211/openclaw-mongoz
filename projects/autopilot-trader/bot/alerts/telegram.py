"""
Telegram alert sender for bot notifications.
"""

import logging

import aiohttp


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

    async def send(self, text: str):
        if not self.enabled:
            logging.debug(f"[alert] {text}")
            return
        import aiohttp
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                }
                async with session.post(url, json=payload, timeout=timeout) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get('Retry-After', 'unknown')
                        logging.warning(f"Telegram rate limited (429), retry after {retry_after}s — alert dropped")
                    elif resp.status != 200:
                        body = await resp.text()
                        logging.warning(f"Telegram API error {resp.status}: {body}")
                    else:
                        logging.debug(f"Alert sent to Telegram ({len(text)} chars)")
        except Exception as e:
            logging.warning(f"Telegram send failed: {e}")
