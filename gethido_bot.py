import os
import time
from datetime import datetime
import csv
import json
import pandas as pd

import requests

from my_secrets import BOT_TOKEN

# === Конфигурация ===
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
ADMIN_IDS = [
    211570366,  # Игорь Асонов
    5037998042,  # Эдгар
    # 517348694,  # Мария Рыбалко
    # 189526817,  # Алена Абрамова
    # 1172339189,  # Елизавета Павлова
    196962881  # Виктория Оськина
    # 300247573,  # Олеся Карпова
    # 403700929, # Екатерина Каляева
]
# Telegram user_id администраторов бота.

PROGRAMS_CSV_FILE = "programs.csv"
LOG_FILE = "logs.txt"
STATE_FILE = "user_states.json"
STATE_BACKUP_FILE = "user_states.json.bak"
DELAY = 10

# Состояния пользователя
STATE_WAITING_FOR_TEXT = "waiting_for_text"
STATE_LEVEL_SELECTION = "level_selection"
STATE_PARTNER_SELECTION = "partner_selection"
STATE_CAMPUS_SELECTION = "campus_selection"
STATE_EARLYINVITATION_SELECTION = "earlyinvitation_selection"
STATE_PROGRAM_CONFIRMATION = "program_confirmation"
STATE_FINAL_CONFIRMATION = "final_confirmation"

# Глобальные переменные для хранения состояний пользователей
user_states = {}
user_data = {}

# === Вспомогательные функции ===


