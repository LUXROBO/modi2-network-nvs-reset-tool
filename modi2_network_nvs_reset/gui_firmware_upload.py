import logging
import os
import pathlib
import sys
import threading as th
import time
import traceback as tb
import io
import urllib.request as ur
import zipfile
import shutil
from io import open
from os import path
from urllib.error import URLError

from PyQt5 import QtCore, QtGui, QtWidgets, uic
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QDialog, QFileDialog

from modi2_network_nvs_reset.util.connection_util import list_modi_ports
# from modi2_network_nvs_reset.core.network_uploader import NetworkFirmwareMultiUpdater
from modi2_network_nvs_reset.core.network_reset import Network_reset_manager


class StdoutRedirect(QObject):
    printOccur = pyqtSignal(str, str, name="print")

    def __init__(self):
        QObject.__init__(self, None)
        self.daemon = True
        self.sysstdout = sys.stdout.write
        self.sysstderr = sys.stderr.write
        self.logger = None

    def stop(self):
        sys.stdout.write = self.sysstdout
        sys.stderr.write = self.sysstderr

    def start(self):
        sys.stdout.write = self.write
        sys.stderr.write = lambda msg: self.write(msg, color="red")

    def write(self, s, color="black"):
        sys.stdout.flush()
        self.printOccur.emit(s, color)
        if self.logger and not self.__is_redundant_line(s):
            self.logger.info(s)

    @staticmethod
    def __is_redundant_line(line):
        return (
            line.startswith("\rUpdating") or
            line.startswith("\rFirmware Upload: [") or
            len(line) < 3
        )


class PopupMessageBox(QtWidgets.QMessageBox):
    def __init__(self, main_window, level):
        QtWidgets.QMessageBox.__init__(self)
        self.window = main_window
        self.setSizeGripEnabled(True)
        self.setWindowTitle("System Message")

        def error_popup():
            self.setIcon(self.Icon.Warning)
            self.setText("ERROR")

        def warning_popup():
            self.setIcon(self.Icon.Information)
            self.setText("WARNING")
            self.addButton("Ok", self.ActionRole)
            # restart_btn.clicked.connect(self.restart_btn)

        func = {
            "error": error_popup,
            "warning": warning_popup,
        }.get(level)
        func()

        close_btn = self.addButton("Exit", self.ActionRole)
        close_btn.clicked.connect(self.close_btn)
        # report_btn = self.addButton('Report Error', self.ActionRole)
        # report_btn.clicked.connect(self.report_btn)
        self.show()

    def event(self, e):
        MAXSIZE = 16_777_215
        MINHEIGHT = 100
        MINWIDTH = 200
        MINWIDTH_CHANGE = 500
        result = QtWidgets.QMessageBox.event(self, e)

        self.setMinimumHeight(MINHEIGHT)
        self.setMaximumHeight(MAXSIZE)
        self.setMinimumWidth(MINWIDTH)
        self.setMaximumWidth(MAXSIZE)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

        textEdit = self.findChild(QtWidgets.QTextEdit)
        if textEdit is not None:
            textEdit.setMinimumHeight(MINHEIGHT)
            textEdit.setMaximumHeight(MAXSIZE)
            textEdit.setMinimumWidth(MINWIDTH_CHANGE)
            textEdit.setMaximumWidth(MAXSIZE)
            textEdit.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )

        return result

    def close_btn(self):
        self.window.close()

    def report_btn(self):
        pass

class ThreadSignal(QObject):
    thread_error = pyqtSignal(object)
    thread_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()


