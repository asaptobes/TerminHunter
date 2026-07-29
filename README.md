# Termin Hunter

A desktop app for watching a Stuttgart Konsentas booking portal (or any
service on it — Ausländerbehörde, driving licence exchange, etc.) and
grabbing an appointment slot the moment one opens up.

Config lives in a form in the app itself — no editing Python required.
Personal details are saved to `~/.termin-hunter/config.json` on your own
computer; nothing is sent anywhere except the portal you're booking on
(and Telegram, if you set that up).

## Requirements

- **Google Chrome** installed (the app drives your existing Chrome instead
  of bundling its own browser — keeps things small).
- Windows 10/11: the WebView2 runtime, which is preinstalled on Windows 11
  and most up-to-date Windows 10. If the app window fails to open, install
  it from Microsoft: search "WebView2 Runtime download".
- macOS: nothing extra — the app uses the built-in WebKit view.

## Running it during development

```bash
pip install -r requirements.txt
python app.py
```

## Building a standalone app (no Python needed by the end user)

This is the step that lets you hand someone a single file. Run this
**once**, on your own machine, after testing the app works:

**Windows** (run from the `termin-hunter` folder):
```powershell
pyinstaller --onefile --windowed --add-data "ui;ui" --name TerminHunter app.py
```

**macOS / Linux:**
```bash
pyinstaller --onefile --windowed --add-data "ui:ui" --name TerminHunter app.py
```

The result is a single executable in `dist/` — `TerminHunter.exe` on
Windows, `TerminHunter` on Mac/Linux. That's the file you send people.
They just need Chrome installed; nothing else.

A couple of notes:
- The first PyInstaller run downloads/bundles the Python interpreter and
  all dependencies, so the resulting file is ~40–80MB — normal for this
  approach, and still far smaller than bundling a browser too.
- `--windowed` hides the console window; drop it while testing if you
  want to see raw errors.
- On Mac, unsigned apps trigger a Gatekeeper warning on first launch
  ("can't be opened because it is from an unidentified developer") —
  right-click → Open once to bypass it. Code-signing an app for wider
  distribution requires an Apple Developer account, which is out of scope
  here but worth knowing if you want a fully seamless install experience.

## Using it

1. Open the app, fill in **Service** (the portal URL and exact service
   name you want to watch — copy the service name exactly as it appears
   on the site), **Your details** (used to fill the booking form), and
   optionally **Telegram** (for phone notifications and remote pause/
   resume — leave blank to just use this window).
2. Click **Save settings**, then **Start**.
3. A Chrome window opens and the app starts checking every 60 seconds.
   When it finds a slot, times appear as buttons in the **Run** tab (and
   as a Telegram message, if configured) — click one to book it.
4. Once it reaches the personal-info form, the app pauses itself
   automatically — check the browser and click Submit yourself. It won't
   touch the browser again until you click **Resume**.

## Before you distribute this to other people

- **Selectors are specific to the current Konsentas site markup.** If the
  city updates their booking portal, selectors may need re-verifying —
  same as any scraping tool.
- **This automates competing for scarce public appointment slots faster
  than a human can click.** Whether that's appropriate to share widely
  is worth thinking about — some portals' terms of service prohibit
  automated booking, even for personal use.
- Each person needs their own Telegram bot token (from @BotFather) if
  they want phone notifications — tokens shouldn't be shared between
  people, since whoever holds a token can read/send as that bot.
