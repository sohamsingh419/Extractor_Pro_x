import asyncio
import importlib
import signal
import os
import threading
from pyrogram import idle
from Extractor.modules import ALL_MODULES

# ── Health-check web server ─────────────────────────────────────────
# Deployment platforms (Render, Railway, Koyeb, etc.) require a
# process that binds to $PORT and responds to HTTP health checks.
# We spin up a tiny Flask server in a background thread so the
# Pyrogram bot keeps running in the main asyncio loop.
# ───────────────────────────────────────────────────────────────────

def start_health_server():
    """Start a minimal Flask app on the port required by the platform."""
    from flask import Flask
    app = Flask(__name__)
    PORT = int(os.environ.get("PORT", 8000))

    @app.route("/")
    def home():
        return "Bot is alive!", 200

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    # Use threaded=True so it doesn't block the bot
    app.run(host="0.0.0.0", port=PORT, threaded=True)

# ── Graceful shutdown helpers ────────────────────────────────────────

loop = asyncio.get_event_loop()
should_exit = asyncio.Event()

def _on_shutdown(signum, frame):
    """Sync signal handler – safely schedules the async exit."""
    print(f"Received signal {signum}, shutting down gracefully...")
    loop.call_soon_threadsafe(should_exit.set)

signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT, _on_shutdown)

# ── Main bot boot sequence ─────────────────────────────────────────

async def sumit_boot():
    for all_module in ALL_MODULES:
        importlib.import_module("Extractor.modules." + all_module)

    print("» ʙᴏᴛ ᴅᴇᴘʟᴏʏ sᴜᴄᴄᴇssғᴜʟʟʏ ✨ 🎉")

    # idle() keeps the bot alive; wrap it so we can break out on signal
    idle_task = loop.create_task(idle())
    exit_task = loop.create_task(should_exit.wait())

    done, pending = await asyncio.wait(
        [idle_task, exit_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    # Cancel whichever task is still running
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    print("» ɢᴏᴏᴅ ʙʏᴇ ! sᴛᴏᴘᴘɪɴɢ ʙᴏᴛ.")

if __name__ == "__main__":
    # Start the health-check server in a daemon thread
    server_thread = threading.Thread(target=start_health_server, daemon=True)
    server_thread.start()
    print(f"» Health server starting on port {os.environ.get('PORT', 8000)}...")

    try:
        loop.run_until_complete(sumit_boot())
    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        print("Loop closed.")
