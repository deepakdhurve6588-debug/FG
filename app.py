from flask import Flask, render_template, request, jsonify
import threading, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)
is_running = False

def send_messages(cookie_str, target_id, message):
    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    driver = webdriver.Chrome(options=chrome_options)

    driver.get("https://www.facebook.com/")
    # Load cookies before going to thread
    for c in cookie_str.split("; "):
        if "=" in c:
            name, value = c.split("=", 1)
            driver.add_cookie({"name": name.strip(), "value": value.strip()})

    driver.get(f"https://www.facebook.com/messages/e2ee/t/{target_id}")
    driver.maximize_window()

    try:
        # Wait for Messenger load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(6)  # wait extra for chat load

        # Try multiple possible selectors (for different Messenger layouts)
        possible_selectors = [
            "//div[@aria-label='Message']",
            "//div[contains(@class, 'notranslate')]",
            "//p[contains(@class,'_1mf')]",
            "//div[contains(@data-lexical-editor,'true')]"
        ]

        box = None
        for sel in possible_selectors:
            try:
                box = driver.find_element(By.XPATH, sel)
                if box:
                    break
            except:
                continue

        if not box:
            print("❌ Message box not found — maybe new E2EE layout.")
            driver.quit()
            return

        box.click()
        time.sleep(1)
        box.send_keys(message)
        box.send_keys(Keys.ENTER)
        print(f"✅ Message sent to thread {target_id}")

    except Exception as e:
        print("❌ Error while sending:", e)
    finally:
        time.sleep(3)
        driver.quit()

def message_thread(cookie, targets, msg):
    global is_running
    for tid in targets:
        if not is_running:
            break
        send_messages(cookie, tid, msg)
        time.sleep(5)

@app.route('/start', methods=['POST'])
def start_bot():
    global is_running
    if is_running:
        return jsonify({"status": "already running"})
    data = request.json
    cookie = data['cookie']
    targets = data['targets'].splitlines()
    message = data['message']
    is_running = True
    threading.Thread(target=message_thread, args=(cookie, targets, message)).start()
    return jsonify({"status": "started"})

@app.route('/stop', methods=['POST'])
def stop_bot():
    global is_running
    is_running = False
    return jsonify({"status": "stopped"})

@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(port=5000)
