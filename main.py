
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
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            name TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            username TEXT,
            first_name TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            text TEXT,
            category_id INTEGER
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()


def get_menu():
    keyboard = [
        ["➕ Новая заметка"],
        ["📋 Мои заметки"],
        ["📂 Категории"],
        ["🔍 Поиск"],
        ["⏰ Напоминания"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def categories_keyboard(categories):

    keyboard = []

    for cat_id, name in categories:
        keyboard.append([
            InlineKeyboardButton(name, callback_data=f"cat_{cat_id}")
        ])

    keyboard.append([
        InlineKeyboardButton("Без категории", callback_data="cat_none")
    ])

    return InlineKeyboardMarkup(keyboard)

def reset_state(context):
    context.user_data.clear()

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
        """
        SELECT 
            notes.id,
            notes.text,
            categories.name
        FROM notes
        LEFT JOIN categories 
            ON notes.category_id = categories.id
        WHERE notes.telegram_id = %s
        ORDER BY notes.id
        """,
        (telegram_id,)
    )

    notes = cursor.fetchall()

    cursor.close()
    conn.close()

    return notes


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text
    user_id = update.effective_user.id


    # ===== СБРОС СОСТОЯНИЯ ПРИ НАЖАТИИ МЕНЮ =====
    if text in ["➕ Новая заметка", "📋 Мои заметки", "📂 Категории", "🔍 Поиск", "⏰ Напоминания"]:
        reset_state(context)


    # =====================================================
    # ================= СОСТОЯНИЯ =========================
    # =====================================================

    # ===== СОХРАНЕНИЕ КАТЕГОРИИ =====
    if context.user_data.get("waiting_category"):
        add_category(user_id, text)

        context.user_data["waiting_category"] = False

        await update.message.reply_text("Категория добавлена ✅")
        return


    # ===== СОХРАНЕНИЕ ЗАМЕТКИ =====
    if context.user_data.get("waiting_note"):
        category_id = context.user_data.get("selected_category")
        save_note(user_id, text, category_id)

        context.user_data["waiting_note"] = False
        context.user_data["selected_category"] = None

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
            await update.message.reply_text("Нет таких номеров")
            return

        deleted = delete_notes_bulk(user_id, ids_to_delete)

        context.user_data["waiting_delete"] = False

        await update.message.reply_text(f"Удалено заметок: {deleted} ✅")
        return


    # =====================================================
    # ================= КНОПКИ МЕНЮ =======================
    # =====================================================

    # ===== НОВАЯ ЗАМЕТКА =====
    if text == "➕ Новая заметка":

        categories = get_categories(user_id)

        if categories:
            await update.message.reply_text(
                "Выбери категорию:",
                reply_markup=categories_keyboard(categories)
            )
        else:
            context.user_data["waiting_note"] = True
            context.user_data["selected_category"] = None

            await update.message.reply_text(
                "Категорий нет. Отправь текст заметки:"
            )

        return


    # ===== КАТЕГОРИИ =====
    if text == "📂 Категории":

        categories = get_categories(user_id)

        if not categories:
            await update.message.reply_text(
                "Категорий пока нет.\n"
                "Напиши название новой категории ✍️"
            )
        else:
            msg = "Твои категории:\n\n"

            for i, (cat_id, name) in enumerate(categories, start=1):
                msg += f"{i}. {name}\n"

            msg += "\nНапиши новую категорию для добавления ✍️"

            await update.message.reply_text(msg)

        context.user_data["waiting_category"] = True
        return


    # ===== УДАЛИТЬ =====
    if text == "❌ Удалить заметки":

        context.user_data["waiting_delete"] = True

        await update.message.reply_text(
            "Пришли номера заметок\n"
            "Пример: 1,2,5-7"
        )
        return


    # ===== МОИ ЗАМЕТКИ =====
    if text == "📋 Мои заметки":

        notes = get_notes(user_id)

        if not notes:
            await update.message.reply_text("Заметок нет")
        else:
           for i, (note_id, note_text, category_name) in enumerate(notes, start=1):
                preview = note_text if len(note_text) <= 60 else note_text[:60] + "…"

                category_label = category_name if category_name else "Без категории"

                keyboard = InlineKeyboardMarkup([
                     [InlineKeyboardButton("❌ Удалить", callback_data=f"confirm_{note_id}")]
                ])

                await update.message.reply_text(
                    f"{i}. [{category_label}] {preview}",
                    reply_markup=keyboard
                )

        return


    # ===== ФОЛБЭК =====
    await update.message.reply_text(
        "Я тебя понял, но пока это не команда 🙂"
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

    #=====ВЫБОР КАТЕГОРИИ====
    if data.startswith("cat_"):

        value = data.split("_")[1]

        if value == "none":
            context.user_data["selected_category"] = None
        else:
            context.user_data["selected_category"] = int(value)

        context.user_data["waiting_note"] = True

        await query.edit_message_text(
            "Категория выбрана ✅\n"
            "Теперь отправь текст заметки ✍️"
        )

        return



def save_note(telegram_id, text, category_id=None):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO notes (telegram_id, text, category_id)
        VALUES (%s, %s, %s)
        """,
        (telegram_id, text, category_id)
    )
    conn.commit()
    cursor.close()
    conn.close()



def main():
    init_db()   # ← ВОТ ЭТО ВАЖНО

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Я работаю!)")
    app.run_polling()



def add_category(telegram_id, name):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO categories (telegram_id, name) VALUES (%s, %s)",
        (telegram_id, name)
    )

    conn.commit()
    cursor.close()
    conn.close()


def get_categories(telegram_id):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, name FROM categories WHERE telegram_id = %s ORDER BY id",
        (telegram_id,)
    )

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data


if __name__ == "__main__":
    main()
