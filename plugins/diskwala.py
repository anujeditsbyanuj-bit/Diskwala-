from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName, InputPeerSelf, DataJSON
from urllib.parse import urlparse, unquote
import motor.motor_asyncio, asyncio, requests, time, re, os, shutil, uuid, json, logging
from config import *
from pyrogram.errors import UserIsBlocked, QueryIdInvalid, UserNotParticipant
import qrcode, io, pytz as pt
import datetime as dl
from collections import defaultdict
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery
import aiohttp
from urllib.parse import quote
import requests
import time

user_orders: dict[str, dict] = {}
active_qr_sessions: dict[int, str] = {}
user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_free_trial_used: set[int] = set()  # backed by DB below, this is just a fast-path cache

mongo = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)[DB_NAME]
db, dumps_col, meta_col, cache_col = mongo.users, mongo.dumps, mongo.meta, mongo.file_cache
tg = TelegramClient(StringSession(SESSION), API_ID, API_HASH)

BOT_USERNAME, APP_SHORT_NAME = "sky577bot", "open"
API_URL = "https://api2.diskwala.net/api/diskwala/download"
RE = re.compile(r"https?://(?:www\.)?diskwala\.com/app/[A-Za-z0-9]+")
CMDS = ["start", "stats", "adddump", "deldump", "dumps", "addpaid", "delpremium", "premium","broadcast"]

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
ARIA2C_PATH = shutil.which("aria2c") or "aria2c"

MAX_CONCURRENT_DOWNLOADS = 6
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

_PCT_RE = re.compile(r"\((\d{1,3})%\)")
_SPEED_RE = re.compile(r"DL:([\d.]+\w+)")

_link_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()

# ── Free-tier / premium config ───────────────────────────────────
FREE_LIMIT = 10  # number of full downloads a non-premium user gets before stream-only mode


async def update_qr_timer(client, msg, order_id, orders, sessions, user_id, total_seconds=300):
    for remaining in range(total_seconds, 0, -5):
        await asyncio.sleep(5)
        if order_id not in orders or orders[order_id]["used"]:
            return
        mins, secs = divmod(remaining - 5, 60)
        try:
            await msg.edit_reply_markup(
                InlineKeyboardMarkup([[InlineKeyboardButton(
                    f"⏳ 𝖤𝖷𝖯𝖨𝖱𝖤𝖲 𝖨𝖭 {mins:02d}:{secs:02d}", callback_data="none")]])
            )
        except Exception:
            pass

