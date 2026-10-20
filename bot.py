import datetime
import logging
import sqlite3
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

(
    ROLE,
    DATE,
    PIZZERIA,
    WAITER_ORDERS,
    BARTENDER_ALCOHOL,
    BO_ORDERS,
    BO_ALCOHOL,
    DELETE_SELECT,
) = range(8)

DB_NAME = "payouts.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            role TEXT NOT NULL,
            is_pizzeria INTEGER DEFAULT 0,
            orders_amount REAL DEFAULT 0,
            alcohol_amount REAL DEFAULT 0,
            base_salary REAL DEFAULT 0,
            waiter_bonus REAL DEFAULT 0,
            bartender_bonus REAL DEFAULT 0,
            total_salary REAL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def calculate_waiter_bonus(amount: float) -> float:
    if amount < 2000:
        return 0.0
    elif amount <= 2999.99:
        return 300.0
    elif amount <= 4999.99:
        return 350.0
    elif amount <= 5999.99:
        return 400.0
    elif amount <= 9999.99:
        return 450.0
    elif amount <= 14999.99:
        return 550.0
    elif amount <= 19999.99:
        return 650.0
    else:
        return 750.0

def calculate_bartender_bonus(amount: float) -> float:
    if amount <= 4999.99:
        return 200.0
    elif amount <= 9999.99:
        return 300.0
    elif amount <= 14999.99:
        return 400.0
    elif amount <= 19999.99:
        return 500.0
    else:
        return 600.0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Бот для расчёта зарплаты готов к работе!\n\n"
        "Команды:\n"
        "➕ /add_shift — Добавить смену\n"
        "📊 /stats — Статистика заработка\n"
        "❌ /delete_shift — Удалить последнюю смену\n"
        "🚫 /cancel — Отмена ввода"
    )
    await update.message.reply_text(welcome_text)

