# -*- coding: utf-8 -*-
"""rom_database가 반환하는 해석 전 원시 참조 데이터 모델."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CoreCompatibilityRecord:
    core_id: str
    rom_name: str
    description: str
    driver_status: str
    color_status: Optional[str] = None
    sound_status: Optional[str] = None
    graphics_status: Optional[str] = None
    samples: Optional[str] = None
    bios_required: bool = False
    source_url: Optional[str] = None
