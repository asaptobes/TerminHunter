# Termin Hunter

A desktop app for watching a Stuttgart Konsentas booking portal (or any
service on it — Ausländerbehörde, driving licence exchange, etc.) and
grabbing an appointment slot the moment one opens up.

## Download

**[Download the latest version](https://github.com/asaptobes/TerminHunter/releases/latest)**
(Windows only for now — a Mac version may follow).

Run the installer, click through it, and you're done. No Python or other
setup needed.

> Windows may show an "Unknown publisher" warning on first run since this
> is small independent software — click "More info" → "Run anyway" to
> continue. This is normal for unsigned indie apps and doesn't mean
> anything is wrong with the file.

Config lives in a form in the app itself — no editing anything by hand.
Personal details are saved to `~/.termin-hunter/config.json` on your own
computer; nothing is sent anywhere except the portal you're booking on
(and Telegram, if you set that up).

## Requirements

- **Google Chrome** installed — the app drives your existing Chrome
  rather than bundling its own browser.
- The WebView2 runtime, which is preinstalled on Windows 11 and most
  up-to-date Windows 10. If the app window fails to open, install it
  from Microsoft: search "WebView2 Runtime download".

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
4. If every field fills successfully, it submits the booking automatically.
   If anything fails to fill (or auto-book is off), it pauses itself
   instead so you can check the browser and finish it by hand — it won't
   touch the browser again until you click **Resume**.

## Code signing policy

Termin Hunter's Windows installer is built automatically from this
repository's source by [GitHub Actions](.github/workflows/build.yml) —
nothing is ever hand-built on a personal machine and uploaded. Windows
code signing for this project is provided for free by
[SignPath.io](https://signpath.io), using a certificate from
[SignPath Foundation](https://signpath.org), for qualifying open-source
projects.

- **Author / reviewer / approver:** [Toby Lee](https://github.com/asaptobes)
  (sole maintainer).
- **Privacy:** Termin Hunter collects no telemetry and phones home to no
  one. The only data it handles is what you type into its own config form
  (stored locally at `~/.termin-hunter/config.json`) and whatever you send
  to the Konsentas booking portal or your own Telegram bot, if you set one
  up.

## Building it yourself / contributing

See [BUILDING.md](BUILDING.md) for the developer setup and build process.