async def add_shift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("Официант", callback_data="ROLE_WAITER")],
        [InlineKeyboardButton("Бармен", callback_data="ROLE_BARTENDER")],
        [InlineKeyboardButton("Бармен-Официант", callback_data="ROLE_BO")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите должность:", reply_markup=reply_markup)
    return ROLE

async def role_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    role_map = {
        "ROLE_WAITER": "Официант",
        "ROLE_BARTENDER": "Бармен",
        "ROLE_BO": "Бармен-Официант"
    }
    context.user_data["role"] = role_map[query.data]
    
    today_str = datetime.date.today().strftime("%d.%m.%Y")
    keyboard = [
        [InlineKeyboardButton(f"Сегодня ({today_str})", callback_data="DATE_TODAY")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_text(
        f"Выбрано: {context.user_data['role']}\n\n"
        "Нажмите 'Сегодня' или введите дату вручную (например: 12.09.2026):",
        reply_markup=reply_markup
    )
    return DATE

async def date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        shift_date = datetime.date.today().strftime("%d.%m.%Y")
    else:
        text = update.message.text.strip()
        try:
            valid_date = datetime.datetime.strptime(text, "%d.%m.%Y").date()
            shift_date = valid_date.strftime("%d.%m.%Y")
        except ValueError:
            await update.message.reply_text("❌ Неверный формат даты. Введите дату как: ДД.ММ.ГГГГ (например, 12.09.2026):")
            return DATE

    context.user_data["date"] = shift_date
    role = context.user_data["role"]

    if role == "Официант":
        keyboard = [
            [InlineKeyboardButton("Да", callback_data="PIZZA_YES"), InlineKeyboardButton("Нет", callback_data="PIZZA_NO")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.message.reply_text("Работал на пиццерии?", reply_markup=reply_markup)
        else:
            await update.message.reply_text("Работал на пиццерии?", reply_markup=reply_markup)
        return PIZZERIA

    elif role == "Бармен":
        msg = "Введите выручку по алкоголю (грн):"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return BARTENDER_ALCOHOL

    elif role == "Бармен-Официант":
        msg = "Введите сумму заказов/кухни (грн):"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return BO_ORDERS

async def pizzeria_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    is_pizza = 1 if query.data == "PIZZA_YES" else 0
    context.user_data["is_pizzeria"] = is_pizza
    
    await query.message.reply_text("Введите общую сумму заказов за смену (грн):")
    return WAITER_ORDERS

async def waiter_orders_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        orders = float(update.message.text.replace(",", "."))
        if orders < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число.")
        return WAITER_ORDERS

    context.user_data["orders"] = orders
    return await finalize_shift(update, context)

async def bartender_alcohol_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        alcohol = float(update.message.text.replace(",", "."))
        if alcohol < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число.")
        return BARTENDER_ALCOHOL

    context.user_data["alcohol"] = alcohol
    return await finalize_shift(update, context)

async def bo_orders_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        orders = float(update.message.text.replace(",", "."))
        if orders < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число.")
        return BO_ORDERS

    context.user_data["orders"] = orders
    await update.message.reply_text("Теперь введите выручку по алкоголю (грн):")
    return BO_ALCOHOL

async def bo_alcohol_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        alcohol = float(update.message.text.replace(",", "."))
        if alcohol < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число.")
        return BO_ALCOHOL

    context.user_data["alcohol"] = alcohol
    return await finalize_shift(update, context)

async def finalize_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = context.user_data

    role = data["role"]
    shift_date = data["date"]
    is_pizzeria = data.get("is_pizzeria", 0)
    orders = data.get("orders", 0.0)
    alcohol = data.get("alcohol", 0.0)

    base_salary = 0.0
    waiter_bonus = 0.0
    bartender_bonus = 0.0

    if role == "Официант":
        base_salary = 750.0 if is_pizzeria else 400.0
        waiter_bonus = calculate_waiter_bonus(orders)
    elif role == "Бармен":
        base_salary = 550.0
        bartender_bonus = calculate_bartender_bonus(alcohol)
    elif role == "Бармен-Официант":
        base_salary = 550.0
        waiter_bonus = calculate_waiter_bonus(orders)
        bartender_bonus = calculate_bartender_bonus(alcohol)

    total_salary = base_salary + waiter_bonus + bartender_bonus

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO shifts 
        (user_id, date, role, is_pizzeria, orders_amount, alcohol_amount, base_salary, waiter_bonus, bartender_bonus, total_salary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, shift_date, role, is_pizzeria, orders, alcohol, base_salary, waiter_bonus, bartender_bonus, total_salary))
    conn.commit()
    conn.close()

    res = f"📅 Смена: {shift_date}\n"
    res += f"👤 Должность: {role}"
    if role == "Официант" and is_pizzeria:
        res += " (Пиццерия)"
    res += "\n\n"

    if role in ["Официант", "Бармен-Официант"]:
        res += f"🍽 Заказы: {orders:,.2f} грн\n".replace(",", " ")
    if role in ["Бармен", "Бармен-Официант"]:
        res += f"🍺 Алкоголь: {alcohol:,.2f} грн\n".replace(",", " ")
    
    res += "\n"
    res += f"💵 Ставка: {base_salary:.0f} грн\n"
    if waiter_bonus > 0 or role in ["Официант", "Бармен-Официант"]:
        res += f"🍽 Премия официанта: {waiter_bonus:.0f} грн\n"
    if bartender_bonus > 0 or role in ["Бармен", "Бармен-Официант"]:
        res += f"🍺 Премия бармена: {bartender_bonus:.0f} грн\n"

    res += f"\n💰 ЗАРАБОТОК ЗА СМЕНУ: {total_salary:,.2f} грн".replace(",", " ")

    await update.message.reply_text(res)
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ввод смены отменён.")
    context.user_data.clear()
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT date, base_salary, waiter_bonus, bartender_bonus, total_salary FROM shifts WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("У вас пока нет сохранённых смен.")
        return

    today = datetime.date.today()
    
    today_shifts = []
    week_shifts = []
    month_shifts = []

    for row in rows:
        try:
            s_date = datetime.datetime.strptime(row[0], "%d.%m.%Y").date()
        except ValueError:
            continue

        if s_date == today:
            today_shifts.append(row)
        
        if (today - s_date).days < 7 and (today - s_date).days >= 0:
            week_shifts.append(row)

        if s_date.month == today.month and s_date.year == today.year:
            month_shifts.append(row)

    def format_period_stats(title, shifts_list):
        if not shifts_list:
            return f"📊 {title}\nСмен: 0\n💰 Всего: 0 грн\n"
        
        count = len(shifts_list)
        base_sum = sum(r[1] for r in shifts_list)
        bonus_sum = sum(r[2] + r[3] for r in shifts_list)
        total_sum = sum(r[4] for r in shifts_list)
        avg = total_sum / count if count > 0 else 0

        res = f"📊 {title}\n"
        res += f"Смен: {count}\n"
        res += f"Ставки: {base_sum:,.0f} грн\n".replace(",", " ")
        res += f"Премии: {bonus_sum:,.0f} грн\n".replace(",", " ")
        res += f"💰 Всего заработано: {total_sum:,.2f} грн\n".replace(",", " ")
        res += f"Средняя зарплата за смену: {avg:,.2f} грн\n".replace(",", " ")
        return res

    month_name = today.strftime("%B %Y")
    
    msg = format_period_stats("СЕГОДНЯ", today_shifts) + "\n"
    msg += format_period_stats("ПОСЛЕДНИЕ 7 ДНЕЙ", week_shifts) + "\n"
    msg += format_period_stats(f"МЕСЯЦ ({month_name})", month_shifts)

    await update.message.reply_text(msg)

async def delete_shift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, date, role, total_salary FROM shifts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("У вас нет сохранённых смен для удаления.")
        return ConversationHandler.END

    keyboard = []
    for row in rows:
        shift_id, s_date, role, total = row
        btn_text = f"{s_date} | {role} | {total:.0f} грн"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"DEL_{shift_id}")])

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="DEL_CANCEL")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Выберите смену для удаления:", reply_markup=reply_markup)
    return DELETE_SELECT

