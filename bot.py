import os
import logging
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

SYSTEM_PROMPT = """Sen ta'lim sohasidagi aqlli yordamchisan.
Foydalanuvchi qaysi tilda yozsa, shu tilda javob ber.
Savollarni aniq, qisqa va tushunarli tushuntir."""

user_histories = {}

def ask_ai(user_id, message):
    history = user_histories.setdefault(user_id, [])
    history.append({"role": "user", "content": message})
    if len(history) > 20:
        history = history[-20:]
        user_histories[user_id] = history
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": "meta-llama/llama-3.1-8b-instruct:free",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        },
    )
    reply = response.json()["choices"][0]["message"]["content"]
    history.append({"role": "assistant", "content": reply})
    return reply

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salom! Men ta'lim yordamchisiman 🎓\n"
        "Istalgan mavzuda savol bering!\n"
        "O'zbek / Rus / Ingliz tilida yozishingiz mumkin."
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories[update.effective_user.id] = []
    await update.message.reply_text("🗑️ Suhbat tozalandi!")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    reply = ask_ai(update.effective_user.id, update.message.text)
    await update.message.reply_text(reply)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.run_polling()
