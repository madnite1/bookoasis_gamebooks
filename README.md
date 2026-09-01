# 🎮 Game Books (BookOasis 레트로 게임 에뮬레이터 플러그인)

**`bookoasis_gamebooks`**는 북오아시스(BookOasis) 미디어 서버에서 **닌텐도(SFC, GBA, NES, GB/GBC, NDS, N64), 세가(MD, GG, SMS, Saturn, Dreamcast), 소니(PlayStation 1, PSP), SNK(Neo-Geo MVS/AES), 아케이드(MAME, FBNeo), 아타리, PC엔진 등 다양한 레트로 게임 플랫폼**을 별도의 프로그램 설치 없이 웹 브라우저에서 즐길 수 있도록 지원하는 WebAssembly 기반 에뮬레이터 플러그인입니다.

북오아시스의 좌측 사이드바 카테고리 메뉴로 통합되며, 유저별 독립 클라우드 세이브, 즉시 이어하기(Instant Resume), 커버 자동 캡처 & 블랙바 크롭, 시스템 바이오스 전용 관리 모달, 표준 Gamepad API 지원 등 편의 기능을 제공합니다.

---

## 🔄 업데이트

Game Books는 `rom_analyzer`, `rom_database` 및 SQLite 참조 DB를 함께 배포합니다. 이 파일들은 중첩 디렉터리와 바이너리를 포함하므로 **BookOasis 코어의 샘플 업데이트 버튼은 사용하지 않습니다.**

- 온라인 업데이트: 최신 **Plugin Manager**의 저장소 ZIP 업데이트 사용
- 수동 업데이트: GitHub Release의 `bookoasis_gamebooks-<version>.zip` 사용
- Release ZIP은 `update_manifest.files`에 선언된 전체 관리 파일을 포함하며 생성 시 SQLite `integrity_check`와 ZIP CRC를 검증합니다.
- 플러그인 소스 디렉터리 밖의 `plugins/data/bookoasis_gamebooks/` 사용자 데이터와 세이브는 업데이트 대상에 포함하지 않습니다.

---

## 🕹️ 지원 콘솔 플랫폼 및 롬(ROM) 포맷

| 플랫폼 / 콘솔 | 지원 확장자 | 기본 에뮬레이션 코어 |
| :--- | :--- | :--- |
| **SFC / SNES** (슈퍼패미컴) | `.sfc`, `.smc`, `.snes`, `.fig`, `.zip`, `.7z` | Snes9x |
| **GBA** (게임보이 어드밴스) | `.gba`, `.zip`, `.7z` | mGBA |
| **FC / NES** (패미컴 / 패미컴 디스크) | `.nes`, `.fds`, `.unf`, `.zip`, `.7z` | FCEUmm / Nestopia |
| **GB / GBC** (게임보이 / 컬러) | `.gb`, `.gbc`, `.zip`, `.7z` | Gambatte |
| **NDS** (닌텐도 DS) | `.nds`, `.zip`, `.7z` | DeSmuME / melonDS |
| **N64** (닌텐도 64) | `.n64`, `.z64`, `.v64`, `.zip`, `.7z` | Mupen64Plus |
| **Mega Drive / Genesis** (메가드라이브) | `.md`, `.gen`, `.smd`, `.zip`, `.7z` | Genesis Plus GX |
| **Master System / Game Gear** | `.sms`, `.gg`, `.sg`, `.zip`, `.7z` | SMS Plus GX |
| **PlayStation 1 (PS1)** | `.psx`, `.ps1`, `.pbp`, `.cue`, `.zip`, `.7z` | PCSX ReARMed |
| **PSP** (PlayStation Portable) | `.cso`, `.pbp`, `.zip`, `.7z` | PPSSPP |
| **Neo-Geo (SNK 네오지오)** | `.zip` (`mslug`, `kof`, `samsho` 등) | FBNeo / MAME 2003+ |
| **Arcade / MAME / FBNeo** | `.zip`, `.7z` | FBNeo / MAME 2003+ |
| **PC Engine / TurboGrafx-16** | `.pce`, `.sgx`, `.zip`, `.7z` | Mednafen PCE |
| **WonderSwan / Color** | `.ws`, `.wsc`, `.zip`, `.7z` | Mednafen Swan |
| **Neo Geo Pocket / Color** | `.ngp`, `.ngc`, `.zip`, `.7z` | Mednafen NGP |
| **Atari 2600 / 5200 / 7800 / Lynx** | `.a26`, `.a52`, `.a78`, `.lnx`, `.zip`, `.7z` | Stella / ProSystem |

