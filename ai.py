import logging
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import os
import json

# Replace with your tokens
TELEGRAM_TOKEN = '8189253029:AAGQc5hjp0fiZ7qNCeH2b4jjCXhWAkLSCc0'
OPENAI_API_KEY = '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'
ADMIN_USER_ID = 7792814115  # Replace with your Telegram user ID

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

USER_FILE = "users.json"

def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_users(user_ids):
    with open(USER_FILE, "w") as f:
        json.dump(list(user_ids), f)

# Track user IDs
user_ids = load_users()

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_ids.add(user_id)
    save_users(user_ids)

    user_message = update.message.text
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": user_message}]
        )
        reply = response.choices[0].message['content']
    except Exception as e:
        reply = f"Error: {e}"

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

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_reply))
    app.add_handler(CommandHandler("broadcast", broadcast))

    print("Bot is running...")
    app.run_polling()
