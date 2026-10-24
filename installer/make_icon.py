"""Рисует значок программы из логотипа CC Lab: installer/icon.ico для .exe.

ICO собирается вручную: внутри — PNG-картинки нескольких размеров (так умеют Windows Vista+).
Значок окна программа рисует сама тем же кодом (cfpilot.theme.icon_surface).
"""

from __future__ import annotations

import io
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # cfpilot — из исходников рядом

import pygame  # noqa: E402

from cfpilot.theme import icon_surface  # noqa: E402

OUT = Path(__file__).resolve().parent / "icon.ico"
CANVAS = 1024  # рисуем крупно и уменьшаем — так края получаются гладкими
SIZES = (256, 64, 48, 32, 16)


def png_bytes(surface: pygame.Surface) -> bytes:
    buffer = io.BytesIO()
    pygame.image.save(surface, buffer, "icon.png")
    return buffer.getvalue()


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in images:
        # Размер 256 в заголовке ICO записывается как 0.
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(d for _, d in images))


def main() -> None:
    pygame.init()
    picture = icon_surface(CANVAS)
    images = [(size, png_bytes(pygame.transform.smoothscale(picture, (size, size)))) for size in SIZES]
    write_ico(OUT, images)
    print(f"Значок: {OUT}")


if __name__ == "__main__":
    main()
