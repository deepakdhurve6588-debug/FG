import puppeteer from "puppeteer";
import fs from "fs";

const THREAD_ID = "24330229269965772"; // 🔹 Your E2EE thread ID
const COOKIE_PATH = "./cookies.json";   // 🔹 Your exported Facebook cookies
const MSG_FILE = "./msg.txt";           // 🔹 File with messages (each line = one message)
const DELAY_BETWEEN_MSG = 3000;         // ⏱ 3 seconds between messages

(async () => {
  console.log("🚀 Launching browser...");
  const browser = await puppeteer.launch({
    headless: false,
    defaultViewport: null,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const page = await browser.newPage();

  // Load cookies
  if (fs.existsSync(COOKIE_PATH)) {
    const cookies = JSON.parse(fs.readFileSync(COOKIE_PATH, "utf8"));
    await page.setCookie(...cookies);
    console.log("✅ Cookies loaded successfully!");
  } else {
    console.log("⚠️ No cookies.json file found!");
    return;
  }

  console.log("🌐 Opening Messenger E2EE thread...");
  await page.goto(`https://www.facebook.com/messages/e2ee/t/${THREAD_ID}`, {
    waitUntil: "networkidle2",
    timeout: 120000,
  });

  const messageBoxSelector =
    'div[aria-label="Message"], div[aria-label="Type a message..."], div[contenteditable="true"]';

  console.log("⌛ Waiting for message box...");
  await page.waitForSelector(messageBoxSelector, { visible: true, timeout: 60000 });

  const input = await page.$(messageBoxSelector);
  if (!input) throw new Error("❌ Message input not found!");

  // Read messages from file
  const messages = fs.readFileSync(MSG_FILE, "utf8").split(/\r?\n/).filter(Boolean);
  console.log(`📜 Loaded ${messages.length} messages from msg.txt`);

  for (const message of messages) {
    console.log(`💬 Sending: ${message}`);
    await input.focus();
    await page.keyboard.type(message, { delay: 50 });
    await page.keyboard.press("Enter");
    await new Promise((r) => setTimeout(r, DELAY_BETWEEN_MSG));
  }

  console.log("✅ All messages sent!");
  // await browser.close();
})();