# ── Premium purchase & payment verification ───────────────────────
@Client.on_callback_query(filters.regex("^(buy_premium|plan_|free_trial|retry_)"))
async def buy_and_verify_handler(client: Client, query: CallbackQuery):
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    # -------- PLAN MENU --------
    if data == "buy_premium":
        rows, row = [], []
        for i, p in enumerate(PLANS):
            row.append(InlineKeyboardButton(f"{p['label']} · {p['price']}", callback_data=f"plan_{i}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)


        return await query.message.edit_text(
            "💳 <b>𝗖𝗛𝗢𝗢𝗦𝗘 𝗔 𝗣𝗟𝗔𝗡</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🏦 <b>𝖯𝖠𝖸𝖳𝖬 • 𝖴𝖯𝖨 • 𝖯𝖧𝖮𝖭𝖤𝖯𝖤 • 𝖦𝖯𝖠𝖸</b>\n\n"
            "✦ Pʀᴇᴍɪᴜᴍ ᴀᴄᴛɪᴠᴀᴛᴇs ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴀғᴛᴇʀ ᴘᴀʏᴍᴇɴᴛ\n"
            "✦ Uɴʟɪᴍɪᴛᴇᴅ ғᴜʟʟ ᴅᴏᴡɴʟᴏᴀᴅs ✅\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👇 Sᴇʟᴇᴄᴛ ʏᴏᴜʀ ᴘʟᴀɴ:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows)
        )

    # -------- RETRY --------
    elif data.startswith("retry_"):
        order = user_orders.get(data.split("_", 1)[1])
        if not order or order["used"]:
            return await query.answer("⚠️ Invalid or already used order!", show_alert=True)
        for i, p in enumerate(PLANS):
            if p["days"] == order["days"]:
                data = f"plan_{i}"
                break

    # -------- GENERATE QR --------
    if data.startswith("plan_"):
        async with user_locks[user_id]:

            if user_id in active_qr_sessions:
                return await query.answer("⚠️ You have a pending payment. Complete it first!", show_alert=True)

            plan = PLANS[int(data.split("_")[1])]
            amount = int(plan["price"].replace("₹", ""))
            order_id = f"DSKWALA-{user_id}_{int(time.time())}"
            account_used = ACTIVE_PAYMENT

            active_qr_sessions[user_id] = order_id
            user_orders[order_id] = {
                "user": user_id, "amount": amount, "days": plan["days"],
                "used": False, "account": account_used,
            }

            upi = (f"upi://pay?pa={PAYMENT_ACCOUNTS[account_used]['upi']}&pn=Premium"
                   f"&am={amount}&cu=INR&tn={order_id}&tr={order_id}")
            
            wait_qrmsg = await query.message.reply_sticker("CAACAgEAAxkBAAEBXB9qWOBi8ijl1-QJYCBjIhOd1xsrFAACPAoAAsesyEaOswABHsPR5XQeBA")

            qr = qrcode.make(upi)
            bio = io.BytesIO(); bio.name = "qr.png"
            qr.save(bio); bio.seek(0)

            msg = await client.send_photo(
                query.message.chat.id, bio,
                caption=(
                    "<blockquote>✦ <b>𝖯𝗅𝖾𝖺𝗌𝖾 𝖼𝗈𝗆𝗉𝗅𝖾𝗍𝖾 𝗍𝗁𝖾 𝖿𝗈𝗅𝗅𝗈𝗐𝗂𝗇𝗀 𝗉𝖺𝗒𝗆𝖾𝗇𝗍:</b></blockquote>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"❐ <b>𝖠𝖬𝖮𝖴𝖭𝖳 :</b>  {plan['price']}\n"
                    f"≡ <b>𝖯𝖫𝖠𝖭 :</b>  {plan['label']}\n"
                    f"❐ <b>𝖮𝖱𝖣𝖤𝖱 𝖨𝖣 :</b>  <code>{order_id}</code>\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<b>⏳𝖥𝗈𝗅𝗅𝗈𝗐 𝗍𝗁𝖾 𝖨𝗇𝗌𝗍𝗋𝗎𝖼𝗍𝗂𝗈𝗇:-</b>\n"
                    "1️⃣ 𝖲𝖼𝖺𝗇 𝗍𝗁𝖾 𝖰𝖱 𝖺𝗇𝖽 𝗉𝖺𝗒 𝗍𝗁𝖾 𝖺𝗆𝗈𝗎𝗇𝗍.\n"
                    "2️⃣ 𝖠𝖿𝗍𝖾𝗋 𝖯𝖺𝗒𝗆𝖾𝗇𝗍, 𝖢𝗅𝗂𝖼𝗄 𝗈𝗇 ✅ 𝖯𝖺𝗒𝗆𝖾𝗇𝗍 𝖣𝗈𝗇𝖾 𝖡𝗎𝗍𝗍𝗈𝗇.\n"
                    "3️⃣ 𝖡𝗈𝗍 𝗐𝗂𝗅𝗅 𝗏𝖾𝗋𝗂𝖿𝗒 𝗒𝗈𝗎𝗋 𝗉𝖺𝗒𝗆𝖾𝗇𝗍 𝖺𝗇𝖽 𝖺𝖼𝗍𝗂𝗏𝖺𝗍𝖾 𝗒𝗈𝗎𝗋 𝗉𝗅𝖺𝗇.\n\n"
                    "🗒𝖳𝗁𝗂𝗌 𝖰𝖱 𝖢𝗈𝖽𝖾 𝗐𝗂𝗅𝗅 𝖾𝗑𝗉𝗂𝗋𝖾 𝗂𝗇 5 𝖬𝗂𝗇𝗎𝗍𝖾𝗌. 𝖼𝗈𝗆𝗉𝗅𝖾𝗍𝖾 𝗍𝗁𝖾 𝗉𝖺𝗒𝗆𝖾𝗇𝗍 𝗂𝗇 5 𝗆𝗂𝗇𝗎𝗍𝖾𝗌\n\n"
                    "<blockquote>𝖨𝖿 𝗒𝗈𝗎 𝖺𝗅𝗋𝖾𝖺𝖽𝗒 𝗉𝖺𝗂𝖽 𝗍𝗁𝖾 𝖺𝗆𝗈𝗎𝗇𝗍 𝖺𝗇𝖽 𝗌𝗍𝗂𝗅𝗅 𝗌𝗁𝗈𝗐𝗂𝗇𝗀 𝖯𝖺𝗒𝗆𝖾𝗇𝗍 𝖭𝗈𝗍 𝖥𝗈𝗎𝗇𝖽 𝗍𝗁𝖾𝗇 𝖢𝗈𝗇𝗍𝖺𝖼𝗍 "
                    "<a href='https://t.me/DumpAdminBot?text=<b>Hey%20my%20order%20ID%20is%20{order_id}.%20\n\nI%20paid%20but%20my%20premium%20is%20not%20activated.</b>'>@DumpAdminBot</a></blockquote></b>"
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏳ 𝖤𝖷𝖯𝖨𝖱𝖤𝖲 𝖨𝖭 05:00", callback_data="none")
                ]]),
                parse_mode=ParseMode.HTML
            )
            await wait_qrmsg.delete()

            asyncio.create_task(
                update_qr_timer(client, msg, order_id, user_orders, active_qr_sessions, user_id, total_seconds=300)
            )

            async def check():
                merchant = PAYMENT_ACCOUNTS[user_orders[order_id]["account"]]["merchant"]

                async with aiohttp.ClientSession() as s:
                    for _ in range(60):
                        await asyncio.sleep(5)
                        try:
                            async with s.get(f"{PAYMENT_VERIFY_API}?mid={merchant}&oid={order_id}") as r:
                                d = await r.json()
                        except Exception:
                            continue

                        if d.get("STATUS") == "TXN_SUCCESS" and int(float(d.get("TXNAMOUNT", 0))) == amount:
                            if user_orders[order_id]["used"]:
                                return
                            user_orders[order_id]["used"] = True
                            active_qr_sessions.pop(user_id, None)

                            await add_premium(user_id, plan["days"])

                            try:
                                await msg.delete()
                            except Exception:
                                pass

                            readable = (
                                dl.datetime.now(pt.timezone("Asia/Kolkata")) +
                                dl.timedelta(days=plan["days"])
                            ).strftime("%d-%b-%Y %I:%M %p")

                            channel_id = LOG_CHANNELS.get(user_orders[order_id]["account"])
                            if channel_id:
                                user = query.from_user
                                mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
                                try:
                                    await client.send_message(
                                        channel_id,
                                        f"💎 <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗔𝗖𝗧𝗜𝗩𝗔𝗧𝗘𝗗</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"👤 <b>Usᴇʀ :</b> {mention}\n"
                                        f"🆔 <b>ID :</b> <code>{user_id}</code>\n"
                                        f"📦 <b>Pʟᴀɴ :</b> {plan['label']} Pʀᴇᴍɪᴜᴍ\n"
                                        f"💰 <b>Aᴍᴏᴜɴᴛ :</b> ₹{amount}\n"
                                        f"🧾 <b>Oʀᴅᴇʀ :</b> <code>{order_id}</code>\n"
                                        f"⏳ <b>Eхᴘɪʀᴇs :</b> {readable}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━",
                                        parse_mode=ParseMode.HTML
                                    )
                                except Exception:
                                    pass

                            return await client.send_message(
                                query.message.chat.id,
                                f"🎉 <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗔𝗖𝗧𝗜𝗩𝗔𝗧𝗘𝗗 !</b>\n\n"
                                f"📦 <b>Pʟᴀɴ :</b> {plan['label']}\n"
                                f"⏳ <b>Eхᴘɪʀᴇs :</b> {readable}\n\n"
                                f"🔥 Eɴᴊᴏʏ ʏᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss!",
                                parse_mode=ParseMode.HTML
                            )

                active_qr_sessions.pop(user_id, None)
                try:
                    await msg.delete()
                except Exception:
                    pass

                await client.send_message(
                    query.message.chat.id,
                    f"⚠️ <b>𝗤𝗥 𝗘𝗫𝗣𝗜𝗥𝗘𝗗</b>\n\n"
                    f"Yᴏᴜʀ ᴘᴀʏᴍᴇɴᴛ ᴡɪɴᴅᴏᴡ ʜᴀs ᴄʟᴏsᴇᴅ.\n\n"
                    f"🧾 <b>Oʀᴅᴇʀ :</b> <code>{order_id}</code>\n"
                    f"↩️ ᴜsᴇ /verify <code>{order_id}</code> ɪғ ʏᴏᴜ ᴀʟʀᴇᴀᴅʏ ᴘᴀɪᴅ.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 𝖳𝖱𝖸 𝖠𝖦𝖠𝖨𝖭", callback_data=f"retry_{order_id}")],
                        [InlineKeyboardButton("🆘 𝖲𝖴𝖯𝖯𝖮𝖱𝖳", url="https://t.me/DumpAdminBot")]
                    ])
                )

            asyncio.create_task(check())


