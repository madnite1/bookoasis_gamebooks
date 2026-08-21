# 🎮 Game Books (BookOasis 레트로 게임 에뮬레이터 플러그인)

**`bookoasis_gamebooks`**는 북오아시스(BookOasis) 미디어 서버에서 **닌텐도(SFC, GBA, NES, GB/GBC, NDS, N64), 세가(MD, GG, SMS), 소니(PlayStation 1, PSP), 아케이드(MAME, FBNeo), 아타리, PC엔진 등 전 기종의 레트로 게임**을 별도의 프로그램 설치 없이 웹 브라우저만으로 즐길 수 있도록 지원하는 고성능 WebAssembly 기반 에뮬레이터 플러그인입니다.

북오아시스의 좌측 사이드바 1등 시민(First-class Citizen) 카테고리 메뉴로 완벽히 통합되어 동작하며, 유저별 독립 클라우드 세이브, 실시간 즉시 이어하기(Instant Resume), 고화질 커버 자동 캡처, 게임패드 완벽 지원 등 풍부한 편의 기능을 제공합니다.

---

## 🕹️ 지원 콘솔 플랫폼 및 롬(ROM) 포맷

| 플랫폼 / 콘솔 | 지원 확장자 | 기본 에뮬레이션 코어 |
| :--- | :--- | :--- |
| **SFC / SNES** (슈퍼패미컴) | `.sfc`, `.smc`, `.snes`, `.fig` | Snes9x |
| **GBA** (게임보이 어드밴스) | `.gba` | mGBA |
| **FC / NES** (패미컴 / 패미컴 디스크) | `.nes`, `.fds`, `.unf` | FCEUmm / Nestopia |
| **GB / GBC** (게임보이 / 컬러) | `.gb`, `.gbc` | Gambatte |
| **NDS** (닌텐도 DS) | `.nds` | DeSmuME / melonDS |
| **N64** (닌텐도 64) | `.n64`, `.z64`, `.v64` | Mupen64Plus |
| **Mega Drive / Genesis** (메가드라이브) | `.md`, `.gen`, `.smd` | Genesis Plus GX |
| **Master System / Game Gear** | `.sms`, `.gg`, `.sg` | SMS Plus GX |
| **PlayStation 1 (PS1)** | `.psx`, `.ps1`, `.pbp`, `.cue` | PCSX ReARMed |
| **PSP** (PlayStation Portable) | `.cso`, `.pbp` | PPSSPP |
| **PC Engine / TurboGrafx-16** | `.pce`, `.sgx` | Mednafen PCE |
| **WonderSwan / Color** | `.ws`, `.wsc` | Mednafen Swan |
| **Neo Geo Pocket / Color** | `.ngp`, `.ngc` | Mednafen NGP |
| **Atari 2600 / 5200 / 7800 / Lynx** | `.a26`, `.a52`, `.a78`, `.lnx` | Stella / ProSystem |
| **Arcade / MAME / FBNeo** | `.zip`, `.7z` | FBNeo / MAME 2003+ |

---

## ✨ 주요 핵심 기능

### 1. 🕹️ 브라우저 WebAssembly 에뮬레이터 & 북오아시스 전용 다크 툴바
* **하단 기본 툴바 완전 차단**: EmulatorJS 기본 메뉴를 숨기고, 북오아시스 테마와 일체화된 상단 콤팩트 다크 툴바를 제공합니다.
* **💾 완전 자동 클라우드 세이브**: 게임 플레이 도중(기본 60초 주기) 및 게임 종료 시 인게임 세이브(`.sav`)와 실시간 상태 스냅샷(`.state`)이 서버에 자동 백업됩니다.
* **🚀 무중단 즉시 이어하기 (Instant Resume)**: 게임을 다시 실행하면 타이틀 화면 없이 마지막으로 플레이하던 시점 그대로 0초 만에 자동 로드되어 즉시 게임을 이어갈 수 있습니다.
* **🔄 안전한 처음부터 재시작**: 상단 재시작 버튼 클릭 시 세이브 삭제 경고 안내창이 표시되며, 확인 시 서버의 세이브 데이터를 깨끗이 초기화하고 새 게임으로 시작합니다.
* **🎨 실시간 그래픽/셰이더 설정**: CRT 레트로 브라운관 스캔라인, 2x/4x HQ 스무딩, 화면 비율(3:2, 4:3, 16:9, 꽉 채우기) 실시간 조절 및 자동 기억.
* **⏩ 자유로운 배속 제어**: 1x / 2x / 3x / 4x 고속 진행을 원클릭으로 전환합니다.
* **⏸ 일시정지 / ▶ 재생, 🔊 음소거 토글, ⛶ 전체화면** 지원.
* **❌ 스마트 종료 (`ESC`)**: 열려있는 최상위 서브 모달부터 1개씩 순차 종료되며, 최종 종료 시 WebAudio 및 WASM 사운드를 완벽 차단합니다.

### 2. 📸 마우스 우클릭 커스텀 메뉴 & 레터박스 자동 제거 커버 캡처
* **게임 화면 우클릭 메뉴**: 에뮬레이터 플레이 화면 어디서든 마우스 우클릭 시 전용 메뉴가 팝업됩니다.
* **스마트 커버 생성**: RetroArch VRAM 네이티브 프레임버퍼 덤프 엔진을 통해 **상하좌우 검은 여백(레터박스)이 전혀 없는 100% 순수 원본 픽셀의 무손실 고화질 스크린샷**을 캡처하여 게임 커버로 즉시 등록합니다.

