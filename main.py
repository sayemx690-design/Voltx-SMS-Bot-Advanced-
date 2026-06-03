#!/usr/bin/env python3

import json, os, time, threading, gc, sys, signal, re, html
from datetime import timedelta
from uuid import uuid4

import requests
import telebot
from telebot import types

TOKEN = "8787548050:AAFnHcIrm7UrruvLISVzv7gD3_3d4A4juL8"
ADMIN_ID = 6668016879
GROUP_ID = -1003727266573
API_KEY = "MMIQ13IODBV"
API_BASE = "https://2oo9.cloud/api/MXS47FLFX0U/project/tetragonexvoltxsms/@public/api"
DATA_FILE = "data.json"
OTP_TIMEOUT = 600
CHECK_INTERVAL = 0.3
CLEAN_INTERVAL = 30
GROUP_LINK = "https://t.me/seven_otp"
STOP = threading.Event()

DEFAULT_COUNTRY = {
    "flag": "\U0001f1f9\U0001f1ec",
    "name": "Togo",
    "rid": "22898"
}

import logging

class _Fmt(logging.Formatter):
    C = {'INFO': '\033[92m', 'WARNING': '\033[93m',
         'ERROR': '\033[91m', 'CRITICAL': '\033[91m\033[1m', 'R': '\033[0m'}
    def format(self, r):
        return f"{self.C.get(r.levelname,'')}{super().format(r)}{self.C['R']}"

_h = logging.StreamHandler()
_h.setFormatter(_Fmt('%(asctime)s | %(message)s', datefmt='%H:%M:%S'))
logging.basicConfig(handlers=[_h], level=logging.INFO)
log = logging.getLogger("seven_otp")


