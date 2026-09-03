# 🤖 GGSel Telegram Bot

Автоматический бот для продавцов GGSel — мониторинг покупок, двусторонняя связь с покупателями через Telegram.

## ✨ Возможности

| Функция | Описание |
|---------|----------|
| 📦 **Мониторинг покупок** | Автоматическое создание топиков для новых заказов |
| 💬 **Двусторонняя связь** | Сообщения из GGSel ↔ Telegram в реальном времени |
| 🤖 **Автоответы** | Приветствия, триггеры на ключевые слова |
| ⭐ **Ответы на отзывы** | Автоматические ответы на хорошие/плохие отзывы |
| 🎯 **Режим ЧСВ** | Реакция на опции покупки с условиями |

---

## 🚀 Установка

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/voterol/ggsel_seller_helper/
cd ggsel_seller_helper/
```

### 2. Создайте виртуальное окружение (рекомендуется)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt
```

### 4. Создайте файл `.env`

```bash
cp .env.example .env
```

Отредактируйте `.env` и заполните данные:

```env
# GGSel API (из личного кабинета продавца)
GGSEL_SELLER_ID=1234567
GGSEL_API_KEY=your_api_key_here

# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_GROUP_ID=-1001234567890
```

### 5. Настройте Telegram

1. Создайте бота через [@BotFather](https://t.me/BotFather) → получите токен
2. Создайте группу и включите "Темы" (Topics) в настройках группы
3. Добавьте бота в группу
4. Сделайте бота администратором с правами:
   - ✅ Управление темами
   - ✅ Отправка сообщений
   - ✅ Удаление сообщений

5. Получите ID группы:
   - Добавьте [@userinfobot](https://t.me/userinfobot) в группу
   - Или перешлите сообщение из группы боту [@userinfobot](https://t.me/userinfobot)
   - ID будет вида `-100xxxxxxxxxx`

### 6. Запустите бота

```bash
python main.py
```

### Запуск через Docker Compose

```bash
cp .env.example .env
cp compose.example.yml compose.yml
# заполните секреты в .env
docker compose up -d
docker compose logs -f ggsel-bot
```

Файл `.env` намеренно не копируется в Docker-образ. Compose передаёт его
содержимое контейнеру через `env_file`. Путь в `env_file` вычисляется относительно
расположения Compose-файла; если Compose-файл хранится в другой папке, укажите
абсолютный путь к `.env` на сервере.

Не подключайте отдельный файл `ggsel_bot.log` как bind mount: его невозможно
безопасно переименовать при ротации. В Docker бот по умолчанию пишет в stdout;
смотрите логи через `docker compose logs`. Для файловых логов включите
`LOG_TO_FILE=true` и подключите всю папку `/app/data`, а не один log-файл.

---

## ⚙️ Команды бота

| Команда | Где использовать | Описание |
|---------|------------------|----------|
| `/auto` | В группе | Меню настроек автоответов и режима ЧСВ |
| `/start_sync` | В группе | Запустить синхронизацию GGSel и сообщения покупателям |
| `/stop_sync` | В группе | Остановить GGSel, оставив Telegram онлайн |
| `/history` | В топике | Загрузить историю сообщений |
| `/options` | В топике | Показать опции покупки |

Состояние синхронизации сохраняется в SQLite и переживает перезапуск контейнера.
При остановленной синхронизации Telegram и очередь уже сохранённых уведомлений
продолжают работать, но новые проверки GGSel и сообщения покупателям блокируются.
Команды доступны участникам настроенной Telegram-группы; используйте их только
в доверенной закрытой группе.

Все запросы к GGSel используют отдельные таймауты подключения и чтения. Их можно
настроить через `GGSEL_CONNECT_TIMEOUT` и `GGSEL_READ_TIMEOUT` (по умолчанию 5 и
30 секунд). Токены передаются как параметры запросов и не встраиваются в URL.
Параметр `CHAT_SWEEP_BATCH_SIZE` (по умолчанию 25) задаёт количество известных
чатов покупок, которые дополнительно проверяются за один цикл. Это страхует от
пропуска сообщения, если кабинет продавца уже снял с чата отметку «непрочитано».

### Public V2 offer diagnostic (read-only)

Use this before implementing live repricing to confirm that your public V2 key can
read the seller offer ID and current RUB price. Create a V2 key with the minimum
read permission available, then set it only in the uncommitted `.env` file:

```env
GGSEL_V2_API_KEY=your_local_v2_key
```

Use the GGSEL product ID shown in the seller dashboard. In the verified example,
the private seller-UI row ID was `2615258`, while the dashboard/catalog ID
`102615259` was the ID accepted by public V2:

```powershell
python ggsel_api.py 102615259
```

Expected output contains only non-sensitive fields:

```text
offer_id=102615259 status=active price=166.0 currency=RUB
```

The diagnostic command sends only `GET /api_sellers/v2/offers/:id`. It rejects an
absent key, invalid/mismatched offer ID, non-positive/non-finite price, and an
explicit non-RUB currency. It never prints the key or invokes the separately gated
V2 price-update method. The seller website request observed at
`/api/v1/offers/:id/update_price` is an internal session-authenticated UI route,
not this public Seller API.

### Price control: dry-run and guarded daily updates

Price control has three mutually exclusive states:

- **Disabled** — no repricing calculation or write.
- **Dry run** — calculates and logs proposed RUB prices without changing GGSEL.
- **Live** — attempts each explicitly selected offer at most once per 24 hours.

The exchange-rate loop runs at startup and every 12 hours using the official CBR
USD/RUB rate. A persistent UTC checkpoint limits live mode to one batch per 24
hours, including across restarts.

#### 1. Install and prepare `.env`

Valid GGSEL and Telegram credentials are required. On Windows PowerShell:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On Linux/macOS:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `GGSEL_V2_API_KEY` with offer-read permission for the product picker. Live mode
also requires permission to patch offers and the independent server kill switch:

```env
GGSEL_V2_API_KEY=your_v2_key
GGSEL_V2_PRICE_WRITE_ENABLED=true
REPRICING_DRY_RUN=false
REPRICING_LIVE_ENABLED=false
REPRICING_TARGETS_USD={}
REPRICING_FEE_PERCENT=2.5
REPRICING_FIXED_RUB=0
REPRICING_MAX_CHANGE_PERCENT=15
```

`REPRICING_LIVE_ENABLED` must remain `false`; live approval is accepted only from
the separate Telegram confirmation and is persisted in SQLite. Setting the server
write gate to `true` therefore cannot enable live mode by itself.

| Variable | Meaning |
|---|---|
| `GGSEL_V2_PRICE_WRITE_ENABLED` | Server-side kill switch for every real price PATCH. Default `false`. A nonempty V2 key is required when true. |
| `REPRICING_DRY_RUN` | Enables simulation when targets exist. Default `false`. |
| `REPRICING_LIVE_ENABLED` | Deployment guard; must remain `false`. Telegram owns persisted live approval. |
| `REPRICING_TARGETS_USD` | Public V2 offer IDs mapped to positive target USD amounts. These IDs are the write allowlist. |
| `REPRICING_FEE_PERCENT` | Percentage cost to cover. Allowed range: 0–50. Verify actual fees first. |
| `REPRICING_FIXED_RUB` | Fixed RUB cost per target. Allowed range: 0–1,000,000. |
| `REPRICING_MAX_CHANGE_PERCENT` | Maximum permitted change from the current GGSEL price. Allowed range: 0.01–100. |

#### 2. Select offers and review dry-run proposals

1. Restart the bot and send `/menu` in the configured Telegram group.
2. Select **💱 Price control** → **📦 Browse GGSEL products**.
3. Click an offer and send its target USD amount, for example `10.50`.
4. Repeat for every offer, then select **▶️ Enable dry run**.
5. Review several `REPRICING DRY RUN` cycles and verify fees and target amounts.

Use **⌨️ Add / update by ID** only as a fallback; send `PRODUCT_ID TARGET_USD`, for
example `123456 10.50`. Adding an existing offer ID updates its target.

Targets and mode are stored in the existing SQLite database and survive restarts.
Any add, update, or removal immediately disables live mode and requires a new live
confirmation. Dry run and live mode cannot run together.

The price is rounded upward to a whole RUB:

```text
ceil((target_usd × CBR_USD_RUB + fixed_rub) / (1 - fee_percent / 100))
```

Example: target `$10`, CBR rate `90.01`, and fee `2.5%` proposes `924 RUB`.

#### 3. Enable guarded live updates

After reviewing dry-run output, open **💱 Price control**, select **⚠️ Enable LIVE
updates**, read the warning, and select **⚠️ Confirm live updates**. Confirmation
succeeds only when the server gate, V2 key, and at least one target are present.

Before each PATCH the client:

1. Validates the CBR rate (20–300) and timezone-aware timestamp (not materially in
   the future and no older than 48 hours).
2. Requires the exact offer ID in the target allowlist.
3. Reads the current RUB price and blocks changes above
   `REPRICING_MAX_CHANGE_PERCENT`.
4. Sends one `PATCH /api_sellers/v2/offers/:id` with no automatic write retry.
5. Reads the offer again and accepts success only when the price exactly matches.

The daily checkpoint is claimed before the first write. This prevents duplicate
financial writes after a crash; if a batch stops partway through, remaining offers
wait for the next 24-hour window. One failed or blocked offer does not stop later
offers in the same batch.

Search logs for `LIVE REPRICING`. `status=VERIFIED` means GET read-back matched;
`status=BLOCKED_OR_FAILED` means no verified price change was accepted.

If synchronization was paused with `/stop_sync`, send `/start_sync`; paused
synchronization also pauses repricing.

#### 4. Docker with these local changes

`compose.example.yml` normally pulls `paparei/ggsel-bot:latest`, which may not
contain local changes. To run this checkout, copy it to `compose.yml`, remove
`pull_policy: always`, and replace the image with:

```yaml
build: .
image: ggsel-bot-local
```

Then build and start it:

```bash
docker compose up -d --build
docker compose logs -f ggsel-bot
```

#### 5. Disable safely

Use **💱 Price control** → **⏸ Disable LIVE updates** for the normal kill switch.
For a server-side stop, set `GGSEL_V2_PRICE_WRITE_ENABLED=false` and restart the
bot. This blocks every real price PATCH even if SQLite contains prior live approval.
Set `REPRICING_DRY_RUN=false` too when no proposals are wanted.

Disabling does not restore prices already verified by GGSEL; rollback is a separate,
explicit price decision. The canonical API handbook is
[`GGSEL_API_REFERENCE.md`](GGSEL_API_REFERENCE.md).

---

## 🎯 Режим ЧСВ

Автоматическая реакция на опции в заказе.

### Типы сопоставления

| Тип | Описание | Пример |
|-----|----------|--------|
| 📝 `name` | Только по названию | Опция "Чай" — любое значение |
| 🎯 `value` | По названию И значению | Опция "Чай" = "20р" |
| 🔍 `contains` | Значение содержит | Опция "Чай" содержит "20" |

### Переменные в сообщениях

- `{option}` — название опции
- `{value}` — значение опции
- `{sum}` — алиас для {value}

**Пример:** Если покупатель выбрал "Чай: 20р", можно отправить ему: `Спасибо за чай на {sum}! ☕`

---

## 📊 Как это работает

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   GGSel     │────▶│    Bot      │────▶│  Telegram   │
│    API      │◀────│             │◀────│   Группа    │
└─────────────┘     └─────────────┘     └─────────────┘
```

1. Бот проверяет новые покупки через GGSel API
2. Для каждой покупки создаётся топик в Telegram
3. Сообщения от покупателя пересылаются в топик
4. Ваши ответы в топике отправляются покупателю

---

## 🔧 Запуск как сервис (Linux)

Создайте файл `/etc/systemd/system/ggsel-bot.service`:

```ini
[Unit]
Description=GGSel Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/ggsel_bot
ExecStart=/path/to/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ggsel-bot
sudo systemctl start ggsel-bot
```

---

## 📁 Структура

```
ggsel_bot/
├── main.py              # Точка входа
├── bot_service.py       # Основная логика
├── telegram_bot.py      # Telegram API
├── ggsel_api.py         # GGSel API
├── autoresponder.py     # Автоответы
├── .env                 # Секреты (не коммитить!)
└── requirements.txt     # Зависимости
```

---

## ❓ FAQ

**Бот не создаёт топики**
- Проверьте что группа — форум (включены темы)
- Проверьте права бота (администратор + управление темами)

**Сообщения не отправляются**
- Проверьте `GGSEL_API_KEY` и `GGSEL_SELLER_ID`
- При обычном запуске посмотрите `ggsel_bot.log`; в Docker используйте `docker compose logs`

**Как получить GGSel API ключ?**
- Личный кабинет продавца → Настройки → API

---

## 📝 Лицензия

MIT

