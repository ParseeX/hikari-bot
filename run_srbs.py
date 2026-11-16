"""Dedicated server launcher. No production dotenv or old bot plugins."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent


def read_profile(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if set(data) != {"schema_version", "allowed_users", "allowed_groups", "card_catalog"}:
            raise ValueError
        users = data["allowed_users"]
        if (data["schema_version"] != 1 or not isinstance(users, list) or len(users) != 1
                or not isinstance(users[0], str) or not users[0].strip()
                or users[0].startswith("REPLACE_") or data["allowed_groups"] != []
                or not isinstance(data["card_catalog"], str)):
            raise ValueError
        if data["card_catalog"] and not Path(data["card_catalog"]).is_file():
            raise ValueError
        return data
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("srbs local profile is missing or invalid") from None


def credential(directory, name):
    try:
        value = (directory / name).read_text(encoding="utf-8")
        if not value or value != value.strip() or len(value) > 4096 or "\x00" in value:
            raise ValueError
        return value
    except (OSError, ValueError):
        raise ValueError("srbs systemd credential is missing or invalid") from None


def configuration(home, credential_directory):
    # A new mapping ignores inherited HIKARI_QQ allowlists and credentials.
    profile = read_profile(home / ".config/srbs/profile.json")
    return {
        "HIKARI_QQ_ENABLED": "true", "HIKARI_QQ_SANDBOX": "false",
        "HIKARI_QQ_APP_ID": credential(credential_directory, "srbs-app-id"),
        "HIKARI_QQ_APP_SECRET": credential(credential_directory, "srbs-app-secret"),
        "HIKARI_QQ_TOKEN": "",
        "HIKARI_QQ_ALLOWED_USERS": json.dumps(profile["allowed_users"]),
        "HIKARI_QQ_ALLOWED_GROUPS": "[]",
        "HIKARI_QQ_CARD_CATALOG": profile["card_catalog"],
    }


def main():
    # -I ignores PYTHONPATH; add only this reviewed release.
    sys.path.insert(0, str(ROOT))
    from hikari_bot.qq_official.diagnostics import Diagnostics
    diagnostic = Diagnostics(lambda line: print(line, flush=True), SimpleNamespace(matched=False))
    diagnostic.phase = "srbs_configuration"
    sink = None
    nonebot = None
    try:
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if not directory or not Path(directory).is_absolute():
            raise ValueError("Start srbs through its dedicated user service")
        from hikari_bot.qq_official.config import PilotConfig
        config = PilotConfig.from_env(configuration(Path.home(), Path(directory)))
        diagnostic.phase = "srbs_initialize"
        import nonebot
        from hikari_bot.qq_official.gateway import SrbsQQAdapter
        from qq_bot import initialize
        nonebot.logger.remove()
        sink = nonebot.logger.add(diagnostic.sink, level="ERROR", format=lambda record: "{level}",
                                 backtrace=False, diagnose=False)
        initialize(nonebot, config)
        driver = nonebot.get_driver()
        driver.register_adapter(SrbsQQAdapter)
        from hikari_bot.qq_official.plugin import register
        from hikari_bot.services.card_catalog import CardCatalog
        from hikari_bot.qq_official.runtime_services import build_services
        catalog = CardCatalog(config.catalog_path) if config.catalog_path else None
        services = build_services(Path.home(), Path(directory), catalog)
        register(config, **services)

        @driver.on_bot_connect
        async def connected(bot):
            print("srbs connected; one C2C user allowed; groups disabled.", flush=True)

        diagnostic.phase = "srbs_runtime"
        print("srbs starting; no original bot plugins or collection tasks loaded.", flush=True)
        nonebot.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        diagnostic.report(exc)
        return 1
    finally:
        if nonebot is not None and sink is not None:
            nonebot.logger.remove(sink)


if __name__ == "__main__":
    raise SystemExit(main())
