import os
import asyncio
import html
import sqlite3
import re
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
BOT_TOKEN = os.getenv("BOT_TOKEN") or "7974067391:AAHVchxLtfMVaknN5qHPyAoA2hOnSzE8GdE"

# сюда впиши свои Telegram‑ID админов, например {111111111, 222222222}
ADMINS = {5240248802, 553539259}

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

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER UNIQUE NOT NULL,
            tg_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            stars INTEGER NOT NULL,
            body TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(id)
        );
        """
        )

        self.conn.commit()

    # --- отзывы ---

    def review_for_application_exists(self, application_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM reviews WHERE application_id=? LIMIT 1",
            (application_id,),
        )
        return cur.fetchone() is not None

    def get_application_tg_id(self, application_id: int) -> Optional[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT tg_id FROM applications WHERE id=?", (application_id,))
        row = cur.fetchone()
        return int(row["tg_id"]) if row else None

    def create_review(
        self,
        application_id: int,
        tg_id: int,
        username: Optional[str],
        first_name: Optional[str],
        stars: int,
        body: Optional[str],
    ) -> int:
        cur = self.conn.cursor()
        now = self._now()
        cur.execute(
            """
            INSERT INTO reviews (
                application_id, tg_id, username, first_name,
                stars, body, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                application_id,
                tg_id,
                username,
                first_name,
                stars,
                (body or "").strip() or None,
                now,
                now,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_reviews_newest_first(self, limit: int = 500) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM reviews
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()

    def get_review(self, review_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM reviews WHERE id=?", (review_id,))
        return cur.fetchone()

    def update_review_body(self, review_id: int, body: Optional[str]) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE reviews SET body=?, updated_at=? WHERE id=?",
            (body, self._now(), review_id),
        )
        self.conn.commit()

    def update_review_stars(self, review_id: int, stars: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE reviews SET stars=?, updated_at=? WHERE id=?",
            (stars, self._now(), review_id),
        )
        self.conn.commit()

    def delete_review(self, review_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM reviews WHERE id=?", (review_id,))
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


class SupportForm(StatesGroup):
    message = State()


class ReviewForm(StatesGroup):
    waiting_text = State()


class AdminReviewForm(StatesGroup):
    waiting_body = State()


# ----------------------- КЛАВИАТУРЫ -----------------------


def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="🏖 Подобрать тур"),
            KeyboardButton(text="📋 Мои заявки"),
        ],
        [
            KeyboardButton(text="⭐ Отзывы клиентов"),
            KeyboardButton(text="ℹ️ О компании"),
        ],
        [
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
            [
                InlineKeyboardButton(text="⭐ Управление отзывами", callback_data="admrev:list"),
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


def review_prompt_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Оставить отзыв",
                    callback_data=f"rev:start:{app_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить",
                    callback_data="rev:skip",
                )
            ],
        ]
    )


def review_stars_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rev:rate:{app_id}:{i}")
                for i in range(1, 6)
            ]
        ]
    )


def review_text_options_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Только оценка (без текста)",
                    callback_data="rev:notext",
                )
            ],
        ]
    )


def stars_row(n: int) -> str:
    n = max(1, min(5, n))
    return "⭐" * n + "☆" * (5 - n)


def admin_reviews_list_kb(rows: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    lines: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for r in rows:
        row.append(
            InlineKeyboardButton(
                text=f"#{r['id']} · {r['stars']}⭐",
                callback_data=f"admrev:open:{r['id']}",
            )
        )
        if len(row) >= 3:
            lines.append(row)
            row = []
    if row:
        lines.append(row)
    lines.append(
        [InlineKeyboardButton(text="⬅️ В админ‑панель", callback_data="admrev:panel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=lines)


def admin_review_manage_kb(review_id: int) -> InlineKeyboardMarkup:
    star_row = [
        InlineKeyboardButton(text=f"{i}⭐", callback_data=f"admrev:star:{review_id}:{i}")
        for i in range(1, 6)
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить текст",
                    callback_data=f"admrev:edittext:{review_id}",
                )
            ],
            star_row[:3],
            star_row[3:],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"admrev:delask:{review_id}",
                )
            ],
            [
                InlineKeyboardButton(text="📋 К списку отзывов", callback_data="admrev:list"),
            ],
            [
                InlineKeyboardButton(text="⬅️ В админ‑панель", callback_data="admrev:panel"),
            ],
        ]
    )


def admin_review_delete_confirm_kb(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"admrev:delyes:{review_id}",
                ),
                InlineKeyboardButton(
                    text="↩️ Отмена",
                    callback_data=f"admrev:open:{review_id}",
                ),
            ],
        ]
    )


