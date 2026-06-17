# 🤖 HLTV Match Predictor Bot

Telegram-бот для анализа статистики CS2 матчей с HLTV.org.

## ⚡ Быстрый старт

### 1. Получи токен бота

1. Открой Telegram, найди [@BotFather](https://t.me/BotFather)
2. Отправь `/newbot`
3. Придумай имя и username (например `hltv_predictor_bot`)
4. Скопируй токен вида `123456:ABC-DEF...`

### 2. Установи зависимости

```bash
pip install -r requirements.txt
```

### 3. Запусти бота

```bash
# Linux / macOS
export BOT_TOKEN="твой_токен_здесь"
python bot.py

# Windows (PowerShell)
$env:BOT_TOKEN="твой_токен_здесь"
python bot.py

# Или прямо в команде
BOT_TOKEN="твой_токен" python bot.py
```

---

## 📋 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и инструкция |
| `/today` | Все матчи на сегодня |
| `/top` | Топ-10 команд по рейтингу |
| `/help` | Помощь |

После `/today` нажми на кнопку матча — получишь детальный анализ.

---

## 📊 Как работает анализ

Бот оценивает каждую команду по 4 критериям:

| Критерий | Вес |
|----------|-----|
| Рейтинг HLTV | 35% |
| Winrate (3 месяца) | 30% |
| Avg Rating 2.0 игроков | 25% |
| Форма (последние 5 матчей) | 10% |

---

## ⚠️ Важные моменты

- **HLTV блокирует** парсинг по IP. Если бот не работает — попробуй VPN или запусти на сервере в ЕС/США.
- Прогнозы носят **развлекательный характер**, не используй как ставочные советы.
- Статистика обновляется при каждом запросе (реальное время).

---

## 🛠 Запуск на сервере (24/7)

### С помощью tmux:
```bash
tmux new -s hltvbot
BOT_TOKEN="токен" python bot.py
# Ctrl+B, D — свернуть сессию
```

### С помощью systemd (Linux):
```ini
# /etc/systemd/system/hltvbot.service
[Unit]
Description=HLTV Predictor Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/path/to/hltv_bot
ExecStart=/usr/bin/python3 bot.py
Environment=BOT_TOKEN=твой_токен
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable hltvbot
sudo systemctl start hltvbot
```

---

## 📁 Структура файлов

```
hltv_bot/
├── bot.py           # Основной файл бота (точка входа)
├── hltv_parser.py   # Парсер HLTV.org
├── analyzer.py      # Алгоритм анализа и прогноза
├── requirements.txt # Зависимости
└── README.md        # Эта инструкция
```
