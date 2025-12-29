import os
import asyncio
import sqlite3
from datetime import datetime
from typing import List, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ====================== НАСТРОЙКИ =========================
# На Render токен задаём переменной окружения BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8216135835:AAE91Pn47KnHmtGG-QWsSSQnp4G-0xFW6ig"

# сюда впиши свои Telegram‑ID админов, например {111111111, 222222222}
ADMINS = {5240248802}

DB_PATH = "tour_agency.db"
# =========================================================


# ---------------------- БАЗА ДАННЫХ ----------------------


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self):
        cur = self.conn.cursor()

        # пользователи
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            created_at TEXT,
            last_seen_at TEXT
        );
        """
        )

        # заявки
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tg_id INTEGER NOT NULL,
            username TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            destination TEXT,
            dates TEXT,
            adults INTEGER,
            children INTEGER,
            budget TEXT,
            wishes TEXT,
            contact TEXT,
            admin_comment TEXT,
            admin_tg_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
        )

        self.conn.commit()

    def _now(self) -> str:
        return datetime.utcnow().isoformat(timespec="seconds")

    # --- пользователи ---

    def get_or_create_user(
        self,
        tg_id: int,
        username: Optional[str],
        first_name: Optional[str],
    ) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        now = self._now()
        if row:
            user_id = row["id"]
            cur.execute(
                "UPDATE users SET username=?, first_name=?, last_seen_at=? WHERE id=?",
                (username, first_name, now, user_id),
            )
        else:
            cur.execute(
                "INSERT INTO users (tg_id, username, first_name, created_at, last_seen_at) VALUES (?,?,?,?,?)",
                (tg_id, username, first_name, now, now),
            )
            user_id = cur.lastrowid
        self.conn.commit()
        return user_id

    def get_user_by_tg(self, tg_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        return cur.fetchone()

    # --- заявки ---

    def create_application(self, user: sqlite3.Row, data: dict) -> int:
        cur = self.conn.cursor()
        now = self._now()
        cur.execute(
            """
            INSERT INTO applications (
                user_id, tg_id, username, status,
                created_at, updated_at,
                destination, dates, adults, children,
                budget, wishes, contact
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user["id"],
                user["tg_id"],
                user["username"],
                "new",
                now,
                now,
                data["destination"],
                data["dates"],
                data["adults"],
                data["children"],
                data["budget"],
                data["wishes"],
                data["contact"],
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_application(self, app_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT a.*, u.first_name
            FROM applications a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.id=?
            """,
            (app_id,),
        )
        return cur.fetchone()

    def get_user_applications(self, user_id: int, limit: int = 20) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM applications
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return cur.fetchall()

    def get_applications_by_status(self, statuses: List[str], limit: int = 20) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        placeholders = ",".join("?" * len(statuses))
        cur.execute(
            f"""
            SELECT a.*, u.first_name
            FROM applications a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.status IN ({placeholders})
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (*statuses, limit),
        )
        return cur.fetchall()

    def update_application_status(
        self,
        app_id: int,
        status: str,
        admin_tg_id: int,
        admin_comment: str,
    ):
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE applications
            SET status=?, admin_tg_id=?, admin_comment=?, updated_at=?
            WHERE id=?
            """,
            (status, admin_tg_id, admin_comment, self._now(), app_id),
        )
        self.conn.commit()


db = Database(DB_PATH)

# ----------------------- FSM СОСТОЯНИЯ --------------------


class AppForm(StatesGroup):
    destination = State()
    dates = State()
    adults = State()
    children = State()
    budget = State()
    wishes = State()
    contact = State()
    confirm = State()


class ApproveForm(StatesGroup):
    app_id = State()
    comment = State()


class RejectForm(StatesGroup):
    app_id = State()
    comment = State()


# ----------------------- КЛАВИАТУРЫ -----------------------


def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="🏖 Подобрать тур"),
            KeyboardButton(text="📋 Мои заявки"),
        ],
        [
            KeyboardButton(text="ℹ️ О компании"),
            KeyboardButton(text="🆘 Связаться с менеджером"),
        ],
        [
            KeyboardButton(text="🔁 Повторить заявку"),
            KeyboardButton(text="❓ FAQ"),
        ],
    ]
    if is_admin:
        kb.append([KeyboardButton(text="🛠 Админ‑панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🆕 Новые заявки", callback_data="adm:list:new"),
                InlineKeyboardButton(text="⏳ В обработке", callback_data="adm:list:in_progress"),
            ],
            [
                InlineKeyboardButton(text="✅ Одобренные", callback_data="adm:list:approved"),
                InlineKeyboardButton(text="❌ Отклонённые", callback_data="adm:list:rejected"),
            ],
            [
                InlineKeyboardButton(text="📊 Все заявки", callback_data="adm:list:all"),
            ],
        ]
    )


def app_item_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Открыть заявку",
                    callback_data=f"adm:open:{app_id}",
                )
            ]
        ]
    )


def app_manage_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить", callback_data=f"adm:approve:{app_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"adm:reject:{app_id}"
                ),
            ],
        ]
    )


def app_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📨 Отправить заявку", callback_data="app:send"),
            ],
            [
                InlineKeyboardButton(text="🔁 Заполнить заново", callback_data="app:restart"),
            ],
        ]
    )


def user_after_status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏖 Новая заявка", callback_data="user:newapp"),
            ],
            [
                InlineKeyboardButton(text="🆘 Связаться с менеджером", callback_data="user:contact"),
            ],
        ]
    )


def repeat_confirm_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Повторить эту заявку",
                    callback_data=f"rep:send:{app_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="rep:cancel",
                )
            ],
        ]
    )


# ---------------------- ИНИЦИАЛИЗАЦИЯ ---------------------

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
admin_router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in ADMINS


# ------------------------- ХЭНДЛЕРЫ -----------------------


@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    _ = user_id
    kb = main_menu_kb(is_admin=is_admin(message.from_user.id))
    await message.answer(
        "👋 <b>Добро пожаловать в тур‑бот Anex!</b>\n\n"
        "Здесь вы можете оформить заявку на подбор тура, посмотреть статус своих заявок "
        "и связаться с менеджером.",
        reply_markup=kb,
    )


# ---------- Пользовательское меню: заявка ----------


@router.message(StateFilter(None), F.text == "🏖 Подобрать тур")
async def start_app_form(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AppForm.destination)
    await message.answer(
        "✈️ <b>Шаг 1 из 7.</b>\n\n"
        "В какую страну или город вы хотите поехать?"
    )


@router.message(AppForm.destination)
async def app_destination(message: Message, state: FSMContext):
    await state.update_data(destination=message.text.strip())
    await state.set_state(AppForm.dates)
    await message.answer(
        "📅 <b>Шаг 2 из 7.</b>\n\n"
        "Когда планируете поездку? Укажите примерные даты или период."
    )


@router.message(AppForm.dates)
async def app_dates(message: Message, state: FSMContext):
    await state.update_data(dates=message.text.strip())
    await state.set_state(AppForm.adults)
    await message.answer(
        "👥 <b>Шаг 3 из 7.</b>\n\n"
        "Сколько взрослых едет? (введите число)"
    )


@router.message(AppForm.adults)
async def app_adults(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Пожалуйста, введите положительное число.")
        return
    await state.update_data(adults=int(text))
    await state.set_state(AppForm.children)
    await message.answer(
        "👨‍👩‍👧 <b>Шаг 4 из 7.</b>\n\n"
        "Сколько детей едет? Если без детей — введите 0."
    )


@router.message(AppForm.children)
async def app_children(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or int(text) < 0:
        await message.answer("Пожалуйста, введите 0 или положительное число.")
        return
    await state.update_data(children=int(text))
    await state.set_state(AppForm.budget)
    await message.answer(
        "💵 <b>Шаг 5 из 7.</b>\n\n"
        "Какой ориентировочный бюджет на тур? Можно указать валюту, например:\n"
        "<i>до 1500$ на двоих</i> или <i>до 120 000 ₽</i>."
    )


@router.message(AppForm.budget)
async def app_budget(message: Message, state: FSMContext):
    await state.update_data(budget=message.text.strip())
    await state.set_state(AppForm.wishes)
    await message.answer(
        "🏨 <b>Шаг 6 из 7.</b>\n\n"
        "Ваши пожелания к отелю и туру:\n"
        "• звёздность отеля\n"
        "• тип питания\n"
        "• важные моменты (первая линия, тихий район и т.д.)\n\n"
        "Если особых пожеланий нет — напишите «без пожеланий»."
    )


@router.message(AppForm.wishes)
async def app_wishes(message: Message, state: FSMContext):
    await state.update_data(wishes=message.text.strip())
    await state.set_state(AppForm.contact)
    default_contact = (
        f"@{message.from_user.username}" if message.from_user.username else ""
    )
    await message.answer(
        "📞 <b>Шаг 7 из 7.</b>\n\n"
        "Как с вами удобнее связаться? Укажите телефон, @username или e‑mail.\n"
        f"По умолчанию можем использовать: <b>{default_contact}</b> (если подходит — просто отправьте его)."
    )


@router.message(AppForm.contact)
async def app_contact(message: Message, state: FSMContext):
    contact = message.text.strip()
    await state.update_data(contact=contact)

    data = await state.get_data()
    text = (
        "📝 <b>Проверьте заявку:</b>\n\n"
        f"<b>Направление:</b> {data['destination']}\n"
        f"<b>Даты:</b> {data['dates']}\n"
        f"<b>Взрослых:</b> {data['adults']}\n"
        f"<b>Детей:</b> {data['children']}\n"
        f"<b>Бюджет:</b> {data['budget']}\n"
        f"<b>Пожелания:</b> {data['wishes']}\n"
        f"<b>Контакт:</b> {data['contact']}\n\n"
        "Если всё верно — отправьте заявку менеджеру."
    )
    await state.set_state(AppForm.confirm)
    await message.answer(text, reply_markup=app_confirm_kb())


@router.callback_query(F.data == "app:restart")
async def app_restart(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AppForm.destination)
    await callback.message.answer(
        "Начнём заново.\n\n"
        "✈️ <b>Шаг 1 из 7.</b>\n"
        "В какую страну или город вы хотите поехать?"
    )
    await callback.answer()


@router.callback_query(F.data == "app:send")
async def app_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    user_row = db.get_user_by_tg(callback.from_user.id)
    if not user_row:
        db.get_or_create_user(
            callback.from_user.id, callback.from_user.username, callback.from_user.first_name
        )
        user_row = db.get_user_by_tg(callback.from_user.id)

    app_id = db.create_application(user_row, data)

    await callback.message.answer(
        f"✅ <b>Заявка №{app_id} отправлена менеджеру.</b>\n\n"
        "Мы свяжемся с вами в ближайшее время.",
        reply_markup=main_menu_kb(is_admin=is_admin(callback.from_user.id)),
    )
    await callback.answer("Заявка отправлена")

    summary = (
        f"📩 <b>Новая заявка №{app_id}</b>\n"
        f"От: @{callback.from_user.username or 'без_username'} (ID {callback.from_user.id})\n\n"
        f"Направление: {data['destination']}\n"
        f"Даты: {data['dates']}\n"
        f"Взрослых: {data['adults']}, детей: {data['children']}\n"
        f"Бюджет: {data['budget']}\n"
        f"Пожелания: {data['wishes']}\n"
        f"Контакт: {data['contact']}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                summary,
                reply_markup=app_manage_kb(app_id),
            )
        except Exception:
            pass


# ---------- Мои заявки ----------


def human_status(code: str) -> str:
    return {
        "new": "🆕 Новая",
        "in_progress": "⏳ В обработке",
        "approved": "✅ Одобрена",
        "rejected": "❌ Отклонённая",
    }.get(code, code)


@router.message(StateFilter(None), F.text == "📋 Мои заявки")
async def my_apps(message: Message):
    user = db.get_user_by_tg(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. Нажмите /start.")
        return
    apps = db.get_user_applications(user["id"], limit=20)
    if not apps:
        await message.answer(
            "У вас пока нет заявок.\n"
            "Нажмите «🏖 Подобрать тур», чтобы отправить первую."
        )
        return
    lines = ["📋 <b>Ваши заявки:</b>\n"]
    for a in apps:
        lines.append(
            f"• №{a['id']} — {human_status(a['status'])}\n"
            f"  Направление: {a['destination']}\n"
            f"  Даты: {a['dates']}\n"
            f"  Обновлено: {a['updated_at']}\n"
        )
    await message.answer("\n".join(lines))


# ---------- Повторить последнюю заявку ----------


@router.message(StateFilter(None), F.text == "🔁 Повторить заявку")
async def repeat_last_app(message: Message):
    user = db.get_user_by_tg(message.from_user.id)
    if not user:
        await message.answer("Профиль не найден. Нажмите /start.")
        return
    apps = db.get_user_applications(user["id"], limit=1)
    if not apps:
        await message.answer(
            "У вас ещё нет заявок, чтобы их повторять.\n"
            "Сначала отправьте первую через «🏖 Подобрать тур»."
        )
        return

    a = apps[0]
    text = (
        f"📎 <b>Последняя заявка №{a['id']}</b> ({human_status(a['status'])})\n\n"
        f"<b>Направление:</b> {a['destination']}\n"
        f"<b>Даты:</b> {a['dates']}\n"
        f"<b>Взрослых:</b> {a['adults']}\n"
        f"<b>Детей:</b> {a['children']}\n"
        f"<b>Бюджет:</b> {a['budget']}\n"
        f"<b>Пожелания:</b> {a['wishes']}\n"
        f"<b>Контакт:</b> {a['contact']}\n\n"
        "Отправить такую же заявку ещё раз?"
    )
    await message.answer(text, reply_markup=repeat_confirm_kb(a["id"]))


@router.callback_query(F.data.startswith("rep:send:"))
async def repeat_send(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[2])
    a = db.get_application(app_id)
    if not a:
        await callback.answer("Не удалось найти исходную заявку.", show_alert=True)
        return

    user = db.get_user_by_tg(a["tg_id"])
    if not user:
        await callback.answer("Профиль пользователя не найден.", show_alert=True)
        return

    data = {
        "destination": a["destination"],
        "dates": a["dates"],
        "adults": a["adults"],
        "children": a["children"],
        "budget": a["budget"],
        "wishes": a["wishes"],
        "contact": a["contact"],
    }
    new_app_id = db.create_application(user, data)

    await callback.message.answer(
        f"✅ Заявка №{new_app_id} отправлена повторно.\n"
        f"(на основе заявки №{app_id})",
        reply_markup=main_menu_kb(is_admin=is_admin(callback.from_user.id)),
    )
    await callback.answer("Заявка повторена")

    summary = (
        f"📩 <b>Новая повторная заявка №{new_app_id}</b>\n"
        f"(на основе заявки №{app_id})\n"
        f"От: @{user['username'] or 'без_username'} (ID {user['tg_id']})\n\n"
        f"Направление: {data['destination']}\n"
        f"Даты: {data['dates']}\n"
        f"Взрослых: {data['adults']}, детей: {data['children']}\n"
        f"Бюджет: {data['budget']}\n"
        f"Пожелания: {data['wishes']}\n"
        f"Контакт: {data['contact']}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                summary,
                reply_markup=app_manage_kb(new_app_id),
            )
        except Exception:
            pass


@router.callback_query(F.data == "rep:cancel")
async def repeat_cancel(callback: CallbackQuery):
    await callback.message.answer("Повтор заявки отменён.")
    await callback.answer()


# ---------- Инфо, FAQ и поддержка ----------


@router.message(StateFilter(None), F.text == "ℹ️ О компании")
async def about(message: Message):
    await message.answer(
        "🌍 <b>Anex Tour — подбор путешествий под ваши желания.</b>\n\n"
        "Мы поможем подобрать тур по вашему бюджету, пожеланиям к отелю и датам.\n"
        "Заполните заявку и дождитесь ответа менеджера."
    )


@router.message(StateFilter(None), F.text == "❓ FAQ")
async def faq(message: Message):
    text = (
        "❓ <b>Частые вопросы</b>\n\n"
        "<b>1. Как быстро отвечает менеджер?</b>\n"
        "Обычно в течение 15–60 минут в рабочее время.\n\n"
        "<b>2. Когда оплачивать тур?</b>\n"
        "После согласования варианта и подтверждения брони.\n\n"
        "<b>3. Нужна ли виза?</b>\n"
        "Зависит от направления. Менеджер подскажет по вашей стране.\n\n"
        "<b>4. Какие документы нужны?</b>\n"
        "Паспорт (загран или внутренний — по направлению), иногда доп. документы для визы.\n\n"
        "<b>5. Можно ли вернуть деньги?</b>\n"
        "Условия зависят от тарифа и правил туроператора. Мы подскажем оптимальный вариант."
    )
    await message.answer(text)


@router.message(StateFilter(None), F.text == "🆘 Связаться с менеджером")
async def contact_manager(message: Message):
    await message.answer(
        "🆘 <b>Связь с менеджером</b>\n\n"
        "Опишите свой вопрос одним сообщением. Мы увидим его и ответим вам в этом чате.",
    )


@router.message(
    StateFilter(None),
    F.text
    & ~F.text.in_(
        {
            "🏖 Подобрать тур",
            "📋 Мои заявки",
            "ℹ️ О компании",
            "🆘 Связаться с менеджером",
            "🛠 Админ‑панель",
            "🔁 Повторить заявку",
            "❓ FAQ",
        }
    )
)
async def forward_to_admins(message: Message):
    """
    Любое «обычное» текстовое сообщение считаем обращением
    к менеджеру и пересылаем админам.
    Работает только когда НЕТ активного состояния (StateFilter(None)).
    """
    text = (
        f"📨 Сообщение от пользователя @{message.from_user.username or 'без_username'} "
        f"(ID {message.from_user.id}):\n\n{message.text}"
    )
    sent = False
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text)
            sent = True
        except Exception:
            pass
    if sent:
        await message.answer(
            "Ваше сообщение передано менеджеру. Постараемся ответить как можно скорее."
        )


# Дополнительные колбэки от кнопок после статуса


@router.callback_query(F.data == "user:newapp")
async def user_newapp(callback: CallbackQuery, state: FSMContext):
    await start_app_form(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "user:contact")
async def user_contact(callback: CallbackQuery):
    await contact_manager(callback.message)
    await callback.answer()


# ---------- Админ‑панель ----------


@admin_router.message(StateFilter(None), F.text == "🛠 Админ‑панель")
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к админ‑панели.")
        return
    await message.answer(
        "🛠 <b>Админ‑панель Anex</b>\n\n"
        "Выберите, какие заявки хотите посмотреть.",
        reply_markup=admin_panel_kb(),
    )


@admin_router.callback_query(F.data.startswith("adm:list:"))
async def admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    kind = callback.data.split(":")[2]
    if kind == "new":
        statuses = ["new"]
        title = "🆕 <b>Новые заявки</b>"
    elif kind == "in_progress":
        statuses = ["in_progress"]
        title = "⏳ <b>Заявки в обработке</b>"
    elif kind == "approved":
        statuses = ["approved"]
        title = "✅ <b>Одобренные заявки</b>"
    elif kind == "rejected":
        statuses = ["rejected"]
        title = "❌ <b>Отклонённые заявки</b>"
    else:
        statuses = ["new", "in_progress", "approved", "rejected"]
        title = "📊 <b>Все заявки</b>"

    apps = db.get_applications_by_status(statuses, limit=20)

    if not apps:
        await callback.message.answer(f"{title}\n\nЗаявок в этой категории нет.")
        await callback.answer()
        return

    await callback.message.answer(title)
    for a in apps:
        text = (
            f"№{a['id']} — {human_status(a['status'])}\n"
            f"Клиент: @{a['username'] or 'без_username'} (ID {a['tg_id']})\n"
            f"Направление: {a['destination']}\n"
            f"Даты: {a['dates']}\n"
            f"Создана: {a['created_at']}"
        )
        await callback.message.answer(text, reply_markup=app_item_kb(a["id"]))

    await callback.answer()


def format_app_full(a: sqlite3.Row) -> str:
    return (
        f"📝 <b>Заявка №{a['id']}</b> — {human_status(a['status'])}\n\n"
        f"<b>Клиент:</b> @{a['username'] or 'без_username'} (ID {a['tg_id']})\n"
        f"<b>Имя:</b> {a['first_name'] or '-'}\n"
        f"<b>Создана:</b> {a['created_at']}\n"
        f"<b>Обновлена:</b> {a['updated_at']}\n\n"
        f"<b>Направление:</b> {a['destination']}\n"
        f"<b>Даты:</b> {a['dates']}\n"
        f"<b>Взрослых:</b> {a['adults']}\n"
        f"<b>Детей:</b> {a['children']}\n"
        f"<b>Бюджет:</b> {a['budget']}\n"
        f"<b>Пожелания:</b> {a['wishes']}\n"
        f"<b>Контакт:</b> {a['contact']}\n\n"
        f"<b>Комментарий менеджера:</b> {a['admin_comment'] or '—'}"
    )


@admin_router.callback_query(F.data.startswith("adm:open:"))
async def admin_open(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    app_id = int(callback.data.split(":")[2])
    a = db.get_application(app_id)
    if not a:
        await callback.message.answer("Заявка не найдена.")
        await callback.answer()
        return

    if a["status"] == "new":
        db.update_application_status(app_id, "in_progress", callback.from_user.id, a["admin_comment"] or "")
        a = db.get_application(app_id)

    text = format_app_full(a)
    await callback.message.answer(text, reply_markup=app_manage_kb(app_id))
    await callback.answer()


# ---------- Одобрение / отклонение заявки ----------


@admin_router.callback_query(F.data.startswith("adm:approve:"))
async def admin_approve_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    app_id = int(callback.data.split(":")[2])
    a = db.get_application(app_id)
    if not a:
        await callback.message.answer("Заявка не найдена.")
        await callback.answer()
        return

    await state.set_state(ApproveForm.comment)
    await state.update_data(
        app_id=app_id,
        src_chat_id=callback.message.chat.id,
        src_msg_id=callback.message.message_id,
    )

    await callback.message.answer(
        f"Одобрение заявки №{app_id}.\n\n"
        "Введите комментарий для клиента (детали по туру, условия и т.п.). "
        "Если без комментария — отправьте «-»."
    )
    await callback.answer()


@admin_router.message(ApproveForm.comment)
async def admin_approve_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    app_id = data["app_id"]
    src_chat_id = data.get("src_chat_id")
    src_msg_id = data.get("src_msg_id")

    comment = message.text.strip()
    if comment == "-":
        comment = ""

    db.update_application_status(app_id, "approved", message.from_user.id, comment)
    a = db.get_application(app_id)

    await state.clear()

    if src_chat_id and src_msg_id:
        try:
            await bot.edit_reply_markup(chat_id=src_chat_id, message_id=src_msg_id, reply_markup=None)
        except Exception:
            pass

    await message.answer(f"Заявка №{app_id} отмечена как <b>одобренная</b>.")

    try:
        text = (
            f"✅ <b>Ваша заявка №{app_id} одобрена менеджером.</b>\n\n"
            f"Направление: {a['destination']}\n"
            f"Даты: {a['dates']}\n\n"
        )
        if comment:
            text += f"Комментарий менеджера:\n{comment}"
        else:
            text += "С вами свяжутся для уточнения деталей."
        await bot.send_message(a["tg_id"], text, reply_markup=user_after_status_kb())
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("adm:reject:"))
async def admin_reject_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    app_id = int(callback.data.split(":")[2])
    a = db.get_application(app_id)
    if not a:
        await callback.message.answer("Заявка не найдена.")
        await callback.answer()
        return

    await state.set_state(RejectForm.comment)
    await state.update_data(
        app_id=app_id,
        src_chat_id=callback.message.chat.id,
        src_msg_id=callback.message.message_id,
    )

    await callback.message.answer(
        f"Отклонение заявки №{app_id}.\n\n"
        "Укажите причину (например: нет мест на нужные даты, бюджет слишком мал и т.п.)."
    )
    await callback.answer()


@admin_router.message(RejectForm.comment)
async def admin_reject_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    app_id = data["app_id"]
    src_chat_id = data.get("src_chat_id")
    src_msg_id = data.get("src_msg_id")

    comment = message.text.strip()
    if not comment:
        comment = "Заявка отклонена без указания причины."

    db.update_application_status(app_id, "rejected", message.from_user.id, comment)
    a = db.get_application(app_id)

    await state.clear()

    if src_chat_id and src_msg_id:
        try:
            await bot.edit_reply_markup(chat_id=src_chat_id, message_id=src_msg_id, reply_markup=None)
        except Exception:
            pass

    await message.answer(f"Заявка №{app_id} отмечена как <b>отклонённая</b>.")

    try:
        text = (
            f"❌ <b>Ваша заявка №{app_id} отклонена.</b>\n\n"
            f"Причина:\n{comment}"
        )
        await bot.send_message(a["tg_id"], text, reply_markup=user_after_status_kb())
    except Exception:
        pass


# ------------------ ЗАПУСК БОТА ---------------------------


async def main():
    if BOT_TOKEN == "ВАШ_ТОКЕН_БОТА_ОТ_BOTFATHER":
        print("⚠️ Укажи реальный BOT_TOKEN в переменной окружения BOT_TOKEN.")
    dp.include_router(router)
    dp.include_router(admin_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
