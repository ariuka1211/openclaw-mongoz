"""
AI Trader — Main daemon
Async loop, 3-min cycles, systemd lifecycle.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path

from context.data_reader import DataReader
from context.outcome_analyzer import OutcomeAnalyzer
from context.pattern_engine import PatternEngine
from context.stats_formatter import StatsFormatter
from context.prompt_builder import PromptBuilder
from cycle_runner import CycleRunner
from db import DecisionDB
from ipc.bot_protocol import BotProtocol
from llm_client import LLMClient
from safety import SafetyLayer

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ai-trader.log"),
    ],
)
log = logging.getLogger("ai-trader")


def load_prompts() -> tuple[str, str]:
    prompt_dir = Path("prompts")
    return (prompt_dir / "system.txt").read_text(), (prompt_dir / "decision.txt").read_text()


class AITrader:

    def __init__(self, config: dict):
        self.config = config
        self.db = DecisionDB(config["db_path"])
        _config_dir = os.path.dirname(os.path.abspath(config["_config_path"]))

        self.data_reader = DataReader(self)
        self.pattern_engine = PatternEngine(self)
        self.outcome_analyzer = OutcomeAnalyzer(self)
        self.stats_formatter = StatsFormatter(self)
        self.prompt_builder = PromptBuilder(self)

        self.safety = SafetyLayer(config, self.db)
        self.llm = LLMClient(config["llm"])
        self.bot_ipc = BotProtocol(self)
        self.cycle_runner = CycleRunner(self)
        self.system_prompt, self.decision_template = load_prompts()
        self.system_prompt = self.system_prompt.format(max_positions=self.safety.max_positions)

        self.cycle_interval = config.get("cycle_interval_seconds", 180)
        self.MAX_CONSECUTIVE_FAILURES = config.get("max_consecutive_failures", 5)
        self.max_rejection_halt_count = config.get("max_rejection_halt_count", 15)
        self.rejection_halt_window_minutes = config.get("rejection_halt_window_minutes", 30)
        self._last_purge_time: float = 0
        self.running = True
        self.emergency_halt = False
        self.consecutive_failures = 0
        self.last_cycle_time: float = 0
        self._last_state_hash: str | None = None
        self._cycles_skipped: int = 0
        self._last_sent_decision_id = None
        self._ack_timeout_seconds = config.get("ack_timeout_seconds", 300)
        self._last_processed_outcome_ts: str | None = None

        self.decision_file = Path(_config_dir) / config["decision_file"]
        self.result_file = Path(_config_dir) / config.get("result_file", "../ai-result.json")

        log.info("AI Trader initialized")
        provider = config['llm'].get('provider', 'legacy')
        log.info(f"  LLM provider: {provider}")
        log.info(f"  DB: {config['db_path']}")
        log.info(f"  Decision file: {self.decision_file}")
        log.info(f"  Kill switch: {self.MAX_CONSECUTIVE_FAILURES} failures, {self.max_rejection_halt_count} rejections/{self.rejection_halt_window_minutes}min")

    async def run_forever(self):
        log.info("AI Trader starting...")

        cleaned = []
        for fpath in [self.decision_file, Path(str(self.decision_file) + ".ack"), self.result_file]:
            if fpath.exists():
                try:
                    fpath.unlink()
                    cleaned.append(fpath.name)
                except Exception:
                    pass
        if cleaned:
            log.info(f"IPC startup cleanup: removed {', '.join(cleaned)}")
        else:
            log.info("IPC startup cleanup: no stale files found")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        cycle_count = 0
        while self.running and not self.emergency_halt:
            start = time.time()
            cycle_id = str(uuid.uuid4())[:8]
            cycle_count += 1

            try:
                await self.cycle_runner.execute(cycle_id)
                self.consecutive_failures = 0
                log.info(f"Cycle {cycle_id} complete ({cycle_count} total)")
            except Exception as e:
                self.consecutive_failures += 1
                self.safety._last_failure_time = time.time()
                log.error(f"Cycle {cycle_id} failed ({self.consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}): {e}", exc_info=True)
                self.db.log_alert("error", f"Cycle failed: {e}")

                if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    await self.bot_ipc.emergency_halt(f"{self.MAX_CONSECUTIVE_FAILURES}+ consecutive failures")
                    log.critical(f"Emergency halt — {self.MAX_CONSECUTIVE_FAILURES}+ consecutive failures")
                    self.db.log_alert("critical", f"Emergency halt — {self.MAX_CONSECUTIVE_FAILURES}+ consecutive failures")

            if self.emergency_halt:
                break

            signals, signals_config = self.data_reader.read_signals()
            equity = self.data_reader.read_equity()
            if equity <= 0:
                equity = signals_config.get("accountEquity", 0)  # fallback
            kill_triggers = self.safety.check_kill_switch(
                self.consecutive_failures,
                self.db.count_recent_rejections(self.rejection_halt_window_minutes),
                equity=equity,
            )
            if kill_triggers:
                await self.bot_ipc.emergency_halt(f"Kill switch: {'; '.join(kill_triggers)}")
                log.critical(f"Kill switch triggered: {kill_triggers}")
                self.db.log_alert("critical", f"Kill switch: {'; '.join(kill_triggers)}")

            if time.time() - self._last_purge_time > 86400:
                self._last_purge_time = time.time()
                try:
                    self.db.purge_old_data(keep_days=7)
                except Exception as e:
                    log.warning(f"DB purge failed: {e}")

            self.last_cycle_time = time.time()

            elapsed = time.time() - start
            remaining = max(0, self.cycle_interval - elapsed)
            if self.running and not self.emergency_halt:
                log.info(f"Next cycle in {remaining:.0f}s")
                while remaining > 0 and self.running:
                    chunk = min(2, remaining)
                    await asyncio.sleep(chunk)
                    remaining -= chunk

        if self.emergency_halt:
            log.info("Waiting 60s for bot to process close_all...")
            await asyncio.sleep(60)
        await self.llm.close()
        self.db.close()

        if self.emergency_halt:
            log.critical("AI Trader halted (emergency). Manual restart required.")
        else:
            log.info("AI Trader stopped gracefully.")

    def _request_shutdown(self):
        log.info("Shutdown requested...")
        self.running = False


def main():
    config_path = os.environ.get("AI_TRADER_CONFIG", "config.json")
    if not Path(config_path).exists():
        log.error(f"Config not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    config["_config_path"] = config_path
    trader = AITrader(config)
    asyncio.run(trader.run_forever())


if __name__ == "__main__":
    main()
