"""
Как пользоваться скриптом на Raspberry Pi 4 Model B (Linux):

0. Сначала выполните разовую инициализацию:
   `bash setup_raspberry_pi.sh` — скрипт поставит пакеты и зависимости,
   добавит пользователя в группу dialout и включит UART3 (/dev/ttyAMA3).
1. Установить библиотеку serial: `pip install pyserial`
   (или `sudo apt install python3-serial`)
2. Скрипт одновременно отправляет одно и то же NMEA-сообщение в три порта:
   - /dev/ttyUSB0  — USB-адаптер 1
   - /dev/ttyUSB1  — USB-адаптер 2
   - /dev/ttyAMA3  — UART3 Raspberry Pi (GPIO4 TX / GPIO5 RX)
3. Каждому порту можно задать свой baudrate в списке PORTS ниже
   (по умолчанию 4800 бод — стандарт NMEA-0183).
4. Интервал отправки задаётся константой SEND_INTERVAL (секунды, по умолчанию 0.3).
5. Каналы работают независимо. Если какой-то канал недоступен (устройство
   не подключено / нет драйвера), вместо него ставится заглушка Null:
   данные «пишутся в пустоту» и отбрасываются. Скрипт не останавливается
   и продолжает слать NMEA в реально доступные каналы. Автоподключения нет:
   чтобы задействовать только что подключённое устройство, перезапустите скрипт.
6. Для доступа к последовательным портам пользователь должен быть в группе dialout:
   `sudo usermod -aG dialout $USER` (после этого перелогиниться).
7. Аппаратный UART3 (GPIO4/GPIO5) включается строкой `dtoverlay=uart3`
   в config.txt — скрипт инициализации делает это автоматически.
   GPIO4 = TX (TXD3), GPIO5 = RX (RXD3) — пины 7 и 29 на разъёме 40-pin.
   После включения нужна ПЕРЕЗАГРУЗКА; проверьте имя устройства:
   `ls -l /dev/ttyAMA*`. Обычно это /dev/ttyAMA3, но номер может сдвинуться
   (если /dev/ttyAMA3 отсутствует — см. пункт 5, скрипт продолжит работу).

Запуск: `python3 generate_send_nmea_gprmc.py` (остановка — Ctrl+C)
"""

import serial
import threading
import time
from datetime import datetime, timezone, timedelta

# ======================= Настройки =======================

# Интервал отправки NMEA-сообщений, секунд
SEND_INTERVAL = 0.3

# Список портов: у каждого свой baudrate (по умолчанию 4800)
PORTS = [
    {"port": "/dev/ttyUSB0", "baudrate": 115200},  # USB-адаптер 1
    {"port": "/dev/ttyUSB1", "baudrate": 115200},  # USB-адаптер 2
    {"port": "/dev/ttyAMA3", "baudrate": 115200},  # UART3 (GPIO4 TX / GPIO5 RX), пины 7/29
]

# =========================================================

def generate_gprmc_sentence(lat, lat_dir, lon, lon_dir, speed, course):
    """Генерирует строку GPRMC с правильной контрольной суммой."""
    now = datetime.now(timezone.utc) + timedelta(hours=5)  
    time_str = now.strftime("%H%M%S")
    date_str = now.strftime("%d%m%y")
    
    # Формируем тело сообщения (между $ и *)
    # Статус 'A' = Active (данные достоверны)
    mag_var="003.1"
    mag_dir="W"
    content = f"GLRMC,{time_str},A,{lat},{lat_dir},{lon},{lon_dir},{speed},{course},{date_str},{mag_var},{mag_dir}"
    
    # Вычисление контрольной суммы (XOR всех символов)
    checksum = 0
    for char in content:
        checksum ^= ord(char)
    
    # Формируем итоговую строку с префиксом $, суффиксом *XX и окончанием \r\n
    return f"${content}*{hex(checksum)[2:].upper().zfill(2)}\r\n"