# ── Force-sub ──────────────────────────────────────────────────────
async def is_subscribed(app: Client, user_id: int) -> bool:
    try:
        member = await app.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return member.status not in ("left", "kicked", "banned")
    except UserNotParticipant:
        return False
    except Exception:
        return True


def join_prompt_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 𝖩𝗈𝗂𝗇 𝖢𝗁𝖺𝗇𝗇𝖾𝗅", url=f"https://t.me/{FORCE_SUB_CHANNEL}")],
        [InlineKeyboardButton("🔄 𝖳𝗋𝗒 𝖠𝗀𝖺𝗂𝗇", callback_data="check_sub")],
    ])


@Client.on_callback_query(filters.regex("^check_sub$"))
async def check_sub_cb(app: Client, cq):
    if await is_subscribed(app, cq.from_user.id):
        await cq.message.edit_text("✅ 𝖳𝗁𝖺𝗇𝗄𝗌 𝖿𝗈𝗋 𝗃𝗈𝗂𝗇𝗂𝗇𝗀! 𝖭𝗈𝗐 𝗌𝖾𝗇𝖽 𝗆𝖾 𝖺 𝖣𝗂𝗌𝗄𝗐𝖺𝗅𝖺 𝗅𝗂𝗇𝗄.")
    else:
        await cq.answer("❌ You haven't joined yet.", show_alert=True)


