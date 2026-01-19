import os
import time
import logging
import asyncio
from threading import Thread
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatJoinRequestHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.constants import ParseMode, ChatType
from pymongo import MongoClient
from dotenv import load_dotenv
from fluent.runtime import FluentBundle, FluentResource
from flask import Flask, request

# Load environment variables
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNERS = [int(i) for i in os.getenv("OWNERS", "").split()]
MONGO_URL = os.getenv("MONGO_URL")
PORT = int(os.getenv("PORT", 8080))

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database Setup
client = MongoClient(MONGO_URL)
db = client.get_database()
users_col = db["BOTUSERS"]
settings_col = db["CHAT_SETTINGS"]

# Global Stats
START_TIME = time.time()
TOTAL_USERS_SEEN = 0

# i18n Setup
bundles = {}
def load_locales():
    locales_dir = "locales"
    if not os.path.exists(locales_dir):
        return
    for file in os.listdir(locales_dir):
        if file.endswith(".ftl"):
            lang = file.split(".")[0]
            with open(os.path.join(locales_dir, file), "r", encoding="utf-8") as f:
                resource = FluentResource(f.read())
                bundle = FluentBundle([lang])
                bundle.add_resource(resource)
                bundles[lang] = bundle

load_locales()

def t(lang: str, key: str, **kwargs) -> str:
    bundle = bundles.get(lang, bundles.get("en"))
    if not bundle:
        return key
    msg = bundle.get_message(key)
    if not msg or not msg.value:
        if lang != "en":
            return t("en", key, **kwargs)
        return key
    pattern = bundle.format_pattern(msg.value, kwargs)
    return pattern[0]

# Database Helpers
async def add_user(user_id: int):
    if not users_col.find_one({"userID": user_id}):
        users_col.insert_one({"userID": user_id})

async def get_chat_settings(chat_id: int):
    return settings_col.find_one({"chatID": chat_id})

async def set_chat_status(chat_id: int, status: bool):
    settings_col.update_one(
        {"chatID": chat_id},
        {"$set": {"status": status, "welcome": ""}},
        upsert=True
    )

async def set_chat_welcome(chat_id: int, welcome: str):
    settings_col.update_one(
        {"chatID": chat_id},
        {"$set": {"welcome": welcome}},
        upsert=True
    )

