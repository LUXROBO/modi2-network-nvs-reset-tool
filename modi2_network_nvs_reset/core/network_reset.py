import json
import sys
import threading as th
import time
from base64 import b64decode, b64encode
from io import open
from os import path
import threading
import os

import serial
# import serial.tools.list_ports as stl
import serial.tools.list_ports as sp

from modi2_network_nvs_reset.util.connection_util import SerTask
from modi2_network_nvs_reset.util.message_util import (decode_message,
                                                     parse_message,
                                                     unpack_data)
from modi2_network_nvs_reset.util.module_util import (Module,
                                                    get_module_type_from_uuid)
from modi2_network_nvs_reset.util.module_util import (Module,
                                                    get_module_uuid_from_type)

def retry(exception_to_catch):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_to_catch:
                return wrapper(*args, **kwargs)
        return wrapper
    return decorator

class Network_reset_manager:
    """Module Firmware Updater: Updates a firmware of given module"""
    SERIAL_MODE_COMPORT = 1
    SERIAL_MODI_WINUSB = 2
    
    NO_ERROR = 0
    UPDATE_READY = 1
    WRITE_FAIL = 2
    VERIFY_FAIL = 3
    CRC_ERROR = 4
    CRC_COMPLETE = 5
    ERASE_ERROR = 6
    ERASE_COMPLETE = 7

    UPDATE_FIRMWARE_MODE = 0
    CHNAGE_TYPE_MODE = 1

    FILE_UPLOADE_STATE = 0x15
    FILE_UPLOADE_COMMAND = 0x16

