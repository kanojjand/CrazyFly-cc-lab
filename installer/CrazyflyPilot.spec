# PyInstaller: всё в один файл dist/CrazyflyPilot.exe — Python, библиотеки, шрифты CC Lab и драйвер
# Crazyradio (его программа ставит сама, когда видит радио без драйвера). Запуск — из build.bat.
# Консоли нет: ошибки main.py показывает окном (report_fatal).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.dirname(SPECPATH)  # spec лежит в installer/

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    # config.json — настройки по умолчанию: при первом запуске копируются в %APPDATA%.
    # libusb_package cflib импортирует при загрузке, хотя на Windows радио работает через libusb0.
    datas=[
        (os.path.join(ROOT, "config.json"), "."),
        (os.path.join(ROOT, "cfpilot", "fonts"), os.path.join("cfpilot", "fonts")),
        (os.path.join(SPECPATH, "driver"), "driver"),
    ] + collect_data_files("libusb_package"),
    binaries=collect_dynamic_libs("libusb_package"),
    excludes=["pytest", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CC Lab CrazyFly",
    console=False,
    upx=False,  # сжатый UPX .exe антивирусы любят принимать за вирус
    icon=os.path.join(SPECPATH, "icon.ico"),
)