class DB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except:
            return self._reset()

    def _reset(self):
        d = {"users": {}, "banned": {}, "countries": {}, "active_numbers": {}, "maintenance": False}
        self._write(d)
        return d

    def _write(self, d=None):
        d = d or self._data
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(d, f, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            log.error(f"[✕] DB write | {e}")

    def get(self, k, d=None):
        with self.lock:
            return self._data.get(k, d)

    def init_user(self, uid):
        uid = str(uid)
        with self.lock:
            if uid not in self._data["users"]:
                self._data["users"][uid] = {}
                self._write()

    def incr(self, uid, field, n=1):
        uid = str(uid)
        with self.lock:
            u = self._data["users"].get(uid)
            if u is None:
                self._data["users"][uid] = {}
                u = self._data["users"][uid]
            u[field] = u.get(field, 0) + n
            self._write()

    def is_banned(self, uid):
        return str(uid) in self._data.get("banned", {})

    def ban(self, uid):
        with self.lock:
            self._data["banned"][str(uid)] = True
            self._write()

    def unban(self, uid):
        with self.lock:
            self._data["banned"].pop(str(uid), None)
            self._write()

    def is_maintenance(self):
        return self._data.get("maintenance", False)

    def toggle_maintenance(self):
        with self.lock:
            self._data["maintenance"] = not self._data.get("maintenance", False)
            self._write()
            return self._data["maintenance"]

    def set_countries(self, c):
        with self.lock:
            self._data["countries"] = c
            self._write()

    def add_active_number(self, number, country_key, user_id):
        with self.lock:
            self._data["active_numbers"][number] = {
                "number": number,
                "country_key": country_key,
                "user_id": user_id,
                "created_at": time.time(),
                "expires_at": time.time() + OTP_TIMEOUT
            }
            self._write()

    def remove_active_number(self, number):
        with self.lock:
            self._data["active_numbers"].pop(number, None)
            self._write()

    def get_expired_active(self):
        now = time.time()
        with self.lock:
            return [(n, i) for n, i in self._data["active_numbers"].items()
                    if now >= i.get("expires_at", i.get("created_at", 0) + OTP_TIMEOUT)]

    def ensure_default_country(self):
        with self.lock:
            if not self._data["countries"]:
                self._data["countries"]["togo"] = dict(DEFAULT_COUNTRY)
                self._write()
                log.info("[✓] Default country Togo auto-created")


class API:
    def __init__(self):
        self.sess = requests.Session()
        self.sess.headers.update({"mauthapi": API_KEY, "Content-Type": "application/json"})
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        self.sess.mount("https://", adapter)

    def _call(self, method, path, **kw):
        url = f"{API_BASE}{path}"
        try:
            r = self.sess.request(method, url, timeout=5, **kw)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and j.get("meta", {}).get("code") == 200:
                    return j.get("data")
                log.warning(f"[ ] API meta")
            else:
                log.warning(f"[ ] HTTP {r.status_code}")
        except:
            log.warning(f"[ ] net error")
        return None

    def get_number(self, rid):
        r = self._call("POST", "/getnum", json={"rid": str(rid)})
        if isinstance(r, dict):
            return r.get("full_number") or r.get("no_plus_number") or r.get("national_number")
        return None

    def fetch_otps_fast(self):
        try:
            r = self.sess.get(f"{API_BASE}/success-otp", timeout=3)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and j.get("meta", {}).get("code") == 200:
                    d = j.get("data")
                    if isinstance(d, dict):
                        return d.get("otps") or []
        except:
            pass
        return []


def mask_num(n):
    if len(n) <= 7:
        return n
    return n[:5] + "\u2665" * (len(n) - 7) + n[-2:]

def get_ram():
    try:
        with open("/proc/self/status") as f:
            for l in f:
                if l.startswith("VmRSS:"):
                    return int(l.split()[1])
    except:
        pass
    return 0

def fmt_ram(kb):
    if kb >= 1048576:
        return f"{kb/1048576:.2f} GB"
    if kb >= 1024:
        return f"{kb/1024:.2f} MB"
    return f"{kb} KB"


class Bot:
    def __init__(self):
        self.db = DB(DATA_FILE)
        self.db.ensure_default_country()
        self.api = API()
        self.bot = telebot.TeleBot(TOKEN, parse_mode="HTML", num_threads=1)
        self.sessions = {}
        self.slock = threading.Lock()
        self.states = {}
        self.start_time = time.time()
        self._clean_expired_on_start()
        self._setup()
        self._start_threads()

    def _clean_expired_on_start(self):
        expired = self.db.get_expired_active()
        for num, info in expired:
            uid = info.get("user_id")
            ckey = info.get("country_key", "")
            if uid:
                self.db.incr(uid, "active", -1)
            self.db.remove_active_number(num)
            log.warning(f"[✕] Expired on load | {num} | {uid}")
        if expired:
            log.info(f"[✓] Cleaned {len(expired)} expired numbers on start")

    def _set(self, uid, v):
        with self.slock:
            self.states[uid] = v

    def _get(self, uid):
        with self.slock:
            return self.states.get(uid)

    def _clr(self, uid):
        with self.slock:
            self.states.pop(uid, None)

    def _add(self, uid, ckey, number, chat_id, msg_id, admin_msg_id=None, user_name="", user_username=""):
        sid = uuid4().hex[:8]
        s = {"id": sid, "user_id": uid, "country_key": ckey, "number": number,
             "chat_id": chat_id, "message_id": msg_id, "admin_msg_id": admin_msg_id,
             "user_name": user_name, "user_username": user_username,
             "created_at": time.time(), "otp": None, "sms": None}
        with self.slock:
            self.sessions[sid] = s
        self.db.add_active_number(number, ckey, uid)
        return sid

    def _del(self, sid):
        with self.slock:
            self.sessions.pop(sid, None)

    def _waiting(self):
        with self.slock:
            return {s["number"]: s for s in self.sessions.values() if s["otp"] is None and s["message_id"]}

    def _country_kb(self):
        countries = self.db.get("countries", {})
        mk = types.InlineKeyboardMarkup(row_width=2)
        items = sorted(countries.items())
        for i in range(0, len(items), 2):
            row = items[i:i+2]
            btns = [types.InlineKeyboardButton(f'{c["flag"]} {c["name"]}', callback_data=f"c:{k}") for k, c in row]
            mk.add(*btns)
        return mk

    def _num_kb(self, ckey):
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("\U0001f195 Get Number", callback_data=f"n:{ckey}"),
            types.InlineKeyboardButton("\U0001f465 OTP Group", url=GROUP_LINK),
        )
        mk.add(types.InlineKeyboardButton("\U0001f30d Change Country", callback_data="menu"))
        return mk

    def _admin_kb(self):
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(
            types.InlineKeyboardButton("\U0001f6ab Ban", callback_data="aban"),
            types.InlineKeyboardButton("\u2705 Unban", callback_data="aunban"),
            types.InlineKeyboardButton("\U0001f4e2 Broadcast", callback_data="abroadcast"),
            types.InlineKeyboardButton("\U0001f30d Countries", callback_data="acountries"),
            types.InlineKeyboardButton("\U0001f465 Users", callback_data="ausers"),
            types.InlineKeyboardButton("\U0001f527 Maintenance", callback_data="amaint"),
        )
        return mk

    def _setup(self):
        bot = self.bot

        @bot.message_handler(commands=["start"])
        def start_cmd(m):
            uid = m.from_user.id
            if self.db.is_maintenance() and uid != ADMIN_ID:
                return bot.reply_to(m, "\U0001f527 Maintenance.")
            if self.db.is_banned(uid):
                return bot.reply_to(m, "\u26d4 Banned.")
            is_new = str(uid) not in self.db.get("users", {})
            self.db.init_user(uid)
            u = m.from_user
            nm = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
            un = "@" + u.username if u.username else "-"
            if is_new:
                bot.send_message(ADMIN_ID,
                    f"\U0001f530 New User\n" +
                    "\u2500" * 21 + "\n"
                    f"\u2622\ufe0f Name : {html.escape(nm.strip())}\n"
                    f"\U0001f7e0 User Name : {html.escape(un)}\n"
                    f"\U0001faaa Uid : <code>{uid}</code>")
                log.info(f"[+] New user | {nm.strip()} | {un} | {uid}")
            else:
                log.info(f"[+] User start | {nm.strip()} | {un} | {uid}")
            bot.reply_to(m, "<b>\U0001f4f1 𝘴ꫀꪜꫀꪀ Number &amp; OTP BOT</b>\n\nSelect a country to get a number.")
            bot.send_message(m.chat.id, "\U0001f30d <b>Select Country:</b>", reply_markup=self._country_kb())

        @bot.message_handler(commands=["admin"])
        def admin_cmd(m):
            if m.from_user.id != ADMIN_ID:
                return bot.reply_to(m, "\u26d4 Unauthorized.")
            self._clr(ADMIN_ID)
            bot.reply_to(m, self._admin_text(), reply_markup=self._admin_kb())

        @bot.message_handler(func=lambda m: self._get(m.from_user.id) is not None)
        def input_h(m):
            self._handle_input(m)

        @bot.callback_query_handler(func=lambda c: True)
        def cb(call):
            self._dispatch(call)

    def _dispatch(self, call):
        d = call.data
        try:
            if d.startswith("c:"):
                return self._cb_country(call, d[2:])
            if d.startswith("n:"):
                return self._cb_new(call, d[2:])
            if d == "menu":
                return self._cb_menu(call)
            if d.startswith("a") and call.from_user.id == ADMIN_ID:
                return self._cb_admin(call)
            self.bot.answer_callback_query(call.id)
        except Exception as e:
            log.error(f"[✕] Callback | {e}")
            try:
                self.bot.answer_callback_query(call.id, "\u274c Error", show_alert=True)
            except:
                pass

    def _no_number(self, call, c):
        uid = call.from_user.id
        chat = call.message.chat.id
        mid = call.message.message_id
        try:
            self.bot.delete_message(chat, mid)
        except:
            try:
                self.bot.edit_message_text("\u274c No numbers available.", chat, mid)
            except:
                pass
        self.bot.send_message(chat, "\u274c <b>No numbers available.</b>\n\n\U0001f30d Select a country:", reply_markup=self._country_kb())
        log.warning(f"[!] No numbers | {c['flag']} {c['name']} | {uid}")

    def _cb_country(self, call, key):
        uid = call.from_user.id
        chat = call.message.chat.id
        mid = call.message.message_id
        c = self.db.get("countries", {}).get(key)
        if not c or not c.get("rid"):
            return self.bot.answer_callback_query(call.id, "\u274c No RID", show_alert=True)
        self.bot.answer_callback_query(call.id)
        number = self.api.get_number(c["rid"])
        if not number:
            self._no_number(call, c)
            return
        self.db.incr(uid, "numbers")
        try:
            self.bot.edit_message_text(f"\u2705 Selected: {c['flag']} {c['name']}", chat, mid)
        except:
            pass
        user = call.from_user
        name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
        username = "@" + user.username if user.username else "-"
        user_info = {"name": name.strip(), "username": username}
        self._send_num(chat, uid, key, number, user_info)

    def _cb_new(self, call, key):
        uid = call.from_user.id
        chat = call.message.chat.id
        mid = call.message.message_id
        c = self.db.get("countries", {}).get(key)
        if not c or not c.get("rid"):
            return self.bot.answer_callback_query(call.id, "\u274c No RID", show_alert=True)
        self.bot.answer_callback_query(call.id)
        number = self.api.get_number(c["rid"])
        if not number:
            self._no_number(call, c)
            return
        self.db.incr(uid, "numbers")
        try:
            self.bot.edit_message_reply_markup(chat, mid, reply_markup=None)
        except:
            pass
        user = call.from_user
        name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
        username = "@" + user.username if user.username else "-"
        user_info = {"name": name.strip(), "username": username}
        self._send_num(chat, uid, key, number, user_info)

    def _cb_menu(self, call):
        uid = call.from_user.id
        chat = call.message.chat.id
        mid = call.message.message_id
        self.bot.answer_callback_query(call.id)
        try:
            self.bot.edit_message_reply_markup(chat, mid, reply_markup=None)
        except:
            pass
        self.bot.send_message(chat, "<b>\U0001f4f1 𝘴ꫀꪜꫀꪀ Number &amp; OTP BOT</b>\n\nSelect a country:", reply_markup=self._country_kb())
        log.info(f"[+] Change country | {uid}")

    def _send_num(self, chat, uid, ckey, number, user_info=None):
        num = number.lstrip("+")
        c = self.db.get("countries", {}).get(ckey, {"flag": "\U0001f3f3\ufe0f", "name": ckey})
        head = "\U0001f195 NEW NUMBER\n\u2500" + "\u2500" * 17 + "\n"
        text = (head +
                f'<blockquote>{c["flag"]} {c["name"]} | <code>{html.escape(num)}</code></blockquote>\n'
                f'<blockquote>\U0001f7e1 OTP - waiting for otp.....</blockquote>')
        sent = self.bot.send_message(chat, text, reply_markup=self._num_kb(ckey))

        user_name = (user_info or {}).get("name", "")
        user_username = (user_info or {}).get("username", "")
        admin_text = (head +
                      f'\U0001f530 User | {html.escape(user_name)} | {html.escape(user_username)} | <code>{uid}</code>\n'
                      f'<blockquote>{c["flag"]} {c["name"]} | <code>{html.escape(num)}</code></blockquote>\n'
                      f'<blockquote>\U0001f7e1 OTP - waiting for otp.....</blockquote>')
        try:
            admin_msg = self.bot.send_message(ADMIN_ID, admin_text)
            admin_msg_id = admin_msg.message_id
        except:
            admin_msg_id = None

        self._add(uid, ckey, number, chat, sent.message_id, admin_msg_id, user_name, user_username)
        c = self.db.get("countries", {}).get(ckey, {"flag": "\U0001f3f3\ufe0f", "name": ckey})
        log.info(f"[+] {c['flag']} {c['name']} | {num}")

    def _cb_admin(self, call):
        d = call.data
        uid = ADMIN_ID
        chat = call.message.chat.id
        mid = call.message.message_id
        self.bot.answer_callback_query(call.id)

        if d == "aban":
            self._set(uid, "ban")
            return self.bot.edit_message_text("Send user ID to <b>ban</b>:", chat, mid)

        if d == "aunban":
            self._set(uid, "unban")
            return self.bot.edit_message_text("Send user ID to <b>unban</b>:", chat, mid)

        if d == "abroadcast":
            self._set(uid, "broadcast")
            return self.bot.edit_message_text("\U0001f4e2 Send content to broadcast:", chat, mid)

        if d == "amaint":
            mode = self.db.toggle_maintenance()
            self.bot.edit_message_text(f"\U0001f527 Maintenance: {'ON' if mode else 'OFF'}", chat, mid)
            log.info(f"[+] Maintenance {'ON' if mode else 'OFF'}")
            return self.bot.send_message(chat, self._admin_text(), reply_markup=self._admin_kb())

        if d == "ausers":
            users = self.db.get("users", {})
            if not users:
                mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("\U0001f519 Back", callback_data="aback"))
                return self.bot.edit_message_text("No users.", chat, mid, reply_markup=mk)
            text = "<b>\U0001f465 Users</b>\n\n"
            for uid_k, u in sorted(users.items(), key=lambda x: x[1].get("numbers", 0) if isinstance(x[1], dict) else 0, reverse=True):
                n = u.get("numbers", 0) if isinstance(u, dict) else 0
                s = u.get("success", 0) if isinstance(u, dict) else 0
                a = u.get("active", 0) if isinstance(u, dict) else 0
                text += f"\U0001f194 <code>{uid_k}</code> | \U0001f4de {n} | \u2705 {s} | \U0001f7e2 {a}\n"
                if len(text) > 3900:
                    text += "..."
                    break
            mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("\U0001f519 Back", callback_data="aback"))
            return self.bot.edit_message_text(text, chat, mid, reply_markup=mk)

        if d == "aback":
            return self.bot.edit_message_text(self._admin_text(), chat, mid, reply_markup=self._admin_kb())

        if d == "acadd":
            self._set(uid, {"action": "add_c", "step": "name"})
            return self.bot.edit_message_text("Send country <b>name</b> (e.g. Bangladesh):", chat, mid)

        if d.startswith("acedit:"):
            key = d[7:]
            c = self.db.get("countries", {}).get(key)
            if not c:
                return
            self._set(uid, {"action": "edit_rid", "key": key})
            return self.bot.edit_message_text(f"Editing {c['flag']} {c['name']}\nCurrent RID: <code>{c.get('rid','?')}</code>\n\nSend new RID:", chat, mid)

        if d.startswith("acdel:"):
            key = d[6:]
            countries = self.db.get("countries", {})
            c = countries.get(key)
            if not c:
                return
            mk = types.InlineKeyboardMarkup(row_width=2).add(
                types.InlineKeyboardButton("\u2705 Yes", callback_data=f"aconfirm:{key}"),
                types.InlineKeyboardButton("\u274c No", callback_data="abackc"))
            return self.bot.edit_message_text(f"Delete {c['flag']} {c['name']}?", chat, mid, reply_markup=mk)

        if d.startswith("aconfirm:"):
            key = d[9:]
            countries = self.db.get("countries", {})
            if key in countries:
                name = countries[key]["name"]
                del countries[key]
                self.db.set_countries(countries)
                self.bot.edit_message_text(f"\u2705 Deleted {name}", chat, mid)
                log.info(f"[✓] Country deleted | {name}")
            return self.bot.send_message(chat, self._admin_text(), reply_markup=self._admin_kb())

        if d == "abackc":
            return self._show_countries(chat, mid)

        if d == "acountries":
            return self._show_countries(chat, mid)

    def _show_countries(self, chat, mid):
        countries = self.db.get("countries", {})
        mk = types.InlineKeyboardMarkup(row_width=2)
        mk.add(types.InlineKeyboardButton("\u2795 Add Country", callback_data="acadd"))
        for key, c in sorted(countries.items()):
            mk.add(
                types.InlineKeyboardButton(f"\u270f\ufe0f {c['flag']} {c['name']}", callback_data=f"acedit:{key}"),
                types.InlineKeyboardButton(f"\u274c {c['flag']} {c['name']}", callback_data=f"acdel:{key}"),
            )
        mk.add(types.InlineKeyboardButton("\U0001f519 Back", callback_data="aback"))
        self.bot.edit_message_text("\U0001f30d <b>Country Management</b>", chat, mid, reply_markup=mk)

    def _handle_input(self, m):
        uid = m.from_user.id
        if uid != ADMIN_ID:
            return
        state = self._get(uid)
        if not state:
            return
        text = m.text.strip()

        if state == "ban":
            self.db.ban(text)
            self.bot.reply_to(m, f"\u2705 Banned: <code>{text}</code>")
            self._clr(uid)
            log.warning(f"[!] Ban | {text}")
            return self.bot.send_message(uid, self._admin_text(), reply_markup=self._admin_kb())

        if state == "unban":
            self.db.unban(text)
            self.bot.reply_to(m, f"\u2705 Unbanned: <code>{text}</code>")
            self._clr(uid)
            log.info(f"[✓] Unban | {text}")
            return self.bot.send_message(uid, self._admin_text(), reply_markup=self._admin_kb())

        if state == "broadcast":
            self._broadcast(m)
            self._clr(uid)
            return self.bot.send_message(uid, self._admin_text(), reply_markup=self._admin_kb())

        if isinstance(state, dict):
            action = state.get("action")

            if action == "add_c":
                step = state.get("step")
                if step == "name":
                    if not text:
                        return
                    key = text.lower().replace(" ", "_")
                    self._set(uid, {"action": "add_c", "step": "flag", "data": {"name": text, "key": key}})
                    return self.bot.reply_to(m, "Send flag emoji (e.g. \U0001f1e7\U0001f1e9):")

                if step == "flag":
                    d = state.get("data", {})
                    d["flag"] = text
                    self._set(uid, {"action": "add_c", "step": "rid", "data": d})
                    return self.bot.reply_to(m, "Send range RID (digits only, e.g. <code>26134</code>):")

                if step == "rid":
                    if not text.isdigit():
                        return self.bot.reply_to(m, "RID must be digits. Try again:")
                    d = state.get("data", {})
                    countries = self.db.get("countries", {})
                    countries[d["key"]] = {"flag": d["flag"], "name": d["name"], "rid": text}
                    self.db.set_countries(countries)
                    self.bot.reply_to(m, f"\u2705 Added: {d['flag']} {d['name']} (RID: {text})")
                    self._clr(uid)
                    log.info(f"[✓] Country added | {d['flag']} {d['name']} | {text}")
                    return self.bot.send_message(uid, self._admin_text(), reply_markup=self._admin_kb())

            if action == "edit_rid":
                key = state.get("key")
                if not text.isdigit():
                    return self.bot.reply_to(m, "RID must be digits. Try again:")
                countries = self.db.get("countries", {})
                if key in countries:
                    countries[key]["rid"] = text
                    self.db.set_countries(countries)
                    self.bot.reply_to(m, f"\u2705 RID updated for {countries[key]['flag']} {countries[key]['name']} -> {text}")
                    log.info(f"[✓] RID updated | {countries[key]['flag']} {countries[key]['name']} -> {text}")
                self._clr(uid)
                return self.bot.send_message(uid, self._admin_text(), reply_markup=self._admin_kb())

    def _broadcast(self, m):
        users = list(self.db.get("users", {}).keys())
        if not users:
            return self.bot.reply_to(m, "\u274c No users.")
        txt = m.text or m.caption or ""
        if not txt.strip():
            return self.bot.reply_to(m, "\u274c No text.")
        ok = fail = 0
        total = len(users)
        log.info(f"[+] Broadcast start | {total} users")
        status = self.bot.reply_to(m, f"\U0001f4e2 Broadcast: 0/{total}")
        for i, uid in enumerate(users, 1):
            try:
                self.bot.send_message(uid, txt)
                ok += 1
            except:
                fail += 1
            if i % 10 == 0 or i == total:
                try:
                    self.bot.edit_message_text(f"\U0001f4e2 Broadcast: {ok}/{total} | \u2705 {ok} \u274c {fail}", status.chat.id, status.message_id)
                except:
                    pass
                log.info(f"[+] Broadcast {i}/{total} | \u2705 {ok} \u274c {fail}")
        try:
            self.bot.edit_message_text(f"\U0001f4e2 Broadcast done: \u2705 {ok} \u274c {fail}", status.chat.id, status.message_id)
        except:
            pass
        log.info(f"[✓] Broadcast done | {ok}/{total} | \u274c {fail}")

    def _admin_text(self):
        users = self.db.get("users", {})
        total_nums = sum(u.get("numbers", 0) for u in users.values() if isinstance(u, dict))
        total_otp = sum(u.get("success", 0) for u in users.values() if isinstance(u, dict))
        active = sum(u.get("active", 0) for u in users.values() if isinstance(u, dict))
        active_db = len(self.db.get("active_numbers", {}))
        members = len(users)
        uptime = str(timedelta(seconds=int(time.time() - self.start_time)))
        ram = fmt_ram(get_ram())
        countries = self.db.get("countries", {})
        return (f"<b>\U0001f451 Admin Panel</b>\n\n"
                f"\U0001f4de Numbers: <code>{total_nums}</code>\n"
                f"\U0001f4ca OTPs: <code>{total_otp}</code>\n"
                f"\U0001f465 Members: <code>{members}</code>\n"
                f"\U0001f4be RAM: <code>{ram}</code>\n"
                f"\U0001f7e2 Active Now: <code>{active_db}</code>\n"
                f"\U0001f30d Countries: <code>{len(countries)}</code>\n"
                f"\u23f1 Uptime: <code>{uptime}</code>\n"
                f"\U0001f527 Maint: <code>{'ON' if self.db.is_maintenance() else 'OFF'}</code>")

    def _otp_loop(self):
        while not STOP.is_set():
            try:
                waiting = self._waiting()
                if waiting:
                    raw = self.api.fetch_otps_fast()
                    if raw:
                        now = time.time()
                        for item in raw:
                            if not isinstance(item, dict):
                                continue
                            num = item.get("number", "")
                            msg = item.get("message", "")
                            if not num or not msg:
                                continue
                            sess = waiting.get(num) or waiting.get("+" + num)
                            if not sess:
                                continue
                            m = re.search(r"(?<!\d)(\d{4,8})(?!\d)", msg)
                            if not m:
                                continue
                            self._process(sess, m.group(1), msg)
            except Exception as e:
                log.error(f"[✕] OTP loop | {e}")
            time.sleep(CHECK_INTERVAL)

    def _process(self, sess, otp, sms):
        chat = sess["chat_id"]
        mid = sess["message_id"]
        raw_num = sess["number"]
        num = raw_num.lstrip("+")
        uid = sess["user_id"]
        ckey = sess["country_key"]
        c = self.db.get("countries", {}).get(ckey, {"flag": "\U0001f3f3\ufe0f", "name": ckey})

        try:
            self.bot.delete_message(chat, mid)
        except:
            pass

        admin_mid = sess.get("admin_msg_id")
        u_name = sess.get("user_name", "")
        u_uname = sess.get("user_username", "")
        if admin_mid:
            try:
                self.bot.delete_message(ADMIN_ID, admin_mid)
            except:
                pass
        otp_head = "\u2705 OTP RECEIVED\n\u2500" + "\u2500" * 17 + "\n"
        admin_new = (otp_head +
                     f'\U0001f530 User | {html.escape(u_name)} | {html.escape(u_uname)} | <code>{uid}</code>\n'
                     f'<blockquote>{c["flag"]} {c["name"]} | Facebook | <code>{html.escape(num)}</code></blockquote>\n'
                     f'<blockquote>\U0001f5e8\ufe0fOTP | <code>{otp}</code></blockquote>')
        try:
            self.bot.send_message(ADMIN_ID, admin_new)
        except:
            pass

        text = (otp_head +
                f'<blockquote>{c["flag"]} {c["name"]} | Facebook | <code>{html.escape(num)}</code></blockquote>\n'
                f'<blockquote>\U0001f5e8\ufe0f OTP | <code>{otp}</code></blockquote>')
        try:
            sent = self.bot.send_message(chat, text, reply_markup=self._num_kb(ckey))
            with self.slock:
                if sess["id"] in self.sessions:
                    self.sessions[sess["id"]]["message_id"] = sent.message_id
                    self.sessions[sess["id"]]["otp"] = otp
                    self.sessions[sess["id"]]["sms"] = sms
        except Exception as e:
            log.error(f"[✕] OTP send | {e}")
            with self.slock:
                if sess["id"] in self.sessions:
                    self.sessions[sess["id"]]["otp"] = otp
                    self.sessions[sess["id"]]["sms"] = sms

        masked = mask_num(raw_num)
        fwd_kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("[ Developer ]", url=f"https://t.me/sevenx2007")
        )
        fwd = (otp_head +
               f'<blockquote>{c["flag"]} {c["name"]} | Facebook | <code>{html.escape(masked)}</code></blockquote>\n'
               f'<blockquote>\U0001f5e8\ufe0f OTP | <code>{otp}</code></blockquote>\n'
               f'\U0001f5e8\ufe0f Full sms:\n{html.escape(sms)}')
        try:
            self.bot.send_message(GROUP_ID, fwd, reply_markup=fwd_kb)
        except Exception as e:
            log.error(f"[✕] Forward | {e}")

        self.db.incr(uid, "success")
        self.db.incr(uid, "active", -1)
        self._del(sess["id"])
        if "facebook" in sms.lower():
            log.info(f"[✓] {c['flag']} {c['name']} | Facebook | {num}")

    def _clean_loop(self):
        while not STOP.is_set():
            try:
                time.sleep(CLEAN_INTERVAL)
                now = time.time()

                expired_sessions = []
                with self.slock:
                    for sid, s in list(self.sessions.items()):
                        if s["otp"] is None and (now - s["created_at"]) > OTP_TIMEOUT:
                            expired_sessions.append((sid, s))
                    for sid, s in expired_sessions:
                        u_name = s.get('user_name', '?')
                        raw = s['number']
                        log.warning(f"[✕] {u_name} | {raw} | expired")
                        try:
                            self.bot.delete_message(s["chat_id"], s["message_id"])
                        except:
                            pass
                        try:
                            self.bot.send_message(s["chat_id"],
                                "\u23f0 <b>Number Expired</b>\n\n"
                                f"<code>{raw.lstrip('+')}</code> time is over.\n\n"
                                "\U0001f30d Select a new country:",
                                reply_markup=self._country_kb())
                        except:
                            pass
                        self.db.remove_active_number(raw)
                        self.db.incr(s["user_id"], "active", -1)
                        self.sessions.pop(sid, None)

                active_nums = self.db.get("active_numbers", {})
                expired_nums = []
                for num, info in list(active_nums.items()):
                    deadline = info.get("expires_at", info.get("created_at", 0) + OTP_TIMEOUT)
                    if now >= deadline:
                        expired_nums.append(num)
                for num in expired_nums:
                    self.db.remove_active_number(num)
                    log.warning(f"[✕] ? | {num} | expired")

                if expired_sessions or expired_nums:
                    gc.collect()
            except Exception as e:
                log.error(f"[✕] Clean | {e}")

    def _start_threads(self):
        threading.Thread(target=self._otp_loop, daemon=True).start()
        threading.Thread(target=self._clean_loop, daemon=True).start()

    def run(self):
        log.info("[✓] Bot started")
        retry = 1
        while not STOP.is_set():
            try:
                self.bot.polling(non_stop=True, interval=1, timeout=30, long_polling_timeout=25,
                                 allowed_updates=["message", "callback_query"])
                retry = 1
            except telebot.apihelper.ApiTelegramException as e:
                log.error(f"[✕] API | {e}")
                if "401" in str(e):
                    log.critical("[✕] Invalid token.")
                    break
                STOP.wait(retry)
                retry = min(retry * 2, 60)
            except requests.exceptions.ConnectionError as e:
                log.error(f"[✕] Conn | {e}")
                STOP.wait(retry)
                retry = min(retry * 2, 60)
            except Exception as e:
                log.error(f"[✕] Poll | {e}")
                STOP.wait(retry)
                retry = min(retry * 2, 60)


if __name__ == "__main__":
    def shutdown(sig, frame):
        log.info("[✕] Shutdown")
        STOP.set()
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        Bot().run()
    except KeyboardInterrupt:
        STOP.set()
    except Exception as e:
        log.critical(f"[✕] Fatal | {e}")
        STOP.set()
        sys.exit(1)
