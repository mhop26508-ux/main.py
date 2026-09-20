import asyncio
import html
import json
import psycopg2
import psycopg2.extras
import urllib.parse
import uuid
import re
import os

import aiohttp
from aiohttp import ClientOSError, web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# ================= الإعدادات =================
BOT_TOKEN = "8998872085:AAHOxb1Hny7c_8P0MTu2-lqObx-DX1AZr1s"
MHD_API_TOKEN = "T7um19tGzZVl2sdz09Z8WnlojsWn8TLDNwFrxT1qcvTBJgk6wtuxI6v9miom"

PRIMARY_ADMIN_ID = 8090426575
CHANNEL_USERNAME = "@SARE3_STOR"

API_BASE_URL = "https://mhd-game.com/api"
MHD_PRODUCTS_URL = f"{API_BASE_URL}/client/api/products"

DATABASE_URL = "postgresql://postgres.qmgvtflvgvosgseqmelf:Mlpoknbji0%24570@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"

USD_PAYMENT_METHODS = {"binance", "bep20", "sham_usd"}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ================= دالة مساعدة لتوزيع الأزرار شبكياً =================
def chunk_buttons(buttons, n=2):
    grid = []
    temp_row = []
    for btn in buttons:
        btn_text = getattr(btn, "text", "")
        if len(btn_text) > 16:
            if temp_row:
                grid.append(temp_row)
                temp_row = []
            grid.append([btn])
        else:
            temp_row.append(btn)
            if len(temp_row) == n:
                grid.append(temp_row)
                temp_row = []
    if temp_row:
        grid.append(temp_row)
    return grid


# ================= خادم الويب للإبقاء حياً على Render =================
async def health_check(request):
    return web.Response(text="Bot is running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Keep-Alive Web Server started on port {port}")


# ================= أدوات الاتصال السحابي =================
def db():
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


def esc(value):
    return html.escape(str(value if value is not None else ""))


def clean_name(name):
    if not name:
        return ""
    return str(name).replace("📁", "").replace("🗂️", "").strip()


def session(timeout=15):
    connector = aiohttp.TCPConnector(family=2, ssl=False, ttl_dns_cache=300, limit=10)
    return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout, connect=5, sock_connect=5))


async def safe_send(uid, text, reply_markup=None, retries=3):
    for attempt in range(retries):
        try:
            return await bot.send_message(uid, text, reply_markup=reply_markup, parse_mode="HTML")
        except TelegramNetworkError:
            if attempt == retries - 1:
                return None
            await asyncio.sleep(1)
        except Exception:
            return None


async def safe_answer(cb: types.CallbackQuery, text=None, alert=False):
    try:
        await cb.answer(text=text, show_alert=alert)
    except Exception:
        pass