class NullPort:
    """Заглушка последовательного порта: принимает данные и отбрасывает их.

    Подставляется вместо канала, когда устройство недоступно. Скрипт
    продолжает работать и отправляет NMEA только в реально доступные
    каналы; заглушка «пишет в пустоту» без ошибок.
    """
    is_null = True

    def __init__(self, name):
        self.name = name

    def write(self, data):
        # Данные «приняты», но выброшены в пустоту
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


def sender_worker(ser, port_name, interval, stop_event):
    """Цикл отправки NMEA-сообщений в один уже открытый порт.

    Запускается в отдельном потоке для каждого канала. Завершается
    по stop_event (Ctrl+C) либо при ошибке записи — при этом остальные
    каналы продолжают работать независимо. Для канала-заглушки Null
    сообщения не выводятся в консоль.
    """
    while not stop_event.is_set():
        # Пример данных: 55°45.123' N, 037°37.567' E, 10.5 узлов, курс 180.0
        packet = generate_gprmc_sentence("5545.1234", "N", "03737.5678", "E", "10.5", "180.0")

        try:
            # Отправка байтов в порт
            ser.write(packet.encode('ascii'))
            ser.flush()
        except (serial.SerialException, OSError) as e:
            print(f"[{port_name}] Ошибка записи, порт останавливается: {e}")
            break

        # Для заглушки Null не выводим «Отправлено» на каждый пакет
        if not getattr(ser, "is_null", False):
            print(f"[{port_name}] Отправлено: {packet.strip()}")

        # Ждём интервала, но мгновенно просыпаемся при остановке (Ctrl+C)
        stop_event.wait(interval)


def try_open_port(cfg):
    """Пытается открыть порт; возвращает serial-объект или None."""
    port_name = cfg["port"]
    baudrate = cfg.get("baudrate", 4800)
    try:
        # Открываем порт (стандарт NMEA-0183 — 4800 бод)
        ser = serial.Serial(port_name, baudrate, timeout=1)
    except (serial.SerialException, OSError) as e:
        print(f"  [НЕДОСТУПЕН] {port_name} ({baudrate} бод): {e}")
        return None
    print(f"  [ОТКРЫТ]     {port_name} ({baudrate} бод)")
    return ser


def start_sender(ser, port_name, stop_event):
    """Запускает поток отправки для уже открытого порта и возвращает его."""
    t = threading.Thread(
        target=sender_worker,
        args=(ser, port_name, SEND_INTERVAL, stop_event),
        name=f"sender-{port_name}",
        daemon=True,
    )
    t.start()
    return t


def main():
    stop_event = threading.Event()
    channels = []  # кортежи: (имя канала, объект порта, поток-отправитель)

    print("=" * 60)
    print("Открытие последовательных портов:")

    # Открываем каждый канал из списка PORTS. Если устройство недоступно —
    # вместо него ставим заглушку Null, чтобы скрипт продолжал работать.
    for cfg in PORTS:
        name = cfg["port"]
        ser = try_open_port(cfg)
        if ser is None:
            ser = NullPort(name)
            print(f"  [ЗАГЛУШКА]  {name} — устройства нет, пишу в Null")
        channels.append((name, ser, start_sender(ser, name, stop_event)))

    real_ports = [n for n, s, _ in channels if not getattr(s, "is_null", False)]
    print("=" * 60)
    if real_ports:
        print(f"NMEA отправляется в: {', '.join(real_ports)}")
    else:
        print("Ни один канал не доступен — все заменены заглушкой Null, отправки нет.")
    print(f"Интервал отправки: {SEND_INTERVAL} с. Остановка: Ctrl+C")
    print("=" * 60)

    try:
        # Ждём, пока все потоки живы (заглушки Null «работают» вечно),
        # выходим по Ctrl+C. Сбой одного канала остальные не останавливает.
        while any(t.is_alive() for _, _, t in channels):
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    finally:
        stop_event.set()  # сигнал всем потокам завершиться
        for name, ser, t in channels:
            t.join(timeout=2)
            try:
                ser.close()
            except Exception as e:
                print(f"[{name}] Ошибка при закрытии порта: {e}")
        print("Все порты закрыты.")


if __name__ == "__main__":
    main()