"""
Core booking engine for the Stuttgart Konsentas Termin Hunter.

This is the same logic that was in permit.py / license_bot.py, unified into
one config-driven class so the UI can point it at ANY service on the portal
instead of two hardcoded scripts. All the fixes from earlier (real selectors,
pause/resume, adaptive Weiter handling, the "Termin wählen" confirmation
step, name-based form fields) are preserved here.
"""

import asyncio
import logging
import re
from datetime import datetime

from playwright.async_api import async_playwright

logger = logging.getLogger("engine")

# --- Verified against the live site on 2026-07-22 ---
TEXT_FIELD_MAP = {
    "vorname": 'input[name="GF_Vorname"]',
    "nachname": 'input[name="GF_Nachname"]',
    "staatsangehoerigkeit": 'input[name="GF_Staatsangehoerigkeit"]',
    "geburtsdatum": 'input[name="Geburtsdatum"]',
    "email": 'input[name="GF_Email"]',
    "telefon": 'input[name="GF_Telefon"]',
    "strasse": 'input[name="GF_Strasse"]',
    "nummer": 'input[name="GF_Hausnummer"]',
}
SELECT_FIELD_MAP = {
    "anrede": 'select[name="GF_Anrede"]',
    "plz": 'select[name="GF_PLZ"]',
    "ort": 'select[name="GF_Ort"]',
}
DATENSCHUTZ_CHECKBOX = 'input[name="GF_Datenschutz"]'
FIRST_FORM_FIELD_SELECTOR = TEXT_FIELD_MAP["vorname"]
NO_APPOINTMENTS_TEXT = "Keine verfügbaren Termine"
# Candidate pool only — see _get_free_day_locators() for the actual
# positive availability check (presence of a FullCalendar "background
# event" element, confirmed against the live DOM). Weekends are excluded
# here too since the office is never open then, cheap sanity filter.
DAY_CELL_SELECTOR = (
    'td.fc-daygrid-day:not(.termin-booked-out):not(.fc-day-other):not(.fc-day-past):not(.fc-day-today)'
    ':not(.fc-day-sat):not(.fc-day-sun)'
)
TIME_ROW_SELECTOR = 'tr.fc-list-event'
TIME_CELL_SELECTOR = 'tr.fc-list-event td.fc-list-event-time'
CONFIRM_APPOINTMENT_BUTTON = 'button:has-text("Termin wählen")'
WEITER_SELECTOR = 'button:has-text("Weiter"), input[value="Weiter"]'


def _normalize_time(t):
    """'9:00' -> '09:00'. The site doesn't zero-pad single-digit hours, but
    plain string sorting/comparison needs zero-padding to be chronologically
    correct (otherwise '9:00' sorts after '10:00' as text)."""
    try:
        h, m = t.split(":")
        return f"{int(h):02d}:{m}"
    except (ValueError, AttributeError):
        return t


