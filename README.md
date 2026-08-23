# 🎮 Game Books (BookOasis 레트로 게임 에뮬레이터 플러그인)

**`bookoasis_gamebooks`**는 북오아시스(BookOasis) 미디어 서버에서 **닌텐도(SFC, GBA, NES, GB/GBC, NDS, N64), 세가(MD, GG, SMS, Saturn, Dreamcast), 소니(PlayStation 1, PSP), SNK(Neo-Geo MVS/AES), 아케이드(MAME, FBNeo), 아타리, PC엔진 등 전 기종의 레트로 게임**을 별도의 프로그램 설치 없이 웹 브라우저만으로 즐길 수 있도록 지원하는 고성능 WebAssembly 기반 에뮬레이터 플러그인입니다.

북오아시스의 좌측 사이드바 1등 시민(First-class Citizen) 카테고리 메뉴로 완벽히 통합되어 동작하며, 유저별 독립 클라우드 세이브, 실시간 즉시 이어하기(Instant Resume), 고화질 커버 자동 캡처 & 블랙바 크롭, 시스템 바이오스 전용 관리 모달, 게임패드 완벽 지원 등 풍부한 편의 기능을 제공합니다.

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

> 💡 **`.7z` 압축 롬 및 아케이드 롬셋 100% 전자동 처리**: `py7zr` 및 내장 All-In-One DAT DB를 통해 `.7z` 압축 파일 내부의 롬을 실시간/영구적으로 감지·변환합니다. 특히 네오지오(Neo-Geo) 등 기판 바이오스가 필요한 아케이드 롬셋은 업로드, 스캔, 풀스캔 시 자동으로 `neogeo.zip` 바이오스 칩셋을 패키징하므로 바이오스 누락 에러 없이 원클릭으로 즉시 플레이할 수 있습니다.

---

## ✨ 주요 핵심 기능

### 1. 🕹️ 브라우저 WebAssembly 에뮬레이터 & 북오아시스 전용 다크 툴바
* **독립 iframe 샌드박스 격리**: SPA 환경에서의 전역 스크립트 충돌(`let EJS_STORAGE` 재선언 에러) 및 WebAssembly 메모리 누수를 완벽 차단하여 장시간 플레이에도 브라우저가 쾌적하게 유지됩니다.
* **하단 기본 툴바 완전 차단**: EmulatorJS 기본 메뉴를 숨기고, 북오아시스 테마와 일체화된 상단 콤팩트 다크 툴바를 제공합니다.
* **💾 완전 자동 클라우드 세이브**: 게임 플레이 도중(기본 60초 주기) 및 게임 종료 시 인게임 세이브(`.sav`)와 실시간 상태 스냅샷(`.state`)이 서버에 자동 백업됩니다.
* **🚀 무중단 즉시 이어하기 (Instant Resume)**: 게임을 다시 실행하면 타이틀 화면 없이 마지막으로 플레이하던 시점 그대로 0초 만에 자동 로드되어 즉시 게임을 이어갈 수 있습니다.
* **🔄 안전한 처음부터 재시작**: 상단 재시작 버튼 클릭 시 세이브 삭제 경고 안내창이 표시되며, 확인 시 서버의 세이브 데이터를 깨끗이 초기화하고 새 게임으로 시작합니다.
* **🎨 실시간 그래픽/셰이더 설정**: CRT 레트로 브라운관 스캔라인, 2x/4x HQ 스무딩, 화면 비율(3:2, 4:3, 16:9, 꽉 채우기) 실시간 조절 및 자동 기억.
* **⏩ 자유로운 배속 제어**: 1x / 2x / 3x / 4x 고속 진행을 원클릭으로 전환합니다.
* **⏸ 일시정지 / ▶ 재생, 🔊 음소거 토글, ⛶ 전체화면** 지원.
* **❌ 스마트 종료 (`ESC`)**: 열려있는 최상위 서브 모달부터 1개씩 순차 종료되며, 최종 종료 시 WebAudio 및 WASM 사운드를 완벽 차단합니다.

### 2. ⚡ 백그라운드 커버 아트 다운로드 큐(Queue) & 실시간 배지 UI
* **논블로킹 백그라운드 다운로드**: 스캔 시 커버 다운로드로 인해 모달이 멈추지 않고, 롬 메타 스캔이 완료되면 즉시 창이 닫히며 백그라운드 워커 큐에서 커버를 순차적으로 자동 다운로드합니다.
* **상단 실시간 상태 배지**: 헤더 상단에 `🖼️ 커버 다운로드 중: 15/374 (사무라이 쇼다운)`과 같이 현재 처리 중인 게임명과 진행률이 실시간 펄스 애니메이션과 함께 표시되며, 완료 시 자동으로 사라집니다.

### 3. 📸 스마트 스크린샷 캡처, 블랙바 크롭 & Libretro 온라인 아트워크 검색
* **게임 화면 우클릭 메뉴**: 에뮬레이터 플레이 화면 어디서든 마우스 우클릭 시 `📸 현재 화면을 커버 이미지로 설정` 메뉴가 팝업됩니다.
* **지능형 블랙바 크롭 (`cropLetterboxFromBlob`)**: 캡처된 스크린샷의 상하좌우 검은 여백(레터박스/필러박스)을 픽셀 단위로 자동 감지 및 잘라내어 **완벽한 순수 인게임 비율의 고화질 커버**를 자동 생성합니다.
* **🌐 Libretro 온라인 아트워크 실시간 검색**: 게임 목록의 `[커버 변경]` 버튼을 통해 Libretro 오픈소스 데이터베이스에서 고화질 정품 패키지 박스아트를 1클릭으로 검색 및 즉시 다운로드하여 커버로 적용할 수 있습니다.
* **📁 커버 이미지 저장 폴더 사용자 지정 및 실시간 마이그레이션**: 설정에서 커버 저장 폴더(예: NAS 마운트 경로)를 지정하면, 기존 커버 이미지들을 새 폴더로 자동 이동하며 실시간 프로그레스바로 진행 상태를 안내합니다.

