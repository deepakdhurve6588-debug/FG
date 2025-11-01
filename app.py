#!/usr/bin/env python3
"""
e2ee_dashboard_full.py

Single-file app:
- Launches chromedriver (auto-download) on port 9515 with --whitelisted-ips=""
- Runs Flask web UI bound to 0.0.0.0:5000
- Accepts cookie header or cookies.json, messages.txt file, thread id
- Start/Stop a Selenium worker that sends messages to a Messenger E2EE thread
"""

import os
import json
import time
import threading
import subprocess
import signal
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

ROOT = Path(__file__).parent.resolve()
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# -- Globals
CHROMEDRIVER_PORT = 9515
CHROMEDRIVER_PROC = None
WORKER_THREAD = None
WORKER_STOP = threading.Event()
WORKER_STATUS = {"running": False, "sent": 0, "last": "", "error": ""}

app = Flask(__name__)

# ----------------- Helper: start chromedriver as subprocess -----------------
def start_chromedriver(port=CHROMEDRIVER_PORT, whitelisted_ips=""):
    global CHROMEDRIVER_PROC
    # get chromedriver binary path using webdriver_manager
    driver_path = ChromeDriverManager().install()
    cmd = [driver_path, f"--port={port}"]
    # allow remote connections if explicitly requested (empty whitelist allows all)
    if whitelisted_ips is not None:
        cmd.append(f"--whitelisted-ips={whitelisted_ips}")
    print("Starting chromedriver:", " ".join(cmd))
    # Start the chromedriver subprocess and stream logs to file
    logf = open(UPLOAD_DIR / "chromedriver.log", "ab")
    CHROMEDRIVER_PROC = subprocess.Popen(cmd, stdout=logf, stderr=logf, close_fds=True)
    # wait briefly for it to start
    for i in range(12):
        time.sleep(0.5)
        if CHROMEDRIVER_PROC.poll() is None:
            # process alive; assume listening soon
            break
    print("Chromedriver process started (pid={})".format(CHROMEDRIVER_PROC.pid))
    return CHROMEDRIVER_PROC

def stop_chromedriver():
    global CHROMEDRIVER_PROC
    if CHROMEDRIVER_PROC:
        try:
            CHROMEDRIVER_PROC.send_signal(signal.SIGINT)
            time.sleep(0.5)
            CHROMEDRIVER_PROC.kill()
        except Exception:
            pass
        CHROMEDRIVER_PROC = None

# ----------------- Selenium util: create remote webdriver -----------------
def create_remote_chrome(port=CHROMEDRIVER_PORT, headless=False) -> WebDriver:
    chrome_opts = Options()
    chrome_opts.add_argument("--disable-notifications")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--disable-extensions")
    chrome_opts.add_argument("--disable-gpu")
    # headless optional
    if headless:
        chrome_opts.add_argument("--headless=new")
        chrome_opts.add_argument("--window-size=1200,900")

    # connect to chromedriver's remote endpoint
    remote_url = f"http://127.0.0.1:{port}"
    # selenium's Remote webdriver can accept options via "options" param in recent versions
    driver = webdriver.Remote(command_executor=remote_url, options=chrome_opts)
    return driver

# ----------------- Worker: send messages -----------------
def add_cookies_to_driver(driver: WebDriver, cookies):
    # must navigate to facebook domain first to set cookies
    driver.get("https://www.facebook.com")
    time.sleep(1.0)
    for c in cookies:
        try:
            driver.add_cookie({
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".facebook.com"),
                "path": c.get("path", "/"),
            })
        except Exception as e:
            print("Cookie add warning:", c.get("name"), e)

