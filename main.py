import asyncio
import logging
import os

from aiohttp import web

from bot import build_app
from server import build_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


async def run() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    port = int(os.environ.get("PORT", 8080))

    # aiohttp runner (handle_signals=False — we manage shutdown)
    web_app = build_web_app(secret=token)
    runner = web.AppRunner(web_app, handle_signals=False)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Webapp server on http://0.0.0.0:%d", port)

    ptb_app = build_app(token)
    async with ptb_app:
        await ptb_app.start()
        await ptb_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        log.info("Bot polling started. WEBAPP_URL=%s", os.environ.get("WEBAPP_URL", "(not set)"))
        try:
            await asyncio.Event().wait()  # block until KeyboardInterrupt cancels the task
        finally:
            await ptb_app.updater.stop()
            await ptb_app.stop()
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
