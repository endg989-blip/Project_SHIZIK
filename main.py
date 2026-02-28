
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import ReplyKeyboardMarkup
from telegram.ext import MessageHandler, filters
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler
from dotenv import load_dotenv
import os
import psycopg2


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN = os.getenv("BOT_TOKEN")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        telegram_id BIGINT UNIQUE,
        username TEXT,
        first_name TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id SERIAL PRIMARY KEY,
        telegram_id BIGINT,
        text TEXT
    );
    """)

    conn.commit()
    cursor.close()
    conn.close()

def save_note(telegram_id, text):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO notes (telegram_id, text) VALUES (%s, %s)",
        (telegram_id, text)
    )

    conn.commit()
    cursor.close()
    conn.close()


def get_menu():
    keyboard = [
        ["➕ Добавить заметку"],
        ["📋 Мои заметки"],
        ["❌ Удалить заметки"]
    ]
    return ReplyKeyboardMarkup(keyboard,resize_keyboard=True)



def save_user(telegram_id, username, first_name):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO users (telegram_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO NOTHING
        """,
        (telegram_id, username, first_name)
    )

    conn.commit()
    cursor.close()
    conn.close()



def get_users():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT telegram_id, username, first_name FROM users")
    users = cursor.fetchall()

    cursor.close()
    conn.close()

    return users



def delete_notes_bulk(telegram_id, ids):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM notes WHERE telegram_id = %s AND id = ANY(%s)",
        (telegram_id, ids)
    )

    deleted = cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()

    return deleted



def parse_ids(text):
    ids = set()

    parts = text.split(",")

    for part in parts:
        part = part.strip()

        if "-" in part:
            start, end = part.split("-")
            start = int(start)
            end = int(end)

            for i in range(start, end + 1):
                ids.add(i)
        else:
            ids.add(int(part))

    return list(ids)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    save_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    await update.message.reply_text(
    "Добро пожаловать 🚀",
    reply_markup=get_menu()
)



async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_users()

    if not users:
        text = "Пользователей нет"
    else:
        text = "Список пользователей:\n\n"
        for user in users:
            text += f"ID: {user[0]}, Username: {user[1]}, Name: {user[2]}\n"

    await update.message.reply_text(text)

def get_notes(telegram_id):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, text FROM notes WHERE telegram_id = %s ORDER BY id ASC",
        (telegram_id,)
    )

    notes = cursor.fetchall()

    cursor.close()
    conn.close()

    return notes

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    # если пользователь нажал меню — сбрасываем ожидания
    if text in ["➕ Добавить заметку", "📋 Мои заметки", "❌ Удалить заметки"]:
        context.user_data["waiting_note"] = False
        context.user_data["waiting_delete"] = False

    # ===== ДОБАВИТЬ ЗАМЕТКУ =====
    if text == "➕ Добавить заметку":
        context.user_data["waiting_note"] = True
        await update.message.reply_text("Ок, пришли текст заметки ✍️")
        return

    # ===== НАЖАЛИ УДАЛЕНИЕ =====
    if text == "❌ Удалить заметки":
        context.user_data["waiting_delete"] = True

        await update.message.reply_text(
            "Пришли номера заметок для удаления\n"
            "Пример: 1,2,5-7"
        )
        return

    # ===== СОХРАНЕНИЕ ЗАМЕТКИ =====
    if context.user_data.get("waiting_note"):
        save_note(user_id, text)
        context.user_data["waiting_note"] = False
        await update.message.reply_text("Заметка сохранена ✅")
        return

    # ===== УДАЛЕНИЕ ЗАМЕТОК =====
    if context.user_data.get("waiting_delete"):
        try:
            numbers = parse_ids(text)
        except Exception:
            await update.message.reply_text("Неверный формат. Пример: 1,2,5-7")
            return

        note_map = context.user_data.get("note_map", {})
        ids_to_delete = []

        for num in numbers:
            if num in note_map:
                ids_to_delete.append(note_map[num])

        if not ids_to_delete:
            await update.message.reply_text("Нет таких номеров в списке")
            return

        deleted = delete_notes_bulk(user_id, ids_to_delete)

        context.user_data["waiting_delete"] = False

        await update.message.reply_text(f"Удалено заметок: {deleted} ✅")
        return

    # ===== ПОКАЗАТЬ ЗАМЕТОК =====
    if text == "📋 Мои заметки":
        notes = get_notes(user_id)

        if not notes:
            await update.message.reply_text("Заметок нет")
        else:
            for i, (note_id, note_text) in enumerate(notes, start=1):
                preview = note_text if len(note_text) <= 60 else note_text[:60] + "…"

                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Удалить", callback_data=f"confirm_{note_id}")]
                ])

                await update.message.reply_text(
                    f"{i}. {preview}",
                    reply_markup=keyboard
                )
        return

    # ===== ФОЛБЭК =====
    await update.message.reply_text(
        "Я тебя понял, но пока это не команда из меню 🙂"
    )

# ===== INLINE КНОПКИ =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    # ===== ШАГ 1: ПОДТВЕРЖДЕНИЕ =====
    if data.startswith("confirm_"):
        note_id = int(data.split("_")[1])

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data=f"delete_{note_id}"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel_delete")
            ]
        ])

        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    # ===== ШАГ 2: УДАЛЕНИЕ =====
    if data.startswith("delete_"):
        note_id = int(data.split("_")[1])

        deleted = delete_notes_bulk(user_id, [note_id])

        if deleted:
            await query.edit_message_text("Заметка удалена ✅")
        else:
            await query.edit_message_text("Ошибка удаления")

        return

    # ===== ОТМЕНА =====
    if data == "cancel_delete":
        await query.edit_message_text("Удаление отменено 👍")
        return



def main():
    init_db()   # ← ВОТ ЭТА СТРОКА НОВАЯ

    app = ApplicationBuilder().token(TOKEN).build()
    
    app.bot.delete_webhook(drop_pending_updates=True)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Я работаю!)")

    app.run_polling()



if __name__ == "__main__":
    main()
