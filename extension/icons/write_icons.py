#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import struct
import zlib


ROOT = pathlib.Path(__file__).resolve().parent


def png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_icon(size: int) -> bytes:
    bg = (17, 19, 26, 255)
    panel = (83, 109, 255, 255)
    accent = (121, 212, 167, 255)
    white = (237, 242, 255, 255)
    pixels = bytearray()
    radius = max(2, size // 7)
    inset = max(1, size // 6)
    page_left = inset
    page_top = inset
    page_right = size - inset - 1
    page_bottom = size - inset - 1
    fold = max(2, size // 5)
    bubble_left = max(1, size // 4)
    bubble_right = size - bubble_left
    bubble_top = size // 2
    bubble_bottom = size - max(2, size // 5)

    for y in range(size):
      row = bytearray([0])
      for x in range(size):
          rgba = bg
          in_page = page_left <= x <= page_right and page_top <= y <= page_bottom
          if in_page:
              rgba = panel
              if x > page_right - fold and y < page_top + fold and (x - (page_right - fold)) + (y - page_top) < fold:
                  rgba = accent
          if bubble_left <= x <= bubble_right and bubble_top <= y <= bubble_bottom:
              rgba = white
              if y > bubble_bottom - radius and x < bubble_left + radius and (bubble_left + radius - x) + (y - (bubble_bottom - radius)) > radius:
                  rgba = panel
          if y == bubble_bottom + 1 and x == bubble_left + radius:
              rgba = white
          if y == bubble_bottom + 2 and bubble_left + radius - 1 <= x <= bubble_left + radius + 1:
              rgba = white
          row.extend(rgba)
      pixels.extend(row)
    header = png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    data = png_chunk(b"IDAT", zlib.compress(bytes(pixels), level=9))
    end = png_chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + header + data + end


def write_if_changed(path: pathlib.Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() == data:
        return
    path.write_bytes(data)


def main() -> None:
    for size in (16, 48, 128):
        write_if_changed(ROOT / f"icon{size}.png", make_icon(size))


if __name__ == "__main__":
    main()
