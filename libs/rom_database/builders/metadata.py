# -*- coding: utf-8 -*-
"""
ROM Metadata SQLite 데이터베이스 빌더 및 데이터 삽입 스크립트.
"""

import os
import json
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "rom_metadata.db")


def build_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 테이블 생성
    cur.execute("""
    CREATE TABLE arcade_romsets (
        rom_name TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        board TEXT NOT NULL,
        parent_rom TEXT,
        is_clone INTEGER DEFAULT 0,
        is_bios INTEGER DEFAULT 0,
        is_device INTEGER DEFAULT 0,
        required_bios TEXT DEFAULT '[]',
        needs_chd INTEGER DEFAULT 0,
        chd_name TEXT,
        year TEXT,
        manufacturer TEXT,
        recommended_cores TEXT DEFAULT '["fbneo", "mame"]'
    );
    """)

    cur.execute("""
    CREATE TABLE disc_serials (
        serial TEXT PRIMARY KEY,
        system_id TEXT NOT NULL,
        title TEXT NOT NULL,
        region TEXT,
        manufacturer TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE bios_manifest (
        system_id TEXT PRIMARY KEY,
        system_name TEXT NOT NULL,
        mandatory INTEGER DEFAULT 1,
        bios_files TEXT NOT NULL,
        description TEXT
    );
    """)

    # 인덱스 생성
    cur.execute("CREATE INDEX idx_arcade_board ON arcade_romsets(board);")
    cur.execute("CREATE INDEX idx_arcade_parent ON arcade_romsets(parent_rom);")
    cur.execute("CREATE INDEX idx_serial_sys ON disc_serials(system_id);")

    # =========================================================================
    # 1. 바이오스 매니페스트 삽입
    # =========================================================================
    bios_data = [
        ("neogeo", "SNK Neo-Geo MVS / AES", 1, ["neogeo.zip"], "네오지오 시스템 필수 바이오스 세트"),
        ("pgm", "IGS PolyGame Master (PGM)", 1, ["pgm.zip"], "IGS PGM(삼국전기, 데몬프론트 등) 필수 바이오스"),
        ("naomi", "Sega NAOMI", 1, ["naomi.zip", "naomigd.zip"], "세가 나오미 아케이드 기판 필수 바이오스"),
        ("naomi2", "Sega NAOMI 2", 1, ["naomi2.zip"], "세가 나오미 2 아케이드 기판 필수 바이오스"),
        ("stv", "Sega Titan Video (ST-V)", 1, ["stvbios.zip"], "세가 ST-V (새턴 기반 아케이드) 필수 바이오스"),
        ("atomiswave", "Sammy Atomiswave", 1, ["awbios.zip"], "새미 아토미스웨이브(KOF XI, 메탈슬러그 6 등) 필수 바이오스"),
        ("skns", "Kaneko Super Nova", 1, ["skns.zip"], "카네코 슈퍼노바 기판 (갈스패닉 등) 필수 바이오스"),
        ("decocass", "Data East DECO Cassette", 1, ["decocass.zip"], "데이터 이스트 카세트 시스템 바이오스"),
        ("konamigx", "Konami GX System", 1, ["konamigx.zip"], "코나미 GX 시스템 기판 바이오스"),
        ("sys246", "Namco System 246", 1, ["sys246.zip"], "남코 시스템 246 (PS2 기반 아케이드) 바이오스"),
        ("sys573", "Konami System 573", 1, ["sys573.zip"], "코나미 시스템 573 (DDR 등) 바이오스"),
        ("hikaru", "Sega Hikaru", 1, ["hikaru.zip"], "세가 히카루 기판 바이오스"),
        ("playch10", "Nintendo PlayChoice-10", 1, ["playch10.zip"], "닌텐도 플레이초이스-10 바이오스"),
        ("nss", "Nintendo Super System", 1, ["nss.zip"], "닌텐도 슈퍼 시스템 아케이드 바이오스"),
        ("cpzn1", "Capcom / Sony ZN-1", 1, ["cpzn1.zip"], "캡콤 ZN-1 3D 아케이드 기판 바이오스"),
        ("cpzn2", "Capcom / Sony ZN-2", 1, ["cpzn2.zip"], "캡콤 ZN-2 3D 아케이드 기판 바이오스"),
        ("gnet", "Taito G-Net", 1, ["gnet.zip"], "타이토 G-NET 시스템 바이오스"),
        ("psx", "Sony PlayStation (PS1)", 1, ["scph5501.bin", "scph5500.bin", "scph5502.bin", "scph1001.bin"], "PlayStation 정품 BIOS 파일"),
        ("ps2", "Sony PlayStation 2", 1, ["scph39001.bin", "scph70012.bin"], "PlayStation 2 정품 BIOS 파일"),
        ("saturn", "Sega Saturn", 1, ["sega_101.bin", "mpr-17933.bin"], "세가 새턴 구동용 BIOS (JP/US/EU)"),
        ("segacd", "Sega CD / Mega CD", 1, ["bios_CD_U.bin", "bios_CD_J.bin", "bios_CD_E.bin"], "메가 CD 바이오스"),
        ("dreamcast", "Sega Dreamcast", 1, ["dc_boot.bin", "dc_flash.bin"], "드림캐스트 부트 바이오스"),
        ("pcecd", "NEC PC Engine CD-ROM²", 1, ["syscard3.pce", "syscard2.pce"], "PC 엔진 CD-ROM² 시스템 카드 3.0"),
        ("pcfx", "NEC PC-FX", 1, ["pcfx.rom"], "NEC PC-FX 시스템 바이오스"),
        ("3do", "Panasonic 3DO Interactive", 1, ["panafz10.bin", "panafz1.bin"], "3DO 오리지널 바이오스"),
        ("neocd", "SNK Neo Geo CD", 1, ["neocd.bin", "neocd_f.rom"], "네오지오 CD 시스템 바이오스"),
        ("fds", "Nintendo FDS", 1, ["disksys.rom"], "패미컴 디스크 시스템 부트 롬"),
        ("gba", "Nintendo Game Boy Advance", 0, ["gba_bios.bin"], "GBA 공식 바이오스 (선택적)"),
        ("nds", "Nintendo DS", 0, ["bios7.bin", "bios9.bin", "firmware.bin"], "NDS 바이오스 및 펌웨어 (선택적)"),
    ]

    for item in bios_data:
        cur.execute(
            "INSERT INTO bios_manifest VALUES (?, ?, ?, ?, ?)",
            (item[0], item[1], item[2], json.dumps(item[3]), item[4])
        )

    # =========================================================================
    # 2. 아케이드 롬셋 및 바이오스/장치 롬셋 데이터 대량 삽입
    # =========================================================================
    arcade_records = [
        # BIOS Sets
        ("neogeo", "SNK Neo-Geo MVS / AES BIOS", "SNK Neo-Geo MVS", None, 0, 1, 0, "[]", 0, None, "1990", "SNK", '["fbneo", "mame"]'),
        ("pgm", "IGS PolyGame Master (PGM) BIOS", "IGS PolyGame Master (PGM)", None, 0, 1, 0, "[]", 0, None, "1997", "IGS", '["fbneo", "mame"]'),
        ("naomi", "Sega NAOMI BIOS", "Sega NAOMI", None, 0, 1, 0, "[]", 0, None, "1998", "Sega", '["flycast", "mame"]'),
        ("naomi2", "Sega NAOMI 2 BIOS", "Sega NAOMI 2", None, 0, 1, 0, "[]", 0, None, "2000", "Sega", '["flycast", "mame"]'),
        ("naomigd", "Sega NAOMI GD-ROM Firmware", "Sega NAOMI GD-ROM", None, 0, 1, 0, "[]", 0, None, "2000", "Sega", '["flycast", "mame"]'),
        ("stvbios", "Sega Titan Video (ST-V) BIOS", "Sega ST-V", None, 0, 1, 0, "[]", 0, None, "1995", "Sega", '["mame", "kronos"]'),
        ("awbios", "Sammy Atomiswave BIOS", "Sammy Atomiswave", None, 0, 1, 0, "[]", 0, None, "2002", "Sammy", '["flycast", "mame"]'),
        ("skns", "Kaneko Super Nova BIOS", "Kaneko Super Nova", None, 0, 1, 0, "[]", 0, None, "1996", "Kaneko", '["mame"]'),
        ("decocass", "DECO Cassette System BIOS", "Data East DECO Cassette", None, 0, 1, 0, "[]", 0, None, "1980", "Data East", '["mame"]'),
        ("konamigx", "Konami GX System BIOS", "Konami GX", None, 0, 1, 0, "[]", 0, None, "1994", "Konami", '["fbneo", "mame"]'),
        ("sys246", "Namco System 246 BIOS", "Namco System 246", None, 0, 1, 0, "[]", 0, None, "2000", "Namco", '["mame"]'),
        ("sys573", "Konami System 573 BIOS", "Konami System 573", None, 0, 1, 0, "[]", 0, None, "1997", "Konami", '["mame"]'),
        ("hikaru", "Sega Hikaru BIOS", "Sega Hikaru", None, 0, 1, 0, "[]", 0, None, "1999", "Sega", '["mame"]'),
        ("playch10", "Nintendo PlayChoice-10 BIOS", "Nintendo PlayChoice-10", None, 0, 1, 0, "[]", 0, None, "1986", "Nintendo", '["mame"]'),
        ("nss", "Nintendo Super System BIOS", "Nintendo Super System", None, 0, 1, 0, "[]", 0, None, "1991", "Nintendo", '["mame"]'),
        ("cpzn1", "Capcom Sony ZN-1 BIOS", "Capcom / Sony ZN-1", None, 0, 1, 0, "[]", 0, None, "1995", "Capcom / Sony", '["mame"]'),
        ("cpzn2", "Capcom Sony ZN-2 BIOS", "Capcom / Sony ZN-2", None, 0, 1, 0, "[]", 0, None, "1997", "Capcom / Sony", '["mame"]'),
        ("gnet", "Taito G-Net BIOS", "Taito G-Net", None, 0, 1, 0, "[]", 0, None, "1998", "Taito", '["mame"]'),

        # Device Sets
        ("qsound", "Capcom QSound Audio DSP", "Capcom CPS-1.5 / CPS-2", None, 0, 0, 1, "[]", 0, None, "1992", "Capcom", '["fbneo", "mame"]'),
        ("qsound_hle", "Capcom QSound HLE DSP", "Capcom CPS-2", None, 0, 0, 1, "[]", 0, None, "1993", "Capcom", '["fbneo", "mame"]'),
        ("namcoc70", "Namco C70 MCU Device", "Namco System 1", None, 0, 0, 1, "[]", 0, None, "1987", "Namco", '["fbneo", "mame"]'),
        ("namcoc75", "Namco C75 MCU Device", "Namco System 2", None, 0, 0, 1, "[]", 0, None, "1988", "Namco", '["fbneo", "mame"]'),
        ("midssio", "Midway Sound I/O", "Midway Wolfunit / Seattle", None, 0, 0, 1, "[]", 0, None, "1995", "Midway", '["mame"]'),
        ("dcs2_audio", "Midway DCS2 Sound System", "Midway Seattle", None, 0, 0, 1, "[]", 0, None, "1997", "Midway", '["mame"]'),
        ("ym2610", "Yamaha YM2610 ADPCM ROM", "SNK Neo-Geo", None, 0, 0, 1, "[]", 0, None, "1990", "Yamaha", '["fbneo", "mame"]'),

        # Capcom CPS-1
        ("sf2", "Street Fighter II: The World Warrior", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1991", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sf2ce", "Street Fighter II': Champion Edition", "Capcom CPS-1", "sf2", 1, 0, 0, "[]", 0, None, "1992", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sf2hf", "Street Fighter II': Hyper Fighting (Turbo)", "Capcom CPS-1", "sf2", 1, 0, 0, "[]", 0, None, "1992", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sf2rb", "Street Fighter II: Rainbow Edition", "Capcom CPS-1", "sf2", 1, 0, 0, "[]", 0, None, "1992", "Bootleg", '["fbneo", "mame2003_plus", "mame"]'),
        ("sf2accp2", "Street Fighter II': Champion Edition (Accelerator Pt.II)", "Capcom CPS-1", "sf2", 1, 0, 0, "[]", 0, None, "1992", "Bootleg", '["fbneo", "mame2003_plus", "mame"]'),
        ("ffight", "Final Fight", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ffightu", "Final Fight (USA)", "Capcom CPS-1", "ffight", 1, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ffightj", "Final Fight (Japan)", "Capcom CPS-1", "ffight", 1, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ghouls", "Ghouls'n Ghosts", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1988", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("daimakai", "Dai Makai-Mura (Japan)", "Capcom CPS-1", "ghouls", 1, 0, 0, "[]", 0, None, "1988", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("strider", "Strider", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("striderj", "Strider Hiryu (Japan)", "Capcom CPS-1", "strider", 1, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("mercs", "Mercs", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1990", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("senjo", "Senjo no Ookami II (Japan)", "Capcom CPS-1", "mercs", 1, 0, 0, "[]", 0, None, "1990", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("unsquad", "U.N. Squadron", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("area88", "Area 88 (Japan)", "Capcom CPS-1", "unsquad", 1, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("knights", "Knights of the Round", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1991", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("knightsj", "Knights of the Round (Japan)", "Capcom CPS-1", "knights", 1, 0, 0, "[]", 0, None, "1991", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("cawing", "Carrier Air Wing", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1990", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("usnavy", "U.S. Navy (Japan)", "Capcom CPS-1", "cawing", 1, 0, 0, "[]", 0, None, "1990", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("captcomm", "Captain Commando", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1991", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("captcommj", "Captain Commando (Japan)", "Capcom CPS-1", "captcomm", 1, 0, 0, "[]", 0, None, "1991", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("varth", "Varth: Operation Sunrise", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1992", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("willow", "Willow", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1989", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("1941", "1941: Counter Attack", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1990", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("megaman", "Mega Man: The Power Battle", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("rockman", "Rockman: The Power Battle (Japan)", "Capcom CPS-1", "megaman", 1, 0, 0, "[]", 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("forgottn", "Forgotten Worlds", "Capcom CPS-1", None, 0, 0, 0, "[]", 0, None, "1988", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("lostwrld", "Lost Worlds (Japan)", "Capcom CPS-1", "forgottn", 1, 0, 0, "[]", 0, None, "1988", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),

        # Capcom CPS-1.5 (QSound)
        ("wof", "Warriors of Fate (Tenchi o Kurau II)", "Capcom CPS-1.5 (QSound)", None, 0, 0, 0, '["qsound.zip"]', 0, None, "1992", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("wofj", "Tenchi o Kurau II: Sekiheki no Tatakai (Japan)", "Capcom CPS-1.5 (QSound)", "wof", 1, 0, 0, '["qsound.zip"]', 0, None, "1992", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("punisher", "The Punisher", "Capcom CPS-1.5 (QSound)", None, 0, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("punisherj", "The Punisher (Japan)", "Capcom CPS-1.5 (QSound)", "punisher", 1, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("cadillacs", "Cadillacs and Dinosaurs", "Capcom CPS-1.5 (QSound)", None, 0, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("dino", "Cadillacs and Dinosaurs (World)", "Capcom CPS-1.5 (QSound)", "cadillacs", 1, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("dinoj", "Cadillacs: Kyouryuu Shinseiki (Japan)", "Capcom CPS-1.5 (QSound)", "cadillacs", 1, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("slammast", "Saturday Night Slam Masters", "Capcom CPS-1.5 (QSound)", None, 0, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("mbomber", "Muscle Bomber: Duo", "Capcom CPS-1.5 (QSound)", "slammast", 1, 0, 0, '["qsound.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),

        # Capcom CPS-2
        ("ssf2", "Super Street Fighter II: The New Challengers", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ssf2t", "Super Street Fighter II Turbo", "Capcom CPS-2", "ssf2", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ssf2xj", "Super Street Fighter II X (Japan)", "Capcom CPS-2", "ssf2", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfa", "Street Fighter Alpha: Warriors' Dreams", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfz", "Street Fighter Zero (Japan)", "Capcom CPS-2", "sfa", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfa2", "Street Fighter Alpha 2", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfz2", "Street Fighter Zero 2 (Japan)", "Capcom CPS-2", "sfa2", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfz2al", "Street Fighter Zero 2 Alpha", "Capcom CPS-2", "sfa2", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfa3", "Street Fighter Alpha 3", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1998", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sfz3", "Street Fighter Zero 3 (Japan)", "Capcom CPS-2", "sfa3", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1998", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("dstlk", "Darkstalkers: The Night Warriors", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("vamp", "Vampire: The Night Warriors (Japan)", "Capcom CPS-2", "dstlk", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("nwarr", "Night Warriors: Darkstalkers' Revenge", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("vhunt2", "Vampire Hunter 2 (Japan)", "Capcom CPS-2", "nwarr", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1997", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("vsav", "Vampire Savior: The Lord of Vampire", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1997", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("vsav2", "Vampire Savior 2 (Japan)", "Capcom CPS-2", "vsav", 1, 0, 0, '["qsound_hle.zip"]', 0, None, "1997", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("xmcota", "X-Men: Children of the Atom", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("msh", "Marvel Super Heroes", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("xmvsf", "X-Men Vs. Street Fighter", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("mshvsf", "Marvel Super Heroes Vs. Street Fighter", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1997", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("mvsc", "Marvel Vs. Capcom: Clash of Super Heroes", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1998", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ddtod", "Dungeons & Dragons: Tower of Doom", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ddsom", "Dungeons & Dragons: Shadow over Mystara", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("avsp", "Alien vs. Predator", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("armwar", "Armored Warriors / Powered Gear", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1994", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("cybots", "Cyberbots: Fullmetal Madness", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("progear", "Progear / Progear no Arashi", "Capcom CPS-2 (Cave)", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "2001", "Capcom / Cave", '["fbneo", "mame2003_plus", "mame"]'),
        ("19xx", "19XX: The War Against Destiny", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1995", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("1944", "1944: The Loop Master", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "2000", "Capcom / Raizing", '["fbneo", "mame2003_plus", "mame"]'),
        ("batcir", "Battle Circuit", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1997", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("ecofghtr", "Eco Fighters", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1993", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("megaman2", "Mega Man 2: The Power Fighters", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("gigawing", "Giga Wing", "Capcom CPS-2 (Takumi)", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1999", "Capcom / Takumi", '["fbneo", "mame2003_plus", "mame"]'),
        ("marsmx", "Mars Matrix: Hyper Solid Shooting", "Capcom CPS-2 (Takumi)", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "2000", "Capcom / Takumi", '["fbneo", "mame2003_plus", "mame"]'),
        ("dimahoo", "Dimahoo / Great Mahou Daisakusen", "Capcom CPS-2 (Raizing)", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "2000", "Capcom / Raizing", '["fbneo", "mame2003_plus", "mame"]'),
        ("spf2t", "Super Puzzle Fighter II Turbo", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1996", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("sgemf", "Super Gem Fighter Mini Mix (Pocket Fighter)", "Capcom CPS-2", None, 0, 0, 0, '["qsound_hle.zip"]', 0, None, "1997", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),

        # Capcom CPS-3
        ("sfiii", "Street Fighter III: New Generation", "Capcom CPS-3", None, 0, 0, 0, "[]", 1, "sfiii.chd", "1997", "Capcom", '["fbneo", "mame"]'),
        ("sfiii2", "Street Fighter III 2nd Impact: Giant Attack", "Capcom CPS-3", None, 0, 0, 0, "[]", 1, "sfiii2.chd", "1997", "Capcom", '["fbneo", "mame"]'),
        ("sfiii3", "Street Fighter III 3rd Strike: Fight for the Future", "Capcom CPS-3", None, 0, 0, 0, "[]", 1, "sfiii3.chd", "1999", "Capcom", '["fbneo", "mame"]'),
        ("sfiii3nr", "Street Fighter III 3rd Strike (No CD)", "Capcom CPS-3", "sfiii3", 1, 0, 0, "[]", 0, None, "1999", "Capcom / Hack", '["fbneo", "mame"]'),
        ("jojoba", "JoJo's Bizarre Adventure: Heritage for the Future", "Capcom CPS-3", None, 0, 0, 0, "[]", 1, "jojoba.chd", "1999", "Capcom", '["fbneo", "mame"]'),
        ("jojo", "JoJo's Venture", "Capcom CPS-3", None, 0, 0, 0, "[]", 1, "jojo.chd", "1998", "Capcom", '["fbneo", "mame"]'),
        ("redearth", "Red Earth / Warzard", "Capcom CPS-3", None, 0, 0, 0, "[]", 1, "redearth.chd", "1996", "Capcom", '["fbneo", "mame"]'),
        ("warzard", "Warzard (Japan)", "Capcom CPS-3", "redearth", 1, 0, 0, "[]", 1, "warzard.chd", "1996", "Capcom", '["fbneo", "mame"]'),

        # SNK Neo-Geo MVS
        ("kof94", "The King of Fighters '94", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1994", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof95", "The King of Fighters '95", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof96", "The King of Fighters '96", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof97", "The King of Fighters '97", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1997", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof98", "The King of Fighters '98 - The Slugfest", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1998", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof99", "The King of Fighters '99 - Millennium Battle", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1999", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof2000", "The King of Fighters 2000", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2000", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof2001", "The King of Fighters 2001", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2001", "Eolith / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof2002", "The King of Fighters 2002 - Challenge to Ultimate Battle", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2002", "Eolith / Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("kof2003", "The King of Fighters 2003", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2003", "SNK Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("mslug", "Metal Slug - Super Vehicle-001", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "Nazca / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("mslug2", "Metal Slug 2 - Super Vehicle-001/II", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1998", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("mslugx", "Metal Slug X - Super Vehicle-001", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1999", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("mslug3", "Metal Slug 3", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2000", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("mslug4", "Metal Slug 4", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2002", "Mega Enterprise / Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("mslug5", "Metal Slug 5", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2003", "SNK Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("samsho", "Samurai Shodown / Samurai Spirits", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1993", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("samsho2", "Samurai Shodown II", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1994", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("samsho3", "Samurai Shodown III", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("samsho4", "Samurai Shodown IV - Amakusa's Revenge", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("samsho5", "Samurai Shodown V / Samurai Spirits Zero", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2003", "Yuki Enterprise / SNK Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("samsh5sp", "Samurai Shodown V Special", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2004", "Yuki Enterprise / SNK Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("fatfur1", "Fatal Fury: King of Fighters", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1991", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("fatfur2", "Fatal Fury 2", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1992", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("fatfury3", "Fatal Fury 3: Road to the Final Victory", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("fatfuryu", "Fatal Fury Special", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1993", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("rbff1", "Real Bout Fatal Fury", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("rbffspec", "Real Bout Fatal Fury Special", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1997", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("rbff2", "Real Bout Fatal Fury 2 - The Newcomers", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1998", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("garou", "Garou: Mark of the Wolves", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1999", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("aof", "Art of Fighting", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1992", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("aof2", "Art of Fighting 2", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1994", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("aof3", "Art of Fighting 3: The Path of the Warrior", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("lastblad", "The Last Blade / Bakumatsu Roman", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1997", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("lastbld2", "The Last Blade 2", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1998", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("wh1", "World Heroes", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1992", "Alpha Denshi / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("wh2", "World Heroes 2", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1993", "ADK / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("wh2j", "World Heroes 2 Jet", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1994", "ADK / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("whp", "World Heroes Perfect", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "ADK / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("sengoku", "Sengoku / Sengoku Denshou", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1991", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("sengoku2", "Sengoku 2", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1993", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("sengoku3", "Sengoku 3 / Sengoku Denshou 2001", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2001", "Noise Factory / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("shocktro", "Shock Troopers", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1997", "Saurus / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("shocktr2", "Shock Troopers: 2nd Squad", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1998", "Saurus / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("spinmast", "Spin Master / Miracle Adventure", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1993", "Data East", '["fbneo", "mame2003_plus", "mame"]'),
        ("pbobblen", "Puzzle Bobble / Bust-A-Move (Neo Geo)", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1994", "Taito", '["fbneo", "mame2003_plus", "mame"]'),
        ("pbobbl2n", "Puzzle Bobble 2 (Neo Geo)", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1999", "Taito / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("magdrop2", "Magical Drop II", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "Data East", '["fbneo", "mame2003_plus", "mame"]'),
        ("magdrop3", "Magical Drop III", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1997", "Data East", '["fbneo", "mame2003_plus", "mame"]'),
        ("twinklstar", "Twinkle Star Sprites", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "ADK / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("neobombe", "Neo Bomberman", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1997", "Hudson Soft / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("blazstar", "Blazing Star", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1998", "Yumekobo / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("pulstar", "Pulstar", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "Aicom / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("stakwin", "Stakes Winner", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1995", "Saurus / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("stakwin2", "Stakes Winner 2", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1996", "Saurus / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("superspy", "The Super Spy", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1990", "SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("viewpoin", "Viewpoint", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1992", "Sammy / SNK", '["fbneo", "mame2003_plus", "mame"]'),
        ("wjammers", "Windjammers / Flying Power Disc", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "1994", "Data East", '["fbneo", "mame2003_plus", "mame"]'),
        ("matrim", "Matrimelee / Shin Gouketsuji Ichizoku", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2002", "Noise Factory / Atlus / Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("rotd", "Rage of the Dragons", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2002", "Evoga / Playmore", '["fbneo", "mame2003_plus", "mame"]'),
        ("svc", "SNK vs. Capcom: SVC Chaos", "SNK Neo-Geo MVS", None, 0, 0, 0, '["neogeo.zip"]', 0, None, "2003", "Playmore / Capcom", '["fbneo", "mame2003_plus", "mame"]'),

        # IGS PolyGame Master (PGM)
        ("kov", "Knights of Valour / Sangoku Senki", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "1999", "IGS", '["fbneo", "mame"]'),
        ("kovplus", "Knights of Valour Plus", "IGS PolyGame Master (PGM)", "kov", 1, 0, 0, '["pgm.zip"]', 0, None, "1999", "IGS", '["fbneo", "mame"]'),
        ("kov2", "Knights of Valour 2", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "2000", "IGS", '["fbneo", "mame"]'),
        ("kov2p", "Knights of Valour 2 Plus - Nine Dragons", "IGS PolyGame Master (PGM)", "kov2", 1, 0, 0, '["pgm.zip"]', 0, None, "2001", "IGS", '["fbneo", "mame"]'),
        ("orlegend", "Oriental Legend / Xi You Shi E Zhuan", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "1997", "IGS", '["fbneo", "mame"]'),
        ("martmast", "Martial Masters", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "1999", "IGS", '["fbneo", "mame"]'),
        ("dfront", "Demon Front", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "2002", "IGS", '["fbneo", "mame"]'),
        ("theglad", "The Gladiator / Shen Jian", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "2003", "IGS", '["fbneo", "mame"]'),
        ("drgpunch", "Dragon World / Sai You Gou Ma Roku", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "1997", "IGS", '["fbneo", "mame"]'),
        ("ddp2", "DoDonPachi II - Bee Storm (PGM)", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "2001", "IGS / Cave", '["fbneo", "mame"]'),
        ("killbld", "The Killing Blade", "IGS PolyGame Master (PGM)", None, 0, 0, 0, '["pgm.zip"]', 0, None, "1998", "IGS", '["fbneo", "mame"]'),

        # Sega NAOMI / NAOMI 2
        ("mvc2", "Marvel Vs. Capcom 2: New Age of Heroes", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 0, None, "2000", "Capcom / Sega", '["flycast", "mame"]'),
        ("ggx", "Guilty Gear X", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 0, None, "2000", "Arc System Works / Sammy", '["flycast", "mame"]'),
        ("ggxx", "Guilty Gear XX", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 0, None, "2002", "Arc System Works / Sammy", '["flycast", "mame"]'),
        ("ggxxac", "Guilty Gear XX Accent Core", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 0, None, "2006", "Arc System Works / Sega", '["flycast", "mame"]'),
        ("cvs", "Capcom vs SNK Millennium Fight 2000", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 0, None, "2000", "Capcom / Sega", '["flycast", "mame"]'),
        ("cvs2", "Capcom vs SNK 2: Mark of the Millennium 2001", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 1, "cvs2.chd", "2001", "Capcom / Sega", '["flycast", "mame"]'),
        ("ikaruga", "Ikaruga", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 1, "ikaruga.chd", "2001", "Treasure / Sega", '["flycast", "mame"]'),
        ("slashout", "Slashout", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 1, "slashout.chd", "2000", "Sega", '["flycast", "mame"]'),
        ("spkrthm", "Spikers Battle", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 1, "spkrthm.chd", "2001", "Sega", '["flycast", "mame"]'),
        ("vtennis2", "Virtua Tennis 2 / Power Smash 2", "Sega NAOMI", None, 0, 0, 0, '["naomi.zip"]', 0, None, "2001", "Hitmaker / Sega", '["flycast", "mame"]'),
        ("vf4", "Virtua Fighter 4", "Sega NAOMI 2", None, 0, 0, 0, '["naomi2.zip"]', 1, "vf4.chd", "2001", "Sega AM2", '["flycast", "mame"]'),
        ("vf4evo", "Virtua Fighter 4 Evolution", "Sega NAOMI 2", "vf4", 1, 0, 0, '["naomi2.zip"]', 1, "vf4evo.chd", "2002", "Sega AM2", '["flycast", "mame"]'),

        # Sega ST-V
        ("astrass", "Astra SuperStars", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1998", "Sunsoft / SantaClaus", '["mame", "kronos"]'),
        ("bakubaku", "Baku Baku Animal", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1995", "Sega", '["mame", "kronos"]'),
        ("diehard", "Die Hard Arcade / Dynamite Deka", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1996", "Sega", '["mame", "kronos"]'),
        ("decathlt", "Decathlete", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1996", "Sega", '["mame", "kronos"]'),
        ("ffreveng", "Final Fight Revenge", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1999", "Capcom", '["mame", "kronos"]'),
        ("cottonbm", "Cotton Boomerang", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1998", "Success", '["mame", "kronos"]'),
        ("cotton2", "Cotton 2", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1997", "Success", '["mame", "kronos"]'),
        ("groovef", "Groove On Fight - Gouketsuji Ichizoku 3", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1997", "Atlus", '["mame", "kronos"]'),
        ("rsgun", "Radiant Silvergun", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1998", "Treasure", '["mame", "kronos"]'),
        ("shienryu", "Shienryu", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1997", "Warashi", '["mame", "kronos"]'),
        ("suikoen", "Suiko Enbu / Outlaws of the Lost Dynasty", "Sega ST-V", None, 0, 0, 0, '["stvbios.zip"]', 0, None, "1995", "Data East", '["mame", "kronos"]'),
        ("dnmt", "Dynamite Deka", "Sega ST-V", "diehard", 1, 0, 0, '["stvbios.zip"]', 0, None, "1996", "Sega", '["mame", "kronos"]'),

        # Sammy Atomiswave
        ("kofxi", "The King of Fighters XI", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2005", "SNK Playmore / Sammy", '["flycast", "mame"]'),
        ("mslug6", "Metal Slug 6", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2006", "SNK Playmore / Sega", '["flycast", "mame"]'),
        ("ngbc", "NeoGeo Battle Coliseum", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2005", "SNK Playmore / Sammy", '["flycast", "mame"]'),
        ("fotns", "Fist of the North Star / Hokuto no Ken", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2005", "Arc System Works / Sega", '["flycast", "mame"]'),
        ("dolphin", "Dolphin Blue", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2003", "Sammy", '["flycast", "mame"]'),
        ("rumblef", "The Rumble Fish", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2004", "Dimps / Sammy", '["flycast", "mame"]'),
        ("rumblef2", "The Rumble Fish 2", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2005", "Dimps / Sammy", '["flycast", "mame"]'),
        ("demoderm", "Demolish Fist", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2003", "Dimps / Sammy", '["flycast", "mame"]'),
        ("kov7spir", "Knights of Valour: The Seven Spirits", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2003", "IGS / Sammy", '["flycast", "mame"]'),
        ("samsptk", "Samurai Shodown VI / Tenkaichi Kenkakuden", "Sammy Atomiswave", None, 0, 0, 0, '["awbios.zip"]', 0, None, "2005", "SNK Playmore / Sega", '["flycast", "mame"]'),

        # Namco System 11 / 12 / NB-1
        ("tekken", "Tekken", "Namco System 11", None, 0, 0, 0, "[]", 0, None, "1994", "Namco", '["mame"]'),
        ("tekken2", "Tekken 2", "Namco System 11", None, 0, 0, 0, "[]", 0, None, "1995", "Namco", '["mame"]'),
        ("tekken3", "Tekken 3", "Namco System 12", None, 0, 0, 0, "[]", 0, None, "1997", "Namco", '["mame"]'),
        ("tekkent", "Tekken Tag Tournament", "Namco System 12", None, 0, 0, 0, "[]", 0, None, "1999", "Namco", '["mame"]'),
        ("soulclbr", "Soulcalibur", "Namco System 12", None, 0, 0, 0, "[]", 0, None, "1998", "Namco", '["mame"]'),
        ("souledge", "Soul Edge", "Namco System 11", None, 0, 0, 0, "[]", 0, None, "1995", "Namco", '["mame"]'),
        ("timecris", "Time Crisis", "Namco Super System 22", None, 0, 0, 0, "[]", 0, None, "1995", "Namco", '["mame"]'),
        ("pointb", "Point Blank / Gun Bullet", "Namco System NB-1", None, 0, 0, 0, "[]", 0, None, "1994", "Namco", '["mame"]'),
        ("pointb2", "Point Blank 2 / Gunbarl", "Namco System NB-1", None, 0, 0, 0, "[]", 0, None, "1999", "Namco", '["mame"]'),
        ("splatter", "Splatterhouse", "Namco System 1", None, 0, 0, 0, '["namcoc70.zip"]', 0, None, "1988", "Namco", '["fbneo", "mame"]'),

        # Cave 68000 / CV1000
        ("donpachi", "DonPachi", "Cave 68000 (Atlus)", None, 0, 0, 0, "[]", 0, None, "1995", "Cave / Atlus", '["fbneo", "mame"]'),
        ("ddonpach", "DoDonPachi", "Cave 68000 (Atlus)", None, 0, 0, 0, "[]", 0, None, "1997", "Cave / Atlus", '["fbneo", "mame"]'),
        ("esprade", "ESP Ra.De.", "Cave 68000", None, 0, 0, 0, "[]", 0, None, "1998", "Cave / Atlus", '["fbneo", "mame"]'),
        ("guwange", "Guwange", "Cave 68000 (Atlus)", None, 0, 0, 0, "[]", 0, None, "1999", "Cave / Atlus", '["fbneo", "mame"]'),
        ("feversos", "Dangun Feveron / Fever SOS", "Cave 68000", None, 0, 0, 0, "[]", 0, None, "1998", "Cave / Nihon System", '["fbneo", "mame"]'),
        ("mushisam", "Mushihimesama", "Cave CV1000", None, 0, 0, 0, "[]", 0, None, "2004", "Cave / AMI", '["fbneo", "mame"]'),
        ("futari", "Mushihimesama Futari", "Cave CV1000", None, 0, 0, 0, "[]", 0, None, "2006", "Cave / AMI", '["fbneo", "mame"]'),
        ("espgal", "Espgaluda", "Cave CV1000", None, 0, 0, 0, "[]", 0, None, "2003", "Cave / AMI", '["fbneo", "mame"]'),
        ("espgal2", "Espgaluda II", "Cave CV1000", None, 0, 0, 0, "[]", 0, None, "2005", "Cave / AMI", '["fbneo", "mame"]'),
        ("deathsm", "Deathsmiles", "Cave CV1000", None, 0, 0, 0, "[]", 0, None, "2007", "Cave / AMI", '["fbneo", "mame"]'),
        ("ibara", "Ibara", "Cave CV1000", None, 0, 0, 0, "[]", 0, None, "2005", "Cave / AMI", '["fbneo", "mame"]'),

        # Konami GX / Classic
        ("tmnt", "Teenage Mutant Ninja Turtles", "Konami 68000 (TMNT)", None, 0, 0, 0, "[]", 0, None, "1989", "Konami", '["fbneo", "mame2003_plus", "mame"]'),
        ("tmnt2", "Teenage Mutant Ninja Turtles: Turtles in Time", "Konami 68000", None, 0, 0, 0, "[]", 0, None, "1991", "Konami", '["fbneo", "mame2003_plus", "mame"]'),
        ("simpsons", "The Simpsons", "Konami 6809", None, 0, 0, 0, "[]", 0, None, "1991", "Konami", '["fbneo", "mame2003_plus", "mame"]'),
        ("xmen", "X-Men", "Konami 68000", None, 0, 0, 0, "[]", 0, None, "1992", "Konami", '["fbneo", "mame2003_plus", "mame"]'),
        ("sunset", "Sunset Riders", "Konami 68000", None, 0, 0, 0, "[]", 0, None, "1991", "Konami", '["fbneo", "mame2003_plus", "mame"]'),
        ("mystwarr", "Mystic Warriors", "Konami Mystic Warriors Hardware", None, 0, 0, 0, "[]", 0, None, "1993", "Konami", '["fbneo", "mame"]'),
        ("gokupar", "Gokujou Parodius", "Konami GX System", None, 0, 0, 0, '["konamigx.zip"]', 0, None, "1994", "Konami", '["fbneo", "mame"]'),
        ("salmndr2", "Salamander 2", "Konami GX System", None, 0, 0, 0, '["konamigx.zip"]', 0, None, "1996", "Konami", '["fbneo", "mame"]'),
        ("sexyparo", "Sexy Parodius", "Konami GX System", None, 0, 0, 0, '["konamigx.zip"]', 0, None, "1996", "Konami", '["fbneo", "mame"]'),

        # Midway
        ("mk", "Mortal Kombat", "Midway Y-Unit", None, 0, 0, 0, "[]", 0, None, "1992", "Midway", '["mame2003_plus", "mame"]'),
        ("mk2", "Mortal Kombat II", "Midway T-Unit", None, 0, 0, 0, "[]", 0, None, "1993", "Midway", '["mame2003_plus", "mame"]'),
        ("mk3", "Mortal Kombat 3", "Midway Wolfunit", None, 0, 0, 0, "[]", 0, None, "1995", "Midway", '["mame2003_plus", "mame"]'),
        ("umk3", "Ultimate Mortal Kombat 3", "Midway Wolfunit", "mk3", 1, 0, 0, "[]", 0, None, "1995", "Midway", '["mame2003_plus", "mame"]'),
        ("nbajam", "NBA Jam", "Midway T-Unit", None, 0, 0, 0, "[]", 0, None, "1993", "Midway", '["mame2003_plus", "mame"]'),
        ("nbahangt", "NBA Hangtime", "Midway Wolfunit", None, 0, 0, 0, "[]", 0, None, "1996", "Midway", '["mame2003_plus", "mame"]'),
        ("wwfmania", "WWF: Wrestlemania", "Midway Wolfunit", None, 0, 0, 0, "[]", 0, None, "1995", "Midway", '["mame2003_plus", "mame"]'),
        ("rampage", "Rampage", "Bally Midway MCR 3", None, 0, 0, 0, "[]", 0, None, "1986", "Bally Midway", '["mame2003_plus", "mame"]'),

        # Psikyo / Video System
        ("s1945", "Strikers 1945", "Psikyo 68EC020", None, 0, 0, 0, "[]", 0, None, "1995", "Psikyo", '["fbneo", "mame"]'),
        ("s1945ii", "Strikers 1945 II", "Psikyo SH-2", None, 0, 0, 0, "[]", 0, None, "1997", "Psikyo", '["fbneo", "mame"]'),
        ("s1945iii", "Strikers 1945 III / Strikers 1999", "Psikyo SH-2", None, 0, 0, 0, "[]", 0, None, "1999", "Psikyo", '["fbneo", "mame"]'),
        ("gunbird", "Gunbird", "Psikyo 68EC020", None, 0, 0, 0, "[]", 0, None, "1994", "Psikyo", '["fbneo", "mame"]'),
        ("gunbird2", "Gunbird 2", "Psikyo SH-2", None, 0, 0, 0, "[]", 0, None, "1998", "Psikyo", '["fbneo", "mame"]'),
        ("tengai", "Sengoku Blade / Tengai", "Psikyo 68EC020", None, 0, 0, 0, "[]", 0, None, "1996", "Psikyo", '["fbneo", "mame"]'),
        ("sonicwi", "Sonic Wings / Aero Fighters", "Video System Hardware", None, 0, 0, 0, "[]", 0, None, "1992", "Video System", '["fbneo", "mame"]'),

        # Classics
        ("pacman", "Pac-Man", "Midway / Namco Pacman", None, 0, 0, 0, "[]", 0, None, "1980", "Namco", '["fbneo", "mame2003_plus", "mame"]'),
        ("mspacman", "Ms. Pac-Man", "Midway / Namco Pacman", "pacman", 1, 0, 0, "[]", 0, None, "1981", "Midway", '["fbneo", "mame2003_plus", "mame"]'),
        ("galaga", "Galaga", "Namco Galaga Hardware", None, 0, 0, 0, "[]", 0, None, "1981", "Namco", '["fbneo", "mame2003_plus", "mame"]'),
        ("dkong", "Donkey Kong", "Nintendo Donkey Kong Hardware", None, 0, 0, 0, "[]", 0, None, "1981", "Nintendo", '["fbneo", "mame2003_plus", "mame"]'),
        ("dkongjr", "Donkey Kong Junior", "Nintendo Donkey Kong Hardware", None, 0, 0, 0, "[]", 0, None, "1982", "Nintendo", '["fbneo", "mame2003_plus", "mame"]'),
        ("dkong3", "Donkey Kong 3", "Nintendo Donkey Kong Hardware", None, 0, 0, 0, "[]", 0, None, "1983", "Nintendo", '["fbneo", "mame2003_plus", "mame"]'),
        ("mario", "Mario Bros.", "Nintendo Mario Bros. Hardware", None, 0, 0, 0, "[]", 0, None, "1983", "Nintendo", '["fbneo", "mame2003_plus", "mame"]'),
        ("digdug", "Dig Dug", "Namco Galaga Hardware", None, 0, 0, 0, "[]", 0, None, "1982", "Namco", '["fbneo", "mame2003_plus", "mame"]'),
        ("galaxian", "Galaxian", "Namco Galaxian Hardware", None, 0, 0, 0, "[]", 0, None, "1979", "Namco", '["fbneo", "mame2003_plus", "mame"]'),
        ("1942", "1942", "Capcom 1942 Hardware", None, 0, 0, 0, "[]", 0, None, "1984", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("1943", "1943: The Battle of Midway", "Capcom 1943 Hardware", None, 0, 0, 0, "[]", 0, None, "1987", "Capcom", '["fbneo", "mame2003_plus", "mame"]'),
        ("baddudes", "Bad Dudes vs. Dragonninja", "Data East 68000", None, 0, 0, 0, "[]", 0, None, "1988", "Data East", '["fbneo", "mame"]'),
        ("robocop", "Robocop", "Data East 68000", None, 0, 0, 0, "[]", 0, None, "1988", "Data East", '["fbneo", "mame"]'),
        ("ddragon", "Double Dragon", "Technos 6809", None, 0, 0, 0, "[]", 0, None, "1987", "Technos Japan", '["fbneo", "mame2003_plus", "mame"]'),
        ("ddragon2", "Double Dragon II: The Revenge", "Technos 6809", None, 0, 0, 0, "[]", 0, None, "1988", "Technos Japan", '["fbneo", "mame2003_plus", "mame"]'),
        ("ddragon3", "Double Dragon 3: The Rosetta Stone", "Technos 68000", None, 0, 0, 0, "[]", 0, None, "1990", "Technos Japan", '["fbneo", "mame2003_plus", "mame"]'),
        ("snowbros", "Snow Bros. - Nick & Tom", "Toaplan V1", None, 0, 0, 0, "[]", 0, None, "1990", "Toaplan", '["fbneo", "mame2003_plus", "mame"]'),
        ("snowbro2", "Snow Bros. 2 - With New Elves", "Toaplan V2 / Hanafram", None, 0, 0, 0, "[]", 0, None, "1994", "Toaplan / Hanafram", '["fbneo", "mame2003_plus", "mame"]'),
        ("bublbobl", "Bubble Bobble", "Taito 68705", None, 0, 0, 0, "[]", 0, None, "1986", "Taito", '["fbneo", "mame2003_plus", "mame"]'),
        ("arkanoid", "Arkanoid", "Taito 68705", None, 0, 0, 0, "[]", 0, None, "1986", "Taito", '["fbneo", "mame2003_plus", "mame"]'),
        ("shinobi", "Shinobi", "Sega System 16A", None, 0, 0, 0, "[]", 0, None, "1987", "Sega", '["fbneo", "mame"]'),
        ("goldnaxe", "Golden Axe", "Sega System 16B", None, 0, 0, 0, "[]", 0, None, "1989", "Sega", '["fbneo", "mame"]'),
        ("outrun", "Out Run", "Sega Outrun Hardware", None, 0, 0, 0, "[]", 0, None, "1986", "Sega", '["fbneo", "mame"]'),
        ("afterbnd", "After Burner", "Sega X Board", None, 0, 0, 0, "[]", 0, None, "1987", "Sega", '["fbneo", "mame"]'),
        ("aburner2", "After Burner II", "Sega X Board", None, 0, 0, 0, "[]", 0, None, "1987", "Sega", '["fbneo", "mame"]'),
        ("spaceinv", "Space Invaders", "Midway 8080", None, 0, 0, 0, "[]", 0, None, "1978", "Taito / Midway", '["mame2003_plus", "mame"]'),
        ("asteroids", "Asteroids", "Atari Vector 6502", None, 0, 0, 0, "[]", 0, None, "1979", "Atari", '["mame2003_plus", "mame"]'),
    ]

    cur.executemany("INSERT OR REPLACE INTO arcade_romsets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", arcade_records)

    # =========================================================================
    # 3. 콘솔 시리얼 (PS1, PS2, PSP, Saturn, Dreamcast, GBA, NDS, N64)
    # =========================================================================
    serial_records = [
        # PS1
        ("SCUS-94163", "psx", "Final Fantasy VII (Disc 1)", "USA", "Square EA"),
        ("SCUS-94164", "psx", "Final Fantasy VII (Disc 2)", "USA", "Square EA"),
        ("SCUS-94165", "psx", "Final Fantasy VII (Disc 3)", "USA", "Square EA"),
        ("SLUS-00594", "psx", "Metal Gear Solid (Disc 1)", "USA", "Konami"),
        ("SLUS-00776", "psx", "Metal Gear Solid (Disc 2)", "USA", "Konami"),
        ("SLUS-00067", "psx", "Resident Evil", "USA", "Capcom"),
        ("SLUS-00747", "psx", "Resident Evil 2 (Leon Disc)", "USA", "Capcom"),
        ("SLUS-00756", "psx", "Resident Evil 2 (Claire Disc)", "USA", "Capcom"),
        ("SLUS-00923", "psx", "Resident Evil 3: Nemesis", "USA", "Capcom"),
        ("SLUS-00664", "psx", "Tekken 3", "USA", "Namco"),
        ("SLUS-00837", "psx", "Silent Hill", "USA", "Konami"),
        ("SLUS-00404", "psx", "Castlevania: Symphony of the Night", "USA", "Konami"),
        ("SCUS-94228", "psx", "Gran Turismo", "USA", "Sony"),
        ("SCUS-94455", "psx", "Gran Turismo 2", "USA", "Sony"),
        ("SCUS-94244", "psx", "Crash Bandicoot: Warped", "USA", "Sony"),
        ("SCUS-94426", "psx", "Spyro: Year of the Dragon", "USA", "Sony"),
        ("SLUS-00508", "psx", "R4: Ridge Racer Type 4", "USA", "Namco"),
        ("SLUS-01011", "psx", "Chrono Cross (Disc 1)", "USA", "Square EA"),
        ("SLUS-01041", "psx", "Chrono Cross (Disc 2)", "USA", "Square EA"),
        ("SLUS-00892", "psx", "Dino Crisis", "USA", "Capcom"),
        ("SLUS-01279", "psx", "Dino Crisis 2", "USA", "Capcom"),
        ("SLPS-00898", "psx", "Final Fantasy VII (Japan)", "Japan", "Square"),
        ("SCES-00001", "psx", "Ridge Racer (Europe)", "Europe", "Namco"),

        # PS2
        ("SLUS-20062", "ps2", "Tekken Tag Tournament", "USA", "Namco"),
        ("SLUS-20682", "ps2", "Metal Gear Solid 3: Snake Eater", "USA", "Konami"),
        ("SLUS-20144", "ps2", "Metal Gear Solid 2: Sons of Liberty", "USA", "Konami"),
        ("SLUS-20228", "ps2", "Grand Theft Auto: Vice City", "USA", "Rockstar Games"),
        ("SLUS-20946", "ps2", "Grand Theft Auto: San Andreas", "USA", "Rockstar Games"),
        ("SCUS-97399", "ps2", "God of War", "USA", "Sony"),
        ("SCUS-97481", "ps2", "God of War II", "USA", "Sony"),
        ("SCUS-97328", "ps2", "Gran Turismo 4", "USA", "Sony"),
        ("SLUS-20672", "ps2", "Resident Evil 4", "USA", "Capcom"),
        ("SLUS-20963", "ps2", "Devil May Cry 3: Dante's Awakening", "USA", "Capcom"),
        ("SCUS-97472", "ps2", "Shadow of the Colossus", "USA", "Sony"),
        ("SLUS-20312", "ps2", "Final Fantasy X", "USA", "Square EA"),
        ("SLUS-20965", "ps2", "Final Fantasy XII", "USA", "Square Enix"),
        ("SLUS-20456", "ps2", "Kingdom Hearts", "USA", "Square EA"),
        ("SLUS-21005", "ps2", "Kingdom Hearts II", "USA", "Square Enix"),

        # PSP
        ("ULUS-10041", "psp", "Ridge Racer", "USA", "Namco"),
        ("ULUS-10285", "psp", "Crisis Core: Final Fantasy VII", "USA", "Square Enix"),
        ("ULUS-10025", "psp", "Lumines", "USA", "Ubisoft"),
        ("ULUS-10336", "psp", "God of War: Chains of Olympus", "USA", "Sony"),
        ("ULUS-10532", "psp", "God of War: Ghost of Sparta", "USA", "Sony"),
        ("ULUS-10036", "psp", "Grand Theft Auto: Liberty City Stories", "USA", "Rockstar Games"),
        ("ULUS-10160", "psp", "Grand Theft Auto: Vice City Stories", "USA", "Rockstar Games"),
        ("ULUS-10500", "psp", "Kingdom Hearts: Birth by Sleep", "USA", "Square Enix"),
        ("ULUS-10245", "psp", "Monster Hunter Freedom 2", "USA", "Capcom"),
        ("ULUS-10391", "psp", "Monster Hunter Freedom Unite", "USA", "Capcom"),
        ("ULUS-10277", "psp", "Castlevania: The Dracula X Chronicles", "USA", "Konami"),
        ("ULUS-10042", "psp", "Wipeout Pure", "USA", "Sony"),
        ("ULUS-10490", "psp", "Metal Gear Solid: Peace Walker", "USA", "Konami"),

        # Sega Saturn
        ("GS-9001", "saturn", "Virtua Fighter", "Japan", "Sega"),
        ("GS-9002", "saturn", "Clockwork Knight", "Japan", "Sega"),
        ("GS-9005", "saturn", "Daytona USA", "Japan", "Sega"),
        ("GS-9016", "saturn", "Sega Rally Championship", "Japan", "Sega"),
        ("GS-9037", "saturn", "Virtua Fighter 2", "Japan", "Sega"),
        ("GS-9097", "saturn", "Nights into Dreams...", "Japan", "Sega"),
        ("GS-9034", "saturn", "Panzer Dragoon", "Japan", "Sega"),
        ("GS-9098", "saturn", "Panzer Dragoon Zwei", "Japan", "Sega"),
        ("GS-9157", "saturn", "Panzer Dragoon Saga (Disc 1)", "Japan", "Sega"),
        ("MK-81000", "saturn", "Virtua Fighter (USA)", "USA", "Sega"),
        ("MK-81005", "saturn", "Daytona USA (USA)", "USA", "Sega"),
        ("MK-81014", "saturn", "Sega Rally Championship (USA)", "USA", "Sega"),

        # Sega Dreamcast
        ("HDR-0010", "dreamcast", "Virtua Fighter 3tb", "Japan", "Sega"),
        ("HDR-0013", "dreamcast", "Sonic Adventure", "Japan", "Sega"),
        ("HDR-0014", "dreamcast", "Sega Rally 2", "Japan", "Sega"),
        ("HDR-0051", "dreamcast", "Crazy Taxi", "Japan", "Sega"),
        ("HDR-0058", "dreamcast", "Shenmue (Disc 1)", "Japan", "Sega"),
        ("HDR-0078", "dreamcast", "Jet Set Radio", "Japan", "Sega"),
        ("HDR-0112", "dreamcast", "Phantasy Star Online", "Japan", "Sega"),
        ("HDR-0115", "dreamcast", "Sonic Adventure 2", "Japan", "Sega"),
        ("MK-51000", "dreamcast", "Sonic Adventure (USA)", "USA", "Sega"),
        ("MK-51009", "dreamcast", "Soulcalibur (USA)", "USA", "Namco"),
        ("MK-51035", "dreamcast", "Crazy Taxi (USA)", "USA", "Sega"),

        # Nintendo 64
        ("NUS-CZLE", "n64", "The Legend of Zelda: Ocarina of Time", "USA", "Nintendo"),
        ("NUS-NZSE", "n64", "The Legend of Zelda: Majora's Mask", "USA", "Nintendo"),
        ("NUS-NSME", "n64", "Super Mario 64", "USA", "Nintendo"),
        ("NUS-NKTE", "n64", "Mario Kart 64", "USA", "Nintendo"),
        ("NUS-NGEE", "n64", "GoldenEye 007", "USA", "Nintendo / Rare"),
        ("NUS-NPDE", "n64", "Perfect Dark", "USA", "Nintendo / Rare"),
        ("NUS-NSFE", "n64", "Star Fox 64", "USA", "Nintendo"),
        ("NUS-NBKE", "n64", "Banjo-Kazooie", "USA", "Nintendo / Rare"),
        ("NUS-NBTE", "n64", "Banjo-Tooie", "USA", "Nintendo / Rare"),
        ("NUS-NSSE", "n64", "Super Smash Bros.", "USA", "Nintendo / HAL"),
        ("NUS-NDOE", "n64", "Donkey Kong 64", "USA", "Nintendo / Rare"),
        ("NUS-NPFE", "n64", "Pokemon Stadium", "USA", "Nintendo"),

        # Game Boy Advance
        ("AGB-BPEE", "gba", "Pokemon - Emerald Version", "USA", "Nintendo / Game Freak"),
        ("AGB-AXVE", "gba", "Pokemon - Ruby Version", "USA", "Nintendo / Game Freak"),
        ("AGB-AXPE", "gba", "Pokemon - Sapphire Version", "USA", "Nintendo / Game Freak"),
        ("AGB-BPRE", "gba", "Pokemon - FireRed Version", "USA", "Nintendo / Game Freak"),
        ("AGB-BPGE", "gba", "Pokemon - LeafGreen Version", "USA", "Nintendo / Game Freak"),
        ("AGB-AZLE", "gba", "The Legend of Zelda: The Minish Cap", "USA", "Nintendo / Capcom"),
        ("AGB-A2YE", "gba", "The Legend of Zelda: A Link to the Past & Four Swords", "USA", "Nintendo"),
        ("AGB-AMTE", "gba", "Metroid Fusion", "USA", "Nintendo"),
        ("AGB-BMZE", "gba", "Metroid: Zero Mission", "USA", "Nintendo"),
        ("AGB-AGSE", "gba", "Golden Sun", "USA", "Nintendo / Camelot"),
        ("AGB-AGFE", "gba", "Golden Sun: The Lost Age", "USA", "Nintendo / Camelot"),
        ("AGB-AALE", "gba", "Castlevania: Circle of the Moon", "USA", "Konami"),
        ("AGB-A2CE", "gba", "Castlevania: Harmony of Dissonance", "USA", "Konami"),
        ("AGB-A4CE", "gba", "Castlevania: Aria of Sorrow", "USA", "Konami"),

        # Nintendo DS
        ("NTR-AMHE", "nds", "Metroid Prime Hunters", "USA", "Nintendo"),
        ("NTR-A2DE", "nds", "New Super Mario Bros.", "USA", "Nintendo"),
        ("NTR-ADAE", "nds", "Pokemon Diamond", "USA", "Nintendo"),
        ("NTR-APAE", "nds", "Pokemon Pearl", "USA", "Nintendo"),
        ("NTR-CPUE", "nds", "Pokemon Platinum", "USA", "Nintendo"),
        ("NTR-IPKE", "nds", "Pokemon HeartGold", "USA", "Nintendo"),
        ("NTR-IPGE", "nds", "Pokemon SoulSilver", "USA", "Nintendo"),
        ("NTR-AZEE", "nds", "The Legend of Zelda: Phantom Hourglass", "USA", "Nintendo"),
        ("NTR-BK9E", "nds", "The Legend of Zelda: Spirit Tracks", "USA", "Nintendo"),
        ("NTR-AMCE", "nds", "Mario Kart DS", "USA", "Nintendo"),
        ("NTR-ACRE", "nds", "Castlevania: Dawn of Sorrow", "USA", "Konami"),
        ("NTR-YRCE", "nds", "Castlevania: Portrait of Ruin", "USA", "Konami"),
        ("NTR-YR9E", "nds", "Castlevania: Order of Ecclesia", "USA", "Konami"),
        ("NTR-AGTE", "nds", "Chrono Trigger DS", "USA", "Square Enix"),
    ]

    cur.executemany("INSERT OR REPLACE INTO disc_serials VALUES (?, ?, ?, ?, ?)", serial_records)

    conn.commit()
    conn.close()
    print(f"Database built successfully at {DB_PATH}")


if __name__ == "__main__":
    build_database()
