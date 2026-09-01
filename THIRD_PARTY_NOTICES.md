# Third-Party Notices / 데이터 출처

Game Books는 최상위 `LICENSE`에 명시된 라이선스로 배포됩니다. 이 문서는 함께 포함되는
구성요소와 참조 데이터의 출처를 추적하기 위한 보조 문서이며, 각 외부 프로젝트의
라이선스 조건을 대체하지 않습니다.

## ROM 분석 / 데이터베이스 구성요소

`libs/rom_analyzer`와 `libs/rom_database`는 Game Books와 함께 개발·배포되는 구성요소입니다.
ROM 분석과 라이브러리 관련 구현을 설계하는 과정에서 RomM 프로젝트를 참고했습니다.

- RomM: `https://github.com/rommapp/romm`

## MAME2003 / MAME2003-Plus 호환성 데이터

`libs/rom_database/data/mame_compatibility.db`는 Libretro가 공개하는 호환성 표의 스냅샷을
조회용 SQLite로 정규화한 데이터입니다. DB 내부 `metadata` 테이블에도 원본 URL이 기록됩니다.

- MAME 2003: `https://buildbot.libretro.com/compatibility_lists/cores/mame2003/mame2003.html`
- MAME 2003-Plus: `https://buildbot.libretro.com/compatibility_lists/cores/mame2003-plus/mame2003-plus.html`

## Arcade DAT / ROM metadata

`arcade_dat.db`와 `rom_metadata.db`는 Game Books/rom-analyzer의 ROM 식별과 진단을 위한
참조 데이터 스냅샷입니다. 여기에는 ROM 바이너리 자체가 포함되지 않으며 게임명, CRC,
기판/BIOS/호환성 등 식별용 메타데이터만 포함합니다.

외부 DAT/메타데이터를 갱신해 새 DB를 배포할 경우에는 해당 빌드에 사용한 원본 데이터의
출처와 라이선스를 이 문서 또는 DB metadata에 함께 기록해야 합니다.
