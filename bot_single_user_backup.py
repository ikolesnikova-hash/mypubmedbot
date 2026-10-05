"""Бот мониторинга PubMed с меню в Telegram.

Запуск:  python bot.py   (работает постоянно; остановить - Ctrl+C)
Темы, период и время проверки меняются кнопками в боте (/menu).
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
SEEN_FILE = os.path.join(HERE, "seen.json")
STATE_FILE = os.path.join(HERE, "state.json")

for line in open(os.path.join(HERE, ".env"), encoding="utf-8-sig"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT = str(os.environ["TG_CHAT"])
API = f"https://api.telegram.org/bot{TG_TOKEN}/"

DAYS_CHOICES = [1, 2, 3, 7, 14]
HOUR_CHOICES = [7, 9, 12, 18, 21]


# ---------- хранилище ----------
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE, encoding="utf-8"))
    topics = []
    old = os.path.join(HERE, "topics.txt")
    if os.path.exists(old):
        topics = [t.strip() for t in open(old, encoding="utf-8") if t.strip()]
    return {"topics": topics, "days": 2, "hour": 9, "awaiting_add": False,
            "last_run": datetime.date.today().isoformat()}


def save_state():
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def load_seen():
    return set(json.load(open(SEEN_FILE))) if os.path.exists(SEEN_FILE) else set()


state = load_state()
save_state()
seen = load_seen()


# ---------- Telegram ----------
def tg(method, **params):
    return requests.post(API + method, json=params, timeout=60).json()


def send(text, markup=None):
    tg("sendMessage", chat_id=TG_CHAT, text=text[:4000],
       disable_web_page_preview=True, **({"reply_markup": markup} if markup else {}))


def show(msg_id, text, markup):
    """Обновить сообщение с меню (или отправить новое)."""
    if msg_id:
        r = tg("editMessageText", chat_id=TG_CHAT, message_id=msg_id, text=text,
               reply_markup=markup)
        if r.get("ok") or "not modified" in str(r.get("description", "")):
            return
    send(text, markup)


def btn(text, data):
    return {"text": text, "callback_data": data}


def menu_markup():
    return {"inline_keyboard": [
        [btn("📋 Мои темы", "list")],
        [btn("➕ Добавить тему", "add")],
        [btn("🔍 Проверить сейчас", "check")],
        [btn(f"📅 Период поиска: {state['days']} дн.", "days"),
         btn(f"⏰ Проверка в {state['hour']}:00", "hour")],
    ]}


def show_menu(msg_id=None):
    show(msg_id, "Меню мониторинга PubMed:", menu_markup())


def show_list(msg_id=None):
    rows = [[btn(f"🗑 {t[:55]}", f"del:{i}")] for i, t in enumerate(state["topics"])]
    rows.append([btn("⬅️ Меню", "menu")])
    text = ("Ваши темы (нажмите, чтобы удалить):" if state["topics"]
            else "Тем пока нет. Добавьте через меню.")
    show(msg_id, text, {"inline_keyboard": rows})


# ---------- PubMed ----------
def search(topic, days):
    r = requests.get(EU + "esearch.fcgi", params={
        "db": "pubmed", "term": topic, "reldate": days, "datetype": "edat",
        "retmax": 20, "sort": "date", "retmode": "json"}, timeout=30)
    return r.json()["esearchresult"]["idlist"]


def fetch(pmid):
    r = requests.get(EU + "efetch.fcgi", params={
        "db": "pubmed", "id": pmid, "retmode": "xml"}, timeout=30)
    root = ET.fromstring(r.content)
    title = "".join(root.find(".//ArticleTitle").itertext())
    abstract = " ".join("".join(a.itertext()) for a in root.findall(".//AbstractText"))
    return title, abstract


def run_check(days):
    sent = 0
    for topic in state["topics"]:
        try:
            ids = search(topic, days)
        except Exception:
            traceback.print_exc()
            send(f"⚠️ Не удалось выполнить поиск по теме: {topic[:80]}")
            continue
        for pmid in ids:
            if pmid in seen:
                continue
            try:
                time.sleep(0.4)  # PubMed просит не чаще 3 запросов в секунду
                title, abstract = fetch(pmid)
            except Exception:
                traceback.print_exc()
                continue
            seen.add(pmid)
            json.dump(sorted(seen), open(SEEN_FILE, "w"))
            if not abstract:
                continue
            send(f"📄 {title}\n\n{abstract[:1500]}\n\nhttps://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            sent += 1
            time.sleep(1)
    return sent


def check_and_report(days):
    n = run_check(days)
    if n == 0:
        send(f"Новых статей за {days} дн. нет.")


# ---------- обработка сообщений ----------
def handle_callback(cb):
    data = cb["data"]
    msg_id = cb["message"]["message_id"]
    tg("answerCallbackQuery", callback_query_id=cb["id"])
    if data == "menu":
        state["awaiting_add"] = False
        show_menu(msg_id)
    elif data == "list":
        show_list(msg_id)
    elif data == "add":
        state["awaiting_add"] = True
        send("Пришлите тему одним сообщением, например:\n"
             "epigenetic clock\n"
             "или с операторами: \"epigenetic clock\" AND cancer\n"
             "(английские слова работают лучше всего)")
    elif data == "check":
        send("Ищу...")
        check_and_report(state["days"])
    elif data == "days":
        i = DAYS_CHOICES.index(state["days"]) if state["days"] in DAYS_CHOICES else -1
        state["days"] = DAYS_CHOICES[(i + 1) % len(DAYS_CHOICES)]
        show_menu(msg_id)
    elif data == "hour":
        i = HOUR_CHOICES.index(state["hour"]) if state["hour"] in HOUR_CHOICES else -1
        state["hour"] = HOUR_CHOICES[(i + 1) % len(HOUR_CHOICES)]
        show_menu(msg_id)
    elif data.startswith("del:"):
        i = int(data[4:])
        if 0 <= i < len(state["topics"]):
            state["topics"].pop(i)
        show_list(msg_id)
    save_state()


def handle_message(msg):
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/"):
        state["awaiting_add"] = False
        if text.startswith("/list"):
            show_list()
        else:
            show_menu()
    elif state["awaiting_add"]:
        state["awaiting_add"] = False
        state["topics"].append(text)
        send(f"✅ Тема добавлена: {text}")
        show_menu()
    else:
        show_menu()
    save_state()


def handle(update):
    if "callback_query" in update:
        cb = update["callback_query"]
        if str(cb["message"]["chat"]["id"]) == TG_CHAT:
            handle_callback(cb)
    elif "message" in update:
        msg = update["message"]
        if str(msg["chat"]["id"]) == TG_CHAT:  # чужим не отвечаем
            handle_message(msg)


def scheduled():
    now = datetime.datetime.now()
    today = now.date().isoformat()
    if now.hour >= state["hour"] and state["last_run"] != today:
        state["last_run"] = today
        save_state()
        run_check(state["days"])


# ---------- главный цикл ----------
tg("setMyCommands", commands=[{"command": "menu", "description": "Меню"},
                              {"command": "list", "description": "Мои темы"}])
print("Бот запущен. Остановить: Ctrl+C")
offset = None
while True:
    try:
        params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
        if offset:
            params["offset"] = offset
        for upd in tg("getUpdates", **params).get("result", []):
            offset = upd["update_id"] + 1
            try:
                handle(upd)
            except Exception:
                traceback.print_exc()
        scheduled()
    except Exception:
        traceback.print_exc()
        time.sleep(5)
