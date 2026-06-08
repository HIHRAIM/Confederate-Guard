import re
import db
from config import ADMINS

SUPPORTED_LANGS = {"ru", "uk", "pl", "en", "es", "pt"}
DEFAULT_LANG = "en"

_LOCALE = {
    "setup_success": {
        "ru": "Сервер зарегистрирован. Язык: {lang}, лог-канал: <#{channel_id}>.",
        "uk": "Сервер зареєстровано. Мова: {lang}, лог-канал: <#{channel_id}>.",
        "pl": "Serwer zarejestrowany. Język: {lang}, kanał logów: <#{channel_id}>.",
        "en": "Server registered. Language: {lang}, log channel: <#{channel_id}>.",
        "es": "Servidor registrado. Idioma: {lang}, canal de registro: <#{channel_id}>.",
        "pt": "Servidor registrado. Idioma: {lang}, canal de log: <#{channel_id}>.",
    },
    "setup_no_perm": {
        "ru": "У вас нет прав для использования этой команды.",
        "uk": "У вас немає прав для використання цієї команди.",
        "pl": "Nie masz uprawnień do użycia tej komendy.",
        "en": "You don't have permission to use this command.",
        "es": "No tienes permiso para usar este comando.",
        "pt": "Você não tem permissão para usar este comando.",
    },
    "setup_unknown_lang": {
        "ru": "Неизвестный язык: {lang}. Поддерживаются: {supported}.",
        "uk": "Невідома мова: {lang}. Підтримуються: {supported}.",
        "pl": "Nieznany język: {lang}. Obsługiwane: {supported}.",
        "en": "Unknown language: {lang}. Supported: {supported}.",
        "es": "Idioma desconocido: {lang}. Soportados: {supported}.",
        "pt": "Idioma desconhecido: {lang}. Suportados: {supported}.",
    },
    "setup_invalid_channel": {
        "ru": "Неверный ID канала.",
        "uk": "Невірний ID каналу.",
        "pl": "Nieprawidłowe ID kanału.",
        "en": "Invalid channel ID.",
        "es": "ID de canal inválido.",
        "pt": "ID de canal inválido.",
    },
    "guard_enabled": {
        "ru": "Охрана включена в этом канале. Длительность для блокировок: {duration}, причина: {reason}.",
        "uk": "Охорону увімкнено в цьому каналі. Тривалість: {duration}, причина: {reason}.",
        "pl": "Ochrona włączona na tym kanale. Czas trwania blokad: {duration}, powód: {reason}.",
        "en": "Guard enabled on this channel. Duration of bans: {duration}, reason: {reason}.",
        "es": "Guardia activada en este canal. Duración de los bloqueos: {duration}, motivo: {reason}.",
        "pt": "Guarda ativada neste canal. Duração dos bloqueios: {duration}, motivo: {reason}.",
    },
    "guard_already": {
        "ru": "Охрана уже включена в этом канале.",
        "uk": "Охорону вже увімкнено в цьому каналі.",
        "pl": "Ochrona jest już włączona na tym kanale.",
        "en": "Guard is already enabled on this channel.",
        "es": "La guardia ya está activa en este canal.",
        "pt": "A guarda já está ativa neste canal.",
    },
    "guard_no_setup": {
        "ru": "Сначала выполните /setup для регистрации сервера.",
        "uk": "Спочатку виконайте /setup для реєстрації сервера.",
        "pl": "Najpierw uruchom /setup, aby zarejestrować serwer.",
        "en": "Run /setup first to register this server.",
        "es": "Primero ejecuta /setup para registrar este servidor.",
        "pt": "Execute /setup primeiro para registrar este servidor.",
    },
    "duration_invalid": {
        "ru": "Неверный формат длительности. Используйте: 30s, 5m, 2h, 1d, 1w или infinity.",
        "uk": "Невірний формат тривалості. Використовуйте: 30s, 5m, 2h, 1d, 1w або infinity.",
        "pl": "Nieprawidłowy format czasu. Użyj: 30s, 5m, 2h, 1d, 1w lub infinity.",
        "en": "Invalid duration format. Use: 30s, 5m, 2h, 1d, 1w, or infinity.",
        "es": "Formato de duración inválido. Usa: 30s, 5m, 2h, 1d, 1w o infinity.",
        "pt": "Formato de duração inválido. Use: 30s, 5m, 2h, 1d, 1w ou infinity.",
    },
    "dm_set": {
        "ru": "Сообщение для ЛС обновлено.",
        "uk": "Повідомлення для ЛС оновлено.",
        "pl": "Wiadomość prywatna zaktualizowana.",
        "en": "DM message updated.",
        "es": "Mensaje privado actualizado.",
        "pt": "Mensagem privada atualizada.",
    },
    "dm_no_setup": {
        "ru": "Сначала выполните /setup для регистрации сервера.",
        "uk": "Спочатку виконайте /setup для реєстрації сервера.",
        "pl": "Najpierw uruchom /setup, aby zarejestrować serwer.",
        "en": "Run /setup first to register this server.",
        "es": "Primero ejecuta /setup para registrar este servidor.",
        "pt": "Execute /setup primeiro para registrar este servidor.",
    },
    "ban_dm": {
        "ru": "Вы были заблокированы на сервере {server} за спам.",
        "uk": "Вас було заблоковано на сервері {server} за спам.",
        "pl": "Zostałeś zablokowany na serwerze {server} za spam.",
        "en": "You have been banned from {server} for spam.",
        "es": "Has sido baneado del servidor {server} por spam.",
        "pt": "Você foi banido do servidor {server} por spam.",
    },
    "ban_log": {
        "ru": "Пользователь {mention} ({username} {id}) заблокирован за спам.",
        "uk": "Користувача {mention} ({username} {id}) заблоковано за спам.",
        "pl": "Użytkownik {mention} ({username} {id}) został zablokowany za spam.",
        "en": "User {mention} ({username} {id}) banned for spam.",
        "es": "Usuario {mention} ({username} {id}) bloqueado por spam.",
        "pt": "Usuário {mention} ({username} {id}) banido por spam.",
    },
    "duration_infinity": {
        "ru": "навсегда",
        "uk": "назавжди",
        "pl": "na zawsze",
        "en": "permanent",
        "es": "permanente",
        "pt": "permanente",
    },
    "autorole_set": {
        "ru": "Роль для всех установлена: <@&{role_id}>. Выдаю роль участникам без неё...",
        "uk": "Роль для всіх встановлено: <@&{role_id}>. Видаю роль учасникам без неї...",
        "pl": "A roleta está definida para todos: <@&{role_id}>. Nadaję rolę członkom, którzy jej nie mają...",
        "en": "Auto-role set to <@&{role_id}>. Assigning it to members who don't have it...",
        "es": "El rol está establecido para todos: <@&{role_id}>. Asignando a miembros que no lo tienen...",
        "pt": "A cargo está definida para todos: <@&{role_id}>. Atribuindo a membros que não o têm...",
    },
    "autorole_done": {
        "ru": "Готово. Роль выдана {count} участникам.",
        "uk": "Готово. Роль видано {count} учасникам.",
        "pl": "Gotowe. Rola nadana {count} członkom.",
        "en": "Done. Role assigned to {count} members.",
        "es": "Listo. Rol asignado a {count} miembros.",
        "pt": "Pronto. Cargo atribuído a {count} membros.",
    },
    "autorole_invalid_role": {
        "ru": "Роль с ID {role_id} не найдена на этом сервере.",
        "uk": "Роль з ID {role_id} не знайдено на цьому сервері.",
        "pl": "Rola o ID {role_id} nie została znaleziona na tym serwerze.",
        "en": "Role with ID {role_id} not found on this server.",
        "es": "No se encontró el rol con ID {role_id} en este servidor.",
        "pt": "Cargo com ID {role_id} não encontrado neste servidor.",
    },
    "autorole_no_setup": {
        "ru": "Сначала выполните /setup для регистрации сервера.",
        "uk": "Спочатку виконайте /setup для реєстрації сервера.",
        "pl": "Najpierw uruchom /setup, aby zarejestrować serwer.",
        "en": "Run /setup first to register this server.",
        "es": "Primero ejecuta /setup para registrar este servidor.",
        "pt": "Execute /setup primeiro para registrar este servidor.",
    },
    "bot_started": {
        "ru": "🤖 Бот запущен.",
        "uk": "🤖 Бот запущено.",
        "pl": "🤖 Bot uruchomiony.",
        "en": "🤖 Bot started.",
        "es": "🤖 Bot iniciado.",
        "pt": "🤖 Bot iniciado.",
    },
    "bot_stopped": {
        "ru": "🛑 Бот остановлен.",
        "uk": "🛑 Бот зупинено.",
        "pl": "🛑 Bot zatrzymany.",
        "en": "🛑 Bot stopped.",
        "es": "🛑 Bot detenido.",
        "pt": "🛑 Bot parado.",
    },
    "setup_success_network": {
        "ru": "Сервер зарегистрирован. Язык: {lang}, лог-канал: <#{channel_id}>, сеть: {network}.",
        "uk": "Сервер зареєстровано. Мова: {lang}, лог-канал: <#{channel_id}>, мережа: {network}.",
        "pl": "Serwer zarejestrowany. Język: {lang}, kanał logów: <#{channel_id}>, sieć: {network}.",
        "en": "Server registered. Language: {lang}, log channel: <#{channel_id}>, network: {network}.",
        "es": "Servidor registrado. Idioma: {lang}, canal de registro: <#{channel_id}>, red: {network}.",
        "pt": "Servidor registrado. Idioma: {lang}, canal de log: <#{channel_id}>, rede: {network}.",
    },
    "report_not_log_channel": {
        "ru": "Эту команду можно использовать только в лог-каналах.",
        "uk": "Цю команду можна використовувати лише в лог-каналах.",
        "pl": "Tej komendy można używać tylko na kanałach logów.",
        "en": "This command can only be used in log channels.",
        "es": "Este comando solo se puede usar en canales de registro.",
        "pt": "Este comando só pode ser usado em canais de log.",
    },
    "report_not_reply": {
        "ru": "Используйте эту команду в ответ на сообщение.",
        "uk": "Використовуйте цю команду у відповідь на повідомлення.",
        "pl": "Użyj tej komendy jako odpowiedź na wiadomość.",
        "en": "Use this command as a reply to a message.",
        "es": "Usa este comando como respuesta a un mensaje.",
        "pt": "Use este comando como resposta a uma mensagem.",
    },
    "report_message_not_found": {
        "ru": "Сообщение с таким ID не найдено на этом сервере.",
        "uk": "Повідомлення з таким ID не знайдено на цьому сервері.",
        "pl": "Wiadomość o podanym ID nie została znaleziona na tym serwerze.",
        "en": "Message with this ID was not found on this server.",
        "es": "No se encontró ningún mensaje con este ID en este servidor.",
        "pt": "Mensagem com este ID não foi encontrada neste servidor.",
    },
    "report_no_network": {
        "ru": "Сервер не привязан ни к одной сети. Укажите сеть в /setup.",
        "uk": "Сервер не прив'язаний до жодної мережі. Вкажіть мережу у /setup.",
        "pl": "Serwer nie jest przypisany do żadnej sieci. Ustaw sieć w /setup.",
        "en": "Server is not part of any network. Set a network in /setup.",
        "es": "El servidor no pertenece a ninguna red. Establece una red en /setup.",
        "pt": "O servidor não faz parte de nenhuma rede. Defina uma rede em /setup.",
    },
    "report_sent": {
        "ru": "Жалоба отправлена во все лог-каналы сети.",
        "uk": "Скаргу надіслано в усі лог-канали мережі.",
        "pl": "Zgłoszenie wysłano do wszystkich kanałów logów sieci.",
        "en": "Report sent to all log channels in the network.",
        "es": "Denuncia enviada a todos los canales de registro de la red.",
        "pt": "Denúncia enviada para todos os canais de log da rede.",
    },
    "ban_cmd_success": {
        "ru": "Пользователь {user_id} заблокирован. Длительность: {duration}, причина: {reason}.",
        "uk": "Користувача {user_id} заблоковано. Тривалість: {duration}, причина: {reason}.",
        "pl": "Użytkownik {user_id} zablokowany. Czas trwania: {duration}, powód: {reason}.",
        "en": "User {user_id} banned. Duration: {duration}, reason: {reason}.",
        "es": "Usuario {user_id} bloqueado. Duración: {duration}, motivo: {reason}.",
        "pt": "Usuário {user_id} banido. Duração: {duration}, motivo: {reason}.",
    },
    "ban_cmd_log": {
        "ru": "Пользователь {user_id} заблокирован администратором {admin}. Длительность: {duration}, причина: {reason}.",
        "uk": "Користувача {user_id} заблоковано адміністратором {admin}. Тривалість: {duration}, причина: {reason}.",
        "pl": "Użytkownik {user_id} zablokowany przez administratora {admin}. Czas trwania: {duration}, powód: {reason}.",
        "en": "User {user_id} banned by admin {admin}. Duration: {duration}, reason: {reason}.",
        "es": "Usuario {user_id} bloqueado por el administrador {admin}. Duración: {duration}, motivo: {reason}.",
        "pt": "Usuário {user_id} banido pelo administrador {admin}. Duração: {duration}, motivo: {reason}.",
    },
    "ban_cmd_failed": {
        "ru": "Не удалось заблокировать пользователя {user_id}: {error}",
        "uk": "Не вдалося заблокувати користувача {user_id}: {error}",
        "pl": "Nie udało się zablokować użytkownika {user_id}: {error}",
        "en": "Failed to ban user {user_id}: {error}",
        "es": "No se pudo bloquear al usuario {user_id}: {error}",
        "pt": "Falha ao banir o usuário {user_id}: {error}",
    },
    "ban_cmd_invalid_id": {
        "ru": "Неверный ID пользователя.",
        "uk": "Невірний ID користувача.",
        "pl": "Nieprawidłowe ID użytkownika.",
        "en": "Invalid user ID.",
        "es": "ID de usuario inválido.",
        "pt": "ID de usuário inválido.",
    },
}

