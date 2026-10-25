# PyInstaller для macOS: dist/CC Lab CrazyFly.app - Python, библиотеки, шрифты CC Lab и libusb внутри.
# Собирать на Mac: build-mac.sh или GitHub Actions (.github/workflows/build.yml). Драйвер Crazyradio на Mac не нужен.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.dirname(SPECPATH)  # spec лежит в installer/
NAME = "CC Lab CrazyFly"

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "config.json"), "."),
        (os.path.join(ROOT, "cfpilot", "fonts"), os.path.join("cfpilot", "fonts")),
    ] + collect_data_files("libusb_package"),
    binaries=collect_dynamic_libs("libusb_package"),
    excludes=["pytest", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
app = BUNDLE(
    coll,
    name=f"{NAME}.app",
    icon=os.path.join(SPECPATH, "icon.ico"),  # PyInstaller сам переведёт в .icns (нужен Pillow)
    bundle_identifier="lab.cc.crazyfly",
    info_plist={
        "CFBundleDisplayName": NAME,
        "CFBundleShortVersionString": "1.1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