# ================= قاعدة البيانات =================
def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY, username TEXT,
        balance REAL DEFAULT 0, is_admin BOOLEAN DEFAULT FALSE,
        is_banned BOOLEAN DEFAULT FALSE)""")

    # للتأكد من وجود عمود is_banned في حال كان الجدول منشأ سابقاً
    c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE")

    c.execute("""CREATE TABLE IF NOT EXISTS categories(
        id SERIAL PRIMARY KEY, name TEXT)""")

    c.execute("""CREATE TABLE IF NOT EXISTS subcategories(
        id SERIAL PRIMARY KEY, name TEXT, cat_id INTEGER)""")

    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id SERIAL PRIMARY KEY, mhd_id BIGINT, name TEXT,
        price REAL, product_type TEXT, available BOOLEAN, stock INTEGER,
        min_qty REAL, max_qty REAL, sub_id INTEGER,
        description TEXT, api_data TEXT)""")

    c.execute("""CREATE TABLE IF NOT EXISTS orders(
        id SERIAL PRIMARY KEY, order_uuid TEXT UNIQUE,
        user_id BIGINT, product_id INTEGER, product_name TEXT,
        price REAL, status TEXT, replay_api TEXT,
        qty REAL DEFAULT 1, player_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT)""")

    c.execute("""CREATE TABLE IF NOT EXISTS payment_methods(
        code TEXT PRIMARY KEY, name TEXT, info TEXT, active BOOLEAN)""")

    c.execute("""CREATE TABLE IF NOT EXISTS wishlist(
        user_id BIGINT, product_id INTEGER, PRIMARY KEY(user_id, product_id))""")

    c.execute("""CREATE TABLE IF NOT EXISTS deposits(
        id SERIAL PRIMARY KEY, user_id BIGINT, amount REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("INSERT INTO settings(key, value) VALUES('store_status', 'open') ON CONFLICT (key) DO NOTHING")
    c.execute("INSERT INTO settings(key, value) VALUES('store_margin_percent', '0') ON CONFLICT (key) DO NOTHING")
    c.execute("INSERT INTO settings(key, value) VALUES('exchange_rate', '15000') ON CONFLICT (key) DO NOTHING")
    c.execute("INSERT INTO settings(key, value) VALUES('support_contact', '@SARE3_570') ON CONFLICT (key) DO NOTHING")
    
    payments = [
        ('binance', 'Binance Pay', '1192954957', True),
        ('syriatel', 'سيرياتيل كاش', '67997320', True),
        ('sham_usd', 'شام كاش (دولار)', 'معرف شام كاش دولار هنا', True),
        ('sham_syp', 'شام كاش (ليرة سورية)', 'معرف شام كاش ليرة هنا', True),
        ('bep20', 'USDT BEP20', '0xe8688d65f474253e290c1b7491ee2b0799784ad0', True)
    ]
    for code, name, info, active in payments:
        c.execute("""INSERT INTO payment_methods(code, name, info, active) VALUES(%s, %s, %s, %s)
                     ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name""", (code, name, info, active))

    c.execute("""INSERT INTO users(user_id, username, balance, is_admin)
                 VALUES(%s, 'Owner', 1000000.0, TRUE)
                 ON CONFLICT (user_id) DO UPDATE SET is_admin=TRUE""", (PRIMARY_ADMIN_ID,))

    conn.commit()
    c.close()
    conn.close()


def is_admin(uid):
    if uid == PRIMARY_ADMIN_ID:
        return True
    conn = db()
    c = conn.cursor()
    c.execute("SELECT is_admin FROM users WHERE user_id=%s", (uid,))
    row = c.fetchone()
    c.close()
    conn.close()
    return bool(row and row[0])


def is_user_banned(uid):
    if uid == PRIMARY_ADMIN_ID:
        return False
    conn = db()
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id=%s", (uid,))
    row = c.fetchone()
    c.close()
    conn.close()
    return bool(row and row[0])


def set_user_ban_status(uid, status: bool):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned=%s WHERE user_id=%s", (status, uid))
    ok = c.rowcount > 0
    conn.commit()
    c.close()
    conn.close()
    return ok


def get_all_admins():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_admin=TRUE")
    rows = c.fetchall()
    c.close()
    conn.close()
    admin_set = {PRIMARY_ADMIN_ID}
    for (r,) in rows:
        admin_set.add(r)
    return list(admin_set)


def store_open():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='store_status'")
    row = c.fetchone()
    c.close()
    conn.close()
    return not row or row[0] == "open"


def get_margin_percent():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='store_margin_percent'")
    row = c.fetchone()
    c.close()
    conn.close()
    try:
        return float(row[0]) if row else 0.0
    except ValueError:
        return 0.0


def get_exchange_rate():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='exchange_rate'")
    row = c.fetchone()
    c.close()
    conn.close()
    try:
        return float(row[0]) if row else 15000.0
    except (ValueError, TypeError):
        return 15000.0


def get_support_contact():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='support_contact'")
    row = c.fetchone()
    c.close()
    conn.close()
    return row[0] if row and row[0] else "@SARE3_570"


def apply_margin(base_price):
    margin = get_margin_percent()
    base = float(base_price or 0)
    return base * (1 + margin / 100.0)


def user_init(uid, username=""):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=%s", (uid,))
    row = c.fetchone()
    if row is None:
        bal = 1000000.0 if uid == PRIMARY_ADMIN_ID else 0.0
        c.execute("INSERT INTO users(user_id, username, balance, is_admin, is_banned) VALUES(%s, %s, %s, %s, FALSE)",
                  (uid, username, bal, uid == PRIMARY_ADMIN_ID))
    else:
        bal = float(row[0] or 0)
        if username:
            c.execute("UPDATE users SET username=%s WHERE user_id=%s", (username, uid))
    conn.commit()
    c.close()
    conn.close()
    return bal


def get_user(uid):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT username, balance, is_banned FROM users WHERE user_id=%s", (uid,))
    row = c.fetchone()
    c.close()
    conn.close()
    return {"username": row[0] or "", "balance": float(row[1] or 0), "is_banned": bool(row[2])} if row else None


def add_balance(uid, amount):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (float(amount), uid))
    conn.commit()
    ok = c.rowcount > 0
    c.close()
    conn.close()
    return ok


def record_deposit(uid, amount):
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO deposits(user_id, amount) VALUES(%s, %s)", (uid, float(amount)))
    conn.commit()
    c.close()
    conn.close()


def generate_deposits_pdf():
    filename = "all_deposits_report.pdf"
    conn = db()
    c_cursor = conn.cursor()
    c_cursor.execute("""
        SELECT d.id, d.user_id, d.amount, d.created_at, COALESCE(u.username, '')
        FROM deposits d 
        LEFT JOIN users u ON d.user_id = u.user_id 
        ORDER BY d.id DESC
    """)
    rows = c_cursor.fetchall()
    c_cursor.close()
    conn.close()

    c = canvas.Canvas(filename, pagesize=letter)
    width, height = letter
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(180, height - 40, "All Deposits History Report")
    c.setFont("Helvetica-Bold", 10)
    y = height - 80
    c.drawString(40, y, "ID")
    c.drawString(90, y, "User ID")
    c.drawString(200, y, "Username")
    c.drawString(330, y, "Amount ($)")
    c.drawString(440, y, "Date & Time")
    y -= 15
    c.line(40, y, 570, y)
    y -= 20
    
    c.setFont("Helvetica", 10)
    for row in rows:
        if y < 50:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)
        did, uid, amt, created, uname = row[0], row[1], row[2], row[3], row[4]
        uname_clean = str(uname).strip()
        uname_str = f"@{uname_clean}" if uname_clean and uname_clean.lower() != "none" else "N/A"
        c.drawString(40, y, str(did))
        c.drawString(90, y, str(uid))
        c.drawString(200, y, str(uname_str))
        c.drawString(330, y, f"${amt:.4f}")
        c.drawString(440, y, str(created)[:19])
        y -= 20
    c.save()
    return filename


def categories():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id, name FROM categories ORDER BY id")
    rows = c.fetchall()
    c.close()
    conn.close()
    return [(cid, clean_name(cname)) for cid, cname in rows]


def subs(cat_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id, name FROM subcategories WHERE cat_id=%s ORDER BY id", (cat_id,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows


def products(sub_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT mhd_id, name FROM products WHERE sub_id=%s ORDER BY id", (sub_id,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows


def product(mhd_id):
    conn = db()
    c = conn.cursor()
    c.execute("""SELECT name, price, available, stock, min_qty, max_qty,
                          product_type, description, api_data
                          FROM products WHERE mhd_id=%s ORDER BY id DESC LIMIT 1""", (mhd_id,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row


def extract_quantities_from_text(text):
    if not text:
        return []
    numbers = re.findall(r'\d+(?:\.\d+)?', text)
    unique_nums = []
    seen = set()
    for n in numbers:
        val = float(n)
        if val not in seen and val > 0:
            seen.add(val)
            unique_nums.append(val)
    return sorted(unique_nums)


def norm_status(status):
    s = str(status or "").strip().lower()
    s = s.replace("_", " ").replace("-", " ")
    return " ".join(s.split())


def accepted(status):
    s = norm_status(status)
    exact = {
        "accept", "accepted", "complete", "completed",
        "success", "successful", "done", "finished",
        "delivered", "delivery", "مكتمل", "مكتملة",
        "ناجح", "تم التنفيذ", "تمت بنجاح", "منجز", "منتهي", "تم الشحن بنجاح"
    }
    if s in exact:
        return True
    return any(x in s for x in ("completed", "complete", "successful", "success", "delivered", "finished", "مكتمل", "ناجح", "بنجاح"))


def rejected(status):
    s = norm_status(status)
    exact = {
        "reject", "rejected", "refused", "refuse", "fail", "failed",
        "failure", "cancel", "canceled", "cancelled", "declined",
        "denied", "error", "مرفوض", "رفض", "ملغي", "ملغى", "فشل"
    }
    if s in exact:
        return True
    return any(x in s for x in ("rejected", "refused", "failed", "cancelled", "canceled", "مرفوض", "فشل"))


def replay(info):
    if not info:
        return ""
    if isinstance(info, list):
        return "\n".join([str(x) for x in info if x])
    if not isinstance(info, dict):
        return str(info).strip()

    for key in ("replay_api", "account_data", "accountData", "replay", "reply", "result", "notes", "note", "response", "code"):
        value = info.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            return "\n".join([str(x) for x in value if x])
        if isinstance(value, dict):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        text = str(value).strip()
        if text and text.lower() not in {"none", "null", "ok", "success"}:
            return text

    value = info.get("data")
    if isinstance(value, list):
        return "\n".join([str(x) for x in value if x])
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value).strip() if value else ""


def find_dicts(obj):
    found = []
    def walk(v):
        if isinstance(v, dict):
            found.append(v)
            for x in v.values():
                if isinstance(x, (dict, list)):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, (dict, list)):
                    walk(x)
    walk(obj)
    return found


def matching_order(payload, wanted_uuid):
    wanted = str(wanted_uuid).lower().strip()
    candidates = find_dicts(payload)
    for item in candidates:
        for key in ("order_uuid", "orderUuid", "order_id", "orderId", "uuid", "id"):
            if item.get(key) is not None and str(item[key]).lower().strip() == wanted:
                return item
    if isinstance(payload, dict) and "data" in payload:
        if isinstance(payload["data"], list) and len(payload["data"]) > 0:
            return payload["data"][0]
        elif isinstance(payload["data"], dict):
            return payload["data"]
    if candidates:
        for c in candidates:
            if "status" in c or "order_status" in c:
                return c
    return payload if isinstance(payload, dict) else None


async def check_order_api(order_uuid, client=None):
    url = f"{API_BASE_URL}/client/api/check?orders=[{urllib.parse.quote(str(order_uuid), safe='')}]&uuid=1"
    headers = {"api-token": MHD_API_TOKEN, "Accept": "application/json"}
    own = client is None
    client = client or session(15)
    try:
        async with client.get(url, headers=headers) as r:
            raw = await r.text()
            if r.status != 200:
                return None
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return None
            info = matching_order(payload, order_uuid)
            st = ""
            rep = ""
            if isinstance(info, dict):
                st = (info.get("status") or info.get("order_status") or info.get("orderStatus") or info.get("state") or "")
                rep = replay(info)
            if not st and isinstance(payload, dict):
                st = payload.get("status", "")
            return {"status": str(st or ""), "replay": rep, "raw": payload}
    except Exception:
        return None
    finally:
        if own:
            await client.close()


def reject_once(order_id, uid, amount, rep):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE orders SET status='reject', replay_api=%s WHERE id=%s AND status NOT IN ('accept','reject')", (rep, order_id))
    changed = c.rowcount
    if changed:
        c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (float(amount), uid))
    conn.commit()
    c.close()
    conn.close()
    return changed > 0


def accept_once(order_id, rep):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE orders SET status='accept', replay_api=%s WHERE id=%s AND status NOT IN ('accept','reject')", (rep, order_id))
    changed = c.rowcount
    conn.commit()
    c.close()
    conn.close()
    return changed > 0


# ================= FSM =================
class AdminStates(StatesGroup):
    add_cat_name = State()
    edit_cat_select = State()
    edit_cat_new_name = State()
    edit_sub_select_cat = State()
    edit_sub_select = State()
    edit_sub_new_name = State()
    edit_prod_select_cat = State()
    edit_prod_select_sub = State()
    edit_prod_select = State()
    edit_prod_new_name = State()
    edit_prod_desc_cat = State()
    edit_prod_desc_sub = State()
    edit_prod_desc_pick = State()
    edit_prod_desc_new = State()
    edit_support_contact = State()
    add_sub_select_cat = State()
    add_sub_name = State()
    add_prod_select_cat = State()
    add_prod_select_sub = State()
    add_prod_id = State()
    wait_deposit_amount = State()
    wait_add_user = State()
    wait_add_amount = State()
    wait_deduct_user = State()
    wait_deduct_amount = State()
    wait_ban_user = State()
    wait_unban_user = State()
    wait_new_admin_id = State()
    wait_del_admin_id = State()
    wait_payment_info_edit = State()
    wait_new_pay_code = State()
    wait_new_pay_name = State()
    wait_new_pay_info = State()
    edit_price_percentage = State()
    edit_exchange_rate = State()
    wait_search_player_id = State()
    broadcast_message = State()


class ClientStates(StatesGroup):
    wait_for_qty = State()
    wait_for_param = State()
    wait_trans_id = State()
    wait_dep_amount = State()
    wait_product_search = State()


# ================= التحقق العام من الحظر =================
@dp.message.outer_middleware()
async def ban_check_middleware(handler, event: types.TelegramObject, data: dict):
    user = getattr(event, "from_user", None)
    if user and is_user_banned(user.id):
        if isinstance(event, types.Message):
            await event.answer("🚫 <b>تم حظر حسابك من استخدام هذا البوت. يرجى التواصل مع الإدارة.</b>", parse_mode="HTML")
        elif isinstance(event, types.CallbackQuery):
            await event.answer("🚫 تم حظر حسابك من البوت.", show_alert=True)
        return
    return await handler(event, data)


# ================= لوحات المفاتيح =================
def main_kb(uid):
    kb = [
        [types.KeyboardButton(text="🛍️ قسم الخدمات", style="primary"), types.KeyboardButton(text="💰 حسابي ورصيدي", style="success")],
        [types.KeyboardButton(text="🔍 بحث عن منتج", style="primary"), types.KeyboardButton(text="⭐ المفضلة", style="success")],
        [types.KeyboardButton(text="➕ شحن الرصيد", style="success"), types.KeyboardButton(text="📋 طلباتي", style="primary"), types.KeyboardButton(text="📞 التواصل مع الدعم", style="danger")]
    ]
    if is_admin(uid):
        kb.append([types.KeyboardButton(text="⚙️ لوحة الإدارة", style="primary")])
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def admin_kb():
    state_txt = "🟢 مفتوح" if store_open() else "🔴 مغلق"
    margin = get_margin_percent()
    rate = get_exchange_rate()
    kb = [
        # إدارة الأقسام
        [types.KeyboardButton(text="➕ إضافة قسم رئيسي", style="success"), types.KeyboardButton(text="✏️ تعديل اسم قسم", style="primary"), types.KeyboardButton(text="🗑️ إزالة قسم رئيسي", style="danger")],
        # إدارة الألعاب
        [types.KeyboardButton(text="➕ إضافة لعبة لقسم", style="success"), types.KeyboardButton(text="✏️ تعديل اسم لعبة", style="primary"), types.KeyboardButton(text="🗑️ إزالة لعبة من قسم", style="danger")],
        # إدارة المنتجات
        [types.KeyboardButton(text="➕ إضافة منتجات للعبة", style="success"), types.KeyboardButton(text="🔄 تفعيل / تعطيل منتج", style="primary")],
        [types.KeyboardButton(text="✏️ تعديل اسم منتج", style="primary"), types.KeyboardButton(text="📝 تعديل وصف منتج", style="primary"), types.KeyboardButton(text="🗑️ حذف منتج من لعبة", style="danger")],
        # المالية والأرصدة
        [types.KeyboardButton(text="➕ إضافة رصيد لعميل", style="success"), types.KeyboardButton(text="🔻 سحب/خصم رصيد عميل", style="danger")],
        [types.KeyboardButton(text=f"💱 سعر الصرف ({rate:,.0f} ل.س)", style="primary"), types.KeyboardButton(text=f"📈 تعديل أسعار ({margin:g}%)", style="primary")],
        # إدارة الأعضاء والحظر
        [types.KeyboardButton(text="🚫 حظر مستخدم", style="danger"), types.KeyboardButton(text="🟢 فك حظر مستخدم", style="success")],
        [types.KeyboardButton(text="👤 إضافة أدمن جديد", style="primary"), types.KeyboardButton(text="🚫 إزالة أدمن", style="danger")],
        # النظام والإحصائيات
        [types.KeyboardButton(text=f"🏪 حالة المتجر: {state_txt}", style="success" if store_open() else "danger"), types.KeyboardButton(text="📋 الطلبات الكلية", style="primary"), types.KeyboardButton(text="📊 إحصائيات المتجر", style="primary")],
        [types.KeyboardButton(text="💳 إدارة طرق الدفع", style="primary"), types.KeyboardButton(text="📞 تعديل حساب الدعم", style="primary")],
        [types.KeyboardButton(text="📢 إرسال رسالة للكل", style="primary"), types.KeyboardButton(text="🔙 رجوع للرئيسية", style="danger")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def fetch_product(pid):
    try:
        async with session(15) as s:
            async with s.get(MHD_PRODUCTS_URL, headers={"api-token": MHD_API_TOKEN}) as r:
                raw = await r.text()
                if r.status != 200:
                    return None
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return None
        if isinstance(payload, list):
            arr = payload
        elif isinstance(payload, dict):
            arr = []
            for key in ("data", "products", "result"):
                if isinstance(payload.get(key), list):
                    arr = payload[key]
                    break
            if not arr:
                for key in ("data", "product", "result"):
                    if isinstance(payload.get(key), dict):
                        arr = [payload[key]]
                        break
        else:
            arr = []
        for p in arr:
            if isinstance(p, dict):
                x = p.get("id") or p.get("product_id") or p.get("productId")
                if str(x) == str(pid):
                    p_copy = dict(p)
                    if "price" in p_copy:
                        p_copy["price"] = apply_margin(p_copy["price"])
                    return p_copy
    except Exception:
        pass
    return None


def required_params(data):
    if not isinstance(data, dict):
        return []
    p = data.get("params")
    if isinstance(p, list) and p:
        out = []
        for x in p:
            if isinstance(x, dict):
                name = x.get("name") or x.get("label") or x.get("key") or x.get("param")
                if name:
                    out.append(str(name))
            elif x:
                out.append(str(x))
        if out:
            return out
    out = []
    def is_true(val):
        return str(val).lower() in ("1", "true", "yes")
    player_id_keys = ["player_id_required", "require_player_id", "playerId_required", "need_player_id", "is_player_id", "require_id"]
    if any(is_true(data.get(k)) for k in player_id_keys):
        out.append("Player ID")
    zone_id_keys = ["require_zone_id", "has_zone_id", "zone_id_required", "need_zone_id", "is_zone_id", "server_id_required"]
    if any(is_true(data.get(k)) for k in zone_id_keys):
        out.append("Zone ID")
    ptype = str(data.get("product_type") or data.get("type") or "").lower()
    pname = str(data.get("name") or "").lower()
    keywords = ["شدة", "شدات", "uc", "pubg", "ببجي", "free fire", "جواهر", "id", "player", "game", "topup"]
    if not out and (any(t in ptype for t in ["game", "player", "topup", "id"]) or any(k in pname for k in keywords)):
        out.append("Player ID")
    return out


def db_product_data(row):
    if not row:
        return {}
    try:
        data = json.loads(row[8] or "{}")
        if row:
            data["name"] = row[0]
            data["description"] = row[7] or ""
            data["product_type"] = row[6] or ""
        return data
    except Exception:
        return {"name": row[0] if row else "", "description": row[7] if row else "", "product_type": row[6] if row else ""}


async def auto_send_deposits_pdf_task():
    while True:
        await asyncio.sleep(12 * 3600)
        try:
            pdf_file = generate_deposits_pdf()
            if os.path.exists(pdf_file):
                await bot.send_document(PRIMARY_ADMIN_ID, types.FSInputFile(pdf_file), caption="📊 <b>تقرير تلقائي:</b> ملف PDF يحتوي على سجل جميع عمليات إضافة الرصيد.")
        except Exception as e:
            print(f"⚠️ خطأ إرسال PDF: {e}")


@dp.message(F.text == "🔙 إلغاء الشراء")
async def cancel_any_action(message: types.Message, state: FSMContext):
    await state.clear()
    await safe_send(message.from_user.id, "🏠 تم الإلغاء والعودة للقائمة الرئيسية.", reply_markup=main_kb(message.from_user.id))


@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    user_init(message.from_user.id, message.from_user.username or message.from_user.first_name or "")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="✅ أوافق", callback_data="agree_policy", style="success")]])
    await safe_send(
        message.from_user.id,
        "🔹<b>سياسة الخصوصية والاستخدام</b>\nباستخدامك لهذا البوت، فإنك توافق على شروط الاستخدام:\n1. المدفوعات غير قابلة للاسترداد إلا عند رفض الطلب تلقائياً.\n2. نحترم خصوصيتك ولا ننشر بياناتك لطرف آخر.\n3. نسعى لتقديم خدمة سريعة وجودة عالية.",
        reply_markup=kb
    )


@dp.callback_query(F.data == "agree_policy")
async def agree(cb: types.CallbackQuery):
    await safe_answer(cb)
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="📢 قناة البوت", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}", style="primary")],
            [types.InlineKeyboardButton(text="🔄 تحقق من الاشتراك", callback_data="check_sub", style="success")]
        ]
    )
    try:
        await cb.message.edit_text("يرجى الاشتراك في قناة البوت ثم اضغط تحقق.", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data == "check_sub")
async def check_sub(cb: types.CallbackQuery):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=cb.from_user.id)
        if member.status in ("member", "administrator", "creator"):
            await safe_answer(cb, "✅ تم التحقق من الاشتراك بنجاح!")
            try:
                await cb.message.delete()
            except Exception:
                pass
            await safe_send(cb.from_user.id, "👋 أهلاً بك في متجرنا! 🛒", reply_markup=main_kb(cb.from_user.id))
        else:
            await cb.answer("❌ لم تقم بالاشتراك في القناة بعد!", show_alert=True)
    except Exception:
        await cb.answer("⚠️ تأكد من اشتراكك بالقناة وأن البوت أدمن فيها.", show_alert=True)


@dp.message(F.text == "🔙 رجوع للرئيسية")
async def back_main(message: types.Message, state: FSMContext):
    await state.clear()
    await safe_send(message.from_user.id, "🏠 تم العودة للقائمة الرئيسية.", reply_markup=main_kb(message.from_user.id))


@dp.message(F.text == "💰 حسابي ورصيدي")
async def balance(message: types.Message):
    b = user_init(message.from_user.id, message.from_user.username or message.from_user.first_name or "")
    role = "مدير المتجر 👑" if is_admin(message.from_user.id) else "عميل 👤"
    await safe_send(
        message.from_user.id,
        f"💳 <b>معلومات حسابك:</b>\n\n"
        f"👤 الرتبة: {role}\n"
        f"💵 الرصيد: <code>${b:.4f}</code>"
    )


@dp.message(F.text == "📞 التواصل مع الدعم")
async def contact_support(message: types.Message):
    sup_contact = get_support_contact()
    await safe_send(
        message.from_user.id,
        "📞 <b>للتواصل مع الدعم الفني:</b>\n\n"
        "في حال حدوث أي مشكلة أو للاستفسار، يرجى مراسلة الدعم عبر المعرف التالي:\n"
        f"👉 {esc(sup_contact)}"
    )


# ================= نظام البحث المباشر عن المنتجات =================
@dp.message(F.text == "🔍 بحث عن منتج")
async def search_product_start(message: types.Message, state: FSMContext):
    await state.clear()
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(message.from_user.id, "🔍 <b>أرسل اسم المنتج أو اللعبة التي تبحث عنها:</b>", reply_markup=cancel_kb)
    await state.set_state(ClientStates.wait_product_search)


@dp.message(ClientStates.wait_product_search)
async def search_product_execute(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم إلغاء البحث.", reply_markup=main_kb(message.from_user.id))
    
    query = message.text.strip()
    await state.clear()
    if not query:
        return await safe_send(message.from_user.id, "❌ يرجى كتابة نص صالح للبحث.", reply_markup=main_kb(message.from_user.id))

    conn = db()
    c = conn.cursor()
    c.execute("SELECT mhd_id, name, price FROM products WHERE name ILIKE %s LIMIT 10", (f"%{query}%",))
    rows = c.fetchall()
    c.close()
    conn.close()

    if not rows:
        return await safe_send(message.from_user.id, f"❌ لم يتم العثور على أي منتج يطابق: <b>{esc(query)}</b>", reply_markup=main_kb(message.from_user.id))

    btn_list = []
    for pid, name, base_price in rows:
        p_final = apply_margin(base_price)
        btn_list.append(types.InlineKeyboardButton(text=f"📦 {name} (${p_final:.4f})", callback_data=f"show_{pid}", style="primary"))
    
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, f"🔍 <b>نتائج البحث عن:</b> {esc(query)}", reply_markup=main_kb(message.from_user.id))
    await safe_send(message.from_user.id, "اختر المنتج الذي ترغب به:", reply_markup=kb)


# ================= المفضلة =================
@dp.message(F.text == "⭐ المفضلة")
async def show_wishlist(message: types.Message):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT product_id FROM wishlist WHERE user_id=%s", (message.from_user.id,))
    rows = c.fetchall()
    c.close()
    conn.close()

    if not rows:
        return await safe_send(message.from_user.id, "⭐ قائمة المفضلة لديك فارغة حالياً.")

    keyboard = []
    for (pid,) in rows:
        p_row = product(pid)
        if p_row:
            name = p_row[0]
            keyboard.append([
                types.InlineKeyboardButton(text=f"📦 {name}", callback_data=f"show_{pid}", style="primary"),
                types.InlineKeyboardButton(text="🗑️ إزالة", callback_data=f"delwish_{pid}", style="danger")
            ])
        else:
            conn = db()
            c = conn.cursor()
            c.execute("DELETE FROM wishlist WHERE user_id=%s AND product_id=%s", (message.from_user.id, pid))
            conn.commit()
            c.close()
            conn.close()

    if not keyboard:
        return await safe_send(message.from_user.id, "⭐ قائمة المفضلة لديك فارغة حالياً.")

    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "⭐ <b>قائمة منتجاتك المفضلة:</b>", reply_markup=kb)


@dp.callback_query(F.data.startswith("addwish_"))
async def add_wishlist_cb(cb: types.CallbackQuery):
    await safe_answer(cb)
    pid = cb.data.split("_")[1]
    conn = db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO wishlist(user_id, product_id) VALUES(%s, %s) ON CONFLICT DO NOTHING", (cb.from_user.id, pid))
        conn.commit()
        await cb.answer("⭐ تم إضافة المنتج إلى المفضلة!", show_alert=True)
    except Exception:
        await cb.answer("⚠️ المنتج موجود في المفضلة.", show_alert=True)
    finally:
        c.close()
        conn.close()


@dp.callback_query(F.data.startswith("delwish_"))
async def del_wishlist_cb(cb: types.CallbackQuery):
    await safe_answer(cb)
    pid = cb.data.split("_")[1]
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM wishlist WHERE user_id=%s AND product_id=%s", (cb.from_user.id, pid))
    conn.commit()
    c.close()
    conn.close()
    await cb.answer("🗑️ تم إزالة المنتج من المفضلة.", show_alert=True)
    try:
        await cb.message.delete()
    except Exception:
        pass


# ================= لوحة إحصائيات المتجر =================
@dp.message(F.text == "📊 إحصائيات المتجر")
async def admin_statistics(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT SUM(balance) FROM users")
    total_balance = cur.fetchone()[0] or 0.0
    cur.execute("SELECT COUNT(*) FROM orders WHERE status ILIKE '%accept%' OR status ILIKE '%complete%' OR status ILIKE '%success%'")
    completed_orders = cur.fetchone()[0]
    cur.execute("SELECT SUM(price) FROM orders WHERE status ILIKE '%accept%' OR status ILIKE '%complete%' OR status ILIKE '%success%'")
    total_sales = cur.fetchone()[0] or 0.0
    cur.execute("""
        SELECT d.user_id, d.amount, d.created_at, COALESCE(u.username, '') 
        FROM deposits d 
        LEFT JOIN users u ON d.user_id = u.user_id 
        ORDER BY d.id DESC LIMIT 10
    """)
    deposits_rows = cur.fetchall()
    cur.close()
    conn.close()

    text = (
        f"📊 <b>إحصائيات وتقارير المتجر:</b>\n\n"
        f"👥 إجمالي المستخدمين: <b>{total_users}</b>\n"
        f"💰 إجمالي أرصدة الأعضاء: <b>${total_balance:.4f}</b>\n"
        f"📦 الطلبات المكتملة: <b>{completed_orders}</b>\n"
        f"💵 إجمالي المبيعات: <b>${total_sales:.4f}</b>\n\n"
        f"💳 <b>آخر عمليات الشحن (10):</b>\n"
    )
    if not deposits_rows:
        text += "<i>لا توجد عمليات شحن مسجلة بعد.</i>"
    else:
        for uid, amt, created_at, uname in deposits_rows:
            uname_clean = str(uname).strip()
            uname_str = f"@{uname_clean}" if uname_clean and uname_clean.lower() != "none" else f"ID: {uid}"
            text += f"▪️ {uname_str} (<code>{uid}</code>) — أضاف <b>${amt:.4f}</b> ({created_at})\n"

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="📥 تحميل تقرير PDF الشامل", callback_data="download_pdf_report", style="success")]])
    await safe_send(message.from_user.id, text, reply_markup=kb)


@dp.callback_query(F.data == "download_pdf_report")
async def download_pdf_cb(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    pdf_file = generate_deposits_pdf()
    if os.path.exists(pdf_file):
        await bot.send_document(cb.from_user.id, types.FSInputFile(pdf_file), caption="📂 <b>تقرير عمليات شحن الرصيد الشامل (PDF)</b>")
    else:
        await cb.answer("❌ حدث خطأ أثناء إنشاء ملف PDF.", show_alert=True)


# ================= نظام حظر وفك حظر المستخدمين =================
@dp.message(F.text == "🚫 حظر مستخدم")
async def ban_user_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(message.from_user.id, "🚫 <b>أرسل User ID الخاص بالمستخدم المراد حظره:</b>", reply_markup=cancel_kb)
    await state.set_state(AdminStates.wait_ban_user)


@dp.message(AdminStates.wait_ban_user)
async def ban_user_finish(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    if not message.text.strip().isdigit():
        return await safe_send(message.from_user.id, "❌ يرجى إرسال آيدي رقمي صحيح.")
    uid = int(message.text.strip())
    if uid == PRIMARY_ADMIN_ID or is_admin(uid):
        return await safe_send(message.from_user.id, "❌ لا يمكن حظر الأدمن أو المالك.")
    
    ok = set_user_ban_status(uid, True)
    await state.clear()
    if ok:
        await safe_send(message.from_user.id, f"✅ <b>تم حظر المستخدم بنجاح!</b> (ID: <code>{uid}</code>)", reply_markup=admin_kb())
        await safe_send(uid, "🚫 <b>تم حظر حسابك من قبل إدارة المتجر.</b>")
    else:
        await safe_send(message.from_user.id, f"❌ المستخدم <code>{uid}</code> غير مسجل في قاعدة البيانات.", reply_markup=admin_kb())


@dp.message(F.text == "🟢 فك حظر مستخدم")
async def unban_user_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(message.from_user.id, "🟢 <b>أرسل User ID الخاص بالمستخدم المراد فك الحظر عنه:</b>", reply_markup=cancel_kb)
    await state.set_state(AdminStates.wait_unban_user)


@dp.message(AdminStates.wait_unban_user)
async def unban_user_finish(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    if not message.text.strip().isdigit():
        return await safe_send(message.from_user.id, "❌ يرجى إرسال آيدي رقمي صحيح.")
    uid = int(message.text.strip())
    ok = set_user_ban_status(uid, False)
    await state.clear()
    if ok:
        await safe_send(message.from_user.id, f"✅ <b>تم فك الحظر عن المستخدم بنجاح!</b> (ID: <code>{uid}</code>)", reply_markup=admin_kb())
        await safe_send(uid, "🎉 <b>تم رفع الحظر عن حسابك، يمكنك الآن استخدام البوت بحرية!</b>", reply_markup=main_kb(uid))
    else:
        await safe_send(message.from_user.id, f"❌ المستخدم <code>{uid}</code> غير مسجل في قاعدة البيانات.", reply_markup=admin_kb())


# ================= الإذاعة =================
@dp.message(F.text == "📢 إرسال رسالة للكل")
async def broadcast_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(message.from_user.id, "📢 <b>أرسل الرسالة التي تريد إذاعتها للجميع:</b>", reply_markup=cancel_kb)
    await state.set_state(AdminStates.broadcast_message)


@dp.message(AdminStates.broadcast_message)
async def broadcast_execute(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم إلغاء الإذاعة.", reply_markup=admin_kb())

    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned=FALSE")
    users_rows = c.fetchall()
    c.close()
    conn.close()

    total_users = len(users_rows)
    success_count = 0
    fail_count = 0
    await safe_send(message.from_user.id, f"⏳ جاري بدء الإذاعة لـ {total_users} مستخدم...")

    for (uid,) in users_rows:
        try:
            await message.send_copy(chat_id=uid)
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1

    await state.clear()
    await safe_send(
        message.from_user.id,
        f"✅ <b>تمت عملية الإذاعة!</b>\n\n👥 الإجمالي: {total_users}\n🟢 تم الإرسال: {success_count}\n🔴 فشل: {fail_count}",
        reply_markup=admin_kb()
    )


# ================= طلباتي للعميل =================
@dp.message(F.text == "📋 طلباتي")
async def my_orders(message: types.Message):
    sup_contact = get_support_contact()
    conn = db()
    c = conn.cursor()
    c.execute("""SELECT id, order_uuid, product_name, price, status, replay_api, qty, player_id, created_at
                           FROM orders WHERE user_id=%s ORDER BY id DESC LIMIT 5""", (message.from_user.id,))
    rows = c.fetchall()
    c.close()
    conn.close()

    if not rows:
        return await safe_send(message.from_user.id, f"ليس لديك أي طلبات سابقة.\n\nللدعم: {esc(sup_contact)}")

    for oid, ouuid, name, price, st, rep, qty, player, created_at in rows:
        status_txt = "🟢 مكتمل" if accepted(st) else ("🔴 مرفوض" if rejected(st) else "⏳ قيد الانتظار")
        qty_val = float(qty or 1)
        total_price = float(price or 0)
        unit_price = total_price / qty_val if qty_val > 0 else total_price

        order_card = (
            f"📦 <b>المنتج:</b> {esc(name)}\n"
            f"🔢 <b>رقم الطلب:</b> #{oid}\n"
            f"📊 <b>الحالة:</b> {status_txt}\n"
        )
        if player and str(player).strip():
            order_card += f"👤 <b>Player ID:</b> <code>{esc(player)}</code>\n"
        order_card += (
            f"🔢 <b>الكمية:</b> {qty_val:g}\n"
            f"🔑 <b>order_uuid:</b> <code>{esc(ouuid)}</code>\n"
            f"💵 <b>سعر الوحدة:</b> ${unit_price:.4f}\n"
            f"💰 <b>الإجمالي:</b> ${total_price:.4f}\n"
        )
        if created_at:
            order_card += f"📅 <b>التاريخ:</b> {esc(created_at)}\n"
        if rep and str(rep).strip().lower() not in {"none", "null", "[]", "{}"}:
            order_card += f"\n🗄️ <b>بيانات الحساب / الرد:</b>\n<code>{esc(rep)}</code>\n"
        order_card += "-----------------------------------"
        await safe_send(message.from_user.id, order_card)


# ================= عرض طلبات الإدارة والبحث الذكي الشامل =================
def build_admin_orders_view(page=1, search_query=None):
    PER_PAGE = 5
    offset = (page - 1) * PER_PAGE

    conn = db()
    cur = conn.cursor()

    if search_query:
        is_num = str(search_query).strip().isdigit()
        if is_num:
            search_uid = int(str(search_query).strip())
            count_query = "SELECT COUNT(*) FROM orders WHERE player_id ILIKE %s OR user_id = %s"
            orders_query = """
                SELECT id, order_uuid, user_id, product_name, price, status, replay_api, qty, player_id, created_at
                FROM orders WHERE player_id ILIKE %s OR user_id = %s ORDER BY id DESC LIMIT %s OFFSET %s
            """
            params_count = (f"%{search_query}%", search_uid)
            params_rows = (f"%{search_query}%", search_uid, PER_PAGE, offset)
        else:
            count_query = "SELECT COUNT(*) FROM orders WHERE player_id ILIKE %s"
            orders_query = """
                SELECT id, order_uuid, user_id, product_name, price, status, replay_api, qty, player_id, created_at
                FROM orders WHERE player_id ILIKE %s ORDER BY id DESC LIMIT %s OFFSET %s
            """
            params_count = (f"%{search_query}%",)
            params_rows = (f"%{search_query}%", PER_PAGE, offset)
        
        cur.execute(count_query, params_count)
        total_count = cur.fetchone()[0]
        cur.execute(orders_query, params_rows)
        rows = cur.fetchall()
    else:
        cur.execute("SELECT COUNT(*) FROM orders")
        total_count = cur.fetchone()[0]
        cur.execute("""
            SELECT id, order_uuid, user_id, product_name, price, status, replay_api, qty, player_id, created_at
            FROM orders ORDER BY id DESC LIMIT %s OFFSET %s
        """, (PER_PAGE, offset))
        rows = cur.fetchall()

    cur.close()
    conn.close()

    total_pages = max(1, (total_count + PER_PAGE - 1) // PER_PAGE)

    if not rows:
        text = "⚠️ لا توجد أي طلبات مسجلة حالياً." if not search_query else f"⚠️ لم يتم العثور على أي طلب للآيدي المدخل: <code>{esc(search_query)}</code>"
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="🔍 بحث حسب آيدي العميل أو اللاعب", callback_data="admin_search_pid", style="primary")],
                [types.InlineKeyboardButton(text="🔙 إلغاء البحث", callback_data="admin_orders_page_1", style="danger")]
            ]
        )
        return text, kb

    text = f"📋 <b>قائمة جميع الطلبات</b> (الإجمالي: {total_count})\n"
    if search_query:
        text += f"🔍 تصفية حسب: <code>{esc(search_query)}</code>\n"
    text += f"الصفحة {page} من {total_pages}\n"
    text += "-----------------------------------\n\n"

    for oid, ouuid, uid, name, price, st, rep, qty, player, created_at in rows:
        status_txt = "🟢 مكتمل" if accepted(st) else ("🔴 مرفوض" if rejected(st) else "⏳ قيد الانتظار")
        qty_val = float(qty or 1)
        total_price = float(price or 0)

        text += (
            f"🆔 <b>رقم الطلب:</b> #{oid}\n"
            f"👤 <b>آيدي العميل:</b> <code>{uid}</code>\n"
            f"📦 <b>المنتج:</b> {esc(name)}\n"
            f"📊 <b>الحالة:</b> {status_txt}\n"
            f"🔢 <b>الكمية:</b> {qty_val:g}\n"
            f"💰 <b>الإجمالي:</b> ${total_price:.4f}\n"
        )
        if player and str(player).strip():
            text += f"🎮 <b>Player ID:</b> <code>{esc(player)}</code>\n"
        text += f"🔑 <b>UUID:</b> <code>{esc(ouuid)}</code>\n"
        if created_at:
            text += f"📅 <b>التاريخ:</b> {esc(created_at)}\n"
        if rep and str(rep).strip().lower() not in {"none", "null", "[]", "{}"}:
            text += f"📝 <b>بيانات الرد:</b> <code>{esc(rep)}</code>\n"
        text += "-----------------------------------\n"

    nav_btns = []
    p_param = f"_{urllib.parse.quote(search_query)}" if search_query else ""
    if page > 1:
        nav_btns.append(types.InlineKeyboardButton(text="⬅️ السابق", callback_data=f"admin_orders_page_{page-1}{p_param}", style="primary"))
    if page < total_pages:
        nav_btns.append(types.InlineKeyboardButton(text="التالي ➡️", callback_data=f"admin_orders_page_{page+1}{p_param}", style="primary"))

    inline_keyboard = []
    if nav_btns:
        inline_keyboard.append(nav_btns)
    inline_keyboard.append([types.InlineKeyboardButton(text="🔍 بحث حسب آيدي العميل أو اللاعب", callback_data="admin_search_pid", style="primary")])
    if search_query:
        inline_keyboard.append([types.InlineKeyboardButton(text="🔄 إلغاء التصفية (عرض الكل)", callback_data="admin_orders_page_1", style="danger")])

    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    return text, kb


@dp.message(F.text == "📋 الطلبات الكلية")
async def show_all_orders_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text, kb = build_admin_orders_view(page=1)
    await safe_send(message.from_user.id, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("admin_orders_page_"))
async def admin_orders_page_cb(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    data_parts = cb.data.replace("admin_orders_page_", "").split("_", 1)
    page = int(data_parts[0])
    search_pid = urllib.parse.unquote(data_parts[1]) if len(data_parts) > 1 else None
    text, kb = build_admin_orders_view(page=page, search_query=search_pid)
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data == "admin_search_pid")
async def admin_search_pid_cb(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(cb.from_user.id, "أرسل <b>آيدي العميل (User ID)</b> أو <b>آيدي اللاعب (Player ID)</b> للبحث:", reply_markup=cancel_kb)
    await state.set_state(AdminStates.wait_search_player_id)


@dp.message(AdminStates.wait_search_player_id)
async def process_pid_search(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم إلغاء البحث.", reply_markup=admin_kb())
    pid_term = message.text.strip()
    await state.clear()
    text, kb = build_admin_orders_view(page=1, search_query=pid_term)
    await safe_send(message.from_user.id, text, reply_markup=kb)


# ================= تعديل سعر الصرف =================
@dp.message(F.text.startswith("💱 سعر الصرف"))
async def edit_exchange_rate_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    current_rate = get_exchange_rate()
    await safe_send(
        message.from_user.id,
        f"💱 <b>تعديل سعر صرف الدولار مقابل الليرة السورية:</b>\n\n"
        f"السعر الحالي: <b>1$ = {current_rate:,.0f} ل.س</b>\n\n"
        "أرسل سعر الصرف الجديد (كم ليرة سورية تساوي 1 دولار؟ مثلاً اكتب: <code>15200</code>):"
    )
    await state.set_state(AdminStates.edit_exchange_rate)


@dp.message(AdminStates.edit_exchange_rate)
async def edit_exchange_rate_finish(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        val = float(message.text.strip().replace(",", ""))
        if val <= 0:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ يرجى كتابة رقم صحيح لسعر الصرف.")
    
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO settings(key, value) VALUES('exchange_rate', %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (str(val),))
    conn.commit()
    c.close()
    conn.close()

    await safe_send(message.from_user.id, f"✅ <b>تم تحديث سعر الصرف بنجاح!</b>\nالآن 1$ = <b>{val:,.0f} ل.س</b>", reply_markup=admin_kb())
    await state.clear()


# ================= تصفح الخدمات =================
@dp.message(F.text == "🛍️ قسم الخدمات")
async def show_cats(message: types.Message):
    if not store_open() and not is_admin(message.from_user.id):
        return await safe_send(message.from_user.id, "⚠️ المتجر مغلق حالياً للصيانة.")
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "لا توجد أقسام حالياً.")
    
    btn_list = [types.InlineKeyboardButton(text=f"{clean_name(name)}", callback_data=f"maincat_{cid}", style="success") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "🛒 <b>أقسام المتجر:</b>\nاختر القسم:", reply_markup=kb)


@dp.callback_query(F.data == "back_to_main_cats")
async def back_cats(cb: types.CallbackQuery):
    await safe_answer(cb)
    rows = categories()
    if not rows:
        return await cb.answer("لا توجد أقسام.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"{clean_name(name)}", callback_data=f"maincat_{cid}", style="success") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("🛒 <b>أقسام المتجر:</b>\nاختر القسم:", reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("maincat_"))
async def show_subs(cb: types.CallbackQuery):
    await safe_answer(cb)
    cid = cb.data.split("_")[1]
    conn = db()
    c = conn.cursor()
    c.execute("SELECT name FROM categories WHERE id=%s", (cid,))
    row = c.fetchone()
    c.close()
    conn.close()
    if not row:
        return await cb.answer("القسم غير موجود.", show_alert=True)

    rows = subs(cid)
    if not rows:
        keyboard = [[types.InlineKeyboardButton(text="🔙 رجوع للأقسام", callback_data="back_to_main_cats", style="danger")]]
        kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
        try:
            return await cb.message.edit_text("هذا القسم فارغ حالياً.", reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
            return

    btn_list = [types.InlineKeyboardButton(text=f"{clean_name(name)}", callback_data=f"gamesub_{sid}", style="primary") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    keyboard.append([types.InlineKeyboardButton(text="🔙 رجوع للأقسام", callback_data="back_to_main_cats", style="danger")])
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text(f"📂 <b>قسم {esc(clean_name(row[0]))}</b>\nاختر لعبة / منتج:", reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("gamesub_"))
async def show_products(cb: types.CallbackQuery):
    await safe_answer(cb)
    sid = cb.data.split("_")[1]
    conn = db()
    c = conn.cursor()
    c.execute("SELECT name, cat_id FROM subcategories WHERE id=%s", (sid,))
    row = c.fetchone()
    c.close()
    conn.close()
    if not row:
        return await cb.answer("القسم غير موجود.", show_alert=True)

    rows = products(sid)
    if not rows:
        keyboard = [[types.InlineKeyboardButton(text="🔙 رجوع للقسم", callback_data=f"maincat_{row[1]}", style="danger")]]
        kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
        try:
            return await cb.message.edit_text("هذه اللعبة لا تحتوي منتجات حالياً.", reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
            return

    btn_list = [types.InlineKeyboardButton(text=f"▪️ {name}", callback_data=f"show_{pid}_{sid}", style="primary") for pid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    keyboard.append([types.InlineKeyboardButton(text="🔙 رجوع للقسم", callback_data=f"maincat_{row[1]}", style="danger")])
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text(f"🛍️ <b>منتجات {esc(clean_name(row[0]))}:</b>", reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("show_"))
async def show_product(cb: types.CallbackQuery):
    await safe_answer(cb)
    parts = cb.data.split("_")
    pid = parts[1]
    sid = parts[2] if len(parts) > 2 else None

    api = await fetch_product(pid)
    row = product(pid)
    if not api and not row:
        return await cb.answer("المنتج غير موجود.", show_alert=True)

    local_available = bool(row[2]) if row else True

    if api:
        name = row[0] if row else (api.get("name") or "غير محدد")
        price = float(api.get("price", 0) or 0)
        available = bool(api.get("available", True)) and local_available
        ptype = api.get("product_type", "digital")
        pdata = api
        desc = row[7] if row else ""
    else:
        name, base_p, db_avail, stock, mn, mx, ptype, desc, api_data = row
        price = apply_margin(base_p)
        available = bool(db_avail)
        pdata = db_product_data(row)

    params = required_params(pdata)
    req = " و ".join(params) if params else "⚡ تسليم فوري (لا يتطلب مدخلات)"
    ptxt = f"${price:.4f}" if str(ptype).lower() != "amount" else f"${price:.4f} للوحدة"

    text = (
        f"📦 <b>المنتج:</b> {esc(name)}\n"
        f"💰 <b>السعر:</b> {ptxt}\n"
        f"📊 <b>الحالة:</b> {'🟢 متوفر' if available else '🔴 غير متوفر'}\n"
    )
    if desc:
        text += f"ℹ️ <b>الوصف:</b> {esc(desc)}\n"
    text += f"📝 <b>المطلوب:</b> {esc(req)}"

    conn = db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM wishlist WHERE user_id=%s AND product_id=%s", (cb.from_user.id, pid))
    in_wish = c.fetchone()
    c.close()
    conn.close()

    keyboard = []
    if available:
        keyboard.append([types.InlineKeyboardButton(text="🛒 شراء الآن", callback_data=f"buy_{pid}", style="success")])
    if in_wish:
        keyboard.append([types.InlineKeyboardButton(text="🗑️ إزالة من المفضلة", callback_data=f"delwish_{pid}", style="danger")])
    else:
        keyboard.append([types.InlineKeyboardButton(text="⭐ إضافة للمفضلة", callback_data=f"addwish_{pid}", style="success")])
    if sid:
        keyboard.append([types.InlineKeyboardButton(text="🔙 رجوع للمنتجات", callback_data=f"gamesub_{sid}", style="danger")])
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


# ================= الشراء والإنهاء =================
async def next_buy_step(user_id: int, state: FSMContext):
    d = await state.get_data()
    idx = d.get("idx", 0)
    params = d.get("params", [])

    if idx < len(params):
        cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
        text = f"👉 أرسل <b>{esc(params[idx])}</b> المطلوبة الآن:"
        await safe_send(user_id, text, reply_markup=cancel_kb)
        await state.set_state(ClientStates.wait_for_param)
        return

    answers = d.get("answers", {})
    details = ("\n".join(f"🔸 {esc(k)}: <code>{esc(v)}</code>" for k, v in answers.items()) if answers else "⚡ لا توجد بيانات مطلوبة")
    qty_txt = (f"🔢 الكمية/المبلغ: {float(d['qty']):g}\n" if str(d["ptype"]).lower() == "amount" else "")
    text = (
        "🧾 <b>مراجعة الطلب:</b>\n\n"
        f"📦 المنتج: {esc(d['name'])}\n"
        f"{qty_txt}"
        f"💵 الإجمالي: ${float(d['total']):.4f}\n\n"
        f"📝 <b>البيانات:</b>\n{details}\n\n"
        "هل أنت متأكد من الشراء؟"
    )
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🚀 تأكيد وإرسال الطلب", callback_data="confirm_api_order", style="success")],
            [types.InlineKeyboardButton(text="❌ إلغاء الطلب", callback_data="cancel_order", style="danger")]
        ]
    )
    await safe_send(user_id, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("buy_"))
async def start_buy(cb: types.CallbackQuery, state: FSMContext):
    if not store_open() and not is_admin(cb.from_user.id):
        return await cb.answer("⚠️ المتجر مغلق حالياً.", show_alert=True)

    pid = cb.data.split("_")[1]
    api = await fetch_product(pid)
    row = product(pid)
    if not api and not row:
        return await cb.answer("خطأ في بيانات المنتج.", show_alert=True)

    local_available = bool(row[2]) if row else True

    if api:
        name = row[0] if row else api.get("name", "غير محدد")
        price = float(api.get("price", 0) or 0)
        desc = ""
        pdata = api
        available = bool(api.get("available", True)) and local_available
    else:
        name = row[0]
        price = apply_margin(row[1])
        desc = row[7] or ""
        pdata = db_product_data(row)
        available = local_available

    ptype = pdata.get("product_type", row[6] if row else "digital")
    if not available:
        return await cb.answer("❌ المنتج غير متوفر حالياً.", show_alert=True)

    current_balance = user_init(cb.from_user.id)
    if str(ptype).lower() != "amount" and current_balance < price:
        return await cb.answer(f"❌ ليس لديك رصيد كافٍ!\nرصيدك: ${current_balance:.4f}\nالمطلوب: ${price:.4f}", show_alert=True)

    await safe_answer(cb)

    qtys_from_desc = extract_quantities_from_text(desc)

    await state.update_data({
        "pid": str(pid), "name": name, "unit_price": price,
        "ptype": ptype, "params": required_params(pdata), "idx": 0,
        "answers": {}, "qtys_list": qtys_from_desc
    })

    if str(ptype).lower() == "amount":
        if qtys_from_desc:
            btn_list = [types.InlineKeyboardButton(text=f"{q:g}", callback_data=f"setqty_{q}", style="primary") for q in qtys_from_desc]
            keyboard = chunk_buttons(btn_list, 3)
            keyboard.append([types.InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel_order", style="danger")])
            kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
            try:
                await cb.message.edit_text(f"🛒 <b>{esc(name)}</b>\nاختر الكمية المطلوبة أدناه:", reply_markup=kb, parse_mode="HTML")
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e
            await state.set_state(ClientStates.wait_for_qty)
            return
        else:
            cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
            await state.set_state(ClientStates.wait_for_qty)
            await safe_send(cb.from_user.id, f"🛒 <b>{esc(name)}</b>\nأرسل الكمية المطلوبة كتابةً:", reply_markup=cancel_kb)
            return

    await state.update_data({"qty": 1, "total": price})
    await next_buy_step(cb.from_user.id, state)


@dp.callback_query(F.data.startswith("setqty_"), ClientStates.wait_for_qty)
async def select_qty_from_button(cb: types.CallbackQuery, state: FSMContext):
    try:
        q = float(cb.data.split("_")[1])
    except ValueError:
        return await cb.answer("قيمة غير صالحة.", show_alert=True)
    d = await state.get_data()
    total = q * float(d["unit_price"])
    current_balance = user_init(cb.from_user.id)
    if current_balance < total:
        return await cb.answer(f"❌ ليس لديك رصيد كافٍ!\nرصيدك: ${current_balance:.4f}\nالمطلوب: ${total:.4f}", show_alert=True)
    await safe_answer(cb)
    await state.update_data({"qty": q, "total": total})
    await next_buy_step(cb.from_user.id, state)


@dp.message(ClientStates.wait_for_qty)
async def qty(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=main_kb(message.from_user.id))
    d = await state.get_data()
    qtys_list = d.get("qtys_list", [])
    try:
        q = float(message.text.strip())
        if q <= 0:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ أرسل رقماً صحيحاً.")
    if qtys_list and q not in qtys_list:
        return await safe_send(message.from_user.id, "❌ اختر من الأرقام المحددة فقط!")
    total = q * float(d["unit_price"])
    current_balance = user_init(message.from_user.id)
    if current_balance < total:
        return await safe_send(message.from_user.id, f"❌ ليس لديك رصيد كافٍ!\nرصيدك: ${current_balance:.4f}\nالمطلوب: ${total:.4f}")
    await state.update_data({"qty": q, "total": total})
    await next_buy_step(message.from_user.id, state)


@dp.message(ClientStates.wait_for_param)
async def param(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=main_kb(message.from_user.id))
    value = message.text.strip()
    if not value:
        return await safe_send(message.from_user.id, "❌ البيانات المرسلة فارغة.")
    d = await state.get_data()
    answers = d.get("answers", {})
    params = d.get("params", [])
    idx = d.get("idx", 0)
    if idx < len(params):
        answers[params[idx]] = value
        await state.update_data({"answers": answers, "idx": idx + 1})
    await next_buy_step(message.from_user.id, state)


@dp.callback_query(F.data == "cancel_order")
async def cancel_order(cb: types.CallbackQuery, state: FSMContext):
    await safe_answer(cb)
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await safe_send(cb.from_user.id, "❌ تم إلغاء الطلب والعودة للرئيسية.", reply_markup=main_kb(cb.from_user.id))


# ================= تتبع الطلب التلقائي =================
async def track_single_order(ouuid, uid, oid, amount, name):
    for attempt in range(1, 702):
        await asyncio.sleep(30)
        try:
            result = await check_order_api(ouuid)
            if not result:
                continue
            st, rep = result["status"], result["replay"]
            clean_rep = str(rep or "").strip()
            if clean_rep in ("[]", "{}", "null", "None"):
                clean_rep = ""

            if accepted(st):
                if accept_once(oid, clean_rep):
                    msg = f"🎉 <b>تم اكتمال طلبك بنجاح! (#{oid})</b>\n📦 المنتج: <b>{esc(name)}</b>\n📌 الحالة: <b>مكتمل ✅</b>"
                    if clean_rep:
                        msg += f"\n\n🎁 <b>بيانات الحساب / التسليم:</b>\n<code>{esc(clean_rep)}</code>"
                    await safe_send(uid, msg)
                break
            elif rejected(st):
                if reject_once(oid, uid, amount, clean_rep):
                    msg = f"❌ <b>تم رفض/إلغاء طلبك (#{oid})</b>\n📦 المنتج: <b>{esc(name)}</b>\n📌 الحالة: <b>مرفوض ❌</b>\n💵 تم إرجاع <b>${float(amount):.4f}</b> إلى حسابك."
                    if clean_rep:
                        msg += f"\n📝 السبب: <code>{esc(clean_rep)}</code>"
                    await safe_send(uid, msg)
                break
            elif clean_rep:
                conn = db()
                c = conn.cursor()
                c.execute("UPDATE orders SET replay_api=%s WHERE id=%s", (clean_rep, oid))
                conn.commit()
                c.close()
                conn.close()
        except Exception as e:
            print(f"⚠️ تتبع الطلب #{oid}: {e}")


@dp.callback_query(F.data == "confirm_api_order")
async def create_order(cb: types.CallbackQuery, state: FSMContext):
    await safe_answer(cb)
    d = await state.get_data()
    if not d:
        return await cb.answer("⚠️ انتهت الجلسة.", show_alert=True)

    uid = cb.from_user.id
    total = float(d["total"])
    pid = str(d["pid"])
    qty_value = float(d.get("qty", 1))
    name = d["name"]
    answers = list(d.get("answers", {}).values())

    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s and balance>=%s", (total, uid, total))
    charged = c.rowcount
    conn.commit()
    c.close()
    conn.close()

    if not charged:
        await state.clear()
        try:
            return await cb.message.edit_text("❌ رصيدك لم يعد كافياً.")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
            return

    try:
        await cb.message.edit_text("🔄 جاري إرسال الطلب...")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

    player = str(answers[0]).strip() if answers else ""
    any_key = str(answers[1]).strip() if len(answers) > 1 else ""
    fallback_ouuid = str(uuid.uuid4())

    params = {"qty": qty_value, "order_uuid": fallback_ouuid}
    if player:
        params["playerId"] = player
    if any_key:
        params["anyKey"] = any_key

    url = f"{API_BASE_URL}/client/api/newOrder/{urllib.parse.quote(pid, safe='')}/params?{urllib.parse.urlencode(params)}"

    try:
        async with session(20) as s:
            async with s.get(url, headers={"api-token": MHD_API_TOKEN, "Accept": "application/json"}) as r:
                raw = await r.text()
                http_status = r.status
        try:
            res = json.loads(raw)
        except json.JSONDecodeError:
            res = {}

        res_data = res.get("data") if isinstance(res.get("data"), dict) else {}
        ouuid = res_data.get("order_id") or res_data.get("order_uuid") or res_data.get("orderUuid") or res.get("order_id") or res.get("order_uuid") or fallback_ouuid
        api_status = str(res_data.get("status") or res.get("status", "wait"))

        success = (http_status == 200 and isinstance(res, dict) and norm_status(res.get("status")) in {"ok", "wait", "success"})
        if not success:
            reason = res.get("message") or res.get("error") or res.get("msg") or "غير معروف"
            add_balance(uid, total)
            try:
                await cb.message.edit_text(f"❌ <b>فشل الطلب.</b>\n💰 تم إرجاع رصيدك.\nالسبب: {esc(reason)}", parse_mode="HTML")
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e
        else:
            rep = replay(res_data if res_data else res)
            conn = db()
            c = conn.cursor()
            c.execute("""INSERT INTO orders
                           (order_uuid, user_id, product_id, product_name, price, status, replay_api, qty, player_id)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (order_uuid) DO NOTHING""",
                        (ouuid, uid, int(pid) if pid.isdigit() else 0, name, total, api_status, rep, qty_value, player))
            c.execute("SELECT id FROM orders WHERE order_uuid=%s", (ouuid,))
            row = c.fetchone()
            oid = row[0] if row else 0
            conn.commit()
            c.close()
            conn.close()

            if accepted(api_status):
                accept_once(oid, rep)
                text = f"🎉 <b>تم اكتمال طلبك بنجاح! (#{oid})</b>\n\n📦 المنتج: {esc(name)}\n📌 order_uuid: <code>{esc(ouuid)}</code>\n"
                if rep and str(rep).strip() not in ("[]", "{}", "null", "None"):
                    text += f"\n🎁 <b>بيانات الحساب / التسليم:</b>\n<code>{esc(clean_rep)}</code>\n"
            else:
                text = f"✅ <b>تم استلام طلبك بنجاح! (#{oid})</b>\n\n📦 المنتج: {esc(name)}\n📌 order_uuid: <code>{esc(ouuid)}</code>\n"
                if rep and str(rep).strip() not in ("[]", "{}", "null", "None"):
                    text += f"📝 البيانات:\n<code>{esc(rep)}</code>\n"
                text += "\n⏳ يجري تتبع الطلب وتحديث حالته تلقائياً..."

            try:
                await cb.message.edit_text(text, parse_mode="HTML")
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e

            if not accepted(api_status) and not rejected(api_status):
                asyncio.create_task(track_single_order(ouuid, uid, oid, total, name))

    except Exception:
        add_balance(uid, total)
        try:
            await cb.message.edit_text("❌ حدث خطأ في الاتصال بالمتجر.\n💰 تم استرجاع رصيدك.")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e

    await state.clear()
    await safe_send(uid, "🏠 يمكنك متابعة التسوق.", reply_markup=main_kb(uid))


# ================= الشحن والدفع =================
@dp.message(F.text == "➕ شحن الرصيد")
async def deposit_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_init(message.from_user.id, message.from_user.username or message.from_user.first_name or "")
    rate = get_exchange_rate()
    conn = db()
    c = conn.cursor()
    c.execute("SELECT code, name FROM payment_methods WHERE active=TRUE ORDER BY code")
    rows = c.fetchall()
    c.close()
    conn.close()
    if not rows:
        return await safe_send(message.from_user.id, "⚠️ لا توجد طرق دفع متاحة حالياً.")
    
    keyboard = []
    for code, name in rows:
        if code == "sham_usd":
            style = "primary"  # باللون الأزرق
        elif code == "syriatel":
            style = "danger"
        elif code in ("bep20", "binance"):
            style = "success"
        else:
            style = "primary"
        keyboard.append([types.InlineKeyboardButton(text=f"💳 {name}", callback_data=f"pay_{code}", style=style)])
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(
        message.from_user.id,
        f"💳 <b>اختر وسيلة الدفع لشحن الرصيد:</b>\n\n📌 <i>سعر الصرف المعتمد لطرق الدفع بالليرة: 1$ = {rate:,.0f} ل.س</i>",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("pay_"))
async def pay_info(cb: types.CallbackQuery, state: FSMContext):
    await safe_answer(cb)
    code = cb.data.replace("pay_", "", 1)
    
    conn = db()
    c = conn.cursor()
    c.execute("SELECT name, info FROM payment_methods WHERE code=%s AND active=TRUE", (code,))
    row = c.fetchone()
    c.close()
    conn.close()
    if not row:
        return await cb.answer("طريقة الدفع غير متوفرة.", show_alert=True)

    await state.update_data({"dep_method": code})
    rate = get_exchange_rate()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ أرسل بيانات التحويل", callback_data="verify_pay", style="success")],
            [types.InlineKeyboardButton(text="❌ إلغاء", callback_data="cancel_pay", style="danger")]
        ]
    )
    
    if code in USD_PAYMENT_METHODS:
        currency_notice = "📌 <i>طريقة الدفع هذه بالدولار ($) مباشرة بدون تصريف.</i>"
    else:
        currency_notice = f"📌 <i>ملاحظة: الشحن بالليرة السورية (سعر الصرف: 1$ = {rate:,.0f} ل.س) وسيتم إضافة الرصيد لحسابك بالدولار ($).</i>"

    try:
        await cb.message.edit_text(
            f"💳 <b>طريقة الدفع: {esc(row[0])}</b>\n\n"
            f"يرجى التحويل للعنوان/الرقم التالي:\n<code>{esc(row[1])}</code>\n\n"
            f"{currency_notice}\n\n"
            "بعد إتمام التحويل، اضغط على 'أرسل بيانات التحويل'.",
            reply_markup=kb, parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data == "cancel_pay")
async def cancel_pay(cb: types.CallbackQuery, state: FSMContext):
    await safe_answer(cb)
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await safe_send(cb.from_user.id, "🏠 تم إلغاء الشحن والرجوع للقائمة.", reply_markup=main_kb(cb.from_user.id))


@dp.callback_query(F.data == "verify_pay")
async def verify_pay(cb: types.CallbackQuery, state: FSMContext):
    await safe_answer(cb)
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(cb.from_user.id, "الرجاء كتابة رقم العملية / إشعار التحويل (Transaction ID):", reply_markup=cancel_kb)
    await state.set_state(ClientStates.wait_trans_id)


@dp.message(ClientStates.wait_trans_id)
async def trans_id(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=main_kb(message.from_user.id))
        
    x = message.text.strip()
    if not x:
        return await safe_send(message.from_user.id, "❌ أدخل رقم العملية بشكل صحيح.")

    await state.update_data({"trans_id": x})
    d = await state.get_data()
    method = d.get("dep_method")

    if method in USD_PAYMENT_METHODS:
        prompt_text = "الآن، أرسل المبلغ الذي قمت بتحويله <b>بالدولار ($)</b>:"
    else:
        rate = get_exchange_rate()
        prompt_text = (
            f"الآن، أرسل المبلغ الذي قمت بتحويله <b>بالليرة السورية (ل.س)</b>:\n"
            f"(سعر الصرف المعتمد: 1$ = {rate:,.0f} ل.س)"
        )

    await safe_send(message.from_user.id, prompt_text)
    await state.set_state(ClientStates.wait_dep_amount)


@dp.message(ClientStates.wait_dep_amount)
async def dep_amount(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=main_kb(message.from_user.id))
        
    d = await state.get_data()
    method = d.get("dep_method")
    trans = d.get("trans_id")

    try:
        entered_amount = float(message.text.strip().replace(",", ""))
        if entered_amount <= 0 or entered_amount > 10000000000:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ أدخل مبلغاً صحيحاً بالأرقام فقط.")

    user_init(message.from_user.id, message.from_user.username or message.from_user.first_name or "")
    u = get_user(message.from_user.id)

    conn_m = db()
    c = conn_m.cursor()
    c.execute("SELECT name FROM payment_methods WHERE code=%s", (method,))
    m_name = c.fetchone()
    c.close()
    conn_m.close()
    method_display_name = m_name[0] if m_name else method

    if method in USD_PAYMENT_METHODS:
        amount_usd = entered_amount
        amount_txt = f"<b>${amount_usd:.4f}</b> (دفع مباشر بالدولار)"
    else:
        rate = get_exchange_rate()
        amount_usd = entered_amount / rate
        amount_txt = f"{entered_amount:,.0f} ل.س (ما يعادل: <b>${amount_usd:.4f}</b> بسعر صرف {rate:,.0f})"

    suggested_usd = f"{amount_usd:.4f}"

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"✅ شحن مباشر (${suggested_usd})", callback_data=f"admd_fast_{message.from_user.id}_{suggested_usd}", style="success")],
            [types.InlineKeyboardButton(text="✏️ تحديد مبلغ يدوي بالدولار", callback_data=f"admd_acc_{message.from_user.id}", style="primary")],
            [types.InlineKeyboardButton(text="❌ غير موافق", callback_data=f"admd_rej_{message.from_user.id}", style="danger")],
            [types.InlineKeyboardButton(text="👤 ملف العميل الشخصي", url=f"tg://user?id={message.from_user.id}", style="primary")]
        ]
    )

    username = f"@{u['username']}" if u and u["username"] else "بدون username"
    deposit_notification_text = (
        f"🔔 <b>طلب شحن رصيد جديد</b>\n\n"
        f"👤 العميل: {esc(username)} (<code>{message.from_user.id}</code>)\n"
        f"💳 وسيلة الدفع: {esc(method_display_name)}\n"
        f"🔢 رقم العملية: <code>{esc(trans)}</code>\n"
        f"💵 المبلغ المحول: {amount_txt}"
    )

    # إرسال طلب الشحن لجميع الأدمنية والمالك
    admins_list = get_all_admins()
    for admin_id in admins_list:
        await safe_send(admin_id, deposit_notification_text, kb)

    await safe_send(message.from_user.id, "✅ تم إرسال طلب الشحن للإدارة بنجاح، ستتم إضافة الرصيد إلى حسابك فور المراجعة.", reply_markup=main_kb(message.from_user.id))
    await state.clear()


@dp.callback_query(F.data.startswith("admd_"))
async def deposit_decision(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    parts = cb.data.split("_")
    action = parts[1]
    uid = int(parts[2])

    if action == "fast":
        amount_to_add = float(parts[3])
        add_balance(uid, amount_to_add)
        record_deposit(uid, amount_to_add)
        new = get_user(uid)["balance"]
        await cb.message.edit_text(f"✅ تم الشحن المباشر بمبلغ <b>${amount_to_add:.4f}</b> للمستخدم <code>{uid}</code>.\nالرصيد الجديد: ${new:.4f}")
        await safe_send(uid, f"🎉 <b>تم شحن محفظتك بنجاح!</b>\nتمت إضافة: <b>${amount_to_add:.4f}</b>\nرصيدك الحالي: <b>${new:.4f}</b>")
    elif action == "rej":
        await safe_send(uid, "❌ تم رفض طلب شحن الرصيد من قبل الإدارة.")
        try:
            await cb.message.edit_text("❌ تم رفض الطلب.")
        except Exception:
            pass
    else:
        await safe_send(cb.from_user.id, f"كم المبلغ بالدولار ($) المراد إضافته لـ <code>{uid}</code>؟")
        await state.update_data({"dep_target_user": uid})
        await state.set_state(AdminStates.wait_deposit_amount)


@dp.message(AdminStates.wait_deposit_amount)
async def finish_deposit(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        amount = float(message.text.strip())
        if amount <= 0 or amount > 1000000:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ أدخل مبلغاً موجباً صحيحاً بالدولار.")

    d = await state.get_data()
    uid = int(d["dep_target_user"])
    if not get_user(uid):
        return await safe_send(message.from_user.id, "❌ المستخدم غير موجود.")

    add_balance(uid, amount)
    record_deposit(uid, amount)
    new = get_user(uid)["balance"]
    await safe_send(message.from_user.id, f"✅ تمت إضافة ${amount:.4f}.\nالرصيد الجديد للعميل: ${new:.4f}", reply_markup=admin_kb())
    await safe_send(uid, f"🎉 <b>تم شحن محفظتك!</b>\nتمت إضافة: <b>${amount:.4f}</b>\nالرصيد الحالي: <b>${new:.4f}</b>")
    await state.clear()


# ================= لوحة الإدارة =================
@dp.message(F.text == "⚙️ لوحة الإدارة")
async def admin_panel(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await safe_send(message.from_user.id, "🛠️ <b>لوحة الإدارة الرئيسية:</b>", reply_markup=admin_kb())


@dp.message(F.text == "➕ إضافة رصيد لعميل")
async def add_bal_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await safe_send(message.from_user.id, "أرسل User ID الخاص بالعميل:")
    await state.set_state(AdminStates.wait_add_user)


@dp.message(AdminStates.wait_add_user)
async def add_bal_uid(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await safe_send(message.from_user.id, "❌ ID غير صحيح.")
    uid = int(message.text.strip())
    u = get_user(uid)
    if not u:
        return await safe_send(message.from_user.id, "❌ المستخدم غير موجود.")
    await state.update_data({"add_uid": uid})
    await safe_send(message.from_user.id, f"المستخدم @{esc(u['username'])}\nالرصيد الحالي: ${u['balance']:.4f}\nأرسل المبلغ المطلوب إضافته بالدولار ($):")
    await state.set_state(AdminStates.wait_add_amount)


@dp.message(AdminStates.wait_add_amount)
async def add_bal_finish(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0 or amount > 1000000:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ مبلغ غير صحيح.")
    d = await state.get_data()
    uid = int(d["add_uid"])
    add_balance(uid, amount)
    record_deposit(uid, amount)
    new = get_user(uid)["balance"]
    await safe_send(message.from_user.id, f"✅ تمت الإضافة ${amount:.4f}.\nالرصيد الجديد: ${new:.4f}", reply_markup=admin_kb())
    await safe_send(uid, f"🎉 تمت إضافة <b>${amount:.4f}</b> إلى رصيدك.\nالرصيد الحالي: <b>${new:.4f}</b>")
    await state.clear()


@dp.message(F.text == "🔻 سحب/خصم رصيد عميل")
async def deduct_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await safe_send(message.from_user.id, "أرسل User ID الخاص بالعميل:")
    await state.set_state(AdminStates.wait_deduct_user)


@dp.message(AdminStates.wait_deduct_user)
async def deduct_uid(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await safe_send(message.from_user.id, "❌ ID غير صحيح.")
    uid = int(message.text.strip())
    u = get_user(uid)
    if not u:
        return await safe_send(message.from_user.id, "❌ المستخدم غير موجود.")
    await state.update_data({"deduct_uid": uid})
    await safe_send(message.from_user.id, f"المستخدم @{esc(u['username'])}\nالرصيد: ${u['balance']:.4f}\nأرسل المبلغ المراد خصمه بالدولار ($):")
    await state.set_state(AdminStates.wait_deduct_amount)


@dp.message(AdminStates.wait_deduct_amount)
async def deduct_finish(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0 or amount > 1000000:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ مبلغ غير صحيح.")
    d = await state.get_data()
    uid = int(d["deduct_uid"])
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s and balance>=%s", (amount, uid, amount))
    ok = c.rowcount > 0
    conn.commit()
    c.close()
    conn.close()
    if not ok:
        return await safe_send(message.from_user.id, "❌ الرصيد الحالي غير كافٍ للخصم.")
    new = get_user(uid)["balance"]
    await safe_send(message.from_user.id, f"✅ تم الخصم ${amount:.4f}.\nالرصيد الحالي: ${new:.4f}", reply_markup=admin_kb())
    await safe_send(uid, f"⚠️ تم خصم <b>${amount:.4f}</b> من رصيدك.\nالرصيد الحالي: <b>${new:.4f}</b>")
    await state.clear()


@dp.message(F.text.startswith("🏪 حالة المتجر:"))
async def toggle_store(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='store_status'")
    current = c.fetchone()
    new = "closed" if current and current[0] == "open" else "open"
    c.execute("INSERT INTO settings(key, value) VALUES('store_status', %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (new,))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, "🟢 تم فتح المتجر." if new == "open" else "🔴 تم إغلاق المتجر.", reply_markup=admin_kb())


@dp.message(F.text.startswith("📈 تعديل أسعار"))
async def edit_all_prices_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    current_margin = get_margin_percent()
    await safe_send(
        message.from_user.id,
        f"📊 <b>تعديل أسعار المتجر بالكامل (%)</b>\n\nالنسبة الحالية: <b>{current_margin:g}%</b>\n\nأدخل النسبة الجديدة (مثال: أرسل 10 لزيادة 10% لكل 1$):"
    )
    await state.set_state(AdminStates.edit_price_percentage)


@dp.message(AdminStates.edit_price_percentage)
async def edit_all_prices_finish(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        val = float(message.text.strip().replace("%", ""))
        if val < 0:
            raise ValueError
    except ValueError:
        return await safe_send(message.from_user.id, "❌ يرجى كتابة رقم نسبة صحيح.")

    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO settings(key, value) VALUES('store_margin_percent', %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (str(val),))
    c.execute("SELECT id, price, api_data FROM products")
    rows = c.fetchall()
    for pid, base_p, api_data_str in rows:
        orig_price = base_p
        if api_data_str:
            try:
                adata = json.loads(api_data_str)
                if "price" in adata and float(adata["price"]) > 0:
                    orig_price = float(adata["price"])
            except Exception:
                pass
        new_price = orig_price * (1 + val / 100.0)
        c.execute("UPDATE products SET price=%s WHERE id=%s", (new_price, pid))
    conn.commit()
    c.close()
    conn.close()

    await safe_send(message.from_user.id, f"✅ <b>تم تحديث أسعار المتجر بنجاح!</b>\nالنسبة المطبقة: <b>{val:g}%</b>", reply_markup=admin_kb())
    await state.clear()


# ================= تفعيل / تعطيل منتج (غير متوفر) =================
@dp.message(F.text == "🔄 تفعيل / تعطيل منتج")
async def toggle_product_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد أقسام حالياً.")
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"tglcat_{cid}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم الخاص بالمنتج لتعديل حالته (متوفر / غير متوفر):", reply_markup=kb)


@dp.callback_query(F.data.startswith("tglcat_"))
async def toggle_product_cat_chosen(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[1]
    rows = subs(cid)
    if not rows:
        return await cb.answer("لا توجد ألعاب بهذا القسم.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🎮 {clean_name(name)}", callback_data=f"tglsub_{sid}", style="primary") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر اللعبة:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


def build_toggle_products_kb(sid):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT mhd_id, name, available FROM products WHERE sub_id=%s ORDER BY id", (sid,))
    rows = c.fetchall()
    c.close()
    conn.close()

    btn_list = []
    for pid, name, is_avail in rows:
        icon = "🟢 متوفر" if is_avail else "🔴 غير متوفر"
        btn_list.append(types.InlineKeyboardButton(text=f"{icon} | {name}", callback_data=f"tglprod_{pid}_{sid}", style="primary"))
    
    keyboard = chunk_buttons(btn_list, 1)
    keyboard.append([types.InlineKeyboardButton(text="🔙 رجوع للأقسام", callback_data="back_to_tgl_cats", style="danger")])
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard), len(rows)


@dp.callback_query(F.data == "back_to_tgl_cats")
async def back_to_tgl_cats_cb(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    rows = categories()
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"tglcat_{cid}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر القسم الخاص بالمنتج لتعديل حالته (متوفر / غير متوفر):", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("tglsub_"))
async def toggle_product_sub_chosen(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    sid = cb.data.split("_")[1]
    kb, count = build_toggle_products_kb(sid)
    if count == 0:
        return await cb.answer("لا توجد منتجات داخل هذه اللعبة.", show_alert=True)
    try:
        await cb.message.edit_text("اضغط على المنتج لتغيير حالته بين (متوفر 🟢) و (غير متوفر 🔴):", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("tglprod_"))
async def toggle_product_action(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    parts = cb.data.split("_")
    pid = parts[1]
    sid = parts[2]

    conn = db()
    c = conn.cursor()
    c.execute("SELECT available, name FROM products WHERE mhd_id=%s", (int(pid),))
    row = c.fetchone()
    if not row:
        c.close()
        conn.close()
        return await cb.answer("المنتج غير موجود.", show_alert=True)
    
    current_status = bool(row[0])
    new_status = not current_status
    c.execute("UPDATE products SET available=%s WHERE mhd_id=%s", (new_status, int(pid)))
    conn.commit()
    c.close()
    conn.close()

    status_str = "🟢 تم تفعيل المنتج (متوفر)" if new_status else "🔴 تم تعطيل المنتج (غير متوفر)"
    await cb.answer(f"{status_str}: {row[1]}", show_alert=True)

    kb, _ = build_toggle_products_kb(sid)
    try:
        await cb.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


# ================= تعديل اسم قسم رئيسي =================
@dp.message(F.text == "✏️ تعديل اسم قسم")
async def edit_category_name_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد أقسام حالياً.")
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"rencat_{cid}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم المراد تعديل اسمه:", reply_markup=kb)


@dp.callback_query(F.data.startswith("rencat_"))
async def edit_category_chosen(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[1]
    await state.update_data({"edit_cid": cid})
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(cb.from_user.id, "أرسل الاسم الجديد للقسم الآن:", reply_markup=cancel_kb)
    await state.set_state(AdminStates.edit_cat_new_name)


@dp.message(AdminStates.edit_cat_new_name)
async def edit_category_save(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    new_name = clean_name(message.text.strip())
    if not new_name:
        return await safe_send(message.from_user.id, "❌ الاسم لا يمكن أن يكون فارغاً.")
    d = await state.get_data()
    cid = d.get("edit_cid")
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE categories SET name=%s WHERE id=%s", (new_name, cid))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تم تعديل اسم القسم إلى: <b>{esc(new_name)}</b>", reply_markup=admin_kb())
    await state.clear()


# ================= تعديل اسم لعبة (Subcategory) =================
@dp.message(F.text == "✏️ تعديل اسم لعبة")
async def edit_game_name_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد أقسام حالياً.")
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"rengame_cat_{cid}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم الذي توجد به اللعبة:", reply_markup=kb)


@dp.callback_query(F.data.startswith("rengame_cat_"))
async def edit_game_cat_chosen(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[2]
    rows = subs(cid)
    if not rows:
        return await cb.answer("لا توجد ألعاب داخل هذا القسم.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🎮 {clean_name(name)}", callback_data=f"rengame_sub_{sid}", style="primary") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر اللعبة المراد تعديل اسمها:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("rengame_sub_"))
async def edit_game_sub_chosen(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    sid = cb.data.split("_")[2]
    await state.update_data({"edit_game_sid": sid})
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(cb.from_user.id, "أرسل الاسم الجديد للعبة الآن:", reply_markup=cancel_kb)
    await state.set_state(AdminStates.edit_sub_new_name)


@dp.message(AdminStates.edit_sub_new_name)
async def edit_game_finish(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    new_name = clean_name(message.text.strip())
    if not new_name:
        return await safe_send(message.from_user.id, "❌ الاسم لا يمكن أن يكون فارغاً.")
    d = await state.get_data()
    sid = d.get("edit_game_sid")
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE subcategories SET name=%s WHERE id=%s", (new_name, int(sid)))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تم تعديل اسم اللعبة بنجاح إلى: <b>{esc(new_name)}</b>", reply_markup=admin_kb())
    await state.clear()


# ================= تعديل اسم منتج =================
@dp.message(F.text == "✏️ تعديل اسم منتج")
async def edit_product_name_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد أقسام.")
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"renprod_cat_{cid}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم الخاص بالمنتج:", reply_markup=kb)


@dp.callback_query(F.data.startswith("renprod_cat_"))
async def edit_product_cat_chosen(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[2]
    rows = subs(cid)
    if not rows:
        return await cb.answer("لا توجد ألعاب بهذا القسم.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🎮 {clean_name(name)}", callback_data=f"renprod_sub_{sid}", style="primary") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر اللعبة:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("renprod_sub_"))
async def edit_product_sub_chosen(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    sid = cb.data.split("_")[2]
    rows = products(sid)
    if not rows:
        return await cb.answer("لا توجد منتجات.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"📦 {name}", callback_data=f"renprod_pick_{pid}", style="primary") for pid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر المنتج لتعديل اسمه:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("renprod_pick_"))
async def edit_product_pick(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    pid = cb.data.split("_")[2]
    await state.update_data({"edit_pid": pid})
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(cb.from_user.id, "أرسل الاسم الجديد للمنتج الآن:", reply_markup=cancel_kb)
    await state.set_state(AdminStates.edit_prod_new_name)


@dp.message(AdminStates.edit_prod_new_name)
async def edit_product_finish(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    new_name = message.text.strip()
    if not new_name:
        return await safe_send(message.from_user.id, "❌ الاسم لا يمكن أن يكون فارغاً.")
    d = await state.get_data()
    pid = d.get("edit_pid")
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE products SET name=%s WHERE mhd_id=%s", (new_name, int(pid)))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تم تعديل اسم المنتج إلى: <b>{esc(new_name)}</b>", reply_markup=admin_kb())
    await state.clear()


# ================= تعديل وصف منتج =================
@dp.message(F.text == "📝 تعديل وصف منتج")
async def edit_product_desc_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد أقسام.")
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"edesc_cat_{cid}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم الخاص بالمنتج:", reply_markup=kb)


@dp.callback_query(F.data.startswith("edesc_cat_"))
async def edit_prod_desc_cat_cb(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[2]
    rows = subs(cid)
    if not rows:
        return await cb.answer("لا توجد ألعاب بهذا القسم.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🎮 {clean_name(name)}", callback_data=f"edesc_sub_{sid}", style="primary") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر اللعبة:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("edesc_sub_"))
async def edit_prod_desc_sub_cb(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    sid = cb.data.split("_")[2]
    rows = products(sid)
    if not rows:
        return await cb.answer("لا توجد منتجات.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"📦 {name}", callback_data=f"edesc_pick_{pid}", style="primary") for pid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر المنتج لتعديل وصفه:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("edesc_pick_"))
async def edit_prod_desc_pick_cb(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    pid = cb.data.split("_")[2]
    p_info = product(pid)
    current_desc = p_info[7] if (p_info and p_info[7]) else "لا يوجد وصف حالياً"
    
    await state.update_data({"edit_desc_pid": pid})
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    
    await safe_send(
        cb.from_user.id,
        f"📝 <b>تعديل وصف المنتج:</b>\n\n"
        f"📦 المنتج: <b>{esc(p_info[0] if p_info else pid)}</b>\n\n"
        f"📄 <b>الوصف الحالي:</b>\n<code>{esc(current_desc)}</code>\n\n"
        "أرسل الوصف الجديد الآن (أو أرسل كلمة <code>حذف الوصف</code> لمسحه):",
        reply_markup=cancel_kb
    )
    await state.set_state(AdminStates.edit_prod_desc_new)


@dp.message(AdminStates.edit_prod_desc_new)
async def edit_prod_desc_save(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    
    val = message.text.strip()
    new_desc = "" if val == "حذف الوصف" else val
    d = await state.get_data()
    pid = d.get("edit_desc_pid")
    
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE products SET description=%s WHERE mhd_id=%s", (new_desc, int(pid)))
    conn.commit()
    c.close()
    conn.close()
    
    msg_txt = "✅ تم مسح وصف المنتج بنجاح." if not new_desc else "✅ تم تحديث وصف المنتج بنجاح!"
    await safe_send(message.from_user.id, msg_txt, reply_markup=admin_kb())
    await state.clear()


# ================= تعديل حساب الدعم =================
@dp.message(F.text == "📞 تعديل حساب الدعم")
async def edit_support_contact_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    current_sup = get_support_contact()
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(
        message.from_user.id,
        f"📞 <b>إدارة وتعديل حساب الدعم الفني:</b>\n\n"
        f"الحساب الحالي: <b>{esc(current_sup)}</b>\n\n"
        "أرسل المعرف الجديد لحساب الدعم (مثال: <code>@SARE3_570</code> أو رابط تليجرام):",
        reply_markup=cancel_kb
    )
    await state.set_state(AdminStates.edit_support_contact)


@dp.message(AdminStates.edit_support_contact)
async def edit_support_contact_finish(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم الإلغاء.", reply_markup=admin_kb())
    
    new_contact = message.text.strip()
    if not new_contact:
        return await safe_send(message.from_user.id, "❌ لا يمكن أن يكون معرف الدعم فارغاً.")
    
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO settings(key, value) VALUES('support_contact', %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (new_contact,))
    conn.commit()
    c.close()
    conn.close()

    await safe_send(message.from_user.id, f"✅ <b>تم تحديث حساب الدعم بنجاح!</b>\nالحساب الجديد: <b>{esc(new_contact)}</b>", reply_markup=admin_kb())
    await state.clear()


# ================= إضافة وحذف الأدمن =================
@dp.message(F.text == "👤 إضافة أدمن جديد")
async def new_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id != PRIMARY_ADMIN_ID:
        return await safe_send(message.from_user.id, "❌ المالك الأساسي فقط.")
    await safe_send(message.from_user.id, "أرسل User ID للأدمن الجديد:")
    await state.set_state(AdminStates.wait_new_admin_id)


@dp.message(AdminStates.wait_new_admin_id)
async def new_admin_finish(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await safe_send(message.from_user.id, "❌ ID غير صحيح.")
    uid = int(message.text.strip())
    conn = db()
    c = conn.cursor()
    c.execute("""INSERT INTO users(user_id, username, balance, is_admin)
                 VALUES(%s, 'Admin', 0, TRUE)
                 ON CONFLICT (user_id) DO UPDATE SET is_admin=TRUE""", (uid,))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تم منح صلاحية الأدمن للمستخدم <code>{uid}</code>.", reply_markup=admin_kb())
    await state.clear()


@dp.message(F.text == "🚫 إزالة أدمن")
async def del_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id != PRIMARY_ADMIN_ID:
        return await safe_send(message.from_user.id, "❌ المالك الأساسي فقط.")
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id, username FROM users WHERE is_admin=TRUE AND user_id!=%s", (PRIMARY_ADMIN_ID,))
    admins = c.fetchall()
    c.close()
    conn.close()

    if not admins:
        return await safe_send(message.from_user.id, "ℹ️ لا يوجد مشرفين إضافيين لإزالتهم.")
    txt = "👥 <b>قائمة المشرفين الحاليين:</b>\n\n"
    for uid, uname in admins:
        txt += f"▪️ <code>{uid}</code> — @{esc(uname) if uname else 'بدون يوزر'}\n"
    txt += "\nأرسل User ID للمشرف المراد إزالته:"
    await safe_send(message.from_user.id, txt)
    await state.set_state(AdminStates.wait_del_admin_id)


@dp.message(AdminStates.wait_del_admin_id)
async def del_admin_finish(message: types.Message, state: FSMContext):
    if not message.text.strip().isdigit():
        return await safe_send(message.from_user.id, "❌ ID غير صحيح.")
    uid = int(message.text.strip())
    if uid == PRIMARY_ADMIN_ID:
        return await safe_send(message.from_user.id, "❌ لا يمكن إزالة المالك الأساسي.")
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET is_admin=FALSE WHERE user_id=%s", (uid,))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تم إزالة صلاحية الأدمن من <code>{uid}</code> بنجاح.", reply_markup=admin_kb())
    await state.clear()


# ================= إدارة طرق الدفع =================
def build_payment_manage_kb():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT code, name, active FROM payment_methods ORDER BY code")
    rows = c.fetchall()
    c.close()
    conn.close()
    
    keyboard = []
    for code, name, active in rows:
        status_icon = "🟢" if active else "🔴"
        keyboard.append([
            types.InlineKeyboardButton(text=f"{status_icon} {name}", callback_data=f"togglepay_{code}", style="primary"),
            types.InlineKeyboardButton(text="✏️ تعديل", callback_data=f"editpay_{code}", style="success"),
            types.InlineKeyboardButton(text="🗑️ حذف", callback_data=f"delpay_{code}", style="danger")
        ])
    keyboard.append([types.InlineKeyboardButton(text="➕ إضافة طريقة دفع جديدة", callback_data="add_new_payment", style="success")])
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


@dp.message(F.text == "💳 إدارة طرق الدفع")
async def payment_manage(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    kb = build_payment_manage_kb()
    await safe_send(message.from_user.id, "⚙️ <b>إدارة طرق الدفع:</b>\n\n• اضغط على الاسم لتفعيل/تعطيل الطريقة.\n• <b>✏️ تعديل:</b> لتحديث العنوان/الرقم.", reply_markup=kb)


@dp.callback_query(F.data == "add_new_payment")
async def add_new_payment_start(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cancel_kb = types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="🔙 إلغاء الشراء", style="danger")]], resize_keyboard=True)
    await safe_send(cb.from_user.id, "أرسل المعرف البرمجي لطريقة الدفع بالإنجليزية بدون مسافات (مثال: <code>zaincash</code>):", reply_markup=cancel_kb)
    await state.set_state(AdminStates.wait_new_pay_code)


@dp.message(AdminStates.wait_new_pay_code)
async def process_new_pay_code(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم إلغاء العملية.", reply_markup=admin_kb())
    code = message.text.strip().lower()
    if not code or not code.isalnum():
        return await safe_send(message.from_user.id, "❌ المعرف يجب أن يكون أحرف وأرقام إنجليزية فقط.")
    conn = db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM payment_methods WHERE code=%s", (code,))
    exists = c.fetchone()
    c.close()
    conn.close()
    if exists:
        return await safe_send(message.from_user.id, "⚠️ معرف الدفع موجود مسبقاً.")

    await state.update_data({"new_pay_code": code})
    await safe_send(message.from_user.id, "أرسل اسم وسيلة الدفع التي تظهر للمستخدم:")
    await state.set_state(AdminStates.wait_new_pay_name)


@dp.message(AdminStates.wait_new_pay_name)
async def process_new_pay_name(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم إلغاء العملية.", reply_markup=admin_kb())
    name = message.text.strip()
    if not name:
        return await safe_send(message.from_user.id, "❌ الاسم لا يمكن أن يكون فارغاً.")
    await state.update_data({"new_pay_name": name})
    await safe_send(message.from_user.id, "أرسل رقم الحساب / العنوان الخاص بوسيلة الدفع:")
    await state.set_state(AdminStates.wait_new_pay_info)


@dp.message(AdminStates.wait_new_pay_info)
async def process_new_pay_info(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "❌ تم إلغاء العملية.", reply_markup=admin_kb())
    info = message.text.strip()
    if not info:
        return await safe_send(message.from_user.id, "❌ التفاصيل لا يمكن أن تكون فارغة.")
    d = await state.get_data()
    code = d.get("new_pay_code")
    name = d.get("new_pay_name")

    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO payment_methods(code, name, info, active) VALUES(%s, %s, %s, TRUE) ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, info=EXCLUDED.info", (code, name, info))
    conn.commit()
    c.close()
    conn.close()

    await state.clear()
    await safe_send(message.from_user.id, f"✅ تم إضافة طريقة الدفع <b>{esc(name)}</b> بنجاح!", reply_markup=admin_kb())


@dp.callback_query(F.data.startswith("delpay_"))
async def delete_payment_method(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    code = cb.data.split("_", 1)[1]
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM payment_methods WHERE code=%s", (code,))
    conn.commit()
    c.close()
    conn.close()
    await cb.answer("🗑️ تم حذف طريقة الدفع.", show_alert=True)
    try:
        await cb.message.edit_reply_markup(reply_markup=build_payment_manage_kb())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("togglepay_"))
async def toggle_pay(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    code = cb.data.split("_", 1)[1]
    conn = db()
    c = conn.cursor()
    c.execute("SELECT active FROM payment_methods WHERE code=%s", (code,))
    row = c.fetchone()
    if not row:
        c.close()
        conn.close()
        return await cb.answer("طريقة الدفع غير موجودة.", show_alert=True)
    new_status = not row[0]
    c.execute("UPDATE payment_methods SET active=%s WHERE code=%s", (new_status, code))
    conn.commit()
    c.close()
    conn.close()
    await cb.answer("تم تغيير حالة وسيلة الدفع.")
    try:
        await cb.message.edit_reply_markup(reply_markup=build_payment_manage_kb())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("editpay_"))
async def edit_pay(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    code = cb.data.split("_", 1)[1]
    conn = db()
    c = conn.cursor()
    c.execute("SELECT name, info FROM payment_methods WHERE code=%s", (code,))
    row = c.fetchone()
    c.close()
    conn.close()
    if not row:
        return await cb.answer("طريقة الدفع غير موجودة.", show_alert=True)

    await state.update_data({"edit_pay": code})
    await safe_send(cb.from_user.id, f"أرسل العنوان/الرقم الجديد الخاص بـ <b>{esc(row[0])}</b>:\nالبيانات الحالية: <code>{esc(row[1])}</code>")
    await state.set_state(AdminStates.wait_payment_info_edit)


@dp.message(AdminStates.wait_payment_info_edit)
async def save_pay(message: types.Message, state: FSMContext):
    d = await state.get_data()
    code = d.get("edit_pay")
    value = message.text.strip()
    if not value:
        return await safe_send(message.from_user.id, "❌ لا يمكن أن تكون البيانات فارغة.")
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE payment_methods SET info=%s WHERE code=%s", (value, code))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, "✅ تم تحديث بيانات طريقة الدفع بنجاح.", reply_markup=admin_kb())
    await state.clear()


# ================= إضافة وحذف الأقسام والمنتجات =================
@dp.message(F.text == "➕ إضافة قسم رئيسي")
async def cat_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await safe_send(message.from_user.id, "أرسل اسم القسم الجديد:")
    await state.set_state(AdminStates.add_cat_name)


@dp.message(AdminStates.add_cat_name)
async def cat_save(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        return await back_main(message, state)
    name = clean_name(message.text.strip())
    if not name:
        return await safe_send(message.from_user.id, "❌ الاسم فارغ.")
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO categories(name) VALUES(%s)", (name,))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تمت إضافة {esc(name)}.", reply_markup=admin_kb())
    await state.clear()


@dp.message(F.text == "🗑️ إزالة قسم رئيسي")
async def cat_delete_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد أقسام.")
    btn_list = [types.InlineKeyboardButton(text=f"🗑️ {name}", callback_data=f"delcat_{cid}", style="danger") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم للحذف:", reply_markup=kb)


@dp.callback_query(F.data.startswith("delcat_"))
async def cat_delete(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[1]
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM subcategories WHERE cat_id=%s", (cid,))
    ids = [x[0] for x in cur.fetchall()]
    for sid in ids:
        cur.execute("DELETE FROM products WHERE sub_id=%s", (sid,))
    cur.execute("DELETE FROM subcategories WHERE cat_id=%s", (cid,))
    cur.execute("DELETE FROM categories WHERE id=%s", (cid,))
    conn.commit()
    cur.close()
    conn.close()
    try:
        await cb.message.edit_text("✅ تم حذف القسم ومكوناته.")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.message(F.text == "➕ إضافة لعبة لقسم")
async def sub_add_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ أضف قسماً أولاً.")
    btn_list = [types.KeyboardButton(text=f"📂 {clean_name(name)}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    keyboard.append([types.KeyboardButton(text="🔙 رجوع للرئيسية", style="danger")])
    kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    await safe_send(message.from_user.id, "اختر القسم:", reply_markup=kb)
    await state.set_state(AdminStates.add_sub_select_cat)


@dp.message(AdminStates.add_sub_select_cat)
async def sub_add_choose(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        return await back_main(message, state)
    name = clean_name(message.text.replace("📂 ", "", 1))
    cid = next((x[0] for x in categories() if x[1] == name), None)
    if not cid:
        return await safe_send(message.from_user.id, "❌ اختيار خاطئ.")
    await state.update_data({"cat_id": cid})
    await safe_send(message.from_user.id, "أرسل اسم اللعبة:")
    await state.set_state(AdminStates.add_sub_name)


@dp.message(AdminStates.add_sub_name)
async def sub_save(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        return await back_main(message, state)
    d = await state.get_data()
    cid = d["cat_id"]
    name = message.text.strip()
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO subcategories(name, cat_id) VALUES(%s, %s)", (name, cid))
    conn.commit()
    c.close()
    conn.close()
    await safe_send(message.from_user.id, f"✅ تمت إضافة اللعبة: {esc(name)}.", reply_markup=admin_kb())
    await state.clear()


@dp.message(F.text == "🗑️ إزالة لعبة من قسم")
async def sub_delete_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"seldelsub_{cid}", style="primary") for cid, name in categories()]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم:", reply_markup=kb)


@dp.callback_query(F.data.startswith("seldelsub_"))
async def sub_delete_list(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[1]
    rows = subs(cid)
    if not rows:
        return await cb.answer("لا توجد ألعاب.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🎮 {name}", callback_data=f"delsub_{sid}", style="danger") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر اللعبة:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("delsub_"))
async def sub_delete(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    sid = cb.data.split("_")[1]
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM products WHERE sub_id=%s", (sid,))
    c.execute("DELETE FROM subcategories WHERE id=%s", (sid,))
    conn.commit()
    c.close()
    conn.close()
    try:
        await cb.message.edit_text("✅ تم حذف اللعبة ومنتجاتها.")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


# ================= إضافة المنتجات المتعددة بالتتالي =================
@dp.message(F.text.startswith("➕ إضافة منتجات"))
async def product_add_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    rows = categories()
    if not rows:
        return await safe_send(message.from_user.id, "❌ أضف قسماً أولاً.")
    btn_list = [types.KeyboardButton(text=f"📂 {clean_name(name)}", style="primary") for cid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    keyboard.append([types.KeyboardButton(text="🔙 رجوع للرئيسية", style="danger")])
    kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    await safe_send(message.from_user.id, "اختر القسم:", reply_markup=kb)
    await state.set_state(AdminStates.add_prod_select_cat)


@dp.message(AdminStates.add_prod_select_cat)
async def product_add_cat(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        return await back_main(message, state)
    name = clean_name(message.text.replace("📂 ", "", 1))
    cid = next((x[0] for x in categories() if x[1] == name), None)
    if not cid:
        return await safe_send(message.from_user.id, "❌ اختيار خاطئ.")
    rows = subs(cid)
    if not rows:
        return await safe_send(message.from_user.id, "❌ لا توجد ألعاب بهذا القسم.")
    btn_list = [types.KeyboardButton(text=f"🎮 {sname}", style="primary") for sid, sname in rows]
    keyboard = chunk_buttons(btn_list, 2)
    keyboard.append([types.KeyboardButton(text="🔙 رجوع للرئيسية", style="danger")])
    kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    await safe_send(message.from_user.id, "اختر اللعبة:", reply_markup=kb)
    await state.set_state(AdminStates.add_prod_select_sub)


@dp.message(AdminStates.add_prod_select_sub)
async def product_add_sub(message: types.Message, state: FSMContext):
    if message.text in ("🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        return await back_main(message, state)
    sname = message.text.replace("🎮 ", "", 1)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id FROM subcategories WHERE name=%s ORDER BY id LIMIT 1", (sname,))
    row = c.fetchone()
    c.close()
    conn.close()
    if not row:
        return await safe_send(message.from_user.id, "❌ اختيار خاطئ.")
    await state.update_data({"sub_id": row[0]})
    
    stop_kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="✅ إنهاء إضافة المنتجات", style="success")],
            [types.KeyboardButton(text="🔙 رجوع للرئيسية", style="danger")]
        ],
        resize_keyboard=True
    )
    await safe_send(
        message.from_user.id,
        "🔢 <b>أرسل معرّف ID المنتج من الموقع:</b>\n\n"
        "💡 <i>يمكنك إرسال ID واحد أو عدة آيديات معاً مفصولة بمسافة (مثلاً: <code>101 102 103</code>)، وسيتم إضافتها ويبقى البوت في وضع الاستقبال حتى تضغط زر الإنهاء.</i>",
        reply_markup=stop_kb
    )
    await state.set_state(AdminStates.add_prod_id)


@dp.message(AdminStates.add_prod_id)
async def product_fetch_multi(message: types.Message, state: FSMContext):
    if message.text in ("✅ إنهاء إضافة المنتجات", "🔙 إلغاء الشراء", "🔙 رجوع للرئيسية"):
        await state.clear()
        return await safe_send(message.from_user.id, "✅ تم إنهاء عملية إضافة المنتجات بنجاح.", reply_markup=admin_kb())

    raw_text = message.text.strip()
    id_list = re.findall(r'\d+', raw_text)
    if not id_list:
        return await safe_send(message.from_user.id, "❌ يرجى إرسال أرقام ID صحيحة.")

    d = await state.get_data()
    sub_id = d["sub_id"]

    status_report = []

    for pid in id_list:
        if product(pid):
            status_report.append(f"⚠️ المنتج <code>{pid}</code>: موجود مسبقاً.")
            continue

        api = await fetch_product(pid)
        if not api:
            status_report.append(f"❌ المنتج <code>{pid}</code>: غير موجود بالموقع.")
            continue

        base_price = float(api.get("price", 0) or 0)
        p_name = api.get("name", "غير محدد")
        conn = db()
        c = conn.cursor()
        c.execute("""INSERT INTO products
                        (mhd_id, name, price, product_type, available, stock, min_qty, max_qty, sub_id, description, api_data)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (int(pid), p_name, base_price,
                      api.get("product_type", "digital"),
                      bool(api.get("available", True)),
                      int(api.get("stock", 999) or 999),
                      float(api.get("min_qty", 1) or 1),
                      float(api.get("max_qty", 1000) or 1000),
                      sub_id, "", json.dumps(api)))
        conn.commit()
        c.close()
        conn.close()
        status_report.append(f"✅ تمت إضافة: <b>{esc(p_name)}</b> (<code>{pid}</code>)")

    status_report.append("\n👉 <b>أرسل معرّف ID آخر للمتابعة، أو اضغط «إنهاء إضافة المنتجات»:</b>")
    await safe_send(message.from_user.id, "\n".join(status_report))


