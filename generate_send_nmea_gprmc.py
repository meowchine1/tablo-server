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
4. Интервал отправки задаётся константой SEND_INTERVAL (секунды, по умолчанию 0.3).
5. Каналы работают независимо, отправка не прерывается. Если канал недоступен
   (устройство не подключено / нет драйвера), вместо него ставится заглушка
   Null: данные «пишутся в пустоту» и отбрасываются, остальные каналы при этом
   продолжают работать. Состояние портов периодически перепроверяется
   (PORT_CHECK_INTERVAL, по умолчанию 5 с): как только устройство появится,
   отправка автоматически пойдёт в порт вместо заглушки; если порт отвалился,
   канал снова уходит в заглушку и продолжает работать, не останавливая скрипт.
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

import os
import serial
import threading
import time
from datetime import datetime, timezone, timedelta

# ======================= Настройки =======================

# Интервал отправки NMEA-сообщений, секунд
SEND_INTERVAL = 0.3

# Периодичность проверки состояния портов, секунд (заглушка -> порт, порт -> заглушка)
PORT_CHECK_INTERVAL = 5.0

# Список портов: у каждого свой baudrate (по умолчанию 4800)
PORTS = [
    {"port": "/dev/ttyUSB0", "baudrate": 4800},  # USB-адаптер 1
    {"port": "/dev/ttyUSB1", "baudrate": 4800},  # USB-адаптер 2
    {"port": "/dev/ttyAMA3", "baudrate": 4800},  # UART3 (GPIO4 TX / GPIO5 RX), пины 7/29
]

# =========================================================

def generate_gprmc_sentence(lat, lat_dir, lon, lon_dir, speed, course):
    """Генерирует строку GPRMC с правильной контрольной суммой."""
    now = datetime.now(timezone.utc) #+ timedelta(hours=5)  
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


def sender_worker(cfg, ser, stop_event):
    """Непрерывный цикл отправки NMEA-сообщений в один канал.

    Запускается в отдельном потоке для каждого порта. Отправка не
    прерывается из-за проблем с портом: при ошибке записи канал переходит
    на заглушку Null, а раз в PORT_CHECK_INTERVAL секунд состояние порта
    проверяется заново (refresh_port). Как только порт снова доступен,
    отправка автоматически идёт в него, а не в заглушку. Поток завершается
    только по stop_event (Ctrl+C), закрывая свой порт. Для канала-заглушки
    Null сообщения «Отправлено» в консоль не выводятся.
    """
    port_name = cfg["port"]
    baudrate = cfg.get("baudrate", 4800)
    next_check = time.monotonic() + PORT_CHECK_INTERVAL

    try:
        while not stop_event.is_set():
            # Периодическая проверка состояния порта (по умолчанию раз в 5 с):
            # заглушка Null -> реальный порт, отвалившийся порт -> заглушка
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + PORT_CHECK_INTERVAL
                ser = refresh_port(ser, port_name, baudrate)

            # Пример данных: 55°45.123' N, 037°37.567' E, 10.5 узлов, курс 180.0
            packet = generate_gprmc_sentence("5545.1234", "N", "03737.5678", "E", "10.5", "180.0")

            try:
                # Отправка байтов в порт
                ser.write(packet.encode('ascii'))
                ser.flush()
            except (serial.SerialException, OSError) as e:
                # Порт «отвалился»: канал не останавливаем, а уходим в заглушку
                print(f"[{port_name}] Ошибка записи: {e}. Канал переходит в заглушку Null.")
                ser = to_null_port(ser, port_name)

            # Для заглушки Null не выводим «Отправлено» на каждый пакет
            if not getattr(ser, "is_null", False):
                print(f"[{port_name}] Отправлено: {packet.strip()}")

            # Ждём интервала, но мгновенно просыпаемся при остановке (Ctrl+C)
            stop_event.wait(SEND_INTERVAL)
    finally:
        # Порт закрывает сам канал (заглушке Null закрытие не нужно)
        close_port(ser, port_name)


