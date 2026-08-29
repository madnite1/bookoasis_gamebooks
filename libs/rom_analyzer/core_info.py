# -*- coding: utf-8 -*-
"""EmulatorJS Stable 코어/확장자/BIOS 매핑.

현재 스냅샷은 EmulatorJS Stable 4.2.3의 data/cores/cores.json 및 공식
시스템 문서를 기준으로 한다. rom-analyzer는 이 목록을 실행 호환성 판단의 기준으로
사용하고, 범용 Libretro 전체 카탈로그를 지원 대상으로 간주하지 않는다.
"""

from dataclasses import dataclass, field, replace
import os
from typing import List, Dict, Optional

from .models import RomAnalysisResult, EmulatorJsInfo, BiosInfo
from .emulatorjs_config import EMULATORJS_STABLE_VERSION, EMULATORJS_BIOS_REQUIREMENTS

ARCHIVE_WRAPPERS = {".zip", ".7z"}


@dataclass
class CoreFirmwareRequirement:
    filename: str
    description: str
    is_optional: bool = False


@dataclass
class CoreInfo:
    core_id: str
    display_name: str
    system_id: str
    supported_extensions: List[str]
    emulatorjs_system: str
    firmwares: List[CoreFirmwareRequirement] = field(default_factory=list)
    requires_threads: bool = False
    notes: Optional[str] = None

    def supports_extension(self, ext: str) -> bool:
        value = (ext or "").lower()
        if value and not value.startswith("."):
            value = "." + value
        return value in self.supported_extensions


def _exts(*values: str) -> List[str]:
    return [v if v.startswith(".") else f".{v}" for v in values]


def _firmwares(system_id: str) -> List[CoreFirmwareRequirement]:
    requirement = EMULATORJS_BIOS_REQUIREMENTS.get(system_id)
    if not requirement:
        return []
    optional = not bool(requirement.get("mandatory", False))
    description = requirement.get("description") or f"{system_id} BIOS"
    return [CoreFirmwareRequirement(name, description, optional) for name in requirement.get("bios_files", [])]