> 💡 **`.7z` 압축 롬 및 아케이드 롬셋 자동 처리**: `py7zr` 및 내장 DAT DB를 통해 `.7z` 압축 파일 내부를 분석하고 필요 시 실행용 ZIP으로 변환합니다. 네오지오(Neo-Geo) 등 기판 바이오스가 필요한 아케이드 롬셋은 등록된 BIOS와 함께 사용할 수 있도록 처리합니다.

---

## ✨ 주요 핵심 기능

### 1. 🕹️ 브라우저 WebAssembly 에뮬레이터 & 북오아시스 전용 다크 툴바
* **독립 iframe 샌드박스 격리**: SPA 환경에서의 전역 스크립트 충돌과 에뮬레이터 리소스 간섭을 줄입니다.
* **하단 기본 툴바 숨김**: EmulatorJS 기본 메뉴 대신 북오아시스 테마와 일체화된 상단 콤팩트 다크 툴바를 제공합니다.
* **💾 자동 클라우드 세이브**: 게임 플레이 도중(기본 60초 주기) 및 게임 종료 시 인게임 세이브(`.sav`)와 상태 스냅샷(`.state`)을 서버에 백업합니다.
* **🚀 즉시 이어하기 (Instant Resume)**: 저장된 상태 스냅샷이 있으면 게임 재실행 시 해당 지점으로 자동 복원을 시도합니다.
* **🔄 안전한 처음부터 재시작**: 상단 재시작 버튼 클릭 시 세이브 삭제 경고 안내창이 표시되며, 확인 시 서버의 세이브 데이터를 깨끗이 초기화하고 새 게임으로 시작합니다.
* **🎨 실시간 그래픽/셰이더 설정**: CRT 레트로 브라운관 스캔라인, 2x/4x HQ 스무딩, 화면 비율(3:2, 4:3, 16:9, 꽉 채우기) 실시간 조절 및 자동 기억.
* **⏩ 자유로운 배속 제어**: 1x / 2x / 3x / 4x 고속 진행을 원클릭으로 전환합니다.
* **⏸ 일시정지 / ▶ 재생, 🔊 음소거 토글, ⛶ 전체화면** 지원.
* **❌ 스마트 종료 (`ESC`)**: 열려있는 최상위 서브 모달부터 1개씩 순차 종료되며, 최종 종료 시 에뮬레이터 오디오와 관련 리소스를 정리합니다.

### 2. ⚡ 백그라운드 커버 아트 다운로드 큐(Queue) & 실시간 배지 UI
* **논블로킹 백그라운드 다운로드**: 라이브러리 동기화 시 커버 다운로드로 인해 모달이 멈추지 않고, ROM 메타 동기화가 완료되면 즉시 창이 닫히며 백그라운드 워커 큐에서 커버를 순차적으로 자동 다운로드합니다.
* **상단 실시간 상태 배지**: 헤더 상단에 `🖼️ 커버 다운로드 중: 15/374 (사무라이 쇼다운)`과 같이 현재 처리 중인 게임명과 진행률이 실시간 펄스 애니메이션과 함께 표시되며, 완료 시 자동으로 사라집니다.

