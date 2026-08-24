"""
Как пользоваться скриптом:

1. Установить библиотеку serial: `pip install pyserial`
2. Посмотреть в диспетчере устройств номер COM порта адаптера RS-485 (будет что-то вроде COM4, COM8, ...)
3. В последней строчке скрипта вставить port_name='COMx' вместо port_name='COM8', где COMx - номер порта адаптера
4. Если скрипт пропускает секунды, то можно в 50 строке заменить time.sleep(0.3) на time.sleep(0.1), тогда скрипт будет
слать несколько пакетов в секунду
5. Если требуется сменить боды (baud rate), то необходимо сменить baudrate=4800 на baudrate=xxxx (где хххх - желаемые боды) в 36 и 59 строках
"""

import serial
import time
from datetime import datetime, timezone

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

def send_to_com(port_name, baudrate=4800):
    try:
        # Открываем COM-порт (стандарт NMEA-0183 — 4800 бод)
        with serial.Serial(port_name, baudrate, timeout=1) as ser:
            print(f"Запущена отправка в {port_name}...")
            
            while True:
                # Пример данных: 55°45.123' N, 037°37.567' E, 10.5 узлов, курс 180.0
                packet = generate_gprmc_sentence("5545.1234", "N", "03737.5678", "E", "10.5", "180.0")
                
                # Отправка байтов в порт
                ser.write(packet.encode('ascii'))
                
                print(f"Отправлено: {packet.strip()}")
                time.sleep(0.3) # Интервал 0.1 секунды
                
    except serial.SerialException as e:
        print(f"Ошибка порта: {e}")
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")

if __name__ == "__main__":
    # Укажите ваш порт: 'COM3' для Windows или '/dev/ttyUSB0' для Linux
    send_to_com(port_name='COM3', baudrate=4800)