import asyncio
import json
import random
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Conflict
import logging
import pytz
import aiohttp

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = "8371557108:AAHf-pvi5Lw-PDuOlWY248ufuQ58NWQbF5w"
API_URL = "https://platform.yclients.com/api/v1/b2c/booking/availability/search-timeslots"
AUTHORIZATION = "Bearer gtcwf654agufy25gsadh"
LOCATION_ID = 967881
RANDOM_DELAY_MIN = 30  # секунд
RANDOM_DELAY_MAX = 120  # секунд
DAYS_TO_CHECK = 30  # проверяем 30 дней вперед

# Путь к файлу состояния
_data_dir = Path(__file__).parent / "data"
_state_in_data = _data_dir / "last_state.json"
_state_in_root = Path(__file__).with_name("last_state.json")

# По умолчанию сохраняем состояние в data/, если она смонтирована (Docker)
if _data_dir.exists():
    STATE_FILE = _state_in_data
else:
    STATE_FILE = _state_in_root

# Глобальные переменные
last_available_date: Optional[str] = None
last_slot: Optional[str] = None
parsing_task: Optional[asyncio.Task] = None
user_chat_id: Optional[int] = None
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
shutdown_event: Optional[asyncio.Event] = None


def load_state():
    """Загружает сохраненное состояние (учитывая возможные пути)"""
    global last_available_date, last_slot, STATE_FILE
    candidates = [STATE_FILE]
    # Добавляем альтернативный путь, если он отличается
    if STATE_FILE != _state_in_data:
        candidates.append(_state_in_data)
    if STATE_FILE != _state_in_root:
        candidates.append(_state_in_root)

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                last_available_date = data.get("date")
                last_slot = data.get("slot")
                STATE_FILE = path
                # Если загружали из корня, но папка data/ доступна, переносим файл туда
                if _data_dir.exists() and path != _state_in_data:
                    try:
                        _data_dir.mkdir(parents=True, exist_ok=True)
                        _state_in_data.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                        STATE_FILE = _state_in_data
                        logger.info(f"Файл состояния перенесен в {_state_in_data}")
                    except Exception as copy_err:
                        logger.warning(f"Не удалось перенести состояние в {_state_in_data}: {copy_err}")
                if last_available_date:
                    logger.info(f"Загружено сохранённое состояние из {path}: {last_available_date} ({last_slot})")
                return
            except Exception as e:
                logger.warning(f"Не удалось загрузить состояние из {path}: {e}")


def save_state(date: Optional[str], slot: Optional[str]):
    """Сохраняет состояние"""
    if not date:
        return
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"date": date, "slot": slot}, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"Состояние сохранено ({STATE_FILE}): {date} ({slot})")
    except Exception as e:
        logger.warning(f"Не удалось сохранить состояние: {e}")


async def check_date(session: aiohttp.ClientSession, date_str: str) -> Optional[List[Dict]]:
    """Проверяет доступность слотов на указанную дату"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU",
        "authorization": AUTHORIZATION,
        "content-type": "application/json",
        "origin": "https://b1044864.yclients.com",
        "referer": "https://b1044864.yclients.com/",
        "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36",
        "x-yclients-application-name": "client.booking",
        "x-yclients-application-platform": "angular-18.2.13",
        "x-yclients-application-version": "302293.e671abf7"
    }
    
    payload = {
        "context": {
            "location_id": LOCATION_ID
        },
        "filter": {
            "date": date_str,
            "records": [
                {
                    "staff_id": -1,
                    "attendance_service_items": []
                }
            ]
        }
    }
    
    try:
        async with session.post(API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                data = await response.json()
                if "data" in data and data["data"]:
                    slots = []
                    for item in data["data"]:
                        attrs = item.get("attributes", {})
                        if attrs.get("is_bookable", False):
                            slots.append({
                                "time": attrs.get("time", ""),
                                "datetime": attrs.get("datetime", "")
                            })
                    return slots if slots else None
            else:
                logger.warning(f"Ошибка API для {date_str}: статус {response.status}")
                return None
    except Exception as e:
        logger.error(f"Ошибка при проверке {date_str}: {e}")
        return None


async def check_available_dates() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Проверяет доступные даты на следующие 30 дней
    Возвращает: (summary, latest_date, latest_slot)
    """
    today = datetime.now(MOSCOW_TZ).date()
    available_dates = []
    latest_date = None
    latest_slot = None
    
    async with aiohttp.ClientSession() as session:
        for day_offset in range(DAYS_TO_CHECK):
            check_date_obj = today + timedelta(days=day_offset)
            date_str = check_date_obj.strftime("%Y-%m-%d")
            
            slots = await check_date(session, date_str)
            if slots:
                first_slot = slots[0]["time"]
                # Форматирование даты на русском
                months = {
                    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
                    5: "мая", 6: "июня", 7: "июля", 8: "августа",
                    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
                }
                date_label = f"{check_date_obj.day} {months[check_date_obj.month]}"
                available_dates.append((date_str, date_label, first_slot))
                latest_date = date_label
                latest_slot = first_slot
                
                # Небольшая задержка между запросами
                await asyncio.sleep(0.5)
    
    if available_dates:
        summary_lines = [f"{label}: {slot}" for _, label, slot in available_dates]
        summary = "\n".join(summary_lines)
        return summary, latest_date, latest_slot
    
    return None, None, None