### 3. 📸 스마트 스크린샷 캡처, 블랙바 크롭 & Libretro 온라인 아트워크 검색
* **게임 화면 우클릭 메뉴**: 에뮬레이터 플레이 화면 어디서든 마우스 우클릭 시 `📸 현재 화면을 커버 이미지로 설정` 메뉴가 팝업됩니다.
* **지능형 블랙바 크롭 (`cropLetterboxFromBlob`)**: 캡처된 스크린샷의 상하좌우 검은 여백(레터박스/필러박스)을 픽셀 단위로 감지하여 커버 이미지에 불필요한 여백을 줄입니다.
* **🌐 Libretro 온라인 아트워크 실시간 검색**: 게임 목록의 `[커버 변경]` 버튼을 통해 Libretro 오픈소스 데이터베이스에서 고화질 정품 패키지 박스아트를 1클릭으로 검색 및 즉시 다운로드하여 커버로 적용할 수 있습니다.
* **📁 커버 이미지 저장 폴더 사용자 지정 및 실시간 마이그레이션**: 설정에서 커버 저장 폴더(예: NAS 마운트 경로)를 지정하면, 기존 커버 이미지들을 새 폴더로 자동 이동하며 실시간 프로그레스바로 진행 상태를 안내합니다.

### 4. 🗄️ 27,000+ 통합 DAT DB 및 롬 자동 변환 / 재배치 파이프라인
* **`rom_database` 참조 계층 분리**: `rom-analyzer` 1.3.0과 함께 독립 `rom_database` 패키지를 vendor하며, ROM 메타데이터·DAT·MAME 호환성 SQLite DB는 `libs/rom_database/data/`에서 관리합니다. `rom_database`는 원시 참조 사실을 제공하고 최종 식별·실행 가능성 판정은 `rom-analyzer`가 담당합니다.
* **정밀 CRC32 분석 & 클론셋 최적화**: MAME/FBNeo 및 주요 콘솔 기종의 참조 정보를 담은 SQLite DAT DB(`arcade_dat.db`)를 이용해 파일명뿐 아니라 CRC/DAT 근거로 타이틀과 기종 판별을 보강하고 클론 롬셋 후보를 매칭합니다.
* **통합 Library Sync Engine**: ROM 추가, 라이브러리 동기화, 전체 재구축, 무결성 진단이 같은 진입점을 사용합니다. 업로드한 여러 ROM은 파일마다 전체 스캔하지 않고 모두 수신한 뒤 한 번에 분석·등록합니다.
* **라이브러리 동기화**: 신규·변경·삭제 ROM을 서버에서 전체 라이브러리 기준으로 비교하며, 변경이 없는 ROM은 크기/수정시간 캐시로 재분석을 건너뜁니다.
* **비동기 전체 재구축**: 설정의 고급 라이브러리 관리에서 모든 ROM을 최신 분석 기준으로 강제 재분석할 수 있으며 진행률을 제공합니다.
* **기종별 자동 폴더 재배치**: 동기화/전체 재구축 시 ROM이 잘못된 기종 폴더에 위치해 있으면 올바른 코어 하위 폴더(예: `snes/`, `gba/`, `arcade/` 등)로 안전하게 물리적 이동을 진행합니다.
* **7z ➔ ZIP 자동 변환**: 지원되는 콘솔 및 아케이드 `.7z` 롬셋을 EmulatorJS에서 다루기 쉬운 `.zip` 형태로 변환하고, 필요한 경우 등록된 기판 BIOS를 함께 사용할 수 있도록 처리합니다.