def try_open_port(cfg, announce=True):
    """Пытается открыть порт; возвращает serial-объект или None.

    announce=False — не выводить результат (используется при фоновой
    периодической проверке, чтобы не засорять консоль).
    """
    port_name = cfg["port"]
    baudrate = cfg.get("baudrate", 4800)
    try:
        # Открываем порт (стандарт NMEA-0183 — 4800 бод)
        ser = serial.Serial(port_name, baudrate, timeout=1)
    except (serial.SerialException, OSError) as e:
        if announce:
            print(f"  [НЕДОСТУПЕН] {port_name} ({baudrate} бод): {e}")
        return None
    if announce:
        print(f"  [ОТКРЫТ]     {port_name} ({baudrate} бод)")
    return ser


def close_port(ser, port_name):
    """Закрывает реальный порт (заглушке Null закрытие не нужно)."""
    if getattr(ser, "is_null", False):
        return
    try:
        ser.close()
    except Exception as e:
        print(f"[{port_name}] Ошибка при закрытии порта: {e}")


def to_null_port(ser, port_name):
    """Закрывает нерабочий порт и возвращает вместо него заглушку Null."""
    close_port(ser, port_name)
    return NullPort(port_name)


def refresh_port(ser, port_name, baudrate):
    """Проверяет состояние канала и возвращает актуальный объект порта.

    Вызывается раз в PORT_CHECK_INTERVAL секунд:
      • канал работал через заглушку Null — пробуем открыть порт снова;
        как только устройство доступно, отправка пойдёт в порт, а не в Null;
      • порт реальный, но устройство пропало (например, выдернули
        USB-адаптер) — канал переходит в заглушку Null и продолжает работу.
    """
    if getattr(ser, "is_null", False):
        new_ser = try_open_port({"port": port_name, "baudrate": baudrate}, announce=False)
        if new_ser is None:
            return ser  # порт всё ещё недоступен — продолжаем писать в заглушку
        print(f"[{port_name}] Порт стал доступен — отправка идёт в порт вместо заглушки.")
        return new_ser

    # Порт реальный: проверяем, что устройство на месте
    if not os.path.exists(port_name):
        print(f"[{port_name}] Устройство пропало — канал переходит в заглушку Null.")
        return to_null_port(ser, port_name)

    return ser


def start_sender(cfg, ser, stop_event):
    """Запускает поток отправки для канала и возвращает его."""
    port_name = cfg["port"]
    t = threading.Thread(
        target=sender_worker,
        args=(cfg, ser, stop_event),
        name=f"sender-{port_name}",
        daemon=True,
    )
    t.start()
    return t


def main():
    stop_event = threading.Event()
    channels = []  # кортежи: (имя канала, поток-отправитель)
    real_ports = []  # каналы, открытые реально (не заглушки)

    print("=" * 60)
    print("Открытие последовательных портов:")

    # Открываем каждый канал из списка PORTS. Если устройство недоступно —
    # вместо него ставим заглушку Null: канал всё равно работает, а состояние
    # порта перепроверяется раз в PORT_CHECK_INTERVAL секунд, и как только
    # устройство появится, отправка автоматически пойдёт в него.
    for cfg in PORTS:
        name = cfg["port"]
        ser = try_open_port(cfg)
        if ser is None:
            ser = NullPort(name)
            print(f"  [ЗАГЛУШКА]  {name} — устройства нет, пишу в Null")
        else:
            real_ports.append(name)
        channels.append((name, start_sender(cfg, ser, stop_event)))

    print("=" * 60)
    if real_ports:
        print(f"NMEA отправляется в: {', '.join(real_ports)}")
    else:
        print("Ни один канал не доступен — пока все каналы работают через заглушку Null.")
        print("Скрипт продолжает работу: как только порт появится, отправка пойдёт в него.")
    print(f"Интервал отправки: {SEND_INTERVAL} с. Проверка портов: каждые {PORT_CHECK_INTERVAL} с.")
    print("Остановка: Ctrl+C")
    print("=" * 60)

    try:
        # Потоки каналов работают постоянно (заглушки Null «работают» вечно),
        # выходим по Ctrl+C. Сбой одного канала остальные не останавливает.
        while any(t.is_alive() for _, t in channels):
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    finally:
        stop_event.set()  # сигнал всем потокам завершиться
        for _, t in channels:
            t.join(timeout=2)  # порты закрывает сам поток канала
        print("Все порты закрыты.")


if __name__ == "__main__":
    main()