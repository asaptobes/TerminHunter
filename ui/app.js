const FIELDS = [
  "portal_url", "category_text", "service_label_text", "option_name",
  "preferred_start_time", "strict_time_filter", "auto_book",
  "anrede", "vorname", "nachname", "geburtsdatum", "email", "telefon",
  "strasse", "nummer", "plz", "ort", "staatsangehoerigkeit",
  "telegram_token", "telegram_chat_id",
];

function readForm() {
  const config = { details: {} };
  const detailKeys = ["anrede", "vorname", "nachname", "geburtsdatum", "email", "telefon",
                       "strasse", "nummer", "plz", "ort", "staatsangehoerigkeit"];
  FIELDS.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const value = el.type === "checkbox" ? el.checked : el.value;
    if (detailKeys.includes(id)) config.details[id] = value;
    else config[id] = value;
  });
  return config;
}

function writeForm(config) {
  if (!config) return;
  Object.entries(config.details || {}).forEach(([k, v]) => {
    const el = document.getElementById(k);
    if (el) el.value = v ?? "";
  });
  FIELDS.forEach((id) => {
    if (id in (config.details || {})) return;
    const el = document.getElementById(id);
    if (!el || !(id in config)) return;
    if (el.type === "checkbox") el.checked = !!config[id];
    else el.value = config[id] ?? "";
  });
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
}

// Wire up pure-UI behavior (tabs) immediately and independently of the
// pywebview bridge, so the interface is never blocked on Python being ready.
function wireTabs() {
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab))
  );
}

function wireAboutModal() {
  const overlay = document.getElementById("aboutOverlay");
  const open = () => overlay.classList.remove("hidden");
  const close = () => overlay.classList.add("hidden");

  document.getElementById("btnAbout").addEventListener("click", open);
  document.getElementById("btnCloseAbout").addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  overlay.querySelectorAll("a[data-url]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.open_external(link.dataset.url);
      }
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    wireTabs();
    wireAboutModal();
  });
} else {
  wireTabs();
  wireAboutModal();
}

// pywebview injects window.pywebview.api asynchronously; it's not
// guaranteed to exist yet at DOMContentLoaded. Wait for the official
// 'pywebviewready' event, but also poll as a fallback in case that event
// fired before this script attached its listener.
function whenApiReady(callback) {
  if (window.pywebview && window.pywebview.api) {
    callback();
    return;
  }
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    callback();
  };
  window.addEventListener("pywebviewready", finish, { once: true });
  const poll = setInterval(() => {
    if (window.pywebview && window.pywebview.api) {
      clearInterval(poll);
      finish();
    }
  }, 100);
}

function formatTimestamp(isoTime) {
  if (!isoTime) return "";
  const match = isoTime.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : isoTime;
}

function appendLog(text, highlight, isoTime) {
  const log = document.getElementById("log");
  const line = document.createElement("div");
  line.className = "line" + (highlight ? " hl" : "");
  const ts = formatTimestamp(isoTime);
  if (ts) {
    const tsSpan = document.createElement("span");
    tsSpan.className = "ts";
    tsSpan.textContent = ts;
    line.appendChild(tsSpan);
  }
  line.appendChild(document.createTextNode(text));
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function setStatus(state, label) {
  const dot = document.getElementById("statusDot");
  dot.className = "dot" + (state ? ` ${state}` : "");
  document.getElementById("statusLabel").textContent = label;
}

function showTimes(times) {
  const box = document.getElementById("timesBox");
  const list = document.getElementById("timesList");
  list.innerHTML = "";
  times.forEach((t) => {
    const btn = document.createElement("button");
    btn.textContent = t;
    btn.onclick = () => window.pywebview.api.select_time(t);
    list.appendChild(btn);
  });
  box.classList.remove("hidden");
  setStatus("alert", `Appointment found — ${times.length} time(s)`);
}

function hideTimes() {
  document.getElementById("timesBox").classList.add("hidden");
}

async function pollEvents() {
  const events = await window.pywebview.api.poll_events();
  events.forEach((e) => {
    switch (e.kind) {
      case "log":
        appendLog(e.message, false, e.time);
        break;
      case "times_found":
        if (document.getElementById("auto_book").checked) {
          appendLog(`Times found: ${e.times.join(", ")} — auto-booking...`, true, e.time);
        } else {
          appendLog(`Times found: ${e.times.join(", ")}`, true, e.time);
          showTimes(e.times);
        }
        break;
      case "times_found_unreadable":
        appendLog("Appointment found but times were unreadable — check the browser.", true, e.time);
        break;
      case "paused":
        appendLog(`Paused. ${e.reason || ""}`.trim(), true, e.time);
        setStatus("", "Paused");
        document.getElementById("btnPause").disabled = true;
        document.getElementById("btnResume").disabled = false;
        break;
      case "resumed":
        appendLog("Resumed searching.", false, e.time);
        hideTimes();
        setStatus("running", "Searching...");
        document.getElementById("btnPause").disabled = false;
        document.getElementById("btnResume").disabled = true;
        break;
      case "form_filled":
        appendLog(
          `Form filled for ${e.appointment_time}.` + (e.failed.length ? ` Needs manual entry: ${e.failed.join(", ")}` : "") + " Check the browser and submit.",
          true,
          e.time
        );
        hideTimes();
        break;
      case "booked":
        appendLog(`✅ Booked! Appointment confirmed for ${e.appointment_time}.`, true, e.time);
        hideTimes();
        break;
      case "stats": {
        const bar = document.getElementById("statsBar");
        bar.textContent = `Searches: ${e.total_searches} · Consecutive errors: ${e.consecutive_errors}/3`;
        bar.classList.toggle("warn", e.consecutive_errors > 0);
        break;
      }
    }
  });
}

whenApiReady(async () => {
  const config = await window.pywebview.api.get_config();
  writeForm(config);

  document.getElementById("btnSave").onclick = async () => {
    await window.pywebview.api.save_config(readForm());
    const hint = document.getElementById("saveHint");
    hint.textContent = "Saved.";
    setTimeout(() => (hint.textContent = ""), 1500);
  };

  document.getElementById("btnStart").onclick = async () => {
    const result = await window.pywebview.api.start(readForm());
    if (result.ok) {
      setStatus("running", "Searching...");
      document.getElementById("btnStart").disabled = true;
      document.getElementById("btnPause").disabled = false;
      appendLog("Started.");
      switchTab("run");
    } else {
      appendLog(`Could not start: ${result.error}`);
    }
  };

  document.getElementById("btnPause").onclick = async () => {
    await window.pywebview.api.pause();
  };
  document.getElementById("btnResume").onclick = async () => {
    await window.pywebview.api.resume();
  };
  document.getElementById("btnSkip").onclick = async () => {
    await window.pywebview.api.skip();
    hideTimes();
  };

  setInterval(pollEvents, 1000);
});