### 5. 🩺 ROM 라이브러리 전수 무결성·호환성 진단 (Health Check, 관리자 전용)
* **이동 없는 전수 재분석**: `[무결성 진단]` 버튼을 누르면 ROM 파일을 이동·변환하지 않고 최신 `rom-analyzer`로 전체 라이브러리를 다시 분석하며 진행률을 실시간 표시합니다.
* **근거 기반 상태 분류**: `진단 통과`, `기종 재분류 필요`, `BIOS 필요`, `CHD/디스크 필요`, `참조 파일 누락`, `현재 코어 미지원`, `판정 미확인`으로 분류합니다. `진단 통과`는 파일·의존성·현재 코어 호환성에 문제가 발견되지 않았다는 의미이며 실제 플레이 성공 자체를 보증하지는 않습니다.
* **legacy 휴리스틱 제거**: 파일명 마지막 문자로 Parent ROM을 추측하거나 `bm`/`ddr` 같은 접두사만으로 CHD 필요 여부를 추측하지 않습니다. 식별 근거가 부족한 경우 임의 오류 상태 대신 `판정 미확인`으로 표시합니다.
* **분리형 콘솔 칩 덤프 감지**: DAT로 기종이 정확히 식별되더라도 ZIP/7z 내부에 해당 EmulatorJS 코어가 직접 로드할 수 있는 단일 ROM 이미지가 없으면 `코어 미지원`으로 분류합니다. 예를 들어 NES의 `.prg/.chr` 분리형 칩 덤프는 `.nes` 이미지가 없으면 직접 실행 불가로 표시합니다.
* **기종 재분류 감지**: 현재 DB의 기종과 `exact`/`strong` 분석 결과가 다르면 파일을 즉시 이동하지 않고 `기종 재분류 필요`로 표시하며, 실제 이동은 관리자가 설정의 고급 관리에서 명시적으로 실행하는 `라이브러리 전체 재구축`에서 수행합니다.
* **게임별 코어 호환성 진단**: 내장 `mame_compatibility.db`를 이용해 `game not working`, `unemulated protection` 등으로 기록된 MAME2003/MAME2003-Plus 게임을 파일 손상과 구분하여 `코어 미지원` 상태로 표시합니다.
* **필수/선택 BIOS 구분**: `rom-analyzer`의 BIOS `mandatory` 정보를 유지하므로 GBA BIOS 같은 선택 파일은 `BIOS 필요` 오류로 표시하지 않습니다.

### 6. 👥 유저별 데이터 격리 (`plugins/data/bookoasis_gamebooks/`)
* **공유 롬(ROMs) & 바이오스(BIOS)**: 서버의 `roms/` 및 `bios/` 폴더에 단 한 번만 업로드하면 모든 유저가 함께 플레이할 수 있습니다.
* **유저별 세이브 격리**: 각자의 세이브 파일(`.sav`)과 실시간 스냅샷(`.state`)이 `saves/user_{user_id}/` 폴더에 독립 저장되어 서로 덮어써지지 않습니다.
* **유저별 기록 격리**: 즐겨찾기, 최근 플레이 시간(한국 표준시 KST 기준 상대 시간), 플레이 횟수가 계정별로 각자 분리 관리됩니다.

### 7. 🧩 시스템 바이오스(BIOS) & 펌웨어 전용 관리 모달
* **독립 모달 분리**: 설정창에서 바이오스 섹션을 분리하여 `[바이오스 관리]` 전용 팝업으로 제공합니다.
* **800+ MAME 디바이스 롬 전수 연동**: 콘솔 주요 바이오스 및 서버에 보관된 800+ MAME/FBNeo 기판 디바이스 롬을 실시간 검색하고 관리할 수 있습니다.
* **5페이지 윈도우 페이징 (`<< < 1 2 3 4 5 > >>`)**: 화면 넘침 없이 페이지 이동 편의성을 극대화한 스마트 윈도우 페이징을 지원합니다.
* **드래그 앤 드롭 업로드**: 바이오스 모달 상단 영역에 파일(`.zip`, `.bin`, `.rom` 등)을 끌어다 놓으면 `bios/` 영속 폴더로 자동 업로드됩니다.

### 8. 🛡️ 관리자(Admin) 권한 제어
* 관리자(Admin) 권한을 가진 계정으로 로그인 시 **`[ROM 추가]`, `[라이브러리 동기화]`, `[바이오스 관리]`, `[홈브류 허브]`, `[무결성 진단]`, `[⚙️ 설정]`** 관리 기능이 노출됩니다. `라이브러리 전체 재구축`은 설정의 고급 라이브러리 관리에 배치됩니다.
* 일반 사용자 화면에서는 불필요한 관리 버튼을 숨기고 깔끔한 게임 플레이어/조작키/검색 UI만 제공합니다.

---