# ── Helpers ────────────────────────────────────────────────────────
def human_size(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


async def add_bandwidth(n: int):
    await meta_col.update_one({"_id": "bandwidth"}, {"$inc": {"bytes": n}}, upsert=True)


async def get_bandwidth() -> int:
    doc = await meta_col.find_one({"_id": "bandwidth"})
    return doc["bytes"] if doc else 0


async def get_dumps() -> list[int]:
    return [d["_id"] async for d in dumps_col.find({})]


async def add_dump(cid: int):
    await dumps_col.update_one({"_id": cid}, {"$set": {"_id": cid}}, upsert=True)


async def del_dump(cid: int):
    await dumps_col.delete_one({"_id": cid})


async def copy_to_dumps(app: Client, sent: Message, dump_ids: list[int]):
    async def _copy(cid):
        try:
            await app.copy_message(cid, sent.chat.id, sent.id)
        except Exception:
            pass
    if dump_ids:
        await asyncio.gather(*[_copy(c) for c in dump_ids])


async def get_cache(link: str):
    return await cache_col.find_one({"_id": link})


async def save_cache(link: str, chat_id: int, msg_id: int, name: str, size: int):
    await cache_col.update_one(
        {"_id": link},
        {"$set": {"chat_id": chat_id, "message_id": msg_id, "file_name": name,
                   "size": size, "cached_at": time.time()}},
        upsert=True,
    )


async def get_link_lock(link: str) -> asyncio.Lock:
    async with _locks_guard:
        return _link_locks.setdefault(link, asyncio.Lock())


async def try_deliver_from_cache(app: Client, m: Message, msg: Message, link: str, tag: str) -> bool:
    cached = await get_cache(link)
    if not cached:
        return False
    try:
        await msg.edit_text(f"<b>⚡ 𝖢𝖠𝖢𝖧𝖤𝖣 — 𝖨𝖭𝖲𝖳𝖠𝖭𝖳 𝖣𝖤𝖫𝖨𝖵𝖤𝖱𝖸 {tag}</b>\n\n<code>{cached['file_name']}</code>")
        await app.copy_message(m.chat.id, cached["chat_id"], cached["message_id"])
        await msg.delete()
        return True
    except Exception:
        return False


async def add_premium(user_id: int, days: float):
    """Extends premium by `days` from now OR from current expiry, whichever is later."""
    doc = await db.find_one({"_id": user_id})
    now = dl.datetime.now(pt.utc)

    current_expiry = doc.get("premium_until") if doc else None
    if current_expiry is not None and current_expiry.tzinfo is None:
        # MongoDB returns naive datetimes even if stored as UTC-aware — reattach UTC
        current_expiry = pt.utc.localize(current_expiry)

    base = current_expiry if (current_expiry and current_expiry > now) else now
    new_expiry = base + dl.timedelta(days=days)

    await db.update_one(
        {"_id": user_id},
        {"$set": {"premium_until": new_expiry}},
        upsert=True,
    )


async def is_premium(user_id: int) -> bool:
    doc = await db.find_one({"_id": user_id})
    if not doc or not doc.get("premium_until"):
        return False
    expiry = doc["premium_until"]
    if expiry.tzinfo is None:
        expiry = pt.utc.localize(expiry)
    return expiry > dl.datetime.now(pt.utc)

async def get_premium_expiry(user_id: int):
    doc = await db.find_one({"_id": user_id})
    expiry = doc.get("premium_until") if doc else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = pt.utc.localize(expiry)
    return expiry


async def set_premium(user_id: int, value: bool):
    await db.update_one({"_id": user_id}, {"$set": {"premium": value}}, upsert=True)


async def get_free_used(user_id: int) -> int:
    doc = await db.find_one({"_id": user_id})
    return doc.get("free_used", 0) if doc else 0


async def increment_free_used(user_id: int):
    await db.update_one({"_id": user_id}, {"$inc": {"free_used": 1}}, upsert=True)


async def can_full_download(user_id: int) -> bool:
    if await is_premium(user_id):
        return True
    return await get_free_used(user_id) < FREE_LIMIT


def premium_prompt_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 𝖦𝖾𝗍 𝖯𝗋𝖾𝗆𝗂𝗎𝗆", callback_data="buy_premium")],
    ])


# ─────────────────────────── VIDEO METADATA ───────────────────────────
async def get_video_metadata(path: str):
    try:
        p = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=width,height",
            "-of", "json", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await p.communicate()
        data = json.loads(stdout.decode().strip())
        duration = float(data.get("format", {}).get("duration", 0))
        s = data.get("streams", [{}])[0]
        return duration, int(s.get("width", 0)), int(s.get("height", 0))
    except Exception as e:
        logging.warning(f"ffprobe metadata failed: {e}")
        return 0, 0, 0


async def get_video_info(path: str) -> dict:
    dur, w, h = await get_video_metadata(path)
    return {"duration": int(dur), "width": w or 1280, "height": h or 720}


async def generate_thumbnail(video_path: str, output_path: str, time_position: int = 10) -> str | None:
    try:
        duration, w, h = await get_video_metadata(video_path)
        w, h = w or 1280, h or 720

        if duration and duration > 1:
            time_position = min(time_position, max(1, int(duration * 0.5)))
        else:
            time_position = 0

        vf = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p,scale={min(w, 720)}:-2"

        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "panic",
            "-ss", str(time_position), "-i", video_path,
            "-vframes", "1", "-vf", vf, "-q:v", "1", "-pix_fmt", "yuv420p", output_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(p.communicate(), timeout=15)
        except asyncio.TimeoutError:
            p.kill()
            return None

        return output_path if p.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024 else None
    except Exception as e:
        logging.warning(f"Thumbnail failed: {e}")
        return None


