# -*- coding: utf-8 -*-
"""판별 근거의 중앙 신뢰도 계산 및 충돌 감지."""

from typing import Optional

from .models import DetectionEvidence, RomAnalysisResult


class EvidenceScorer:
    """Detector가 만든 evidence를 일관된 규칙으로 최종 결과에 반영한다."""

    WEAK_METHODS = {
        "extension_hint", "filename_hint", "path_hint", "disc_format_only",
        "unidentified", "legacy_detector_fallback", "malformed_pbp", "malformed_chd",
        "m3u_playlist", "chd_header",
    }

    COMPATIBLE_SYSTEM_GROUPS = (
        {"gb", "gbc"},
        {"megadrive", "sega32x"},
    )

    @classmethod
    def apply(
        cls,
        result: RomAnalysisResult,
        expected_system_id: Optional[str] = None,
        file_ext: Optional[str] = None,
    ) -> RomAnalysisResult:
        if result.evidence:
            ordered = sorted(result.evidence, key=lambda e: e.confidence, reverse=True)
            result.primary_evidence = ordered[0]
            score = max(0.0, min(1.0, float(ordered[0].confidence)))

            # 독립적인 추가 근거는 남은 불확실성의 일부만 보강한다.
            seen = {(ordered[0].method, ordered[0].source)}
            for evidence in ordered[1:]:
                key = (evidence.method, evidence.source)
                if key in seen:
                    continue
                seen.add(key)
                weight = 0.06 if evidence.method not in cls.WEAK_METHODS else 0.02
                score += (1.0 - score) * max(0.0, min(1.0, evidence.confidence)) * weight

            result.confidence_score = min(0.999, score)
            result.detection_methods = list(dict.fromkeys(result.detection_methods or [e.method for e in result.evidence]))

        cls._detect_extension_conflict(result, expected_system_id, file_ext)
        cls._update_identity_status(result)
        cls._update_confidence_label(result)
        return result

    @classmethod
    def _detect_extension_conflict(
        cls,
        result: RomAnalysisResult,
        expected_system_id: Optional[str],
        file_ext: Optional[str],
    ):
        if not expected_system_id or result.system_id in {"unknown", expected_system_id}:
            return
        if cls._systems_compatible(expected_system_id, result.system_id):
            return
        primary = result.primary_evidence
        if not primary or primary.method in cls.WEAK_METHODS or primary.confidence < 0.85:
            return

        conflict = (
            f"확장자 {file_ext or ''}는 {expected_system_id} 기종을 가리키지만 "
            f"강한 바이너리 근거({primary.method})는 {result.system_id} 기종으로 판별했습니다."
        )
        if conflict not in result.conflicts:
            result.conflicts.append(conflict)
        result.add_warning(conflict)

    @classmethod
    def _systems_compatible(cls, left: str, right: str) -> bool:
        if left == right:
            return True
        return any(left in group and right in group for group in cls.COMPATIBLE_SYSTEM_GROUPS)

    @classmethod
    def _update_identity_status(cls, result: RomAnalysisResult):
        if result.identity_status == "ambiguous":
            return
        if result.system_id == "unknown":
            result.identity_status = "unknown"
        elif result.arcade_info and result.arcade_info.dat_status == "ambiguous":
            result.identity_status = "ambiguous"
        elif result.confidence_score >= 0.985:
            result.identity_status = "exact"
        elif result.confidence_score >= 0.90:
            result.identity_status = "strong"
        elif result.confidence_score >= 0.55:
            result.identity_status = "partial"
        else:
            result.identity_status = "unknown"

    @staticmethod
    def _update_confidence_label(result: RomAnalysisResult):
        if result.confidence_score >= 0.90:
            result.confidence = "high"
        elif result.confidence_score >= 0.55:
            result.confidence = "medium"
        else:
            result.confidence = "low"
