"""Explicit, separate entry point; never loads the existing bot's plugins/env."""
import argparse
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def initialize(nonebot, config):
    from nonebot.adapters.qq.config import Config as SDKConfig
    supports_sandbox = 'qq_is_sandbox' in SDKConfig.model_fields
    if config.sandbox and not supports_sandbox:
        raise ValueError('当前 QQ adapter 已移除沙箱模式；拒绝自动改连生产。请明确设置 HIKARI_QQ_SANDBOX=false。')
    sdk_options = {'qq_is_sandbox': config.sandbox} if supports_sandbox else {}
    # NoneBot 2.5 always probes .env for Env(), even with _env_file=None.
    # Initialize in an empty directory and give Config an explicit file list.
    previous = Path.cwd()
    with TemporaryDirectory(prefix="hikari-qq-init-") as empty:
        try:
            os.chdir(empty)
            nonebot.init(_env_file=[Path(empty) / "unused.env"], driver="~aiohttp",
                         log_level="WARNING", superusers=set(), command_start={"", "/"},
                         qq_bots=[config.bot_info()], **sdk_options)
        finally:
            os.chdir(previous)


def main():
    parser = argparse.ArgumentParser(description="Hikari official QQ pilot")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--connect", action="store_true", help="start an authorized live business connection")
    mode.add_argument("--pair", choices=("c2c", "group"), help="receive-only, one-shot OpenID discovery")
    parser.add_argument("--pair-timeout", type=int, default=120, help="pairing lifetime in seconds (30-300)")
    args = parser.parse_args()
    if not 30 <= args.pair_timeout <= 300:
        parser.error("--pair-timeout must be between 30 and 300 seconds")
    if not args.connect and not args.pair:
        for package in ("nonebot2", "nonebot-adapter-qq", "aiohttp"):
            try:
                installed = version(package)
            except PackageNotFoundError:
                installed = "not installed"
            print(f"{package}: {installed}")
        print("Offline check only; no configuration, credentials or network accessed.")
        return

    from hikari_bot.qq_official.config import PilotConfig

    try:
        config = PilotConfig.from_env(os.environ, pairing=bool(args.pair))
    except ValueError as exc:
        parser.error(str(exc))
    if args.pair:
        from hikari_bot.qq_official.pairing import run
        return run(config, args.pair, args.pair_timeout)
    import nonebot
    from hikari_bot.qq_official.gateway import SrbsQQAdapter as Adapter

    initialize(nonebot, config)
    nonebot.get_driver().register_adapter(Adapter)
    from hikari_bot.qq_official.plugin import register

    from hikari_bot.services.card_catalog import CardCatalog
    from hikari_bot.qq_official.runtime_services import build_migration_queries
    from hikari_bot.qq_official.card_actions import existing_art_loader
    catalog = CardCatalog(config.catalog_path) if config.catalog_path else None
    queries = build_migration_queries(Path.home(), catalog, config=config)
    register(config, queries=queries, image_loader=existing_art_loader if catalog else None)
    nonebot.run()


if __name__ == "__main__":
    raise SystemExit(main())