URL_RE = re.compile(
    r"(https?://|discord\.gg/|discord\.com/invite/)\S+",
    re.IGNORECASE
)

def is_admin(user_id):
    return user_id in ADMINS

def get_guild_lang(guild_id):
    row = db.get_guild(guild_id)
    if row and row["lang"] in SUPPORTED_LANGS:
        return row["lang"]
    return DEFAULT_LANG

def localized(key, locale, **kwargs):
    table = _LOCALE.get(key, {})
    template = table.get(locale, table.get(DEFAULT_LANG, key))
    try:
        return template.format(**kwargs)
    except Exception:
        return template

def parse_duration(text):
    """Returns duration in seconds, or None for infinity. Raises ValueError on invalid format."""
    text = text.strip().lower()
    if text == "infinity":
        return None
    m = re.fullmatch(r"(\d+)(s|m|h|d|w)", text)
    if not m:
        raise ValueError("invalid_duration")
    n = int(m.group(1))
    unit = m.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return n * multipliers[unit]

def format_duration(seconds, lang):
    if seconds is None:
        return localized("duration_infinity", lang)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 604800:
        return f"{seconds // 86400}d"
    return f"{seconds // 604800}w"

def message_has_spam(message):
    """Returns True if the message contains a URL or any attachment (file, image, video, gif)."""
    if message.attachments:
        return True
    if URL_RE.search(message.content or ""):
        return True
    return False