# self.signal_list[2].emit(100," ")
    def __init__(
        self, port=None, conn_type="ser"
    ):
        self.print = True
        self.conn_type = conn_type

        self.app_version = 8192

        self.line = [] #라인 단위로 데이터 가져올 리스트 변수
        self.serial_port = None
        self.baud = 115200
        self.exitThread = False   # 쓰레드 종료용 변수
        self.file_name = ""
        self.start_flag = False
        self.signal_list = []
        self.target_module_health_flag = False
        self.target_uuid = 0
        self.target_id = 0
        self.nvs_reset_timeout_thread = None
        
    def __del__(self):
        try:
            self.close()
        except serial.SerialException:
            self.__print("Magic del is called with an exception")

    def parse_message(self, command: int, source: int, destination: int,
                    byte_data: bytes):
        message = dict()
        message['c'] = command
        message['s'] = source
        message['d'] = destination
        message['b'] = self.__encode_bytes(byte_data)
        message['l'] = len(byte_data)
        return json.dumps(message, separators=(",", ":"))

    def __encode_bytes(self, byte_data: bytes):
        idx = 0
        data = bytearray(len(byte_data))
        while idx < len(byte_data):
            if not byte_data[idx]:
                idx += 1
            elif byte_data[idx] > 256:
                length = self.__extract_length(idx, byte_data)
                data[idx: idx + length] = int.to_bytes(
                    byte_data[idx], byteorder='little', length=length, signed=True
                )
                idx += length
            elif byte_data[idx] < 0:
                data[idx: idx + 4] = int.to_bytes(
                    int(byte_data[idx]), byteorder='little', length=4, signed=True
                )
                idx += 4
            elif byte_data[idx] < 256:
                data[idx] = int(byte_data[idx])
                idx += 1
        return b64encode(bytes(data)).decode('utf8')

    def __extract_length(self, begin: int, src: bytes) -> int:
        length = 1
        for i in range(begin + 1, len(src)):
            if not src[i]:
                length += 1
            else:
                break
        return length

    def uuid_to_module_type(self, uuid):
        module_type_temp = (uuid >> 32) & 0xFFFF
        if module_type_temp == 0x0000:
            return "Network"
        elif module_type_temp == 0x0010:
            return "Battery"
        elif module_type_temp == 0x2000:
            return "Env"
        elif module_type_temp == 0x2010:
            return "Gyro"
        elif module_type_temp == 0x2030:
            return "Button"
        elif module_type_temp == 0x2040:
            return "Dial"
        elif module_type_temp == 0x2070:
            return "Joystick"
        elif module_type_temp == 0x2080:
            return "ToF"
        elif module_type_temp == 0x4000:
            return "Display"
        elif module_type_temp == 0x4010:
            return "MotorA"
        elif module_type_temp == 0x4011:
            return "MotorB"
        elif module_type_temp == 0x4020:
            return "Led"
        elif module_type_temp == 0x4030:
            return "Speaker"
        return "none"

    def type_to_module_uuid(self, uuid):
        if uuid == "Network":
            return 0x0000
        elif uuid == "Battery":
            return 0x0010
        elif uuid ==  "Env":
            return 0x2000
        elif uuid == "Gyro":
            return 0x2010
        elif uuid == "Button":
            return 0x2030
        elif uuid == "Dial":
            return 0x2040
        elif uuid == "Joystick":
            return 0x2070
        elif uuid == "ToF":
            return 0x2080
        elif uuid == "Display":
            return 0x4000
        elif uuid == "MotorA":
            return 0x4010
        elif uuid == "MotorB":
            return 0x4011
        elif uuid == "Led":
            return 0x4020
        elif uuid == "Speaker":
            return 0x4030
        return 1

    def version_to_int(self, version):
        version_value = version[1:].split(".")
        return int(version_value[0]) << 13 | int(version_value[1]) << 8 | int(version_value[2])
    
    def parsing_data(self, data, ser):
        tmp = ''.join(data)
        
        jsonObject = json.loads(tmp)

        cmd = jsonObject.get("c")
        sid = jsonObject.get("s")
        did = jsonObject.get("d")
        length = jsonObject.get("l")
        data = b64decode(jsonObject.get("b"))

        # if cmd != 0 :
        # print(tmp)
        
        if cmd == 0x0:  # health
            if self.start_flag == False:
                send_data = int.to_bytes(0xFFF, byteorder="little", length=8)
                ser.write(parse_message(0x28, 0x0, sid, send_data).encode('utf-8'))
        elif cmd == 0x05: # assign id
            if self.start_flag == False:
                received_uuid = int.from_bytes(data, byteorder='little') & 0xFFFFFFFFFFFF
                print(self.uuid_to_module_type(received_uuid), " module detected, uuid = ", hex(received_uuid))
                if self.uuid_to_module_type(received_uuid) == "Network":
                    self.start_flag = True
                    self.target_uuid = received_uuid
                    self.target_id = self.target_uuid & 0xFFF
                    send_data = int.to_bytes(0, byteorder="little", length=1)
                    ser.write(parse_message(0x04, 30, self.target_id, send_data).encode('utf-8')) # Reset
                    self.signal_list[1].emit("module detected")
                    self.nvs_reset_timeout_thread.start()
        elif cmd == 0x0A:
            print("warning")
        elif cmd == 0xA1:
            if self.start_flag == True:
                if sid == 9: # esp32 version
                    self.signal_list[1].emit("reset complete\npress the button")
                    self.signal_list[2].emit()
                    self.start_flag = False
                    ser.close()
                    self.exitThread = True


    def readThread(self, ser):
        json_flag = False
        while not self.exitThread:
            for c in ser.read(1):
                if json_flag == False:
                    if chr(c) == '{':
                        self.line.append(chr(c))
                        json_flag = True
                else :
                    self.line.append(chr(c))
                    if chr(c) == '}':
                        try:
                            json_flag = False
                            self.parsing_data(self.line, ser)
                        except Exception as e:
                            del self.line[:]
                            error_string = repr(e)
                            print(error_string)
                            if "The version of this module" in error_string:
                                raise e 
                            elif "Streaming response error" in error_string:
                                raise e
                        del  self.line[:]

    def nvs_reset_timeout_thread_function(self, ser):
        timeout_count = 0
        while not self.exitThread:
            time.sleep(2)
            if timeout_count < 3 and self.start_flag == True:
                send_data = int.to_bytes(0, byteorder="little", length=1)
                ser.write(parse_message(0x04, 30, self.target_id, send_data).encode('utf-8')) # Reset
                timeout_count += 1
            else:
                if self.start_flag == False:
                    return
                print("Timeout error")
                self.signal_list[0].emit("Timeout error")
                self.signal_list[1].emit("Press the button")
                self.signal_list[2].emit()
                self.exitThread = True
                ser.close()

    def start_reset_thread(self):
        ports = sp.comports()
        ser = None

        for port in ports:
            if (port.vid == 0x2FDE and port.pid == 0x0003):
                self.serial_port = port.device

        if self.serial_port is None:
            if sys.platform.startswith("win"):
                from modi2_network_nvs_reset.util.modi_winusb.modi_winusb import list_modi_winusb_paths
                path_list = list_modi_winusb_paths()
                for index, value in enumerate(path_list):
                    self.serial_port = value
        else:
            self.type = self.SERIAL_MODE_COMPORT
            ser = serial.Serial(self.serial_port, self.baud, timeout=0)

        if self.serial_port is None:
            print("Please connect MODI+ Network Module")
            self.signal_list[0].emit("Please connect MODI+ Network Module")
            self.signal_list[1].emit("Press the button")
            self.signal_list[2].emit()
            return
        else:
            if sys.platform.startswith("win"):
                from modi2_network_nvs_reset.util.modi_winusb.modi_winusb import ModiWinUsbComPort, list_modi_winusb_paths
                if self.serial_port in list_modi_winusb_paths():
                    self.type = self.SERIAL_MODI_WINUSB
                    winusb = ModiWinUsbComPort(path = self.serial_port, baudrate=self.baud, timeout=0)
                    ser = winusb

        self.nvs_reset_timeout_thread = threading.Thread(target=self.nvs_reset_timeout_thread_function, args=(ser,), daemon=True)
        thread1 = threading.Thread(target=self.readThread, args=(ser,), daemon=True)
        thread1.start()

    def set_ui(self, ui):
        self.ui = ui
        print(type(ui))
    
    def set_ui(self, ui, signal_list):
        self.ui = ui
        self.signal_list = signal_list

    def set_print(self, print):
        self.print = print

    def set_raise_error(self, raise_error_message):
        self.raise_error_message = raise_error_message

    def request_network_id(self):
        self.__conn.send_nowait(
            parse_message(0x28, 0x0, 0xFFF, (0xFF, 0x0F))
        )

    def close(self):
        self.__running = False

    def add_to_waitlist(self, module_id: int, module_type: str) -> None:
        # Check if input module already exist in the list
        for curr_module_id, curr_module_type in self.modules_to_update:
            if module_id == curr_module_id:
                return

        # Check if module is already updated
        for curr_module_id, curr_module_type in self.modules_updated:
            if module_id == curr_module_id:
                return

        self.__print(
            f"Adding {module_type} ({module_id}) to waiting list..."
            f"{' ' * 60}"
        )

        # Add the module to the waiting list
        module_elem = module_id, module_type
        self.modules_to_update.append(module_elem)
        self.update_module_num += 1

    @staticmethod
    def __delay(span):
        init_time = time.perf_counter()
        while time.perf_counter() - init_time < span:
            pass
        return

    @staticmethod
    def __set_module_state(
        destination_id: int, module_state: int, pnp_state: int
    ) -> str:
        message = dict()

        message["c"] = 0x09
        message["s"] = 0
        message["d"] = destination_id

        state_bytes = bytearray(2)
        state_bytes[0] = module_state
        state_bytes[1] = pnp_state

        message["b"] = b64encode(bytes(state_bytes)).decode("utf-8")
        message["l"] = 2

        return json.dumps(message, separators=(",", ":"))

    def __print(self, data, end="\n"):
        if self.print:
            print(data, end)