# ── Telethon token + API fetch ──────────────────────────────────────
async def get_init():
    if not tg.is_connected():
        await tg.connect()
    bot = await tg.get_input_entity(BOT_USERNAME)
    r = await tg(RequestAppWebViewRequest(
        peer=InputPeerSelf(),
        app=InputBotAppShortName(bot_id=bot, short_name=APP_SHORT_NAME),
        platform="android", write_allowed=True, start_param="", theme_params=DataJSON("{}"),
    ))
    return unquote(urlparse(r.url).fragment.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion=", 1)[0])

from urllib.parse import quote
import requests
import time

def fetch(link, auth):
    headers = {
        "Authorization": f"Bearer {auth}",
        "X-Bot-Id": "diskwala",
        "Origin": "https://miniapp.diskwala.net",
        "Referer": "https://miniapp.diskwala.net/",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0",
    }

    # Detect service
    if "flezen.com" in link.lower():
        download_api = "https://api2.diskwala.net/api/flezen/download"
        status_api = "https://api2.diskwala.net/api/flezen/status?link="
    else:
        download_api = "https://api2.diskwala.net/api/diskwala/download"
        status_api = "https://api2.diskwala.net/api/diskwala/status?link="

    # Start job
    r = requests.post(
        download_api,
        headers=headers,
        json={"link": link},
        timeout=60,
    )

    data = r.json()

    if not data.get("ok"):
        raise Exception(data.get("error", "Unknown API Error"))

    # Poll status
    status_url = status_api + quote(link, safe="")

    while True:
        r = requests.get(status_url, headers=headers, timeout=60)
        data = r.json()

        if not data.get("ok"):
            raise Exception(data.get("error", "Unknown API Error"))

        status = data.get("status", "").lower()

        if status == "pending":
            time.sleep(2)
            continue

        if status == "done":
            file = data.get("file")
            if not file:
                raise Exception(f"No file returned\n{data}")

            # Normalize Flezen response to Diskwala format
            if "flezen.com" in link.lower():
                return {
                    "name": file["name"],
                    "size": file["size"],
                    "downloadUrl": file["url"],
                    "thumb": None,
                    "type": file.get("type"),
                }

            # Diskwala response
            return {
                "name": file["name"],
                "size": file["size"],
                "downloadUrl": file["downloadUrl"],
                "thumb": file.get("thumb"),
                "extension": file.get("extension"),
            }

        raise Exception(data)


async def run_aria2c(url: str, out_path: str, status_msg: Message, tag: str):
    if not (shutil.which(ARIA2C_PATH) or os.path.isfile(ARIA2C_PATH)):
        raise RuntimeError("aria2c not found. Install it (e.g. `apt install aria2`).")

    out_dir, out_name = os.path.dirname(out_path) or ".", os.path.basename(out_path)
    cmd = [ARIA2C_PATH, url, "-d", out_dir, "-o", out_name, "-x", "16", "-s", "16",
           "-k", "1M", "--summary-interval=2", "--console-log-level=warn", "--allow-overwrite=true"]

    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.STDOUT)
    last_edit = 0
    async for raw in process.stdout:
        line = raw.decode(errors="ignore").strip()
        pct_m, spd_m = _PCT_RE.search(line), _SPEED_RE.search(line)
        if pct_m and time.time() - last_edit > 3:
            speed = spd_m.group(1) if spd_m else "—"
            try:
                await status_msg.edit_text(
                    f"<b>⬇️ 𝖣𝖮𝖶𝖭𝖫𝖮𝖠𝖣𝖨𝖭𝖦... {tag}</b>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                        f"⏳ {pct_m.group(1)}% • {speed}/s", callback_data="none")]])
                )
            except Exception:
                pass
            last_edit = time.time()

    await process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"aria2c exited with code {process.returncode}")


# ── Commands ──────────────────────────────────────────────────────
@Client.on_message(filters.command("start") & filters.private)
async def start(app: Client, m: Message):
    await db.update_one({"_id": m.from_user.id}, {"$set": {"name": m.from_user.first_name}}, upsert=True)
    try:
        await m.reply("<b>👋 Send me a Diskwala or Flezen link.</b>")
    except UserIsBlocked:
        pass


@Client.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats(_, m):
    users, dumps, bw = await db.count_documents({}), await get_dumps(), await get_bandwidth()
    premium_count = await db.count_documents({"premium": True})
    await m.reply(
        f"👥 Users: {users}\n"
        f"💎 Premium: {premium_count}\n"
        f"📡 Dump channels: {len(dumps)}\n"
        f"📊 Bandwidth used: <code>{human_size(bw)}</code>"
    )

PREMIUM_STICKER = "CAACAgEAAxkBAAEBXB5qWOBdtSOL6gI_ul76mhFn7JdFdQACOAwAAu3CwUb78hzfVdJX-R4E"


import re as _re

_DURATION_RE = _re.compile(r"^(\d+(?:\.\d+)?)([mhdwMy])$")

_DURATION_UNITS = {
    "m": 1 / 1440,        # minutes → fraction of a day
    "h": 1 / 24,          # hours → fraction of a day
    "d": 1,                # days
    "w": 7,                # weeks
    "M": 30,                # months (approx)
    "y": 365,               # years (approx)
}


def parse_duration(text: str) -> float | None:
    """Parses '7d', '1m', '2h', '3w', '1M', '1y' into a number of days. Returns None if invalid."""
    match = _DURATION_RE.match(text.strip())
    if not match:
        return None
    value, unit = match.groups()
    days = float(value) * _DURATION_UNITS[unit]
    if not (0 < days <= 3650):  # sanity cap ~10 years
        return None
    return days


