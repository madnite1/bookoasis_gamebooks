# -*- coding: utf-8 -*-
"""
ROM Analyzer 데이터 모델.
분석 결과 객체 및 세부 정보 데이터클래스 정의.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any

from .emulatorjs_config import EMULATORJS_STABLE_VERSION


@dataclass
class DetectionEvidence:
    """판별 근거와 개별 신뢰도."""
    method: str
    confidence: float
    detail: str = ""
    source: str = ""


@dataclass
class ArcadeCoreCompatibility:
    """특정 아케이드 롬셋과 EmulatorJS 아케이드 코어의 실행 호환성."""
    core_id: str
    rom_name: str
    description: str = ""
    supported: bool = False
    driver_status: str = "unknown"
    color_status: Optional[str] = None
    sound_status: Optional[str] = None
    graphics_status: Optional[str] = None
    samples: Optional[str] = None
    bios_required: bool = False
    source_url: Optional[str] = None


@dataclass
class ArcadeInfo:
    """아케이드(MAME / FBNeo) 전용 분석 정보"""
    is_arcade: bool = False
    driver: Optional[str] = None                    # 드라이버/롬셋명 (예: sf2, kof98, pacman)
    board: Optional[str] = None                     # 기판/하드웨어 (예: Capcom CPS-1, SNK Neo-Geo MVS, Sega NAOMI)
    parent_rom: Optional[str] = None                # 부모 롬셋명 (클론인 경우 부모 롬셋, 예: sf2)
    is_clone: bool = False                          # 클론 롬셋 여부
    is_bios_set: bool = False                       # 바이오스 파일 자체인지 여부 (예: neogeo.zip, pgm.zip)
    is_device_set: bool = False                     # 장치/사운드칩 롬셋 여부 (예: qsound.zip)
    needs_bios: bool = False                        # 구동 시 바이오스 롬셋이 필수/권장되는지 여부
    required_bios: List[str] = field(default_factory=list)  # 필요한 바이오스 파일명 목록 (예: ['neogeo.zip'])
    needs_chd: bool = False                         # CHD(하드디스크/CD 이미지) 필수 여부 (예: sfiii3, naomi 게임 등)
    chd_name: Optional[str] = None                  # 필요한 CHD 파일명 또는 디렉터리
    recommended_cores: List[str] = field(default_factory=list)  # 추천 에뮬레이터 코어 (예: ['fbneo', 'mame2003_plus'])
    core_compatibility: Dict[str, ArcadeCoreCompatibility] = field(default_factory=dict)  # 코어별 실제 게임 호환성
    matched_count: int = 0
    total_roms: int = 0
    match_rate: float = 0.0
    archive_match_rate: float = 0.0
    dat_missing_count: int = 0
    dat_extra_count: int = 0
    dat_system: Optional[str] = None
    dat_status: Optional[str] = None
    dat_score: float = 0.0
    dat_candidate_count: int = 0


@dataclass
class DiscEntryInfo:
    """멀티디스크 세트 안의 개별 디스크 판별 정보."""
    path: str
    disc_format: Optional[str] = None
    system_id: str = "unknown"
    system_name: Optional[str] = None
    title: Optional[str] = None
    serial: Optional[str] = None
    region: Optional[str] = None
    confidence_score: float = 0.0
    identity_status: str = "unknown"
    is_complete: bool = True
    is_playable: bool = False
    detection_methods: List[str] = field(default_factory=list)


@dataclass
class DiscInfo:
    """디스크 기반(CD/DVD/GD-ROM) 미디어 분석 정보"""
    is_disc: bool = False
    disc_format: Optional[str] = None               # CUE/BIN, GDI, CHD, ISO, CCD/IMG, MDS/MDF, PBP, RVZ, WBFS
    is_multi_file: bool = False                     # 여러 보조 트랙 파일로 구성된 미디어인지 여부
    is_complete: bool = True                        # 필요한 모든 트랙/보조 파일이 존재하는지 여부
    referenced_files: List[str] = field(default_factory=list)  # CUE/GDI 등에서 참조하는 트랙 파일 목록
    missing_files: List[str] = field(default_factory=list)     # 누락된 트랙/사이드카 파일 목록
    track_count: int = 0                            # 인식된 트랙 수(또는 M3U의 디스크 엔트리 수)
    companion_extensions: List[str] = field(default_factory=list)  # 세트 확장자 목록 (예: ['.cue', '.bin'])
    playlist_entries: List[str] = field(default_factory=list)      # M3U가 직접 참조하는 디스크 엔트리
    disc_count: int = 0                              # 멀티디스크 플레이리스트의 디스크 수
    disc_entries: List[DiscEntryInfo] = field(default_factory=list) # 각 디스크의 개별 identity/상태
    container_metadata: Dict[str, Any] = field(default_factory=dict) # CHD 등 컨테이너 구조 메타데이터


@dataclass
class BiosInfo:
    """바이오스 요구사항 정보"""
    needs_bios: bool = False                        # 바이오스 필요 여부
    mandatory: bool = False                         # 필수 바이오스인지 (없으면 구동 불가)
    bios_files: List[str] = field(default_factory=list)  # 필요한 바이오스 파일명 (예: ['scph5501.bin', 'dc_boot.bin'])
    description: Optional[str] = None               # 바이오스 설명 및 안내


@dataclass
class HeaderMetadata:
    """ROM 내부 바이너리 헤더 추출 메타데이터"""
    title: Optional[str] = None                     # ROM 헤더에 기록된 타이틀
    serial: Optional[str] = None                    # 시리얼 / 프로덕트 코드 (예: SLUS-00594, NUS-CZLE-USA)
    region: Optional[str] = None                    # 지역 (NTSC-U, PAL, NTSC-J, World 등)
    version: Optional[str] = None                   # 롬 버전 (예: 1.0, 1.1, Rev A)
    developer: Optional[str] = None                 # 개발사/제조사 코드 (예: Nintendo, Capcom, Sega)
    header_type: Optional[str] = None               # 헤더 규격 (예: iNES, NES 2.0, SMC, LoROM, HiROM, IP.BIN, SYSTEM.CNF)
    internal_checksum: Optional[str] = None         # 헤더 내 자체 체크섬 값
    cartridge_hardware: Optional[str] = None        # 카트리지 특수 칩 (예: SuperFX, SA-1, DSP-1, MMC3, MBC5)


@dataclass
class EmulatorJsInfo:
    """EmulatorJS Stable에서 현재 분석 결과를 실행할 수 있는지에 대한 정보."""
    supported: bool = False                         # 이 파일/세트를 Stable에서 직접 실행 가능한지
    system_supported: bool = False                  # 기종 자체가 Stable 지원 대상인지
    stable_version: str = EMULATORJS_STABLE_VERSION # 검증 기준 EmulatorJS Stable 버전
    system: Optional[str] = None                    # EJS_core에 사용할 시스템 별칭 (예: psx, segaSaturn)
    core: Optional[str] = None                      # 추천 실제 코어 (예: pcsx_rearmed)
    alternative_cores: List[str] = field(default_factory=list)
    supported_extensions: List[str] = field(default_factory=list)
    content_extensions: List[str] = field(default_factory=list)
    requires_threads: bool = False
    reason: Optional[str] = None


@dataclass
class RomAnalysisResult:
    """ROM 분석 최종 결과 객체"""
    # 기본 파일 정보
    file_path: str
    file_name: str
    file_size: int
    file_ext: str

    # 기종 및 플랫폼 식별 정보
    system_id: str                                  # 고유 식별자 (예: arcade, snes, nes, megadrive, psx, ps2, saturn, dreamcast, gba, n64 등)
    system_name: str                                # 사람이 읽기 편한 이름 (예: Capcom CPS-2, Sony PlayStation, Super Nintendo)
    system_type: str                                # arcade | console | handheld | computer | unknown
    platform_slug: str                              # Libretro / IGDB / ScreenScraper 호환 슬러그 (예: snes, megadrive, psx)
    libretro_system: Optional[str] = None           # Libretro CDN / Thumbnails 시스템명 (예: Sony_-_PlayStation)
    emulatorjs: EmulatorJsInfo = field(default_factory=EmulatorJsInfo)

    # 분류 플래그
    is_arcade: bool = False
    is_disc: bool = False
    is_playable: bool = True                        # 플레이 가능한 게임 롬인지 (바이오스 자체 파일이거나 필수 트랙 누락 시 False)
    confidence: str = "medium"                      # high | medium | low
    confidence_score: float = 0.0                   # 0.0 ~ 1.0 정량 신뢰도

    # 세부 정보
    arcade_info: ArcadeInfo = field(default_factory=ArcadeInfo)
    disc_info: DiscInfo = field(default_factory=DiscInfo)
    bios_info: BiosInfo = field(default_factory=BiosInfo)
    header_metadata: HeaderMetadata = field(default_factory=HeaderMetadata)

    # 해시 정보
    crc32: Optional[str] = None
    md5: Optional[str] = None
    sha1: Optional[str] = None

    # 판별 근거
    evidence: List[DetectionEvidence] = field(default_factory=list)
    detection_methods: List[str] = field(default_factory=list)
    primary_evidence: Optional[DetectionEvidence] = None
    identity_status: str = "unknown"                # exact | strong | partial | ambiguous | unknown
    conflicts: List[str] = field(default_factory=list)
    alternatives: List[Dict[str, Any]] = field(default_factory=list)

    # 요약 문구
    summary: str = ""
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def __post_init__(self):
        """기존 confidence 문자열 API를 유지하면서 정량 점수를 자동 보강."""
        if self.confidence_score <= 0.0:
            self.confidence_score = {"high": 0.95, "medium": 0.65, "low": 0.25}.get((self.confidence or "").lower(), 0.5)
        self.confidence_score = max(0.0, min(1.0, float(self.confidence_score)))
        if self.evidence:
            self.confidence_score = max(self.confidence_score, max(e.confidence for e in self.evidence))
        if not self.detection_methods and self.evidence:
            self.detection_methods = list(dict.fromkeys(e.method for e in self.evidence))
        if self.confidence_score >= 0.90:
            self.confidence = "high"
        elif self.confidence_score >= 0.55:
            self.confidence = "medium"
        else:
            self.confidence = "low"

    def add_warning(self, message: str):
        if message and message not in self.warnings:
            self.warnings.append(message)

    def add_error(self, message: str):
        if message and message not in self.errors:
            self.errors.append(message)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화 가능한 딕셔너리로 변환"""
        return asdict(self)

    @property
    def needs_bios(self) -> bool:
        """바이오스가 필요한지 간편 확인"""
        return self.bios_info.needs_bios or self.arcade_info.needs_bios

    @property
    def is_missing_files(self) -> bool:
        """디스크 등의 추가 파일이 누락되었는지 확인"""
        return bool(self.disc_info.missing_files)
