# -*- coding: utf-8 -*-
"""DAT/CRC based archive matcher with ranked multi-candidate scoring."""

from dataclasses import dataclass, field
from functools import lru_cache
import logging
import math
import os
import zipfile
import zlib
from typing import Dict, List, Optional, Sequence

from rom_database.paths import DatabasePaths
from rom_database.repositories.dat import DatRepository

from ..database_context import get_active_database

logger = logging.getLogger(__name__)
# 테스트/외부 도구의 기존 경로 주입 계약을 유지하면서 기본값만 rom_database로 이동한다.
_DEFAULT_DAT_DB_PATH = str(DatabasePaths.default().dat)
_DAT_DB_PATH = _DEFAULT_DAT_DB_PATH
_SOURCE_PRIORITY = {"FBNeo": 1.0, "MAME2003Plus": 0.8, "MAME": 0.7, "MAME_Softlist": 0.6}
_AMBIGUOUS_GAP = 0.035
_MAX_CRC_CANDIDATES = 64


@dataclass
class DatCandidate:
    game_id: int
    rom_name: str
    title: str
    platform: str
    system_name: str
    parent_rom: Optional[str] = None
    romof: Optional[str] = None
    is_clone: bool = False
    matched_count: int = 0
    total_roms: int = 0
    archive_crc_count: int = 0
    matched_crcs: List[str] = field(default_factory=list)
    dat_coverage: float = 0.0
    archive_coverage: float = 0.0
    weighted_crc_score: float = 0.0
    exact_name: bool = False
    source_priority: float = 0.0
    score: float = 0.0


@dataclass
class DatMatchResult:
    matched: bool = False
    rom_name: str = ""
    title: str = ""
    platform: str = ""
    system_name: str = ""
    parent_rom: Optional[str] = None
    romof: Optional[str] = None
    is_clone: bool = False
    matched_count: int = 0
    total_roms: int = 0
    match_rate: float = 0.0
    archive_crc_count: int = 0
    archive_match_rate: float = 0.0
    missing_count: int = 0
    extra_count: int = 0
    matched_crcs: List[str] = field(default_factory=list)
    match_basis: str = ""
    score: float = 0.0
    status: str = "none"  # exact | strong | partial | ambiguous | name | none
    candidates: List[DatCandidate] = field(default_factory=list)
    candidate_count: int = 0

    @property
    def confidence_score(self) -> float:
        if not self.matched:
            return 0.0
        if self.status == "ambiguous":
            return min(0.74, max(0.60, self.score))
        if self.status == "name":
            return 0.68
        # Coverage is primary; rarity/source are tie-breakers rather than confidence killers.
        if self.status == "exact":
            return 0.99
        coverage_conf = ((self.match_rate / 100.0) * 0.55 + (self.archive_match_rate / 100.0) * 0.45) * 0.98
        if self.status == "strong":
            return max(0.90, min(0.97, max(self.score, coverage_conf)))
        return max(0.72, min(0.89, max(self.score, coverage_conf * 0.92)))


