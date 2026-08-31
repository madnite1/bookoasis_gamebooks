# -*- coding: utf-8 -*-
"""아케이드 기판 추론 규칙과 기존 카탈로그 API 호환 래퍼.

정적 게임 카탈로그는 rom_database가 관리하고, 파일명 기반 기판 추론 규칙은
분석 정책이므로 rom_analyzer에 유지한다.
"""

from rom_database.catalogs.arcade import ARCADE_GAMES_CATALOG, lookup_arcade_catalog

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

__all__ = ["ARCADE_GAMES_CATALOG", "BOARD_PREFIX_PATTERNS", "lookup_arcade_catalog"]