## 🎮 기종별 조작키 및 컨트롤러 가이드 (12개 기종 지원)

웹 표준 Gamepad API를 사용하여 **Xbox 컨트롤러, DualSense/DualShock(PlayStation), 닌텐도 스위치 프로콘, 8BitDo 등** 브라우저가 인식하는 게임패드를 지원합니다. 게임 실행 시 해당 기종의 조작키 탭이 자동으로 열립니다.

### 🕹️ 기종별 대표 키 매핑 안내

| 기종 / 시스템 | 방향키 / 이동 | 주요 액션 버튼 (A / B / X / Y 등) | 트리거 / 범퍼 (L / R) | START / SELECT / COIN |
| :--- | :--- | :--- | :--- | :--- |
| **SFC / SNES** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A:<kbd>X</kbd>, B:<kbd>Z</kbd>, X:<kbd>S</kbd>, Y:<kbd>A</kbd> | L:<kbd>Q</kbd>, R:<kbd>W</kbd> | Start:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |
| **GBA** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A:<kbd>X</kbd>, B:<kbd>Z</kbd> | L:<kbd>Q</kbd>, R:<kbd>W</kbd> | Start:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |
| **FC / NES / GB** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A:<kbd>X</kbd>, B:<kbd>Z</kbd> (터보:<kbd>S</kbd>/<kbd>A</kbd>) | - | Start:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |
| **N64** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A:<kbd>X</kbd>, B:<kbd>Z</kbd>, C버튼:<kbd>I</kbd><kbd>K</kbd><kbd>J</kbd><kbd>L</kbd> | Z트리거:<kbd>Q</kbd>, L:<kbd>A</kbd>, R:<kbd>S</kbd> | Start:<kbd>Enter</kbd> |
| **MD / Genesis** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A:<kbd>A</kbd>, B:<kbd>Z</kbd>, C:<kbd>X</kbd>, X/Y/Z:<kbd>Q</kbd><kbd>S</kbd><kbd>W</kbd> | - | Start:<kbd>Enter</kbd> |
| **Arcade / MAME** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | 1~4버튼:<kbd>Z</kbd><kbd>X</kbd><kbd>A</kbd><kbd>S</kbd>, 5/6버튼:<kbd>Q</kbd><kbd>W</kbd> | 서비스메뉴:<kbd>F2</kbd>/<kbd>Tab</kbd> | COIN:<kbd>Shift</kbd>/<kbd>5</kbd>, 1P:<kbd>Enter</kbd>/<kbd>1</kbd> |
| **Neo-Geo** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A:<kbd>Z</kbd>, B:<kbd>X</kbd>, C:<kbd>A</kbd>, D:<kbd>S</kbd> | - | COIN:<kbd>Shift</kbd>, START:<kbd>Enter</kbd> |
| **PS1** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | ○:<kbd>X</kbd>, ✕:<kbd>Z</kbd>, △:<kbd>S</kbd>, □:<kbd>A</kbd> | L1/R1:<kbd>Q</kbd>/<kbd>W</kbd>, L2/R2:<kbd>E</kbd>/<kbd>R</kbd> | Start:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |
| **PSP** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | ○:<kbd>X</kbd>, ✕:<kbd>Z</kbd>, △:<kbd>S</kbd>, □:<kbd>A</kbd> | L:<kbd>Q</kbd>, R:<kbd>W</kbd> | Start:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |
| **NDS** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A/B/X/Y:<kbd>X</kbd><kbd>Z</kbd><kbd>S</kbd><kbd>A</kbd> (터치: 마우스) | L:<kbd>Q</kbd>, R:<kbd>W</kbd> | Start:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |
| **Sega Saturn** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | A/B/C:<kbd>A</kbd><kbd>Z</kbd><kbd>X</kbd>, X/Y/Z:<kbd>Q</kbd><kbd>S</kbd><kbd>W</kbd> | L:<kbd>E</kbd>, R:<kbd>R</kbd> | Start:<kbd>Enter</kbd> |
| **PC Engine** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | I버튼:<kbd>X</kbd>, II버튼:<kbd>Z</kbd>, III~VI:<kbd>A</kbd><kbd>S</kbd><kbd>Q</kbd><kbd>W</kbd> | - | RUN:<kbd>Enter</kbd>, Select:<kbd>Shift</kbd> |

