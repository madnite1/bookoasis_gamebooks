# -*- coding: utf-8 -*-
"""
아케이드(MAME / FBNeo) 롬셋 하드웨어 기판 매핑 및 분류 데이터베이스.
기판 종류(CPS1, CPS2, CPS3, Neo-Geo, NAOMI, ST-V, PGM, Namco System 등),
부모/클론 관계, 필수 바이오스, CHD 필요 여부 및 권장 코어 매핑.
"""

from typing import Dict, Any, Optional

# 아케이드 대표 롬셋 상세 카탈로그
# 롬셋명 (소문자 ZIP 파일 베이스 이름) -> 속성
ARCADE_GAMES_CATALOG: Dict[str, Dict[str, Any]] = {
    # =========================================================================
    # 1. Capcom CPS-1 (Capcom Play System 1 / 1.5)
    # =========================================================================
    "sf2": {"title": "Street Fighter II: The World Warrior", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sf2ce": {"title": "Street Fighter II': Champion Edition", "board": "Capcom CPS-1", "parent": "sf2", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sf2hf": {"title": "Street Fighter II': Hyper Fighting (Turbo)", "board": "Capcom CPS-1", "parent": "sf2", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sf2rb": {"title": "Street Fighter II: Rainbow Edition", "board": "Capcom CPS-1", "parent": "sf2", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ffight": {"title": "Final Fight", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ffightu": {"title": "Final Fight (USA)", "board": "Capcom CPS-1", "parent": "ffight", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ffightj": {"title": "Final Fight (Japan)", "board": "Capcom CPS-1", "parent": "ffight", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ghouls": {"title": "Ghouls'n Ghosts", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "daimakai": {"title": "Dai Makai-Mura (Japan)", "board": "Capcom CPS-1", "parent": "ghouls", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "strider": {"title": "Strider", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "striderj": {"title": "Strider Hiryu (Japan)", "board": "Capcom CPS-1", "parent": "strider", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mercs": {"title": "Mercs", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "senjo": {"title": "Senjo no Ookami II (Japan)", "board": "Capcom CPS-1", "parent": "mercs", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "unsquad": {"title": "U.N. Squadron", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "area88": {"title": "Area 88 (Japan)", "board": "Capcom CPS-1", "parent": "unsquad", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "knights": {"title": "Knights of the Round", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "knightsj": {"title": "Knights of the Round (Japan)", "board": "Capcom CPS-1", "parent": "knights", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "cawing": {"title": "Carrier Air Wing", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "usnavy": {"title": "U.S. Navy (Japan)", "board": "Capcom CPS-1", "parent": "cawing", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "captcomm": {"title": "Captain Commando", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "captcommj": {"title": "Captain Commando (Japan)", "board": "Capcom CPS-1", "parent": "captcomm", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "wof": {"title": "Warriors of Fate (Tenchi o Kurau II)", "board": "Capcom CPS-1.5 (QSound)", "parent": None, "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "wofj": {"title": "Tenchi o Kurau II: Sekiheki no Tatakai (Japan)", "board": "Capcom CPS-1.5 (QSound)", "parent": "wof", "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "punisher": {"title": "The Punisher", "board": "Capcom CPS-1.5 (QSound)", "parent": None, "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "punisherj": {"title": "The Punisher (Japan)", "board": "Capcom CPS-1.5 (QSound)", "parent": "punisher", "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "cadillacs": {"title": "Cadillacs and Dinosaurs", "board": "Capcom CPS-1.5 (QSound)", "parent": None, "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dino": {"title": "Cadillacs and Dinosaurs (World)", "board": "Capcom CPS-1.5 (QSound)", "parent": "cadillacs", "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dinoj": {"title": "Cadillacs: Kyouryuu Shinseiki (Japan)", "board": "Capcom CPS-1.5 (QSound)", "parent": "cadillacs", "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "slammast": {"title": "Saturday Night Slam Masters", "board": "Capcom CPS-1.5 (QSound)", "parent": None, "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mbomber": {"title": "Muscle Bomber: Duo", "board": "Capcom CPS-1.5 (QSound)", "parent": "slammast", "bios": ["qsound.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "forgottn": {"title": "Forgotten Worlds", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "lostwrld": {"title": "Lost Worlds (Japan)", "board": "Capcom CPS-1", "parent": "forgottn", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "varth": {"title": "Varth: Operation Sunrise", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "willow": {"title": "Willow", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "1941": {"title": "1941: Counter Attack", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "megaman": {"title": "Mega Man: The Power Battle", "board": "Capcom CPS-1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "rockman": {"title": "Rockman: The Power Battle (Japan)", "board": "Capcom CPS-1", "parent": "megaman", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},

    # =========================================================================
    # 2. Capcom CPS-2 (Capcom Play System 2)
    # =========================================================================
    "ssf2": {"title": "Super Street Fighter II: The New Challengers", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ssf2t": {"title": "Super Street Fighter II Turbo", "board": "Capcom CPS-2", "parent": "ssf2", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ssf2xj": {"title": "Super Street Fighter II X: Grand Master Challenge (Japan)", "board": "Capcom CPS-2", "parent": "ssf2", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfa": {"title": "Street Fighter Alpha: Warriors' Dreams", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfz": {"title": "Street Fighter Zero (Japan)", "board": "Capcom CPS-2", "parent": "sfa", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfa2": {"title": "Street Fighter Alpha 2", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfz2": {"title": "Street Fighter Zero 2 (Japan)", "board": "Capcom CPS-2", "parent": "sfa2", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfz2al": {"title": "Street Fighter Zero 2 Alpha (Asia)", "board": "Capcom CPS-2", "parent": "sfa2", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfa3": {"title": "Street Fighter Alpha 3", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sfz3": {"title": "Street Fighter Zero 3 (Japan)", "board": "Capcom CPS-2", "parent": "sfa3", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dstlk": {"title": "Darkstalkers: The Night Warriors", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "vamp": {"title": "Vampire: The Night Warriors (Japan)", "board": "Capcom CPS-2", "parent": "dstlk", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "nwarr": {"title": "Night Warriors: Darkstalkers' Revenge", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "vhunt2": {"title": "Vampire Hunter 2: Darkstalkers Revenge (Japan)", "board": "Capcom CPS-2", "parent": "nwarr", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "vsav": {"title": "Vampire Savior: The Lord of Vampire", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "vsav2": {"title": "Vampire Savior 2: The Lord of Vampire (Japan)", "board": "Capcom CPS-2", "parent": "vsav", "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "xmcota": {"title": "X-Men: Children of the Atom", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "msh": {"title": "Marvel Super Heroes", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "xmvsf": {"title": "X-Men Vs. Street Fighter", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mshvsf": {"title": "Marvel Super Heroes Vs. Street Fighter", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mvsc": {"title": "Marvel Vs. Capcom: Clash of Super Heroes", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ddtod": {"title": "Dungeons & Dragons: Tower of Doom", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ddsom": {"title": "Dungeons & Dragons: Shadow over Mystara", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "avsp": {"title": "Alien vs. Predator", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "armwar": {"title": "Armored Warriors / Powered Gear", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "cybots": {"title": "Cyberbots: Fullmetal Madness", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "progear": {"title": "Progear / Progear no Arashi", "board": "Capcom CPS-2 (Cave)", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "19xx": {"title": "19XX: The War Against Destiny", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "1944": {"title": "1944: The Loop Master", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "batcir": {"title": "Battle Circuit", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ecofghtr": {"title": "Eco Fighters", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "megaman2": {"title": "Mega Man 2: The Power Fighters", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "gigawing": {"title": "Giga Wing", "board": "Capcom CPS-2 (Takumi)", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "marsmx": {"title": "Mars Matrix: Hyper Solid Shooting", "board": "Capcom CPS-2 (Takumi)", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dimahoo": {"title": "Dimahoo / Great Mahou Daisakusen", "board": "Capcom CPS-2 (Raizing)", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "smpmsg": {"title": "Super Muscle Bomber: The International Blowout", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "spf2t": {"title": "Super Puzzle Fighter II Turbo", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sgemf": {"title": "Super Gem Fighter Mini Mix (Pocket Fighter)", "board": "Capcom CPS-2", "parent": None, "bios": ["qsound_hle.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},

    # =========================================================================
    # 3. Capcom CPS-3 (Capcom Play System 3)
    # =========================================================================
    "sfiii": {"title": "Street Fighter III: New Generation", "board": "Capcom CPS-3", "parent": None, "bios": [], "chd": True, "cores": ["fbneo", "mame"]},
    "sfiii2": {"title": "Street Fighter III 2nd Impact: Giant Attack", "board": "Capcom CPS-3", "parent": None, "bios": [], "chd": True, "cores": ["fbneo", "mame"]},
    "sfiii3": {"title": "Street Fighter III 3rd Strike: Fight for the Future", "board": "Capcom CPS-3", "parent": None, "bios": [], "chd": True, "cores": ["fbneo", "mame"]},
    "sfiii3nr": {"title": "Street Fighter III 3rd Strike (No CD / Custom)", "board": "Capcom CPS-3", "parent": "sfiii3", "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "jojoba": {"title": "JoJo's Bizarre Adventure: Heritage for the Future", "board": "Capcom CPS-3", "parent": None, "bios": [], "chd": True, "cores": ["fbneo", "mame"]},
    "jojo": {"title": "JoJo's Venture", "board": "Capcom CPS-3", "parent": None, "bios": [], "chd": True, "cores": ["fbneo", "mame"]},
    "redearth": {"title": "Red Earth / Warzard", "board": "Capcom CPS-3", "parent": None, "bios": [], "chd": True, "cores": ["fbneo", "mame"]},
    "warzard": {"title": "Warzard (Japan)", "board": "Capcom CPS-3", "parent": "redearth", "bios": [], "chd": True, "cores": ["fbneo", "mame"]},

    # =========================================================================
    # 4. SNK Neo-Geo MVS / AES
    # =========================================================================
    "kof94": {"title": "The King of Fighters '94", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof95": {"title": "The King of Fighters '95", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof96": {"title": "The King of Fighters '96", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof97": {"title": "The King of Fighters '97", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof98": {"title": "The King of Fighters '98 - The Slugfest", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof99": {"title": "The King of Fighters '99 - Millennium Battle", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof2000": {"title": "The King of Fighters 2000", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof2001": {"title": "The King of Fighters 2001", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof2002": {"title": "The King of Fighters 2002 - Challenge to Ultimate Battle", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kof2003": {"title": "The King of Fighters 2003", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mslug": {"title": "Metal Slug - Super Vehicle-001", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mslug2": {"title": "Metal Slug 2 - Super Vehicle-001/II", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mslugx": {"title": "Metal Slug X - Super Vehicle-001", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mslug3": {"title": "Metal Slug 3", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mslug4": {"title": "Metal Slug 4", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mslug5": {"title": "Metal Slug 5", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "samsho": {"title": "Samurai Shodown / Samurai Spirits", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "samsho2": {"title": "Samurai Shodown II / Shin Samurai Spirits", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "samsho3": {"title": "Samurai Shodown III", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "samsho4": {"title": "Samurai Shodown IV - Amakusa's Revenge", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "samsho5": {"title": "Samurai Shodown V / Samurai Spirits Zero", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "samsh5sp": {"title": "Samurai Shodown V Special", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "fatfur1": {"title": "Fatal Fury: King of Fighters", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "fatfur2": {"title": "Fatal Fury 2", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "fatfury3": {"title": "Fatal Fury 3: Road to the Final Victory", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "fatfuryu": {"title": "Fatal Fury Special", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "rbff1": {"title": "Real Bout Fatal Fury", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "rbffspec": {"title": "Real Bout Fatal Fury Special", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "rbff2": {"title": "Real Bout Fatal Fury 2 - The Newcomers", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "garou": {"title": "Garou: Mark of the Wolves", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "aof": {"title": "Art of Fighting", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "aof2": {"title": "Art of Fighting 2", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "aof3": {"title": "Art of Fighting 3: The Path of the Warrior", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "lastblad": {"title": "The Last Blade / Bakumatsu Roman", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "lastbld2": {"title": "The Last Blade 2", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "wh1": {"title": "World Heroes", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "wh2": {"title": "World Heroes 2", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "wh2j": {"title": "World Heroes 2 Jet", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "whp": {"title": "World Heroes Perfect", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sengoku": {"title": "Sengoku / Sengoku Denshou", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sengoku2": {"title": "Sengoku 2", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sengoku3": {"title": "Sengoku 3 / Sengoku Denshou 2001", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "shocktro": {"title": "Shock Troopers", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "shocktr2": {"title": "Shock Troopers: 2nd Squad", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "spinmast": {"title": "Spin Master / Miracle Adventure", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "pbobblen": {"title": "Puzzle Bobble / Bust-A-Move (Neo Geo)", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "pbobbl2n": {"title": "Puzzle Bobble 2 / Bust-A-Move Again (Neo Geo)", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "magdrop2": {"title": "Magical Drop II", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "magdrop3": {"title": "Magical Drop III", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "twinklstar": {"title": "Twinkle Star Sprites", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "neobombe": {"title": "Neo Bomberman", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "blazstar": {"title": "Blazing Star", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "pulstar": {"title": "Pulstar", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "stakwin": {"title": "Stakes Winner - GI Kinzen Seiha e no Michi", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "stakwin2": {"title": "Stakes Winner 2", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "superspy": {"title": "The Super Spy", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "viewpoin": {"title": "Viewpoint", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "wjammers": {"title": "Windjammers / Flying Power Disc", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "matrim": {"title": "Matrimelee / Shin Gouketsuji Ichizoku", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "rotd": {"title": "Rage of the Dragons", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "svc": {"title": "SNK vs. Capcom: SVC Chaos", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},

    # =========================================================================
    # 5. IGS PolyGame Master (PGM)
    # =========================================================================
    "kov": {"title": "Knights of Valour / Sangoku Senki", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "kovplus": {"title": "Knights of Valour Plus", "board": "IGS PolyGame Master (PGM)", "parent": "kov", "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "kov2": {"title": "Knights of Valour 2", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "kov2p": {"title": "Knights of Valour 2 Plus - Nine Dragons", "board": "IGS PolyGame Master (PGM)", "parent": "kov2", "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "orlegend": {"title": "Oriental Legend / Xi You Shi E Zhuan", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "martmast": {"title": "Martial Masters", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "dfront": {"title": "Demon Front", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "theglad": {"title": "The Gladiator / Shen Jian", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "drgpunch": {"title": "Dragon World / Sai You Gou Ma Roku", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "ddp2": {"title": "DoDonPachi II - Bee Storm (PGM)", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "killbld": {"title": "The Killing Blade", "board": "IGS PolyGame Master (PGM)", "parent": None, "bios": ["pgm.zip"], "chd": False, "cores": ["fbneo", "mame"]},

    # =========================================================================
    # 6. Sega NAOMI / NAOMI 2 / Hikaru
    # =========================================================================
    "mvc2": {"title": "Marvel Vs. Capcom 2: New Age of Heroes", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "ggx": {"title": "Guilty Gear X", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "ggxx": {"title": "Guilty Gear XX", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "ggxxac": {"title": "Guilty Gear XX Accent Core", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "cvs": {"title": "Capcom vs SNK Millennium Fight 2000", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "cvs2": {"title": "Capcom vs SNK 2: Mark of the Millennium 2001", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": True, "cores": ["flycast", "mame"]},
    "ikaruga": {"title": "Ikaruga", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": True, "cores": ["flycast", "mame"]},
    "slashout": {"title": "Slashout", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": True, "cores": ["flycast", "mame"]},
    "spkrthm": {"title": "Spikers Battle", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": True, "cores": ["flycast", "mame"]},
    "vtennis2": {"title": "Virtua Tennis 2 / Power Smash 2", "board": "Sega NAOMI", "parent": None, "bios": ["naomi.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "vf4": {"title": "Virtua Fighter 4", "board": "Sega NAOMI 2", "parent": None, "bios": ["naomi2.zip"], "chd": True, "cores": ["flycast", "mame"]},
    "vf4evo": {"title": "Virtua Fighter 4 Evolution", "board": "Sega NAOMI 2", "parent": "vf4", "bios": ["naomi2.zip"], "chd": True, "cores": ["flycast", "mame"]},

    # =========================================================================
    # 7. Sega ST-V (Sega Titan Video)
    # =========================================================================
    "astrass": {"title": "Astra SuperStars", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "bakubaku": {"title": "Baku Baku Animal", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "diehard": {"title": "Die Hard Arcade / Dynamite Deka", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "decathlt": {"title": "Decathlete", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "ffreveng": {"title": "Final Fight Revenge", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "cottonbm": {"title": "Cotton Boomerang", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "cotton2": {"title": "Cotton 2", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "groovef": {"title": "Groove On Fight - Gouketsuji Ichizoku 3", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "rsgun": {"title": "Radiant Silvergun", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "shienryu": {"title": "Shienryu", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "suikoen": {"title": "Suiko Enbu / Outlaws of the Lost Dynasty", "board": "Sega ST-V", "parent": None, "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},
    "dnmt": {"title": "Dynamite Deka", "board": "Sega ST-V", "parent": "diehard", "bios": ["stvbios.zip"], "chd": False, "cores": ["mame", "kronos"]},

    # =========================================================================
    # 8. Sammy Atomiswave
    # =========================================================================
    "kofxi": {"title": "The King of Fighters XI", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "mslug6": {"title": "Metal Slug 6", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "ngbc": {"title": "NeoGeo Battle Coliseum", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "fotns": {"title": "Fist of the North Star / Hokuto no Ken", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "dolphin": {"title": "Dolphin Blue", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "rumblef": {"title": "The Rumble Fish", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "rumblef2": {"title": "The Rumble Fish 2", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "demoderm": {"title": "Demolish Fist", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "kov7spir": {"title": "Knights of Valour: The Seven Spirits", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},
    "samsptk": {"title": "Samurai Shodown VI / Tenkaichi Kenkakuden", "board": "Sammy Atomiswave", "parent": None, "bios": ["awbios.zip"], "chd": False, "cores": ["flycast", "mame"]},

    # =========================================================================
    # 9. Namco System 1 / 2 / 11 / 12
    # =========================================================================
    "tekken": {"title": "Tekken", "board": "Namco System 11", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "tekken2": {"title": "Tekken 2", "board": "Namco System 11", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "tekken3": {"title": "Tekken 3", "board": "Namco System 12", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "tekkent": {"title": "Tekken Tag Tournament", "board": "Namco System 12", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "soulclbr": {"title": "Soulcalibur", "board": "Namco System 12", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "souledge": {"title": "Soul Edge", "board": "Namco System 11", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "timecris": {"title": "Time Crisis", "board": "Namco Super System 22", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "pointb": {"title": "Point Blank / Gun Bullet", "board": "Namco System NB-1", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "pointb2": {"title": "Point Blank 2 / Gunbarl", "board": "Namco System NB-1", "parent": None, "bios": [], "chd": False, "cores": ["mame"]},
    "splatter": {"title": "Splatterhouse", "board": "Namco System 1", "parent": None, "bios": ["namcoc70.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "pacland": {"title": "Pac-Land", "board": "Namco Pac-Land Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},

    # =========================================================================
    # 10. Cave 68000 / CV1000 Hardware
    # =========================================================================
    "donpachi": {"title": "DonPachi", "board": "Cave 68000 (Atlus)", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "ddonpach": {"title": "DoDonPachi", "board": "Cave 68000 (Atlus)", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "esprade": {"title": "ESP Ra.De.", "board": "Cave 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "guwange": {"title": "Guwange", "board": "Cave 68000 (Atlus)", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "feversos": {"title": "Dangun Feveron / Fever SOS", "board": "Cave 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "mushisam": {"title": "Mushihimesama", "board": "Cave CV1000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "futari": {"title": "Mushihimesama Futari", "board": "Cave CV1000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "espgal": {"title": "Espgaluda", "board": "Cave CV1000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "espgal2": {"title": "Espgaluda II", "board": "Cave CV1000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "deathsm": {"title": "Deathsmiles", "board": "Cave CV1000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "ibara": {"title": "Ibara", "board": "Cave CV1000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},

    # =========================================================================
    # 11. Konami Classic / GX / TMNT
    # =========================================================================
    "tmnt": {"title": "Teenage Mutant Ninja Turtles", "board": "Konami 68000 (TMNT)", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "tmnt2": {"title": "Teenage Mutant Ninja Turtles: Turtles in Time", "board": "Konami 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "simpsons": {"title": "The Simpsons", "board": "Konami 6809", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "xmen": {"title": "X-Men (4 Players / 6 Players)", "board": "Konami 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "sunset": {"title": "Sunset Riders", "board": "Konami 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mystwarr": {"title": "Mystic Warriors", "board": "Konami Mystic Warriors Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "gokupar": {"title": "Gokujou Parodius - Kako no Eikou o Motomete", "board": "Konami GX System", "parent": None, "bios": ["konamigx.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "salmndr2": {"title": "Salamander 2", "board": "Konami GX System", "parent": None, "bios": ["konamigx.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "sexyparo": {"title": "Sexy Parodius", "board": "Konami GX System", "parent": None, "bios": ["konamigx.zip"], "chd": False, "cores": ["fbneo", "mame"]},

    # =========================================================================
    # 12. Midway Y-Unit / T-Unit / Wolfunit
    # =========================================================================
    "mk": {"title": "Mortal Kombat", "board": "Midway Y-Unit", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "mk2": {"title": "Mortal Kombat II", "board": "Midway T-Unit", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "mk3": {"title": "Mortal Kombat 3", "board": "Midway Wolfunit", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "umk3": {"title": "Ultimate Mortal Kombat 3", "board": "Midway Wolfunit", "parent": "mk3", "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "nbajam": {"title": "NBA Jam", "board": "Midway T-Unit", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "nbahangt": {"title": "NBA Hangtime", "board": "Midway Wolfunit", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "wwfmania": {"title": "WWF: Wrestlemania", "board": "Midway Wolfunit", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "rampage": {"title": "Rampage", "board": "Bally Midway MCR 3", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},

    # =========================================================================
    # 13. Psikyo / Video System
    # =========================================================================
    "s1945": {"title": "Strikers 1945", "board": "Psikyo 68EC020", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "s1945ii": {"title": "Strikers 1945 II", "board": "Psikyo SH-2", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "s1945iii": {"title": "Strikers 1945 III / Strikers 1999", "board": "Psikyo SH-2", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "gunbird": {"title": "Gunbird", "board": "Psikyo 68EC020", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "gunbird2": {"title": "Gunbird 2", "board": "Psikyo SH-2", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "tengai": {"title": "Sengoku Blade / Tengai", "board": "Psikyo 68EC020", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "sonicwi": {"title": "Sonic Wings / Aero Fighters", "board": "Video System Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "sonicwi2": {"title": "Sonic Wings 2 / Aero Fighters 2 (Neo Geo)", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame"]},
    "sonicwi3": {"title": "Sonic Wings 3 / Aero Fighters 3 (Neo Geo)", "board": "SNK Neo-Geo MVS", "parent": None, "bios": ["neogeo.zip"], "chd": False, "cores": ["fbneo", "mame"]},

    # =========================================================================
    # 14. Classic MAME & Others
    # =========================================================================
    "pacman": {"title": "Pac-Man", "board": "Midway / Namco Pacman", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mspacman": {"title": "Ms. Pac-Man", "board": "Midway / Namco Pacman", "parent": "pacman", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "galaga": {"title": "Galaga", "board": "Namco Galaga Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dkong": {"title": "Donkey Kong", "board": "Nintendo Donkey Kong Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dkongjr": {"title": "Donkey Kong Junior", "board": "Nintendo Donkey Kong Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "dkong3": {"title": "Donkey Kong 3", "board": "Nintendo Donkey Kong Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "mario": {"title": "Mario Bros.", "board": "Nintendo Mario Bros. Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "digdug": {"title": "Dig Dug", "board": "Namco Galaga Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "galaxian": {"title": "Galaxian", "board": "Namco Galaxian Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "1942": {"title": "1942", "board": "Capcom 1942 Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "1943": {"title": "1943: The Battle of Midway", "board": "Capcom 1943 Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "baddudes": {"title": "Bad Dudes vs. Dragonninja", "board": "Data East 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "robocop": {"title": "Robocop", "board": "Data East 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "ddragon": {"title": "Double Dragon", "board": "Technos 6809", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ddragon2": {"title": "Double Dragon II: The Revenge", "board": "Technos 6809", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "ddragon3": {"title": "Double Dragon 3: The Rosetta Stone", "board": "Technos 68000", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "snowbros": {"title": "Snow Bros. - Nick & Tom", "board": "Toaplan V1", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "snowbro2": {"title": "Snow Bros. 2 - With New Elves", "board": "Toaplan V2 / Hanafram", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "bublbobl": {"title": "Bubble Bobble", "board": "Taito 68705", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "penta": {"title": "Yie Ar Kung-Fu", "board": "Konami 6809", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "arkanoid": {"title": "Arkanoid", "board": "Taito 68705", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "bombjack": {"title": "Bomb Jack", "board": "Tehkan Bomb Jack", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "kungfum": {"title": "Kung-Fu Master / Spartan X", "board": "Irem M62", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "spartanx": {"title": "Spartan X (Japan)", "board": "Irem M62", "parent": "kungfum", "bios": [], "chd": False, "cores": ["fbneo", "mame2003_plus", "mame"]},
    "shinobi": {"title": "Shinobi", "board": "Sega System 16A", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "goldnaxe": {"title": "Golden Axe", "board": "Sega System 16B", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "outrun": {"title": "Out Run", "board": "Sega Outrun Hardware", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "afterbnd": {"title": "After Burner", "board": "Sega X Board", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "aburner2": {"title": "After Burner II", "board": "Sega X Board", "parent": None, "bios": [], "chd": False, "cores": ["fbneo", "mame"]},
    "spaceinv": {"title": "Space Invaders", "board": "Midway 8080", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "asteroids": {"title": "Asteroids", "board": "Atari Vector 6502", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "centiped": {"title": "Centipede", "board": "Atari 6502", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "defender": {"title": "Defender", "board": "Williams 6809", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "joust": {"title": "Joust", "board": "Williams 6809", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
    "robotron": {"title": "Robotron: 2084", "board": "Williams 6809", "parent": None, "bios": [], "chd": False, "cores": ["mame2003_plus", "mame"]},
}

# 접두사/패턴 기반 하드웨어 기판 추론 규칙
BOARD_PREFIX_PATTERNS = [
    # CPS1
    (r"^(sf2|ffight|ghouls|strider|mercs|unsquad|knights|cawing|captcomm|forgottn|varth|willow|1941)", "Capcom CPS-1"),
    # CPS1.5 (QSound)
    (r"^(wof|punisher|cadillacs|dino|slammast|mbomber)", "Capcom CPS-1.5 (QSound)"),
    # CPS2
    (r"^(ssf2|sfa|sfz|dstlk|vamp|nwarr|vhunt|vsav|xmcota|msh|xmvsf|mshvsf|mvsc|ddtod|ddsom|avsp|armwar|cybots|progear|19xx|1944|batcir|ecofghtr|megaman2|gigawing|marsmx|dimahoo|smpmsg|spf2|sgemf)", "Capcom CPS-2"),
    # CPS3
    (r"^(sfiii|jojo|redearth|warzard)", "Capcom CPS-3"),
    # Neo-Geo
    (r"^(kof|mslug|samsho|samsh|fatfur|rbff|garou|aof|lastblad|lastbld|wh|whp|sengoku|shocktro|shocktr|spinmast|pbobblen|pbobbl2n|magdrop|twinklstar|neobombe|blazstar|pulstar|stakwin|superspy|viewpoin|wjammers|matrim|rotd|svc)", "SNK Neo-Geo MVS"),
    # PGM
    (r"^(kov|orlegend|martmast|dfront|theglad|drgpunch|ddp2|killbld)", "IGS PolyGame Master (PGM)"),
    # NAOMI
    (r"^(naomi|mvc2|ggx|cvs|ikaruga|slashout|spkrthm|vtennis|vf4)", "Sega NAOMI"),
    # ST-V
    (r"^(astrass|bakubaku|diehard|decathlt|ffreveng|cottonbm|cotton2|groovef|rsgun|shienryu|suikoen|dnmt)", "Sega ST-V"),
    # Atomiswave
    (r"^(kofxi|mslug6|ngbc|fotns|dolphin|rumblef|demoderm|kov7spir|samsptk)", "Sammy Atomiswave"),
]


def lookup_arcade_catalog(driver_name: str) -> Optional[Dict[str, Any]]:
    """드라이버/롬셋명으로 아케이드 카탈로그 정보 조회"""
    if not driver_name:
        return None
    key = driver_name.lower().strip()
    # 1. 완전 일치 조회
    if key in ARCADE_GAMES_CATALOG:
        return ARCADE_GAMES_CATALOG[key]

    # 2. 클론/지역 접미사(예: sf2ce, kof98k, mslug3n) 앞부분 부모 검색
    for parent_key, info in ARCADE_GAMES_CATALOG.items():
        if key.startswith(parent_key) and len(key) <= len(parent_key) + 4:
            clone_info = dict(info)
            clone_info["parent"] = parent_key
            clone_info["is_clone"] = True
            return clone_info

    return None