### 3. 👥 유저별 완벽한 데이터 격리 (`plugins/data/bookoasis_gamebooks/`)
* **공유 롬(ROMs)**: 서버의 `roms/` 폴더에 단 한 번만 업로드하면 모든 유저가 함께 플레이할 수 있습니다.
* **유저별 세이브 격리**: 각자의 세이브 파일(`.sav`)과 실시간 스냅샷(`.state`)이 `saves/user_{user_id}/` 폴더에 독립 저장되어 서로 덮어써지지 않습니다.
* **유저별 기록 격리**: 즐겨찾기, 최근 플레이 시간(한국 표준시 KST 기준 상대 시간), 플레이 횟수가 계정별로 각자 분리 관리됩니다.

### 4. ⚙️ 관리자(Admin) 전용 설정 관리
* 관리자(Admin) 권한을 가진 계정으로 로그인 시에만 상단 툴바에 **`[⚙️ 설정]` 톱니바퀴 버튼**이 노출됩니다.
* 일반 사용자에게는 설정 버튼이 노출되지 않으며, 백엔드 API 수준에서도 비인가 사용자의 설정 변경이 원천 차단됩니다.
* 기본 저장소 외에 대용량 외부 디렉터리를 연결할 수 있는 **추가 ROM 폴더 경로(`EXTRA_ROMS_PATH`)** 및 자동 세이브 주기를 자유롭게 설정할 수 있습니다.

---

## 🎮 기본 조작키 및 컨트롤러 가이드

웹 표준 Gamepad API를 완벽 지원하여 **Xbox 컨트롤러, DualSense/DualShock(PlayStation), 닌텐도 스위치 프로콘, 8BitDo 등** 모든 게임패드를 PC/모바일/태블릿에 연결하여 즉시 플레이할 수 있습니다.

### 대표 기종별 기본 키 매핑

| 패드 버튼 / 콘솔 기능 | 키보드 키 | 게임패드 (Xbox / PS / Switch) |
| :--- | :--- | :--- |
| **방향키 (D-Pad)** | <kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd> | 십자키 / 왼쪽 아날로그 스틱 |
| **A / 동그라미 버튼** | <kbd>X</kbd> | <kbd>A</kbd> (Xbox) / <kbd>✕</kbd> (PS) / <kbd>B</kbd> (Switch) |
| **B / 엑스 버튼** | <kbd>Z</kbd> | <kbd>B</kbd> (Xbox) / <kbd>○</kbd> (PS) / <kbd>A</kbd> (Switch) |
| **X / 세모 버튼** | <kbd>S</kbd> | <kbd>X</kbd> (Xbox) / <kbd>□</kbd> (PS) / <kbd>Y</kbd> (Switch) |
| **Y / 네모 버튼** | <kbd>A</kbd> | <kbd>Y</kbd> (Xbox) / <kbd>△</kbd> (PS) / <kbd>X</kbd> (Switch) |
| **L 트리거 / 범퍼** | <kbd>Q</kbd> | <kbd>LB</kbd> / <kbd>L1</kbd> / <kbd>L</kbd> |
| **R 트리거 / 범퍼** | <kbd>W</kbd> | <kbd>RB</kbd> / <kbd>R1</kbd> / <kbd>R</kbd> |
| **START** | <kbd>Enter</kbd> | <kbd>Start</kbd> / <kbd>Options</kbd> / <kbd>+</kbd> |
| **SELECT** | <kbd>Shift</kbd> | <kbd>Back</kbd> / <kbd>Share</kbd> / <kbd>-</kbd> |
| **커버 이미지 캡처** | **마우스 우클릭** | 화면 우클릭 ➔ `📸 현재 화면을 커버 이미지로 설정` |
| **모달 닫기 / 게임 종료** | <kbd>ESC</kbd> | 상위 모달 순차 닫기 / 화면 상단 [나가기] 버튼 |

---

## 📁 디렉터리 구조

```text
/home/ubuntu/BookOasis/plugins/
├── metadata/
│   └── bookoasis_gamebooks/               # [플러그인 소스 코드]
│       ├── __init__.py                    # 모듈 패키지 진입점
│       ├── bookoasis_gamebooks.py         # 플러그인 백엔드 (Flask 라우트 & 멀티 코어 감지)
│       ├── VERSION                        # 버전 정보 (v1.0.0)
│       ├── LICENSE                        # 오픈소스 라이선스 (GNU AGPL-3.0)
│       ├── index.html                     # UI 레이아웃 및 조작키/설정 모달
│       ├── style.css                      # 모던 레트로 다크 테마 & 반응형 스타일
│       ├── script.js                      # 에뮬레이터 코어 제어 & 세이브/패드 브릿지
│       └── README.md                      # 플러그인 상세 안내 문서
│
└── data/
    └── bookoasis_gamebooks/               # [영속 데이터 - 업데이트 시에도 영구 보존]
        ├── gba.db                         # 멀티 플랫폼 롬 메타 및 유저별 플레이 기록 DB
        ├── roms/                          # 업로드된 공용 ROM 파일 저장소 (SFC, GBA, NES, MD 등)
        ├── covers/                        # 고화질 게임 커버 아트 이미지 저장소
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