# Функции для управления состояниями пользователей
def load_user_states():
    """Load user states from file"""
    global user_states, user_data

    def _load_state_file(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('states', {}), data.get('data', {})

    try:
        if os.path.exists(STATE_FILE):
            user_states, user_data = _load_state_file(STATE_FILE)
        elif os.path.exists(STATE_BACKUP_FILE):
            user_states, user_data = _load_state_file(STATE_BACKUP_FILE)
    except Exception as e:
        print(f"Error loading user states from {STATE_FILE}: {e}")
        try:
            if os.path.exists(STATE_BACKUP_FILE):
                print(f"Trying backup state file: {STATE_BACKUP_FILE}")
                user_states, user_data = _load_state_file(STATE_BACKUP_FILE)
                print("Backup user state loaded successfully")
                return
        except Exception as backup_e:
            print(f"Error loading backup user states: {backup_e}")

        user_states = {}
        user_data = {}


def save_user_states():
    """Save user states to file"""
    try:
        state_payload = {
            'states': user_states,
            'data': user_data
        }

        temp_file = f"{STATE_FILE}.tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(state_payload, f, ensure_ascii=False, indent=2)

        if os.path.exists(STATE_FILE):
            with open(STATE_BACKUP_FILE, 'w', encoding='utf-8') as f:
                json.dump(state_payload, f, ensure_ascii=False, indent=2)

        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        print(f"Error saving user states: {e}")


def serialize_filtered_programs(filtered_programs):
    """Convert DataFrame to JSON-serializable records for persistence."""
    if filtered_programs is None:
        return []
    return filtered_programs.to_dict(orient='records')


def get_filtered_programs(user_id):
    """Get filtered programs for user as DataFrame."""
    records = get_user_data(user_id, "filtered_programs", [])
    if not records:
        return None
    try:
        return pd.DataFrame(records)
    except Exception as e:
        print(f"Error rebuilding filtered programs for user {user_id}: {e}")
        return None


def set_user_state(user_id, state):
    """Set user state"""
    user_states[str(user_id)] = state
    save_user_states()


def get_user_state(user_id):
    """Get user state"""
    return user_states.get(str(user_id))


def clear_user_state(user_id):
    """Clear user state and data"""
    user_id_str = str(user_id)
    if user_id_str in user_states:
        del user_states[user_id_str]
    if user_id_str in user_data:
        del user_data[user_id_str]
    save_user_states()


def set_user_data(user_id, key, value):
    """Set user data"""
    user_id_str = str(user_id)
    if user_id_str not in user_data:
        user_data[user_id_str] = {}
    user_data[user_id_str][key] = value
    save_user_states()


def get_user_data(user_id, key, default=None):
    """Get user data"""
    user_id_str = str(user_id)
    return user_data.get(user_id_str, {}).get(key, default)


# Функции для работы с CSV файлом програм
def load_programs():
    """Load programs from CSV file"""
    programs = None
    try:
        programs = pd.read_csv(PROGRAMS_CSV_FILE, sep=";")
        programs['tg_chat_id'] = pd.to_numeric(programs['tg_chat_id'], errors='coerce')
        programs = programs.dropna(subset=['tg_chat_id'])
        programs['tg_chat_id'] = programs['tg_chat_id'].astype(int)
        programs = programs[programs['tg_chat_id'] != 0].copy()
    except Exception as e:
        print(f"Error loading programs: {e}")

    return programs


def filter_programs(programs, level=None, partner_filter=None, campus_filter=None, earlyinvitation_filter=None):
    """Filter programs by level, early invitation and partner status, and campus"""
    filtered = programs.copy()

    # Filter by level
    if level and level != 'all': # master of bachelor
        filtered = filtered[filtered['level'] == level]

    # Filter by partner status
    if partner_filter and partner_filter != 'all':
        if partner_filter == 'no_partners':
            filtered = filtered[filtered['partner'] == 'нет']
        elif partner_filter == 'no_netology':
            filtered = filtered[filtered['partner'] != 'Нетология']
        elif partner_filter == 'no_carpovcourses':
            filtered = filtered[filtered['partner'] != 'Карпов Курсы']

    # Filter by campus
    if campus_filter and campus_filter != 'all':
        if campus_filter == 'msk':
            filtered = filtered[filtered['campus'] == 'Москва']
        elif campus_filter == 'nn':
            filtered = filtered[filtered['campus'] == 'Нижний Новгород']
        elif campus_filter == 'perm':
            filtered = filtered[filtered['campus'] == 'Пермь']
        elif campus_filter == 'spb':
            filtered = filtered[filtered['campus'] == 'Санкт-Петербург']


    # Filter by early invitation status
    if earlyinvitation_filter and earlyinvitation_filter != 'all':
        if earlyinvitation_filter == 'no':
            filtered = filtered[filtered['early_invitation'] == 'нет']
        elif earlyinvitation_filter == 'yes':
            filtered = filtered[filtered['early_invitation'] == 'да']

    return filtered


# Функции для создания клавиатур
def create_level_keyboard():
    """Create keyboard for level selection"""
    keyboard = [
        [{"text": "Все программы", "callback_data": "level_all"}],
        [{"text": "Только бакалавриат", "callback_data": "level_bachelor"}],
        [{"text": "Только магистратура", "callback_data": "level_master"}]
    ]
    return keyboard


def create_partner_keyboard():
    """Create keyboard for partner status selection"""
    keyboard = [
        [{"text": "Все программы", "callback_data": "partner_all"}],
        [{"text": "Без партнерских программ", "callback_data": "partner_no_partners"}],
        [{"text": "Без программ Нетологии", "callback_data": "partner_no_netology"}],
        [{"text": "Без программ Карпов Курсы", "callback_data": "partner_no_carpovcourses"}],
        [{"text": "← Назад", "callback_data": "back_to_level"}]
    ]
    return keyboard

def create_campus_keyboard():
    """Create keyboard for campus selection"""
    keyboard = [
        [{"text": "Все", "callback_data": "campus_all"}],
        [{"text": "Только Москва", "callback_data": "campus_msk"}],
        [{"text": "Только НН", "callback_data": "campus_nn"}],
        [{"text": "Только Пермь", "callback_data": "campus_perm"}],
        [{"text": "Только СПб", "callback_data": "campus_spb"}],
        [{"text": "← Назад", "callback_data": "back_to_partner"}]
    ]
    return keyboard


def create_earlyinvitation_keyboard():
    """Create keyboard for early invitation status selection"""
    keyboard = [
        [{"text": "Все", "callback_data": "earlyinvitation_all"}],
        [{"text": "Участвуют в РП", "callback_data": "earlyinvitation_yes"}],
        [{"text": "Не участвуют в РП", "callback_data": "earlyinvitation_no"}],
        [{"text": "← Назад", "callback_data": "back_to_campus"}]
    ]
    return keyboard


def create_program_list_keyboard(programs, selected_programs):
    """Create keyboard for program selection with toggle buttons"""
    keyboard = []

    for i, program in programs.reset_index(drop=True).iterrows():
        program_id = str(program['tg_chat_id'])
        is_selected = program_id in selected_programs
        icon = "✅" if is_selected else "❌"
        text = f"{icon} {program['program']} ({program['level']})"
        callback_data = f"toggle_program_{i}"
        keyboard.append([{"text": text, "callback_data": callback_data}])

    # Add navigation buttons
    keyboard.append([
        {"text": "← Назад", "callback_data": "back_to_earlyinvitation"},
        {"text": "Продолжить ▶️", "callback_data": "confirm_programs"}
    ])

    return keyboard


def create_final_confirmation_keyboard():
    """Create keyboard for final confirmation"""
    keyboard = [
        [{"text": "✅ Подтвердить и начать рассылку", "callback_data": "start_broadcast"}],
        [{"text": "← Назад", "callback_data": "back_to_programs"}]
    ]
    return keyboard


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
            except Exception:
                f.write(f"Chats file {chats} cannot be opened\n\n")
        # else:
        #     f.write("No chats file provided for logging\n\n")
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


def send_message_with_keyboard(chat_id, text, keyboard, markdown="Markdown"):
    """Send message with inline keyboard"""
    url = API_URL + "sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": markdown,
        "reply_markup": json.dumps({"inline_keyboard": keyboard})
    }
    response = requests.post(url, data=data, timeout=DELAY)
    return response.json()


