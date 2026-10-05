"""Бот мониторинга PubMed с меню в Telegram - для нескольких пользователей.

Запуск:  python bot.py   (работает постоянно; остановить - Ctrl+C)
У каждого пользователя свои темы, период и час проверки (кнопки в /menu).
Новые пользователи входят по коду приглашения: /start КОД
"""
import datetime
import json
import os
import time
import traceback
import xml.etree.ElementTree as ET

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
SEEN_FILE = os.path.join(HERE, "seen.json")      # старые файлы одного пользователя,
STATE_FILE = os.path.join(HERE, "state.json")    # нужны только для переноса
USERS_FILE = os.path.join(HERE, "users.json")

for line in open(os.path.join(HERE, ".env"), encoding="utf-8-sig"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()

TG_TOKEN = os.environ["TG_TOKEN"]
OWNER = str(os.environ["TG_CHAT"])               # владелец: входит без кода
INVITE_CODE = os.environ.get("INVITE_CODE", "")
API = f"https://api.telegram.org/bot{TG_TOKEN}/"

DAYS_CHOICES = [1, 2, 3, 7, 14]
HOURS = list(range(7, 23))  # доступные часы проверки: 7:00 ... 22:00
MAX_TOPICS = 10


# ---------- хранилище ----------
def new_user(topics=None, days=2, hour=12, seen=None, last_run=""):
    return {"topics": topics or [], "days": days, "hour": hour,
            "awaiting_add": False, "last_run": last_run, "seen": seen or []}


def load_users():
    if os.path.exists(USERS_FILE):
        return json.load(open(USERS_FILE, encoding="utf-8"))
    # первый запуск: переносим данные владельца из старой версии
    st = json.load(open(STATE_FILE, encoding="utf-8")) if os.path.exists(STATE_FILE) else {}
    seen = json.load(open(SEEN_FILE)) if os.path.exists(SEEN_FILE) else []
    return {OWNER: new_user(st.get("topics"), st.get("days", 2), st.get("hour", 12),
                            seen, st.get("last_run", datetime.date.today().isoformat()))}


def save_users():
    json.dump(users, open(USERS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


users = load_users()
users.setdefault(OWNER, new_user())
save_users()


# ---------- Telegram ----------
def tg(method, **params):
    try:
        return requests.post(API + method, json=params, timeout=60).json()
    except requests.RequestException as e:
        # без исходной ошибки: в ней была бы ссылка с токеном
        raise RuntimeError(f"Нет связи с Telegram ({method}): {type(e).__name__}") from None


def send(chat, text, markup=None):
    return tg("sendMessage", chat_id=chat, text=text[:4000], disable_web_page_preview=True,
              **({"reply_markup": markup} if markup else {}))


def show(chat, msg_id, text, markup):
    """Обновить сообщение с меню; если нельзя - прислать новое вместо старого."""
    if msg_id:
        r = tg("editMessageText", chat_id=chat, message_id=msg_id, text=text,
               reply_markup=markup)
        if r.get("ok") or "not modified" in str(r.get("description", "")):
            return
    u = users[chat]
    if u.get("menu_msg"):  # убираем прежнее меню, чтобы не было дублей
        tg("deleteMessage", chat_id=chat, message_id=u["menu_msg"])
    u["menu_msg"] = send(chat, text, markup).get("result", {}).get("message_id")


def btn(text, data):
    return {"text": text, "callback_data": data}


def show_menu(chat, msg_id=None):
    u = users[chat]
    markup = {"inline_keyboard": [
        [btn("📋 Мои темы", "list")],
        [btn("➕ Добавить тему", "add")],
        [btn("🔍 Проверить сейчас", "check")],
        [btn(f"📅 Период поиска: {u['days']} дн.", "days"),
         btn(f"⏰ Проверка в {u['hour']}:00", "hour")],
    ]}
    show(chat, msg_id, "Меню мониторинга PubMed:", markup)


def show_hours(chat, msg_id=None):
    rows = [[btn(f"{h}:00", f"sethour:{h}") for h in HOURS[i:i + 4]]
            for i in range(0, len(HOURS), 4)]
    rows.append([btn("⬅️ Меню", "menu")])
    show(chat, msg_id, "Во сколько присылать подборку? (время компьютера, на котором работает бот)",
         {"inline_keyboard": rows})


def show_list(chat, msg_id=None):
    topics = users[chat]["topics"]
    rows = [[btn(f"🗑 {t[:55]}", f"del:{i}")] for i, t in enumerate(topics)]
    rows.append([btn("⬅️ Меню", "menu")])
    text = ("Ваши темы (нажмите, чтобы удалить):" if topics
            else "Тем пока нет. Добавьте через меню.")
    show(chat, msg_id, text, {"inline_keyboard": rows})


# ---------- PubMed (с кэшем, чтобы одинаковые запросы не повторять) ----------
search_cache, fetch_cache = {}, {}
last_manual_check = {}  # когда пользователь в последний раз нажимал «Проверить сейчас»


def search(topic, days):
    if (topic, days) not in search_cache:
        r = requests.get(EU + "esearch.fcgi", params={
            "db": "pubmed", "term": topic, "reldate": days, "datetype": "edat",
            "retmax": 20, "sort": "date", "retmode": "json"}, timeout=30)
        search_cache[(topic, days)] = r.json()["esearchresult"]["idlist"]
    return search_cache[(topic, days)]


def fetch(pmid):
    if pmid not in fetch_cache:
        time.sleep(0.4)  # PubMed просит не чаще 3 запросов в секунду
        r = requests.get(EU + "efetch.fcgi", params={
            "db": "pubmed", "id": pmid, "retmode": "xml"}, timeout=30)
        root = ET.fromstring(r.content)
        title = "".join(root.find(".//ArticleTitle").itertext())
        abstract = " ".join("".join(a.itertext()) for a in root.findall(".//AbstractText"))
        fetch_cache[pmid] = (title, abstract)
    return fetch_cache[pmid]


def run_check(chat):
    u = users[chat]
    sent = 0
    for topic in u["topics"]:
        try:
            ids = search(topic, u["days"])
        except Exception:
            traceback.print_exc()
            send(chat, f"⚠️ Не удалось выполнить поиск по теме: {topic[:80]}")
            continue
        for pmid in ids:
            if pmid in u["seen"]:
                continue
            try:
                title, abstract = fetch(pmid)
            except Exception:
                traceback.print_exc()
                continue
            u["seen"].append(pmid)
            save_users()
            if not abstract:
                continue
            send(chat, f"📄 {title}\n\n{abstract[:1500]}\n\nhttps://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            sent += 1
            time.sleep(1)
    return sent


# ---------- обработка сообщений ----------
def handle_callback(chat, cb):
    u = users[chat]
    data = cb["data"]
    msg_id = cb["message"]["message_id"]
    tg("answerCallbackQuery", callback_query_id=cb["id"])
    if data == "menu":
        u["awaiting_add"] = False
        show_menu(chat, msg_id)
    elif data == "list":
        show_list(chat, msg_id)
    elif data == "add":
        if len(u["topics"]) >= MAX_TOPICS:
            send(chat, f"Максимум {MAX_TOPICS} тем. Удалите одну в «Мои темы».")
        else:
            u["awaiting_add"] = True
            send(chat, "Пришлите тему одним сообщением, например:\n"
                       "epigenetic clock\n"
                       "или с операторами: \"epigenetic clock\" AND cancer\n"
                       "(английские слова работают лучше всего)")
    elif data == "check":
        if time.time() - last_manual_check.get(chat, 0) < 15:
            return  # повторное нажатие сразу после проверки - игнорируем
        send(chat, "Ищу...")
        if run_check(chat) == 0:
            send(chat, f"Новых статей за {u['days']} дн. нет.")
        search_cache.clear()
        fetch_cache.clear()
        last_manual_check[chat] = time.time()
    elif data == "days":
        i = DAYS_CHOICES.index(u["days"]) if u["days"] in DAYS_CHOICES else -1
        u["days"] = DAYS_CHOICES[(i + 1) % len(DAYS_CHOICES)]
        show_menu(chat, msg_id)
    elif data == "hour":
        show_hours(chat, msg_id)
    elif data.startswith("sethour:"):
        h = int(data[8:])
        if h in HOURS:
            u["hour"] = h
        show_menu(chat, msg_id)
    elif data.startswith("del:"):
        i = int(data[4:])
        if 0 <= i < len(u["topics"]):
            u["topics"].pop(i)
        show_list(chat, msg_id)
    save_users()


def handle_message(chat, msg):
    u = users[chat]
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/"):
        u["awaiting_add"] = False
        if text.startswith("/list"):
            show_list(chat)
        elif text.startswith("/users") and chat == OWNER:
            send(chat, f"Пользователей: {len(users)}")
        else:
            show_menu(chat)
    elif u["awaiting_add"]:
        u["awaiting_add"] = False
        u["topics"].append(text)
        send(chat, f"✅ Тема добавлена: {text}")
        show_menu(chat)
    else:
        show_menu(chat)
    save_users()


def try_register(chat, msg):
    """Пускает нового человека, только если он прислал /start КОД."""
    parts = (msg.get("text") or "").split()
    if len(parts) == 2 and parts[0].startswith("/start") and INVITE_CODE \
            and parts[1] == INVITE_CODE:
        users[chat] = new_user()
        save_users()
        send(chat, "Добро пожаловать! Бот присылает новые статьи из PubMed по вашим темам.\n"
                   "Нажмите «➕ Добавить тему» и пришлите запрос (лучше по-английски).")
        show_menu(chat)
    else:
        send(chat, "Это закрытый бот. Для входа отправьте: /start КОД")


def handle(update):
    if "callback_query" in update:
        cb = update["callback_query"]
        chat = str(cb["message"]["chat"]["id"])
        if chat in users:
            handle_callback(chat, cb)
    elif "message" in update:
        msg = update["message"]
        chat = str(msg["chat"]["id"])
        if chat in users:
            handle_message(chat, msg)
        else:
            try_register(chat, msg)


def scheduled():
    now = datetime.datetime.now()
    today = now.date().isoformat()
    for chat, u in list(users.items()):
        if now.hour >= u["hour"] and u["last_run"] != today:
            u["last_run"] = today
            save_users()
            try:
                run_check(chat)
            except Exception:
                traceback.print_exc()
    search_cache.clear()
    fetch_cache.clear()


# ---------- главный цикл ----------
tg("setMyCommands", commands=[{"command": "menu", "description": "Меню"},
                              {"command": "list", "description": "Мои темы"}])
print(f"Бот запущен. Пользователей: {len(users)}. Остановить: Ctrl+C", flush=True)
offset = None
while True:
    try:
        params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
        if offset:
            params["offset"] = offset
        for upd in tg("getUpdates", **params).get("result", []):
            offset = upd["update_id"] + 1
            print(datetime.datetime.now().strftime("%H:%M:%S"),
                  "получено сообщение №", upd["update_id"], flush=True)
            try:
                handle(upd)
            except Exception:
                traceback.print_exc()
        scheduled()
    except Exception:
        traceback.print_exc()
        time.sleep(5)