class Form(QDialog):
    """
    GUI Form of MODI Firmware Updater
    """
    current_module_changed_signal = pyqtSignal(int, str)
    progress_signal = pyqtSignal(int)
    button_enable_signal = pyqtSignal()
    error_message_signal = pyqtSignal(str)
    button_text_change_signal = pyqtSignal(str)

    def __init__(self, installer=False):
        QDialog.__init__(self)
        # self.logger = self.__init_logger()
        self.__excepthook = sys.excepthook
        sys.excepthook = self.__popup_excepthook
        th.excepthook = self.__popup_thread_excepthook
        self.err_list = list()
        self.is_popup = False

        # ui_path = os.path.join(os.path.dirname(__file__), "assets", "file-uploader.ui")
        ui_path = os.path.join(os.path.dirname(__file__), "assets", "network-reset.ui")

        if sys.platform.startswith("win"):
            self.component_path = pathlib.PurePosixPath(pathlib.PurePath(__file__), "..", "assets", "component")
        else:
            self.component_path = os.path.join(os.path.dirname(__file__), "assets", "component")

        self.test_signal_list = []

        self.error_message_signal.connect(self.set_error_message)
        self.button_text_change_signal.connect(self.set_process_state_text)
        self.button_enable_signal.connect(self.set_button_enable)

        self.test_signal_list.append(self.error_message_signal)
        self.test_signal_list.append(self.button_text_change_signal)
        self.test_signal_list.append(self.button_enable_signal)

        
        version_path = os.path.join(os.path.dirname(__file__), "..", "version.txt")
        with io.open(version_path, "r") as version_file:
            self.version_info = version_file.readline().rstrip("\n")

        self.ui = uic.loadUi(ui_path)
        self.ui.setWindowTitle("MODI+ Network reset - " + self.version_info)
        self.ui.setWindowIcon(QtGui.QIcon(os.path.join(self.component_path, "network_module.ico")))

        self.ui.setStyleSheet("background-color: white")
        # Set signal for thread communication
        self.stream = ThreadSignal()

        # Connect up the buttons
        self.ui.nvs_reset_start.clicked.connect(self.reset_network_module)

        self.buttons = [
            self.ui.nvs_reset_start,
        ]
        # Print init status
        time_now_str = time.strftime("[%Y/%m/%d@%X]", time.localtime())
        print(time_now_str + " GUI MODI Network reset has been started!")

        # Set up field variables
        self.firmware_updater = None
        self.button_in_english = False
        self.console = False

        self.specific_file_path = None

        self.ui.stream = self.stream
        self.ui.popup = self._thread_signal_hook

        self.ui.show()

    def set_error_message(self, error_message):
        QtWidgets.QMessageBox.warning(self, 'error', error_message)

    def set_process_state_text(self, button_text):
        self.ui.process_state.setText(button_text)

    def set_button_enable(self):
        self.ui.nvs_reset_start.setEnabled(True)


    #
    # Main methods
    #
    def reset_network_module(self):
        print("button clicked")
        self.ui.process_state.setText("processing")
        self.ui.nvs_reset_start.setEnabled(False)
        nvs_reset_manager = Network_reset_manager()
        nvs_reset_manager.set_ui(self.ui, self.test_signal_list)
        th.Thread(
            target=nvs_reset_manager.start_reset_thread,
            daemon=True,
        ).start()

    def specific_file_button_event(self):
        fname = QFileDialog.getOpenFileNames(self, 'Open files', './')
        print(fname)
        self.specific_file_path = (fname[0])
        # print(type(self.specific_file_path))
        # print(len(self.specific_file_path))
        self.ui.specific_file_name.setText(str(fname))

    def specific_file_check_box_event(self):
        if self.ui.specific_file_upload_check_box.isChecked():
            self.ui.specific_file_open_button.setEnabled(True)
        else :
            self.ui.specific_file_open_button.setEnabled(False)

    def check_module_firmware(self):
        if not os.path.exists(self.local_firmware_path):
            os.mkdir(self.local_firmware_path)

        self.__check_module_version()
        self.__check_network_base_version()
        self.__check_esp32_version()

    def __check_module_version(self):
        if not os.path.exists(self.local_module_firmware_path):
            assert_path = path.join(path.dirname(__file__), "assets", "firmware", "module")
            shutil.copytree(assert_path, self.local_module_firmware_path)

    def __check_network_base_version(self):
        if not os.path.exists(self.local_network_firmware_path):
            assert_path = path.join(path.dirname(__file__), "assets", "firmware", "module")
            shutil.copytree(assert_path, self.local_network_firmware_path)

    def __check_esp32_version(self):
        if not os.path.exists(self.local_esp32_firmware_path):
            assert_path = path.join(path.dirname(__file__), "assets", "firmware", "esp32")
            shutil.copytree(assert_path, self.local_esp32_firmware_path)
    #
    # Helper functions
    #
    @staticmethod
    def __init_logger():
        logger = logging.getLogger("GUI MODI Firmware Updater Logger")
        logger.setLevel(logging.DEBUG)

        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        file_handler = logging.FileHandler("gmfu.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        return logger

    def __popup_excepthook(self, exctype, value, traceback):
        self.__excepthook(exctype, value, traceback)
        if self.is_popup:
            return
        self.popup = PopupMessageBox(self.ui, level="error")
        self.popup.setInformativeText(str(value))
        self.popup.setDetailedText(str(tb.extract_tb(traceback)))
        self.is_popup = True

    def __popup_thread_excepthook(self, err_msg):
        if err_msg.exc_type in self.err_list:
            return
        self.err_list.append(err_msg.exc_type)
        self.stream.thread_error.connect(self.__thread_error_hook)
        self.stream.thread_error.emit(err_msg)

    @pyqtSlot(object)
    def __thread_error_hook(self, err_msg):
        self.__popup_excepthook(
            err_msg.exc_type, err_msg.exc_value, err_msg.exc_traceback
        )

    @pyqtSlot(object)
    def _thread_signal_hook(self):
        self.thread_popup = PopupMessageBox(self.ui, level="warning")
        if self.button_in_english:
            text = (
                "Reconnect network module and "
                "click the button again please."
            )
        else:
            text = "네트워크 모듈을 재연결 후 버튼을 다시 눌러주십시오."
        self.thread_popup.setInformativeText(text)
        self.is_popup = True

    def __click_motion(self, button_type, start_time):
        # Busy wait for 0.2 seconds
        while time.time() - start_time < 0.2:
            pass

        if button_type in [6, 7]:
            self.buttons[button_type].setStyleSheet(f"border-image: url({self.language_frame_path}); font-size: 13px")
        else:
            self.buttons[button_type].setStyleSheet(f"border-image: url({self.active_path}); font-size: 16px")
            for i, q_button in enumerate(self.buttons):
                if i in [button_type, 6, 7]:
                    continue
                q_button.setStyleSheet(f"border-image: url({self.inactive_path}); font-size: 16px")
                q_button.setEnabled(False)

    def __append_text_line(self, line):
        self.ui.console.moveCursor(
            QtGui.QTextCursor.End, QtGui.QTextCursor.MoveAnchor
        )
        self.ui.console.moveCursor(
            QtGui.QTextCursor.StartOfLine, QtGui.QTextCursor.MoveAnchor
        )
        self.ui.console.moveCursor(
            QtGui.QTextCursor.End, QtGui.QTextCursor.KeepAnchor
        )

        # Remove new line character if current line represents update_progress
        if self.__is_update_progress_line(line):
            self.ui.console.textCursor().removeSelectedText()
            self.ui.console.textCursor().deletePreviousChar()

        # Display user text input
        self.ui.console.moveCursor(QtGui.QTextCursor.End)
        self.ui.console.insertPlainText(line)
        # QtWidgets.QApplication.processEvents(
        #     QtCore.QEventLoop.ExcludeUserInputEvents
        # )

    @staticmethod
    def __is_update_progress_line(line):
        return line.startswith("\rUpdating") or line.startswith("\rFirmware Upload: [")