"""
Как пользоваться скриптом:

1. Установить библиотеки serial и ntplib: `pip install pyserial`, `pip install ntplib`
2. Посмотреть в диспетчере устройств номер COM порта адаптера RS-485 (будет что-то вроде COM4, COM8, ...)
3. В строчке 18 скрипта вставить port_name_gl='COMx' вместо port_name_gl='COM3', где COMx - номер порта адаптера
5. Если требуется сменить боды (baud rate), то необходимо сменить baudrate_gl=4800 на baudrate_gl=xxxx (где хххх - желаемые боды) в строчке 19
"""

import socket
import struct
import time

import serial
from datetime import datetime, timezone
import ntplib

port_name_gl='COM3'
baudrate_gl=4800
host_gl="asia.pool.ntp.org"

from datetime import timezone

def generate_gprmc_sentence(time, lat, lat_dir, lon, lon_dir, speed, course):
    """Генерирует строку GPRMC с правильной контрольной суммой."""

    time_str = time.strftime("%H%M%S")
    date_str = time.strftime("%d%m%y")
    
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

def get_ntp_time_raw(host, ser):
    client = ntplib.NTPClient()

    try:
        response = client.request(host, version=3, timeout=5)

        # dt = datetime.fromtimestamp(response.tx_time)
        dt = datetime.fromtimestamp(response.tx_time, timezone.utc)

        packet = generate_gprmc_sentence(
            dt,
            "5545.1234",
            "N",
            "03737.5678",
            "E",
            "10.5",
            "180.0"
        )

        ser.write(packet.encode('ascii'))

        return time.ctime(response.tx_time)

    except (ntplib.NTPException, TimeoutError) as e:
        print("NTP error:", e)
        return None

if __name__ == "__main__":
    with serial.Serial(port_name_gl, baudrate_gl, timeout=1) as ser:

        while True:
            print("Current NTP Time (Raw): ", get_ntp_time_raw(host_gl, ser))
            time.sleep(0.5)