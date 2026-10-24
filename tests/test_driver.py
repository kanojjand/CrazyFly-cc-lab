import sys
from pathlib import Path

import pytest

from cfpilot import driver
from cfpilot.driver import (
    ERROR_CANCELLED,
    PNP_NO_DEVICES,
    DriverJob,
    RadioUsb,
    classify,
    install_driver,
    radio_usb_state,
)


@pytest.mark.parametrize(
    "services, state",
    [(None, RadioUsb.UNKNOWN), ([], RadioUsb.ABSENT), ([""], RadioUsb.NO_DRIVER),
     (["WinUSB"], RadioUsb.NO_DRIVER), (["libusb0"], RadioUsb.READY), (["", "LIBUSB0"], RadioUsb.READY)],
)
def test_radio_state_from_driver_services(services, state):
    assert classify(services) is state


def test_usb_query_failure_is_not_fatal(monkeypatch):
    def broken():
        raise OSError("setupapi.dll missing")

    monkeypatch.setattr(driver, "_radio_services", broken)
    assert radio_usb_state() is RadioUsb.UNKNOWN


@pytest.mark.skipif(sys.platform != "win32", reason="SetupAPI есть только в Windows")
def test_real_usb_query_runs():
    assert isinstance(radio_usb_state(), RadioUsb)


class Tools:
    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}

    def __call__(self, exe, *args):
        self.calls.append((exe,) + args)
        return self.codes.get(exe, 0)


def test_install_trusts_certificate_then_adds_driver():
    tools = Tools()
    assert install_driver(Path("pkg"), run=tools) == 0
    assert [call[0] for call in tools.calls] == ["certutil.exe", "certutil.exe", "pnputil.exe"]
    assert {call[3] for call in tools.calls[:2]} == {"Root", "TrustedPublisher"}
    assert tools.calls[2][1:] == ("/add-driver", str(Path("pkg") / "crazyradio_2.0.inf"), "/install")


def test_driver_added_without_radio_plugged_in_is_success():
    assert install_driver(Path("pkg"), run=Tools({"pnputil.exe": PNP_NO_DEVICES})) == 0


def test_certificate_failure_stops_install():
    tools = Tools({"certutil.exe": 5})
    assert install_driver(Path("pkg"), run=tools) == 5
    assert len(tools.calls) == 1


def test_driver_job_reports_progress():
    job = DriverJob(lambda: 0)
    assert job.describe() == ""
    job.start()
    job.wait(5.0)
    assert job.result == 0
    assert "установлен" in job.describe()


@pytest.mark.parametrize("code, text", [(ERROR_CANCELLED, "отменена"), (2, "код 2")])
def test_driver_job_explains_failures(code, text):
    job = DriverJob(lambda: code)
    job.start()
    job.wait(5.0)
    assert text in job.describe()
    assert job.attempts == 1


def test_driver_job_survives_launch_error():
    def broken():
        raise OSError("ShellExecuteExW")

    job = DriverJob(broken)
    job.start()
    job.wait(5.0)
    assert job.result == driver.INSTALL_CRASHED
