"""Английский перевод интерфейса: русский текст из кода -> английский. Подстановки {имя} — те же, что в ключе.

Полноту и лишние строки проверяет tests/test_i18n.py.
"""

EN = {
    # ---------- фазы полёта и сообщения логики (flight.py) ----------
    "На земле": "On the ground",
    "Арминг": "Arming",
    "Жду броска": "Ready to throw",
    "Взлёт": "Takeoff",
    "Полёт": "Flying",
    "Посадка": "Landing",
    "FPV: полное ручное (acro)": "FPV: full manual (acro)",
    "вращение (rate)": "rotation (rate)",
    "режим FPV": "FPV mode",
    "Включён режим FPV (Acro): полное ручное управление!": "FPV mode (Acro) enabled: full manual control!",
    "Режим стабилизации ({mode}) включён": "Stabilization mode ({mode}) enabled",
    "МОТОРЫ ОСТАНОВЛЕНЫ": "MOTORS STOPPED",
    "Опусти газ в самый низ, потом взлетай": "Pull the throttle all the way down, then take off",
    "Поставь газ в центр, потом взлетай": "Center the throttle, then take off",
    "Бросок: подними газ до висения и подбрось дрон — поймаю. B — отмена":
        "Throw: raise the throttle to hover level and toss the drone — I'll catch it. B — cancel",
    "Бросок: подбрось дрон — поймаю и повисну на этой высоте. B — отмена":
        "Throw: toss the drone — I'll catch it and hover at that height. B — cancel",
    "Бросок отменён": "Throw cancelled",
    "Броска не было {seconds:.0f} с — моторы выключены": "No throw for {seconds:.0f} s — motors off",
    "Газ ниже середины — ловить нечем, подними газ": "Throttle below middle — nothing to catch with, raise it",
    "Поймал! Дальше — как обычно": "Caught! Fly as usual",
    "Подожди секунду после аварийной остановки": "Wait a second after the emergency stop",
    "Газ подняли во время арминга — взлёт отменён": "Throttle raised while arming — takeoff cancelled",
    "Дрон не смог определить своё положение — взлёт отменён":
        "The drone couldn't find its position — takeoff cancelled",
    "Жду, пока дрон определит положение…": "Waiting for the drone to find its position…",
    # ---------- окно полёта: сообщения (app.py) ----------
    "На пульте не назначен аварийный стоп (kill) — назначь его в калибровке (C)":
        "No emergency stop (kill) on the controller — assign it in calibration (C)",
    "клавиатура": "keyboard",
    "Трим не нужен: в hover дрон сам держит место": "No trim needed: in hover the drone holds its position itself",
    "Трим: вправо {roll:+.1f}°, вперёд {pitch:+.1f}°": "Trim: right {roll:+.1f}°, forward {pitch:+.1f}°",
    "Окно потеряло фокус — сажаю дрон": "The window lost focus — landing the drone",
    "Программа подвисала {seconds:.1f} с — сажаю дрон": "The program froze for {seconds:.1f} s — landing the drone",
    "Сажаю дрон перед выходом…": "Landing the drone before quitting…",
    "Перезагрузка — только на земле. Сначала посади дрон": "Reboot works only on the ground. Land the drone first",
    "Пульт подключён: {name}": "Controller connected: {name}",
    "Пульт подключён: {name} — нажми C, чтобы настроить под него кнопки":
        "Controller connected: {name} — press C to set up its buttons",
    "Пульт отключился — сажаю дрон": "Controller disconnected — landing the drone",
    "Пульт отключился": "Controller disconnected",
    "Калибровка — только на земле. Сначала посади дрон": "Calibration works only on the ground. Land the drone first",
    "Калибровка отменена": "Calibration cancelled",
    "Пульт «{device}» настроен, но не сохранён: {error}": "Controller “{device}” is set up but not saved: {error}",
    "Пульт «{device}» настроен и сохранён": "Controller “{device}” is set up and saved",
    "Программа подвисала — бросок отменён, моторы выключены": "The program froze — throw cancelled, motors off",
    "Программа подвисала — дрон сел сам. Взлетай заново (T)":
        "The program froze — the drone landed by itself. Take off again (T)",
    "калибровка пульта": "controller calibration",
    "пульт ({name})": "controller ({name})",
    "ПУЛЬТ НЕ ПОДКЛЮЧЁН": "CONTROLLER NOT CONNECTED",
    "Газ снова у стика": "The throttle is back on the stick",
    " — газ удерживается": " — throttle held",
    " — подведи стик газа к удерживаемому газу": " — move the throttle stick to the held throttle",
    "В режиме {mode} дрон и так держит высоту — просто отпусти стики":
        "In {mode} mode the drone already holds its height — just let go of the sticks",
    "Удержание газа включается в полёте или перед броском": "Throttle hold works in flight or before a throw",
    "Удержание выкл — подведи стик газа к текущему газу, чтобы взять управление":
        "Hold off — move the throttle stick to the current throttle to take over",
    "Удержание газа: можно отпустить стик, он только подправляет газ":
        "Throttle hold: you can let go of the stick, it only nudges the throttle",
    "Дрон не присылает данные датчика ускорения — бросок сейчас не распознать":
        "The drone isn't sending accelerometer data — a throw can't be detected now",
    "Секунду: взлёт сразу после подключения и калибровки заблокирован — нажми ещё раз":
        "One second: takeoff is locked right after connecting and calibrating — press again",
    "Газ ниже середины — поймать будет нечем. Подними газ до висения, потом бросай":
        "Throttle below middle — nothing to catch with. Raise it to hover level, then throw",
    # ---------- мастер калибровки (calibration.py) ----------
    "Газ": "Throttle",
    "Подними стик газа ВВЕРХ до упора и подержи. Если он уже вверху — опусти и подними снова.":
        "Push the throttle stick all the way UP and hold it. If it's already up, lower it and raise it again.",
    "Вперёд": "Forward",
    "Толкни стик наклона ВПЕРЁД (от себя) до упора и подержи.":
        "Push the tilt stick all the way FORWARD (away from you) and hold it.",
    "Вправо": "Right",
    "Отклони стик наклона ВПРАВО до упора и подержи.": "Push the tilt stick all the way RIGHT and hold it.",
    "Поворот": "Turn",
    "Отклони стик поворота ВПРАВО до упора и подержи. Если поворачивать будешь кнопками — Backspace.":
        "Push the turn stick all the way RIGHT and hold it. If you'll turn with buttons — Backspace.",
    "Нажми кнопку или щёлкни тумблер для ВЗЛЁТА.": "Press the button or flip the switch for TAKEOFF.",
    "Нажми кнопку или щёлкни тумблер для ПОСАДКИ.": "Press the button or flip the switch for LANDING.",
    "Аварийный стоп": "Emergency stop",
    "Кнопка или тумблер, который мгновенно ГЛУШИТ МОТОРЫ. Очень советую назначить.":
        "A button or switch that instantly STOPS THE MOTORS. Strongly recommended.",
    "Удержание газа": "Throttle hold",
    "Кнопка удержания газа (режим manual): нажал — газ стоит.":
        "Throttle hold button (manual mode): press it and the throttle stays put.",
    "Поворот влево": "Turn left",
    "Кнопка поворота влево, пока зажата.": "Turns left while held.",
    "Поворот вправо": "Turn right",
    "Кнопка поворота вправо, пока зажата.": "Turns right while held.",
    "Бросок": "Throw",
    "Кнопка броска: нажал — подбрасываешь дрон, он сам ловится в воздухе.":
        "Throw button: press it, toss the drone, and it catches itself in the air.",
    "назад": "back",
    "отмена": "cancel",
    "кнопка {button}": "button {button}",
    "ось {axis} > {threshold:+.1f}": "axis {axis} > {threshold:+.1f}",
    "ось {axis} < {threshold:+.1f}": "axis {axis} < {threshold:+.1f}",
    "Этот шаг пропустить нельзя": "This step can't be skipped",
    "Пульт отключился — подключи его снова": "Controller disconnected — plug it in again",
    "Подвигай все стики до упора — пока вижу движение только у {moved} осей":
        "Move all sticks to their limits — so far I see movement on only {moved} axes",
    "Проведи газ от самого низа до самого верха, потом снова подними и подержи":
        "Move the throttle from the very bottom to the very top, then raise it again and hold",
    "Покрути этот стик до упора во все стороны, потом снова отклони и подержи":
        "Move this stick to its limits in every direction, then push it again and hold",
    "Уже занято: {clash}. Нажми другую кнопку или Backspace": "Already used for {clash}. Press another button or Backspace",
    "ось {axis}": "axis {axis}",
    "ось {axis}, обратная": "axis {axis}, reversed",
    "Аварийный стоп на пульте не назначен — останется только Пробел на клавиатуре":
        "No emergency stop on the controller — only Space on the keyboard will stop the motors",
    "Подключи пульт": "Connect a controller",
    "Пульт не найден. Подключи его к компьютеру (USB или Bluetooth).":
        "No controller found. Connect it to the computer (USB or Bluetooth).",
    "Какой пульт настраиваем?": "Which controller are we setting up?",
    "Подключено несколько пультов — тронь стик или нажми кнопку на том, который нужен.":
        "Several controllers are connected — move a stick or press a button on the one you want.",
    "первый в списке": "first in the list",
    "Положение покоя": "Rest position",
    "Отпусти стики и не трогай пульт — запомню, где у него центр. Газ без пружины оставь внизу.":
        "Let go of the sticks and don't touch the controller — I'll remember its center. "
        "Leave a non-spring throttle at the bottom.",
    "запомнить сейчас": "remember now",
    "Ход стиков": "Stick travel",
    "Покрути оба стика по кругу до упора, газ проведи от низа до верха. Потом нажми любую кнопку на пульте.":
        "Circle both sticks to their limits and move the throttle from bottom to top. "
        "Then press any button on the controller.",
    "дальше": "next",
    "Готово": "Done",
    "Проверь, что получилось. Сохраню раскладку под именем этого пульта — в следующий раз он подхватится сам.":
        "Check the result. I'll save the layout under this controller's name — next time it's picked up "
        "automatically.",
    "сохранить": "save",
    "пропустить": "skip",
    " Не нужна — Backspace.": " Not needed — Backspace.",
    # ---------- поиск по каналам, подключение, драйвер, связь ----------
    "канал {channel} · {rate} · связь {answers:.0%}": "channel {channel} · {rate} · link {answers:.0%}",
    "Дрон не отвечает: он включён? Совпадает адрес (uri в config.json)?":
        "The drone doesn't answer: is it on? Does the address match (uri in config.json)?",
    "Crazyradio не открывается: вставь его заново или закрой cfclient":
        "Can't open the Crazyradio: plug it in again or close cfclient",
    "Неверный адрес дрона в config.json (uri)": "Wrong drone address in config.json (uri)",
    "Дрон не ответил на перезагрузку: он включён?": "The drone didn't answer the reboot: is it on?",
    "неизвестная ошибка": "unknown error",
    "Ни на одном канале нет дрона с адресом {address:010X} — он включён?":
        "No drone with address {address:010X} on any channel — is it on?",
    "Внутренняя ошибка подключения — пробую снова": "Internal connection error — trying again",
    "Перезагрузка зависла — открываю радио заново": "The reboot hung — reopening the radio",
    "Поиск по каналам завис — открываю радио заново": "The channel search hung — reopening the radio",
    "Подключение зависло — открываю радио заново": "The connection hung — reopening the radio",
    "{action}: у пульта нет кнопки {button} (всего {count})":
        "{action}: the controller has no button {button} ({count} in total)",
    "{action}: у пульта нет оси {axis} (всего {count})": "{action}: the controller has no axis {axis} ({count} in total)",
    "Ставлю драйвер Crazyradio — подтверди запрос Windows «Разрешить этому приложению…»":
        "Installing the Crazyradio driver — confirm the Windows prompt “Allow this app…”",
    "Драйвер установлен. Если радио не подхватилось за пару секунд — выдерни и вставь его":
        "Driver installed. If the radio isn't picked up in a couple of seconds, unplug it and plug it back in",
    "Установка драйвера отменена. D — попробовать снова": "Driver installation cancelled. D — try again",
    "Драйвер не установился (код {code}). D — ещё раз; не выходит — Zadig, см. README":
        "The driver didn't install (code {code}). D — try again; if it still fails — Zadig, see README",
    "Дрон не отвечает — связь потеряна (выключили дрон или выдернули радио?)":
        "The drone doesn't answer — link lost (drone switched off or radio unplugged?)",
    "нет": "none",
    "Дрон заблокирован прошивкой — нажми R: перезагружу его по радио и подключусь снова":
        "The firmware locked the drone — press R: I'll reboot it over the radio and reconnect",
    "Дрон перевёрнут или стоит криво — поставь его ровно": "The drone is upside down or tilted — put it level",
    "Дрон не прислал параметры — перезапусти его и попробуй снова":
        "The drone didn't send its parameters — restart it and try again",
    "Связь с дроном потеряна: {message}": "Link to the drone lost: {message}",
    # ---------- экран подключения и калибровки (screens.py, lobby.py) ----------
    "{name} — свой профиль": "{name} — own profile",
    "{name} — общая раскладка (C — настроить)": "{name} — default layout (C — set up)",
    "Вставь Crazyradio": "Plug in the Crazyradio",
    "Радио не видно в USB. Вставь его — программа подхватит сама, перезапускать ничего не нужно.":
        "The radio isn't visible on USB. Plug it in — the program will pick it up, no restart needed.",
    "Нужен драйвер": "Driver needed",
    "Crazyradio вставлен, но без драйвера libusb-win32. Ставлю его сам — Windows спросит разрешения.":
        "The Crazyradio is plugged in but has no libusb-win32 driver. Installing it — Windows will ask "
        "for permission.",
    "Ищу дрон": "Searching for the drone",
    "Включи дрон и поставь его ровно. Ищу на всех каналах и подключусь, как только он ответит, — включать "
    "можно в любой момент. Первое подключение занимает 10–20 секунд.":
        "Turn the drone on and put it level. I search all channels and connect as soon as it answers — "
        "you can turn it on at any time. The first connection takes 10–20 seconds.",
    "Выбери дрон": "Choose a drone",
    "В эфире несколько дронов. Нажми цифру своего — подключусь и запомню его. Ближний к радио обычно "
    "отвечает лучше.":
        "Several drones are on the air. Press the number of yours — I'll connect and remember it. "
        "The one closest to the radio usually answers best.",
    "Перезагружаю дрон": "Rebooting the drone",
    "Перезапускаю дрон по радио — это снимает блокировку прошивки. Не трогай его пару секунд.":
        "Restarting the drone over the radio — this clears the firmware lock. Don't touch it for a couple "
        "of seconds.",
    "Подключено": "Connected",
    "Дрон на связи.": "The drone is online.",
    "Это твой дрон?": "Is this your drone?",
    "Нашёл дрон на другом канале, не на том, что в настройках. Если он твой — нажми 1: подключусь и запомню "
    "канал, дальше спрашивать не буду.":
        "Found a drone on a different channel than in the settings. If it's yours, press 1: I'll connect, "
        "remember the channel and won't ask again.",
    "весь экран": "full screen",
    "Подключение": "Connection",
    "адрес": "address",
    "попытка": "attempt",
    "пульт": "controller",
    "не подключён": "not connected",
    "выбрать дрон": "choose drone",
    "перезагрузить дрон": "reboot drone",
    "поставить драйвер": "install driver",
    "выход": "quit",
    "Калибровка пульта": "Controller calibration",
    "Пробел на клавиатуре глушит моторы и во время калибровки. Летать с пульта можно после неё.":
        "Space on the keyboard stops the motors during calibration too. Fly with the controller after it.",
    "Шаг {step} из {total}": "Step {step} of {total}",
    "Подключены": "Connected",
    "Назначено": "Assigned",
    "Оси": "Axes",
    "Здесь появятся оси и кнопки, когда пульт будет выбран.":
        "Axes and buttons will appear here once a controller is chosen.",
    "Кнопки": "Buttons",
    "Внимание": "Attention",
    # ---------- окно полёта (ui.py, theme.py) ----------
    "держит место и высоту": "holds position and height",
    "держит высоту": "holds height",
    "ручной газ": "manual throttle",
    "взлёт": "takeoff",
    "посадка": "land",
    "Пробел": "Space",
    "стоп моторов": "stop motors",
    "посадить и выйти": "land and quit",
    "Стрелки": "Arrows",
    "вперёд и вбок": "forward and sideways",
    "поворот": "turn",
    "выше / ниже": "up / down",
    "наклон": "tilt",
    "Shift+стрелки": "Shift+arrows",
    "трим": "trim",
    "газ": "throttle",
    "бросок": "throw",
    "калибровка": "calibration",
    "монитор пульта": "controller monitor",
    "перезагрузка": "reboot",
    "{volts:.2f} В": "{volts:.2f} V",
    "{percent}% перед взлётом": "{percent}% before takeoff",
    "связь {quality}": "link {quality}",
    "деки: {decks}": "decks: {decks}",
    "Состояние": "Status",
    "цель {height:.2f} м": "target {height:.2f} m",
    "связь": "link",
    "качество радио": "radio quality",
    "высота": "height",
    "{:.2f} м": "{:.2f} m",
    "батарея": "battery",
    "курс": "heading",
    "крен": "roll",
    "тангаж": "pitch",
    "трим {roll:+.1f}° / {pitch:+.1f}°": "trim {roll:+.1f}° / {pitch:+.1f}°",
    "тяга": "thrust",
    "вправо / вперёд": "right / forward",
    "Стики": "Sticks",
    "выше-ниже": "up-down",
    "поворот · {throttle}": "turn · {throttle}",
    "вбок · вперёд": "sideways · forward",
    "Пульт": "Controller",
    "Не подключён": "Not connected",
    "Подключи геймпад или пульт по USB — программа найдёт его сама.":
        "Connect a gamepad or RC transmitter over USB — the program will find it.",
    "свой профиль": "own profile",
    "общая раскладка — нажми C": "default layout — press C",
    "Управление: {source}": "Control: {source}",
    # ---------- настройки (config.py) ----------
    "целое число": "an integer",
    "число": "a number",
    "{name}: нужно {kind}, а не {value!r}": "{name}: expected {kind}, not {value!r}",
    "{name} должно быть от {low} до {high}, сейчас {value}": "{name} must be between {low} and {high}, now {value}",
    "{name}: нужно true или false, а не {value!r}": "{name}: expected true or false, not {value!r}",
    "нужно flight.min_height <= flight.takeoff_height <= flight.max_height":
        "flight.min_height <= flight.takeoff_height <= flight.max_height is required",
    "нужно flight.thrust_min < flight.thrust_max": "flight.thrust_min < flight.thrust_max is required",
    "привязка должна содержать либо 'button', либо 'axis'": "a binding must contain either 'button' or 'axis'",
    "для 'axis' укажи ровно один порог: 'above' или 'below'": "for 'axis' give exactly one threshold: 'above' or 'below'",
    "порог above/below": "above/below threshold",
    "калибровка оси {axis}: слишком маленький ход ({low}..{high})":
        "axis {axis} calibration: travel too small ({low}..{high})",
    "калибровка оси {axis}: center должен быть между low и high":
        "axis {axis} calibration: center must be between low and high",
    "{name}: нужен список калибровок осей": "{name}: a list of axis calibrations is required",
    "{name}: одна ось откалибрована дважды": "{name}: one axis is calibrated twice",
    "{name}: у газа (ось {axis}) слишком маленький ход — перекалибруй пульт (C)":
        "{name}: the throttle (axis {axis}) travel is too small — recalibrate the controller (C)",
    "{name}: центр оси {axis} почти в упоре — перекалибруй пульт (C)":
        "{name}: the center of axis {axis} is almost at the limit — recalibrate the controller (C)",
    "bindings: {first} и {second} назначены на одну кнопку или тумблер":
        "bindings: {first} and {second} are on the same button or switch",
    "joystick.name_filter должен быть строкой": "joystick.name_filter must be a string",
    "uri должен быть строкой вида radio://0/80/2M/E7E7E7E7E7": "uri must be a string like radio://0/80/2M/E7E7E7E7E7",
    "mode должен быть одним из: {options}": "mode must be one of: {options}",
    "input должен быть одним из: {options}": "input must be one of: {options}",
    "language должен быть одним из: {options}": "language must be one of: {options}",
    "'{name}' должен быть объектом {{...}}": "'{name}' must be an object {{...}}",
    "неизвестные ключи в '{name}': {keys}": "unknown keys in '{name}': {keys}",
    "'{name}' должен быть списком [...]": "'{name}' must be a list [...]",
    "{file}: ошибка JSON в строке {line}: {error}": "{file}: JSON error on line {line}: {error}",
    "{file}: не удалось прочитать файл: {error}": "{file}: couldn't read the file: {error}",
    "{file}: неверное значение: {error}": "{file}: invalid value: {error}",
    "{file}: не удалось сохранить {what}: {error}": "{file}: couldn't save {what}: {error}",
    "адрес дрона": "the drone address",
    "калибровку пульта": "the controller calibration",
    "язык и полноэкранный режим": "language and full-screen mode",
    # ---------- запуск (main.py) ----------
    "Crazyradio не виден программе. Почти всегда это драйвер: запусти программу без --scan —\n"
    "она поставит драйвер сама (Windows спросит права администратора). Или вручную: Zadig,\n"
    "Options -> List All Devices, Crazyradio (USB ID 1915 7777), libusb-win32, Install/Replace Driver.":
        "The program can't see the Crazyradio. It's almost always the driver: run the program without --scan —\n"
        "it will install the driver itself (Windows will ask for administrator rights). Or manually: Zadig,\n"
        "Options -> List All Devices, Crazyradio (USB ID 1915 7777), libusb-win32, Install/Replace Driver.",
    "hover — нужен Flow deck, Lighthouse или Loco; althold — ещё подойдёт Z-ranger.":
        "hover needs a Flow deck, Lighthouse or Loco; althold also works with a Z-ranger.",
    'Убери --mode / поставь "mode": "auto" — программа выберет режим сама.':
        'Remove --mode / set "mode": "auto" — the program will pick the mode itself.',
    "Любая клавиша — выход": "Any key — quit",
    "Режиму {mode} нужны деки, которых нет на дроне (найдено: {decks})":
        "Mode {mode} needs decks the drone doesn't have (found: {decks})",
    "Связь с дроном потеряна": "Link to the drone lost",
    "Неподходящий режим": "Unsuitable mode",
    "Ошибка в настройках ({path}): {error}\nИсправь файл или удали его — тогда будут настройки по умолчанию.":
        "Settings error ({path}): {error}\nFix the file or delete it — then default settings will be used.",
    "Программа упала с ошибкой. Подробности в файле:\n{log}": "The program crashed. Details are in the file:\n{log}",
    'F: смена режима только на земле — сначала посади дрон': 'F: change modes on the ground — land first',
    'Acro: газ управляется напрямую, удержание отключено': 'Acro: direct throttle control, throttle hold disabled',
    'Включён режим FPV (Acro): полное ручное управление!': 'FPV (Acro) enabled: full manual control!',
    'Режим стабилизации ({mode}) включён': 'Stabilized mode ({mode}) enabled',
    'Acro: бросок отключён — взлетай с земли с газом внизу': 'Acro: throw disabled — arm on the ground with throttle down',
    "Дрон не подтвердил параметр {name} — арминг отменён": "Drone did not confirm {name} — arming cancelled",
    # ---------- сальто, бочка, рывок 45° ----------
    "Сальто": "Flip",
    "сальто": "flips",
    "Сальто вперёд": "Front flip",
    "Сальто назад": "Back flip",
    "Бочка влево": "Roll left",
    "Бочка вправо": "Roll right",
    "Сальто вперёд!": "Front flip!",
    "Сальто назад!": "Back flip!",
    "Бочка влево!": "Roll left!",
    "Бочка вправо!": "Roll right!",
    "Сальто готово!": "Flip done!",
    "Сальто: нажми одну стрелку": "Flip: press one arrow",
    "Сальто: сначала подними газ": "Flip: raise the throttle first",
    "Сальто — только в режиме manual или FPV (F на земле)": "Flips work only in manual or FPV mode (F on the ground)",
    "Газ в ноль — манёвр прерван": "Throttle cut — manoeuvre aborted",
    "Дрон не перешёл на угловые скорости — сальто отменено": "The drone did not switch to rate mode — flip cancelled",
    "Кнопка САЛЬТО 360° ВПЕРЁД. На DualSense — стрелка ↑.": "Button for a 360° FRONT FLIP. On DualSense — arrow ↑.",
    "Кнопка САЛЬТО 360° НАЗАД. На DualSense — стрелка ↓.": "Button for a 360° BACK FLIP. On DualSense — arrow ↓.",
    "Кнопка БОЧКИ 360° ВЛЕВО. На DualSense — стрелка ←.": "Button for a 360° ROLL LEFT. On DualSense — arrow ←.",
    "Кнопка БОЧКИ 360° ВПРАВО. На DualSense — стрелка →.": "Button for a 360° ROLL RIGHT. On DualSense — arrow →.",
    "Курок РЫВКА: пока зажат — дрон наклонён на 45° вперёд и разгоняется. На DualSense — R2.":
        "DASH trigger: while held the drone tilts 45° forward and speeds up. On DualSense — R2.",
    "Курок рывка НАЗАД (наклон 45° назад, торможение). На DualSense — L2.":
        "BACKWARD dash trigger (45° back tilt, braking). On DualSense — L2.",
    "Рывок 45° вперёд": "45° dash forward",
    "Рывок 45° назад": "45° dash backward",
    "Рывок 45° вперёд!": "45° dash forward!",
    "Рывок 45° назад!": "45° dash backward!",
    "Рывок 45° — в режимах althold, manual и FPV": "The 45° dash works in althold, manual and FPV modes",

}