# Bot Logic
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type != ChatType.PRIVATE:
        if context.args and context.args[0] == "by_BotzHub":
            await update.message.reply_text(
                "Continue setting me up in PM!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Continue", url=f"https://t.me/{context.bot.username}")
                ]])
            )
        return

    lang = context.user_data.get("lang", "en")
    text = t(lang, "start-msg", user=user.first_name)
    
    keyboard = [
        [
            InlineKeyboardButton(t(lang, "usage-help"), callback_data="helper"),
            InlineKeyboardButton("Language 🌐", callback_data="setLang")
        ],
        [InlineKeyboardButton(t(lang, "updates"), url="https://t.me/BotzHub")]
    ]
    
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await add_user(user.id)

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "en")
    
    text = t(lang, "help") + "\n\nTo approve members who are already in waiting list, upgrade to premium! Contact @xditya_bot for information on pricing."
    
    keyboard = [
        [
            InlineKeyboardButton("Add me to your channel", callback_data="add_to_channel"),
            InlineKeyboardButton("Add me to your group", callback_data="add_to_group")
        ],
        [InlineKeyboardButton("Main Menu 📭", callback_data="mainMenu")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_to_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "en")
    target = query.data.split("_")[-1]
    
    text = "You can add me to channels as well as groups. Use the buttons below!"
    keyboard = [
        [InlineKeyboardButton(f"Add to {target}", url=f"https://t.me/{context.bot.username}?start{target}=by_BotzHub&admin=invite_users+manage_chat")],
        [InlineKeyboardButton("✅ Done", callback_data=f"select_{target}")],
        [InlineKeyboardButton("« Back", callback_data="mainMenu")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.split("_")[-1]
    
    text = f"Select the {target} you just added the bot to, so as to configure settings of the {target}.\n\nSettings include:\n- Custom Welcome\n- Auto Approve\n- Auto Disapprove"
    
    from telegram import KeyboardButtonRequestChat
    
    request_button = KeyboardButton(
        text=f"Select the {target}",
        request_chat=KeyboardButtonRequestChat(
            request_id=1,
            chat_is_channel=(target == "channel"),
            bot_is_member=True,
            bot_administrator_rights={
                "can_invite_users": True,
                "can_manage_chat": True,
            },
            user_administrator_rights={
                "can_invite_users": True,
                "can_manage_chat": True,
            }
        )
    )
    
    keyboard = [[request_button]]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    await query.delete_message()

async def chat_shared_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_shared.chat_id
    user_id = update.effective_user.id
    await settings_handler(update, context, chat_id, user_id)

async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    lang = context.user_data.get("lang", "en")
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ["administrator", "creator"]:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=t(lang, "not-admin"))
            return
    except Exception:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=t(lang, "no-perms"))
        return

    chat_info = await context.bot.get_chat(chat_id)
    settings = await get_chat_settings(chat_id)
    autoappr = settings.get("status", True) if settings else True
    
    text = t(lang, "chat-settings", title=chat_info.title, autoappr=str(autoappr))
    keyboard = [
        [InlineKeyboardButton(t(lang, "btn-approve"), callback_data=f"approve_{chat_id}")],
        [InlineKeyboardButton(t(lang, "btn-disapprove"), callback_data=f"decline_{chat_id}")],
        [InlineKeyboardButton(t(lang, "btn-custom"), callback_data=f"welcome_{chat_id}")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    temp = await context.bot.send_message(chat_id=update.effective_chat.id, text="Removing keyboard...", reply_markup=ReplyKeyboardRemove())
    await temp.delete()

async def callback_settings_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split("_")[-1])
    lang = context.user_data.get("lang", "en")
    
    chat_info = await context.bot.get_chat(chat_id)
    settings = await get_chat_settings(chat_id)
    autoappr = settings.get("status", True) if settings else True
    
    text = t(lang, "chat-settings", title=chat_info.title, autoappr=str(autoappr))
    keyboard = [
        [InlineKeyboardButton(t(lang, "btn-approve"), callback_data=f"approve_{chat_id}")],
        [InlineKeyboardButton(t(lang, "btn-disapprove"), callback_data=f"decline_{chat_id}")],
        [InlineKeyboardButton(t(lang, "btn-custom"), callback_data=f"welcome_{chat_id}")]
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def approve_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    chat_id = int(data[1])
    lang = context.user_data.get("lang", "en")
    
    status = (action == "approve")
    await set_chat_status(chat_id, status)
    
    chat_info = await context.bot.get_chat(chat_id)
    key = "chat-settings-approved" if status else "chat-settings-disapproved"
    text = t(lang, key, title=chat_info.title)
    
    keyboard = [[InlineKeyboardButton("« Back", callback_data=f"settings_page_{chat_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

WELCOME_MSG = 1
async def welcome_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.data.split("_")[-1]
    context.user_data["target_chat_id"] = chat_id
    lang = context.user_data.get("lang", "en")
    
    await query.edit_message_text(t(lang, "welcome-text"))
    return WELCOME_MSG

async def set_welcome_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = int(context.user_data.get("target_chat_id"))
    text = update.message.text
    lang = context.user_data.get("lang", "en")
    
    if not text:
        keyboard = [[InlineKeyboardButton("« Back", callback_data=f"settings_page_{chat_id}")]]
        await update.message.reply_text(t(lang, "provide-msg"), reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    await set_chat_welcome(chat_id, text)
    keyboard = [[InlineKeyboardButton("Back", callback_data=f"settings_page_{chat_id}")]]
    await update.message.reply_text(t(lang, "welcome-set", msg=text), reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "en")
    
    keyboard = []
    row = []
    for l in bundles.keys():
        btn_text = l
        if l == lang:
            btn_text += " ✅"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"lang_{l}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("« Back", callback_data="mainMenu")])
    
    await query.edit_message_text("Please select the language you want to use:", reply_markup=InlineKeyboardMarkup(keyboard))

async def lang_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_lang = query.data.split("_")[1]
    context.user_data["lang"] = new_lang
    
    keyboard = []
    row = []
    for l in bundles.keys():
        btn_text = l
        if l == new_lang:
            btn_text += " ✅"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"lang_{l}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("« Back", callback_data="mainMenu")])
    
    await query.edit_message_text(f"Language set to {new_lang}\n\nUse the buttons to change it again!", reply_markup=InlineKeyboardMarkup(keyboard))

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = context.user_data.get("lang", "en")
    
    text = t(lang, "start-msg", user=user.first_name)
    keyboard = [
        [
            InlineKeyboardButton(t(lang, "usage-help"), callback_data="helper"),
            InlineKeyboardButton("Language 🌐", callback_data="setLang")
        ],
        [InlineKeyboardButton(t(lang, "updates"), url="https://t.me/BotzHub")]
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TOTAL_USERS_SEEN
    request = update.chat_join_request
    chat_id = request.chat.id
    user_id = request.from_user.id
    
    settings = await get_chat_settings(chat_id)
    approve = True
    welcome_template = "Hey {name}, your request to join {chat} has been approved!"
    
    if settings:
        approve = settings.get("status", True)
        welcome_template = settings.get("welcome") or (
            "Hey {name}, your request to join {chat} has been approved!" if approve 
            else "Hey {name}, your request to join {chat} has been declined!"
        )
    else:
        if not approve:
            welcome_template = "Hey {name}, your request to join {chat} has been declined!"

    TOTAL_USERS_SEEN += 1
    
    try:
        if approve:
            await context.bot.approve_chat_join_request(chat_id, user_id)
        else:
            await context.bot.decline_chat_join_request(chat_id, user_id)
    except Exception as e:
        logger.error(f"Error handling join request: {e}")
        return

    welcome_msg = welcome_template.replace("{name}", request.from_user.first_name).replace("{chat}", request.chat.title)
    welcome_msg = welcome_msg.replace("$name", request.from_user.first_name).replace("$chat", request.chat.title)
    welcome_msg += "\n\nSend /start to know more!"
    
    try:
        await context.bot.send_message(chat_id=user_id, text=welcome_msg)
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNERS:
        return
    
    msg = await update.message.reply_text("Calculating...")
    uptime_sec = time.time() - START_TIME
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    
    uptime = ""
    if days > 0: uptime += f"{int(days)}d "
    if hours > 0: uptime += f"{int(hours)}h "
    if minutes > 0: uptime += f"{int(minutes)}m "
    uptime += f"{int(seconds)}s."
    
    total_users = users_col.count_documents({})
    chats_modified = settings_col.count_documents({})
    
    bot_info = await context.bot.get_me()
    text = (
        f"<b>Stats for @{bot_info.username}</b>\n\n"
        f"<b>Total users</b>: {total_users}\n"
        f"<b>Chats with modified settings</b>: {chats_modified}\n"
        f"<b>Total Users Seen (Approved/Disapproved)</b>: {TOTAL_USERS_SEEN}\n"
        f"<b>Uptime</b>: {uptime}\n\n"
        f"<b><a href='https://github.com/xditya/ChannelActionsBot'>Repository</a> | <a href='https://t.me/BotzHub'>Channel</a> | <a href='https://t.me/BotzHubChat'>Support</a></b>"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNERS:
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("Please reply to a message to broadcast.")
        return
    
    msg = await update.message.reply_text("Please wait, in progress...")
    total_users = users_col.count_documents({})
    done = 0
    blocked = 0
    
    for user_doc in users_col.find():
        user_id = user_doc["userID"]
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.reply_to_message.message_id,
                reply_markup=update.message.reply_to_message.reply_markup
            )
            done += 1
        except Exception as e:
            blocked += 1
        
        if done % 100 == 0:
            try:
                await msg.edit_text(f"Broadcast done to {done}/{total_users} users, of which {blocked} blocked the bot.\n\nStill in progress...")
            except: pass
            
    await msg.edit_text(
        f"Broadcast completed.\n\n"
        f"Total users: {total_users}\n"
        f"Sent to: {done}\n"
        f"Blocked: {blocked}\n"
        f"Failed for unknown reason: {total_users - done - blocked}"
    )

app = Flask(__name__)
@app.route('/')
def hello():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

async def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    
    application.add_handler(CallbackQueryHandler(help_handler, pattern="^helper$"))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^mainMenu$"))
    application.add_handler(CallbackQueryHandler(add_to_handler, pattern="^add_to_"))
    application.add_handler(CallbackQueryHandler(select_handler, pattern="^select_"))
    application.add_handler(CallbackQueryHandler(set_lang_callback, pattern="^setLang$"))
    application.add_handler(CallbackQueryHandler(lang_select_callback, pattern="^lang_"))
    application.add_handler(CallbackQueryHandler(callback_settings_page, pattern="^settings_page_"))
    application.add_handler(CallbackQueryHandler(approve_decline_callback, pattern="^(approve|decline)_"))
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(welcome_callback, pattern="^welcome_")],
        states={
            WELCOME_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_welcome_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )
    application.add_handler(conv_handler)
    
    application.add_handler(MessageHandler(filters.StatusUpdate.CHAT_SHARED, chat_shared_handler))
    application.add_handler(ChatJoinRequestHandler(join_request_handler))
    
    application.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, 
        lambda u, c: settings_handler(u, c, u.message.forward_from_chat.id, u.effective_user.id) 
        if u.message.forward_from_chat and u.message.forward_from_chat.type == ChatType.CHANNEL else None))

    Thread(target=run_flask, daemon=True).start()

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
