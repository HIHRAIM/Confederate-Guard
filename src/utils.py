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
    "report_no_setup": {
        "ru": "Сначала выполните /setup для регистрации сервера.",
        "uk": "Спочатку виконайте /setup для реєстрації сервера.",
        "pl": "Najpierw uruchom /setup, aby zarejestrować serwer.",
        "en": "Run /setup first to register this server.",
        "es": "Primero ejecuta /setup para registrar este servidor.",
        "pt": "Execute /setup primeiro para registrar este servidor.",
    },
    "report_invalid_message_id": {
        "ru": "Неверный ID сообщения.",
        "uk": "Невірний ID повідомлення.",
        "pl": "Nieprawidłowe ID wiadomości.",
        "en": "Invalid message ID.",
        "es": "ID de mensaje inválido.",
        "pt": "ID de mensagem inválido.",
    },
    "report_text": {
        "ru": "[Discord | {server}] {nick}:\n{content}\nID пользователя: {user_id}",
        "uk": "[Discord | {server}] {nick}:\n{content}\nID користувача: {user_id}",
        "pl": "[Discord | {server}] {nick}:\n{content}\nID użytkownika: {user_id}",
        "en": "[Discord | {server}] {nick}:\n{content}\nUser ID: {user_id}",
        "es": "[Discord | {server}] {nick}:\n{content}\nID del usuario: {user_id}",
        "pt": "[Discord | {server}] {nick}:\n{content}\nID do usuário: {user_id}",
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
    "setadmin_success": {
        "ru": "Пользователь {user_id} добавлен в администраторы этого сервера.",
        "uk": "Користувача {user_id} додано до адміністраторів цього сервера.",
        "pl": "Użytkownik {user_id} został dodany jako administrator tego serwera.",
        "en": "User {user_id} has been added as an admin of this server.",
        "es": "El usuario {user_id} ha sido añadido como administrador de este servidor.",
        "pt": "O usuário {user_id} foi adicionado como administrador deste servidor.",
    },
    "setadmin_already": {
        "ru": "Пользователь {user_id} уже является администратором этого сервера.",
        "uk": "Користувач {user_id} вже є адміністратором цього сервера.",
        "pl": "Użytkownik {user_id} jest już administratorem tego serwera.",
        "en": "User {user_id} is already an admin of this server.",
        "es": "El usuario {user_id} ya es administrador de este servidor.",
        "pt": "O usuário {user_id} já é administrador deste servidor.",
    },
    "setadmin_invalid_id": {
        "ru": "Неверный ID пользователя.",
        "uk": "Невірний ID користувача.",
        "pl": "Nieprawidłowe ID użytkownika.",
        "en": "Invalid user ID.",
        "es": "ID de usuario inválido.",
        "pt": "ID de usuário inválido.",
    },
    "remadmin_success": {
        "ru": "Пользователь {user_id} лишён статуса администратора этого сервера.",
        "uk": "Користувача {user_id} позбавлено статусу адміністратора цього сервера.",
        "pl": "Użytkownik {user_id} został pozbawiony statusu administratora tego serwera.",
        "en": "User {user_id} is no longer an admin of this server.",
        "es": "El usuario {user_id} ya no es administrador de este servidor.",
        "pt": "O usuário {user_id} não é mais administrador deste servidor.",
    },
    "remadmin_not_admin": {
        "ru": "Пользователь {user_id} не является администратором этого сервера.",
        "uk": "Користувач {user_id} не є адміністратором цього сервера.",
        "pl": "Użytkownik {user_id} nie jest administratorem tego serwera.",
        "en": "User {user_id} is not an admin of this server.",
        "es": "El usuario {user_id} no es administrador de este servidor.",
        "pt": "O usuário {user_id} não é administrador deste servidor.",
    },
    "banlink_added_invite": {
        "ru": "Код приглашения `{code}` добавлен в запрещённые.",
        "uk": "Код запрошення `{code}` додано до заборонених.",
        "pl": "Kod zaproszenia `{code}` został dodany do zabronionych.",
        "en": "Invite code `{code}` added to the banned list.",
        "es": "El código de invitación `{code}` fue añadido a la lista de prohibidos.",
        "pt": "O código de convite `{code}` foi adicionado à lista de proibidos.",
    },
    "banlink_added_url": {
        "ru": "Ссылка `{url}` добавлена в запрещённые.",
        "uk": "Посилання `{url}` додано до заборонених.",
        "pl": "Link `{url}` został dodany do zabronionych.",
        "en": "Link `{url}` added to the banned list.",
        "es": "El enlace `{url}` fue añadido a la lista de prohibidos.",
        "pt": "O link `{url}` foi adicionado à lista de proibidos.",
    },
    "unbanlink_success": {
        "ru": "Ссылка с номером {link_id} удалена из запрещённых.",
        "uk": "Посилання з номером {link_id} видалено із заборонених.",
        "pl": "Link o numerze {link_id} został usunięty z zabronionych.",
        "en": "Link number {link_id} removed from the banned list.",
        "es": "El enlace número {link_id} fue eliminado de la lista de prohibidos.",
        "pt": "O link número {link_id} foi removido da lista de proibidos.",
    },
    "unbanlink_not_found": {
        "ru": "Ссылка с номером {link_id} не найдена.",
        "uk": "Посилання з номером {link_id} не знайдено.",
        "pl": "Link o numerze {link_id} nie został znaleziony.",
        "en": "Link number {link_id} was not found.",
        "es": "No se encontró el enlace número {link_id}.",
        "pt": "O link número {link_id} não foi encontrado.",
    },
    "banlink_exists": {
        "ru": "Эта ссылка уже есть в списке запрещённых.",
        "uk": "Це посилання вже є у списку заборонених.",
        "pl": "Ten link już znajduje się na liście zabronionych.",
        "en": "This link is already in the banned list.",
        "es": "Este enlace ya está en la lista de prohibidos.",
        "pt": "Este link já está na lista de proibidos.",
    },
    "links_title": {
        "ru": "Запрещённые ссылки",
        "uk": "Заборонені посилання",
        "pl": "Zabronione linki",
        "en": "Banned links",
        "es": "Enlaces prohibidos",
        "pt": "Links proibidos",
    },
    "links_empty": {
        "ru": "Список запрещённых ссылок пуст.",
        "uk": "Список заборонених посилань порожній.",
        "pl": "Lista zabronionych linków jest pusta.",
        "en": "The banned links list is empty.",
        "es": "La lista de enlaces prohibidos está vacía.",
        "pt": "A lista de links proibidos está vazia.",
    },
    "links_kind_invite": {
        "ru": "Приглашение",
        "uk": "Запрошення",
        "pl": "Zaproszenie",
        "en": "Invite",
        "es": "Invitación",
        "pt": "Convite",
    },
    "links_kind_url": {
        "ru": "Ссылка",
        "uk": "Посилання",
        "pl": "Link",
        "en": "Link",
        "es": "Enlace",
        "pt": "Link",
    },
    "links_page": {
        "ru": "Страница {page}/{pages}",
        "uk": "Сторінка {page}/{pages}",
        "pl": "Strona {page}/{pages}",
        "en": "Page {page}/{pages}",
        "es": "Página {page}/{pages}",
        "pt": "Página {page}/{pages}",
    },
    "help_title": {
        "ru": "Команды бота",
        "uk": "Команди бота",
        "pl": "Komendy bota",
        "en": "Bot commands",
        "es": "Comandos del bot",
        "pt": "Comandos do bot",
    },
    "help_section_server_admins": {
        "ru": "Для админов серверов",
        "uk": "Для адмінів серверів",
        "pl": "Dla adminów serwerów",
        "en": "For server admins",
        "es": "Para admins de servidores",
        "pt": "Para admins de servidores",
    },
    "help_section_bot_admins": {
        "ru": "Для админов бота",
        "uk": "Для адмінів бота",
        "pl": "Dla adminów bota",
        "en": "For bot admins",
        "es": "Para admins del bot",
        "pt": "Para admins do bot",
    },
    "help_cmd_setup": {
        "ru": "/setup <язык> <channel_id> [сеть] — зарегистрировать сервер: язык, лог-канал, сеть",
        "uk": "/setup <мова> <channel_id> [мережа] — зареєструвати сервер: мова, лог-канал, мережа",
        "pl": "/setup <język> <channel_id> [sieć] — zarejestrować serwer: język, kanał logów, sieć",
        "en": "/setup <lang> <channel_id> [network] — register the server: language, log channel, network",
        "es": "/setup <idioma> <channel_id> [red] — registrar el servidor: idioma, canal de registro, red",
        "pt": "/setup <idioma> <channel_id> [rede] — registrar o servidor: idioma, canal de log, rede",
    },
    "help_cmd_guard": {
        "ru": "/guard <длительность> <причина> — включить охрану от спама в этом канале",
        "uk": "/guard <тривалість> <причина> — увімкнути охорону від спаму в цьому каналі",
        "pl": "/guard <czas> <powód> — włączyć ochronę przed spamem na tym kanale",
        "en": "/guard <duration> <reason> — enable spam guard on this channel",
        "es": "/guard <duración> <motivo> — activar la guardia antispam en este canal",
        "pt": "/guard <duração> <motivo> — ativar a guarda antispam neste canal",
    },
    "help_cmd_dm": {
        "ru": "/dm <текст> — задать текст ЛС перед баном ({{server}} — название сервера)",
        "uk": "/dm <текст> — задати текст ПП перед баном ({{server}} — назва сервера)",
        "pl": "/dm <tekst> — ustawić treść wiadomości prywatnej przed banem ({{server}} — nazwa serwera)",
        "en": "/dm <text> — set the DM text sent before a ban ({{server}} — server name)",
        "es": "/dm <texto> — establecer el texto del MD antes del baneo ({{server}} — nombre del servidor)",
        "pt": "/dm <texto> — definir o texto da MD antes do banimento ({{server}} — nome do servidor)",
    },
    "help_cmd_autorole": {
        "ru": "/autorole <role_id> — автоматически выдавать роль всем участникам",
        "uk": "/autorole <role_id> — автоматично видавати роль усім учасникам",
        "pl": "/autorole <role_id> — automatycznie nadawać rolę wszystkim członkom",
        "en": "/autorole <role_id> — automatically assign a role to all members",
        "es": "/autorole <role_id> — asignar automáticamente un rol a todos los miembros",
        "pt": "/autorole <role_id> — atribuir automaticamente um cargo a todos os membros",
    },
    "help_cmd_ban": {
        "ru": "/ban <user_id> <длительность> <причина> — забанить пользователя по ID",
        "uk": "/ban <user_id> <тривалість> <причина> — забанити користувача за ID",
        "pl": "/ban <user_id> <czas> <powód> — zbanować użytkownika po ID",
        "en": "/ban <user_id> <duration> <reason> — ban a user by ID",
        "es": "/ban <user_id> <duración> <motivo> — banear a un usuario por ID",
        "pt": "/ban <user_id> <duração> <motivo> — banir um usuário por ID",
    },
    "help_cmd_report": {
        "ru": "/report [message_id] — отправить жалобу в лог-каналы сети (или ПКМ по сообщению → Apps → report)",
        "uk": "/report [message_id] — надіслати скаргу в лог-канали мережі (або ПКМ на повідомленні → Apps → report)",
        "pl": "/report [message_id] — wysłać zgłoszenie do kanałów logów sieci (lub PPM na wiadomości → Apps → report)",
        "en": "/report [message_id] — send a report to the network log channels (or right-click a message → Apps → report)",
        "es": "/report [message_id] — enviar una denuncia a los canales de registro de la red (o clic derecho en un mensaje → Apps → report)",
        "pt": "/report [message_id] — enviar uma denúncia aos canais de log da rede (ou clique direito em uma mensagem → Apps → report)",
    },
    "help_cmd_links": {
        "ru": "/links — список запрещённых ссылок",
        "uk": "/links — список заборонених посилань",
        "pl": "/links — lista zabronionych linków",
        "en": "/links — list of banned links",
        "es": "/links — lista de enlaces prohibidos",
        "pt": "/links — lista de links proibidos",
    },
    "help_cmd_banlink": {
        "ru": "/banlink <ссылка или код> — добавить ссылку или код приглашения в запрещённые",
        "uk": "/banlink <посилання або код> — додати посилання або код запрошення до заборонених",
        "pl": "/banlink <link lub kod> — dodać link lub kod zaproszenia do zabronionych",
        "en": "/banlink <link or code> — add a link or invite code to the banned list",
        "es": "/banlink <enlace o código> — añadir un enlace o código de invitación a los prohibidos",
        "pt": "/banlink <link ou código> — adicionar um link ou código de convite aos proibidos",
    },
    "help_cmd_unbanlink": {
        "ru": "/unbanlink <номер> — удалить ссылку из запрещённых (номер — в /links)",
        "uk": "/unbanlink <номер> — видалити посилання із заборонених (номер — у /links)",
        "pl": "/unbanlink <numer> — usunąć link z zabronionych (numer — w /links)",
        "en": "/unbanlink <number> — remove a link from the banned list (number shown in /links)",
        "es": "/unbanlink <número> — eliminar un enlace de los prohibidos (el número aparece en /links)",
        "pt": "/unbanlink <número> — remover um link dos proibidos (o número aparece em /links)",
    },
    "help_cmd_setadmin": {
        "ru": "/setadmin <user_id> — назначить администратора сервера",
        "uk": "/setadmin <user_id> — призначити адміністратора сервера",
        "pl": "/setadmin <user_id> — mianować administratora serwera",
        "en": "/setadmin <user_id> — add a server admin",
        "es": "/setadmin <user_id> — nombrar un administrador del servidor",
        "pt": "/setadmin <user_id> — adicionar um administrador do servidor",
    },
    "help_cmd_remadmin": {
        "ru": "/remadmin <user_id> — лишить пользователя статуса админа сервера",
        "uk": "/remadmin <user_id> — позбавити користувача статусу адміна сервера",
        "pl": "/remadmin <user_id> — odebrać użytkownikowi status admina serwera",
        "en": "/remadmin <user_id> — remove a user's server admin status",
        "es": "/remadmin <user_id> — quitar el estado de admin del servidor a un usuario",
        "pt": "/remadmin <user_id> — remover o status de admin do servidor de um usuário",
    },
    "help_cmd_backup": {
        "ru": "/backup — получить резервную копию базы данных",
        "uk": "/backup — отримати резервну копію бази даних",
        "pl": "/backup — pobrać kopię zapasową bazy danych",
        "en": "/backup — get a database backup",
        "es": "/backup — obtener una copia de seguridad de la base de datos",
        "pt": "/backup — obter um backup do banco de dados",
    },
    "help_cmd_list_chats": {
        "ru": "/list_chats — список серверов, на которых есть бот",
        "uk": "/list_chats — список серверів, на яких є бот",
        "pl": "/list_chats — lista serwerów, na których jest bot",
        "en": "/list_chats — list of servers the bot is in",
        "es": "/list_chats — lista de servidores donde está el bot",
        "pt": "/list_chats — lista de servidores onde o bot está",
    },
    "help_cmd_force_leave": {
        "ru": "/force_leave <server_id> — вывести бота с сервера",
        "uk": "/force_leave <server_id> — вивести бота з сервера",
        "pl": "/force_leave <server_id> — usunąć bota z serwera",
        "en": "/force_leave <server_id> — make the bot leave a server",
        "es": "/force_leave <server_id> — hacer que el bot abandone un servidor",
        "pt": "/force_leave <server_id> — fazer o bot sair de um servidor",
    },
    "list_chats_header": {
        "ru": "**Discord-серверы:**",
        "uk": "**Discord-сервери:**",
        "pl": "**Serwery Discord:**",
        "en": "**Discord servers:**",
        "es": "**Servidores de Discord:**",
        "pt": "**Servidores do Discord:**",
    },
    "list_chats_too_long": {
        "ru": "Список большой — загружаю файл.",
        "uk": "Список великий — завантажую файл.",
        "pl": "Lista jest duża — wysyłam plik.",
        "en": "The list is long — uploading a file.",
        "es": "La lista es larga — subiendo un archivo.",
        "pt": "A lista é longa — enviando um arquivo.",
    },
    "force_leave_invalid_id": {
        "ru": "Неверный ID сервера.",
        "uk": "Невірний ID сервера.",
        "pl": "Nieprawidłowe ID serwera.",
        "en": "Invalid server ID.",
        "es": "ID de servidor inválido.",
        "pt": "ID de servidor inválido.",
    },
    "force_leave_not_member": {
        "ru": "Бот не находится на этом сервере.",
        "uk": "Бот не знаходиться на цьому сервері.",
        "pl": "Bot nie znajduje się na tym serwerze.",
        "en": "The bot is not a member of that server.",
        "es": "El bot no está en ese servidor.",
        "pt": "O bot não está nesse servidor.",
    },
    "force_leave_failed": {
        "ru": "Не удалось покинуть сервер: {error}",
        "uk": "Не вдалося покинути сервер: {error}",
        "pl": "Nie udało się opuścić serwera: {error}",
        "en": "Failed to leave the server: {error}",
        "es": "No se pudo abandonar el servidor: {error}",
        "pt": "Falha ao sair do servidor: {error}",
    },
    "force_leave_success": {
        "ru": "Бот покинул сервер {guild_id}, данные сервера удалены из базы.",
        "uk": "Бот покинув сервер {guild_id}, дані сервера видалено з бази.",
        "pl": "Bot opuścił serwer {guild_id}, dane serwera usunięto z bazy.",
        "en": "The bot left server {guild_id} and its data was removed from the database.",
        "es": "El bot abandonó el servidor {guild_id} y sus datos fueron eliminados de la base de datos.",
        "pt": "O bot saiu do servidor {guild_id} e seus dados foram removidos do banco de dados.",
    },
    "verify_no_setup": {
        "ru": "Сначала выполните /setup для регистрации сервера.",
        "uk": "Спочатку виконайте /setup для реєстрації сервера.",
        "pl": "Najpierw uruchom /setup, aby zarejestrować serwer.",
        "en": "Run /setup first to register this server.",
        "es": "Primero ejecuta /setup para registrar este servidor.",
        "pt": "Execute /setup primeiro para registrar este servidor.",
    },
    "verify_invalid_role": {
        "ru": "Роль с ID {role_id} не найдена на этом сервере.",
        "uk": "Роль з ID {role_id} не знайдено на цьому сервері.",
        "pl": "Rola o ID {role_id} nie została znaleziona na tym serwerze.",
        "en": "Role with ID {role_id} not found on this server.",
        "es": "No se encontró el rol con ID {role_id} en este servidor.",
        "pt": "Cargo com ID {role_id} não encontrado neste servidor.",
    },
    "verify_invalid_channel": {
        "ru": "Неверный ID канала.",
        "uk": "Невірний ID каналу.",
        "pl": "Nieprawidłowe ID kanału.",
        "en": "Invalid channel ID.",
        "es": "ID de canal inválido.",
        "pt": "ID de canal inválido.",
    },
    "verify_set": {
        "ru": "Верификация настроена. Роль: <@&{role_id}>, канал: <#{channel_id}>.",
        "uk": "Верифікацію налаштовано. Роль: <@&{role_id}>, канал: <#{channel_id}>.",
        "pl": "Weryfikacja skonfigurowana. Rola: <@&{role_id}>, kanał: <#{channel_id}>.",
        "en": "Verification configured. Role: <@&{role_id}>, channel: <#{channel_id}>.",
        "es": "Verificación configurada. Rol: <@&{role_id}>, canal: <#{channel_id}>.",
        "pt": "Verificação configurada. Cargo: <@&{role_id}>, canal: <#{channel_id}>.",
    },
    "verify_set_no_channel": {
        "ru": "Верификация настроена. Роль: <@&{role_id}>. Уведомления — в лог-канал из /setup.",
        "uk": "Верифікацію налаштовано. Роль: <@&{role_id}>. Сповіщення — в лог-канал із /setup.",
        "pl": "Weryfikacja skonfigurowana. Rola: <@&{role_id}>. Powiadomienia — na kanał logów z /setup.",
        "en": "Verification configured. Role: <@&{role_id}>. Announcements go to the /setup log channel.",
        "es": "Verificación configurada. Rol: <@&{role_id}>. Los avisos van al canal de registro de /setup.",
        "pt": "Verificação configurada. Cargo: <@&{role_id}>. Os avisos vão para o canal de log de /setup.",
    },
    "verify_announce": {
        "ru": "Пользователь {mention} ({username} {id}) верифицирован.",
        "uk": "Користувача {mention} ({username} {id}) верифіковано.",
        "pl": "Użytkownik {mention} ({username} {id}) został zweryfikowany.",
        "en": "User {mention} ({username} {id}) has been verified.",
        "es": "El usuario {mention} ({username} {id}) ha sido verificado.",
        "pt": "O usuário {mention} ({username} {id}) foi verificado.",
    },
    "verify_announce_cross": {
        "ru": "Пользователь {mention} ({username} {id}) верифицирован (из-за верификации на другом сервере).",
        "uk": "Користувача {mention} ({username} {id}) верифіковано (через верифікацію на іншому сервері).",
        "pl": "Użytkownik {mention} ({username} {id}) został zweryfikowany (z powodu weryfikacji na innym serwerze).",
        "en": "User {mention} ({username} {id}) has been verified (due to verification on another server).",
        "es": "El usuario {mention} ({username} {id}) ha sido verificado (debido a la verificación en otro servidor).",
        "pt": "O usuário {mention} ({username} {id}) foi verificado (devido à verificação em outro servidor).",
    },
    "help_cmd_setverify": {
        "ru": "/setverify <role_id> [channel_id] — выдавать роль участникам, писавшим в разные дни (общая база между серверами)",
        "uk": "/setverify <role_id> [channel_id] — видавати роль учасникам, що писали в різні дні (спільна база між серверами)",
        "pl": "/setverify <role_id> [channel_id] — nadawać rolę członkom piszącym w różne dni (wspólna baza między serwerami)",
        "en": "/setverify <role_id> [channel_id] — give a role to members who wrote on more than one day (shared cross-server database)",
        "es": "/setverify <role_id> [channel_id] — dar un rol a los miembros que escribieron en distintos días (base compartida entre servidores)",
        "pt": "/setverify <role_id> [channel_id] — dar um cargo a membros que escreveram em dias diferentes (base compartilhada entre servidores)",
    },
}

