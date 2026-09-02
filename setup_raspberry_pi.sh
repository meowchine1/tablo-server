#!/usr/bin/env bash
# ============================================================================
#  setup_raspberry_pi.sh
#
#  Разовая инициализация Raspberry Pi 4 Model B (Raspberry Pi OS 32/64-bit)
#  перед запуском проекта генератора NMEA-сообщений
#  (generate_send_nmea_gprmc.py, ntp.py).
#
#  Что делает скрипт:
#    1. Обновляет список пакетов и саму ОС (при согласии).
#    2. Устанавливает python3, pip, git и зависимости проекта:
#         - pyserial (serial) — работа с последовательными портами / NMEA
#         - ntplib            — для ntp.py (точное время через NTP)
#    3. Добавляет текущего пользователя в группу dialout —
#       даёт право открывать /dev/ttyUSB0, /dev/ttyUSB1, /dev/ttyAMA2.
#    4. Включает аппаратный UART3 (GPIO4 TX / GPIO5 RX) через dtoverlay=uart3 —
#       устройство /dev/ttyAMA2 используется для отправки NMEA-сообщений.
#    5. Выводит итоговую сводку и предлагает перезагрузку.
#
#  Запуск на Raspberry Pi:
#      bash setup_raspberry_pi.sh
#
#  После перезагрузки проверьте:
#      ls -l /dev/ttyAMA2 /dev/ttyUSB0 /dev/ttyUSB1
#      id -nG            # в списке должна быть группа dialout
#      python3 -c "import serial, ntplib; print('ok')"
# ============================================================================

set -Eeuo pipefail

# --- Защита от CRLF: файлы, скопированные из Windows, содержат \r\n. ---------
#     Если такие переводы найдены — чиним файл и перезапускаем скрипт.
if grep -q $'\r' "$0"; then
    echo "[WARN]  Обнаружены CRLF-переводы строк (файл скопирован из Windows)."
    sed -i 's/\r$//' "$0"
    echo "[INFO]  Файл починен, перезапускаю скрипт..."
    exec bash "$0" "$@"
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# --- Проверка, что скрипт не запущен от root --------------------------------
if [[ "$(id -u)" -eq 0 ]]; then
    fail "Запустите скрипт как обычный пользователь (БЕЗ sudo). Права sudo скрипт запросит сам."
fi
sudo -v || fail "Нужны права sudo — продолжить нельзя."

# --- Проверка, что это Raspberry Pi -----------------------------------------
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
if [[ "$MODEL" != *"Raspberry Pi"* ]]; then
    warn "Похоже, это не Raspberry Pi (model: '${MODEL:-неизвестно}')."
    read -r -p "Продолжить всё равно? [y/N]: " ans
    [[ "${ans,,}" =~ ^y ]] || { echo "Отменено."; exit 0; }
fi

echo "============================================================================"
info "Инициализация Raspberry Pi для проекта NMEA."
info "Пользователь: $USER | Модель: $MODEL"
echo "============================================================================"

# -----------------------------------------------------------------------------
# 1. Обновление ОС
# -----------------------------------------------------------------------------
info "Обновление списка пакетов (apt-get update)..."
sudo apt-get update -y || fail "apt-get update не выполнен."

echo
read -r -p "Обновить установленные пакеты ОС (может занять время)? [y/N]: " do_up
if [[ "${do_up,,}" =~ ^y ]]; then
    info "Обновление пакетов (apt-get upgrade)..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
        || warn "apt-get upgrade завершился с ошибкой (продолжаю)."
else
    warn "apt-get upgrade пропущен."
fi
# -----------------------------------------------------------------------------
# 2. Базовые пакеты и зависимости Python
# -----------------------------------------------------------------------------
info "Установка python3, pip, git и системных Python-пакетов..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-pip python3-venv \
    python3-serial python3-ntplib \
    git || warn "Часть системных пакетов не установилась — проверяю импорт библиотек."

# Проверка: доступны ли обе библиотеки
if python3 -c "import serial, ntplib" 2>/dev/null; then
    info "Python-библиотеки pyserial и ntplib доступны."
else
    info "Системных пакетов не хватило — ставлю через pip (pyserial, ntplib)..."
    sudo python3 -m pip install --break-system-packages pyserial ntplib 2>/dev/null \
        || sudo python3 -m pip install pyserial ntplib \
        || fail "Не удалось установить pyserial/ntplib."
    python3 -c "import serial, ntplib" || fail "Проверка импорта serial/ntplib не пройдена."
