import os
import psycopg2
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN = os.getenv("BOT_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# -------------------- DB --------------------
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            name TEXT NOT NULL
        );
    """)

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
            telegram_id BIGINT NOT NULL,
            text TEXT NOT NULL,
            category_id INTEGER NULL
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()


def save_user(telegram_id, username, first_name):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO users (telegram_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO NOTHING
        """,
        (telegram_id, username, first_name),
    )

    conn.commit()
    cursor.close()
    conn.close()


def add_category(telegram_id, name):
    name = (name or "").strip()
    if not name:
        return False

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO categories (telegram_id, name) VALUES (%s, %s)",
        (telegram_id, name),
    )

    conn.commit()
    cursor.close()
    conn.close()
    return True


def get_categories(telegram_id):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, name FROM categories WHERE telegram_id = %s ORDER BY id",
        (telegram_id,),
    )

    data = cursor.fetchall()
    cursor.close()
    conn.close()
    return data


def save_note(telegram_id, text, category_id=None):
    text = (text or "").strip()
    if not text:
        return False

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO notes (telegram_id, text, category_id)
        VALUES (%s, %s, %s)
        """,
        (telegram_id, text, category_id),
    )

    conn.commit()
    cursor.close()
    conn.close()
    return True


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
        (telegram_id,),
    )

    notes = cursor.fetchall()
    cursor.close()
    conn.close()
    return notes


def delete_notes_bulk(telegram_id, ids):
    if not ids:
        return 0

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM notes WHERE telegram_id = %s AND id = ANY(%s)",
        (telegram_id, ids),
    )

    deleted = cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()
    return deleted


def parse_ids(text):
    ids = set()
    parts = (text or "").split(",")

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start, end = part.split("-", 1)
            start = int(start)
            end = int(end)
            if start > end:
                start, end = end, start
            for i in range(start, end + 1):
                ids.add(i)
        else:
            ids.add(int(part))

    return list(ids)


# -------------------- UI helpers --------------------
MENU_BUTTONS = ["➕ Новая заметка", "📋 Мои заметки", "📂 Категории", "❌ Удалить заметки", "🔍 Поиск", "⏰ Напоминания"]


def get_menu():
    keyboard = [
        ["➕ Новая заметка"],
        ["📋 Мои заметки"],
        ["📂 Категории"],
        ["❌ Удалить заметки"],
        ["🔍 Поиск"],
        ["⏰ Напоминания"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def categories_keyboard(categories):
    keyboard = [[InlineKeyboardButton(name, callback_data=f"cat_{cat_id}")] for cat_id, name in categories]
    keyboard.append([InlineKeyboardButton("Без категории", callback_data="cat_none")])
    return InlineKeyboardMarkup(keyboard)


def reset_state(context: ContextTypes.DEFAULT_TYPE):
    # Сбрасываем только "флаги процессов", но НЕ выносим всю память (например note_map)
    for key in ["waiting_note", "waiting_delete", "waiting_category", "selected_category"]:
        context.user_data.pop(key, None)


# -------------------- Handlers --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name)

    reset_state(context)
    await update.message.reply_text("Добро пожаловать 🚀", reply_markup=get_menu())


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, username, first_name FROM users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()

    if not users:
        await update.message.reply_text("Пользователей нет")
        return

    text = "Список пользователей:\n\n"
    for u in users:
        text += f"ID: {u[0]}, Username: {u[1]}, Name: {u[2]}\n"
    await update.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    # ---------- STATES FIRST ----------
    if context.user_data.get("waiting_category"):
        ok = add_category(user_id, text)
        context.user_data["waiting_category"] = False

        if ok:
            await update.message.reply_text("Категория добавлена ✅", reply_markup=get_menu())
        else:
            await update.message.reply_text("Пустое название. Напиши нормальное имя категории ✍️")
            context.user_data["waiting_category"] = True
        return

    if context.user_data.get("waiting_note"):
        category_id = context.user_data.get("selected_category")
        ok = save_note(user_id, text, category_id)

        context.user_data["waiting_note"] = False
        context.user_data["selected_category"] = None

        if ok:
            await update.message.reply_text("Заметка сохранена ✅", reply_markup=get_menu())
        else:
            await update.message.reply_text("Пустая заметка. Отправь текст ещё раз ✍️")
            context.user_data["waiting_note"] = True
        return

    if context.user_data.get("waiting_delete"):
        try:
            numbers = parse_ids(text)
        except Exception:
            await update.message.reply_text("Неверный формат. Пример: 1,2,5-7")
            return

        note_map = context.user_data.get("note_map", {})
        ids_to_delete = [note_map.get(num) for num in numbers if num in note_map]

        if not ids_to_delete:
            await update.message.reply_text(
                "Нет таких номеров. Сначала открой «📋 Мои заметки»."
            )
            return

        deleted = delete_notes_bulk(user_id, ids_to_delete)
        context.user_data["waiting_delete"] = False
        await update.message.reply_text(
            f"Удалено заметок: {deleted} ✅", reply_markup=get_menu()
        )
        return

    # ---------- MENU ACTIONS ----------
    if text == "➕ Новая заметка":
        reset_state(context)

        categories = get_categories(user_id)
        if categories:
            await update.message.reply_text(
                "Выбери категорию:",
                reply_markup=categories_keyboard(categories),
            )
        else:
            context.user_data["waiting_note"] = True
            context.user_data["selected_category"] = None
            await update.message.reply_text("Категорий нет. Отправь текст заметки:")
        return

    if text == "📂 Категории":
        reset_state(context)

        categories = get_categories(user_id)
        if not categories:
            await update.message.reply_text(
                "Категорий пока нет.\nНапиши название новой категории ✍️"
            )
        else:
            msg = "Твои категории:\n\n"
            for i, (_, name) in enumerate(categories, start=1):
                msg += f"{i}. {name}\n"
            msg += "\nНапиши новую категорию для добавления ✍️"
            await update.message.reply_text(msg)

        context.user_data["waiting_category"] = True
        return

    if text == "📋 Мои заметки":
        reset_state(context)

        notes = get_notes(user_id)
        context.user_data["note_map"] = {}

        if not notes:
            await update.message.reply_text("Заметок нет")
            return

        for i, (note_id, note_text, category_name) in enumerate(notes, start=1):
            context.user_data["note_map"][i] = note_id

            preview = note_text if len(note_text) <= 60 else note_text[:60] + "…"
            category_label = category_name if category_name else "Без категории"

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Удалить", callback_data=f"confirm_{note_id}"
                        )
                    ]
                ]
            )

            await update.message.reply_text(
                f"{i}. [{category_label}] {preview}", reply_markup=keyboard
            )

        return

    if text == "❌ Удалить заметки":
        reset_state(context)

        context.user_data["waiting_delete"] = True
        await update.message.reply_text(
            "Пришли номера заметок для удаления\n"
            "Пример: 1,2,5-7\n\n"
            "Подсказка: номера берутся из «📋 Мои заметки»."
        )
        return

    if text == "🔍 Поиск":
        await update.message.reply_text("Поиск подключим следующим спринтом 🙂")
        return

    if text == "⏰ Напоминания":
        await update.message.reply_text("Напоминания подключим следующим спринтом 🙂")
        return

    await update.message.reply_text("Я тебя понял, но пока это не команда 🙂")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data.startswith("confirm_"):
        note_id = int(data.split("_", 1)[1])
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да", callback_data=f"delete_{note_id}"),
                InlineKeyboardButton("❌ Нет", callback_data="cancel_delete"),
            ]
        ])
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if data.startswith("delete_"):
        note_id = int(data.split("_", 1)[1])
        deleted = delete_notes_bulk(user_id, [note_id])
        await query.edit_message_text("Заметка удалена ✅" if deleted else "Ошибка удаления")
        return

    if data == "cancel_delete":
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if data.startswith("cat_"):
        value = data.split("_", 1)[1]
        context.user_data["selected_category"] = None if value == "none" else int(value)
        context.user_data["waiting_note"] = True

        await query.edit_message_text("Категория выбрана ✅\nТеперь отправь текст заметки ✍️")
        return


def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Я работаю!)")
    app.run_polling()


if __name__ == "__main__":
    main()