URL_RE = re.compile(
    r"(https?://|discord\.gg/|discord\.com/invite/)\S+",
    re.IGNORECASE
)

INVITE_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)",
    re.IGNORECASE
)

def normalize_banned_url(raw):
    value = raw.strip().lower()
    value = re.sub(r"^https?://", "", value)
    if value.startswith("www."):
        value = value[4:]
    return value.rstrip("/")

def classify_banned_link(raw):
    """Returns ('invite', code) or ('url', normalized_url) for a user-supplied link."""
    raw = raw.strip().rstrip("/")
    m = INVITE_LINK_RE.fullmatch(raw)
    if m:
        return "invite", m.group(1)
    if re.fullmatch(r"[A-Za-z0-9-]+", raw):
        return "invite", raw
    return "url", normalize_banned_url(raw)

def message_has_banned_link(content):
    """Returns True if the text contains a banned invite code or a banned URL."""
    if not content:
        return False
    rows = db.get_banned_links()
    if not rows:
        return False
    invites = {r["value"] for r in rows if r["kind"] == "invite"}
    urls = [r["value"] for r in rows if r["kind"] == "url"]
    for m in INVITE_LINK_RE.finditer(content):
        if m.group(1) in invites:
            return True
    if urls:
        low = content.lower()
        for u in urls:
            if u in low:
                return True
    return False

def is_admin(user_id, guild_id=None):
    if user_id in ADMINS:
        return True
    if guild_id is not None:
        return db.is_guild_admin(guild_id, user_id)
    return False

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
