# -*- coding: utf-8 -*-
"""DAT/CRC based archive matcher with ranked multi-candidate scoring."""

from dataclasses import dataclass, field
from functools import lru_cache
import logging
import math
import os
import sqlite3
import zipfile
import zlib
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)
_DAT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "arcade_dat.db")
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
        return os.path.isfile(_DAT_DB_PATH)

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
        return cls._match_archive_cached(os.path.abspath(file_path), stat.st_size, stat.st_mtime_ns)

    @classmethod
    @lru_cache(maxsize=256)
    def _match_archive_cached(cls, file_path: str, _size: int, _mtime_ns: int) -> Optional[DatMatchResult]:
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
        return cls._match_file_cached(os.path.abspath(file_path), stat.st_size, stat.st_mtime_ns)

    @classmethod
    @lru_cache(maxsize=256)
    def _match_file_cached(cls, file_path: str, _size: int, _mtime_ns: int) -> Optional[DatMatchResult]:
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
    def _connect(cls):
        con = sqlite3.connect(f"file:{_DAT_DB_PATH}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        return con

    @classmethod
    def _crc_frequencies(cls, con, crcs: Sequence[str]) -> Dict[str, int]:
        if not crcs:
            return {}
        marks = ",".join("?" for _ in crcs)
        rows = con.execute(
            f"SELECT crc32, COUNT(DISTINCT game_id) AS n FROM roms WHERE crc32 IN ({marks}) GROUP BY crc32",
            list(crcs),
        ).fetchall()
        return {str(r["crc32"]).lower(): int(r["n"] or 0) for r in rows}

    @staticmethod
    def _rarity_weight(freq: int) -> float:
        # Unique CRC=1.0; shared CRCs rapidly lose identifying power.
        if freq <= 1:
            return 1.0
        return 1.0 / math.log2(freq + 1.0)

    @classmethod
    def _expected_crc_map(cls, con, rows) -> Dict[int, set]:
        game_ids = [int(row["id"]) for row in rows]
        if not game_ids:
            return {}
        marks = ",".join("?" for _ in game_ids)
        result: Dict[int, set] = {gid: set() for gid in game_ids}
        for row in con.execute(
            f"SELECT game_id, crc32 FROM roms WHERE game_id IN ({marks}) AND crc32 IS NOT NULL AND crc32!=''",
            game_ids,
        ).fetchall():
            result[int(row["game_id"])].add(str(row["crc32"]).lower())
        return result

    @classmethod
    def _build_candidate(cls, con, row, archive_crcs: Sequence[str], stem: str,
                         crc_freq: Dict[str, int], expected_crcs=None) -> DatCandidate:
        gid = int(row["id"])
        if expected_crcs is None:
            expected = con.execute(
                "SELECT crc32 FROM roms WHERE game_id=? AND crc32 IS NOT NULL AND crc32!=''",
                (gid,),
            ).fetchall()
            expected_crcs = {str(r[0]).lower() for r in expected}
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
    def _candidate_rows(cls, con, stem: str, crcs: Sequence[str], unique_crcs: Sequence[str]):
        rows = {}
        # Exact archive name always remains a candidate, even with zero/one CRC hit.
        for row in con.execute("SELECT * FROM games WHERE name=?", (stem,)).fetchall():
            rows[int(row["id"])] = row
        if crcs:
            marks = ",".join("?" for _ in crcs)
            # 먼저 roms 테이블에서 game_id별 hit만 집계하고, games 정보는 결과에
            # 필요한 후보만 후속 조회한다. 큰 g.* JOIN/GROUP BY보다 훨씬 저렴하다.
            hit_rows = con.execute(
                f"""SELECT game_id, COUNT(DISTINCT crc32) AS hits
                    FROM roms
                    WHERE crc32 IN ({marks})
                    GROUP BY game_id HAVING hits >= 2""",
                list(crcs),
            ).fetchall()
            hit_map = {int(r["game_id"]): int(r["hits"] or 0) for r in hit_rows}
            game_ids = list(hit_map)
            game_rows = []
            for start in range(0, len(game_ids), 800):
                chunk = game_ids[start:start + 800]
                chunk_marks = ",".join("?" for _ in chunk)
                game_rows.extend(con.execute(
                    f"SELECT * FROM games WHERE id IN ({chunk_marks})", chunk
                ).fetchall())
            game_rows.sort(key=lambda r: (
                -hit_map.get(int(r["id"]), 0),
                str(r["name"] or ""),
                str(r["system_name"] or ""),
            ))
            for row in game_rows[:_MAX_CRC_CANDIDATES]:
                rows[int(row["id"])] = row
        if unique_crcs:
            marks = ",".join("?" for _ in unique_crcs)
            # A globally unique CRC can identify a renamed single-ROM image by itself.
            for row in con.execute(
                f"SELECT DISTINCT g.* FROM roms r JOIN games g ON r.game_id=g.id WHERE r.crc32 IN ({marks})",
                list(unique_crcs),
            ).fetchall():
                rows[int(row["id"])] = row
        return list(rows.values())

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
                elif best.dat_coverage >= 0.999 and best.archive_coverage >= 0.95:
                    status = "exact"
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