async def delete_shift_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "DEL_CANCEL":
        await query.message.reply_text("Удаление отменено.")
        return ConversationHandler.END

    shift_id = int(query.data.replace("DEL_", ""))
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shifts WHERE id = ? AND user_id = ?", (shift_id, user_id))
    conn.commit()
    conn.close()

    await query.message.reply_text("✅ Смена успешно удалена!")
    return ConversationHandler.END

def main():
    init_db()
    
    # ⚠️ ВСТАВЬТЕ ВАШ ТОКЕН НИЖЕ В КАВЫЧКАХ ⚠️
    BOT_TOKEN = "8539441930:AAG30aygbgRd8nMdCoBW6rGyZ5VyEANdr_k"

    application = Application.builder().token(BOT_TOKEN).build()

    add_shift_handler = ConversationHandler(
        entry_points=[CommandHandler("add_shift", add_shift_start)],
        states={
            ROLE: [CallbackQueryHandler(role_selected, pattern="^ROLE_")],
            DATE: [
                CallbackQueryHandler(date_received, pattern="^DATE_TODAY$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, date_received)
            ],
            PIZZERIA: [CallbackQueryHandler(pizzeria_selected, pattern="^PIZZA_")],
            WAITER_ORDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, waiter_orders_received)],
            BARTENDER_ALCOHOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, bartender_alcohol_received)],
            BO_ORDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, bo_orders_received)],
            BO_ALCOHOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, bo_alcohol_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    delete_shift_handler = ConversationHandler(
        entry_points=[CommandHandler("delete_shift", delete_shift_start)],
        states={
            DELETE_SELECT: [CallbackQueryHandler(delete_shift_confirm, pattern="^DEL_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(add_shift_handler)
    application.add_handler(delete_shift_handler)

    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
