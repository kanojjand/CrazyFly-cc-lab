"""Драйвер Crazyradio на Windows: вставлено ли радио, стоит ли на нём libusb-win32, установка драйвера.

cflib на Windows работает с радио только через libusb0 (libusb-win32). Драйвер — тот же пакет, что ставит
Zadig (installer/driver, выгружен pnputil /export-driver); программа ставит его сама, когда видит радио
без драйвера: Windows один раз спросит права администратора.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Callable

from cfpilot.i18n import t

log = logging.getLogger(__name__)

RADIO_HARDWARE_ID = "VID_1915&PID_7777"  # Crazyradio PA и Crazyradio 2.0
LIBUSB_SERVICE = "libusb0"
DRIVER_INF = "crazyradio_2.0.inf"
DRIVER_CERT = "crazyradio-driver.cer"
CERT_STORES = ("Root", "TrustedPublisher")  # так же делает Zadig: без этого Windows не примет подпись пакета
PNP_NO_DEVICES = 259  # pnputil: радио сейчас не вставлено — пакет всё равно добавлен в систему
PNP_REBOOT_REQUIRED = 3010
ERROR_CANCELLED = 1223  # пользователь ответил «Нет» на запрос прав администратора
INSTALL_CRASHED = -1
CREATE_NO_WINDOW = 0x08000000
# SetupAPI / Shell
DIGCF_PRESENT = 0x02
DIGCF_ALLCLASSES = 0x04
SPDRP_HARDWAREID = 0x01
SPDRP_SERVICE = 0x04
PROPERTY_CHARS = 1024
SEE_MASK_NOCLOSEPROCESS = 0x40
SW_HIDE = 0
INFINITE = 0xFFFFFFFF


class RadioUsb(Enum):
    ABSENT = "absent"  # радио не вставлено
    NO_DRIVER = "no_driver"  # вставлено, но без libusb-win32 — cflib его не увидит
    READY = "ready"
    UNKNOWN = "unknown"  # не Windows или SetupAPI недоступен — узнаем при подключении


def classify(services: list[str] | None) -> RadioUsb:
    """services — драйверы (службы) всех вставленных Crazyradio; "" — драйвера нет."""
    if services is None:
        return RadioUsb.UNKNOWN
    if not services:
        return RadioUsb.ABSENT
    if any(service.lower() == LIBUSB_SERVICE for service in services):
        return RadioUsb.READY
    return RadioUsb.NO_DRIVER


def radio_usb_state() -> RadioUsb:
    try:
        return classify(_radio_services())
    except Exception as error:  # это лишь подсказка: нет SetupAPI, сбой ctypes — узнаем при подключении
        log.warning("Не удалось опросить USB: %s", error)
        return RadioUsb.UNKNOWN


def _radio_services() -> list[str] | None:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class DeviceInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", ctypes.c_byte * 16),
                    ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

    api = ctypes.WinDLL("setupapi", use_last_error=True)
    api.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    api.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    api.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DeviceInfo)]
    api.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    api.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(DeviceInfo), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
    api.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    def strings(devices, info, prop) -> list[str]:
        buffer = ctypes.create_unicode_buffer(PROPERTY_CHARS)
        if not api.SetupDiGetDeviceRegistryPropertyW(devices, ctypes.byref(info), prop, None, buffer,
                                                     ctypes.sizeof(buffer), None):
            return []  # у устройства без драйвера свойства «служба» просто нет
        return [item for item in ctypes.wstring_at(buffer, PROPERTY_CHARS).split("\0") if item]

    devices = api.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if devices in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), "SetupDiGetClassDevsW")
    try:
        services, index = [], 0
        info = DeviceInfo(cbSize=ctypes.sizeof(DeviceInfo))
        while api.SetupDiEnumDeviceInfo(devices, index, ctypes.byref(info)):
            index += 1
            if any(RADIO_HARDWARE_ID in item.upper() for item in strings(devices, info, SPDRP_HARDWAREID)):
                service = strings(devices, info, SPDRP_SERVICE)
                services.append(service[0] if service else "")
        return services
    finally:
        api.SetupDiDestroyDeviceInfoList(devices)


# ---------- установка (в процессе с правами администратора) ----------

def _run_system_tool(exe: str, *args: str) -> int:
    tool = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / exe
    completed = subprocess.run([str(tool), *args], stdin=subprocess.DEVNULL, capture_output=True,
                               creationflags=CREATE_NO_WINDOW, check=False)
    if completed.returncode:
        log.warning("%s %s -> %s: %s", exe, " ".join(args), completed.returncode,
                    completed.stdout.decode(errors="replace").strip())
    return completed.returncode


def install_driver(folder: Path, run: Callable[..., int] = _run_system_tool) -> int:
    """Ставит драйвер, как Zadig: доверяет сертификату пакета и добавляет пакет в систему. 0 — получилось.

    Сертификат сгенерировал Zadig, закрытого ключа у него нет — подписать им что-то ещё нельзя.
    """
    cert = str(folder / DRIVER_CERT)
    for store in CERT_STORES:
        code = run("certutil.exe", "-f", "-addstore", store, cert)
        if code:
            return code
    code = run("pnputil.exe", "/add-driver", str(folder / DRIVER_INF), "/install")
    return 0 if code in (PNP_NO_DEVICES, PNP_REBOOT_REQUIRED) else code


def run_elevated(program: str, parameters: str) -> int:
    """Запускает программу с правами администратора (Windows спросит разрешения) и ждёт. Код выхода."""
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo), fMask=SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                            lpFile=program, lpParameters=parameters, nShow=SW_HIDE)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        return ctypes.get_last_error() or ERROR_CANCELLED
    if not info.hProcess:
        return INSTALL_CRASHED
    try:
        kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


class DriverJob:
    """Установка в фоне: окно живёт, пока Windows спрашивает права и pnputil работает."""

    def __init__(self, install: Callable[[], int]) -> None:
        self._install = install
        self._thread: threading.Thread | None = None
        self.result: int | None = None
        self.attempts = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.attempts += 1
        self.result = None
        self._thread = threading.Thread(target=self._run, name="cfpilot-driver", daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self.result = self._install()
        except Exception:  # иначе экран так и не узнал бы, что установка не удалась
            log.exception("Установка драйвера не запустилась")
            self.result = INSTALL_CRASHED

    def describe(self) -> str:
        if self.running:
            return t("Ставлю драйвер Crazyradio — подтверди запрос Windows «Разрешить этому приложению…»")
        if self.result is None:
            return ""
        if self.result == 0:
            return t("Драйвер установлен. Если радио не подхватилось за пару секунд — выдерни и вставь его")
        if self.result == ERROR_CANCELLED:
            return t("Установка драйвера отменена. D — попробовать снова")
        return t("Драйвер не установился (код {code}). D — ещё раз; не выходит — Zadig, см. README",
                 code=self.result)
