# -*- coding: utf-8 -*-
"""DAT SQLite의 원시 후보/CRC 정보 조회 계층."""

from pathlib import Path
from typing import Dict, List, Sequence, Set, Union

from ..connection import open_readonly

PathLike = Union[str, Path]


class DatRepository:
    def __init__(self, db_path: PathLike):
        self.db_path = Path(db_path)

    def __enter__(self) -> "DatRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def is_available(self) -> bool:
        return self.db_path.is_file()

    def crc_frequencies(self, crcs: Sequence[str]) -> Dict[str, int]:
        if not crcs or not self.is_available():
            return {}
        connection = open_readonly(self.db_path)
        if connection is None:
            return {}
        try:
            marks = ",".join("?" for _ in crcs)
            rows = connection.execute(
                f"SELECT crc32, COUNT(DISTINCT game_id) AS n FROM roms WHERE crc32 IN ({marks}) GROUP BY crc32",
                list(crcs),
            ).fetchall()
            return {str(row["crc32"]).lower(): int(row["n"] or 0) for row in rows}
        finally:
            connection.close()

    def expected_crc_map(self, game_ids: Sequence[int]) -> Dict[int, Set[str]]:
        ids = [int(game_id) for game_id in game_ids]
        if not ids or not self.is_available():
            return {}
        connection = open_readonly(self.db_path)
        if connection is None:
            return {}
        try:
            result: Dict[int, Set[str]] = {game_id: set() for game_id in ids}
            for start in range(0, len(ids), 800):
                chunk = ids[start:start + 800]
                marks = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT game_id, crc32 FROM roms WHERE game_id IN ({marks}) AND crc32 IS NOT NULL AND crc32!=''",
                    chunk,
                ).fetchall()
                for row in rows:
                    result[int(row["game_id"])].add(str(row["crc32"]).lower())
            return result
        finally:
            connection.close()

    def candidate_rows(
        self,
        stem: str,
        crcs: Sequence[str],
        unique_crcs: Sequence[str],
        max_crc_candidates: int = 64,
    ) -> List[dict]:
        if not self.is_available():
            return []
        connection = open_readonly(self.db_path)
        if connection is None:
            return []
        try:
            rows = {}
            for row in connection.execute("SELECT * FROM games WHERE name=?", (stem,)).fetchall():
                rows[int(row["id"])] = dict(row)

            if crcs:
                marks = ",".join("?" for _ in crcs)
                hit_rows = connection.execute(
                    f"""SELECT game_id, COUNT(DISTINCT crc32) AS hits
                        FROM roms
                        WHERE crc32 IN ({marks})
                        GROUP BY game_id HAVING hits >= 2""",
                    list(crcs),
                ).fetchall()
                hit_map = {int(row["game_id"]): int(row["hits"] or 0) for row in hit_rows}
                game_ids = list(hit_map)
                game_rows = []
                for start in range(0, len(game_ids), 800):
                    chunk = game_ids[start:start + 800]
                    chunk_marks = ",".join("?" for _ in chunk)
                    game_rows.extend(
                        dict(row)
                        for row in connection.execute(
                            f"SELECT * FROM games WHERE id IN ({chunk_marks})", chunk
                        ).fetchall()
                    )
                game_rows.sort(
                    key=lambda row: (
                        -hit_map.get(int(row["id"]), 0),
                        str(row["name"] or ""),
                        str(row["system_name"] or ""),
                    )
                )
                for row in game_rows[:max_crc_candidates]:
                    rows[int(row["id"])] = row

            if unique_crcs:
                marks = ",".join("?" for _ in unique_crcs)
                found = connection.execute(
                    f"SELECT DISTINCT g.* FROM roms r JOIN games g ON r.game_id=g.id WHERE r.crc32 IN ({marks})",
                    list(unique_crcs),
                ).fetchall()
                for row in found:
                    rows[int(row["id"])] = dict(row)
            return list(rows.values())
        finally:
            connection.close()

    def find_exact_name(self, rom_name: str) -> List[dict]:
        stem = (rom_name or "").lower().strip()
        if not stem or not self.is_available():
            return []
        connection = open_readonly(self.db_path)
        if connection is None:
            return []
        try:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM games WHERE name=?", (stem,)
            ).fetchall()]
        finally:
            connection.close()
