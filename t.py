import os
import json
import random
import asyncio
import requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ReportSpamRequest
from aiohttp import ClientSession
from telebot import TeleBot

# ✅ Telegram Bot Token
BOT_TOKEN = "7907570121:AAGi0lKmOEgro8w8sUUJiupz7z0mQr1Xo9M"
bot = TeleBot(BOT_TOKEN)

# ✅ Groups for fetching usernames
GROUPS = [
    "https://t.me/Team_Sonik3",
    "https://t.me/instagramidban"
]

# ✅ Load Proxies
def load_proxies():
    try:
        with open("proxies.txt", "r") as f:
            return [line.strip() for line in f.readlines()]
    except FileNotFoundError:
        return []
proxies = load_proxies()

# ✅ Load Reasons
def load_reasons():
    try:
        with open("message.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    except FileNotFoundError:
        return ["Spam", "Scam", "Abuse"]
reasons = load_reasons()

# ✅ Session File
SESSION_FILE = "session.json"

# ✅ Load Sessions
if os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "r") as f:
        sessions = json.load(f)
else:
    sessions = {}

# ✅ Save Sessions
def save_sessions():
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=4)

# ✅ Report Counter
report_counter = 0  # 🔹 Keeps track of reports sent

# ✅ Fetch Random Username from Groups
async def get_random_user():
    try:
        group = random.choice(GROUPS)
        response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatAdministrators?chat_id={group}")
        data = response.json()
        if "result" in data:
            users = [user["user"]["username"] for user in data["result"] if "username" in user["user"]]
            if users:
                return random.choice(users)
    except Exception as e:
        print(f"[❌] Error fetching username: {e}")
    return "anonymous_user"

# ✅ Telegram API Report
async def report_api(client, target, reason):
    global report_counter
    try:
        await client(ReportSpamRequest(peer=target))
        report_counter += 1
        print(f"[✔] API Report Sent: {target} ({report_counter})")
        if report_counter % 200 == 0:
            bot.send_message(chat_id=message.chat.id, text=f"✅ **200 reports completed!** 🚀")
        return True
    except Exception as e:
        print(f"[❌] API Report Failed: {target} | {e}")
        return False

# ✅ Telegram Web Report
async def report_web(target, reason):
    global report_counter
    async with ClientSession() as session:
        headers = {"User-Agent": "Mozilla/5.0"}
        fake_gmail = f"{random.randint(1000,9999)}@gmail.com"

        data = {
            "user": target,
            "email": fake_gmail,
            "reason": reason
        }

        proxy = random.choice(proxies) if proxies else None
        async with session.post("https://telegram.org/report", headers=headers, data=data, proxy=proxy) as response:
            if response.status == 200:
                report_counter += 1
                print(f"[✔] Web Report Success: {target} ({report_counter})")
                if report_counter % 200 == 0:
                    bot.send_message(chat_id=message.chat.id, text=f"✅ **200 reports completed!** 🚀")
                return True
            else:
                print(f"[❌] Web Report Failed: {target}")
                return False

# ✅ Mass Reporting Function
async def mass_report(target, chat_id):
    random_user = await get_random_user()
    reason = f"{random.choice(reasons)} Report by @{random_user}"

    if target.startswith("https://t.me/c/"):
        print(f"[🚀] Reporting Chat Link: {target}")
    elif target.startswith("@"):
        print(f"[🚀] Reporting User: {target}")
    else:
        print(f"[❌] Invalid Target: {target}")
        return False

    for name, data in sessions.items():
        try:
            client = TelegramClient(StringSession(data["session"]), 6, "eb06d4abfb49dc3eeb1aeb98ae0f581e")
            async with client:
                await report_api(client, target, reason)
            await asyncio.sleep(random.randint(3, 7))
        except Exception as e:
            print(f"[❌] API Error: {e}")

    for _ in range(10):
        await report_web(target, reason)
        await asyncio.sleep(random.randint(1, 3))

    bot.send_message(chat_id, f"✅ Report process completed for {target}")

# ✅ /report Command
@bot.message_handler(commands=['report'])
def start_report(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "⚠️ Usage: /report <@username> or /report <chat link>")
        return

    target = parts[1]
    bot.reply_to(message, f"🚀 Starting mass report on {target}...")
    asyncio.run(mass_report(target, message.chat.id))

# ✅ Start Bot
print("🚀 Bot is running...")
bot.polling()