---

## 💡 플랫폼별 바이오스(BIOS) & 롬 파일 가이드

대부분의 콘솔(GBA, SFC, NES, MD, NDS, N64 등)은 별도의 바이오스 없이 롬 파일만으로 즉시 플레이할 수 있습니다. 단, **아케이드(Neo-Geo), PlayStation 1, 디스크 시스템** 등 특정 기종은 원활한 구동을 위해 바이오스 및 파일 형식을 확인해야 합니다.

### 🕹️ 기종별 바이오스 및 롬셋 권장 사양

| 플랫폼 / 콘솔 | 바이오스(BIOS) 필요 여부 | 권장 파일 포맷 및 바이오스 파일명 | 구동 안내 및 팁 |
| :--- | :---: | :--- | :--- |
| **SFC / SNES / GBA / GB / GBC** | ❌ **불필요** | `.sfc`, `.smc`, `.gba`, `.gb`, `.gbc` | 일반적인 카트리지 ROM은 별도 BIOS 없이 실행할 수 있습니다. |
| **FC / NES (패미컴)** | ❌ **카트리지 불필요**<br>⚠️ **FDS 디스크 필요** | `.nes` (일반 팩)<br>`.fds` + `disksys.rom` (패미컴 디스크) | 패미컴 디스크 시스템(`.fds`) 게임 실행 시 `disksys.rom` 바이오스를 함께 올려주세요. |
| **Mega Drive (메가드라이브)** | ❌ **불필요** | `.md`, `.gen`, `.smd`, `.zip` | 바이오스 없이 즉시 구동됩니다. |
| **Nintendo DS (NDS)** | ❌ **불필요** | `.nds`, `.zip` | 터치스크린 조작은 마우스 클릭 및 드래그로 지원됩니다. |
| **Nintendo 64 (N64)** | ❌ **불필요** | `.z64`, `.n64`, `.v64` | WebGL 가속 기반으로 바이오스 없이 구동됩니다. |
| **PlayStation 1 (PS1)** | ⚠️ **선택 사항 (권장)** | `.pbp` (단일 파일 권장)<br>`.cue` + `.bin`<br>`scph5501.bin` (바이오스) | • PSP용 단일 압축 포맷인 **`.pbp` 파일**이 가장 가볍고 안정적입니다.<br>• `scph5501.bin` 또는 `scph1001.bin` 바이오스를 등록하면 구동 호환성이 대폭 향상됩니다. |
| **PC Engine (PC엔진)** | ❌ **휴카드 불필요**<br>⚠️ **CD롬 필요** | `.pce`, `.sgx` (휴카드)<br>`.chd`, `.cue` + `syscard3.pce` | 일반 휴카드(`.pce`)는 바이오스 없이 실행되며, CD롬 게임은 `syscard3.pce` 시스템 카드가 필요합니다. |
| **Neo-Geo / MAME / FBNeo** | ⚠️ **기판별 바이오스 필수** | `.zip` (Non-Merged 롬셋 권장)<br>`neogeo.zip` (네오지오 기판 필수)<br>`pgm.zip` (IGS 기판 필수) | • **네오지오 게임 (킹오파, 메탈슬러그 등)**: **`neogeo.zip`**을 반드시 함께 업로드해야 `Romset is unknown` 오류 없이 실행됩니다.<br>• 아케이드 롬은 ZIP 파일명(영문 약칭) 그대로 업로드해야 코어가 인식합니다. |

---

## 📁 디렉터리 구조

