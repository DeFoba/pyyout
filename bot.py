import os
import glob
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
import yt_dlp

# Логирование для отслеживания ошибок и работы
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = "ВАШ_ТОКЕН_ОТ_BOTFATHER"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Ограничиваем количество одновременных загрузок (например, не больше 2 параллельно),
# чтобы не исчерпать оперативную память и трафик сервера.
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

# Создаем папку для временных файлов
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def download_video_sync(url: str, user_id: int) -> str:
    """
    Синхронная функция скачивания через yt-dlp.
    Выбирает наихудшее качество (для быстроты и минимального веса).
    """
    out_template = os.path.join(DOWNLOAD_DIR, f"{user_id}_%(id)s.%(ext)s")

    ydl_opts = {
        # 'worst' выбирает готовый комбинированный поток низкого качества (360p/240p/144p).
        # Если такого нет, берет худшее видео и худшее аудио и объединяет через ffmpeg.
        'format': 'worst[ext=mp4]/worstvideo[ext=mp4]+worstaudio/worst',
        'outtmpl': out_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        # Лимит размера на скачивание (50MB - лимит Telegram Bot API на отправку файлов)
        'max_filesize': 50 * 1024 * 1024,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Получаем точный путь к сохраненному файлу
        file_path = ydl.prepare_filename(info)
        return file_path


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Отправь мне ссылку на YouTube-видео, и я пришлю его в легком качестве.")


@dp.message(F.text.regexp(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+"))
async def handle_youtube_link(message: types.Message):
    url = message.text.strip()
    status_msg = await message.answer("⏳ Добавлено в очередь...")

    # Семафор защищает от перегрузки очереди
    async with DOWNLOAD_SEMAPHORE:
        await status_msg.edit_text("⏳ Скачиваю видео в низком качестве...")
        file_path = None

        try:
            # Выполняем блокирующее скачивание в отдельном потоке
            file_path = await asyncio.to_thread(download_video_sync, url, message.from_user.id)

            if not os.path.exists(file_path):
                # Иногда yt-dlp может перекодировать файл в другое расширение (например, mkv -> mp4)
                base = os.path.splitext(file_path)[0]
                matches = glob.glob(f"{base}.*")
                if matches:
                    file_path = matches[0]

            await status_msg.edit_text("📤 Отправляю видео...")

            # Проверка лимита Telegram (50 MB для ботов)
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > 49.5:
                await message.answer("⚠️ Видео превышает лимит отправки Telegram (50 МБ).")
                return

            video_file = FSInputFile(file_path)
            await message.answer_video(video=video_file)
            await status_msg.delete()

        except yt_dlp.utils.MaxDownloadsReached:
            await message.answer("❌ Превышен лимит.")
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Download error: {e}")
            await message.answer("❌ Не удалось скачать видео. Возможно, оно приватное, с возрастным ограничением или больше 50 МБ.")
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            await message.answer("❌ Произошла непредвиденная ошибка при обработке.")

        finally:
            # Гарантированное удаление файла даже при сбое
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Файл {file_path} успешно удален.")
                except OSError as e:
                    logger.error(f"Ошибка удаления файла {file_path}: {e}")


async def main():
    logger.info("Запуск бота...")
    # Удаляем вебхуки и пропускаем накопившиеся апдейты при старте
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