class DatMatcher:
    @classmethod
    def is_available(cls) -> bool:
        return cls._repository().is_available()

    @classmethod
    def collect_archive_crcs(cls, file_path: str) -> List[str]:
        ext = os.path.splitext(file_path)[1].lower()
        crcs: List[str] = []
        try:
            if ext == ".zip":
                if not zipfile.is_zipfile(file_path):
                    return []
                with zipfile.ZipFile(file_path, "r") as zf:
                    for info in zf.infolist():
                        if info.is_dir() or info.file_size <= 0 or info.filename.startswith("."):
                            continue
                        crcs.append(f"{info.CRC:08x}".lower())
            elif ext == ".7z":
                import py7zr
                with py7zr.SevenZipFile(file_path, mode="r") as zf:
                    for info in zf.list():
                        crc = getattr(info, "crc32", None)
                        if not getattr(info, "is_directory", False) and crc is not None:
                            crcs.append(f"{int(crc):08x}".lower())
        except Exception as exc:
            logger.debug("DAT CRC collection failed for %s: %s", file_path, exc, exc_info=True)
        return list(dict.fromkeys(crcs))

    @classmethod
    def match_archive(cls, file_path: str) -> Optional[DatMatchResult]:
        if not cls.is_available():
            return None
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        database_key = str(cls._repository().db_path.resolve())
        return cls._match_archive_cached(
            os.path.abspath(file_path), stat.st_size, stat.st_mtime_ns, database_key
        )

    @classmethod
    @lru_cache(maxsize=256)
    def _match_archive_cached(
        cls, file_path: str, _size: int, _mtime_ns: int, _database_key: str
    ) -> Optional[DatMatchResult]:
        stem = os.path.splitext(os.path.basename(file_path))[0].lower().strip()
        return cls.match(stem, cls.collect_archive_crcs(file_path))

    @classmethod
    def collect_file_crc(cls, file_path: str) -> Optional[str]:
        """단일 카트리지 ROM의 CRC32를 스트리밍 계산한다."""
        crc = 0
        try:
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    crc = zlib.crc32(chunk, crc)
            return f"{crc & 0xFFFFFFFF:08x}"
        except OSError as exc:
            logger.debug("DAT file CRC collection failed for %s: %s", file_path, exc, exc_info=True)
            return None

    @classmethod
    def match_file(cls, file_path: str) -> Optional[DatMatchResult]:
        if not cls.is_available() or not os.path.isfile(file_path):
            return None
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        database_key = str(cls._repository().db_path.resolve())
        return cls._match_file_cached(
            os.path.abspath(file_path), stat.st_size, stat.st_mtime_ns, database_key
        )

    @classmethod
    @lru_cache(maxsize=256)
    def _match_file_cached(
        cls, file_path: str, _size: int, _mtime_ns: int, _database_key: str
    ) -> Optional[DatMatchResult]:
        crc = cls.collect_file_crc(file_path)
        if not crc:
            return None
        stem = os.path.splitext(os.path.basename(file_path))[0].lower().strip()
        return cls.match(stem, [crc])

    @classmethod
    def clear_cache(cls):
        """테스트/장기 실행 프로세스에서 DAT 파일 매치 캐시를 명시적으로 비운다."""
        cls._match_archive_cached.cache_clear()
        cls._match_file_cached.cache_clear()

    @classmethod
    def match(cls, stem: str, crcs: Sequence[str]) -> Optional[DatMatchResult]:
        """Match a normalized archive stem and CRC list. Public mainly for tests/tools."""
        if not cls.is_available():
            return None
        return cls._match((stem or "").lower().strip(), [str(c).lower() for c in crcs if c])

    @classmethod
    def _repository(cls) -> DatRepository:
        # 기존 테스트가 _DAT_DB_PATH를 임시 DB로 교체하는 계약은 유지한다.
        # 기본 경로일 때는 현재 분석 컨텍스트에 주입된 RomDatabase의 DAT 저장소를 사용한다.
        if _DAT_DB_PATH != _DEFAULT_DAT_DB_PATH:
            return DatRepository(_DAT_DB_PATH)
        return get_active_database().dat

    @classmethod
    def _connect(cls) -> DatRepository:
        """과거 내부 호출 형태를 유지하는 repository 별칭."""
        return cls._repository()

    @classmethod
    def _crc_frequencies(cls, repository: DatRepository, crcs: Sequence[str]) -> Dict[str, int]:
        return repository.crc_frequencies(crcs)

    @staticmethod
    def _rarity_weight(freq: int) -> float:
        # Unique CRC=1.0; shared CRCs rapidly lose identifying power.
        if freq <= 1:
            return 1.0
        return 1.0 / math.log2(freq + 1.0)

    @classmethod
    def _expected_crc_map(cls, repository: DatRepository, rows) -> Dict[int, set]:
        game_ids = [int(row["id"]) for row in rows]
        return repository.expected_crc_map(game_ids)

    @classmethod
    def _build_candidate(cls, repository: DatRepository, row, archive_crcs: Sequence[str], stem: str,
                         crc_freq: Dict[str, int], expected_crcs=None) -> DatCandidate:
        gid = int(row["id"])
        if expected_crcs is None:
            expected_crcs = repository.expected_crc_map([gid]).get(gid, set())
        archive_set = set(archive_crcs)
        matched = sorted(expected_crcs & archive_set)
        total = len(expected_crcs)
        matched_count = len(matched)
        archive_count = len(archive_set)
        dat_cov = matched_count / total if total else 0.0
        archive_cov = matched_count / archive_count if archive_count else 0.0
        weighted = (
            sum(cls._rarity_weight(crc_freq.get(c, 1)) for c in matched) / matched_count
            if matched_count else 0.0
        )
        exact_name = str(row["name"] or "").lower() == stem
        source_priority = _SOURCE_PRIORITY.get(str(row["system_name"] or ""), 0.5)

        # Coverage dominates; name/source only break otherwise similar real CRC evidence.
        score = (
            dat_cov * 0.48
            + archive_cov * 0.32
            + weighted * 0.08
            + (1.0 if exact_name else 0.0) * 0.09
            + source_priority * 0.03
        )
        return DatCandidate(
            game_id=gid,
            rom_name=row["name"] or "",
            title=row["description"] or row["name"] or "",
            platform=row["platform"] or "",
            system_name=row["system_name"] or "",
            parent_rom=row["cloneof"] or None,
            romof=row["romof"] or None,
            is_clone=bool(row["cloneof"]),
            matched_count=matched_count,
            total_roms=total,
            archive_crc_count=archive_count,
            matched_crcs=matched,
            dat_coverage=dat_cov,
            archive_coverage=archive_cov,
            weighted_crc_score=weighted,
            exact_name=exact_name,
            source_priority=source_priority,
            score=round(score, 6),
        )

    @classmethod
    def _candidate_rows(
        cls,
        repository: DatRepository,
        stem: str,
        crcs: Sequence[str],
        unique_crcs: Sequence[str],
    ):
        return repository.candidate_rows(
            stem,
            crcs,
            unique_crcs,
            max_crc_candidates=_MAX_CRC_CANDIDATES,
        )

    @classmethod
    def _to_result(cls, best: DatCandidate, candidates: List[DatCandidate], status: str) -> DatMatchResult:
        basis = "stem+crc" if best.exact_name and best.matched_count else ("crc" if best.matched_count else "stem")
        return DatMatchResult(
            matched=True,
            rom_name=best.rom_name,
            title=best.title,
            platform=best.platform,
            system_name=best.system_name,
            parent_rom=best.parent_rom,
            romof=best.romof,
            is_clone=best.is_clone,
            matched_count=best.matched_count,
            total_roms=best.total_roms or 1,
            match_rate=round(best.dat_coverage * 100.0, 2),
            archive_crc_count=best.archive_crc_count,
            archive_match_rate=round(best.archive_coverage * 100.0, 2),
            missing_count=max(0, best.total_roms - best.matched_count),
            extra_count=max(0, best.archive_crc_count - best.matched_count),
            matched_crcs=best.matched_crcs,
            match_basis=basis,
            score=best.score,
            status=status,
            candidates=candidates[:8],
            candidate_count=len(candidates),
        )

    @classmethod
    def _match(cls, stem: str, crcs: List[str]) -> Optional[DatMatchResult]:
        try:
            with cls._connect() as con:
                freq = cls._crc_frequencies(con, crcs)
                unique_crcs = [crc for crc, count in freq.items() if count == 1]
                rows = cls._candidate_rows(con, stem, crcs, unique_crcs)
                if not rows:
                    return None
                expected_map = cls._expected_crc_map(con, rows)
                candidates = [
                    cls._build_candidate(con, r, crcs, stem, freq, expected_map.get(int(r["id"]), set()))
                    for r in rows
                ]

                crc_candidates = [c for c in candidates if c.matched_count > 0]
                if not crc_candidates:
                    name_candidates = [c for c in candidates if c.exact_name]
                    if not name_candidates:
                        return None
                    name_candidates.sort(key=lambda c: (-c.source_priority, c.system_name, c.rom_name))
                    best = name_candidates[0]
                    best.score = 0.68
                    return cls._to_result(best, name_candidates, "name")

                crc_candidates.sort(
                    key=lambda c: (-c.score, -c.matched_count, -c.archive_coverage, -c.dat_coverage, c.rom_name, c.system_name)
                )
                best = crc_candidates[0]
                second = crc_candidates[1] if len(crc_candidates) > 1 else None

                # Near-ties across different identities are ambiguous. The same ROM name
                # represented by multiple DAT sources is a source-selection issue, not identity ambiguity.
                ambiguous = bool(
                    second
                    and second.rom_name.lower() != best.rom_name.lower()
                    and (best.score - second.score) < _AMBIGUOUS_GAP
                )
                if ambiguous:
                    status = "ambiguous"
                elif (
                    best.dat_coverage >= 0.999
                    and (
                        best.archive_coverage >= 0.95
                        or (best.exact_name and best.matched_count >= 2)
                    )
                ):
                    status = "exact"
                elif best.dat_coverage >= 0.999 and best.matched_count >= 2:
                    # 필수 DAT ROM이 모두 있으면 추가 파일 때문에 archive coverage가 낮아져도
                    # 게임 identity 자체는 강하게 유지한다. exact 파일명까지 맞을 때만 exact로 승격한다.
                    status = "strong"
                elif (
                    best.matched_count >= 2
                    and best.dat_coverage >= 0.85
                    and best.archive_coverage >= 0.85
                ):
                    status = "strong"
                else:
                    status = "partial"
                return cls._to_result(best, crc_candidates, status)
        except Exception as exc:
            logger.debug("DAT match query failed for %s: %s", stem, exc, exc_info=True)
        return None
