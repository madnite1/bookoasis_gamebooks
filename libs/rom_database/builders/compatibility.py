#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""공식 Libretro 호환성 표에서 MAME2003 계열 실행 호환성 DB를 생성한다."""

from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
import urllib.request


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PACKAGE_ROOT / "data" / "mame_compatibility.db"
SOURCES = {
    "mame2003": "https://buildbot.libretro.com/compatibility_lists/cores/mame2003/mame2003.html",
    "mame2003_plus": "https://buildbot.libretro.com/compatibility_lists/cores/mame2003-plus/mame2003-plus.html",
}


class CompatibilityTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_row = False
        self._in_cell = False
        self._cell = []
        self._row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_row = True
            self._row = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._in_cell:
            self._row.append(" ".join("".join(self._cell).split()))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False


def download_rows(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "rom-analyzer compatibility DB builder"})
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", "replace")
    parser = CompatibilityTableParser()
    parser.feed(html)
    rows = parser.rows
    if not rows or rows[0][:3] != ["Roms", "Description", "Driver status"]:
        raise RuntimeError(f"호환성 표 형식이 예상과 다릅니다: {url}")
    return rows[1:]


def normalize_row(core_id: str, row):
    padded = list(row[:8]) + [""] * max(0, 8 - len(row))
    rom_name, description, driver, color, sound, graphics, samples, bios = padded[:8]
    return (
        core_id,
        rom_name.lower().strip(),
        description.strip(),
        driver.lower().strip(),
        color.lower().strip(),
        sound.lower().strip(),
        graphics.lower().strip(),
        samples.strip(),
        1 if bios.lower().strip() == "yes" else 0,
    )


def build():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".db.tmp")
    if temp.exists():
        temp.unlink()

    with sqlite3.connect(temp) as con:
        con.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE compatibility (
                core_id TEXT NOT NULL,
                rom_name TEXT NOT NULL,
                description TEXT NOT NULL,
                driver_status TEXT NOT NULL,
                color_status TEXT NOT NULL,
                sound_status TEXT NOT NULL,
                graphics_status TEXT NOT NULL,
                samples TEXT NOT NULL,
                bios_required INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (core_id, rom_name)
            );
            CREATE INDEX idx_compatibility_rom_name ON compatibility(rom_name);
            """
        )
        total = 0
        for core_id, url in SOURCES.items():
            rows = [normalize_row(core_id, row) for row in download_rows(url) if row and row[0].strip()]
            con.executemany(
                """INSERT OR REPLACE INTO compatibility(
                       core_id, rom_name, description, driver_status,
                       color_status, sound_status, graphics_status, samples, bios_required
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            con.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (f"source.{core_id}", url))
            con.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (f"rows.{core_id}", str(len(rows))))
            total += len(rows)
        con.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            ("generated_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        con.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("row_count", str(total)))
        con.execute("ANALYZE")
        con.commit()

    temp.replace(OUTPUT)
    print(f"생성 완료: {OUTPUT} ({total} rows)")


if __name__ == "__main__":
    build()