### 4. 🗄️ 27,000+ 통합 DAT DB 및 롬 자동 변환 / 재배치 파이프라인
* **정밀 CRC32 분석 & 클론셋 최적화**: MAME/FBNeo 및 주요 콘솔 기종의 27,000여 개 롬셋 정보를 내장한 초경량 SQLite DAT DB(`arcade_dat.db`)를 탑재하여 파일명이 영문 약칭이거나 `.7z` 압축이어도 정확한 한글 타이틀과 기종을 100% 감지하며, 클론 롬셋(예: 1944u)을 정확히 매칭합니다.
* **⚡ 초고속 비동기 풀스캔 (Non-blocking Full Scan)**: 수천 개의 롬도 브라우저 타임아웃 없이 백그라운드 워커 스레드에서 초고속으로 병렬 분석하며, 고스트(Ghost) 레코드를 완벽히 자동 정리하고 진행률을 실시간 제공합니다.
* **기종별 자동 폴더 재배치**: 스캔 시 롬이 잘못된 기종 폴더에 위치해 있으면 올바른 코어 하위 폴더(예: `snes/`, `gba/`, `arcade/` 등)로 안전하게 물리적 이동을 진행합니다.
* **아케이드 7z ➔ ZIP 자동 변환**: FBNeo WebAssembly 코어가 읽을 수 없는 `.7z` 롬셋을 발견하면 표준 `.zip` 규격으로 즉시 변환하고 네오지오 기판 칩셋을 자동 병합합니다.

### 5. 👥 유저별 완벽한 데이터 격리 (`plugins/data/bookoasis_gamebooks/`)
* **공유 롬(ROMs) & 바이오스(BIOS)**: 서버의 `roms/` 및 `bios/` 폴더에 단 한 번만 업로드하면 모든 유저가 함께 플레이할 수 있습니다.
* **유저별 세이브 격리**: 각자의 세이브 파일(`.sav`)과 실시간 스냅샷(`.state`)이 `saves/user_{user_id}/` 폴더에 독립 저장되어 서로 덮어써지지 않습니다.
* **유저별 기록 격리**: 즐겨찾기, 최근 플레이 시간(한국 표준시 KST 기준 상대 시간), 플레이 횟수가 계정별로 각자 분리 관리됩니다.

### 6. 🧩 시스템 바이오스(BIOS) & 펌웨어 전용 관리 모달
* **독립 모달 분리**: 설정창에서 바이오스 섹션을 분리하여 `[바이오스 관리]` 전용 팝업으로 제공합니다.
* **800+ MAME 디바이스 롬 전수 연동**: 콘솔 주요 바이오스 및 서버에 보관된 800+ MAME/FBNeo 기판 디바이스 롬을 실시간 검색하고 관리할 수 있습니다.
* **5페이지 윈도우 페이징 (`<< < 1 2 3 4 5 > >>`)**: 화면 넘침 없이 페이지 이동 편의성을 극대화한 스마트 윈도우 페이징을 지원합니다.
* **드래그 앤 드롭 업로드**: 바이오스 모달 상단 영역에 파일(`.zip`, `.bin`, `.rom` 등)을 끌어다 놓으면 `bios/` 영속 폴더로 자동 업로드됩니다.

### 7. 🛡️ 관리자(Admin) 권한 제어
* 관리자(Admin) 권한을 가진 계정으로 로그인 시에만 **`[ROM 업로드]`, `[바이오스 관리]`, `[홈브류 허브]`, `[⚙️ 설정]`** 메뉴가 노출됩니다.
* 일반 사용자 화면에서는 불필요한 관리 버튼을 숨기고 깔끔한 게임 플레이어/조작키/검색 UI만 제공합니다.

---

## 🎮 기종별 조작키 및 컨트롤러 가이드 (12개 기종 지원)

웹 표준 Gamepad API를 완벽 지원하여 **Xbox 컨트롤러, DualSense/DualShock(PlayStation), 닌텐도 스위치 프로콘, 8BitDo 등** 모든 게임패드를 PC/모바일/태블릿에 연결하여 즉시 플레이할 수 있습니다. 게임 실행 시 해당 기종의 조작키 탭이 자동으로 열립니다.

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
| **SFC / SNES / GBA / GB / GBC** | ❌ **불필요** | `.sfc`, `.smc`, `.gba`, `.gb`, `.gbc` | 에뮬레이터 코어 자체 가상 바이오스로 100% 즉시 구동됩니다. |
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
│       ├── VERSION                        # 버전 정보 (v1.9.1)
│       ├── LICENSE                        # 오픈소스 라이선스 (GNU AGPL-3.0)
│       ├── index.html                     # UI 레이아웃, 조작키, 설정 & 바이오스 모달
│       ├── style.css                      # 모던 레트로 다크 테마 & 반응형 스타일
│       ├── script.js                      # 에뮬레이터 코어 제어, 커버 큐 모니터링 & 바이오스 페이징
│       ├── requirements.txt               # 의존성 라이브러리 목록 (py7zr)
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