def format_public_reviews_block(rows: List[sqlite3.Row]) -> str:
    if not rows:
        return (
            "⭐️ <b>Отзывы клиентов</b>\n\n"
            "Пока отзывов нет. После отправки заявки бот предложит оставить оценку."
        )
    head = "⭐️ <b>Отзывы клиентов Anex</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    reserve = 120
    max_len = 4096
    parts: List[str] = []
    pos = 0
    total = len(head)
    while pos < len(rows) and total < max_len - reserve:
        r = rows[pos]
        who = r["first_name"] or (f"@{r['username']}" if r["username"] else "Клиент")
        who_e = html.escape(str(who))
        body_raw = r["body"]
        if body_raw:
            body_e = html.escape(str(body_raw))
            body_line = body_e
        else:
            body_line = "<i>без текста</i>"
        block = (
            f"▸ <b>#{r['id']}</b>  {stars_row(int(r['stars']))}\n"
            f"👤 {who_e}\n"
            f"💬 {body_line}\n"
            f"───────────────"
        )
        sep = "\n\n" if parts else ""
        if total + len(sep) + len(block) > max_len - reserve:
            break
        parts.append(block)
        total += len(sep) + len(block)
        pos += 1
    text = head + "\n\n".join(parts)
    rest = len(rows) - pos
    if rest > 0:
        text += f"\n\n<i>… и ещё {rest} отзыв(ов) — сообщение ограничено длиной Telegram.</i>"
    return text


def format_admin_review_caption(r: sqlite3.Row) -> str:
    who = r["first_name"] or (f"@{r['username']}" if r["username"] else "Клиент")
    who_e = html.escape(str(who))
    body_raw = r["body"]
    body_e = html.escape(str(body_raw)) if body_raw else "—"
    return (
        f"📝 <b>Отзыв №{r['id']}</b>\n"
        f"Заявка: №{r['application_id']}\n"
        f"Клиент: {who_e} (tg {r['tg_id']})\n"
        f"Оценка: {stars_row(int(r['stars']))}\n"
        f"Текст:\n{body_e}\n"
        f"<i>Создан: {r['created_at']}</i>"
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
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
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
        "Оставьте, пожалуйста, контакт для связи: только номер телефона.\n"
        "Только номер РФ: +7XXXXXXXXXX или 8XXXXXXXXXX.\n"
        f"По умолчанию можем использовать: <b>{default_contact}</b> (если это номер телефона — просто отправьте его)."
    )