@Client.on_message(filters.command("addpaid") & filters.user(OWNER_ID))
async def addpaid(client, m):
    """Usage: /addpaid <user_id> <duration>
    Duration examples: 30m (30 min), 2h (2 hours), 7d (7 days), 2w (2 weeks), 1M (1 month), 1y (1 year)
    """
    parts = m.text.split()[1:]
    if len(parts) != 2:
        return await m.reply(
            "⚠️ Usage: <code>/addpaid user_id duration</code>\n"
            "Examples:\n"
            "<code>/addpaid 123456789 7d</code> — 7 days\n"
            "<code>/addpaid 123456789 1m</code> — 1 minute\n"
            "<code>/addpaid 123456789 1M</code> — 1 month\n"
            "<code>/addpaid 123456789 2h</code> — 2 hours\n"
            "<code>/addpaid 123456789 1y</code> — 1 year"
        )

    uid_str, dur_str = parts

    try:
        uid = int(uid_str)
    except ValueError:
        return await m.reply(f"⚠️ Invalid user ID: <code>{uid_str}</code>")

    days = parse_duration(dur_str)
    if days is None:
        return await m.reply(
            f"⚠️ Invalid duration: <code>{dur_str}</code>\n"
            "Use a number + unit: <code>m</code>=minute, <code>h</code>=hour, <code>d</code>=day, "
            "<code>w</code>=week, <code>M</code>=month, <code>y</code>=year\n"
            "Example: <code>7d</code>, <code>1M</code>, <code>2h</code>"
        )

    await add_premium(uid, days)

    expiry = await get_premium_expiry(uid)
    readable = expiry.astimezone(pt.timezone("Asia/Kolkata")).strftime("%d-%b-%Y %I:%M %p")

    # Notify the user directly — best-effort
    notified = True
    try:
        try:
            await client.send_sticker(uid, PREMIUM_STICKER)
        except Exception:
            pass

        await client.send_message(
            uid,
            f"🎉 <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗔𝗖𝗧𝗜𝗩𝗔𝗧𝗘𝗗 !</b>\n\n"
            f"📦 <b>Gʀᴀɴᴛᴇᴅ :</b> {dur_str}\n"
            f"⏳ <b>Eхᴘɪʀᴇs :</b> {readable}\n\n"
            f"🔥 Eɴᴊᴏʏ ʏᴏᴜʀ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss!",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        notified = False

    text = f"💎 Premium granted to <code>{uid}</code> for <b>{dur_str}</b>\n⏳ Expires: <code>{readable}</code>"
    if not notified:
        text += "\n🔕 Couldn't DM the user (blocked bot / never started chat)."
    await m.reply(text)


@Client.on_message(filters.command("delpremium") & filters.user(OWNER_ID))
async def delpremium(_, m):
    """Usage: /delpremium <user_id> [user_id...]"""
    parts = m.text.split()[1:]
    if not parts:
        return await m.reply("⚠️ Usage: <code>/delpremium user_id1 user_id2 ...</code>")

    done, failed = [], []
    for p in parts:
        try:
            uid = int(p)
        except ValueError:
            failed.append(p)
            continue
        await db.update_one({"_id": uid}, {"$unset": {"premium_until": ""}})
        done.append(str(uid))

    text = ""
    if done:
        text += f"🗑️ Premium revoked: <code>{', '.join(done)}</code>\n"
    if failed:
        text += f"❌ Invalid IDs: <code>{', '.join(failed)}</code>"
    await m.reply(text or "Nothing to remove.")

def premium_prompt_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 𝖡𝗎𝗒 𝖯𝗋𝖾𝗆𝗂𝗎𝗆", callback_data="buy_premium")],
    ])


@Client.on_message(filters.command("premium") & filters.private)
async def premium_status(_, m):
    uid = m.from_user.id
    if await is_premium(uid):
        expiry = await get_premium_expiry(uid)
        readable = expiry.astimezone(pt.timezone("Asia/Kolkata")).strftime("%d-%b-%Y %I:%M %p")
        await m.reply(f"💎 You're <b>Premium</b> until <code>{readable}</code>.")
    else:
        used = await get_free_used(uid)
        left = max(0, FREE_LIMIT - used)
        await m.reply(
            f"🆓 Free downloads left: <b>{left}/{FREE_LIMIT}</b>\n"
            f"After that, links stream only (in-app), not downloadable.\n\n"
            f"Upgrade to Premium for unlimited full downloads.",
            reply_markup=premium_prompt_markup(),
        )


@Client.on_message(filters.command("adddump") & filters.user(OWNER_ID))
async def adddump(_, m):
    parts = m.text.split()[1:]
    if not parts:
        return await m.reply("⚠️ Usage: <code>/adddump -100xxxx -100yyyy ...</code>")
    added, failed = [], []
    for p in parts:
        try:
            cid = int(p)
        except ValueError:
            failed.append(p); continue
        await add_dump(cid); added.append(str(cid))
    text = (f"✅ Added: <code>{', '.join(added)}</code>\n" if added else "") + \
           (f"❌ Invalid: <code>{', '.join(failed)}</code>" if failed else "")
    await m.reply(text or "Nothing to add.")