def is_parsing_time() -> bool:
    """Проверяет, можно ли парсить в текущее время (10:00-22:00 по Москве, включительно)"""
    moscow_time = datetime.now(MOSCOW_TZ)
    current_hour = moscow_time.hour
    # Работаем с 10:00 до 22:59 (включая весь час 22:00)
    return 10 <= current_hour <= 22


async def check_and_notify(bot):
    """Проверяет доступные даты и отправляет уведомление при изменении"""
    global last_available_date, last_slot
    
    logger.info("Начинаю проверку доступных дат...")
    summary, latest_date, latest_slot = await check_available_dates()
    
    if latest_date:
        if last_available_date != latest_date:
            if last_available_date is not None:
                # Новая дата обнаружена
                slot_part = f" (слот: {latest_slot})" if latest_slot else ""
                message = f"🎾 Новая доступная дата найдена:\n{latest_date}{slot_part}"
                if user_chat_id:
                    try:
                        await bot.send_message(chat_id=user_chat_id, text=message)
                        logger.info(f"Отправлено уведомление: {message}")
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления: {e}")
            
            last_available_date = latest_date
            last_slot = latest_slot
            save_state(last_available_date, last_slot)
            logger.info(f"Последняя доступная дата: {latest_date}")
        else:
            logger.info(f"Доступная дата не изменилась: {latest_date}")
    else:
        logger.info("Доступных дат не найдено")


async def periodic_check(bot):
    """Периодическая проверка с рандомными интервалами"""
    global shutdown_event
    try:
        while shutdown_event and not shutdown_event.is_set():
            try:
                if not is_parsing_time():
                    moscow_time = datetime.now(MOSCOW_TZ)
                    logger.info(f"Парсинг пропущен: текущее время {moscow_time.strftime('%H:%M')} (нерабочее время 22:00-10:00)")
                    # Проверяем shutdown_event каждые 5 минут
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=300)
                        break
                    except asyncio.TimeoutError:
                        continue
                
                await check_and_notify(bot)
                
                delay = random.randint(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
                logger.info(f"Следующая проверка через {delay} секунд")
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    continue
                
            except asyncio.CancelledError:
                logger.info("Периодическая проверка отменена")
                break
            except Exception as e:
                logger.error(f"Ошибка в периодической проверке: {e}")
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=60)
                    break
                except asyncio.TimeoutError:
                    continue
    except asyncio.CancelledError:
        logger.info("Периодическая проверка отменена")
    finally:
        logger.info("Периодическая проверка завершена")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    global user_chat_id
    
    user_chat_id = update.effective_chat.id
    
    keyboard = [
        [InlineKeyboardButton("🔍 Проверить доступные даты", callback_data="check_dates")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Привет! Я бот для отслеживания доступных дат на кортах.\n\n"
        "Я автоматически проверяю сайт каждые 30-120 секунд (с 10:00 до 22:00 по Москве) "
        "и уведомлю тебя о новых доступных датах.\n\n"
        "Также ты можешь вручную проверить доступные даты, нажав кнопку ниже.",
        reply_markup=reply_markup
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_dates":
        await query.edit_message_text("🔍 Проверяю доступные даты...")
        
        summary, latest_date, latest_slot = await check_available_dates()
        
        if summary:
            message = f"✅ Доступные даты:\n{summary}"
        else:
            message = "❌ Доступных дат не найдено"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Проверить снова", callback_data="check_dates")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup)


async def post_init(application: Application) -> None:
    """Запускается после инициализации приложения"""
    global parsing_task, shutdown_event
    # Создаем event в event loop
    shutdown_event = asyncio.Event()
    parsing_task = asyncio.create_task(periodic_check(application.bot))
    logger.info("Периодическая проверка запущена автоматически при старте бота")


async def post_shutdown(application: Application) -> None:
    """Запускается при остановке приложения"""
    global parsing_task, shutdown_event
    logger.info("Останавливаю периодическую проверку...")
    if shutdown_event:
        shutdown_event.set()
    if parsing_task and not parsing_task.done():
        parsing_task.cancel()
        try:
            await parsing_task
        except asyncio.CancelledError:
            pass
    logger.info("Периодическая проверка остановлена")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок"""
    error = context.error
    if isinstance(error, Conflict):
        logger.error("⚠️ КОНФЛИКТ: Другой экземпляр бота уже запущен! Остановите все другие экземпляры перед запуском.")
        logger.error("Причина: Telegram API не позволяет нескольким экземплярам одного бота получать обновления одновременно.")
        # Не останавливаем приложение автоматически - пусть пользователь сам остановит другой экземпляр
    else:
        logger.error(f"Необработанная ошибка: {error}", exc_info=error)


def main():
    """Главная функция"""
    load_state()
    
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)
    
    logger.info("Бот запущен")
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True  # Игнорируем старые обновления при перезапуске
        )
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=e)
    finally:
        logger.info("Бот остановлен")


if __name__ == "__main__":
    main()