def forward_message(chat_id, from_chat_id, message_id):
    url = API_URL + "copyMessage"
    data = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
    header = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'}
    attempts = 0
    while attempts < 2:
        r = None
        try:
            r = requests.post(url, data=data, timeout=DELAY, headers=header)
            if r.status_code != requests.codes.ok:
                print(f"Failed to forward message to {chat_id} with code {r.status_code}")
                return False
            return True
        except requests.exceptions.Timeout as e:
            attempts += 1
            print(f"Timeout while forwarding message to {chat_id}: {e}")
            if attempts < 2:
                time.sleep(DELAY)
                continue
            return False
        except requests.exceptions.RequestException as e:
            status_code = r.status_code if r is not None else "no response"
            print(f"Failed to forward message to {chat_id} with error: {e} and code {status_code}")
            return False


def answer_callback_query(callback_query_id, text=None):
    """Answer callback query"""
    url = API_URL + "answerCallbackQuery"
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text
    requests.post(url, data=data, timeout=DELAY)


def edit_message_text(chat_id, message_id, text, keyboard=None, markdown="Markdown"):
    """Edit message text"""
    url = API_URL + "editMessageText"
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": markdown
    }
    if keyboard:
        data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    response = requests.post(url, data=data, timeout=DELAY)
    return response.json()

def escape_markdown(text):
    """Экранирует специальные символы Markdown."""
    escape_chars = ['_', '*', '[', ']', '`']  # Для старого Markdown достаточно этих
    for ch in escape_chars:
        text = text.replace(ch, '\\' + ch)
    return text

