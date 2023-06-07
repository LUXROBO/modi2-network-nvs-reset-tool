import sys
import argparse

from PyQt5 import QtWidgets

from modi2_network_nvs_reset.gui_firmware_upload import Form


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode', type=str, default='installer',
        choices=['native','installer'],
        help='What mode should the application run on?'
    )
    args = parser.parse_args()
    mode = args.mode
    installer = mode == 'installer'
    print("Running MODI2 File Uploader")
    app = QtWidgets.QApplication(sys.argv)
    w = Form(installer=installer)
    sys.exit(app.exec())
    print("Terminating MODI2 File Uploader")