def find_composer_and_send(driver: WebDriver, payload: str):
    # try multiple selectors (E2EE layout variants)
    selectors = [
        ("css", 'div[role="textbox"]'),
        ("css", 'div[contenteditable="true"][role="textbox"]'),
        ("xpath", "//div[@aria-label='Message']"),
        ("xpath", "//div[contains(@data-lexical-editor,'true')]"),
        ("xpath", "//p[contains(@class,'_1mf')]"),
    ]
    el = None
    for kind, sel in selectors:
        try:
            if kind == "css":
                found = driver.find_elements(By.CSS_SELECTOR, sel)
            else:
                found = driver.find_elements(By.XPATH, sel)
            if found:
                el = found[-1]  # pick last/most recent composer
                break
        except Exception:
            continue
    if not el:
        # save screenshot for debugging
        screenshot = UPLOAD_DIR / "err_composer.png"
        driver.save_screenshot(str(screenshot))
        raise RuntimeError(f"Composer not found (screenshot saved to {screenshot})")
    el.click()
    time.sleep(0.2)
    try:
        el.send_keys(payload)
    except Exception:
        # fallback JS insert
        driver.execute_script("arguments[0].innerText = arguments[1];", el, payload)
    time.sleep(0.2)
    el.send_keys(Keys.ENTER)

def worker_loop(cookies, thread_id, messages, delay, headless=False):
    global WORKER_STATUS, WORKER_STOP
    WORKER_STATUS.update({"running": True, "sent": 0, "last": "", "error": ""})
    try:
        driver = create_remote_chrome(headless=headless)
    except Exception as e:
        WORKER_STATUS["error"] = f"Could not start remote driver: {e}"
        WORKER_STATUS["running"] = False
        return

    try:
        add_cookies_to_driver(driver, cookies)
        WORKER_STATUS["last"] = "opening thread"
        driver.get(f"https://www.facebook.com/messages/e2ee/t/{thread_id}")
        driver.maximize_window()
        # wait for full page load
        time.sleep(5.0)

        for idx, msg in enumerate(messages):
            if WORKER_STOP.is_set():
                WORKER_STATUS["last"] = "stopped"
                break
            try:
                WORKER_STATUS["last"] = f"sending #{idx+1}"
                find_composer_and_send(driver, msg)
                WORKER_STATUS["sent"] += 1
            except Exception as e:
                WORKER_STATUS["error"] = str(e)
                break
            # delay with responsiveness to stop event
            for _ in range(int(delay*10)):
                if WORKER_STOP.is_set():
                    break
                time.sleep(0.1)

        WORKER_STATUS["last"] = "finished"
    except Exception as e:
        WORKER_STATUS["error"] = str(e)
    finally:
        try:
            driver.quit()
        except:
            pass
        WORKER_STATUS["running"] = False
        WORKER_STOP.clear()

