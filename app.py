from flask import Flask, render_template, request, jsonify
import threading, json, time, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

is_running = False

def send_messages(cookie_str, target_id, message):
    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    driver = webdriver.Chrome(options=chrome_options)
    driver.get("https://www.facebook.com/messages/e2ee/t/" + target_id)

    cookies = cookie_str.split("; ")
    for c in cookies:
        if "=" in c:
            name, value = c.split("=", 1)
            driver.add_cookie({"name": name, "value": value})

    driver.refresh()
    time.sleep(5)
    try:
        box = driver.find_element("xpath", "//div[@aria-label='Message']")
        box.send_keys(message)
        box.send_keys(u'\ue007')
    except Exception as e:
        print("❌ Error:", e)
    driver.quit()

def message_thread(cookie, targets, msg):
    global is_running
    for tid in targets:
        if not is_running:
            break
        send_messages(cookie, tid, msg)
        time.sleep(5)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_message():
    file = request.files['file']
    if file and file.filename.endswith('.txt'):
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        return jsonify({"message": content})
    return jsonify({"error": "Invalid file"}), 400

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
