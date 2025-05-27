import logging
import json
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# ======= CONFIGURATION =======
TELEGRAM_TOKEN = '8189253029:AAGQc5hjp0fiZ7qNCeH2b4jjCXhWAkLSCc0'
HUGGINGFACE_API_KEY = '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'
ADMIN_USER_ID = 7792814115  # Replace with your Telegram user ID
MODEL = "mistralai/Mistral-7B-Instruct-v0.1"
USER_FILE = "users.json"
# =============================

logging.basicConfig(level=logging.INFO)

# Load/save users
def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_users(user_ids):
    with open(USER_FILE, "w") as f:
        json.dump(list(user_ids), f)

user_ids = load_users()

# AI from Hugging Face
def ask_ai(prompt):
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    json_data = {"inputs": prompt, "parameters": {"max_new_tokens": 100}}
    response = requests.post(
        f"https://api-inference.huggingface.co/models/{MODEL}",
        headers=headers,
        json=json_data
    )
    try:
        result = response.json()
        return result[0]["generated_text"]
    except Exception:
        return "Sorry, I couldn't process that."

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_ids.add(user_id)
    save_users(user_ids)
    await update.message.reply_text(
        "Welcome! I'm your AI assistant. Just type your message and I'll reply. "
        "You can also ask me to generate simple code files!"
    )

# Handle all user messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_ids.add(user_id)
    save_users(user_ids)

    user_message = update.message.text
    reply = ask_ai(user_message)
    await update.message.reply_text(reply)

# Admin broadcast command
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if sender_id != ADMIN_USER_ID:
        await update.message.reply_text("Unauthorized.")
        return

    message = ' '.join(context.args)
    if not message:
        await update.message.reply_text("Usage: /broadcast your message here")
        return

    count = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            count += 1
        except Exception:
            continue

    await update.message.reply_text(f"Broadcast sent to {count} users.")

# Main
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()
