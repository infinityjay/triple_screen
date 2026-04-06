from __future__ import annotations

import argparse
import logging
import sys
import time

from alpaca import AlpacaClient
from loader import load_settings
from scan_engine import TripleScreenScanner
from sqlite import SQLiteStorage
from telegram import TelegramNotifier


def _configure_logging(log_level: str, log_file) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Triple Screen Scanner")
    parser.add_argument("--config", default=None, help="Path to YAML config file")
    parser.add_argument("--once", action="store_true", help="Run a single scan and exit")
    parser.add_argument("--loop", action="store_true", help="Run forever using configured interval")
    parser.add_argument("--dry-run", action="store_true", help="Run scan without Telegram sends or alert-log updates")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    _configure_logging(settings.runtime.log_level, settings.runtime.log_file)

    logger = logging.getLogger(__name__)
    logger.info("bootstrapping %s", settings.app.name)
    logger.info("config loaded from %s", settings.config_path)
    if args.dry_run:
        logger.info("dry-run CLI flag enabled")

    storage = SQLiteStorage(settings.storage.database_path)
    storage.init_db()

    scanner = TripleScreenScanner(
        settings=settings,
        market_data=AlpacaClient(
            settings.alpaca,
            storage=storage,
            market_timezone=settings.app.timezone,
        ),
        storage=storage,
        notifier=TelegramNotifier(settings.alerts.telegram),
        dry_run=args.dry_run,
    )

    run_forever = args.loop or not args.once
    while True:
        try:
            scanner.run_scan()
        except Exception as exc:
            logger.exception("scan loop failed: %s", exc)
            if not args.dry_run:
                scanner.notifier.send_error(str(exc))

        if not run_forever:
            return 0

        logger.info("sleeping %s minutes until next scan", settings.runtime.scan_interval_minutes)
        time.sleep(settings.runtime.scan_interval_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