fi

# -----------------------------------------------------------------------------
# 3. Группа dialout (доступ к последовательным портам)
# -----------------------------------------------------------------------------
info "Права на последовательные порты (группа dialout)..."
if id -nG "$USER" | grep -qw dialout; then
    info "Пользователь $USER уже состоит в группе dialout."
else
    sudo usermod -aG dialout "$USER"
    info "Пользователь $USER добавлен в группу dialout."
fi
# -----------------------------------------------------------------------------
# 4. UART3 Raspberry Pi (/dev/ttyAMA2, GPIO4 TX / GPIO5 RX) — для NMEA
# -----------------------------------------------------------------------------
CONFIG="/boot/firmware/config.txt"; [[ -f "$CONFIG" ]] || CONFIG="/boot/config.txt"

if [[ ! -f "$CONFIG" ]]; then
    warn "Не найден config.txt ($CONFIG). Настройте UART3 вручную:"
    warn "  Добавьте в config.txt строки:"
    warn "    dtoverlay=uart3"
    warn "    enable_uart=1"
else
    info "Настройка UART3 (GPIO4 TX / GPIO5 RX -> /dev/ttyAMA2):"

    # 4a. enable_uart=1 — флаг гарантирует работу UART-clock на Pi 4
    #     (нужен для дополнительных PL011 uart2-uart5).
    if grep -qE '^[[:space:]]*enable_uart[[:space:]]*=' "$CONFIG"; then
        sudo sed -i 's/^[[:space:]]*enable_uart[[:space:]]*=.*/enable_uart=1/' "$CONFIG"
        info "  enable_uart=1 уже есть в config.txt."
    else
        echo "enable_uart=1" | sudo tee -a "$CONFIG" > /dev/null
        info "  enable_uart=1 добавлено в $CONFIG"
    fi

    # 4b. Сам UART3: dtoverlay=uart3. Строка учитывает и варианты
    #     с параметрами: dtoverlay=uart3,ctsrts
    if grep -qE '^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*uart3([, ]|$)' "$CONFIG"; then
        info "  dtoverlay=uart3 уже есть в config.txt."
    else
        echo "dtoverlay=uart3" | sudo tee -a "$CONFIG" > /dev/null
        info "  dtoverlay=uart3 добавлено в $CONFIG"
    fi

    info "  ВАЖНО: изменения UART вступят в силу только после перезагрузки."
fi

# -----------------------------------------------------------------------------
# 5. Итог
# -----------------------------------------------------------------------------
echo
echo "============================================================================"
echo " ИТОГ:"
echo "  • python3 / pip / git   — установлены"
echo "  • pyserial, ntplib      — установлены"
echo "  • группа dialout        — $USER (применится после перезагрузки)"
echo "  • UART3 (/dev/ttyAMA2) — включён (GPIO4 TX / GPIO5 RX), после ребута активируется"
echo
echo " ДАЛЬШЕ:"
echo "  1) Перезагрузитесь:        sudo reboot"
echo "  2) Скопируйте скрипты проекта на Pi, например с компьютера:"
echo "       scp generate_send_nmea_gprmc.py ntp.py $USER@<ip-pi>:~/"
echo "  3) Проверьте порты:        ls -l /dev/ttyAMA* /dev/ttyUSB0 /dev/ttyUSB1"
echo "     UART3 обычно даёт /dev/ttyAMA2, но номер может сдвинуться —"
echo "     возьмите тот ttyAMA*, что появился после включения, и пропишите"
echo "     его в PORTS в generate_send_nmea_gprmc.py при необходимости."
echo "     Подключение UART3: TX устройства -> GPIO5 (RX), RX устройства -> GPIO4 (TX)"
echo "  4) Запустите проект:       python3 ~/generate_send_nmea_gprmc.py"
echo "     (если будет 'Permission denied' — перелогиньтесь, чтобы dialout применилась)"
echo "============================================================================"
echo
read -r -p "Перезагрузить Raspberry Pi сейчас? [y/N]: " rb
if [[ "${rb,,}" =~ ^y ]]; then
    info "Перезагрузка..."
    sudo reboot
fi
exit 0