"""
Как пользоваться скриптом на Raspberry Pi 4 Model B (Linux):

0. Сначала выполните разовую инициализацию:
   `bash setup_raspberry_pi.sh` — скрипт поставит пакеты и зависимости,
   добавит пользователя в группу dialout и включит UART3 (/dev/ttyAMA2).
1. Установить библиотеку serial: `pip install pyserial`
   (или `sudo apt install python3-serial`)
2. Скрипт одновременно отправляет одно и то же NMEA-сообщение в три порта:
   - /dev/ttyUSB0  — USB-адаптер 1
   - /dev/ttyUSB1  — USB-адаптер 2
   - /dev/ttyAMA2  — UART3 Raspberry Pi (GPIO4 TX / GPIO5 RX)
3. Каждому порту можно задать свой baudrate в списке PORTS ниже
   (по умолчанию 4800 бод — стандарт NMEA-0183).
4. Интервал отправки задаётся константой SEND_INTERVAL (секунды, по умолчанию 0.3).
5. Порты работают независимо: если один недоступен или произошла ошибка записи,
   остальные продолжают отправку.
6. Для доступа к последовательным портам пользователь должен быть в группе dialout:
   `sudo usermod -aG dialout $USER` (после этого перелогиниться).
7. Аппаратный UART3 (GPIO4/GPIO5) включается строкой `dtoverlay=uart3`
   в config.txt — скрипт инициализации делает это автоматически.
   GPIO4 = TX, GPIO5 = RX (пины 7 и 29 на разъёме 40-pin).

Запуск: `python3 generate_send_nmea_gprmc.py` (остановка — Ctrl+C)
"""

import serial
import threading
import time
from datetime import datetime, timezone

# ======================= Настройки =======================

# Интервал отправки NMEA-сообщений, секунд
SEND_INTERVAL = 0.3

# Список портов: у каждого свой baudrate (по умолчанию 4800)
PORTS = [
    {"port": "/dev/ttyUSB0", "baudrate": 4800},  # USB-адаптер 1
    {"port": "/dev/ttyUSB1", "baudrate": 4800},  # USB-адаптер 2
    {"port": "/dev/ttyAMA2", "baudrate": 4800},  # UART3 Raspberry Pi (GPIO4 TX / GPIO5 RX)
]

# =========================================================

def generate_gprmc_sentence(lat, lat_dir, lon, lon_dir, speed, course):
    """Генерирует строку GPRMC с правильной контрольной суммой."""
    now = datetime.now(timezone.utc)
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

def sender_worker(ser, port_name, interval, stop_event):
    """Цикл отправки NMEA-сообщений в один уже открытый порт.

    Запускается в отдельном потоке для каждого порта. Завершается
    по stop_event (Ctrl+C) либо при ошибке записи — при этом остальные
    порты продолжают работать независимо.
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

        print(f"[{port_name}] Отправлено: {packet.strip()}")

        # Ждём интервала, но мгновенно просыпаемся при остановке (Ctrl+C)
        stop_event.wait(interval)


def main():
    stop_event = threading.Event()

    print("=" * 60)
    print("Открытие последовательных портов:")

    workers = []  # пары (поток-отправитель, открытый порт)
    for cfg in PORTS:
        port_name = cfg["port"]
        baudrate = cfg.get("baudrate", 4800)
        try:
            # Открываем порт (стандарт NMEA-0183 — 4800 бод)
            ser = serial.Serial(port_name, baudrate, timeout=1)
        except (serial.SerialException, OSError) as e:
            print(f"  [НЕДОСТУПЕН] {port_name} ({baudrate} бод): {e}")
            continue
        print(f"  [ОТКРЫТ]     {port_name} ({baudrate} бод)")
        t = threading.Thread(
            target=sender_worker,
            args=(ser, port_name, SEND_INTERVAL, stop_event),
            name=f"sender-{port_name}",
            daemon=True,
        )
        workers.append((t, ser))

    if not workers:
        print("Ни один порт не открыт — отправлять некуда. Выход.")
        return

    print(f"Интервал отправки: {SEND_INTERVAL} с. Остановка: Ctrl+C")
    print("=" * 60)

    try:
        # Главный поток ждёт Ctrl+C, пока потоки шлют данные;
        # если все порты упали сами — завершаемся тоже
        while any(t.is_alive() for t, _ in workers):
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    finally:
        stop_event.set()  # сигнал всем потокам завершиться
        for t, _ in workers:
            t.join(timeout=2)
        for _, ser in workers:  # закрываем все открытые порты
            try:
                ser.close()
            except Exception as e:
                print(f"Ошибка при закрытии порта: {e}")
        print("Все порты закрыты.")


if __name__ == "__main__":
    main()