# EmulatorJS Stable 4.2.3에서 rom-analyzer가 현재 다루는 시스템에 필요한 코어만 유지한다.
EMULATORJS_STABLE_CORES: Dict[str, CoreInfo] = {
    "fceumm": CoreInfo("fceumm", "NES / Famicom (FCEUmm)", "nes", _exts("fds", "nes", "unif", "unf"), "nes",
        _firmwares("fds")),
    "nestopia": CoreInfo("nestopia", "NES / Famicom (Nestopia)", "nes", _exts("fds", "nes", "unif", "unf"), "nes"),
    "snes9x": CoreInfo("snes9x", "SNES / SFC (Snes9x)", "snes", _exts("smc", "sfc", "swc", "fig", "bs", "st"), "snes"),
    "gambatte": CoreInfo("gambatte", "Game Boy / Color (Gambatte)", "gb", _exts("gb", "gbc", "dmg"), "gb"),
    "mgba": CoreInfo("mgba", "Game Boy Advance (mGBA)", "gba", _exts("gb", "gbc", "gba"), "gba",
        _firmwares("gba")),
    "beetle_vb": CoreInfo("beetle_vb", "Virtual Boy (Beetle VB)", "vb", _exts("vb", "vboy", "bin"), "vb"),
    "mupen64plus_next": CoreInfo("mupen64plus_next", "Nintendo 64 (Mupen64Plus-Next)", "n64", _exts("n64", "v64", "z64", "bin", "u1", "ndd", "gb"), "n64"),
    "parallel_n64": CoreInfo("parallel_n64", "Nintendo 64 (ParaLLEl N64)", "n64", _exts("n64", "v64", "z64", "bin", "u1", "ndd", "gb"), "n64"),
    "melonds": CoreInfo("melonds", "Nintendo DS (melonDS)", "nds", _exts("nds"), "nds"),
    "desmume2015": CoreInfo("desmume2015", "Nintendo DS (DeSmuME 2015)", "nds", _exts("nds", "bin"), "nds"),
    "desmume": CoreInfo("desmume", "Nintendo DS (DeSmuME)", "nds", _exts("nds", "bin"), "nds"),
    "a5200": CoreInfo("a5200", "Atari 5200", "atari5200", _exts("a52", "bin"), "a5200"),
    "prosystem": CoreInfo("prosystem", "Atari 7800 (ProSystem)", "atari7800", _exts("a78", "bin"), "atari7800"),
    "stella2014": CoreInfo("stella2014", "Atari 2600 (Stella 2014)", "atari2600", _exts("a26", "bin", "zip"), "atari2600"),
    "handy": CoreInfo("handy", "Atari Lynx (Handy)", "lynx", _exts("lnx"), "lynx",
        _firmwares("lynx")),
    "virtualjaguar": CoreInfo("virtualjaguar", "Atari Jaguar (Virtual Jaguar)", "jaguar", _exts("j64", "jag", "rom", "abs", "cof", "bin", "prg"), "jaguar"),
    "opera": CoreInfo("opera", "3DO (Opera)", "3do", _exts("iso", "bin", "chd", "cue"), "3do",
        _firmwares("3do")),
    "genesis_plus_gx": CoreInfo("genesis_plus_gx", "Sega MD / GG / CD (Genesis Plus GX)", "megadrive",
        _exts("m3u", "mdx", "md", "smd", "gen", "bin", "cue", "iso", "chd", "bms", "sms", "gg", "sg", "68k", "sgd"), "segaMD",
        _firmwares("segacd")),
    "smsplus": CoreInfo("smsplus", "Sega Master System (SMS Plus GX)", "mastersystem",
        _exts("m3u", "mdx", "md", "smd", "gen", "bin", "cue", "iso", "chd", "bms", "sms", "gg", "sg", "68k", "sgd"), "segaMS"),
    "picodrive": CoreInfo("picodrive", "Sega 32X / MD / CD (PicoDrive)", "sega32x", _exts("bin", "gen", "smd", "md", "32x", "cue", "iso", "sms", "68k", "chd"), "sega32x"),
    "yabause": CoreInfo("yabause", "Sega Saturn (Yabause)", "saturn", _exts("cue", "iso", "ccd", "mds", "chd", "zip", "m3u"), "segaSaturn",
        _firmwares("saturn")),
    "pcsx_rearmed": CoreInfo("pcsx_rearmed", "PlayStation (PCSX ReARMed)", "psx", _exts("bin", "cue", "img", "mdf", "pbp", "toc", "cbn", "m3u", "ccd"), "psx",
        _firmwares("psx")),
    "mednafen_psx_hw": CoreInfo("mednafen_psx_hw", "PlayStation (Beetle PSX HW)", "psx", _exts("cue", "toc", "m3u", "ccd", "exe", "pbp", "chd"), "psx",
        _firmwares("psx")),
    "fbneo": CoreInfo("fbneo", "Arcade (FinalBurn Neo)", "arcade", _exts("zip", "7z"), "arcade"),
    "mame2003": CoreInfo("mame2003", "MAME 2003", "arcade", _exts("zip"), "mame2003"),
    "mame2003_plus": CoreInfo("mame2003_plus", "MAME 2003-Plus", "arcade", _exts("zip"), "mame2003"),
    "mednafen_pce": CoreInfo("mednafen_pce", "PC Engine / CD (Beetle PCE)", "pce", _exts("pce", "cue", "ccd", "iso", "img", "bin", "chd"), "pce",
        _firmwares("pcecd")),
    "mednafen_ngp": CoreInfo("mednafen_ngp", "Neo Geo Pocket (Beetle NeoPop)", "ngp", _exts("ngp", "ngc"), "ngp"),
    "mednafen_wswan": CoreInfo("mednafen_wswan", "WonderSwan (Beetle WonderSwan)", "wonderswan", _exts("ws", "wsc", "pc2"), "ws"),
    "gearcoleco": CoreInfo("gearcoleco", "ColecoVision (Gearcoleco)", "coleco", _exts("col", "cv", "bin", "rom"), "coleco"),
    "ppsspp": CoreInfo("ppsspp", "PlayStation Portable (PPSSPP)", "psp", _exts("elf", "iso", "cso", "prx", "pbp"), "psp", requires_threads=True),
}