class TerminBot:
    """
    One instance per configured search. `config` is a dict — see
    config.example.json for the shape. `on_event(event: dict)` is called
    for every status change (log lines, times found, paused, error, etc.)
    so the UI (and optionally Telegram) can react.
    """

    def __init__(self, config: dict, on_event):
        self.config = config
        self.on_event = on_event

        self.paused = False
        self.appointment_found = False
        self.booking_in_progress = False
        self.available_times = []
        self.consecutive_errors = 0
        self.total_searches = 0
        self._stop = False

        self.browser_page = None
        self.browser_lock = asyncio.Lock()
        self._playwright = None
        self._browser = None

    # -- event helper ------------------------------------------------------
    def emit(self, kind, **data):
        self.on_event({"kind": kind, "time": datetime.now().isoformat(timespec="seconds"), **data})

    def log(self, message):
        logger.info(message)
        self.emit("log", message=message)

    # -- public controls (safe to call from another thread via the app) ---
    def pause(self):
        self.paused = True
        self.emit("paused")

    def resume(self):
        self.paused = False
        self.appointment_found = False
        self.booking_in_progress = False
        self.available_times = []
        self.emit("resumed")

    def stop(self):
        self._stop = True

    # -- lifecycle -----------------------------------------------------------
    async def run(self):
        async with async_playwright() as p:
            self._playwright = p
            # Use the user's installed Chrome instead of downloading a
            # separate ~300MB Chromium — keeps the packaged app small.
            try:
                self._browser = await p.chromium.launch(channel="chrome", headless=False)
            except Exception as e:
                self.log(
                    "Couldn't find Google Chrome installed on this computer. "
                    "Termin Hunter uses your existing Chrome instead of bundling its own "
                    "browser (keeps the app small) — please install Chrome from "
                    "google.com/chrome and restart the app."
                )
                self.emit("paused", reason="Google Chrome not found")
                raise

            context = await self._browser.new_context()
            self.browser_page = await context.new_page()

            self.log("Bot started. Searching...")
            while not self._stop:
                if self.paused or self.appointment_found or self.booking_in_progress:
                    await asyncio.sleep(5)
                    continue

                async with self.browser_lock:
                    self.total_searches += 1
                    try:
                        try:
                            await self._search_once()
                        except Exception as first_error:
                            # A single failed click is often just a page
                            # that didn't finish loading in time, not a
                            # real problem — a fresh reload usually clears
                            # it. Give it one automatic retry before this
                            # counts toward the consecutive-failure pause.
                            self.log(f"First attempt failed ({first_error}) — reloading and trying once more...")
                            await self.browser_page.reload()
                            await self.browser_page.wait_for_timeout(1500)
                            await self._search_once()
                        self.consecutive_errors = 0
                    except Exception as e:
                        self.consecutive_errors += 1
                        self.log(f"Search error: {e}")
                        if self.consecutive_errors >= 3:
                            self.paused = True
                            self.emit("paused", reason=f"{self.consecutive_errors} failed attempts in a row: {e}")
                    self.emit("stats", total_searches=self.total_searches, consecutive_errors=self.consecutive_errors)

                await asyncio.sleep(60)

            await self._browser.close()

    async def safe_click(self, page_or_locator, selectors, description, timeout=6000, attempts=3):
        if selectors is None:
            selectors = [None]
        elif isinstance(selectors, str):
            selectors = [selectors]
        last_error = None
        for attempt in range(1, attempts + 1):
            for selector in selectors:
                try:
                    locator = page_or_locator.locator(selector).first if selector else page_or_locator.first
                    await locator.wait_for(state="visible", timeout=timeout)
                    await locator.click(timeout=timeout)
                    return True
                except Exception as e:
                    last_error = e
            await self.browser_page.wait_for_timeout(1000)
        try:
            path = f"/tmp/failed_{description.replace(' ', '_')}_{datetime.now():%H%M%S}.png"
            await self.browser_page.screenshot(path=path)
            self.log(f"Could not click '{description}'. Screenshot: {path}")
        except Exception:
            pass
        raise RuntimeError(f"Could not click '{description}': {last_error}")

    async def advance_to_calendar(self, timeout=15000, max_hops=3):
        """
        After selecting a service (+ option, if it has one), the site
        takes one of a few paths:
          (a) auto-advances straight to the calendar,
          (b) shows "Weiter" -> a confirmation page -> "Weiter" -> calendar, or
          (c) the service has NO appointments at all right now and shows
              "Keine verfügbaren Termine!" directly instead of any
              calendar — a normal, expected outcome for a low/no-
              availability service, not an error.
        Returns "calendar" or "no_appointments". Only raises if genuinely
        none of these three things ever show up.
        """
        page = self.browser_page
        calendar_selector = 'td.fc-daygrid-day'
        for hop in range(max_hops):
            calendar = page.locator(calendar_selector).first
            weiter = page.locator(WEITER_SELECTOR).first
            no_appts = page.locator(f"text={NO_APPOINTMENTS_TEXT}").first

            tasks = [
                asyncio.create_task(calendar.wait_for(state="visible", timeout=timeout)),
                asyncio.create_task(weiter.wait_for(state="visible", timeout=timeout)),
                asyncio.create_task(no_appts.wait_for(state="visible", timeout=timeout)),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.exception()

            if await calendar.is_visible():
                return "calendar"
            if await no_appts.is_visible():
                return "no_appointments"
            if await weiter.is_visible():
                await weiter.click(timeout=5000)
                await page.wait_for_timeout(800)
                continue
            break
        raise RuntimeError("Could not reach the calendar after selecting the service — no calendar, 'Weiter', or 'no appointments' message appeared")

    async def wait_for_either(self, selector_a, selector_b, timeout=15000):
        """
        Wait for either of two locators to become visible. Playwright's
        wait_for_selector doesn't support mixing selector engines (e.g. CSS
        + text=) in one comma-separated string, so this races two separate
        waits instead — same pattern as wait_for_form_or_loss.
        Returns "a", "b", or "neither".
        """
        page = self.browser_page
        loc_a = page.locator(selector_a).first
        loc_b = page.locator(selector_b).first
        task_a = asyncio.create_task(loc_a.wait_for(state="visible", timeout=timeout))
        task_b = asyncio.create_task(loc_b.wait_for(state="visible", timeout=timeout))
        done, pending = await asyncio.wait({task_a, task_b}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            t.exception()
        if await loc_a.is_visible():
            return "a"
        if await loc_b.is_visible():
            return "b"
        return "neither"

    async def wait_for_form_or_loss(self, timeout=20000):
        page = self.browser_page
        form_locator = page.locator(FIRST_FORM_FIELD_SELECTOR).first
        lost_locator = page.locator(f"text={NO_APPOINTMENTS_TEXT}").first
        form_task = asyncio.create_task(form_locator.wait_for(state="visible", timeout=timeout))
        lost_task = asyncio.create_task(lost_locator.wait_for(state="visible", timeout=timeout))
        done, pending = await asyncio.wait({form_task, lost_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            t.exception()
        if await lost_locator.is_visible():
            return "lost"
        if await form_locator.is_visible():
            return "form"
        return "unknown"

    async def fill_form(self):
        page = self.browser_page
        details = self.config.get("details", {})
        filled, failed = [], []

        for key, selector in TEXT_FIELD_MAP.items():
            if not details.get(key):
                continue
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=5000)
                # .fill() sets the value directly and fires one generic
                # input event — some fields here (confirmed: Geburtsdatum)
                # have JS input-masking that only reacts to real per-
                # character keystroke events, so .fill() silently leaves
                # them empty. Typing it out works for every field, not
                # just the masked ones, so do it for all of them.
                await locator.click()
                await locator.fill("")
                await locator.press_sequentially(str(details[key]), delay=30)
                filled.append(key)
            except Exception as e:
                failed.append(key)
                self.log(f"Could not fill '{key}': {e}")

        if details.get("email"):
            try:
                locator = page.locator('input[name="GF_Email2"]').first
                await locator.wait_for(state="visible", timeout=5000)
                await locator.click()
                await locator.fill("")
                await locator.press_sequentially(str(details["email"]), delay=30)
                filled.append("email2")
            except Exception as e:
                failed.append("email2")
                self.log(f"Could not fill email confirmation field: {e}")

        for key, selector in SELECT_FIELD_MAP.items():
            if not details.get(key):
                continue
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=5000)
                await locator.select_option(str(details[key]))
                # select_option() silently no-ops if the value doesn't
                # match any <option> — verify it actually took, and if not,
                # report what WAS available so the mismatch is obvious
                # (e.g. a configured PLZ that isn't in this dropdown's
                # specific list of accepted postcodes).
                selected = await locator.evaluate("el => el.value")
                if selected != str(details[key]):
                    available = await locator.evaluate(
                        "el => [...el.options].map(o => o.value).filter(Boolean).join(', ')"
                    )
                    raise ValueError(f"'{details[key]}' isn't one of the available options: {available}")
                filled.append(key)
            except Exception as e:
                failed.append(key)
                self.log(f"Could not select '{key}' ({selector}) = '{details[key]}': {e}")

        try:
            checkbox = page.locator(DATENSCHUTZ_CHECKBOX).first
            checkbox_id = await checkbox.get_attribute("id")
            await checkbox.wait_for(state="attached", timeout=5000)

            if not await checkbox.is_checked() and checkbox_id:
                # The real, genuinely-visible label ("Datenschutz &
                # Nutzungsbedingungen*") has for="<checkbox id>" — clicking
                # a label with a matching `for` natively toggles its
                # associated input, regardless of whether the input itself
                # is visually hidden by the custom checkbox styling.
                label = page.locator(f'label[for="{checkbox_id}"]').first
                if await label.count() > 0:
                    try:
                        await label.click(timeout=5000)
                    except Exception:
                        pass

            if not await checkbox.is_checked():
                try:
                    await checkbox.check(timeout=5000)
                except Exception:
                    pass

            if not await checkbox.is_checked():
                try:
                    # force=True skips visibility/actionability checks, but
                    # still needs a real layout box to click at — won't
                    # help if the input has zero size (e.g. display:none).
                    await checkbox.check(force=True, timeout=5000)
                except Exception:
                    pass

            if not await checkbox.is_checked():
                # Last resort: set the DOM state directly and fire the
                # events a real click would, bypassing the click
                # simulation (and any visibility/layout requirement)
                # entirely.
                await checkbox.evaluate(
                    "el => { el.checked = true; "
                    "el.dispatchEvent(new Event('input', {bubbles: true})); "
                    "el.dispatchEvent(new Event('change', {bubbles: true})); "
                    "el.dispatchEvent(new Event('click', {bubbles: true})); }"
                )

            if not await checkbox.is_checked():
                raise RuntimeError("checkbox still unchecked after every fallback")
            filled.append("datenschutz")
        except Exception as e:
            failed.append("datenschutz")
            self.log(f"Could not check the Datenschutz consent box: {e}")

        return filled, failed

    def _pick_time(self, times):
        """
        Pick which scraped time to go for, honoring preferred_start_time /
        strict_time_filter if configured. Returns None if strict filtering
        is on and nothing meets the preference (caller should skip this
        day rather than book an unwanted time). `times` is assumed already
        sorted chronologically (see _scrape_times).
        """
        preferred = (self.config.get("preferred_start_time") or "").strip()
        if not preferred:
            return times[0]
        preferred_norm = _normalize_time(preferred)
        good = [t for t in times if _normalize_time(t) >= preferred_norm]
        if good:
            return good[0]
        if self.config.get("strict_time_filter"):
            return None
        return times[0]

    async def _scrape_times(self):
        """
        Read times directly from the confirmed real time-slot elements
        (td.fc-list-event-time inside tr.fc-list-event rows) instead of
        regex-matching the whole page's text, which could pick up any
        HH:MM-shaped string that isn't actually a bookable slot. Hours
        aren't zero-padded on this site ("9:00", not "09:00"), so the
        regex allows one or two digits; results are sorted using the
        zero-padded form so early-morning times sort correctly.
        """
        page = self.browser_page
        cells = page.locator(TIME_CELL_SELECTOR)
        count = await cells.count()
        times = []
        for i in range(count):
            text = (await cells.nth(i).text_content() or "").strip()
            if re.match(r'^(?:[01]?\d|2[0-3]):[0-5]\d$', text):
                times.append(text)
        return sorted(set(times), key=_normalize_time)

    async def _get_free_day_locators(self):
        """
        Returns Locators (the clickable day-number links) for cells that
        genuinely have appointments. Confirmed against the live DOM: the
        green highlight isn't styling on the <td> itself at all — it's a
        nested FullCalendar "background event" element
        (div.fc-daygrid-day-bg > .fc-event.fc-bg-event) that only renders
        when the day actually has availability. A booked-out day's
        .fc-daygrid-day-bg is empty; a free day's contains this element
        (in either of the site's two green shades — the shade doesn't
        matter, only whether the element exists at all).
        """
        page = self.browser_page
        cells = page.locator(DAY_CELL_SELECTOR)
        count = await cells.count()
        free = []
        for i in range(count):
            cell = cells.nth(i)
            has_bg_event = await cell.locator('.fc-bg-event').count() > 0
            if has_bg_event:
                free.append(cell.locator('a.fc-daygrid-day-number'))
        return free

    async def _navigate_to_calendar(self):
        cfg = self.config
        page = self.browser_page
        # NOTE: deliberately not clearing cookies here — a plain page
        # reload doesn't either, and this is a session-driven SPA; wiping
        # cookies before every single attempt may have been destabilizing
        # something the wizard relies on between steps.
        await page.goto(cfg["portal_url"], wait_until="domcontentloaded")
        # domcontentloaded fires once the raw HTML is parsed, but this is
        # a JS-heavy single-page app — the actual service list/buttons get
        # attached afterward, via JS. Clicking immediately after
        # domcontentloaded can race ahead of that. Give it a real chance
        # to settle first (best-effort: some sites never go fully network-
        # idle due to background polling/analytics, so this shouldn't be
        # fatal if it times out).
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(500)

        if cfg.get("category_text"):
            await self.safe_click(page, [f'text="{cfg["category_text"]}"'], "category menu")
            await page.wait_for_timeout(1000)

        service_label_text = cfg["service_label_text"]
        if cfg.get("option_name"):
            service_container = page.locator(f'div[data-process-name="{service_label_text}"]')
            await self.safe_click(service_container.locator('button.dropdown-toggle'), None, "service dropdown")
            await page.wait_for_timeout(500)
            await self.safe_click(service_container.locator(f'a[data-option-name="{cfg["option_name"]}"]'), None, "service option")

        service_label = page.locator(f'label[data-process-name="{service_label_text}"]')
        await self.safe_click(service_label, None, "service radio button")

        outcome = await self.advance_to_calendar()
        await page.wait_for_timeout(1000)
        return outcome

    async def _search_once(self):
        page = self.browser_page
        self.log("Checking for new appointments...")

        outcome = await self._navigate_to_calendar()
        if outcome == "no_appointments":
            self.log("No appointment found — trying again in 60 seconds")
            return

        no_appts = page.locator(f"text={NO_APPOINTMENTS_TEXT}").first
        free_days = await self._get_free_day_locators()
        free_day_count = len(free_days)

        if await no_appts.is_visible() or free_day_count == 0:
            self.log("No appointment found — trying again in 60 seconds")
            return

        self.appointment_found = True
        # A day that LOOKS free on the calendar can still turn out to have
        # nothing bookable (e.g. all remaining slots taken since the
        # calendar last refreshed). Try a few candidates instead of giving
        # up on the whole 60-second cycle after the first one.
        max_days_to_try = min(free_day_count, 5)
        self.log(f"Appointment page reached! {free_day_count} free day(s) visible — trying up to {max_days_to_try}...")

        for day_index in range(max_days_to_try):
            if day_index > 0:
                # The page reloads fresh each retry, so re-navigate and
                # re-check how many free days are still showing.
                retry_outcome = await self._navigate_to_calendar()
                if retry_outcome == "no_appointments":
                    # Availability disappeared entirely between retries
                    # (e.g. the last slot was taken while we were trying
                    # other days) — nothing left to try this cycle.
                    break
                free_days = await self._get_free_day_locators()
                if day_index >= len(free_days):
                    break

            day_locator = free_days[day_index]
            day_label = (await day_locator.text_content() or "?").strip()
            await day_locator.click(timeout=5000)
            outcome = await self.wait_for_either(TIME_ROW_SELECTOR, f"text={NO_APPOINTMENTS_TEXT}", timeout=15000)

            if outcome == "b":
                self.log(f"The {day_label}. turned out to have no times, trying the next free day...")
                continue

            scraped_times = await self._scrape_times()

            if scraped_times:
                self.available_times = scraped_times
                self.emit("times_found", times=scraped_times)

                if self.config.get("auto_book"):
                    chosen = self._pick_time(scraped_times)
                    if chosen is None:
                        self.log(
                            f"The {day_label}.: only earlier times than your preferred "
                            f"{self.config.get('preferred_start_time')} were available, and strict "
                            f"filtering is on — skipping this day."
                        )
                        continue
                    self.log(f"Auto-booking {chosen}...")
                    self.booking_in_progress = True
                    await self._book_time(chosen)
                return
            else:
                self.log(f"The {day_label}. showed a time list but nothing readable, trying the next free day...")
                continue

        self.appointment_found = False
        self.log("None of the visible free days actually had bookable times — trying again in 60 seconds.")

    async def select_time(self, exact_time):
        """Called when the user picks a time — from the UI or from Telegram."""
        if exact_time not in self.available_times or not self.appointment_found or self.booking_in_progress:
            return
        self.booking_in_progress = True
        self.log(f"Selecting {exact_time}...")
        async with self.browser_lock:
            await self._book_time(exact_time)

    async def _book_time(self, exact_time):
        """
        Actually clicks through the booking flow for a time. Does NOT
        acquire browser_lock itself — callers must already hold it (either
        select_time() above, or the auto-book path in _search_once, which
        runs inside run()'s own `async with self.browser_lock:`).
        """
        page = self.browser_page
        try:
            await self.safe_click(page, [f'{TIME_ROW_SELECTOR}:has-text("{exact_time}")'], f"time slot {exact_time}", timeout=10000)

            confirm_btn = page.locator(CONFIRM_APPOINTMENT_BUTTON).first
            await confirm_btn.wait_for(state="visible", timeout=15000)
            await confirm_btn.click(timeout=5000)

            outcome = await self.wait_for_form_or_loss()

            if outcome == "lost":
                self.log("Someone else grabbed that slot. Resuming search.")
                self.appointment_found = False
                self.booking_in_progress = False
                self.available_times = []
                await page.goto(self.config["portal_url"])
                return

            self.available_times = []
            self.paused = True  # stop touching the browser no matter what

            if outcome == "unknown":
                self.emit("paused", reason="Time selected but couldn't confirm the form loaded — check the browser.")
                return

            filled, failed = await self.fill_form()

            if failed:
                # Something didn't fill correctly — don't submit a form
                # that might be missing required/incorrect data. Leave it
                # for manual review instead, same as before.
                self.emit("form_filled", filled=filled, failed=failed, appointment_time=exact_time)
                return

            try:
                submit_btn = page.locator(WEITER_SELECTOR).first
                await submit_btn.wait_for(state="visible", timeout=10000)
                await submit_btn.click(timeout=5000)
                await page.wait_for_timeout(2000)  # let the confirmation page render
                self.emit("booked", appointment_time=exact_time)
                self.log(f"✅ Booked {exact_time}! Check the browser / your email to confirm.")
            except Exception as e:
                # The form itself is filled correctly — only the final
                # click failed. Don't treat this as a full failure (which
                # would misleadingly reset state); fall back to the same
                # safe manual-review pause as a filling failure would.
                self.log(f"Form filled, but couldn't click the final submit button: {e}")
                self.emit("form_filled", filled=filled, failed=[], appointment_time=exact_time)

        except Exception as e:
            self.paused = True
            self.appointment_found = False
            self.booking_in_progress = False
            self.available_times = []
            self.emit("paused", reason=f"Error selecting time: {e}")

    async def skip_appointment(self):
        self.appointment_found = False
        self.available_times = []
        async with self.browser_lock:
            await self.browser_page.goto(self.config["portal_url"])
        self.log("Skipped. Resuming search.")