@Client.on_message(filters.command("deldump") & filters.user(OWNER_ID))
async def deldump(_, m):
    parts = m.text.split()[1:]
    if not parts:
        return await m.reply("⚠️ Usage: <code>/deldump -100xxxx</code>")
    removed = []
    for p in parts:
        try:
            cid = int(p)
        except ValueError:
            continue
        await del_dump(cid); removed.append(str(cid))
    await m.reply(f"🗑️ Removed: <code>{', '.join(removed)}</code>" if removed else "Nothing removed.")


@Client.on_message(filters.command("dumps") & filters.user(OWNER_ID))
async def list_dumps(_, m):
    dumps = await get_dumps()
    await m.reply("<b>📡 Dump channels:</b>\n" + "\n".join(f"• <code>{d}</code>" for d in dumps)
                  if dumps else "No dump channels configured.")


@Client.on_callback_query(filters.regex("^none$"))
async def ignore_cb(_, cq):
    try:
        await cq.answer()
    except QueryIdInvalid:
        pass
    except Exception:
        pass


async def deliver_stream_only(m: Message, msg: Message, link: str, tag: str):
    try:
        f = await asyncio.to_thread(fetch, link, await get_init())

        file_name = f["name"]
        download_url = f["downloadUrl"]
        size = f["size"]
        thumb = f["thumb"]

        # Send thumbnail as spoiler
        if thumb:
            await m.reply_photo(
                photo=thumb,
                has_spoiler=True,
                caption=f"""<b>🔒 𝖥𝖱𝖤𝖤 𝖫𝖨𝖬𝖨𝖳 𝖱𝖤𝖠𝖢𝖧𝖤𝖣 {tag}</b>

<blockquote expandable>
📂 <code>{file_name}</code>
💾 <code>{f['size']/1048576:.2f} MB</code>
</blockquote>

𝖸𝗈𝗎 𝖼𝖺𝗇 𝗌𝗍𝗂𝗅𝗅 𝗌𝗍𝗋𝖾𝖺𝗆 𝗂𝗍 𝖻𝖾𝗅𝗈𝗐.
𝖴𝗉𝗀𝗋𝖺𝖽𝖾 𝗍𝗈 𝖯𝗋𝖾𝗆𝗂𝗎𝗆 𝖿𝗈𝗋 𝖿𝗎𝗅𝗅 𝖽𝗈𝗐𝗇𝗅𝗈𝖺𝖽𝗌.
""",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "▶️ 𝖮𝖭𝖫𝖨𝖭𝖤 𝖯𝖫𝖠𝖸𝖤𝖱",
                        web_app=WebAppInfo(url=download_url)
                    )],
                    [InlineKeyboardButton(
                        "💎 𝖦𝖾𝗍 𝖯𝗋𝖾𝗆𝗂𝗎𝗆",
                        callback_data="buy_premium"
                    )]
                ])
            )

            await msg.delete()

        else:
            await msg.edit_text(
                f"""<b>🔒 𝖥𝖱𝖤𝖤 𝖫𝖨𝖬𝖨𝖳 𝖱𝖤𝖠𝖢𝖧𝖤𝖣 {tag}</b>

<blockquote expandable>
📂 <code>{file_name}</code>
💾 <code>{f['size']/1048576:.2f} MB</code>
</blockquote>""",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "▶️ 𝖮𝖭𝖫𝖨𝖭𝖤 𝖯𝖫𝖠𝖸𝖤𝖱",
                        web_app=WebAppInfo(url=download_url)
                    )],
                    [InlineKeyboardButton(
                        "💎 𝖦𝖾𝗍 𝖯𝗋𝖾𝗆𝗂𝗎𝗆",
                        callback_data="buy_premium"
                    )]
                ])
            )

    except Exception as e:
        try:
            await msg.edit_text(f"<b>❌ 𝖤𝗋𝗋𝗈𝗋 {tag}</b>\n<code>{e}</code>")
        except:
            pass


