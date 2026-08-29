# -*- coding: utf-8 -*-
"""
콘솔 및 핸드헬드 카트리지 ROM 통합 탐지기.
Nintendo, Sega, NEC, Atari 등 바이너리 헤더를 읽어 기종 식별.
"""

import os
import logging
import zipfile
from typing import Optional

from ..models import RomAnalysisResult
from .nintendo import NintendoHeaderDetector
from .sega import SegaHeaderDetector
from .misc import MiscHeaderDetector

logger = logging.getLogger(__name__)


class ConsoleHeaderDetector:
    """콘솔/핸드헬드 롬 바이너리 헤더 분석 총괄"""

    @classmethod
    def detect(cls, file_path: str) -> Optional[RomAnalysisResult]:
        if not os.path.exists(file_path):
            return None

        ext = os.path.splitext(file_path)[1].lower()

        # 1. ZIP 아카이브 내부에 단일 콘솔 롬이 들어있는 경우 처리
        if ext == ".zip":
            try:
                if zipfile.is_zipfile(file_path):
                    with zipfile.ZipFile(file_path, "r") as z:
                        infolist = [f for f in z.infolist() if not f.is_dir()]
                        if len(infolist) == 1 or (len(infolist) <= 3 and any(os.path.splitext(f.filename)[1].lower() in [".nes", ".smc", ".sfc", ".gba", ".gb", ".gbc", ".nds", ".n64", ".z64", ".md", ".pce"] for f in infolist)):
                            # 주요 롬 파일 선택
                            target_info = infolist[0]
                            for f in infolist:
                                if os.path.splitext(f.filename)[1].lower() in [".nes", ".smc", ".sfc", ".gba", ".gb", ".gbc", ".nds", ".n64", ".z64", ".md", ".pce"]:
                                    target_info = f
                                    break

                            with z.open(target_info) as zf:
                                data = zf.read(1024 * 1024)  # 1MB 헤더 데이터
                                result = cls._detect_from_data(target_info.filename, data, total_size=target_info.file_size)
                                if result:
                                    # 원본 ZIP 파일 경로 유지
                                    result.file_path = file_path
                                    result.file_name = os.path.basename(file_path)
                                    result.file_size = os.path.getsize(file_path)
                                    result.file_ext = ".zip"
                                    result.summary = f"[ZIP 압축 콘솔 롬] {result.summary} (내부 파일: {target_info.filename})"
                                    return result
            except Exception as exc:
                logger.debug("console ZIP inspection failed for %s: %s", file_path, exc, exc_info=True)

        # 2. 일반 파일인 경우 앞부분 바이너리 읽기
        try:
            with open(file_path, "rb") as f:
                data = f.read(1024 * 1024)
                return cls._detect_from_data(file_path, data, total_size=os.path.getsize(file_path))
        except Exception as exc:
            logger.debug("console header read/detection failed for %s: %s", file_path, exc, exc_info=True)
            return None

    @classmethod
    def _detect_from_data(cls, virtual_path: str, data: bytes, total_size: Optional[int] = None) -> Optional[RomAnalysisResult]:
        """바이너리 데이터 및 경로로부터 기종 탐지"""
        if not data:
            return None

        # 1. 강한 바이너리 시그니처를 제조사/확장자보다 우선한다.
        for detector, kwargs in [
            (NintendoHeaderDetector, {"total_size": total_size, "signature_only": True}),
            (SegaHeaderDetector, {"signature_only": True, "total_size": total_size}),
            (MiscHeaderDetector, {"signature_only": True}),
        ]:
            res = detector.detect(virtual_path, data, **kwargs)
            if res:
                return res

        # 2. 강한 시그니처가 없으면 확장자/구조 힌트를 허용한다.
        res = NintendoHeaderDetector.detect(virtual_path, data, total_size=total_size)
        if res:
            return res

        res = SegaHeaderDetector.detect(virtual_path, data, total_size=total_size)
        if res:
            return res

        res = MiscHeaderDetector.detect(virtual_path, data)
        if res:
            return res

        return None