@router.message(AppForm.contact)
async def app_contact(message: Message, state: FSMContext):
    contact = message.text.strip()
    normalized = re.sub(r"[^\d+]", "", contact)
    normalized = normalized.replace("+", "", 1) if normalized.startswith("+") else normalized

    # Допускаем только номера РФ: +7XXXXXXXXXX или 8XXXXXXXXXX (ровно 11 цифр).
    if not re.fullmatch(r"(7|8)\d{10}", normalized):
        await message.answer(
            "Пожалуйста, укажите корректный номер телефона РФ.\n"
            "Пример: <b>+79991234567</b> или <b>89991234567</b>."
        )
        return
    await state.update_data(contact=contact)

    data = await state.get_data()
    required_keys = ["destination", "dates", "adults", "children", "budget", "wishes", "contact"]
    if not all(k in data for k in required_keys):
        await message.answer(
            "Произошла ошибка при сохранении заявки. "
            "Пожалуйста, начните оформление заново."
        )
        await state.clear()
        await start_app_form(message, state)
        return

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
    required_keys = ["destination", "dates", "adults", "children", "budget", "wishes", "contact"]
    if not all(k in data for k in required_keys):
        await callback.message.answer(
            "Произошла ошибка при сохранении заявки. "
            "Пожалуйста, начните оформление заново через «🏖 Подобрать тур»."
        )
        await state.clear()
        await callback.answer()
        return

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
    await callback.message.answer(
        "Хотите оставить короткий отзыв о сервисе?",
        reply_markup=review_prompt_kb(app_id),
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
    await callback.message.answer(
        "Хотите оставить короткий отзыв о сервисе?",
        reply_markup=review_prompt_kb(new_app_id),
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


# ---------- Отзывы (пользователь) ----------


@router.callback_query(F.data == "rev:skip")
async def rev_skip(callback: CallbackQuery):
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Без проблем")


@router.callback_query(F.data.startswith("rev:start:"))
async def rev_start(callback: CallbackQuery, state: FSMContext):
    app_id = int(callback.data.split(":")[2])
    if db.review_for_application_exists(app_id):
        await callback.answer("По этой заявке отзыв уже оставлен.", show_alert=True)
        return
    owner = db.get_application_tg_id(app_id)
    if owner is None or owner != callback.from_user.id:
        await callback.answer("Можно оставить отзыв только по своей заявке.", show_alert=True)
        return
    await state.clear()
    await callback.message.answer(
        "Выберите оценку от 1 до 5:",
        reply_markup=review_stars_kb(app_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rev:rate:"))
async def rev_rate(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    app_id = int(parts[2])
    stars = int(parts[3])
    if stars < 1 or stars > 5:
        await callback.answer()
        return
    if db.review_for_application_exists(app_id):
        await callback.answer("По этой заявке отзыв уже есть.", show_alert=True)
        return
    owner = db.get_application_tg_id(app_id)
    if owner is None or owner != callback.from_user.id:
        await callback.answer("Это не ваша заявка.", show_alert=True)
        return
    await state.set_state(ReviewForm.waiting_text)
    await state.update_data(rev_app_id=app_id, rev_stars=stars)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"Оценка: {stars_row(stars)}\n\n"
        "Напишите текст отзыва одним сообщением или нажмите кнопку ниже, "
        "если достаточно только оценки.",
        reply_markup=review_text_options_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "rev:notext")
async def rev_notext(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    app_id = data.get("rev_app_id")
    stars = data.get("rev_stars")
    if app_id is None or stars is None:
        await callback.answer("Сначала выберите оценку звёздами.", show_alert=True)
        return
    if db.review_for_application_exists(app_id):
        await state.clear()
        await callback.answer("Отзыв уже сохранён.", show_alert=True)
        return
    owner = db.get_application_tg_id(app_id)
    if owner is None or owner != callback.from_user.id:
        await state.clear()
        await callback.answer("Ошибка доступа.", show_alert=True)
        return
    try:
        db.create_review(
            app_id,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
            int(stars),
            None,
        )
    except sqlite3.IntegrityError:
        await state.clear()
        await callback.answer("Отзыв уже был сохранён.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Спасибо!")
    await callback.message.answer("✅ Спасибо за отзыв! Он появится в разделе «⭐ Отзывы клиентов».")


@router.message(ReviewForm.waiting_text)
async def rev_text(message: Message, state: FSMContext):
    data = await state.get_data()
    app_id = data.get("rev_app_id")
    stars = data.get("rev_stars")
    if app_id is None or stars is None:
        await state.clear()
        await message.answer("Сессия отзыва сброшена. Начните с кнопки под заявкой.")
        return
    if db.review_for_application_exists(app_id):
        await state.clear()
        await message.answer("По этой заявке отзыв уже оставлен.")
        return
    owner = db.get_application_tg_id(app_id)
    if owner is None or owner != message.from_user.id:
        await state.clear()
        await message.answer("Ошибка доступа.")
        return
    body = (message.text or "").strip()
    if not body:
        await message.answer("Введите текст отзыва или нажмите «Только оценка».")
        return
    if len(body) > 2000:
        body = body[:2000]
    try:
        db.create_review(
            app_id,
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
            int(stars),
            body,
        )
    except sqlite3.IntegrityError:
        await state.clear()
        await message.answer("По этой заявке отзыв уже сохранён.")
        return
    await state.clear()
    await message.answer(
        "✅ Спасибо за отзыв! Он появится в разделе «⭐ Отзывы клиентов»."
    )


# ---------- Инфо, FAQ и поддержка ----------


@router.message(StateFilter(None), F.text == "⭐ Отзывы клиентов")
async def show_public_reviews(message: Message):
    rows = db.list_reviews_newest_first(limit=200)
    await message.answer(format_public_reviews_block(rows))


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
async def contact_manager_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SupportForm.message)
    await message.answer(
        "🆘 <b>Связь с менеджером</b>\n\n"
        "Опишите свой вопрос одним сообщением. Мы передадим его менеджеру.",
    )


@router.message(SupportForm.message)
async def contact_manager_send(message: Message, state: FSMContext):
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

    await state.clear()

    if sent:
        await message.answer(
            "Ваше сообщение передано менеджеру. "
            "Мы ответим вам в этом чате."
        )
    else:
        await message.answer(
            "Не удалось передать сообщение менеджеру. Попробуйте позже."
        )


# Дополнительные колбэки от кнопок после статуса


@router.callback_query(F.data == "user:newapp")
async def user_newapp(callback: CallbackQuery, state: FSMContext):
    await start_app_form(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "user:contact")
async def user_contact(callback: CallbackQuery, state: FSMContext):
    await contact_manager_start(callback.message, state)
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


# ---------- Админ: отзывы ----------


@admin_router.callback_query(F.data == "admrev:panel")
async def admrev_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await callback.message.answer(
        "🛠 <b>Админ‑панель Anex</b>\n\n"
        "Выберите, какие заявки хотите посмотреть.",
        reply_markup=admin_panel_kb(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admrev:list")
async def admrev_list(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    rows = db.list_reviews_newest_first(limit=25)
    back = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В админ‑панель", callback_data="admrev:panel")]
        ]
    )
    if not rows:
        await callback.message.answer(
            "⭐ <b>Отзывы</b>\n\nПока нет ни одного отзыва.",
            reply_markup=back,
        )
        await callback.answer()
        return
    await callback.message.answer(
        "⭐ <b>Управление отзывами</b>\n\nВыберите отзыв (последние 25):",
        reply_markup=admin_reviews_list_kb(rows),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admrev:open:"))
async def admrev_open(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    review_id = int(callback.data.split(":")[2])
    r = db.get_review(review_id)
    if not r:
        await callback.message.answer("Отзыв не найден.")
        await callback.answer()
        return
    await callback.message.answer(
        format_admin_review_caption(r),
        reply_markup=admin_review_manage_kb(review_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admrev:star:"))
async def admrev_star(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = callback.data.split(":")
    review_id = int(parts[2])
    stars = int(parts[3])
    if stars < 1 or stars > 5:
        await callback.answer()
        return
    r = db.get_review(review_id)
    if not r:
        await callback.answer("Отзыв удалён.", show_alert=True)
        return
    db.update_review_stars(review_id, stars)
    r = db.get_review(review_id)
    await callback.message.answer(
        f"Оценка обновлена.\n\n{format_admin_review_caption(r)}",
        reply_markup=admin_review_manage_kb(review_id),
    )
    await callback.answer("Сохранено")


@admin_router.callback_query(F.data.startswith("admrev:edittext:"))
async def admrev_edittext(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    review_id = int(callback.data.split(":")[2])
    r = db.get_review(review_id)
    if not r:
        await callback.answer("Отзыв не найден.", show_alert=True)
        return
    await state.set_state(AdminReviewForm.waiting_body)
    await state.update_data(adm_rev_id=review_id)
    await callback.message.answer(
        f"Отзыв №{review_id}. Отправьте новый текст одним сообщением.\n"
        "Чтобы убрать текст отзыва, отправьте «-»."
    )
    await callback.answer()


@admin_router.message(AdminReviewForm.waiting_body)
async def admrev_edittext_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    review_id = data.get("adm_rev_id")
    if not review_id:
        await state.clear()
        return
    r = db.get_review(review_id)
    if not r:
        await state.clear()
        await message.answer("Отзыв не найден.")
        return
    raw = (message.text or "").strip()
    if raw == "-":
        db.update_review_body(review_id, None)
    else:
        db.update_review_body(review_id, raw[:2000])
    await state.clear()
    r = db.get_review(review_id)
    await message.answer(
        "Текст обновлён.\n\n" + format_admin_review_caption(r),
        reply_markup=admin_review_manage_kb(review_id),
    )


@admin_router.callback_query(F.data.startswith("admrev:delask:"))
async def admrev_del_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    review_id = int(callback.data.split(":")[2])
    r = db.get_review(review_id)
    if not r:
        await callback.answer("Отзыв не найден.", show_alert=True)
        return
    await callback.message.answer(
        f"Удалить отзыв №{review_id}?",
        reply_markup=admin_review_delete_confirm_kb(review_id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admrev:delyes:"))
async def admrev_del_yes(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    review_id = int(callback.data.split(":")[2])
    db.delete_review(review_id)
    await state.clear()
    await callback.message.answer(f"Отзыв №{review_id} удалён.")
    rows = db.list_reviews_newest_first(limit=25)
    back = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В админ‑панель", callback_data="admrev:panel")]
        ]
    )
    if not rows:
        await callback.message.answer("⭐ Отзывов больше нет.", reply_markup=back)
    else:
        await callback.message.answer(
            "⭐ <b>Управление отзывами</b>",
            reply_markup=admin_reviews_list_kb(rows),
        )
    await callback.answer("Удалено")


# ------------------ ЗАПУСК БОТА ---------------------------


async def main():
    if BOT_TOKEN == "ВАШ_ТОКЕН_БОТА_ОТ_BOTFATHER":
        print("⚠️ Укажи реальный BOT_TOKEN в переменной окружения BOT_TOKEN.")
    dp.include_router(router)
    dp.include_router(admin_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