# ── Per-link worker (full download + upload) ──────────────────────
async def process_link(app: Client, m: Message, link: str, idx: int, total: int):
    tag = f"[{idx}/{total}]"
    msg = await m.reply(f"<blockquote><b>⚡ 𝖥𝖤𝖳𝖢𝖧𝖨𝖭𝖦 𝖫𝖨𝖭𝖪 {tag}...</b></blockquote>")

    if await try_deliver_from_cache(app, m, msg, link, tag):
        return

    async with await get_link_lock(link):
        if await try_deliver_from_cache(app, m, msg, link, tag):
            return

        req_dir = os.path.join(DOWNLOAD_DIR, str(m.from_user.id), uuid.uuid4().hex[:8])
        os.makedirs(req_dir, exist_ok=True)

        try:
            f = await asyncio.to_thread(fetch, link, await get_init())
            file_name, download_url = f["name"], f["downloadUrl"]
            file_path = os.path.join(req_dir, file_name)

            async with download_semaphore:
                await msg.edit_text(
                    f"<b>⬇️ 𝖣𝖮𝖶𝖭𝖫𝖮𝖠𝖣𝖨𝖭𝖦... {tag}</b>\n\n<code>{file_name}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏳ 0%", callback_data="none")]])
                )
                await run_aria2c(download_url, file_path, msg, tag)

            try:
                actual_size = os.path.getsize(file_path)
                await add_bandwidth(actual_size)
            except Exception:
                actual_size = f.get("size", 0)

            info = await get_video_info(file_path)
            thumb_path = os.path.join(req_dir, "thumb.jpg")
            thumb = await generate_thumbnail(file_path, thumb_path)

            last_edit = time.time()

            async def progress(current, total_bytes):
                nonlocal last_edit
                if time.time() - last_edit > 4:
                    pct = current * 100 / total_bytes
                    try:
                        await msg.edit_text(
                            f"<b>📤 𝖴𝖯𝖫𝖮𝖠𝖣𝖨𝖭𝖦... {tag}</b>\n\n<code>{file_name}</code>",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                f"⏳ {pct:.1f}%", callback_data="none")]])
                        )
                    except Exception:
                        pass
                    last_edit = time.time()

            dump_ids = await get_dumps()

            upload_kwargs = dict(
                file_name=file_name,
                caption=f"<code>{file_name}</code>",
                duration=info["duration"], width=info["width"], height=info["height"],
                thumb=thumb, supports_streaming=True, progress=progress,
            )

            if dump_ids:
                primary = dump_ids[0]
                cache_msg = await app.send_video(
                    primary, file_path,
                    caption=f"<code>{file_name}</code>",
                    duration=info["duration"], width=info["width"], height=info["height"],
                    thumb=thumb, supports_streaming=True, progress=progress,
                )
                await save_cache(link, primary, cache_msg.id, file_name, actual_size)
                sent = await app.copy_message(m.chat.id, primary, cache_msg.id)
                if dump_ids[1:]:
                    asyncio.create_task(copy_to_dumps(app, sent, dump_ids[1:]))
            else:
                await m.reply_video(file_path, **upload_kwargs)

            # Only count against free quota AFTER a successful full delivery
            if not await is_premium(m.from_user.id):
                await increment_free_used(m.from_user.id)

            try:
                await msg.delete()
            except Exception:
                pass

        except UserIsBlocked:
            pass

        except Exception as e:
            try:
                await msg.edit_text(f"<b>❌ 𝖤𝗋𝗋𝗈𝗋 {tag}</b>\n<code>{e}</code>")
            except Exception:
                pass

        finally:
            shutil.rmtree(req_dir, ignore_errors=True)

RE = re.compile(
    r"https?://(?:www\.)?(?:diskwala\.com/app/[A-Za-z0-9]+|flezen\.com/s/[A-Za-z0-9]+)",
    re.I
)

@Client.on_message(filters.private & (filters.text | filters.caption) & ~filters.command(CMDS))
async def diskwala(app: Client, m: Message):
    links = RE.findall(m.text or m.caption or "")
    if not links:
        return

    if not await is_subscribed(app, m.from_user.id):
        await m.reply(
            f"<b>🔒 𝖩𝗈𝗂𝗇 𝗈𝗎𝗋 𝖼𝗁𝖺𝗇𝗇𝖾𝗅 𝗍𝗈 𝗎𝗌𝖾 𝗍𝗁𝗂𝗌 𝖻𝗈𝗍</b>\n\n"
            f"Join @{FORCE_SUB_CHANNEL}, then send your link again.",
            reply_markup=join_prompt_markup(),
        )
        return

    uid = m.from_user.id
    premium = await is_premium(uid)
    used = await get_free_used(uid)
    total = len(links)

    tasks = []

    for i, link in enumerate(links):
        if premium or used < FREE_LIMIT:
            tasks.append(process_link(app, m, link, i + 1, total))
            if not premium:
                used += 1
        else:
            tag = f"[{i + 1}/{total}]"
            msg = await m.reply(
                f"<blockquote><b>⚡ 𝖥𝖤𝖳𝖢𝖧𝖨𝖭𝖦 𝖫𝖨𝖭𝖪 {tag}...</b></blockquote>"
            )
            tasks.append(deliver_stream_only(m, msg, link, tag))

    await asyncio.gather(*tasks, return_exceptions=True)

from pyrogram import filters
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, PeerIdInvalid
import asyncio

@Client.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(client, message):
    if not message.reply_to_message:
        return await message.reply(
            "<b>Reply to a message with <code>/broadcast</code>.</b>"
        )

    users = db.find({}, {"_id": 1})

    status = await message.reply("<b>📢 Broadcasting...</b>")

    sent = failed = blocked = deleted = 0

    async for user in users:
        uid = user["_id"]

        try:
            await message.reply_to_message.copy(uid)
            sent += 1

        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await message.reply_to_message.copy(uid)
                sent += 1
            except Exception:
                failed += 1

        except UserIsBlocked:
            blocked += 1

        except InputUserDeactivated:
            deleted += 1

        except PeerIdInvalid:
            failed += 1

        except Exception:
            failed += 1

    await status.edit_text(
        f"""<b>📢 Broadcast Completed</b>

✅ Sent: <code>{sent}</code>
🚫 Blocked: <code>{blocked}</code>
🗑 Deleted: <code>{deleted}</code>
❌ Failed: <code>{failed}</code>
👥 Total: <code>{sent+blocked+deleted+failed}</code>
"""
    )