@dp.message(F.text == "🗑️ حذف منتج من لعبة")
async def product_delete_start(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    btn_list = [types.InlineKeyboardButton(text=f"📂 {clean_name(name)}", callback_data=f"seldelprodcat_{cid}", style="primary") for cid, name in categories()]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await safe_send(message.from_user.id, "اختر القسم:", reply_markup=kb)


@dp.callback_query(F.data.startswith("seldelprodcat_"))
async def product_delete_sub_list(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    cid = cb.data.split("_")[1]
    rows = subs(cid)
    if not rows:
        return await cb.answer("لا توجد ألعاب.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🎮 {name}", callback_data=f"seldelprodsub_{sid}", style="primary") for sid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر اللعبة:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("seldelprodsub_"))
async def product_delete_prod_list(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    sid = cb.data.split("_")[1]
    rows = products(sid)
    if not rows:
        return await cb.answer("لا توجد منتجات داخل هذه اللعبة.", show_alert=True)
    btn_list = [types.InlineKeyboardButton(text=f"🗑️ {name}", callback_data=f"delprod_{pid}", style="danger") for pid, name in rows]
    keyboard = chunk_buttons(btn_list, 2)
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    try:
        await cb.message.edit_text("اختر المنتج للحذف:", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


@dp.callback_query(F.data.startswith("delprod_"))
async def product_delete(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await safe_answer(cb, "غير مصرح.", True)
    await safe_answer(cb)
    pid = cb.data.split("_")[1]
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM products WHERE mhd_id=%s", (int(pid),))
    conn.commit()
    c.close()
    conn.close()
    try:
        await cb.message.edit_text("✅ تم حذف المنتج بنجاح.")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e


# ================= التشغيل الرئيسي =================
async def main():
    init_db()
    await start_web_server()
    
    # حذف أي Webhook قديم معلق لتفادي TelegramConflictError
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"⚠️ تحذير مسح الويب هوك: {e}")

    try:
        await bot.set_my_commands([types.BotCommand(command="start", description="بدء تشغيل البوت والعودة للرئيسية")])
    except Exception as e:
        print(f"⚠️ خطأ تعيين القائمة: {e}")

    print("🚀 Bot Started Successfully!")
    asyncio.create_task(auto_send_deposits_pdf_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
