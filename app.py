"""
Termin Hunter — desktop app entry point.

Runs a small local UI (ui/index.html) in a native window via pywebview,
and drives the booking engine (engine.py) in a background thread so the
GUI stays responsive. Telegram is optional: if a token + chat ID are set
in the config, you'll also get push notifications and can reply from your
phone; without it, the desktop window is the only control surface.
"""

import asyncio
import json
import os
import sys
import threading
import queue
import time
import webbrowser

import webview

from engine import TerminBot

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".termin-hunter")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
# Off by default for normal/production use. To re-enable right-click ->
# Inspect for debugging, run with the env var set instead of editing this
# file, e.g.:
#   Windows (PowerShell): $env:TERMIN_HUNTER_DEBUG=1; python app.py
#   Mac/Linux:             TERMIN_HUNTER_DEBUG=1 python app.py
DEBUG = os.environ.get("TERMIN_HUNTER_DEBUG") == "1"

os.makedirs(CONFIG_DIR, exist_ok=True)


class AppState:
    def __init__(self):
        self.bot: TerminBot | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.events = queue.Queue()
        self.telegram_app = None


state = AppState()


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def on_engine_event(event):
    state.events.put(event)
    # Mirror important events to Telegram, if configured.
    tg = state.telegram_app
    chat_id = state.bot.config.get("telegram_chat_id") if state.bot else None
    if not (tg and chat_id):
        return
    text = None
    if event["kind"] == "times_found":
        times = ", ".join(event["times"])
        text = f"🚨 Appointment(s) found! Times: {times}\n\nOpen the app or reply with the exact time to book, or 'no' to skip."
    elif event["kind"] == "times_found_unreadable":
        text = "🚨 Appointment found, but I couldn't read the times — check the app window."
    elif event["kind"] == "paused":
        text = f"⏸️ Paused. {event.get('reason', '')}".strip()
    elif event["kind"] == "form_filled":
        failed = event.get("failed") or []
        text = (f"🎉 Form filled for {event['appointment_time']}! " +
                (f"Manual entry needed for: {', '.join(failed)}. " if failed else "") +
                "Check the browser window and submit.")
    elif event["kind"] == "booked":
        text = f"✅ Booked! Appointment confirmed for {event['appointment_time']}. Check your email / the browser to verify."
    if text:
        asyncio.run_coroutine_threadsafe(
            tg.bot.send_message(chat_id=chat_id, text=text), state.loop
        )


class Api:
    """Methods exposed to the UI as window.pywebview.api.<method>()"""

    def get_config(self):
        return load_config()

    def save_config(self, config):
        save_config(config)
        return {"ok": True}

    def start(self, config):
        if state.thread and state.thread.is_alive():
            return {"ok": False, "error": "Already running"}
        save_config(config)
        state.thread = threading.Thread(target=_run_engine_thread, args=(config,), daemon=True)
        state.thread.start()
        return {"ok": True}

    def pause(self):
        if state.bot:
            state.bot.pause()
        return {"ok": True}

    def resume(self):
        if state.bot:
            state.bot.resume()
        return {"ok": True}

    def select_time(self, exact_time):
        if state.bot and state.loop:
            asyncio.run_coroutine_threadsafe(state.bot.select_time(exact_time), state.loop)
        return {"ok": True}

    def skip(self):
        if state.bot and state.loop:
            asyncio.run_coroutine_threadsafe(state.bot.skip_appointment(), state.loop)
        return {"ok": True}

    def poll_events(self):
        events = []
        while not state.events.empty():
            events.append(state.events.get_nowait())
        return events

    def open_external(self, url):
        webbrowser.open(url)
        return {"ok": True}


def _run_engine_thread(config):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state.loop = loop
    state.bot = TerminBot(config, on_engine_event)

    async def main():
        tasks = [asyncio.create_task(state.bot.run())]
        token = config.get("telegram_token")
        chat_id = config.get("telegram_chat_id")
        if token and chat_id:
            tasks.append(asyncio.create_task(_run_telegram(token)))
        await asyncio.gather(*tasks)

    try:
        loop.run_until_complete(main())
    except Exception as e:
        on_engine_event({"kind": "log", "message": f"Engine crashed: {e}"})


async def _run_telegram(token):
    """Optional: lets you pause/resume/pick a time by texting the bot."""
    from telegram.ext import ApplicationBuilder, MessageHandler, filters

    async def handle_message(update, context):
        text = update.message.text.strip().lower()
        if text in ("pause", "stop"):
            state.bot.pause()
        elif text in ("resume", "go"):
            state.bot.resume()
        elif text == "no":
            await state.bot.skip_appointment()
        elif text in [t.lower() for t in state.bot.available_times]:
            match = next(t for t in state.bot.available_times if t.lower() == text)
            await state.bot.select_time(match)

    application = ApplicationBuilder().token(token).build()
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    state.telegram_app = application
    await application.initialize()
    await application.start()
    await application.updater.start_polling()


def resource_path(*parts):
    """
    Resolve a path to a bundled resource that works both in dev mode
    (running `python app.py` directly) and once packaged with PyInstaller
    --onefile, which extracts --add-data files to a temp dir referenced by
    sys._MEIPASS — NOT next to the exe, which is what sys.argv[0] would
    (incorrectly) point to.
    """
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, *parts)


def main():
    index_path = resource_path("ui", "index.html")
    splash_path = resource_path("ui", "splash.html")
    if not os.path.exists(index_path):
        raise FileNotFoundError(
            f"Couldn't find the UI at {index_path}. If you're running a PyInstaller "
            f"build, make sure it was built with --add-data pointing at the ui folder "
            f"(see README.md)."
        )

    main_window = webview.create_window(
        "Termin Hunter", index_path, js_api=Api(),
        width=460, height=760, resizable=True, min_size=(420, 600), hidden=True,
        background_color="#12151a",
    )

    splash_window = None
    if os.path.exists(splash_path):
        # Loaded as raw HTML content rather than a file path/URL — pywebview
        # serves local files through its own internal server, which chokes
        # on this file (it's ~340KB since the splash image is embedded as
        # base64 to sidestep a *different* file-serving issue). Passing the
        # content directly skips that layer entirely; it's handed straight
        # to the native webview engine, no serving involved.
        with open(splash_path, "r", encoding="utf-8") as f:
            splash_html = f.read()
        splash_window = webview.create_window(
            "", html=splash_html, width=500, height=500,
            frameless=True, on_top=True, resizable=False,
            background_color="#12151a",  # avoids a white flash before content paints
        )

    def _reveal_main():
        if splash_window:
            # The splash's own JS drives its full fade-in -> hold -> fade-out
            # sequence, anchored to the real image-load event. This just
            # needs to wait at least that long before tearing it down — no
            # need to trigger anything mid-sequence from here anymore.
            time.sleep(3.2)
            try:
                splash_window.destroy()
            except Exception:
                pass
        main_window.show()

    webview.start(_reveal_main, debug=DEBUG)


if __name__ == "__main__":
    main()