```text
/home/ubuntu/BookOasis/plugins/
├── metadata/
│   └── bookoasis_gamebooks/               # [플러그인 소스 코드]
│       ├── __init__.py                    # 모듈 패키지 진입점
│       ├── bookoasis_gamebooks.py         # 플러그인 백엔드 (Flask 라우트 & 멀티 코어 감지)
│       ├── arcade_dat.db                  # 27,000+ 게임 내장 All-In-One DAT DB (CRC32 정밀 매칭)
│       ├── VERSION                        # 버전 정보 (v1.9.20)
│       ├── LICENSE                        # 오픈소스 라이선스 (GNU AGPL-3.0)
│       ├── THIRD_PARTY_NOTICES.md         # vendor 코드/참조 데이터 provenance
│       ├── index.html                     # UI 레이아웃, 조작키, 설정 & 바이오스 모달
│       ├── style.css                      # 모던 레트로 다크 테마 & 반응형 스타일
│       ├── script.js                      # 에뮬레이터 코어 제어, 커버 큐 모니터링 & 바이오스 페이징
│       ├── requirements.txt               # 의존성 라이브러리 목록 (py7zr)
│       ├── libs/rom_analyzer/             # ROM 식별·판정 엔진 vendor 스냅샷
│       ├── libs/rom_database/             # 메타데이터·DAT·코어 호환성 참조 DB 계층
│       └── README.md                      # 플러그인 상세 안내 문서
│
└── data/
    └── bookoasis_gamebooks/               # [영속 데이터 - 업데이트 시에도 영구 보존]
        ├── gba.db                         # 멀티 플랫폼 롬 메타 및 유저별 플레이 기록 DB
        ├── roms/                          # 업로드된 공용 ROM 파일 저장소 (SFC, GBA, NES, MD 등)
        ├── bios/                          # 시스템 바이오스 및 아케이드 기판 펌웨어 저장소
        ├── covers/                        # 고화질 게임 커버 아트 이미지 저장소 (또는 사용자 지정 경로)
        └── saves/                         # [유저별 독립 세이브 보관소]
            ├── user_1/                    # 유저 1 전용 세이브 (.sav, .state)
            └── user_2/                    # 유저 2 전용 세이브 (.sav, .state)
```

---

## 📚 사용된 오픈소스 및 라이브러리 (Credits & Libraries)

본 플러그인은 다음과 같은 검증된 오픈소스 라이브러리와 웹 표준 API를 기반으로 제작되었습니다.

| 라이브러리 / 에셋 | 용도 및 역할 | 라이선스 | 연동 방식 |
| :--- | :--- | :--- | :--- |
| **[EmulatorJS](https://emulatorjs.org/)** | WebAssembly 기반 멀티 플랫폼 레트로 에뮬레이터 코어 엔진 (RetroArch / Libretro Web Port) | GNU GPL-3.0 / BSD | 공식 CDN (`cdn.emulatorjs.org`) 클라이언트 동적 로드 |
| **[Font Awesome 6 Free](https://fontawesome.com/)** | UI 액션 버튼 및 게임패드 컨트롤러 벡터 아이콘 | Icons: CC BY 4.0<br>Fonts: SIL OFL 1.1<br>Code: MIT | 북오아시스 공통 CDN 및 클래스 렌더링 |
| **[Python Standard Library](https://www.python.org/)** | ROM 헤더 파싱, 멀티 코어 자동 감지, 유저 격리 SQLite DB 처리, 세이브 스트림 I/O | PSF License | 내장 모듈 (`sqlite3`, `zipfile`, `hashlib` 등) |
| **[W3C Web Gamepad API](https://w3c.github.io/gamepad/)** | Xbox / DualSense / Switch 프로콘 등 물리 게임패드 표준 입력 연동 | W3C Software Notice | 브라우저 표준 API |
| **[Web Audio API](https://www.w3.org/TR/webaudio/)** | 저지연 멀티 채널 게임 사운드 처리 및 스마트 음소거/종료 연동 | W3C Software Notice | 브라우저 표준 API |

---

## 📄 라이선스 (License)

본 플러그인은 BookOasis 본체와 동일하게 **[GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE)** 라이선스 하에 배포됩니다.

vendor 코드와 참조 DB의 출처 정보는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)를 참고하세요.



