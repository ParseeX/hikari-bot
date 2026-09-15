import asyncio

from hikari_bot.core import logger


def test_failure_streak_alerts_once_and_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "DATA_DIR", str(tmp_path))
    logger.new_log_file()
    sent = []

    async def notify(message):
        sent.append(message)

    monkeypatch.setattr(logger, "_notify_failure", notify)

    async def scenario():
        for index in range(9):
            await logger.log_message(f"Failed {index}")
        assert sent == []
        await logger.log_message("Failed tenth\nextra detail")
        assert len(sent) == 1
        assert sent[0].endswith("Failed tenth\nextra detail")
        for _ in range(12):
            await logger.log_message("Failed again")
        assert len(sent) == 1
        await logger.log_message("failed lowercase")
        for _ in range(10):
            await logger.log_message("Failed new streak")
        assert len(sent) == 2

    asyncio.run(scenario())


def test_notification_failure_does_not_recurse_or_skip_other_admins(tmp_path, monkeypatch):
    import nonebot
    from hikari_bot.core import constants

    monkeypatch.setattr(logger, "DATA_DIR", str(tmp_path))
    logger.new_log_file()
    attempts = []

    class Bot:
        async def send_private_msg(self, user_id, message):
            attempts.append(user_id)
            raise RuntimeError("delivery unavailable")

    monkeypatch.setattr(nonebot, "get_bot", lambda: Bot())
    monkeypatch.setattr(constants, "ADMIN", [1, 2])

    async def scenario():
        for _ in range(11):
            await logger.log_message("Failed request")
        assert len(await logger.log_read()) == 11

    asyncio.run(scenario())
    assert attempts == [1, 2]
