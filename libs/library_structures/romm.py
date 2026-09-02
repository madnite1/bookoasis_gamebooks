# -*- coding: utf-8 -*-
"""
Game Books 라이브러리 디렉터리 레이아웃 관리자.
구조 설계 시 RomM의 파일 시스템 관리 방식을 참고했다.

최종 불변식:
- 게임 콘텐츠 디렉터리는 DB의 영구 game_id를 키로 사용한다.
- ROM 파일명/경로/플랫폼 변경은 game_id를 변경하지 않는다.
- 네트워크 다운로드 정책은 Game Books가 담당하고 이 모듈은 로컬 bytes/path만 저장한다.
"""

import io
import json
import os
import re
import shutil
import tempfile
from typing import Optional, Dict, Any, Union, List

from rom_analyzer.models import RomAnalysisResult
from .base import BaseLibraryStructure, GameKey, ImageSource
from .models import SaveResult, LibraryPaths


class RomMLibraryStructure(BaseLibraryStructure):
    """Game Books 라이브러리의 물리 배치 구현체."""

    COVER_SMALL_MAX_WIDTH = 320
    COVER_LARGE_MAX_WIDTH = 1024
    COVER_SMALL_QUALITY = 78
    COVER_LARGE_QUALITY = 85

    def __init__(self, root_dir: str = "/romm", roms_folder: str = "roms", firmware_folder: str = "bios"):
        super().__init__(os.path.abspath(root_dir))
        self.roms_folder = self._safe_component(roms_folder, "roms")
        self.firmware_folder = self._safe_component(firmware_folder, "bios")
        self.paths = self.init_structure()

    def init_structure(self) -> LibraryPaths:
        library_dir = os.path.join(self.root_dir, "library")
        resources_dir = os.path.join(self.root_dir, "resources", "roms")
        assets_dir = os.path.join(self.root_dir, "assets", "users")
        config_dir = os.path.join(self.root_dir, "config")
        for directory in (library_dir, resources_dir, assets_dir, config_dir):
            os.makedirs(directory, exist_ok=True)
        return LibraryPaths(
            root=self.root_dir,
            library_dir=library_dir,
            resources_dir=resources_dir,
            assets_dir=assets_dir,
            config_dir=config_dir,
        )

    def get_paths(self) -> LibraryPaths:
        return self.paths

    def get_platform_roms_dir(self, platform_slug: str) -> str:
        platform_slug = self._safe_component(str(platform_slug or ""), "unknown")
        directory = os.path.join(self.paths.library_dir, platform_slug, self.roms_folder)
        os.makedirs(directory, exist_ok=True)
        return directory

    def get_platform_bios_dir(self, platform_slug: str) -> str:
        platform_slug = self._safe_component(str(platform_slug or ""), "unknown")
        directory = os.path.join(self.paths.library_dir, platform_slug, self.firmware_folder)
        os.makedirs(directory, exist_ok=True)
        return directory

    def get_game_content_dir(self, platform_slug: str, game_id: GameKey) -> str:
        game_key = self._game_key(game_id)
        directory = os.path.join(self.get_platform_roms_dir(platform_slug), game_key)
        os.makedirs(directory, exist_ok=True)
        return directory

    def get_resource_rom_dir(self, game_id: GameKey) -> str:
        game_key = self._game_key(game_id)
        directory = os.path.join(self.paths.resources_dir, game_key)
        os.makedirs(directory, exist_ok=True)
        return directory

    def get_user_asset_dirs(self, user_id: GameKey, game_id: GameKey) -> Dict[str, str]:
        user_key = self._safe_component(str(user_id), "default")
        game_key = self._game_key(game_id)
        base = os.path.join(self.paths.assets_dir, user_key, game_key)
        saves_dir = os.path.join(base, "saves")
        states_dir = os.path.join(base, "states")
        shots_dir = os.path.join(base, "screenshots")
        for directory in (saves_dir, states_dir, shots_dir):
            os.makedirs(directory, exist_ok=True)
        return {"saves": saves_dir, "states": states_dir, "screenshots": shots_dir}

    def place_content(
        self,
        rom_info: RomAnalysisResult,
        game_id: GameKey,
        move_files: bool = True,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        """ROM/번들을 library/{platform}/roms/{game_id}/ 에 트랜잭션 배치한다."""
        game_key = self._game_key(game_id)
        platform_slug = self._platform_slug(rom_info)
        result = SaveResult(platform_slug=platform_slug, game_id=game_key)
        source_path = os.path.abspath(str(getattr(rom_info, "file_path", "") or ""))
        file_name = self._safe_component(str(getattr(rom_info, "file_name", "") or ""), "game.rom")
        if not source_path or not os.path.isfile(source_path):
            result.add_error(f"ROM 원본 파일을 찾을 수 없습니다: {source_path}")
            return result

        strategy = self._content_conflict_strategy(conflict_strategy)
        game_dir = os.path.join(self.get_platform_roms_dir(platform_slug), game_key)
        sources = [(source_path, file_name)]

        disc_info = getattr(rom_info, "disc_info", None)
        is_bundle = bool(getattr(rom_info, "is_disc", False) and getattr(disc_info, "is_multi_file", False))
        if is_bundle:
            result.item_type = "bundle"
            if not bool(getattr(disc_info, "is_complete", False)):
                result.add_error("분석 결과가 불완전한 멀티파일 디스크 세트이므로 저장을 취소했습니다.")
                return result
            source_dir = os.path.dirname(source_path)
            seen_sources = {source_path}
            missing = []
            for reference in list(getattr(disc_info, "referenced_files", None) or []):
                safe_ref = self._safe_relative_path(str(reference or ""))
                if not safe_ref:
                    missing.append(str(reference or ""))
                    continue
                candidate = os.path.abspath(os.path.join(source_dir, safe_ref))
                try:
                    inside_source = os.path.commonpath([source_dir, candidate]) == source_dir
                except ValueError:
                    inside_source = False
                if not inside_source:
                    missing.append(str(reference or ""))
                    continue
                if not os.path.isfile(candidate):
                    candidate = self._find_case_insensitive_relative(source_dir, safe_ref) or ""
                candidate = os.path.abspath(candidate) if candidate else ""
                if candidate and os.path.isfile(candidate):
                    if candidate not in seen_sources:
                        sources.append((candidate, safe_ref))
                        seen_sources.add(candidate)
                elif safe_ref != file_name:
                    missing.append(str(reference or ""))
            if missing:
                result.add_error(f"멀티파일 세트 누락으로 저장 취소: {', '.join(missing)}")
                return result
        else:
            result.item_type = "rom"

        try:
            placed = self._transfer_directory_transactional(
                sources,
                game_dir,
                move=move_files,
                conflict_strategy=strategy,
            )
            if not placed:
                result.warnings.append(f"이미 존재하여 건너뜀: {game_dir}")
                return result
            result.rom_dest_path = placed[0]
            result.companion_dest_paths.extend(placed[1:])
        except Exception as exc:
            result.add_error(f"게임 콘텐츠 배치 실패: {exc}")
        return result

    def place_bios(
        self,
        rom_info: RomAnalysisResult,
        move_files: bool = True,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        platform_slug = self._platform_slug(rom_info)
        result = SaveResult(item_type="bios", platform_slug=platform_slug)
        source_path = os.path.abspath(str(getattr(rom_info, "file_path", "") or ""))
        file_name = self._safe_component(str(getattr(rom_info, "file_name", "") or ""), "bios.bin")
        if not source_path or not os.path.isfile(source_path):
            result.add_error(f"BIOS 원본 파일을 찾을 수 없습니다: {source_path}")
            return result
        dest_path = os.path.join(self.get_platform_bios_dir(platform_slug), file_name)
        try:
            actual_dest = self._transfer_file_transactional(
                source_path,
                dest_path,
                move=move_files,
                conflict_strategy=conflict_strategy,
            )
            if actual_dest:
                result.bios_dest_path = actual_dest
                result.rom_dest_path = actual_dest
            else:
                result.warnings.append(f"이미 존재하여 건너뜀: {dest_path}")
        except Exception as exc:
            result.add_error(f"BIOS 파일 배치 실패: {exc}")
        return result

    def save_cover(self, game_id: GameKey, cover_data: ImageSource) -> SaveResult:
        game_key = self._game_key(game_id)
        result = SaveResult(item_type="cover", game_id=game_key)
        try:
            image_bytes = self._read_image_bytes(cover_data)
            if not image_bytes:
                raise ValueError("커버 이미지 데이터를 읽지 못했습니다.")
            large_bytes = self._to_webp_variant(
                image_bytes,
                max_width=self.COVER_LARGE_MAX_WIDTH,
                quality=self.COVER_LARGE_QUALITY,
            )
            small_bytes = self._to_webp_variant(
                image_bytes,
                max_width=self.COVER_SMALL_MAX_WIDTH,
                quality=self.COVER_SMALL_QUALITY,
            )
            resource_dir = self.get_resource_rom_dir(game_key)
            large_path = os.path.join(resource_dir, "cover_l.webp")
            small_path = os.path.join(resource_dir, "cover_s.webp")
            self._atomic_write_bytes(large_path, large_bytes)
            self._atomic_write_bytes(small_path, small_bytes)
            result.cover_l_dest_path = large_path
            result.cover_s_dest_path = small_path
        except Exception as exc:
            result.add_error(f"커버아트 저장 실패: {exc}")
        return result

    def save_user_save(
        self,
        user_id: GameKey,
        game_id: GameKey,
        data: bytes,
        extension: str = ".sav",
    ) -> SaveResult:
        game_key = self._game_key(game_id)
        result = SaveResult(item_type="save", game_id=game_key)
        try:
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError("세이브 데이터는 bytes여야 합니다.")
            ext = str(extension or ".sav").lower()
            if ext not in {".sav", ".srm"}:
                raise ValueError("세이브 확장자는 .sav 또는 .srm만 허용됩니다.")
            directories = self.get_user_asset_dirs(user_id, game_key)
            dest_path = os.path.join(directories["saves"], f"default{ext}")
            self._atomic_write_bytes(dest_path, bytes(data))
            result.save_dest_path = dest_path
        except Exception as exc:
            result.add_error(f"세이브 데이터 저장 실패: {exc}")
        return result

    def save_user_state(
        self,
        user_id: GameKey,
        game_id: GameKey,
        data: bytes,
        slot: int = 0,
    ) -> SaveResult:
        game_key = self._game_key(game_id)
        result = SaveResult(item_type="state", game_id=game_key)
        try:
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError("세이브스테이트 데이터는 bytes여야 합니다.")
            slot_num = int(slot or 0)
            if slot_num < 0 or slot_num > 99:
                raise ValueError("세이브스테이트 슬롯은 0~99 범위여야 합니다.")
            filename = "default.state" if slot_num == 0 else f"slot_{slot_num}.state"
            directories = self.get_user_asset_dirs(user_id, game_key)
            dest_path = os.path.join(directories["states"], filename)
            self._atomic_write_bytes(dest_path, bytes(data))
            result.state_dest_path = dest_path
        except Exception as exc:
            result.add_error(f"세이브스테이트 저장 실패: {exc}")
        return result

    def save(
        self,
        rom_info: RomAnalysisResult,
        move_files: bool = True,
        cover_data: Optional[ImageSource] = None,
        screenshots: Optional[List[ImageSource]] = None,
        metadata: Optional[Union[Dict[str, Any], str]] = None,
        save_metadata: bool = True,
        save_data: Optional[bytes] = None,
        state_data: Optional[bytes] = None,
        user_id: str = "default",
        rom_identifier: Optional[str] = None,
        conflict_strategy: str = "replace",
    ) -> SaveResult:
        """기존 호환용 복합 API. 새 호출부는 독립 메서드를 사용한다."""
        file_name = str(getattr(rom_info, "file_name", "") or "")
        fallback_key = os.path.splitext(file_name)[0] or "rom"
        game_key = self._game_key(rom_identifier or fallback_key)

        if self._is_bios_result(rom_info):
            return self.place_bios(rom_info, move_files=move_files, conflict_strategy=conflict_strategy)

        result = self.place_content(
            rom_info,
            game_key,
            move_files=move_files,
            conflict_strategy=conflict_strategy,
        )
        if not result.success:
            return result

        if cover_data is not None:
            result.absorb(self.save_cover(game_key, cover_data))
        if screenshots:
            result.absorb(self._save_screenshots(game_key, screenshots))
        if save_metadata:
            result.absorb(self._save_metadata(game_key, metadata, rom_info))
        if save_data is not None:
            result.absorb(self.save_user_save(user_id, game_key, save_data, extension=".sav"))
        if state_data is not None:
            result.absorb(self.save_user_state(user_id, game_key, state_data, slot=0))
        return result

    def _save_metadata(
        self,
        game_id: GameKey,
        metadata: Optional[Union[Dict[str, Any], str]],
        rom_info: RomAnalysisResult,
    ) -> SaveResult:
        game_key = self._game_key(game_id)
        result = SaveResult(item_type="metadata", game_id=game_key)
        try:
            if metadata is None:
                text = json.dumps(rom_info.to_dict(), ensure_ascii=False, indent=2)
            elif isinstance(metadata, str):
                text = metadata
            else:
                text = json.dumps(metadata, ensure_ascii=False, indent=2)
            dest_path = os.path.join(self.get_resource_rom_dir(game_key), "metadata.json")
            self._atomic_write_text(dest_path, text)
            result.metadata_dest_path = dest_path
        except Exception as exc:
            result.add_error(f"메타데이터 저장 실패: {exc}")
        return result

    def _save_screenshots(self, game_id: GameKey, screenshots: List[ImageSource]) -> SaveResult:
        game_key = self._game_key(game_id)
        result = SaveResult(item_type="screenshots", game_id=game_key)
        shots_dir = os.path.join(self.get_resource_rom_dir(game_key), "screenshots")
        os.makedirs(shots_dir, exist_ok=True)
        for index, source in enumerate(screenshots, start=1):
            try:
                image_bytes = self._read_image_bytes(source)
                if not image_bytes:
                    raise ValueError("이미지 데이터를 읽지 못했습니다.")
                webp = self._to_webp_variant(image_bytes, max_width=1280, quality=82)
                dest_path = os.path.join(shots_dir, f"screenshot_{index}.webp")
                self._atomic_write_bytes(dest_path, webp)
                result.screenshots_dest_paths.append(dest_path)
            except Exception as exc:
                result.add_error(f"스크린샷 {index} 저장 실패: {exc}")
        return result

    @staticmethod
    def _platform_slug(rom_info: RomAnalysisResult) -> str:
        value = getattr(rom_info, "platform_slug", None) or getattr(rom_info, "system_id", None) or "unknown"
        return RomMLibraryStructure._safe_component(str(value), "unknown")

    @staticmethod
    def _is_bios_result(rom_info: RomAnalysisResult) -> bool:
        arcade = getattr(rom_info, "arcade_info", None)
        return bool(
            getattr(arcade, "is_bios_set", False)
            or getattr(arcade, "is_device_set", False)
            or (
                not bool(getattr(rom_info, "is_playable", True))
                and "bios" in str(getattr(rom_info, "system_name", "") or "").lower()
            )
        )

    @staticmethod
    def _content_conflict_strategy(value: str) -> str:
        strategy = str(value or "replace").lower()
        if strategy not in {"replace", "skip", "verify"}:
            raise ValueError("game_id 콘텐츠 디렉터리는 conflict_strategy=replace, skip 또는 verify만 허용됩니다.")
        return strategy

    @classmethod
    def _game_key(cls, value: GameKey) -> str:
        raw = str(value if value is not None else "").strip()
        if not raw:
            raise ValueError("game_id가 필요합니다.")
        return cls._safe_component(raw, "game")

    @staticmethod
    def _safe_relative_path(value: str) -> Optional[str]:
        raw = (value or "").replace("\\", os.sep).strip()
        normalized = os.path.normpath(raw)
        if not normalized or normalized in {".", ".."} or os.path.isabs(normalized):
            return None
        if normalized.startswith(".." + os.sep):
            return None
        parts = []
        for part in normalized.split(os.sep):
            safe = RomMLibraryStructure._safe_component(part, "")
            if not safe:
                return None
            parts.append(safe)
        return os.path.join(*parts) if parts else None

    @classmethod
    def _find_case_insensitive_relative(cls, base_dir: str, relative_path: str) -> Optional[str]:
        current = os.path.abspath(base_dir)
        safe_rel = cls._safe_relative_path(relative_path)
        if not safe_rel:
            return None
        for part in safe_rel.split(os.sep):
            if not os.path.isdir(current):
                return None
            match = None
            try:
                for entry in os.listdir(current):
                    if entry.casefold() == part.casefold():
                        match = entry
                        break
            except OSError:
                return None
            if match is None:
                return None
            current = os.path.join(current, match)
        return current

    @staticmethod
    def _safe_component(value: str, fallback: str = "item") -> str:
        value = os.path.basename((value or "").replace("\\", "/")).strip()
        value = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", value)
        value = value.replace("..", "_").strip(" .")
        return value[:180] or fallback

    @staticmethod
    def _read_image_bytes(source: ImageSource) -> Optional[bytes]:
        if isinstance(source, bytes):
            return source
        if isinstance(source, bytearray):
            return bytes(source)
        if isinstance(source, str):
            if source.startswith(("http://", "https://")):
                raise ValueError("library_structures는 네트워크 URL을 직접 다운로드하지 않습니다.")
            if os.path.isfile(source):
                with open(source, "rb") as file_obj:
                    return file_obj.read()
        return None

    @staticmethod
    def _to_webp_variant(data: bytes, max_width: int, quality: int) -> bytes:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise RuntimeError("WebP 변환에는 BookOasis가 제공하는 Pillow가 필요합니다") from exc

        with Image.open(io.BytesIO(data)) as source_image:
            image = ImageOps.exif_transpose(source_image)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            if max_width > 0 and image.width > max_width:
                target_height = max(1, int(round(image.height * (max_width / float(image.width)))))
                resampling = getattr(Image, "Resampling", Image)
                image = image.resize((max_width, target_height), resample=resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=int(quality), method=4)
            return out.getvalue()

    @classmethod
    def _to_webp(cls, data: bytes) -> bytes:
        """기존 내부 호출 호환용 large WebP 변환."""
        return cls._to_webp_variant(data, cls.COVER_LARGE_MAX_WIDTH, cls.COVER_LARGE_QUALITY)

    @staticmethod
    def _file_sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
        import hashlib
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _verify_existing_content(cls, normalized_sources, final_dir: str) -> bool:
        """재개 시 기존 game_id 디렉터리가 원본 세트와 정확히 같은지 확인한다."""
        expected_names = {safe_name for _src, safe_name, _dest, _inside in normalized_sources}
        actual_names = set()
        if not os.path.isdir(final_dir):
            return False
        for root, _dirs, files in os.walk(final_dir):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), final_dir)
                actual_names.add(rel)
        if actual_names != expected_names:
            return False
        for src_abs, safe_name, _dest_abs, _inside in normalized_sources:
            target = os.path.join(final_dir, safe_name)
            try:
                if os.path.getsize(src_abs) != os.path.getsize(target):
                    return False
            except OSError:
                return False
            if cls._file_sha256(src_abs) != cls._file_sha256(target):
                return False
        return True

    @classmethod
    def _same_device_move_possible(cls, normalized_sources, parent: str) -> bool:
        if not normalized_sources:
            return False
        try:
            target_dev = os.stat(parent).st_dev
            return all(os.stat(src_abs).st_dev == target_dev for src_abs, _name, _dest, _inside in normalized_sources)
        except OSError:
            return False

    @classmethod
    def _transfer_directory_remote_rename(cls, normalized_sources, final_dir: str):
        """같은 파일시스템에서는 contents를 재전송하지 않고 rename으로 배치한다.

        rclone VFS/FUSE에서는 같은 remote 내 rename이 서버 측 move로 처리될 수 있어
        대용량 ROM을 로컬 캐시를 통해 다시 읽고 쓰는 비용을 피한다. 실패하면 원본
        위치를 복구한 뒤 None을 반환해 copy fallback을 허용한다.
        """
        parent = os.path.dirname(final_dir)
        staging = tempfile.mkdtemp(prefix=".game-content-move-", dir=parent)
        backup = None
        moved = []
        installed = False
        try:
            for src_abs, safe_name, _dest_abs, was_inside_target in normalized_sources:
                if was_inside_target:
                    # 일부만 이미 대상 내부인 혼합 세트는 rename fast-path로 처리하지 않는다.
                    raise OSError("mixed target/source content")
                staged_path = os.path.join(staging, safe_name)
                os.makedirs(os.path.dirname(staged_path), exist_ok=True)
                os.replace(src_abs, staged_path)
                moved.append((src_abs, staged_path))

            if os.path.exists(final_dir):
                backup = cls._reserve_backup_path(parent, ".game-backup-")
                os.replace(final_dir, backup)
            os.replace(staging, final_dir)
            staging = None
            installed = True
            if backup and os.path.exists(backup):
                cls._remove_path(backup)
                backup = None
            return [os.path.join(final_dir, safe_name) for _src, safe_name, _dest, _inside in normalized_sources]
        except OSError:
            if installed:
                # 최종 설치 이후의 오류는 copy fallback을 하면 중복/손실 위험이 있으므로 전파한다.
                raise
            if backup and os.path.exists(backup) and not os.path.exists(final_dir):
                try:
                    os.replace(backup, final_dir)
                    backup = None
                except OSError:
                    raise
            # staging으로 옮겨진 원본을 역순으로 되돌린다.
            rollback_ok = True
            for src_abs, staged_path in reversed(moved):
                if not os.path.exists(staged_path):
                    continue
                try:
                    os.makedirs(os.path.dirname(src_abs), exist_ok=True)
                    os.replace(staged_path, src_abs)
                except OSError:
                    rollback_ok = False
            if not rollback_ok:
                raise RuntimeError("rename fast-path 실패 후 원본 롤백에 실패했습니다.")
            return None
        finally:
            if staging and os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)
            if backup and os.path.exists(backup) and not os.path.exists(final_dir):
                try:
                    os.replace(backup, final_dir)
                except OSError:
                    pass

    @classmethod
    def _transfer_directory_transactional(
        cls,
        sources,
        target_dir: str,
        move: bool,
        conflict_strategy: str,
    ):
        strategy = cls._content_conflict_strategy(conflict_strategy)
        final_dir = os.path.abspath(target_dir)
        parent = os.path.dirname(final_dir)
        os.makedirs(parent, exist_ok=True)

        normalized_sources = []
        seen_destinations = set()
        for src, relative_name in sources:
            src_abs = os.path.abspath(str(src))
            safe_name = cls._safe_relative_path(str(relative_name or ""))
            if not safe_name:
                raise ValueError(f"안전하지 않은 콘텐츠 상대경로: {relative_name}")
            if not os.path.isfile(src_abs):
                raise FileNotFoundError(src_abs)
            dest_abs = os.path.abspath(os.path.join(final_dir, safe_name))
            if dest_abs in seen_destinations:
                raise ValueError(f"중복 콘텐츠 목적지: {safe_name}")
            seen_destinations.add(dest_abs)
            normalized_sources.append((src_abs, safe_name, dest_abs, cls._path_within(src_abs, final_dir)))

        expected_paths = [item[2] for item in normalized_sources]
        if normalized_sources and all(src == dest for src, _name, dest, _inside in normalized_sources):
            return expected_paths

        if os.path.exists(final_dir) and strategy == "skip":
            return []
        if os.path.exists(final_dir) and strategy == "verify":
            if not cls._verify_existing_content(normalized_sources, final_dir):
                raise FileExistsError(f"기존 game_id 콘텐츠가 원본과 달라 자동 재개할 수 없습니다: {final_dir}")
            if move:
                for src_abs, _safe_name, _dest_abs, was_inside_target in normalized_sources:
                    if not was_inside_target and os.path.isfile(src_abs):
                        os.remove(src_abs)
            return expected_paths

        if move and cls._same_device_move_possible(normalized_sources, parent):
            renamed = cls._transfer_directory_remote_rename(normalized_sources, final_dir)
            if renamed is not None:
                return renamed

        staging = tempfile.mkdtemp(prefix=".game-content-", dir=parent)
        backup = None
        try:
            for src_abs, safe_name, _dest_abs, _inside in normalized_sources:
                staged_path = os.path.join(staging, safe_name)
                os.makedirs(os.path.dirname(staged_path), exist_ok=True)
                shutil.copy2(src_abs, staged_path)

            if os.path.exists(final_dir):
                backup = cls._reserve_backup_path(parent, ".game-backup-")
                os.replace(final_dir, backup)

            try:
                os.replace(staging, final_dir)
                staging = None
            except Exception:
                if backup and os.path.exists(backup) and not os.path.exists(final_dir):
                    os.replace(backup, final_dir)
                    backup = None
                raise

            if backup and os.path.exists(backup):
                cls._remove_path(backup)
                backup = None

            if move:
                for src_abs, _safe_name, _dest_abs, was_inside_target in normalized_sources:
                    if was_inside_target:
                        continue
                    try:
                        if os.path.isfile(src_abs):
                            os.remove(src_abs)
                    except OSError:
                        pass
            return [os.path.join(final_dir, safe_name) for _src, safe_name, _dest, _inside in normalized_sources]
        finally:
            if staging and os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)
            if backup and os.path.exists(backup):
                if not os.path.exists(final_dir):
                    try:
                        os.replace(backup, final_dir)
                        backup = None
                    except OSError:
                        pass
                if backup and os.path.exists(backup) and os.path.exists(final_dir):
                    cls._remove_path(backup)

    @classmethod
    def _transfer_file_transactional(
        cls,
        src: str,
        dest: str,
        move: bool = True,
        conflict_strategy: str = "replace",
    ) -> Optional[str]:
        src_abs = os.path.abspath(src)
        dest_abs = os.path.abspath(dest)
        if src_abs == dest_abs:
            return dest_abs
        if not os.path.isfile(src_abs):
            raise FileNotFoundError(src_abs)

        strategy = str(conflict_strategy or "replace").lower()
        if strategy not in {"replace", "skip", "rename"}:
            raise ValueError("conflict_strategy must be one of: replace, skip, rename")

        parent = os.path.dirname(dest_abs)
        os.makedirs(parent, exist_ok=True)
        if os.path.exists(dest_abs):
            if strategy == "skip":
                return None
            if strategy == "rename":
                base, ext = os.path.splitext(dest_abs)
                index = 1
                candidate = f"{base} ({index}){ext}"
                while os.path.exists(candidate):
                    index += 1
                    candidate = f"{base} ({index}){ext}"
                dest_abs = candidate
            elif os.path.isdir(dest_abs):
                raise IsADirectoryError(dest_abs)

        fd, staging = tempfile.mkstemp(prefix=".file-content-", dir=parent)
        os.close(fd)
        backup = None
        try:
            shutil.copy2(src_abs, staging)
            if os.path.exists(dest_abs):
                backup = cls._reserve_backup_path(parent, ".file-backup-")
                os.replace(dest_abs, backup)
            try:
                os.replace(staging, dest_abs)
                staging = None
            except Exception:
                if backup and os.path.exists(backup) and not os.path.exists(dest_abs):
                    os.replace(backup, dest_abs)
                    backup = None
                raise
            if backup and os.path.exists(backup):
                cls._remove_path(backup)
                backup = None
            if move:
                try:
                    if os.path.isfile(src_abs):
                        os.remove(src_abs)
                except OSError:
                    pass
            return dest_abs
        finally:
            if staging and os.path.exists(staging):
                cls._remove_path(staging)
            if backup and os.path.exists(backup):
                if not os.path.exists(dest_abs):
                    try:
                        os.replace(backup, dest_abs)
                        backup = None
                    except OSError:
                        pass
                if backup and os.path.exists(backup) and os.path.exists(dest_abs):
                    cls._remove_path(backup)

    @classmethod
    def _transfer_bundle_transactional(cls, sources, bundle_dir: str, move: bool, conflict_strategy: str):
        """기존 이름 호환. 실제 구현은 game-id 디렉터리 트랜잭션을 사용한다."""
        return cls._transfer_directory_transactional(sources, bundle_dir, move, conflict_strategy)

    @classmethod
    def _transfer_file(cls, src: str, dest: str, move: bool = True, conflict_strategy: str = "replace") -> Optional[str]:
        """기존 이름 호환. 단일 파일도 staging 후 commit한다."""
        return cls._transfer_file_transactional(src, dest, move=move, conflict_strategy=conflict_strategy)

    @staticmethod
    def _reserve_backup_path(parent: str, prefix: str) -> str:
        path = tempfile.mkdtemp(prefix=prefix, dir=parent)
        os.rmdir(path)
        return path

    @staticmethod
    def _remove_path(path: str):
        if not path or not os.path.lexists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

    @staticmethod
    def _path_within(path: str, root: str) -> bool:
        try:
            return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
        except (ValueError, OSError):
            return False

    @staticmethod
    def _atomic_write_bytes(path: str, data: bytes):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".asset-", dir=parent)
        try:
            with os.fdopen(fd, "wb") as file_obj:
                file_obj.write(data)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    @classmethod
    def _atomic_write_text(cls, path: str, text: str):
        cls._atomic_write_bytes(path, str(text).encode("utf-8"))

    @classmethod
    def _find_case_insensitive(cls, directory: str, filename: str) -> Optional[str]:
        filename_lower = filename.lower()
        if not os.path.isdir(directory):
            return None
        for entry in os.listdir(directory):
            if entry.lower() == filename_lower:
                return os.path.join(directory, entry)
        return None
