import time
import requests
import os
from my_secrets import BOT_TOKEN

# === Конфигурация ===
API_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/'
ADMIN_IDS = [211570366, # Игорь Асонов
             517348694, # Мария Рыбалко
             189526817, # Алена Абрамова
             1172339189, # Елизавета Павлова
             403700929 ]  # Екатерина Каляева
            # Вставь свои Telegram user_id
CHAT_IDS_FILE = 'chat_ids.txt'

# === Вспомогательные функции ===

def get_updates(offset=None):
    url = API_URL + 'getUpdates'
    # params = {'timeout': 1, 'offset': offset}

    payload = {
        "offset": offset,
        "limit": None,
        "timeout": None # 10
    }
    headers = {
        "accept": "application/json",
        "User-Agent": "Python",
        "content-type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)
    return response.json()

def send_message(chat_id, text, markdown='Markdown'):
    url = API_URL + 'sendMessage'
    data = {'chat_id': chat_id, 
            'text': text,
            'parse_mode': markdown
            }
    requests.post(url, data=data)

def forward_message(chat_id, from_chat_id, message_id):
    url = API_URL + 'copyMessage'
    data = {'chat_id': chat_id, 
            'from_chat_id': from_chat_id,
            'message_id': message_id
            }
    requests.post(url, data=data)
    

def load_chat_ids():
    if not os.path.exists(CHAT_IDS_FILE):
        return set()
    with open(CHAT_IDS_FILE, 'r') as f:
        return set(line.strip() for line in f if line.strip())

def save_chat_id(chat_id):
    chat_ids = load_chat_ids()
    if chat_id not in chat_ids:
        with open(CHAT_IDS_FILE, 'a') as f:
            f.write(f"{chat_id}\n")

# def apply_markdown_entities(text, entities):
#     chars = list(text)
#     inserts = []

#     for entity in entities:
#         offset = entity['offset']
#         length = entity['length']
#         end = offset + length

#         if entity['type'] == 'bold':
#             inserts.append((offset, '*'))
#             inserts.append((end, '*'))
#         elif entity['type'] == 'italic':
#             inserts.append((offset, '_'))
#             inserts.append((end, '_'))
#         elif entity['type'] == 'code':
#             inserts.append((offset, '`'))
#             inserts.append((end, '`'))
#         elif entity['type'] == 'text_link':
#             url = entity.get('url', '')
#             inserts.append((offset, '['))
#             inserts.append((end, f']({url})'))
#         # можно добавить другие типы при необходимости

#     # Сортируем вставки в порядке убывания, чтобы не сбить позиции
#     for pos, val in sorted(inserts, reverse=True):
#         chars[pos:pos] = list(val)

#     return ''.join(chars)

def apply_markdown_entities(text, entities):
    result = text
    shift = 0
    # TODO there are problems in case of same offset (example: bold inside link)
    for e in sorted(entities, key=lambda x: x['offset'] + x['length']):
        start = e['offset']
        end = start + e['length']
        t = text[start:end]

        if e['type'] == 'bold':
            wrap = f"*{t}*"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        elif e['type'] == 'italic':
            wrap = f"_{t}_"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        elif e['type'] == 'code':
            wrap = f"`{t}`"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        elif e['type'] == 'text_link':
            wrap = f"[{t}]({e['url']})"
            delta_shift = len(wrap) - len(t)
            shift += delta_shift
        else:
            continue

        result = result[:start + shift - delta_shift] + wrap + result[end + shift - delta_shift:]
       # 

    return result

# def apply_markdown_entities(text, entities):
#     """
#     Преобразует text + entities в markdown-строку с вложенной разметкой.
#     Поддерживает: bold, italic, code, text_link.
#     """
#     # Словарь всех позиций, куда нужно вставить маркеры
#     insertions = {}

#     # Приоритеты вложенности (меньше число = глубже вложенность)
#     priority = {
#         'text_link': 4,
#         'bold': 1,
#         'italic': 2,
#         'code': 3,
#     }

#     def get_tags(entity):
#         if entity['type'] == 'bold':
#             return '*', '*'
#         elif entity['type'] == 'italic':
#             return '_', '_'
#         elif entity['type'] == 'code':
#             return '`', '`'
#         elif entity['type'] == 'text_link':
#             url = entity['url']
#             return f'[', f']({url})'
#         return '', ''

#     # Собираем все маркеры в позиции начала и конца
#     for entity in entities:
#         start = entity['offset']
#         end = start + entity['length']
#         prio = priority.get(entity['type'], 99)
#         open_tag, close_tag = get_tags(entity)

#         insertions.setdefault(start, []).append((prio, open_tag))
#         insertions.setdefault(end, []).append((prio, close_tag, True))

#     # Сортируем маркеры: сначала открывающиеся, потом закрывающиеся, по вложенности
#     result = []
#     for i in range(len(text) + 1):
#         if i in insertions:
#             open_tags = [tag for tag in insertions[i] if len(tag) == 2]
#             close_tags = [tag for tag in insertions[i] if len(tag) == 3]

#             # Открывающиеся — по возрастанию приоритета (глубже — раньше)
#             for _, tag in sorted(open_tags):
#                 result.append(tag)

#             # Закрывающиеся — по убыванию приоритета (вложенные — позже)
#             for _, tag, _ in sorted(close_tags, reverse=True):
#                 result.append(tag)

#         if i < len(text):
#             result.append(text[i])

#     return ''.join(result)

# def apply_markdown_entities(text, entities):
#     spans = []
#     end = 0
#     for entity in sorted(entities, key=lambda e: e['offset']):
#         start = entity['offset']
#         spans.append(text[end:start])
        
#         segment = text[start:start + entity['length']]

#         if entity['type'] == 'bold':
#             formatted = f"*{segment}*"
#         elif entity['type'] == 'italic':
#             formatted = f"_{segment}_"
#         elif entity['type'] == 'code':
#             formatted = f"`{segment}`"
#         elif entity['type'] == 'pre':
#             formatted = f"```{segment}```"
#         elif entity['type'] == 'text_link':
#             url = entity.get('url', '')
#             formatted = f"[{segment}]({url})"
#         else:
#             formatted = segment  # без форматирования

#         spans.append(formatted)
#         end = start + entity['length']

#     spans.append(text[end:])

#     return ''.join(spans)

# === Основной цикл ===

def main():
    print("Бот запущен (polling)...")
    offset = None

    while True:
        try:
            updates = get_updates(offset)

            for update in updates.get('result', []):
                offset = update['update_id'] + 1

                message = update.get('message')
                if not message:
                    continue

                chat_id = str(message['chat']['id'])
                user_id = message['from']['id']
                text = message.get('text', '')
                # entities = message.get('entities', [])

                # Сохраняем групповые чаты
                if message['chat']['type'] in ['group', 'supergroup']:
                    save_chat_id(chat_id)

                # Команда администратора
                if text == '/start':
                    send_message(chat_id, "Привет! Я бот-рассыльщик.")
                elif user_id in ADMIN_IDS: # and text.startswith('/broadcast'):
                    #msg_text = text[len('/broadcast') +  1:]
                    # markdown = apply_markdown_entities(text, entities)
                    # for cid in load_chat_ids():
                    #     send_message(cid, markdown[len('/broadcast') +  1:])
                    # send_message(chat_id, "Рассылка отправлена.")
                    for cid in load_chat_ids():
                        forward_message(cid, chat_id, message['message_id'])
                    send_message(chat_id, "Рассылка отправлена.")

        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
