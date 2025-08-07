import os
import time
from datetime import datetime

import requests

from my_secrets import BOT_TOKEN

# === Конфигурация ===
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
ADMIN_IDS = [
    211570366,  # Игорь Асонов
    517348694,  # Мария Рыбалко
    189526817,  # Алена Абрамова
    1172339189,  # Елизавета Павлова
    196962881,  # Виктория Оськина
    300247573,  # Олеся Карпова
    403700929,
]  # Екатерина Каляева
# Вставь свои Telegram user_id
# CHAT_IDS_FILE = "chat_ids_master_load_reminder.txt"
CHAT_IDS_FILE = 'chat_ids_master_07.08.25.txt'
LOG_FILE = "logs.txt"
DELAY = 10

# === Вспомогательные функции ===


def log_broadcast(sender_name, message_text, chats=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {sender_name}:\n{message_text}\n\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
        if chats is not None:
            try:
                with open(chats, "r", encoding="utf-8") as c:
                    for line in c:
                        f.write(line)
            except:
                f.write("Chats file {chats} cannot be opened\n\n", chats)
        else:
            f.write("No chats file provided for logging\n\n")
        f.write("-----------------------------------------\n\n")


def get_updates(offset=None):
    url = API_URL + "getUpdates"
    # params = {'timeout': 1, 'offset': offset}

    payload = {"offset": offset, "limit": None, "timeout": DELAY}
    headers = {
        "accept": "application/json",
        "User-Agent": "Python",
        "content-type": "application/json",
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        return response.json()
    except requests.exceptions.Timeout:
        print("Timeout")
        return None
        # Maybe set up for a retry, or continue in a retry loop
    except requests.exceptions.TooManyRedirects:
        print("TooManyRedirects")
        return None
        # Tell the user their URL was bad and try a different one
    except requests.exceptions.RequestException as e:
        print("RequestException")
        print(e)
        # catastrophic error. bail.
        return None


def send_message(chat_id, text, markdown="Markdown"):
    url = API_URL + "sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": markdown}
    requests.post(url, data=data, timeout=DELAY)


def forward_message(chat_id, from_chat_id, message_id):
    url = API_URL + "copyMessage"
    data = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
    try:
        r = requests.post(url, data=data, timeout=DELAY, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'})
        if r.status_code != requests.codes.ok:
            print(f"Failed to forward message to {chat_id} with code {r.status_code}")
            return False
    except requests.exceptions as e:
        print(f"Failed to forward message to {chat_id} with code {r.status_code}")
        return False
    return True


def load_chat_ids():
    if not os.path.exists(CHAT_IDS_FILE):
        return set()
    with open(CHAT_IDS_FILE, "r", encoding="utf-8", errors="ignore") as f:
        return set(line.strip()[: line.strip().find(" ")] for line in f if line.strip())


def save_chat_id(chat_id):
    chat_ids = load_chat_ids()
    if chat_id not in chat_ids:
        with open(CHAT_IDS_FILE, "a") as f:
            f.write(f"{chat_id}\n")


def apply_markdown_entities(text, entities):
    result = text
    shift = 0
    # TODO there are problems in case of same offset (example: bold inside link)
    for e in sorted(entities, key=lambda x: x["offset"] + x["length"]):
        start = e["offset"]
        end = start + e["length"]
        t = text[start:end]

        if e["type"] == "bold":
            wrap = f"*{t}*"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        elif e["type"] == "italic":
            wrap = f"_{t}_"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        elif e["type"] == "code":
            wrap = f"`{t}`"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        elif e["type"] == "text_link":
            wrap = f"[{t}]({e['url']})"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        else:
            continue

        result = (
            result[: start + shift - delta_shift]
            + wrap
            + result[end + shift - delta_shift :]
        )
    #

    return result


# === Основной цикл ===


def main():
    print("Бот запущен (polling)...")
    offset = 216122217

    while True:
        try:
            updates = get_updates(offset)
            if updates is None:
                continue
            groups_ids = load_chat_ids()

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")
                if not message:
                    continue

                chat_id = str(message["chat"]["id"])
                user_id = message["from"]["id"]
                text = message.get("text", "")
                # entities = message.get('entities', [])

                # # Сохраняем групповые чаты
                # if message['chat']['type'] in ['group', 'supergroup']:
                #     save_chat_id(chat_id)

                # Команда администратора
                if text == "/start":
                    send_message(
                        chat_id,
                        "Добрый день! Я бот, отправляющий информацию. Админ бота @iasonov ",
                    )
                elif chat_id == str(user_id):  # общение в привате с ботом
                    if user_id in ADMIN_IDS:  # and text.startswith('/broadcast'):
                        # Пересылаем сообщение во все группы
                        for group_id in groups_ids:
                            forward_message(group_id, chat_id, message["message_id"])
                            time.sleep(DELAY)

                        # Получаем данные об отправителе
                        user = message.get("from", {})
                        first_name = user.get("first_name", "")
                        last_name = user.get("last_name", "")
                        username = user.get("username", "")

                        sender_name = f"{first_name} {last_name}".strip()
                        if username:
                            sender_name += f" (@{username})"

                        # Пробуем взять отформатированный текст
                        if "text" in message and "entities" in message:
                            msg_text = apply_markdown_entities(
                                message["text"], message["entities"]
                            )
                        else:
                            msg_text = message.get("text", "")

                        # Отправляем summary всем админам
                        summary_text = f"*Рассылка отправлена*\n\n*Отправитель:* {sender_name}\n\n*Текст:*\n{msg_text}"
                        for admin_id in ADMIN_IDS:
                            send_message(admin_id, summary_text)

                        # Логируем рассылку
                        log_broadcast(sender_name, msg_text, CHAT_IDS_FILE)
                    else:
                        send_message(
                            chat_id,
                            "Вы не относитесь к менеджерам и не можете рассылать информацию по группам. Обратитесь к @iasonov",
                        )

        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