# 기존 API 이름은 유지하되 의미는 EmulatorJS Stable 스냅샷이다.
LIBRETRO_CORES_CATALOG = EMULATORJS_STABLE_CORES

SYSTEM_CORE_ORDER: Dict[str, List[str]] = {
    "nes": ["fceumm", "nestopia"], "fds": ["fceumm", "nestopia"],
    "snes": ["snes9x"], "gb": ["gambatte", "mgba"], "gbc": ["gambatte", "mgba"], "gba": ["mgba"],
    "vb": ["beetle_vb"], "n64": ["mupen64plus_next", "parallel_n64"], "nds": ["melonds", "desmume2015", "desmume"],
    "atari5200": ["a5200"], "atari7800": ["prosystem"], "atari2600": ["stella2014"], "lynx": ["handy"], "jaguar": ["virtualjaguar"],
    "3do": ["opera"],
    "megadrive": ["genesis_plus_gx"], "gamegear": ["genesis_plus_gx"], "segacd": ["genesis_plus_gx", "picodrive"],
    "mastersystem": ["smsplus", "genesis_plus_gx"], "sega32x": ["picodrive"], "saturn": ["yabause"],
    "psx": ["pcsx_rearmed", "mednafen_psx_hw"], "psp": ["ppsspp"],
    "arcade": ["fbneo", "mame2003_plus", "mame2003"], "neogeo": ["fbneo"],
    "pce": ["mednafen_pce"], "pcecd": ["mednafen_pce"], "supergrafx": ["mednafen_pce"],
    "ngp": ["mednafen_ngp"], "ngpc": ["mednafen_ngp"], "wonderswan": ["mednafen_wswan"], "wsc": ["mednafen_wswan"],
    "coleco": ["gearcoleco"],
}


EJS_SYSTEM_ALIAS_BY_ROM_SYSTEM: Dict[str, str] = {
    "nes": "nes", "fds": "nes", "snes": "snes",
    "gb": "gb", "gbc": "gb", "gba": "gba", "vb": "vb", "n64": "n64", "nds": "nds",
    "atari5200": "a5200", "atari7800": "atari7800", "atari2600": "atari2600",
    "lynx": "lynx", "jaguar": "jaguar", "3do": "3do",
    "megadrive": "segaMD", "gamegear": "segaGG", "segacd": "segaCD",
    "mastersystem": "segaMS", "sega32x": "sega32x", "saturn": "segaSaturn",
    "psx": "psx", "psp": "psp",
    "arcade": "arcade", "neogeo": "arcade",
    "pce": "pce", "pcecd": "pce", "supergrafx": "pce",
    "ngp": "ngp", "ngpc": "ngp", "wonderswan": "ws", "wsc": "ws",
    "coleco": "coleco",
}