def handle_callback_query(callback_query):
    """Handle callback query from inline keyboards"""
    callback_id = callback_query["id"]
    chat_id = str(callback_query["message"]["chat"]["id"])
    message_id = callback_query["message"]["message_id"]
    user_id = callback_query["from"]["id"]
    data = callback_query["data"]

    # Check if user is admin
    if user_id not in ADMIN_IDS:
        answer_callback_query(callback_id, "У вас нет прав доступа")
        return

    if data.startswith("level_"):
        level = data.replace("level_", "")
        set_user_data(user_id, "level", level)

        set_user_state(user_id, STATE_PARTNER_SELECTION)
        keyboard = create_partner_keyboard()
        text = "Выберите программы по партнерскому статусу:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data.startswith("partner_"):
        partner_filter = data.replace("partner_", "")
        set_user_data(user_id, "partner_filter", partner_filter)

        set_user_state(user_id, STATE_CAMPUS_SELECTION)
        keyboard = create_campus_keyboard()
        text = "Выберите к какому офлайн-кампусу относится программа:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data.startswith("campus_"):
        campus_filter = data.replace("campus_", "")
        set_user_data(user_id, "campus_filter", campus_filter)
        set_user_state(user_id, STATE_EARLYINVITATION_SELECTION)

        keyboard = create_earlyinvitation_keyboard()
        text = "Выберите участвует ли программа в раннем приглашении:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data.startswith("earlyinvitation_"):
        earlyinvitation_filter = data.replace("earlyinvitation_", "")
        set_user_data(user_id, "earlyinvitation_filter", earlyinvitation_filter)

        set_user_state(user_id, STATE_PROGRAM_CONFIRMATION)

        # Filter programs based on selections
        level = get_user_data(user_id, "level")
        partner_filter = get_user_data(user_id, "partner_filter")
        campus_filter = get_user_data(user_id, "campus_filter")
        earlyinvitation_filter = get_user_data(user_id, "earlyinvitation_filter")

        programs = load_programs()
        filtered_programs = filter_programs(programs, level, partner_filter, campus_filter, earlyinvitation_filter)

        if filtered_programs is None or filtered_programs.empty:
            set_user_state(user_id, STATE_EARLYINVITATION_SELECTION)
            keyboard = create_earlyinvitation_keyboard()
            text = "По вашим параметрам нет программ для рассылки - настройте фильтры заново.\nВыберите статус участия программы в раннем приглашении или вернитесь назад и настройте предыдущие фильтры:"
            edit_message_text(chat_id, message_id, text, keyboard)
        else:
            # Initialize all programs as selected
            selected_programs = set([str(p['tg_chat_id']) for _, p in filtered_programs.iterrows()])
            set_user_data(user_id, "filtered_programs", serialize_filtered_programs(filtered_programs))
            set_user_data(user_id, "selected_programs", list(selected_programs))

            keyboard = create_program_list_keyboard(filtered_programs, selected_programs)
            text = "Финально выберите программы для рассылки (нажмите на программу, чтобы включить/выключить):"
            edit_message_text(chat_id, message_id, text, keyboard)

    elif data.startswith("toggle_program_"):
        program_index = int(data.replace("toggle_program_", ""))
        filtered_programs = get_filtered_programs(user_id)
        selected_programs = set(get_user_data(user_id, "selected_programs", []))

        if filtered_programs is None or filtered_programs.empty:
            clear_user_state(user_id)
            set_user_state(user_id, STATE_WAITING_FOR_TEXT)
            keyboard = [] # create_level_keyboard()
            text = (
                "Похоже, сохраненное состояние рассылки потерялось после перезапуска. "
                "Давайте начнем заново: введите сообщение."
            )
            edit_message_text(chat_id, message_id, text, keyboard)
            answer_callback_query(callback_id, "Состояние сброшено, начните заново")
            return

        if program_index < len(filtered_programs):
            program_id = str(filtered_programs.iloc[program_index]['tg_chat_id'])
            if program_id in selected_programs:
                selected_programs.remove(program_id)
            else:
                selected_programs.add(program_id)

            set_user_data(user_id, "selected_programs", list(selected_programs))

            keyboard = create_program_list_keyboard(filtered_programs, selected_programs)
            text = "Выберите программы для рассылки (нажмите на программу, чтобы включить/выключить):"
            edit_message_text(chat_id, message_id, text, keyboard)

    elif data == "confirm_programs":
        set_user_state(user_id, STATE_FINAL_CONFIRMATION)

        broadcast_text = get_user_data(user_id, "broadcast_text")
        selected_programs = get_user_data(user_id, "selected_programs", [])
        filtered_programs = get_filtered_programs(user_id)

        if filtered_programs is None or filtered_programs.empty:
            clear_user_state(user_id)
            set_user_state(user_id, STATE_WAITING_FOR_TEXT)
            keyboard = [] # create_level_keyboard()
            text = (
                "Похоже, сохраненное состояние рассылки потерялось после перезапуска. "
                "Давайте начнем заново: введите сообщение."
            )
            edit_message_text(chat_id, message_id, text, keyboard)
            answer_callback_query(callback_id, "Состояние сброшено, начните заново")
            return

        # Create final confirmation text
        selected_program_names = []
        for _, program in filtered_programs.iterrows():
            if str(program['tg_chat_id']) in selected_programs:
                selected_program_names.append(f"• {program['program']} ({program['level']})")

        confirmation_text = f"*Подтверждение рассылки*\n\n*Текст:*\n{escape_markdown(broadcast_text)}\n\n*Программы (всего: {len(selected_program_names)}):*\n" + "\n".join(selected_program_names)

        keyboard = create_final_confirmation_keyboard()
        edit_message_text(chat_id, message_id, confirmation_text, keyboard)

    elif data == "start_broadcast":
        # Start the broadcast
        broadcast_text = get_user_data(user_id, "broadcast_text")
        selected_programs = get_user_data(user_id, "selected_programs", [])
        filtered_programs = get_filtered_programs(user_id)

        if filtered_programs is None or filtered_programs.empty:
            clear_user_state(user_id)
            set_user_state(user_id, STATE_WAITING_FOR_TEXT)
            keyboard = [] # create_level_keyboard()
            text = (
                "Похоже, сохраненное состояние рассылки потерялось после перезапуска. "
                "Давайте начнем заново: введите сообщение."
            )
            edit_message_text(chat_id, message_id, text, keyboard)
            answer_callback_query(callback_id, "Состояние сброшено, начните заново")
            return
        else:
            keyboard = []
            text = ("Начинаю рассылку... Это может занять некоторое время, пожалуйста, не пишите ничего боту.")
            edit_message_text(chat_id, message_id, text, keyboard)


        # Get original message for forwarding
        original_message_id = get_user_data(user_id, "original_message_id")

        # Clear user state
        clear_user_state(user_id)


        # Create list of selected chat IDs
        success_count = 0
        count = 0
        text = "Рассылка запущена. Подождите и не отправляйте сообщения боту.\nОтправка сообщений в группы:\n"
        keyboard = []
        for _, program in filtered_programs.iterrows():
            if str(program['tg_chat_id']) in selected_programs:
                count += 1
                chat_id_to_send = int(program['tg_chat_id'])
                if forward_message(chat_id_to_send, chat_id, original_message_id):
                    text += f"✅ {program['program']} ({program['level']})\n"
                    print(f"Message forwarded to {chat_id_to_send}")
                    success_count += 1
                else:
                    text += f"❌ {program['program']} ({program['level']})\n"
                    print(f"Failed to forward message to {chat_id_to_send}")
                edit_message_text(chat_id, message_id, text, keyboard)
                time.sleep(DELAY)

        # Send summary to all admins
        user = callback_query["from"]
        first_name = user.get("first_name", "")
        last_name = user.get("last_name", "")
        username = user.get("username", "")

        sender_name = f"{first_name} {last_name}".strip()
        if username:
            sender_name += f" (@{username})"

        summary_text = f"*Отправитель:* {sender_name}\n*Отправлено:* {success_count} из {count}\n\n*Текст:*\n{broadcast_text}"

        for admin_id in ADMIN_IDS:
            send_message(admin_id, summary_text)

        # Log broadcast
        log_broadcast(sender_name, broadcast_text + '\n\n' + text)

        edit_message_text(chat_id, message_id, f"*Рассылка завершена!* Отправлено в в чаты:\n{text}")

    # Handle back navigation
    elif data == "back_to_level":
        set_user_state(user_id, STATE_LEVEL_SELECTION)
        keyboard = create_level_keyboard()
        text = "Выберите уровень программ:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data == "back_to_partner":
        set_user_state(user_id, STATE_PARTNER_SELECTION)
        keyboard = create_partner_keyboard()
        text = "Выберите партнерские программы:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data == "back_to_campus":
        set_user_state(user_id, STATE_CAMPUS_SELECTION)
        keyboard = create_campus_keyboard()
        text = "Выберите офлайн-кампус программы:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data == "back_to_earlyinvitation":
        set_user_state(user_id, STATE_EARLYINVITATION_SELECTION)
        keyboard = create_earlyinvitation_keyboard()
        text = "Выберите участвует ли программа в раннем приглашении:"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data == "back_to_programs":
        set_user_state(user_id, STATE_PROGRAM_CONFIRMATION)
        filtered_programs = get_filtered_programs(user_id)
        selected_programs = set(get_user_data(user_id, "selected_programs", []))

        if filtered_programs is None or filtered_programs.empty:
            clear_user_state(user_id)
            set_user_state(user_id, STATE_WAITING_FOR_TEXT)
            keyboard = [] # create_level_keyboard()
            text = (
                "Похоже, сохраненное состояние рассылки потерялось после перезапуска. "
                "Давайте начнем заново: введите сообщение."
            )
            edit_message_text(chat_id, message_id, text, keyboard)
            answer_callback_query(callback_id, "Состояние сброшено, начните заново")
            return

        keyboard = create_program_list_keyboard(filtered_programs, selected_programs)
        text = "Выберите программы для рассылки (нажмите на программу, чтобы включить/выключить):"
        edit_message_text(chat_id, message_id, text, keyboard)

    elif data == "start_broadcast_flow":
        set_user_state(user_id, STATE_WAITING_FOR_TEXT)
        edit_message_text(chat_id, message_id, "Отлично! Теперь отправьте мне сообщение, которое нужно разослать:")

    answer_callback_query(callback_id)