# ----------------- Flask endpoints & UI -----------------
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Messenger E2EE Dashboard — Full</title>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <style>
    body{font-family:Inter,system-ui,Arial;background:linear-gradient(135deg,#0f172a,#0ea5e9);color:#ecfeff;padding:18px}
    .card{background:linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.02));border-radius:12px;padding:16px;margin:10px 0;box-shadow:0 6px 18px rgba(2,6,23,0.6)}
    textarea,input{width:100%;padding:10px;border-radius:8px;border:1px solid rgba(255,255,255,0.06);background:transparent;color:#fff}
    label{font-weight:600}
    .row{display:flex;gap:12px}
    .col{flex:1}
    button{background:#fff;color:#0b1220;padding:10px 14px;border-radius:10px;border:none;font-weight:700;cursor:pointer}
    .muted{opacity:0.8;font-size:13px}
    pre{white-space:pre-wrap;word-break:break-word;background:rgba(0,0,0,0.25);padding:10px;border-radius:8px;color:#fff}
  </style>
</head>
<body>
  <h2>💬 Messenger E2EE Panel — Full</h2>

  <div class="card">
    <label>Cookie header (paste)</label>
    <textarea id="cookie" rows="3" placeholder="fr=...; xs=...; c_user=...;"></textarea>
    <div class="row" style="margin-top:8px">
      <div class="col">
        <label>Targets (thread ids, one per line)</label>
        <textarea id="targets" rows="3" placeholder="100012345678901"></textarea>
      </div>
      <div class="col">
        <label>Delay (seconds) between messages</label>
        <input id="delay" type="number" value="2" step="0.5"/>
      </div>
    </div>

    <div style="margin-top:10px">
      <label>Message or use .txt upload</label>
      <textarea id="message" rows="3" placeholder="Your message here"></textarea>
      <div style="margin-top:8px">
        <input id="msgfile" type="file" accept=".txt"/>
        <button onclick="uploadMessageFile()">Load .txt</button>
      </div>
    </div>

    <div style="margin-top:12px">
      <button onclick="start()">🚀 Start Worker</button>
      <button onclick="stop()">🛑 Stop Worker</button>
      <button onclick="startChromedriver()">🔧 Start chromedriver</button>
      <button onclick="stopChromedriver()">✋ Stop chromedriver</button>
    </div>
  </div>

  <div class="card">
    <h4>Status</h4>
    <pre id="status">idle</pre>
    <h4>Logs / Debug</h4>
    <pre id="logs">no logs yet</pre>
    <div style="margin-top:8px">
      <a href="/downloads/chromedriver.log" target="_blank" style="color:#a7f3d0">chromedriver.log</a> |
      <a href="/uploads/err_composer.png" target="_blank" style="color:#ffd6a5">err_composer.png</a>
    </div>
  </div>

<script>
async function startChromedriver(){
  const res = await fetch('/chromedriver/start', {method:'POST'});
  const j = await res.json();
  alert(JSON.stringify(j));
}
async function stopChromedriver(){
  const res = await fetch('/chromedriver/stop', {method:'POST'});
  const j = await res.json();
  alert(JSON.stringify(j));
}
async function uploadMessageFile(){
  const f = document.getElementById('msgfile').files[0];
  if(!f){ alert('Select a .txt file first'); return; }
  const data = new FormData(); data.append('file', f);
  const res = await fetch('/upload_message_file', {method:'POST', body: data});
  const j = await res.json();
  if (j.message) document.getElementById('message').value = j.message;
  alert(JSON.stringify(j));
}
async function start(){
  const body = {
    cookie: document.getElementById('cookie').value,
    targets: document.getElementById('targets').value,
    message: document.getElementById('message').value,
    delay: parseFloat(document.getElementById('delay').value || '2'),
    headless: false
  };
  const res = await fetch('/start', {method:'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await res.json();
  alert(JSON.stringify(j));
}
async function stop(){
  const res = await fetch('/stop', {method:'POST'});
  const j = await res.json();
  alert(JSON.stringify(j));
}
async function pollStatus(){
  try {
    const res = await fetch('/status'); const j = await res.json();
    document.getElementById('status').textContent = JSON.stringify(j, null, 2);
  } catch(e){}
  setTimeout(pollStatus, 2000);
}
pollStatus();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/chromedriver/start", methods=["POST"])
def api_start_chromedriver():
    # start chromedriver subprocess if not running
    global CHROMEDRIVER_PROC
    if CHROMEDRIVER_PROC:
        return jsonify({"ok": False, "error": "chromedriver already started", "pid": CHROMEDRIVER_PROC.pid})
    try:
        start_chromedriver(port=CHROMEDRIVER_PORT, whitelisted_ips="")
        return jsonify({"ok": True, "pid": CHROMEDRIVER_PROC.pid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/chromedriver/stop", methods=["POST"])
def api_stop_chromedriver():
    stop_chromedriver()
    return jsonify({"ok": True})

@app.route("/upload_message_file", methods=["POST"])
def api_upload_message_file():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file"}), 400
    data = f.read().decode("utf8", errors="ignore")
    # save
    path = UPLOAD_DIR / "messages.txt"
    path.write_text(data, encoding="utf8")
    return jsonify({"ok": True, "message": data[:200]})

@app.route("/start", methods=["POST"])
def api_start():
    global WORKER_THREAD, WORKER_STATUS, WORKER_STOP
    if WORKER_STATUS.get("running"):
        return jsonify({"ok": False, "error": "worker already running"})
    payload = request.get_json(force=True)
    cookie_header = payload.get("cookie", "").strip()
    if not cookie_header:
        return jsonify({"ok": False, "error": "no cookie provided"})
    targets_raw = payload.get("targets", "").strip()
    if not targets_raw:
        return jsonify({"ok": False, "error": "no targets provided"})
    targets = [t.strip() for t in targets_raw.splitlines() if t.strip()]
    message = payload.get("message", "").strip()
    if not message:
        # try messages.txt
        p = UPLOAD_DIR / "messages.txt"
        if p.exists():
            message = p.read_text(encoding="utf8").strip()
    if not message:
        return jsonify({"ok": False, "error": "no message provided"})
    delay = float(payload.get("delay", 2.0))
    headless = bool(payload.get("headless", False))

    # parse cookie header into list of cookie dicts
    cookies = []
    for part in cookie_header.split(";"):
        if "=" not in part: continue
        name, val = part.split("=", 1)
        cookies.append({"name": name.strip(), "value": val.strip(), "domain": ".facebook.com", "path": "/"})

    # build messages list (single message repeated for each target)
    messages = [message]

    # start worker thread
    WORKER_STOP.clear()
    WORKER_STATUS.update({"running": False, "sent": 0, "last": "", "error": ""})
    WORKER_THREAD = threading.Thread(target=run_worker_thread, args=(cookies, targets, messages, delay, headless), daemon=True)
    WORKER_THREAD.start()
    # small wait to allow status update
    time.sleep(0.2)
    return jsonify({"ok": True, "status": "started"})

def run_worker_thread(cookies, targets, messages, delay, headless):
    global WORKER_STATUS, WORKER_STOP
    # ensure chromedriver is running — if not, attempt to start
    if CHROMEDRIVER_PROC is None:
        try:
            start_chromedriver(port=CHROMEDRIVER_PORT, whitelisted_ips="")
            time.sleep(0.4)
        except Exception as e:
            WORKER_STATUS["error"] = f"Could not start chromedriver: {e}"
            return

    WORKER_STATUS["running"] = True
    try:
        # for each target send each message
        for tid in targets:
            if WORKER_STOP.is_set():
                WORKER_STATUS["last"] = "stopped"
                break
            # if multiple messages you can iterate messages list
            for m in messages:
                if WORKER_STOP.is_set():
                    break
                WORKER_STATUS["last"] = f"sending to {tid}"
                try:
                    worker_loop(cookies, tid, [m], delay, headless=headless)
                except Exception as e:
                    WORKER_STATUS["error"] = str(e)
                    break
                WORKER_STATUS["sent"] += 1
    finally:
        WORKER_STATUS["running"] = False
        WORKER_STOP.clear()

@app.route("/stop", methods=["POST"])
def api_stop():
    WORKER_STOP.set()
    return jsonify({"ok": True, "message": "stop requested"})

@app.route("/status", methods=["GET"])
def api_status():
    return jsonify(WORKER_STATUS)

@app.route("/downloads/chromedriver.log")
def download_log():
    p = UPLOAD_DIR / "chromedriver.log"
    if p.exists():
        return send_file(str(p))
    return jsonify({"ok": False, "error": "no log"}), 404

@app.route("/uploads/<path:name>")
def uploaded_files(name):
    p = UPLOAD_DIR / name
    if p.exists():
        return send_file(str(p))
    return jsonify({"ok": False}), 404

# ----------------- entrypoint -----------------
if __name__ == "__main__":
    # start chromedriver automatically on launch (optional)
    try:
        start_chromedriver(port=CHROMEDRIVER_PORT, whitelisted_ips="")
    except Exception as e:
        print("Warning: chromedriver auto-start failed:", e)
    # run Flask bound to 0.0.0.0 so remote host can reach it
    app.run(host="0.0.0.0", port=5000, debug=True)