class CoreInfoManager:
    """EmulatorJS Stable 기준 코어 추천/실행 호환성 판단."""

    @classmethod
    def get_emulatorjs_system(cls, system_id: str, fallback: Optional[str] = None) -> Optional[str]:
        return EJS_SYSTEM_ALIAS_BY_ROM_SYSTEM.get((system_id or "").lower().strip(), fallback)

    @classmethod
    def get_cores_for_system(cls, system_id: str) -> List[CoreInfo]:
        normalized = (system_id or "").lower().strip()
        result: List[CoreInfo] = []
        for core_id in SYSTEM_CORE_ORDER.get(normalized, []):
            base = EMULATORJS_STABLE_CORES.get(core_id)
            if not base:
                continue
            result.append(replace(
                base,
                system_id=normalized,
                emulatorjs_system=cls.get_emulatorjs_system(normalized, base.emulatorjs_system) or base.emulatorjs_system,
                firmwares=_firmwares(normalized),
            ))
        return result

    @classmethod
    def _content_extensions(cls, rom_info: RomAnalysisResult) -> List[str]:
        # M3U는 플레이리스트 자체와 직접 참조 엔트리를 모두 해당 코어가 이해해야 한다.
        if rom_info.disc_info.disc_format == "M3U":
            exts = [".m3u"]
            for entry in rom_info.disc_info.playlist_entries:
                ext = os.path.splitext(entry)[1].lower()
                if ext and ext not in exts:
                    exts.append(ext)
            return exts
        return [rom_info.file_ext.lower()] if rom_info.file_ext else []

    @classmethod
    def _core_supports_result(cls, core: CoreInfo, rom_info: RomAnalysisResult) -> bool:
        exts = cls._content_extensions(rom_info)
        if not exts:
            return False

        # EmulatorJS가 ZIP/7z를 먼저 푸는 콘솔 ROM은 내부 판별이 이미 성공했다면 허용한다.
        if len(exts) == 1 and exts[0] in ARCHIVE_WRAPPERS and not rom_info.is_arcade and not rom_info.is_disc:
            return True
        return all(core.supports_extension(ext) for ext in exts)

    @classmethod
    def _arcade_core_is_compatible(cls, rom_info: RomAnalysisResult, core_id: str) -> bool:
        if not rom_info.is_arcade:
            return True
        compatibility = rom_info.arcade_info.core_compatibility.get(core_id)
        return compatibility is None or compatibility.supported

    @classmethod
    def get_compatible_cores(cls, rom_info: RomAnalysisResult) -> List[CoreInfo]:
        cores = cls.get_cores_for_system(rom_info.system_id)
        if rom_info.is_arcade and rom_info.arcade_info.recommended_cores:
            # 아케이드 롬셋은 코어별 ROM set 호환성이 중요하다.
            # 범용 "mame"/flycast 권장을 mame2003/fbneo로 임의 치환하지 않는다.
            stable_recommended = {
                core_id for core_id in rom_info.arcade_info.recommended_cores
                if core_id in EMULATORJS_STABLE_CORES
            }
            if not stable_recommended:
                return []
            cores = [core for core in cores if core.core_id in stable_recommended]
        return [
            core for core in cores
            if cls._core_supports_result(core, rom_info)
            and cls._arcade_core_is_compatible(rom_info, core.core_id)
        ]

    @classmethod
    def get_recommended_core(cls, rom_info: RomAnalysisResult) -> Optional[CoreInfo]:
        compatible = cls.get_compatible_cores(rom_info)
        if not compatible:
            return None
        if rom_info.is_arcade and rom_info.arcade_info.recommended_cores:
            for preferred in rom_info.arcade_info.recommended_cores:
                for core in compatible:
                    if core.core_id == preferred:
                        return core
        return compatible[0]

    @classmethod
    def apply_bios_info(cls, rom_info: RomAnalysisResult) -> BiosInfo:
        requirement = EMULATORJS_BIOS_REQUIREMENTS.get((rom_info.system_id or "").lower().strip())
        if not requirement:
            return rom_info.bios_info
        bios_files = list(requirement.get("bios_files", []))
        mandatory = bool(requirement.get("mandatory", False))
        rom_info.bios_info = BiosInfo(
            needs_bios=mandatory,
            mandatory=mandatory,
            bios_files=bios_files,
            description=requirement.get("description"),
        )
        return rom_info.bios_info

    @classmethod
    def apply_emulatorjs_info(cls, rom_info: RomAnalysisResult) -> EmulatorJsInfo:
        all_cores = cls.get_cores_for_system(rom_info.system_id)
        compatible = cls.get_compatible_cores(rom_info)
        selected = cls.get_recommended_core(rom_info)
        content_exts = cls._content_extensions(rom_info)
        media_ready = rom_info.is_playable and (not rom_info.is_disc or rom_info.disc_info.is_complete)

        if selected:
            info = EmulatorJsInfo(
                supported=media_ready,
                system_supported=True,
                stable_version=EMULATORJS_STABLE_VERSION,
                system=cls.get_emulatorjs_system(rom_info.system_id, selected.emulatorjs_system),
                core=selected.core_id,
                alternative_cores=[c.core_id for c in compatible if c.core_id != selected.core_id],
                supported_extensions=list(selected.supported_extensions),
                content_extensions=content_exts,
                requires_threads=selected.requires_threads,
                reason=(
                    None if media_ready else
                    next(
                        (
                            warning for warning in rom_info.warnings
                            if "코어가 직접 실행할 수 있는 ROM 이미지" in warning
                        ),
                        "파일 형식은 EmulatorJS Stable 코어와 호환되지만 ROM/디스크 분석 결과가 불완전하거나 실행 불가 상태입니다.",
                    )
                ),
            )
        elif all_cores:
            # 기종은 지원하지만 해당 롬셋의 권장 코어나 파일 형식/게임별 드라이버 상태가 Stable과 맞지 않는다.
            stable_arcade_recommendations = []
            blocked_arcade_cores = []
            if rom_info.is_arcade:
                stable_arcade_recommendations = [
                    core_id for core_id in rom_info.arcade_info.recommended_cores
                    if core_id in EMULATORJS_STABLE_CORES
                ]
                blocked_arcade_cores = [
                    (core_id, compatibility.driver_status)
                    for core_id, compatibility in rom_info.arcade_info.core_compatibility.items()
                    if core_id in EMULATORJS_STABLE_CORES and not compatibility.supported
                ]

            if blocked_arcade_cores and not stable_arcade_recommendations:
                game_name = rom_info.header_metadata.title or rom_info.arcade_info.driver or rom_info.file_name
                details = "; ".join(f"{core_id}: {status}" for core_id, status in blocked_arcade_cores)
                reason = (
                    f"{game_name}는 MAME2003 계열 게임별 호환성 표에서 실행 불가로 기록되어 있습니다 "
                    f"({details})."
                )
            elif rom_info.is_arcade and rom_info.arcade_info.recommended_cores and not stable_arcade_recommendations:
                reason = (
                    f"이 아케이드 롬셋의 권장 코어({', '.join(rom_info.arcade_info.recommended_cores)})가 "
                    f"EmulatorJS Stable {EMULATORJS_STABLE_VERSION}에 없습니다."
                )
            else:
                reason = f"EmulatorJS Stable {EMULATORJS_STABLE_VERSION} 코어가 현재 콘텐츠 확장자 조합을 지원하지 않습니다."
            info = EmulatorJsInfo(
                supported=False,
                system_supported=True,
                stable_version=EMULATORJS_STABLE_VERSION,
                system=cls.get_emulatorjs_system(rom_info.system_id, all_cores[0].emulatorjs_system),
                core=None,
                alternative_cores=[],
                supported_extensions=sorted({ext for c in all_cores for ext in c.supported_extensions}),
                content_extensions=content_exts,
                requires_threads=any(c.requires_threads for c in all_cores),
                reason=reason,
            )
        else:
            info = EmulatorJsInfo(
                supported=False,
                system_supported=False,
                stable_version=EMULATORJS_STABLE_VERSION,
                content_extensions=content_exts,
                reason=f"{rom_info.system_id} 기종은 현재 rom-analyzer의 EmulatorJS Stable {EMULATORJS_STABLE_VERSION} 지원 범위 밖입니다.",
            )

        rom_info.emulatorjs = info
        return info