# === Основной цикл ===


def main():
    print("Бот запущен (polling)...")
    offset = None

    # Load user states at startup
    load_user_states()

    while True:
        try:
            updates = get_updates(offset)
            if updates is None:
                continue

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                # Handle callback queries (inline keyboard buttons)
                if "callback_query" in update:
                    handle_callback_query(update["callback_query"])
                    continue

                message = update.get("message")
                if not message:
                    continue

                chat_id = str(message["chat"]["id"])
                user_id = message["from"]["id"]
                text = message.get("text", "")
                # entities = message.get('entities', [])

                # Команда администратора
                if text == "/start":
                    if user_id in ADMIN_IDS:
                        send_message_with_keyboard(
                            chat_id,
                            "Привет! Я бот для рассылки сообщений.\n\nНаправьте мне сообщение, которое нужно разослать, или нажмите кнопку ниже:",
                            [[{"text": "📲 Отправить рассылку", "callback_data": "start_broadcast_flow"}]]
                        )
                    else:
                        send_message(
                            chat_id,
                            "Добрый день! Я бот, отправляющий информацию. Админ бота @iasonov ",
                        )
                elif text == "/cancel":
                    if user_id in ADMIN_IDS:
                        clear_user_state(user_id)
                        send_message(
                            chat_id,
                            "Ок, процесс создания рассылки отменен. Напишите /start чтобы начать снова."
                        )
                elif chat_id == str(user_id):  # общение в привате с ботом
                    if user_id in ADMIN_IDS:
                        # Check current user state
                        user_state = get_user_state(user_id)

                        if user_state == STATE_WAITING_FOR_TEXT:
                            # User is in text input mode from button click
                            broadcast_text = text
                            set_user_data(user_id, "broadcast_text", broadcast_text)
                            set_user_data(user_id, "original_message_id", message["message_id"])

                            set_user_state(user_id, STATE_LEVEL_SELECTION)

                            keyboard = create_level_keyboard()
                            send_message_with_keyboard(
                                chat_id,
                                "Выберите уровень программ:",
                                keyboard
                            )

                        elif user_state is None:
                            # No active state, start new broadcast process
                            broadcast_text = text
                            set_user_data(user_id, "broadcast_text", broadcast_text)
                            set_user_data(user_id, "original_message_id", message["message_id"])
                            set_user_state(user_id, STATE_LEVEL_SELECTION)

                            keyboard = create_level_keyboard()
                            send_message_with_keyboard(
                                chat_id,
                                "Выберите уровень программ:",
                                keyboard
                            )
                        else:
                            # User is in the middle of a process, inform them
                            send_message(
                                chat_id,
                                "Вы уже начали процесс создания рассылки. Пожалуйста, завершите его или отмените с помощью /cancel"
                            )
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
