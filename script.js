// plugins/metadata/bookoasis_gamebooks/script.js
(function () {
  'use strict';

  console.log('[BookOasis Game Books] Initializing Game Books plugin with user isolation & custom toolbar...');

  const PLUGIN_ID = 'bookoasis_gamebooks';
  const API_DASHBOARD = `/api/media/dashboard/widgets/${PLUGIN_ID}/data`;
  const API_WEBHOOK = `/api/webhook/${PLUGIN_ID}`;

  // 전역 상태
  let state = {
    games: [],
    category: 'all',
    sort: localStorage.getItem('gba_library_sort') || 'newest',
    isFavoriteOnly: false,
    searchQuery: '',
    userId: 1,
    isAdmin: false,
    config: {
      cloud_save_enabled: true,
      auto_save_interval_sec: 60,
      extra_roms_path: '',
    },
    activeGame: null,
    autoSaveIntervalId: null,
    targetGameForCover: null,
    gamepadPollId: null,
    lastGamepadStates: {},
    isPaused: false,
    currentSpeed: 1,
    isMuted: false,
    homebrewPage: 1,
    homebrewBusy: false,
    graphics: {
      shader: localStorage.getItem('gba_shader') || 'disabled',
      pixelMode: localStorage.getItem('gba_pixel_mode') || 'pixelated',
      aspectRatio: localStorage.getItem('gba_aspect_ratio') || '3/2',
    },
    available_bios: [],
    biosPage: 1,
    biosPageSize: 10,
    biosSearch: '',
    biosFilter: 'all',
    renderedCount: 40,
    pageSize: 40,
    filteredGames: [],
    scrollObserver: null,
    coverQueuePollTimer: null,
    coverQueue: {
      is_running: false,
      total: 0,
      completed: 0,
      failed: 0,
      remaining: 0,
      current_title: '',
    },
  };

  // DOM 헬퍼
  const $ = (id) => document.getElementById(id);

  // --------------------------------------------------------------------------
  // API 통신 헬퍼
  // --------------------------------------------------------------------------
  async function apiCall(action, params = {}, method = 'GET', body = null) {
    const url = new URL(API_DASHBOARD, window.location.origin);
    url.searchParams.set('type', 'general');
    url.searchParams.set('action', action);
    url.searchParams.set('user_id', state.userId);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) {
        url.searchParams.set(k, v);
      }
    }

    const options = {
      method,
      credentials: 'same-origin',
    };
    if (body) {
      options.body = body;
    }

    const res = await fetch(url.toString(), options);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  // --------------------------------------------------------------------------
  // 데이터 로드 & 렌더링
  // --------------------------------------------------------------------------
  async function loadLibrary(silent = false) {
    if (!silent) showLoading(true);
    try {
      const data = await apiCall('list_games');
      if (data.success) {
        state.games = data.games || [];
        if (data.user_id) {
          state.userId = data.user_id;
        }
        if (data.config) {
          state.config = Object.assign(state.config, data.config);
        }

        state.available_bios = data.available_bios || [];

        state.isAdmin = !!data.is_admin;

        // 관리자 전용 UI 제어 (ROM 업로드, 바이오스 업로드, 홈브류 허브, 설정)
        document.querySelectorAll('.gba-admin-only').forEach((el) => {
          if (el.tagName === 'BUTTON') {
            el.style.display = state.isAdmin ? 'inline-flex' : 'none';
          } else {
            el.style.display = state.isAdmin ? 'block' : 'none';
          }
        });

        const emptyDesc = $('gbaEmptyStateDesc');
        if (emptyDesc) {
          if (state.isAdmin) {
            emptyDesc.innerHTML = '지원하는 롬 파일(<code>.sfc</code>, <code>.gba</code>, <code>.nes</code>, <code>.zip</code> 등)을 업로드하거나, 배포가 허용된 무료 홈브류 게임을 Homebrew Hub에서 바로 등록하세요.';
          } else {
            emptyDesc.textContent = '현재 등록된 게임이 없습니다. 관리자가 게임을 등록할 때까지 기다려 주세요.';
          }
        }

        renderGames();
        if ($('gbaBiosModal') && $('gbaBiosModal').style.display === 'flex') {
          renderBiosModal();
        }

        // 백그라운드 커버 큐 감시 시작
        startCoverQueueMonitor();
      } else {
        showToast('게임 목록을 불러오지 못했습니다: ' + (data.error || '알 수 없는 오류'), true);
      }
    } catch (err) {
      console.error('[GBA] Load library error:', err);
      showToast('서버와 통신 중 오류가 발생했습니다.', true);
    } finally {
      if (!silent) showLoading(false);
    }
  }

  // --------------------------------------------------------------------------
  // 백그라운드 커버 다운로드 큐 실시간 모니터링 & UI 연동
  // --------------------------------------------------------------------------
  function startCoverQueueMonitor() {
    if (state.coverQueuePollTimer) return;

    const checkQueue = async () => {
      try {
        const res = await apiCall('cover_queue_status');
        if (res && res.success && res.cover_queue) {
          const q = res.cover_queue;
          state.coverQueue = q;
          updateCoverQueueBadge(q);

          // 큐가 완료되었을 때 마지막으로 라이브러리 커버 실시간 리로드
          if (!q.is_running && q.remaining === 0 && q.total > 0 && q.completed > 0) {
            // 다운로드 작업이 막 끝났다면 한 번 라이브러리를 조용히 갱신
            if (q._refreshed !== true) {
              q._refreshed = true;
              loadLibrary(true);
            }
          }
        }
      } catch (e) {
        // ignore
      }
    };

    // 최초 1회 즉시 실행
    checkQueue();
    // 2초 주기로 백그라운드 큐 상태 지속 감시
    state.coverQueuePollTimer = setInterval(checkQueue, 2000);
  }

  function updateCoverQueueBadge(q) {
    const badge = $('gbaCoverQueueBadge');
    const textEl = $('gbaCoverQueueText');
    if (!badge || !textEl) return;

    if (q.is_running || (q.remaining && q.remaining > 0)) {
      badge.style.display = 'inline-flex';
      const completed = q.completed || 0;
      const failed = q.failed || 0;
      const total = q.total || (completed + failed + (q.remaining || 0)) || 1;
      const processed = Math.min(total, completed + failed || (total - (q.remaining || 0)));
      const title = q.current_title ? ` (${q.current_title})` : '';
      textEl.textContent = `커버 다운로드 중: ${processed}/${total}${title}`;
      badge.title = `백그라운드 커버 다운로드 진행 중: ${processed}/${total} 처리 (${completed}개 성공, ${failed}개 미발견)\n현재: ${q.current_title || '준비 중...'}`;
    } else {
      badge.style.display = 'none';
    }
  }

  function renderGames(resetPaging = true) {
    const grid = $('gbaGameGrid');
    const emptyState = $('gbaEmptyState');
    const countEl = $('gbaGameCount');
    const sentinel = $('gbaScrollSentinel');

    if (resetPaging) {
      state.renderedCount = state.pageSize;

      // 검색 및 필터 적용
      state.filteredGames = state.games.filter((g) => {
        // 즐겨찾기 단독 필터
        if (state.isFavoriteOnly && !g.is_favorite) return false;

        // 기종 드롭다운 카테고리 필터
        if (state.category === 'snes' && g.core !== 'snes' && g.platform !== 'SNES') return false;
        if (state.category === 'gba' && g.core !== 'gba' && g.platform !== 'GBA') return false;
        if (state.category === 'nes' && g.core !== 'nes' && g.platform !== 'NES' && g.platform !== 'FDS') return false;
        if (state.category === 'gb' && g.core !== 'gb' && g.core !== 'gbc' && g.platform !== 'GB' && g.platform !== 'GBC') return false;
        if (state.category === 'nds' && g.core !== 'nds' && g.platform !== 'NDS') return false;
        if (state.category === 'n64' && g.core !== 'n64' && g.platform !== 'N64') return false;
        if (state.category === 'genesis' && !['segaMD', 'segaMS', 'segaGG', 'sega32x', 'segaCD', 'segaSaturn'].includes(g.core) && !['Genesis', 'MasterSystem', 'GameGear', 'Sega32X', 'Saturn'].includes(g.platform)) return false;
        if (state.category === 'psx' && g.core !== 'psx' && g.platform !== 'PS1') return false;
        if (state.category === 'psp' && g.core !== 'psp' && g.platform !== 'PSP') return false;
        if (state.category === 'arcade' && g.core !== 'arcade' && g.core !== 'mame2003' && g.platform !== 'Arcade' && g.platform !== 'Neo-Geo') return false;
        if (state.category === 'neogeo' && g.platform !== 'Neo-Geo' && g.platform !== 'NEOGEO') return false;
        if (state.category === 'other') {
          const mainPlatforms = ['SNES', 'GBA', 'NES', 'FDS', 'GB', 'GBC', 'NDS', 'N64', 'Genesis', 'MasterSystem', 'GameGear', 'Sega32X', 'Saturn', 'PS1', 'PSP', 'Arcade', 'Neo-Geo', 'NEOGEO'];
          if (mainPlatforms.includes(g.platform)) return false;
        }

        // 검색어
        if (state.searchQuery) {
          const q = state.searchQuery.toLowerCase();
          const titleMatch = (g.title || '').toLowerCase().includes(q);
          const fileMatch = (g.filename || '').toLowerCase().includes(q);
          const codeMatch = (g.game_code || '').toLowerCase().includes(q);
          if (!titleMatch && !fileMatch && !codeMatch) return false;
        }
        return true;
      });

      // 정렬 (최신 등록순 / 가나다순 / 최근 플레이순)
      state.filteredGames.sort((a, b) => {
        if (state.sort === 'title') {
          return (a.title || '').localeCompare(b.title || '', 'ko');
        }
        if (state.sort === 'recent') {
          const tA = a.last_played_at || '';
          const tB = b.last_played_at || '';
          if (tA && !tB) return -1;
          if (!tA && tB) return 1;
          if (tA && tB) return tB.localeCompare(tA);
          return (b.added_at || '').localeCompare(a.added_at || '');
        }
        // default: newest
        return (b.added_at || '').localeCompare(a.added_at || '');
      });

      grid.innerHTML = '';
    }

    const filtered = state.filteredGames;
    countEl.textContent = `${filtered.length}개의 게임 (전체 ${state.games.length}개)`;

    if (filtered.length === 0) {
      grid.style.display = 'none';
      if (sentinel) sentinel.style.display = 'none';
      emptyState.style.display = 'flex';
      return;
    }

    emptyState.style.display = 'none';
    grid.style.display = 'grid';

    // 현재 렌더링할 범위 (Paging Window)
    const currentLength = grid.children.length;
    const targetSlice = filtered.slice(currentLength, state.renderedCount);

    const fragment = document.createDocumentFragment();
    targetSlice.forEach((game) => {
      const card = createGameCard(game);
      fragment.appendChild(card);
    });
    grid.appendChild(fragment);

    // 다음 페이지가 남아있으면 센티넬 활성화
    if (sentinel) {
      if (grid.children.length < filtered.length) {
        sentinel.style.display = 'block';
        initScrollObserver();
      } else {
        sentinel.style.display = 'none';
      }
    }
  }

  function loadMoreGames() {
    if (!state.filteredGames) return;
    const grid = $('gbaGameGrid');
    if (!grid || grid.children.length >= state.filteredGames.length) return;

    state.renderedCount += state.pageSize;
    renderGames(false);
  }

  function initScrollObserver() {
    if (state.scrollObserver) return;
    const sentinel = $('gbaScrollSentinel');
    if (!sentinel) return;

    state.scrollObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          loadMoreGames();
        }
      });
    }, { rootMargin: '300px' });

    state.scrollObserver.observe(sentinel);
  }

  const SYSTEM_DISPLAY_MAP = {
    'snes': { label: 'SFC', colorClass: 'gba-badge-snes', name: '슈퍼패미컴 (SNES)' },
    'gba': { label: 'GBA', colorClass: 'gba-badge-gba', name: '게임보이 어드밴스' },
    'nes': { label: 'FC / NES', colorClass: 'gba-badge-nes', name: '패미컴 / NES' },
    'fds': { label: 'FDS', colorClass: 'gba-badge-nes', name: '패미컴 디스크' },
    'gb': { label: 'GB', colorClass: 'gba-badge-gb', name: '게임보이' },
    'gbc': { label: 'GBC', colorClass: 'gba-badge-gbc', name: '게임보이 컬러' },
    'nds': { label: 'NDS', colorClass: 'gba-badge-nds', name: '닌텐도 DS' },
    'n64': { label: 'N64', colorClass: 'gba-badge-n64', name: '닌텐도 64' },
    'genesis': { label: 'MD', colorClass: 'gba-badge-genesis', name: '메가드라이브' },
    'segamd': { label: 'MD', colorClass: 'gba-badge-genesis', name: '메가드라이브' },
    'mastersystem': { label: 'SMS', colorClass: 'gba-badge-genesis', name: '마스터 시스템' },
    'gamegear': { label: 'GG', colorClass: 'gba-badge-genesis', name: '게임기어' },
    'sega32x': { label: '32X', colorClass: 'gba-badge-genesis', name: '세가 32X' },
    'segacd': { label: 'MegaCD', colorClass: 'gba-badge-genesis', name: '메가 CD' },
    'saturn': { label: 'Saturn', colorClass: 'gba-badge-genesis', name: '세가 새턴' },
    'segasaturn': { label: 'Saturn', colorClass: 'gba-badge-genesis', name: '세가 새턴' },
    'ps1': { label: 'PS1', colorClass: 'gba-badge-psx', name: '플레이스테이션 1' },
    'psx': { label: 'PS1', colorClass: 'gba-badge-psx', name: '플레이스테이션 1' },
    'psp': { label: 'PSP', colorClass: 'gba-badge-psp', name: '플레이스테이션 포터블' },
    'arcade': { label: 'Arcade', colorClass: 'gba-badge-arcade', name: '아케이드 (FBNeo)' },
    'mame2003': { label: 'MAME', colorClass: 'gba-badge-arcade', name: 'MAME 2003 Plus' },
    'neogeo': { label: 'Neo-Geo', colorClass: 'gba-badge-neogeo', name: 'SNK 네오지오 (MVS/AES)' },
    'neo-geo': { label: 'Neo-Geo', colorClass: 'gba-badge-neogeo', name: 'SNK 네오지오 (MVS/AES)' },
    'pce': { label: 'PCE', colorClass: 'gba-badge-other', name: 'PC엔진' },
    'pcfx': { label: 'PC-FX', colorClass: 'gba-badge-other', name: 'PC-FX' },
    'wonderswan': { label: 'WS', colorClass: 'gba-badge-other', name: '원더스완' },
    'wonderswancolor': { label: 'WSC', colorClass: 'gba-badge-other', name: '원더스완 컬러' },
    'ngp': { label: 'NGP', colorClass: 'gba-badge-other', name: '네오지오 포켓' },
    'ngpc': { label: 'NGPC', colorClass: 'gba-badge-other', name: '네오지오 포켓 컬러' },
    'atari2600': { label: 'Atari', colorClass: 'gba-badge-other', name: '아타리 2600' },
    'atari5200': { label: 'A5200', colorClass: 'gba-badge-other', name: '아타리 5200' },
    'atari7800': { label: 'A7800', colorClass: 'gba-badge-other', name: '아타리 7800' },
    'lynx': { label: 'Lynx', colorClass: 'gba-badge-other', name: '아타리 링스' },
    'jaguar': { label: 'Jaguar', colorClass: 'gba-badge-other', name: '아타리 재규어' },
    'coleco': { label: 'Coleco', colorClass: 'gba-badge-other', name: '콜레코비전' },
    'amiga': { label: 'Amiga', colorClass: 'gba-badge-other', name: '코모도어 아미가' },
    'c64': { label: 'C64', colorClass: 'gba-badge-other', name: '코모도어 64' },
  };

  function getSystemInfo(game) {
    const rawPlat = (game.platform || '').toLowerCase();
    const rawCore = (game.core || '').toLowerCase();
    if (rawPlat && SYSTEM_DISPLAY_MAP[rawPlat]) return SYSTEM_DISPLAY_MAP[rawPlat];
    if (rawCore && SYSTEM_DISPLAY_MAP[rawCore]) return SYSTEM_DISPLAY_MAP[rawCore];
    return {
      label: game.platform || (game.core ? game.core.toUpperCase() : 'RETRO'),
      colorClass: 'gba-badge-other',
      name: game.platform || '레트로 게임',
    };
  }

  function createGameCard(game) {
    const card = document.createElement('div');
    card.className = 'gba-card';
    card.dataset.id = game.id;

    // 포맷팅
    const sizeMb = (game.size_bytes / (1024 * 1024)).toFixed(1) + ' MB';
    const lastPlayed = formatRelativeTime(game.last_played_at);
    const hasCover = !!game.cover_path;
    const sysInfo = getSystemInfo(game);

    // 커버 영역 (브라우저 디스크 캐시 즉시 활용)
    let coverHtml = '';
    if (hasCover) {
      coverHtml = `
        <img src="${game.cover_url}" alt="${escapeHtml(game.title)}" class="gba-card-cover" loading="lazy" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';">
        <div class="gba-card-default-cover" style="display: none;">
          <i class="fa-solid fa-gamepad"></i>
          <span>${escapeHtml(sysInfo.label)}</span>
        </div>
      `;
    } else {
      coverHtml = `
        <div class="gba-card-default-cover">
          <i class="fa-solid fa-gamepad"></i>
          <span>${escapeHtml(sysInfo.label)}</span>
        </div>
      `;
    }

    // 필요한 바이오스 미설치 여부 검사
    const biosList = (state.available_bios || []).map((b) => b.toLowerCase());
    const neededBios = (game.needed_bios || '').trim().toLowerCase();
    const isBiosMissing = neededBios && !biosList.includes(neededBios);

    card.innerHTML = `
      <div class="gba-card-cover-wrap" data-action="play">
        ${coverHtml}
        <div class="gba-card-overlay">
          <button class="gba-play-overlay-btn" title="게임 실행">
            <i class="fa-solid fa-play"></i>
          </button>
        </div>
        <div class="gba-card-badges">
          <span class="gba-badge ${sysInfo.colorClass}" title="${escapeHtml(sysInfo.name)}">${escapeHtml(sysInfo.label)}</span>
          ${game.has_save ? `<span class="gba-badge gba-badge-save" title="클라우드 세이브 보관됨"><i class="fa-solid fa-floppy-disk"></i> SAVE</span>` : ''}
        </div>
        ${isBiosMissing ? `
          <div class="gba-card-missing-bios" title="구동 필수 바이오스 '${escapeHtml(neededBios)}' 누락됨 (바이오스 업로드 필요)">
            <i class="fa-solid fa-triangle-exclamation"></i>
            <span>${escapeHtml(neededBios)} 필요</span>
          </div>
        ` : ''}
        <button class="gba-card-fav-btn ${game.is_favorite ? 'active' : ''}" data-action="toggle-fav" title="${game.is_favorite ? '즐겨찾기 해제' : '즐겨찾기 추가'}">
          <i class="fa-${game.is_favorite ? 'solid' : 'regular'} fa-star"></i>
        </button>
      </div>

      <div class="gba-card-content">
        <h3 class="gba-card-title" title="${escapeHtml(game.title)}">${escapeHtml(game.title)}</h3>
        <div class="gba-card-meta">
          <span title="최근 플레이: ${game.last_played_at || '기록 없음'}">${lastPlayed}</span>
        </div>
      </div>

      <div class="gba-card-footer">
        <span class="gba-card-filesize">${sizeMb}</span>
        <div class="gba-card-actions">
          <button class="gba-card-icon-btn" data-action="edit-title" title="이름 변경"><i class="fa-solid fa-pen"></i></button>
          ${state.isAdmin ? `
            <button class="gba-card-icon-btn" data-action="set-cover" title="커버 이미지 등록"><i class="fa-regular fa-image"></i></button>
            <button class="gba-card-icon-btn gba-btn-danger" data-action="delete" title="게임 삭제"><i class="fa-regular fa-trash-can"></i></button>
          ` : ''}
        </div>
      </div>
    `;

    // 이벤트 리스너 등록 (카드 전체 클릭 시 게임 실행, 액션 버튼 클릭 시 개별 동작)
    card.addEventListener('click', (e) => {
      const actionBtn = e.target.closest('[data-action]');
      if (actionBtn) {
        const action = actionBtn.dataset.action;
        if (action === 'toggle-fav') {
          e.stopPropagation();
          toggleFavorite(game.id);
          return;
        } else if (action === 'edit-title') {
          e.stopPropagation();
          editGameTitle(game);
          return;
        } else if (action === 'set-cover') {
          e.stopPropagation();
          promptCoverUpload(game.id);
          return;
        } else if (action === 'delete') {
          e.stopPropagation();
          confirmDeleteGame(game);
          return;
        }
      }

      // 카드 전체(커버, 제목, 본문 등) 클릭 시 게임 즉시 실행
      launchGame(game);
    });

    return card;
  }

  // --------------------------------------------------------------------------
  // 시스템 바이오스(BIOS) 점검 및 상태 관리
  // --------------------------------------------------------------------------
  const FULL_BIOS_CATALOG = [
    // 소니 (Sony)
    { id: 'scph5501', system: 'PS1', name: 'PlayStation 1 (북미판)', file: 'scph5501.bin', desc: '북미판 PS1 게임 호환성 권장 바이오스', required: true },
    { id: 'scph5500', system: 'PS1', name: 'PlayStation 1 (일판)', file: 'scph5500.bin', desc: '일본판 PS1 게임 구동 바이오스', required: false },
    { id: 'scph5502', system: 'PS1', name: 'PlayStation 1 (유럽판)', file: 'scph5502.bin', desc: '유럽(PAL)판 PS1 게임 구동 바이오스', required: false },
    { id: 'scph1001', system: 'PS1', name: 'PlayStation 1 (초기형)', file: 'scph1001.bin', desc: '초기형 PS1 호환 바이오스', required: false },
    { id: 'scph7001', system: 'PS1', name: 'PlayStation 1 (슬림형)', file: 'scph7001.bin', desc: '후기형 PS1 호환 바이오스', required: false },
    { id: 'ps2_bios', system: 'PS2', name: 'PlayStation 2', file: 'scph39001.bin', desc: 'PlayStation 2 시스템 펌웨어', required: false },

    // 아케이드 (Arcade / SNK / Capcom / IGS / Sega / Namco)
    { id: 'neogeo', system: 'Neo-Geo', name: '네오지오 MVS/AES 기판', file: 'neogeo.zip', desc: '메탈슬러그, KOF, 사무라이쇼다운 등 네오지오 필수 롬', required: true },
    { id: 'pgm', system: 'Arcade', name: 'IGS PGM 아케이드 기판', file: 'pgm.zip', desc: '삼국전기, 데몬프론트, 오리엔탈레전드 필수 롬', required: true },
    { id: 'qsound', system: 'Arcade', name: '캡콤 Q-Sound 오디오', file: 'qsound.zip', desc: 'CPS-2 캡콤 아케이드 오디오 드라이버 롬', required: true },
    { id: 'naomi', system: 'Arcade', name: '세가 NAOMI 기판', file: 'naomi.zip', desc: '세가 나오미 1/2 아케이드 기판 바이오스', required: false },
    { id: 'stvbios', system: 'Arcade', name: '세가 ST-V 기판', file: 'stvbios.zip', desc: '세가 타이탄 비디오(ST-V) 아케이드 기판', required: false },
    { id: 'awbios', system: 'Arcade', name: '사미 아토미스웨이브', file: 'awbios.zip', desc: 'Sammy Atomiswave 아케이드 기판', required: false },
    { id: 'skns', system: 'Arcade', name: '슈퍼 카네코 노바 시스템', file: 'skns.zip', desc: '세이부 축구 등 카네코 기판 바이오스', required: false },
    { id: 'decocass', system: 'Arcade', name: '데코 카세트 시스템', file: 'decocass.zip', desc: 'Data East 카세트 시스템', required: false },
    { id: 'namco50', system: 'Arcade', name: '남코 시스템 50/51/52', file: 'namco50.zip', desc: '남코 클래식 아케이드 칩셋 롬', required: false },
    { id: 'konamigv', system: 'Arcade', name: '코나미 GV 시스템', file: 'konamigv.zip', desc: '코나미 90년대 아케이드 기판', required: false },
    { id: 'taitofx1', system: 'Arcade', name: '타이토 FX-1 기판', file: 'taitofx1.zip', desc: '타이토 3D 아케이드 기판', required: false },

    // 닌텐도 (Nintendo)
    { id: 'disksys', system: 'FDS', name: '패미컴 디스크 시스템', file: 'disksys.rom', desc: '패미컴 디스크 시스템 (.fds) 게임 필수 바이오스', required: true },
    { id: 'bios7', system: 'NDS', name: '닌텐도 DS ARM7 바이오스', file: 'bios7.bin', desc: '닌텐도 DS 코어 구동 및 호환성 펌웨어', required: false },
    { id: 'bios9', system: 'NDS', name: '닌텐도 DS ARM9 바이오스', file: 'bios9.bin', desc: '닌텐도 DS 메인 프로세서 바이오스', required: false },
    { id: 'firmware_nds', system: 'NDS', name: '닌텐도 DS 펌웨어', file: 'firmware.bin', desc: '닌텐도 DS 오리지널 펌웨어 롬', required: false },
    { id: 'gba_bios', system: 'GBA', name: '게임보이 어드밴스', file: 'gba_bios.bin', desc: 'GBA 인트로 부팅 및 특수 연산 최적화', required: false },
    { id: 'gb_bios', system: 'GB', name: '게임보이 오리지널', file: 'gb_bios.bin', desc: '초대 게임보이(DMG) 부트 롬', required: false },
    { id: 'gbc_bios', system: 'GBC', name: '게임보이 컬러', file: 'gbc_bios.bin', desc: '게임보이 컬러 부트 롬', required: false },

    // 세가 (SEGA)
    { id: 'bios_cd_u', system: 'SegaCD', name: '메가 CD (북미판)', file: 'bios_cd_u.bin', desc: 'Sega CD (Mega CD) 북미 게임 구동 필수', required: true },
    { id: 'bios_cd_j', system: 'SegaCD', name: '메가 CD (일판)', file: 'bios_cd_j.bin', desc: 'Mega CD 일본 게임 구동 필수', required: false },
    { id: 'bios_cd_e', system: 'SegaCD', name: '메가 CD (유럽판)', file: 'bios_cd_e.bin', desc: 'Mega CD 유럽 게임 구동 필수', required: false },
    { id: 'saturn_bios', system: 'Saturn', name: '세가 새턴 (Saturn)', file: 'saturn_bios.bin', desc: '세가 새턴 에뮬레이션 호환성 향상', required: false },
    { id: 'dc_boot', system: 'Dreamcast', name: '세가 드림캐스트', file: 'dc_boot.bin', desc: '드림캐스트 부트 롬 바이오스', required: false },

    // NEC / 파나소닉 / 아타리 / 코모도어 등
    { id: 'syscard3', system: 'PCE', name: 'PC엔진 CD 시스템카드 v3.0', file: 'syscard3.pce', desc: 'PC Engine CD-ROM² 및 Super CD-ROM² 필수 카드', required: true },
    { id: 'pcfx_bios', system: 'PC-FX', name: 'NEC PC-FX 바이오스', file: 'pcfxbios.bin', desc: 'NEC PC-FX 콘솔 바이오스', required: false },
    { id: '3dobios', system: '3DO', name: '파나소닉 3DO 바이오스', file: '3dobios.rom', desc: 'Panasonic 3DO Interactive Multiplayer 필수 롬', required: true },
    { id: 'lynx_boot', system: 'Lynx', name: '아타리 링스 (Lynx)', file: 'lynxboot.img', desc: 'Atari Lynx 부트 롬 바이오스', required: false },
    { id: 'atari5200_bios', system: 'Atari', name: '아타리 5200 바이오스', file: '5200.rom', desc: 'Atari 5200 SuperSystem 필수 바이오스', required: false },
    { id: 'kickstart', system: 'Amiga', name: '아미가 킥스타트 3.1', file: 'kick34005.A500', desc: 'Commodore Amiga 500/1200 킥스타트 롬', required: false },
    { id: 'ws_boot', system: 'WonderSwan', name: '원더스완 컬러 부트', file: 'ws_boot.bin', desc: 'Bandai WonderSwan Color 부트 롬', required: false },
  ];

  function isBiosInstalled(item) {
    const available = (state.available_bios || []).map((b) => b.toLowerCase());
    const target = item.file.toLowerCase();
    const stem = target.replace(/\.[a-z0-9]+$/i, '');

    if (available.includes(target) || available.includes(stem)) return true;
    if (item.id.startsWith('scph') && available.some((b) => b.includes(item.id))) return true;
    if (item.id === 'psx' && available.some((b) => b.startsWith('scph'))) return true;
    return false;
  }

  function renderBiosModal() {
    const tbody = $('gbaBiosTableBody');
    const countBadge = $('gbaBiosCountBadge');
    const paginationEl = $('gbaBiosPagination');
    if (!tbody) return;

    const available = (state.available_bios || []).map((b) => b.toLowerCase());
    const q = (state.biosSearch || '').trim().toLowerCase();

    // FULL_BIOS_CATALOG 매핑 맵
    const catalogMap = new Map();
    FULL_BIOS_CATALOG.forEach((item) => {
      catalogMap.set(item.file.toLowerCase(), item);
      const stem = item.file.toLowerCase().replace(/\.[a-z0-9]+$/i, '');
      catalogMap.set(stem, item);
    });

    // 서버에 실제로 보관된 바이오스/디바이스 파일 목록 구성
    const installedList = available.map((fname) => {
      const lower = fname.toLowerCase();
      const stem = lower.replace(/\.[a-z0-9]+$/i, '');
      const catalogItem = catalogMap.get(lower) || catalogMap.get(stem);

      if (catalogItem) {
        return {
          id: catalogItem.id,
          system: catalogItem.system,
          name: catalogItem.name,
          file: fname,
          desc: catalogItem.desc,
        };
      } else {
        return {
          id: 'mame_' + stem,
          system: 'Arcade',
          name: `MAME 기판 디바이스 (${stem})`,
          file: fname,
          desc: 'MAME / FBNeo 기판 펌웨어',
        };
      }
    });

    // 검색어 필터링
    let list = installedList.filter((item) => {
      if (q) {
        const matchName = item.name.toLowerCase().includes(q);
        const matchFile = item.file.toLowerCase().includes(q);
        const matchSys = item.system.toLowerCase().includes(q);
        const matchDesc = item.desc.toLowerCase().includes(q);
        if (!matchName && !matchFile && !matchSys && !matchDesc) return false;
      }
      return true;
    });

    const totalCount = installedList.length;
    const filteredCount = list.length;
    if (countBadge) {
      if (q) {
        countBadge.innerHTML = `총 ${totalCount}개 중 <strong>${filteredCount}개 검색됨</strong>`;
      } else {
        countBadge.innerHTML = `총 <strong style="color:#34d399;">${totalCount}개 보유 중</strong>`;
      }
    }

    const pageSize = state.biosPageSize || 10;
    const totalPages = Math.max(1, Math.ceil(filteredCount / pageSize));
    if (state.biosPage > totalPages) state.biosPage = totalPages;
    if (state.biosPage < 1) state.biosPage = 1;

    const startIdx = (state.biosPage - 1) * pageSize;
    const pageItems = list.slice(startIdx, startIdx + pageSize);

    if (pageItems.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="3" style="text-align: center; padding: 28px; color: var(--gba-text-muted);">
            <i class="fa-solid fa-circle-exclamation" style="margin-right: 6px;"></i> ${q ? '검색된 바이오스 파일이 없습니다.' : '등록된 바이오스 파일이 없습니다. 상단에서 파일을 업로드해 주세요.'}
          </td>
        </tr>
      `;
    } else {
      tbody.innerHTML = pageItems.map((item) => {
        const sysInfo = SYSTEM_DISPLAY_MAP[item.system.toLowerCase()] || { label: item.system, colorClass: 'gba-badge-other' };
        return `
          <tr>
            <td>
              <div class="gba-bios-row-system">
                <span class="gba-badge ${sysInfo.colorClass}">${escapeHtml(sysInfo.label)}</span>
              </div>
            </td>
            <td>
              <span class="gba-bios-row-filename">${escapeHtml(item.file)}</span>
            </td>
            <td>
              <div class="gba-bios-desc-cell" title="${escapeHtml(item.name)} - ${escapeHtml(item.desc)}">
                <span class="gba-bios-row-title">${escapeHtml(item.name)}</span>
                <span class="gba-bios-row-divider">-</span>
                <span class="gba-bios-row-desc">${escapeHtml(item.desc)}</span>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    // 페이징 버튼 렌더링 (<< < 1 2 3 4 5 > >> 형태 5페이지 윈도우)
    if (paginationEl) {
      if (totalPages <= 1) {
        paginationEl.innerHTML = '';
      } else {
        const currentPage = state.biosPage;
        const windowSize = 5;
        let startPage = Math.max(1, currentPage - Math.floor(windowSize / 2));
        let endPage = startPage + windowSize - 1;
        if (endPage > totalPages) {
          endPage = totalPages;
          startPage = Math.max(1, endPage - windowSize + 1);
        }

        let pagesHtml = `
          <button type="button" class="gba-bios-page-btn" data-page="1" ${currentPage <= 1 ? 'disabled' : ''} title="처음 페이지 (1페이지)">
            <i class="fa-solid fa-angles-left"></i>
          </button>
          <button type="button" class="gba-bios-page-btn" data-page="${currentPage - 1}" ${currentPage <= 1 ? 'disabled' : ''} title="이전 페이지">
            <i class="fa-solid fa-chevron-left"></i>
          </button>
        `;

        for (let p = startPage; p <= endPage; p++) {
          pagesHtml += `
            <button type="button" class="gba-bios-page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">
              ${p}
            </button>
          `;
        }

        pagesHtml += `
          <button type="button" class="gba-bios-page-btn" data-page="${currentPage + 1}" ${currentPage >= totalPages ? 'disabled' : ''} title="다음 페이지">
            <i class="fa-solid fa-chevron-right"></i>
          </button>
          <button type="button" class="gba-bios-page-btn" data-page="${totalPages}" ${currentPage >= totalPages ? 'disabled' : ''} title="마지막 페이지 (${totalPages}페이지)">
            <i class="fa-solid fa-angles-right"></i>
          </button>
        `;
        paginationEl.innerHTML = pagesHtml;

        paginationEl.querySelectorAll('.gba-bios-page-btn').forEach((btn) => {
          btn.addEventListener('click', () => {
            const p = parseInt(btn.dataset.page, 10);
            if (!isNaN(p) && p >= 1 && p <= totalPages && p !== state.biosPage) {
              state.biosPage = p;
              renderBiosModal();
            }
          });
        });
      }
    }
  }

  function openBiosModal() {
    state.biosPage = 1;
    state.biosSearch = '';
    if ($('gbaBiosSearchInput')) $('gbaBiosSearchInput').value = '';
    renderBiosModal();
    $('gbaBiosModal').style.display = 'flex';
  }

  function closeBiosModal() {
    $('gbaBiosModal').style.display = 'none';
  }

  function checkMissingBios(game) {
    const biosList = (state.available_bios || []).map((b) => b.toLowerCase());
    const filename = (game.filename || '').toLowerCase();
    const title = (game.title || '').toLowerCase();
    const platform = (game.platform || '').toUpperCase();
    const core = (game.core || '').toLowerCase();

    // 1. Neo-Geo games
    const neoGeoKeywords = [
      'kof', 'mslug', 'samsho', 'fatfur', 'garou', 'neogeo', 'snk', 'aof', 'rbff',
      'maglord', 'nam1975', 'spinmast', 'shocktro', 'pbobbl', 'pulstar', 'blazstar',
      'sengoku', 'lastblad', 'wh1', 'wh2', 'whp', 'wjammers', 'kotm', 'viewpoin',
      'strhoop', 'tws96', 'zedblade', 'matrim', 'gururin', 'breakers'
    ];
    if (platform === 'NEOGEO' || neoGeoKeywords.some((k) => filename.includes(k) || title.includes(k))) {
      if (!biosList.includes('neogeo.zip')) {
        return {
          type: 'bios',
          needed: 'neogeo.zip',
          systemName: '네오지오 (Neo-Geo)',
          title: '시스템 바이오스(BIOS) 확인',
          reason: '이 게임은 <strong>[네오지오 (Neo-Geo)]</strong> 기판 게임으로, 원활한 구동을 위해 <code>neogeo.zip</code> 바이오스가 필요합니다.',
          notice: '바이오스(<code>neogeo.zip</code>) 없이 실행 시 <strong>Romset is unknown</strong> 오류가 발생할 수 있습니다.',
          btnText: '바이오스 (neogeo.zip) 업로드',
          isOptional: false,
        };
      }
    }

    // 2. IGS PGM games
    const pgmKeywords = ['orlegend', 'kov', 'martmast', 'theglad', 'demonfr', 'drgw', 'oldsplus'];
    if (pgmKeywords.some((k) => filename.includes(k) || title.includes(k))) {
      if (!biosList.includes('pgm.zip')) {
        return {
          type: 'bios',
          needed: 'pgm.zip',
          systemName: 'IGS PGM (삼국전기/데몬프론트)',
          title: '시스템 바이오스(BIOS) 확인',
          reason: '이 게임은 <strong>[IGS PGM 기판]</strong> 게임으로, 원활한 구동을 위해 <code>pgm.zip</code> 바이오스가 필요합니다.',
          notice: '바이오스(<code>pgm.zip</code>) 없이 실행 시 <strong>Romset is unknown</strong> 오류가 발생할 수 있습니다.',
          btnText: '바이오스 (pgm.zip) 업로드',
          isOptional: false,
        };
      }
    }

    // 3. FDS games
    if (platform === 'FDS' || filename.endsWith('.fds')) {
      if (!biosList.includes('disksys.rom')) {
        return {
          type: 'bios',
          needed: 'disksys.rom',
          systemName: '패미컴 디스크 시스템 (FDS)',
          title: '시스템 바이오스(BIOS) 확인',
          reason: '패미컴 디스크 시스템 롬 구동을 위해 <code>disksys.rom</code> 바이오스가 필요합니다.',
          notice: '바이오스(<code>disksys.rom</code>) 없이 실행 시 게임이 구동되지 않을 수 있습니다.',
          btnText: '바이오스 (disksys.rom) 업로드',
          isOptional: false,
        };
      }
    }

    // 4. PCE-CD
    if (platform === 'PCECD' || (platform === 'PCE' && filename.endsWith('.chd'))) {
      if (!biosList.includes('syscard3.pce')) {
        return {
          type: 'bios',
          needed: 'syscard3.pce',
          systemName: 'PC엔진 CD-ROM²',
          title: '시스템 카드 바이오스 확인',
          reason: 'PC엔진 CD 게임을 구동하기 위해 <code>syscard3.pce</code> 시스템 카드가 필요합니다.',
          notice: '시스템 카드(<code>syscard3.pce</code>) 없이 실행 시 CD 게임이 로드되지 않습니다.',
          btnText: '시스템 카드 (syscard3.pce) 업로드',
          isOptional: false,
        };
      }
    }

    // 5. PlayStation 1 (권장 안내)
    if (platform === 'PS1' || core === 'psx') {
      const hasPsxBios = biosList.some((b) => b.startsWith('scph'));
      if (!hasPsxBios) {
        return {
          type: 'bios',
          needed: 'scph5501.bin (또는 scph1001.bin)',
          systemName: 'PlayStation 1 (PS1)',
          reason: 'PS1 공식 바이오스가 있으면 호환성과 사운드 재생 품질이 향상됩니다.',
          isOptional: true,
        };
      }
    }

    // 6. 아케이드 클론(Clone) 롬셋 & 부모 롬(Parent ROM) 의존성 검사
    const ARCADE_CLONE_MAP = {
      wofj: { parent: 'wof.zip', name: '천지를 먹다 2 (Warriors of Fate)', system: 'CPS-1.5' },
      wofa: { parent: 'wof.zip', name: '천지를 먹다 2 (Warriors of Fate)', system: 'CPS-1.5' },
      wofu: { parent: 'wof.zip', name: '천지를 먹다 2 (Warriors of Fate)', system: 'CPS-1.5' },
      wofr1: { parent: 'wof.zip', name: '천지를 먹다 2 (Warriors of Fate)', system: 'CPS-1.5' },
      captcommj: { parent: 'captcomm.zip', name: '캡틴 코만도 (Captain Commando)', system: 'CPS-1' },
      captcommu: { parent: 'captcomm.zip', name: '캡틴 코만도 (Captain Commando)', system: 'CPS-1' },
      ffightj: { parent: 'ffight.zip', name: '파이널 파이트 (Final Fight)', system: 'CPS-1' },
      ffightu: { parent: 'ffight.zip', name: '파이널 파이트 (Final Fight)', system: 'CPS-1' },
      dinoj: { parent: 'dino.zip', name: '캐딜락 & 다이노소어', system: 'CPS-1.5' },
      dinou: { parent: 'dino.zip', name: '캐딜락 & 다이노소어', system: 'CPS-1.5' },
      punisherj: { parent: 'punisher.zip', name: '퍼니셔 (The Punisher)', system: 'CPS-1.5' },
      punisheru: { parent: 'punisher.zip', name: '퍼니셔 (The Punisher)', system: 'CPS-1.5' },
      avspj: { parent: 'avsp.zip', name: '에이리언 vs 프레데터', system: 'CPS-2' },
      ddsomj: { parent: 'ddsom.zip', name: '던전 & 드래곤: 섀도 오버 미스타라', system: 'CPS-2' },
      ddtodj: { parent: 'ddtod.zip', name: '던전 & 드래곤: 타워 오브 둠', system: 'CPS-2' },
      kodj: { parent: 'kod.zip', name: '원탁의 기사 (Knights of the Round)', system: 'CPS-1' },
      sf2j: { parent: 'sf2.zip', name: '스트리트 파이터 2', system: 'CPS-1' },
      sf2cej: { parent: 'sf2ce.zip', name: '스트리트 파이터 2 챔피언 에디션', system: 'CPS-1' },
      ssf2j: { parent: 'ssf2.zip', name: '슈퍼 스트리트 파이터 2', system: 'CPS-2' },
      kof97k: { parent: 'kof97.zip', name: '더 킹 오브 파이터즈 97 (한글판)', system: 'Neo-Geo' },
      kof98k: { parent: 'kof98.zip', name: '더 킹 오브 파이터즈 98 (한글판)', system: 'Neo-Geo' },
      kof99k: { parent: 'kof99.zip', name: '더 킹 오브 파이터즈 99 (한글판)', system: 'Neo-Geo' },
      kof2000k: { parent: 'kof2000.zip', name: '더 킹 오브 파이터즈 2000 (한글판)', system: 'Neo-Geo' },
      kof2002k: { parent: 'kof2002.zip', name: '더 킹 오브 파이터즈 2002 (한글판)', system: 'Neo-Geo' },
      mslugx: { parent: 'mslug2.zip', name: '메탈슬러그 X', system: 'Neo-Geo' },
    };

    const rawStem = filename.replace(/\.(zip|7z)$/i, '').toLowerCase();
    const existingGameFiles = (state.games || []).map((g) => (g.filename || '').toLowerCase());
    const allRoms = [...biosList, ...existingGameFiles];

    if (ARCADE_CLONE_MAP[rawStem]) {
      const cloneInfo = ARCADE_CLONE_MAP[rawStem];
      if (!allRoms.includes(cloneInfo.parent.toLowerCase())) {
        return {
          type: 'parent',
          needed: cloneInfo.parent,
          systemName: `${cloneInfo.system} / ${cloneInfo.name}`,
          title: '아케이드 부모 롬(Parent ROM) 필요',
          reason: `이 게임(<code>${escapeHtml(filename)}</code>)은 클론/한글패치 롬셋입니다.<br>에뮬레이터 구동에 필수적인 기본 그래픽/사운드 데이터가 들어있는 원본 부모 롬 <code>${cloneInfo.parent}</code> 파일이 roms 폴더에 함께 있어야 정상 구동됩니다.`,
          notice: `부모 롬(<code>${cloneInfo.parent}</code>) 없이 실행 시 <strong>Romset is unknown</strong> 오류가 발생합니다.`,
          btnText: `부모 롬 (${cloneInfo.parent}) 업로드`,
          isOptional: false,
        };
      }
    } else if (core === 'arcade' || platform === 'Arcade') {
      const match = rawStem.match(/^([a-z0-9_]+?)([juka-e1-3])$/i);
      if (match && match[1].length >= 3) {
        const potentialParent = match[1] + '.zip';
        if (!allRoms.includes(potentialParent.toLowerCase())) {
          return {
            type: 'parent',
            needed: potentialParent,
            systemName: '아케이드 클론(Clone) 롬셋',
            title: '아케이드 부모 롬(Parent ROM) 확인',
            reason: `이 게임은 변형판(클론) 롬셋일 가능성이 높습니다.<br>실행 시 오류가 발생한다면 원본 부모 롬 <code>${potentialParent}</code>을 함께 넣어주세요.`,
            notice: `부모 롬(<code>${potentialParent}</code>)이 없으면 게임이 실행되지 않을 수 있습니다.`,
            btnText: `부모 롬 (${potentialParent}) 업로드`,
            isOptional: true,
          };
        }
      }
    }

    return null;
  }

  function showBiosWarningModal(game, missing) {
    const modal = $('gbaBiosWarningModal');
    if (!modal) {
      _startEmulator(game);
      return;
    }
    const isParent = missing.type === 'parent';

    if ($('gbaBiosWarningHeaderSpan')) {
      $('gbaBiosWarningHeaderSpan').textContent = missing.title || (isParent ? '아케이드 부모 롬(Parent ROM) 필요' : '시스템 바이오스(BIOS) 확인');
    }
    if ($('gbaBiosWarningHeaderIcon')) {
      $('gbaBiosWarningHeaderIcon').className = isParent ? 'fa-solid fa-folder-tree' : 'fa-solid fa-microchip';
    }
    if ($('gbaBiosWarningBodyIcon')) {
      $('gbaBiosWarningBodyIcon').innerHTML = isParent
        ? '<i class="fa-solid fa-folder-tree"></i>'
        : '<i class="fa-solid fa-microchip"></i>';
    }
    if ($('gbaBiosWarningGameTitle')) {
      $('gbaBiosWarningGameTitle').textContent = game.title;
    }
    if ($('gbaBiosWarningDesc')) {
      $('gbaBiosWarningDesc').innerHTML = missing.reason;
    }
    if ($('gbaBiosWarningNoticeSpan')) {
      $('gbaBiosWarningNoticeSpan').innerHTML = missing.notice || (isParent ? '부모 롬 없이 실행 시 <strong>Romset is unknown</strong> 오류가 발생합니다.' : '바이오스 없이 실행 시 <strong>Romset is unknown</strong> 오류가 발생할 수 있습니다.');
    }
    if ($('gbaBiosWarningUploadSpan')) {
      $('gbaBiosWarningUploadSpan').textContent = missing.btnText || (isParent ? '부모 롬 업로드' : '바이오스 업로드');
    }
    modal.style.display = 'flex';

    $('gbaBiosWarningProceedBtn').onclick = () => {
      modal.style.display = 'none';
      _startEmulator(game);
    };

    $('gbaBiosWarningUploadBtn').onclick = () => {
      modal.style.display = 'none';
      $('gbaFileInput').click();
    };

    $('gbaBiosWarningCloseBtn').onclick = () => {
      modal.style.display = 'none';
    };
  }

  // --------------------------------------------------------------------------
  // 에뮬레이터 실행 & 플레이어 관리 (커스텀 툴바 연동)
  // --------------------------------------------------------------------------
  async function launchGame(game, bypassBiosCheck = false) {
    if (!bypassBiosCheck) {
      const missing = checkMissingBios(game);
      if (missing) {
        if (!missing.isOptional) {
          showBiosWarningModal(game, missing);
          return;
        } else {
          showToast(`💡 ${missing.systemName}: ${missing.needed} 등록 권장`, false);
        }
      }
    }
    await _startEmulator(game);
  }

  async function _startEmulator(game) {
    state.activeGame = game;
    state.isPaused = false;
    state.currentSpeed = 1;
    state.isMuted = false;

    if ($('gbaCurrentGameTitle')) {
      $('gbaCurrentGameTitle').textContent = game.title;
    }
    if ($('gbaPlayerBadge')) {
      const sys = getSystemInfo(game);
      $('gbaPlayerBadge').textContent = sys.label;
    }
    $('gbaPlayerModal').style.display = 'flex';
    setSaveStatus('클라우드 세이브 확인 중...', 'saving');
    updatePlayerToolbarUI();

    // 유저별 재생 기록 업데이트
    try {
      const res = await apiCall('record_play', { game_id: game.id });
      const found = state.games.find((g) => g.id === game.id);
      if (found) {
        found.play_count = (found.play_count || 0) + 1;
        found.last_played_at = (res && res.last_played_at) || new Date(Date.now() + 9 * 3600 * 1000).toISOString().replace('T', ' ').substring(0, 19);
      }
    } catch (e) {
      console.warn('[GBA] Record play error:', e);
    }

    const container = $('gbaEmulatorContainer');
    container.innerHTML = '';

    // EmulatorJS 공식 문서(docs4devs/cores) 규격 기반 동적 시스템/코어 매퍼
    const EMULATORJS_CORE_MAP = {
      // 닌텐도 (Nintendo)
      gba: 'gba',
      gb: 'gb',
      gbc: 'gb',
      snes: 'snes',
      smc: 'snes',
      sfc: 'snes',
      nes: 'nes',
      fds: 'nes',
      nds: 'nds',
      n64: 'n64',
      z64: 'n64',
      v64: 'n64',
      vb: 'vb',
      virtualboy: 'vb',

      // 세가 (SEGA)
      genesis: 'segaMD',
      megadrive: 'segaMD',
      md: 'segaMD',
      gen: 'segaMD',
      smd: 'segaMD',
      segamd: 'segaMD',
      segacd: 'segaCD',
      sega32x: 'sega32x',
      '32x': 'sega32x',
      mastersystem: 'segaMS',
      segams: 'segaMS',
      sms: 'segaMS',
      gamegear: 'segaGG',
      segagg: 'segaGG',
      gg: 'segaGG',
      saturn: 'segaSaturn',
      segasaturn: 'segaSaturn',

      // 소니 (Sony)
      psx: 'psx',
      ps1: 'psx',
      playstation: 'psx',
      psp: 'psp',

      // 아케이드 / MAME
      arcade: 'arcade',
      fbneo: 'arcade',
      mame: 'mame',
      mame2003: 'mame',
      mame2003_plus: 'mame',

      // NEC / 아타리 / SNK / 코모도어 등
      pce: 'pce',
      pcengine: 'pce',
      supergrafx: 'pce',
      sgx: 'pce',
      pcfx: 'pcfx',
      ngp: 'ngp',
      ngpc: 'ngp',
      neogeopocket: 'ngp',
      ws: 'ws',
      wsc: 'ws',
      wonderswan: 'ws',
      atari2600: 'atari2600',
      a26: 'atari2600',
      atari5200: 'a5200',
      a52: 'a5200',
      atari7800: 'atari7800',
      a78: 'atari7800',
      lynx: 'lynx',
      jaguar: 'jaguar',
      coleco: 'coleco',
      colecovision: 'coleco',
      c64: 'c64',
      c128: 'c128',
      pet: 'pet',
      plus4: 'plus4',
      vic20: 'vic20',
      amiga: 'amiga',
      dos: 'dos',
      '3do': '3do',
      '3ds': '3ds',
    };

    const rawStem = (game.filename || '').replace(/\.[a-zA-Z0-9]+$/i, '').toLowerCase();
    const coreKey = (game.core || '').toLowerCase();
    const platformKey = (game.platform || '').toLowerCase();

    // 1. 코어 동적 판별
    let coreToUse = EMULATORJS_CORE_MAP[coreKey] || EMULATORJS_CORE_MAP[platformKey] || 'gba';

    // 2. 아케이드 세부 라우팅 (FBNeo vs MAME2003-Plus)
    const isArcade = coreToUse === 'arcade' || coreToUse === 'mame' || platformKey === 'arcade';
    if (isArcade) {
      if (rawStem === 'cupsoc' || rawStem === 'seibucup' || rawStem === 'danceyes' || coreKey === 'mame' || coreKey === 'mame2003' || game.core === 'mame' || game.core === 'mame2003') {
        coreToUse = 'mame';
      } else {
        coreToUse = 'arcade';
      }
    }

    // 시스템 바이오스(BIOS) EJS_biosUrl 자동 매핑 (DB needed_bios 우선 연동)
    const biosList = (state.available_bios || []).map((b) => b.toLowerCase());
    let neededBiosFile = null;

    if (game.needed_bios && biosList.includes(game.needed_bios.toLowerCase())) {
      neededBiosFile = game.needed_bios;
    } else {
      // 폴백: DB에 바이오스가 미기록된 레거시 롬 동적 보정
      if (platformKey === 'neo-geo' || (isArcade && (rawStem.startsWith('mslug') || rawStem.startsWith('kof') || rawStem.startsWith('samsho') || rawStem.startsWith('fatfur') || rawStem.startsWith('garou')))) {
        if (biosList.includes('neogeo.zip')) neededBiosFile = 'neogeo.zip';
      } else if (isArcade && (rawStem.startsWith('olds') || rawStem.startsWith('kov') || rawStem.startsWith('orlegend') || rawStem.startsWith('dmnfrnt'))) {
        if (biosList.includes('pgm.zip')) neededBiosFile = 'pgm.zip';
      } else if (isArcade && (rawStem.startsWith('bldyror') || rawStem.startsWith('brvblade') || rawStem.startsWith('sfex') || rawStem.startsWith('rvschool') || rawStem.startsWith('starglad') || rawStem.startsWith('strider2') || rawStem.startsWith('techromn') || rawStem.startsWith('jgts') || rawStem.startsWith('raiden2') || rawStem.startsWith('raidendx'))) {
        if (biosList.includes('acpsx.zip')) neededBiosFile = 'acpsx.zip';
        else if (biosList.includes('atluspsx.zip')) neededBiosFile = 'atluspsx.zip';
        else if (biosList.includes('boardrom.zip')) neededBiosFile = 'boardrom.zip';
      } else if (coreToUse === 'psx' || platformKey === 'ps1') {
        const psxBios = biosList.find((b) => b.startsWith('scph5501') || b.startsWith('scph1001') || b.startsWith('scph5500') || b.startsWith('scph5502') || b.startsWith('scph7001'));
        if (psxBios) neededBiosFile = psxBios;
      } else if (platformKey === 'fds' || (game.filename || '').toLowerCase().endsWith('.fds')) {
        if (biosList.includes('disksys.rom')) neededBiosFile = 'disksys.rom';
      } else if (platformKey === 'segacd' || coreToUse === 'segacd') {
        const cdBios = biosList.find((b) => b.startsWith('bios_cd'));
        if (cdBios) neededBiosFile = cdBios;
      } else if (platformKey === 'pce' || coreToUse === 'pce') {
        if (biosList.includes('syscard3.pce')) neededBiosFile = 'syscard3.pce';
      } else if (platformKey === 'saturn' || coreToUse === 'saturn' || coreToUse === 'segasaturn') {
        const saturnBios = biosList.find((b) => b.includes('saturn'));
        if (saturnBios) neededBiosFile = saturnBios;
      } else if (platformKey === '3do' || coreToUse === '3do') {
        const d3doBios = biosList.find((b) => b.includes('3do'));
        if (d3doBios) neededBiosFile = d3doBios;
      }
    }

    const biosUrl = neededBiosFile ? `${window.location.origin}/api/webhook/bookoasis_gamebooks/bios/${encodeURIComponent(neededBiosFile)}` : null;
    const gameUrl = window.location.origin + game.rom_url;
    const gameName = isArcade ? rawStem : game.title;
    const loadStateUrl = game.has_state ? window.location.origin + game.state_url : null;

    // SPA 환경에서의 전역 변수 충돌(let EJS_STORAGE redeclaration) 및 WASM 메모리 누수 방지를 위해
    // 완전히 독립된 iframe 샌드박스를 생성하여 EmulatorJS를 격리 실행
    const iframe = document.createElement('iframe');
    iframe.id = 'gbaEmulatorIframe';
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.style.border = 'none';
    iframe.style.outline = 'none';
    iframe.allow = 'autoplay; gamepad; fullscreen; cross-origin-isolated';
    container.appendChild(iframe);

    const iframeHtml = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 100%; height: 100%; overflow: hidden; background: #000; }
    #game { width: 100%; height: 100%; }
    .ejs_menu_bar, [class*="ejs_menu"], [class*="context_menu"], .ejs_context, #context_menu { display: none !important; opacity: 0 !important; pointer-events: none !important; visibility: hidden !important; }
  </style>
</head>
<body>
  <div id="game"></div>
  <script>
    window.EJS_player = '#game';
    window.EJS_core = ${JSON.stringify(coreToUse)};
    window.EJS_gameName = ${JSON.stringify(gameName)};
    window.EJS_gameUrl = ${JSON.stringify(gameUrl)};
    window.EJS_pathtodata = 'https://cdn.emulatorjs.org/stable/data/';
    ${biosUrl ? `window.EJS_biosUrl = ${JSON.stringify(biosUrl)};` : ''}
    ${loadStateUrl ? `window.EJS_loadStateURL = ${JSON.stringify(loadStateUrl)};` : ''}
    window.EJS_startOnLoaded = true;
    window.EJS_color = '#6366f1';
    window.EJS_alignStartButton = 'center';
    window.EJS_gamepad = true;
    window.EJS_mouse = true;
    window.EJS_pointerLock = false;

    // WakeLock API의 "The requesting document is hidden" 예외 안전 처리
    try {
      if (navigator.wakeLock && typeof navigator.wakeLock.request === 'function') {
        const origRequest = navigator.wakeLock.request.bind(navigator.wakeLock);
        navigator.wakeLock.request = function(type) {
          try {
            return origRequest(type).catch(() => ({ release: () => Promise.resolve() }));
          } catch(e) {
            return Promise.resolve({ release: () => Promise.resolve() });
          }
        };
      }
    } catch(e) {}

    // EmulatorJS 내부의 allSettings.disk 접근 에러 방지 방어 코드
    try {
      Object.defineProperty(Object.prototype, 'allSettings', {
        get() {
          return this.__allSettings__ || {
            disk: { currentDisk: 0, disks: [] },
            shaders: 'disabled',
            volume: 1,
            muted: false
          };
        },
        set(v) {
          this.__allSettings__ = v;
        },
        configurable: true,
        enumerable: false
      });
    } catch(e) {}

    window.EJS_onGameStart = function() {
      if (window.parent && window.parent.__GBA_ON_GAME_START__) {
        window.parent.__GBA_ON_GAME_START__();
      }
    };

    // 캡처 단계(true)에서 contextmenu 이벤트를 가로채 EmulatorJS 자체 메뉴 호출 원천 차단
    window.addEventListener('contextmenu', function(e) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') {
        e.stopImmediatePropagation();
      }
      if (window.parent && window.parent.__GBA_ON_CONTEXT_MENU__) {
        window.parent.__GBA_ON_CONTEXT_MENU__(e.clientX, e.clientY, e.screenX, e.screenY);
      }
      return false;
    }, true);
  <\/script>
  <script src="https://cdn.emulatorjs.org/stable/data/loader.js"><\/script>
</body>
</html>`;

    // 부모-자식 프레임 브릿지 콜백 등록
    window.__GBA_ON_GAME_START__ = () => {
      focusEmulator();
      setTimeout(() => {
        applyGraphicsSettings();
        focusEmulator();
      }, 200);
      setTimeout(focusEmulator, 800);
    };

    window.__GBA_ON_CONTEXT_MENU__ = (clientX, clientY) => {
      if (!state.activeGame) return;
      const iframeRect = iframe.getBoundingClientRect();
      const x = iframeRect.left + clientX;
      const y = iframeRect.top + clientY;
      showCustomContextMenu(x, y);
    };

    const focusEmulator = () => {
      try {
        if (iframe && iframe.contentWindow) {
          iframe.contentWindow.focus();
          const innerDoc = iframe.contentDocument || iframe.contentWindow.document;
          const canvas = innerDoc.querySelector('canvas') || innerDoc.querySelector('#game');
          if (canvas) {
            canvas.setAttribute('tabindex', '0');
            canvas.focus();
          }
        }
      } catch (e) {}
    };

    container.onclick = focusEmulator;
    $('gbaEmulatorViewport').onclick = focusEmulator;

    // iframe 콘텐츠 주입
    const doc = iframe.contentWindow.document;
    doc.open();
    doc.write(iframeHtml);
    doc.close();

    setSaveStatus('클라우드 세이브 준비됨', 'ready');
    setupAutoSave(game);
    startGamepadPoller();
    if (coreToUse === 'mame2003') {
      showToast('💡 [MAME] 시작 시 방향키 "←" "→" (또는 Enter)를 누르면 게임으로 진입합니다.', false);
    }
  }

  function getIframeEmulator() {
    try {
      const iframe = $('gbaEmulatorIframe');
      if (iframe && iframe.contentWindow) {
        return iframe.contentWindow.EJS_emulator || window.EJS_emulator;
      }
    } catch (e) {}
    return window.EJS_emulator;
  }

  function applyGraphicsSettings() {
    const emu = getIframeEmulator();
    if (emu && typeof emu.enableShader === 'function') {
      try {
        emu.enableShader(state.graphics.shader || 'disabled');
      } catch (err) {
        console.warn('[GBA] enableShader error:', err);
      }
    }

    try {
      const iframe = $('gbaEmulatorIframe');
      const innerDoc = iframe ? (iframe.contentDocument || iframe.contentWindow.document) : document;
      const canvas = innerDoc.querySelector('#game canvas') || innerDoc.querySelector('canvas');
      if (canvas) {
        canvas.style.imageRendering = state.graphics.pixelMode || 'pixelated';
        canvas.style.margin = '0 auto';
        if (state.graphics.aspectRatio === 'stretch') {
          canvas.style.aspectRatio = 'unset';
          canvas.style.width = '100%';
          canvas.style.height = '100%';
          canvas.style.objectFit = 'fill';
        } else {
          canvas.style.aspectRatio = state.graphics.aspectRatio || '3/2';
          canvas.style.width = 'auto';
          canvas.style.height = '100%';
          canvas.style.maxWidth = '100%';
          canvas.style.objectFit = 'contain';
        }
      }
    } catch (e) {}
  }

  function setupAutoSave(game) {
    if (state.autoSaveIntervalId) {
      clearInterval(state.autoSaveIntervalId);
    }

    if (!state.config.cloud_save_enabled) return;

    const intervalMs = Math.max(state.config.auto_save_interval_sec || 60, 15) * 1000;
    state.autoSaveIntervalId = setInterval(() => {
      triggerSaveSync(game, false);
    }, intervalMs);
  }

  async function triggerSaveSync(game, isManual = false) {
    if (!game) return false;
    const emu = getIframeEmulator();
    if (!emu || !emu.gameManager) {
      if (isManual) showToast('에뮬레이터 코어가 로딩 중입니다. 잠시 후 다시 시도해 주세요.');
      return false;
    }

    try {
      setSaveStatus('서버 세이브 동기화 중...', 'saving');

      let savedCount = 0;

      // 1. 실시간 스냅샷 (.state) 추출 (현재 화면/메모리 그대로 캡처)
      let stateData = null;
      try {
        if (typeof emu.gameManager.getState === 'function') {
          stateData = emu.gameManager.getState();
        }
      } catch (err) {
        console.warn('[GBA] getState error:', err);
      }

      if (stateData && stateData.length > 0) {
        const stateRes = await fetch(`${API_WEBHOOK}/state/${game.id}?user_id=${state.userId}`, {
          method: 'POST',
          body: stateData,
          headers: {
            'Content-Type': 'application/octet-stream',
          },
        });
        if (stateRes.ok) savedCount++;
      }

      // 2. 인게임 배터리 세이브 (.sav) 추출
      let savData = null;
      try {
        if (typeof emu.gameManager.getSaveFile === 'function') {
          savData = emu.gameManager.getSaveFile(true);
        }
      } catch (err) {
        console.warn('[GBA] getSaveFile error:', err);
      }

      if (savData && savData.length > 0) {
        const savRes = await fetch(`${API_WEBHOOK}/save/${game.id}?user_id=${state.userId}`, {
          method: 'POST',
          body: savData,
          headers: {
            'Content-Type': 'application/octet-stream',
          },
        });
        if (savRes.ok) savedCount++;
      }

      if (savedCount > 0) {
        const nowStr = new Date().toTimeString().substring(0, 8);
        setSaveStatus(`서버 동기화 완료 (${nowStr})`, 'ready');
        game.has_save = 1;
        game.has_state = 1;
        if (isManual) {
          showToast('실시간 진행 상황이 서버에 안전하게 저장되었습니다! 💾');
        }
        return true;
      } else {
        if (isManual) {
          showToast('저장할 게임 데이터가 없습니다.');
        }
      }
    } catch (e) {
      console.error('[GBA] Save sync error:', e);
      setSaveStatus('세이브 동기화 대기', 'ready');
      if (isManual) showToast('세이브 저장 중 오류가 발생했습니다.', true);
    }
    return false;
  }

  function updatePlayerToolbarUI() {
    // 일시정지 아이콘
    const pauseIcon = $('gbaPausePlayIcon');
    if (pauseIcon) {
      pauseIcon.className = state.isPaused ? 'fa-solid fa-play' : 'fa-solid fa-pause';
    }
    // 배속 텍스트
    const speedText = $('gbaSpeedText');
    if (speedText) {
      speedText.textContent = `${state.currentSpeed}x`;
    }
    // 볼륨 아이콘
    const volIcon = $('gbaVolumeIcon');
    if (volIcon) {
      volIcon.className = state.isMuted ? 'fa-solid fa-volume-xmark' : 'fa-solid fa-volume-high';
    }
  }

  function togglePausePlay() {
    const emu = getIframeEmulator();
    if (!emu || !emu.gameManager || typeof emu.gameManager.toggleMainLoop !== 'function') {
      showToast('에뮬레이터가 아직 로딩 중입니다.');
      return;
    }
    state.isPaused = !state.isPaused;
    try {
      emu.gameManager.toggleMainLoop(state.isPaused ? 0 : 1);
      updatePlayerToolbarUI();
      showToast(state.isPaused ? '게임이 일시정지되었습니다 ⏸' : '게임이 재개되었습니다 ▶');
    } catch (err) {
      console.warn('[GBA] togglePausePlay error:', err);
    }
  }

  function cycleSpeed() {
    const emu = getIframeEmulator();
    if (!emu || !emu.gameManager) {
      showToast('에뮬레이터가 아직 로딩 중입니다.');
      return;
    }
    const speeds = [1, 2, 3, 4];
    const nextIdx = (speeds.indexOf(state.currentSpeed) + 1) % speeds.length;
    state.currentSpeed = speeds[nextIdx];

    try {
      if (typeof emu.gameManager.setFastForwardRatio === 'function') {
        emu.gameManager.setFastForwardRatio(state.currentSpeed);
      }
      if (typeof emu.gameManager.toggleFastForward === 'function') {
        emu.gameManager.toggleFastForward(state.currentSpeed > 1 ? 1 : 0);
      }
      emu.isFastForward = state.currentSpeed > 1;
      updatePlayerToolbarUI();
      showToast(`게임 속도: ${state.currentSpeed}배속 ⏩`);
    } catch (e) {
      console.warn('[GBA] cycleSpeed error:', e);
    }
  }

  function toggleMuteVolume() {
    const emu = getIframeEmulator();
    if (!emu || typeof emu.setVolume !== 'function') {
      showToast('에뮬레이터가 아직 로딩 중입니다.');
      return;
    }
    state.isMuted = !state.isMuted;
    try {
      if (state.isMuted) {
        emu.setVolume(0);
        showToast('음소거 되었습니다 🔇');
      } else {
        emu.setVolume(1);
        showToast('소리가 켜졌습니다 🔊');
      }
      updatePlayerToolbarUI();
    } catch (e) {
      console.warn('[GBA] toggleMuteVolume error:', e);
    }
  }

  async function restartCurrentGame() {
    if (!state.activeGame) return;
    const confirmed = confirm(
      '⚠️ 경고: 게임을 처음부터 재시작하면 서버에 저장된 모든 세이브 데이터(배터리 세이브 및 자동 저장 스냅샷)가 영구히 삭제됩니다.\n\n정말 처음부터 완전히 초기화하여 다시 시작하시겠습니까?'
    );
    if (!confirmed) return;

    try {
      showToast('세이브 데이터를 초기화하고 게임을 재시작하는 중...');

      // 1. 서버에 보관된 유저 세이브 파일 (.sav, .state) 삭제
      await apiCall('reset_game_save', { game_id: state.activeGame.id });
      state.activeGame.has_save = 0;
      state.activeGame.has_state = 0;

      // 2. 자동 로드 URL 제거 및 에뮬레이터 초기화 재시작
      window.EJS_loadStateURL = null;
      const emu = window.EJS_emulator;
      if (emu && emu.gameManager && typeof emu.gameManager.restart === 'function') {
        emu.gameManager.restart();
      } else {
        await launchGame(state.activeGame);
      }

      setSaveStatus('세이브 초기화됨 (새 게임)', 'ready');
      showToast('세이브 데이터가 모두 초기화되었으며 게임이 처음부터 시작되었습니다! 🔄');
    } catch (err) {
      console.error('[GBA] Restart error:', err);
      showToast('재시작 중 오류가 발생했습니다.', true);
    }
  }

  async function exitGame() {
    const emu = getIframeEmulator();

    // 1. 종료 전 세이브 동기화
    if (state.activeGame) {
      try {
        await triggerSaveSync(state.activeGame, false);
      } catch (e) {
        console.warn('[GBA] Save on exit error:', e);
      }
    }

    // 2. 오디오 즉시 뮤트 및 WebAudio / WASM 메인 루프 완전 정지
    if (emu) {
      try {
        if (typeof emu.setVolume === 'function') emu.setVolume(0);
        if (emu.gameManager && typeof emu.gameManager.toggleMainLoop === 'function') emu.gameManager.toggleMainLoop(0);
        if (emu.Module) {
          if (typeof emu.Module.pauseMainLoop === 'function') emu.Module.pauseMainLoop();
          if (emu.Module.AL && emu.Module.AL.currentCtx) {
            try {
              emu.Module.AL.currentCtx.suspend();
              emu.Module.AL.currentCtx.close();
            } catch (err) {}
          }
          if (typeof emu.Module.abort === 'function') {
            try { emu.Module.abort(); } catch (err) {}
          }
        }
      } catch (e) {
        console.warn('[GBA] Emulator shutdown error:', e);
      }
    }

    // 3. 타이머 및 패드 폴러 정지
    if (state.autoSaveIntervalId) {
      clearInterval(state.autoSaveIntervalId);
      state.autoSaveIntervalId = null;
    }
    stopGamepadPoller();

    // 4. iframe 완전 제거 및 DOM 컨테이너 비우기 (메모리 완전 해제 및 let 재선언 충돌 방지)
    const container = $('gbaEmulatorContainer');
    if (container) {
      container.innerHTML = '';
    }
    window.__GBA_ON_GAME_START__ = null;
    window.EJS_emulator = null;
    $('gbaPlayerModal').style.display = 'none';
    state.activeGame = null;
    renderGames();
  }

  function setSaveStatus(text, type = 'ready') {
    const el = $('gbaSaveStatus');
    if (!el) return;
    el.className = 'gba-save-status ' + (type === 'saving' ? 'saving' : '');
    el.innerHTML = `<i class="fa-solid fa-${type === 'saving' ? 'spinner fa-spin' : 'cloud-check'}"></i> <span>${escapeHtml(text)}</span>`;
  }

  // --------------------------------------------------------------------------
  // 게임패드 컨트롤러 실시간 매퍼 (HTML5 Gamepad API 브릿지)
  // --------------------------------------------------------------------------
  const GAMEPAD_BUTTON_MAP = {
    0: { code: 'KeyX', key: 'x', keyCode: 88 },
    1: { code: 'KeyZ', key: 'z', keyCode: 90 },
    2: { code: 'KeyZ', key: 'z', keyCode: 90 },
    3: { code: 'KeyX', key: 'x', keyCode: 88 },
    4: { code: 'KeyA', key: 'a', keyCode: 65 },
    5: { code: 'KeyS', key: 's', keyCode: 83 },
    8: { code: 'ShiftRight', key: 'Shift', keyCode: 16 },
    9: { code: 'Enter', key: 'Enter', keyCode: 13 },
    12: { code: 'ArrowUp', key: 'ArrowUp', keyCode: 38 },
    13: { code: 'ArrowDown', key: 'ArrowDown', keyCode: 40 },
    14: { code: 'ArrowLeft', key: 'ArrowLeft', keyCode: 37 },
    15: { code: 'ArrowRight', key: 'ArrowRight', keyCode: 39 },
  };

  function dispatchKeyEvent(type, mapping) {
    const event = new KeyboardEvent(type, {
      code: mapping.code,
      key: mapping.key,
      keyCode: mapping.keyCode,
      which: mapping.keyCode,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(event);
    document.dispatchEvent(event);

    try {
      const iframe = $('gbaEmulatorIframe');
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.dispatchEvent(event);
        const innerDoc = iframe.contentDocument || iframe.contentWindow.document;
        if (innerDoc) {
          innerDoc.dispatchEvent(event);
          const canvas = innerDoc.querySelector('canvas') || innerDoc.querySelector('#game');
          if (canvas) canvas.dispatchEvent(event);
        }
      }
    } catch (e) {}
  }

  function startGamepadPoller() {
    if (state.gamepadPollId) return;

    function poll() {
      const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
      let hasPad = false;

      for (let i = 0; i < gamepads.length; i++) {
        const gp = gamepads[i];
        if (!gp) continue;
        hasPad = true;

        if (!state.lastGamepadStates[i]) {
          state.lastGamepadStates[i] = { buttons: {}, axes: {} };
        }
        const last = state.lastGamepadStates[i];

        // 버튼 체크
        gp.buttons.forEach((btn, idx) => {
          const isPressed = btn.pressed || btn.value > 0.4;
          const wasPressed = !!last.buttons[idx];

          if (isPressed !== wasPressed) {
            last.buttons[idx] = isPressed;
            const mapping = GAMEPAD_BUTTON_MAP[idx];
            if (mapping) {
              dispatchKeyEvent(isPressed ? 'keydown' : 'keyup', mapping);
            }
          }
        });

        // 아날로그 스틱 (Axis 0: Left/Right, Axis 1: Up/Down)
        if (gp.axes && gp.axes.length >= 2) {
          const axX = gp.axes[0];
          const axY = gp.axes[1];

          // 좌/우
          const leftPressed = axX < -0.45;
          const rightPressed = axX > 0.45;
          if (leftPressed !== last.axes.left) {
            last.axes.left = leftPressed;
            dispatchKeyEvent(leftPressed ? 'keydown' : 'keyup', GAMEPAD_BUTTON_MAP[14]);
          }
          if (rightPressed !== last.axes.right) {
            last.axes.right = rightPressed;
            dispatchKeyEvent(rightPressed ? 'keydown' : 'keyup', GAMEPAD_BUTTON_MAP[15]);
          }

          // 상/하
          const upPressed = axY < -0.45;
          const downPressed = axY > 0.45;
          if (upPressed !== last.axes.up) {
            last.axes.up = upPressed;
            dispatchKeyEvent(upPressed ? 'keydown' : 'keyup', GAMEPAD_BUTTON_MAP[12]);
          }
          if (downPressed !== last.axes.down) {
            last.axes.down = downPressed;
            dispatchKeyEvent(downPressed ? 'keydown' : 'keyup', GAMEPAD_BUTTON_MAP[13]);
          }
        }
      }

      const padIndicator = $('gbaPlayerPadIndicator');
      if (padIndicator) {
        padIndicator.style.display = hasPad ? 'flex' : 'none';
      }

      state.gamepadPollId = requestAnimationFrame(poll);
    }

    state.gamepadPollId = requestAnimationFrame(poll);
  }

  function stopGamepadPoller() {
    if (state.gamepadPollId) {
      cancelAnimationFrame(state.gamepadPollId);
      state.gamepadPollId = null;
      state.lastGamepadStates = {};
    }
  }

  // --------------------------------------------------------------------------
  // 파일 업로드 (ROM & 커버 아트 & 바이오스)
  // --------------------------------------------------------------------------
  async function handleFileUpload(files, type = 'rom') {
    if (!files || files.length === 0) return;
    if (type !== 'cover' && !state.isAdmin) {
      showToast('관리자만 ROM 및 바이오스를 업로드할 수 있습니다.', true);
      return;
    }

    const maxUploadBytes = (state.config && state.config.max_upload_bytes) ? state.config.max_upload_bytes : (100 * 1024 * 1024);
    const maxMb = (state.config && state.config.max_content_length_mb) ? state.config.max_content_length_mb : Math.round(maxUploadBytes / (1024 * 1024));

    $('gbaUploadModal').style.display = 'flex';
    const statusEl = $('gbaUploadStatus');
    const progEl = $('gbaProgressBar');
    const detailsEl = $('gbaUploadDetails');

    let completed = 0;
    const total = files.length;

    for (let i = 0; i < total; i++) {
      const file = files[i];
      const fileMb = (file.size / (1024 * 1024)).toFixed(1);

      // 클라이언트 측 실시간 사전 용량 검증 (MAX_CONTENT_LENGTH_MB 동적 연동)
      if (file.size > maxUploadBytes) {
        showToast(
          `업로드 실패 (${file.name}, ${fileMb}MB): 파일 크기가 서버 허용 한도(${maxMb}MB)를 초과했습니다. ` +
          `대용량 파일은 설정된 롬 저장소 폴더에 직접 넣고 [스캔]을 실행하시거나, 북오아시스 .env의 MAX_CONTENT_LENGTH_MB를 늘려주세요.`,
          true
        );
        continue;
      }

      // [Pre-flight] 브라우저 단에서 16바이트 헤더 슬라이싱 후 0.05초 만에 사전 검증
      if (type === 'rom') {
        try {
          const headSlice = file.slice(0, 16);
          const headBuf = await headSlice.arrayBuffer();
          const headBytes = new Uint8Array(headBuf);
          let headHex = '';
          for (let b = 0; b < headBytes.length; b++) {
            headHex += headBytes[b].toString(16).padStart(2, '0');
          }

          const preflightRes = await fetch(`${API_WEBHOOK}/preflight`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              filename: file.name,
              filesize: file.size,
              head_hex: headHex,
            }),
          });
          const preflight = await preflightRes.json();

          if (preflight && preflight.is_split) {
            showToast(
              `⚠️ '${file.name}' 은(는) 부모 롬(${preflight.parent_hint || '원본'}.zip)이 필요한 스플릿(Split) 롬셋입니다. 원활한 단독 구동을 위해 Non-Merged 완본 롬셋을 권장합니다.`,
              false
            );
          }
        } catch (e) {
          // Pre-flight 실패 시 일반 업로드로 자연스럽게 진행
        }
      }

      statusEl.textContent = `'${file.name}' 업로드 중... (${fileMb}MB)`;
      progEl.style.width = `${Math.round((i / total) * 100)}%`;
      detailsEl.textContent = `${i} / ${total} 파일 완료`;

      const formData = new FormData();
      formData.append('file', file);
      formData.append('type', type);
      if (state.targetGameForCover) {
        formData.append('game_id', state.targetGameForCover);
      }

      try {
        const res = await fetch(`${API_WEBHOOK}/upload`, {
          method: 'POST',
          body: formData,
        });

        let result = null;
        const text = await res.text();
        try {
          result = JSON.parse(text);
        } catch (e) {
          result = { success: false, error: text || `서버 응답 오류 (HTTP ${res.status})` };
        }

        if (res.status === 413 || (result && typeof result.error === 'string' && result.error.includes('too large'))) {
          showToast(
            `업로드 실패 (${file.name}, ${fileMb}MB): 파일 크기가 서버 허용 한도(${maxMb}MB)를 초과했습니다. ` +
            `대용량 롬은 설정된 롬 폴더에 직접 복사 후 [스캔]을 이용해 주세요.`,
            true
          );
        } else if (result && result.success) {
          completed++;
          if (result.notice) {
            setTimeout(() => {
              showToast(result.notice, false);
            }, 1200);
          } else if (result.type === 'bios') {
            setTimeout(() => {
              showToast(result.message || '시스템 바이오스가 등록되었습니다.', false);
            }, 1200);
          }
        } else {
          showToast(`업로드 실패 (${file.name}): ${result && result.error ? result.error : '알 수 없는 오류'}`, true);
        }
      } catch (err) {
        console.error('[GBA] Upload error:', err);
        showToast(`업로드 에러 (${file.name}): ${err.message || err}`, true);
      }
    }

    progEl.style.width = '100%';
    detailsEl.textContent = `${completed} / ${total} 파일 완료`;
    statusEl.textContent = '업로드 완료! 라이브러리를 갱신합니다.';

    setTimeout(() => {
      $('gbaUploadModal').style.display = 'none';
      state.targetGameForCover = null;
      loadLibrary();
    }, 900);
  }

  function promptCoverUpload(gameId) {
    const game = state.games.find((g) => g.id === gameId);
    openArtworkModal(gameId, game ? game.title : '');
  }

  // --------------------------------------------------------------------------
  // 커버 아트워크 검색 및 선택 모달
  // --------------------------------------------------------------------------
  function openArtworkModal(gameId, defaultTitle = '') {
    state.targetGameForCover = gameId;
    const modal = $('gbaArtworkModal');
    if (!modal) return;
    $('gbaArtworkQuery').value = defaultTitle || '';
    modal.style.display = 'flex';
    searchArtwork(gameId, defaultTitle);
  }

  function closeArtworkModal() {
    const modal = $('gbaArtworkModal');
    if (modal) modal.style.display = 'none';
  }

  async function searchArtwork(gameId, query = '') {
    const statusEl = $('gbaArtworkStatus');
    const gridEl = $('gbaArtworkGrid');
    if (!statusEl || !gridEl) return;

    statusEl.textContent = '아트워크를 검색하는 중...';
    gridEl.innerHTML = '';

    try {
      const data = await apiCall('search_artwork', {
        game_id: gameId || state.targetGameForCover,
        q: query || ($('gbaArtworkQuery')?.value || '').trim(),
      });

      if (!data.success) {
        statusEl.textContent = data.error || '아트워크 검색에 실패했습니다.';
        return;
      }

      if (data.query && $('gbaArtworkQuery')) {
        $('gbaArtworkQuery').value = data.query;
      }

      const results = data.results || [];
      if (results.length === 0) {
        statusEl.textContent = '검색된 아트워크가 없습니다. 직접 이미지 업로드를 이용해 보세요.';
        return;
      }

      statusEl.textContent = `총 ${results.length}개의 아트워크 후보가 발견되었습니다. 적용할 이미지를 클릭하세요.`;
      gridEl.innerHTML = '';

      results.forEach((item) => {
        const div = document.createElement('div');
        div.className = 'gba-artwork-item';
        div.innerHTML = `
          <img class="gba-artwork-thumb" src="${escapeHtml(item.thumb_url)}" alt="${escapeHtml(item.title)}" loading="lazy">
          <div class="gba-artwork-meta">
            <div class="gba-artwork-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
            <div class="gba-artwork-source">${escapeHtml(item.source)}</div>
          </div>
        `;
        div.addEventListener('click', () => {
          applySelectedArtwork(gameId || state.targetGameForCover, item.image_url);
        });
        gridEl.appendChild(div);
      });
    } catch (e) {
      statusEl.textContent = '아트워크 검색 중 오류가 발생했습니다.';
    }
  }

  async function applySelectedArtwork(gameId, imageUrl) {
    if (!gameId || !imageUrl) return;
    const statusEl = $('gbaArtworkStatus');
    if (statusEl) statusEl.textContent = '선택한 이미지를 다운로드하여 커버로 적용하는 중...';

    try {
      const res = await apiCall('set_artwork', {
        game_id: gameId,
        image_url: imageUrl,
      });
      if (res.success) {
        showToast('커버 이미지가 성공적으로 변경되었습니다.');
        closeArtworkModal();
        loadLibrary();
      } else {
        showToast(res.error || '커버 적용 실패', true);
        if (statusEl) statusEl.textContent = res.error || '커버 적용 실패';
      }
    } catch (e) {
      showToast('커버 변경 중 오류가 발생했습니다.', true);
      if (statusEl) statusEl.textContent = '커버 변경 중 오류가 발생했습니다.';
    }
  }

  // --------------------------------------------------------------------------
  // 무료 홈브류 허브 (Homebrew Hub) 연동
  // --------------------------------------------------------------------------
  function openHomebrewModal() {
    const modal = $('gbaHomebrewModal');
    if (!modal) return;
    modal.style.display = 'flex';
    searchHomebrew(1);
  }

  function closeHomebrewModal() {
    const modal = $('gbaHomebrewModal');
    if (modal) modal.style.display = 'none';
  }

  async function searchHomebrew(page = 1) {
    const statusEl = $('gbaHomebrewStatus');
    const listEl = $('gbaHomebrewList');
    if (!statusEl || !listEl) return;
    state.homebrewPage = page || 1;
    statusEl.textContent = 'Homebrew Hub에서 검색하는 중...';
    listEl.innerHTML = '';
    try {
      const data = await apiCall('homebrew_search', {
        q: ($('gbaHomebrewQuery')?.value || '').trim(),
        platform: $('gbaHomebrewPlatform')?.value || 'GB',
        page: state.homebrewPage,
      });
      if (!data.success) {
        statusEl.textContent = data.error || '검색에 실패했습니다.';
        return;
      }
      state.homebrewPage = Number(data.page_current || state.homebrewPage);
      const totalPages = Math.max(1, Number(data.page_total || 1));
      $('gbaHomebrewPageLabel').textContent = `${state.homebrewPage} / ${totalPages}`;
      $('gbaHomebrewPrevBtn').disabled = state.homebrewPage <= 1;
      $('gbaHomebrewNextBtn').disabled = state.homebrewPage >= totalPages;
      const entries = data.entries || [];
      statusEl.textContent = entries.length
        ? `${data.source || 'Homebrew Hub'} · ${data.results || entries.length}건 중 이 페이지`
        : '조건에 맞는 홈브류가 없습니다.';
      listEl.innerHTML = entries.map((item) => {
        const cover = item.cover_url
          ? `<img class="gba-homebrew-thumb" src="${escapeHtml(item.cover_url)}" alt="">`
          : `<div class="gba-homebrew-thumb"></div>`;
        const btnLabel = item.installed ? '등록됨' : '라이브러리에 넣기';
        const disabled = item.installed || !state.isAdmin ? 'disabled' : '';
        const adminHint = state.isAdmin ? '' : ' title="관리자만 등록할 수 있습니다"';
        return `<div class="gba-homebrew-item" data-slug="${escapeHtml(item.slug)}">
          ${cover}
          <div class="gba-homebrew-meta">
            <div class="gba-homebrew-title">${escapeHtml(item.title)}</div>
            <div class="gba-homebrew-sub">${escapeHtml(item.platform)} · ${escapeHtml(item.developer || 'unknown')} · ${escapeHtml(item.license)}</div>
          </div>
          <button type="button" class="gba-btn gba-btn-primary gba-homebrew-install-btn" data-slug="${escapeHtml(item.slug)}" ${disabled}${adminHint}>${btnLabel}</button>
        </div>`;
      }).join('');
      listEl.querySelectorAll('.gba-homebrew-install-btn').forEach((btn) => {
        btn.addEventListener('click', () => installHomebrew(btn.dataset.slug, btn));
      });
    } catch (err) {
      console.error('[GBA] Homebrew search error:', err);
      statusEl.textContent = 'Homebrew Hub에 연결하지 못했습니다.';
    }
  }

  async function installHomebrew(slug, btn) {
    if (!slug || state.homebrewBusy) return;
    if (!state.isAdmin) {
      showToast('관리자만 홈브류를 등록할 수 있습니다.', true);
      return;
    }
    state.homebrewBusy = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = '받는 중...';
    }
    try {
      const res = await fetch(`${API_WEBHOOK}/homebrew-install`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug }),
      });
      const result = await res.json();
      if (result.success) {
        showToast(result.message || '라이브러리에 등록했습니다.');
        await loadLibrary();
        await searchHomebrew(state.homebrewPage);
      } else {
        showToast(result.error || '등록에 실패했습니다.', true);
        if (btn) {
          btn.disabled = false;
          btn.textContent = '라이브러리에 넣기';
        }
      }
    } catch (err) {
      console.error('[GBA] Homebrew install error:', err);
      showToast('홈브류 등록 중 오류가 발생했습니다.', true);
      if (btn) {
        btn.disabled = false;
        btn.textContent = '라이브러리에 넣기';
      }
    } finally {
      state.homebrewBusy = false;
    }
  }

  function downloadSaveFile(game) {
    if (!game.has_save) {
      showToast(`유저 #${state.userId}의 저장된 세이브 파일이 아직 없습니다.`);
      return;
    }
    const a = document.createElement('a');
    a.href = game.save_url;
    a.download = `${game.title}_user${state.userId}.sav`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // --------------------------------------------------------------------------
  // 게임 관리 기능 (즐겨찾기, 제목 수정, 삭제)
  // --------------------------------------------------------------------------
  async function toggleFavorite(gameId) {
    try {
      const res = await apiCall('toggle_favorite', { game_id: gameId });
      if (res.success) {
        const g = state.games.find((item) => item.id === gameId);
        if (g) {
          g.is_favorite = res.is_favorite;
          renderGames();
        }
      }
    } catch (e) {
      console.error('[GBA] Toggle favorite error:', e);
    }
  }

  async function editGameTitle(game) {
    const newTitle = prompt('변경할 게임 제목을 입력하세요:', game.title);
    if (!newTitle || newTitle.trim() === '' || newTitle === game.title) return;

    try {
      const res = await apiCall('update_title', { game_id: game.id, title: newTitle.trim() });
      if (res.success) {
        game.title = newTitle.trim();
        renderGames();
        showToast('게임 제목이 변경되었습니다.');
      }
    } catch (e) {
      showToast('제목 변경 중 오류가 발생했습니다.', true);
    }
  }

  async function confirmDeleteGame(game) {
    if (!confirm(`'${game.title}' 게임을 정말로 라이브러리에서 삭제하시겠습니까?\n(유저 세이브 데이터도 함께 삭제됩니다)`)) {
      return;
    }

    try {
      const res = await apiCall('delete_game', { game_id: game.id });
      if (res.success) {
        state.games = state.games.filter((g) => g.id !== game.id);
        renderGames();
        showToast('게임이 삭제되었습니다.');
      } else {
        showToast(res.error || '삭제 실패', true);
      }
    } catch (e) {
      showToast('삭제 중 오류가 발생했습니다.', true);
    }
  }

  // --------------------------------------------------------------------------
  // 드래그 & 드롭 이벤트 바인딩
  // --------------------------------------------------------------------------
  function initDragAndDrop() {
    const dropZone = $('gbaDropZone');
    let dragCounter = 0;

    window.addEventListener('dragenter', (e) => {
      e.preventDefault();
      if (!state.isAdmin) return;
      dragCounter++;
      dropZone.classList.add('active');
    });

    window.addEventListener('dragleave', (e) => {
      e.preventDefault();
      if (!state.isAdmin) return;
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        dropZone.classList.remove('active');
      }
    });

    window.addEventListener('dragover', (e) => {
      e.preventDefault();
    });

    window.addEventListener('drop', (e) => {
      e.preventDefault();
      if (!state.isAdmin) return;
      dragCounter = 0;
      dropZone.classList.remove('active');

      const dt = e.dataTransfer;
      if (dt && dt.files && dt.files.length > 0) {
        handleFileUpload(dt.files);
      }
    });
  }

  // --------------------------------------------------------------------------
  // 기종별 조작키 테이블 데이터 & 렌더러
  // --------------------------------------------------------------------------
  const CONTROLS_DATA = {
    snes: {
      name: "슈퍼패미컴 (SFC / SNES)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A 버튼", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "B 버튼", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "X 버튼", key: "S", pad: "X (Xbox) / □ (PS) / Y (스위치)" },
        { action: "Y 버튼", key: "A", pad: "Y (Xbox) / △ (PS) / X (스위치)" },
        { action: "L 범퍼 / 트리거", key: "Q", pad: "LB / L1 / L" },
        { action: "R 범퍼 / 트리거", key: "W", pad: "RB / R1 / R" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    gba: {
      name: "게임보이 어드밴스 (GBA)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A 버튼", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "B 버튼", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "L 트리거", key: "Q (또는 A)", pad: "LB / L1 / L" },
        { action: "R 트리거", key: "W (또는 S)", pad: "RB / R1 / R" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    nes: {
      name: "패미컴 (FC / NES / GB / GBC)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A 버튼", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "B 버튼", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "터보(연사) A / B", key: "S / A", pad: "X / Y" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    n64: {
      name: "닌텐도 64 (N64)",
      rows: [
        { action: "아날로그 스틱 (이동)", key: "↑ ↓ ← →", pad: "왼쪽 아날로그 스틱" },
        { action: "A 버튼 / B 버튼", key: "X / Z", pad: "A / B (Xbox) (✕ / ○ PS)" },
        { action: "Z 트리거 (주요 발사/앉기)", key: "Q (또는 E)", pad: "LT / L2 / ZL" },
        { action: "L / R 버튼", key: "A / S", pad: "LB / RB (L1 / R1)" },
        { action: "C 버튼 (상/하/좌/우)", key: "I / K / J / L", pad: "오른쪽 아날로그 스틱" },
        { action: "START", key: "Enter", pad: "Start / Menu" },
      ],
    },
    genesis: {
      name: "메가드라이브 (MD / Genesis / MasterSystem / GameGear)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A 버튼 (공격 1)", key: "A", pad: "Y (Xbox) / △ (PS) / X (스위치)" },
        { action: "B 버튼 (점프/공격 2)", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "C 버튼 (특수/공격 3)", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "X / Y / Z (6버튼 격투)", key: "Q / S / W", pad: "LB / X / RB" },
        { action: "START", key: "Enter", pad: "Start / Menu" },
      ],
    },
    arcade: {
      name: "오락실 아케이드 / MAME (Arcade / FBNeo)",
      rows: [
        { action: "레버 / 스틱 (8방향 이동)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "동전 넣기 (COIN 1)", key: "Shift (또는 5 / 6)", pad: "Back / Select / Share" },
        { action: "1인용 시작 (START 1)", key: "Enter (또는 1)", pad: "Start / Options / +" },
        { action: "버튼 1 (약펀치 / 샷)", key: "Z", pad: "A (Xbox) / ✕ (PS)" },
        { action: "버튼 2 (강펀치 / 점프)", key: "X", pad: "B (Xbox) / ○ (PS)" },
        { action: "버튼 3 (약킥 / 폭탄)", key: "A", pad: "X (Xbox) / □ (PS)" },
        { action: "버튼 4 (강킥 / 특수)", key: "S", pad: "Y (Xbox) / △ (PS)" },
        { action: "버튼 5 / 6 (6버튼 격투)", key: "Q / W", pad: "LB / RB (L1 / R1)" },
        { action: "MAME 서비스/테스트 메뉴", key: "F2 / Tab", pad: "R3 (오른쪽 스틱 클릭)" },
      ],
    },
    neogeo: {
      name: "SNK 네오지오 (Neo-Geo MVS/AES)",
      rows: [
        { action: "레버 (방향 이동)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "동전 (COIN / SELECT)", key: "Shift", pad: "Back / Select / Share" },
        { action: "1P 스타트 (START)", key: "Enter", pad: "Start / +" },
        { action: "A 버튼 (약펀치 / 샷)", key: "Z", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "B 버튼 (약킥 / 점프)", key: "X", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "C 버튼 (강펀치 / 폭탄)", key: "A", pad: "X (Xbox) / □ (PS) / Y (스위치)" },
        { action: "D 버튼 (강킥 / 구르기)", key: "S", pad: "Y (Xbox) / △ (PS) / X (스위치)" },
      ],
    },
    psx: {
      name: "플레이스테이션 1 (PS1)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 아날로그 스틱" },
        { action: "○ (동그라미)", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "✕ (엑스)", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "△ (세모)", key: "S", pad: "X (Xbox) / □ (PS) / Y (스위치)" },
        { action: "□ (네모)", key: "A", pad: "Y (Xbox) / △ (PS) / X (스위치)" },
        { action: "L1 / R1 (숄더)", key: "Q / W", pad: "LB / RB (L1 / R1)" },
        { action: "L2 / R2 (트리거)", key: "E / R", pad: "LT / RT (L2 / R2)" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    psp: {
      name: "플레이스테이션 포터블 (PSP)",
      rows: [
        { action: "아날로그 패드 / D-Pad", key: "↑ ↓ ← →", pad: "왼쪽 아날로그 스틱 / 십자키" },
        { action: "○ (동그라미)", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "✕ (엑스)", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "△ (세모)", key: "S", pad: "X (Xbox) / □ (PS) / Y (스위치)" },
        { action: "□ (네모)", key: "A", pad: "Y (Xbox) / △ (PS) / X (스위치)" },
        { action: "L / R 트리거", key: "Q / W", pad: "LB / RB (L1 / R1)" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    nds: {
      name: "닌텐도 DS (NDS)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A / B / X / Y", key: "X / Z / S / A", pad: "A / B / X / Y (패드 4버튼)" },
        { action: "L / R 트리거", key: "Q / W", pad: "LB / RB" },
        { action: "하단 터치스크린", key: "마우스 좌클릭 & 드래그", pad: "터치패드 / 마우스" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    saturn: {
      name: "세가 새턴 (Sega Saturn)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A / B / C 버튼 (하단 3열)", key: "A / Z / X", pad: "X / A / B (Xbox) (□ / ✕ / ○ PS)" },
        { action: "X / Y / Z 버튼 (상단 3열)", key: "Q / S / W", pad: "Y / LB / RB" },
        { action: "L / R 트리거", key: "E / R", pad: "LT / RT (L2 / R2)" },
        { action: "START", key: "Enter", pad: "Start / Menu" },
      ],
    },
    pce: {
      name: "PC엔진 (PC Engine / TurboGrafx-16)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "I 버튼 (결정/공격 1)", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "II 버튼 (취소/공격 2)", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "III ~ VI (6버튼 패드)", key: "A / S / Q / W", pad: "X / Y / LB / RB" },
        { action: "RUN / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
  };

  function renderControlsTable(sys) {
    const data = CONTROLS_DATA[sys] || CONTROLS_DATA.snes;
    const wrap = $('gbaControlsTableWrap');
    if (!wrap) return;

    let html = `
      <table class="gba-controls-table">
        <thead>
          <tr>
            <th>${escapeHtml(data.name)} 기능</th>
            <th>키보드</th>
            <th>게임패드 (Xbox / PS / 스위치)</th>
          </tr>
        </thead>
        <tbody>
    `;

    data.rows.forEach((r) => {
      const keysHtml = r.key.split(' ').map((k) => `<kbd>${escapeHtml(k)}</kbd>`).join(' ');
      html += `
        <tr>
          <td><strong>${escapeHtml(r.action)}</strong></td>
          <td>${keysHtml}</td>
          <td>${escapeHtml(r.pad)}</td>
        </tr>
      `;
    });

    html += `</tbody></table>`;
    wrap.innerHTML = html;
  }

  function bindEvents() {
    // 검색창 입력 & 초기화
    const searchInput = $('gbaSearchInput');
    const clearBtn = $('gbaSearchClear');
    searchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value.trim();
      clearBtn.style.display = state.searchQuery ? 'block' : 'none';
      renderGames();
    });
    clearBtn.addEventListener('click', () => {
      searchInput.value = '';
      state.searchQuery = '';
      clearBtn.style.display = 'none';
      renderGames();
    });

    // 기종 카테고리 드롭다운 변경
    $('gbaCategorySelect')?.addEventListener('change', (e) => {
      state.category = e.target.value;
      renderGames();
    });

    // 라이브러리 정렬 선택
    const sortSelect = $('gbaSortSelect');
    if (sortSelect) {
      sortSelect.value = state.sort;
      sortSelect.addEventListener('change', (e) => {
        state.sort = e.target.value;
        localStorage.setItem('gba_library_sort', state.sort);
        renderGames();
      });
    }

    // 즐겨찾기 필터 토글 버튼
    $('gbaFavoriteFilterBtn')?.addEventListener('click', () => {
      state.isFavoriteOnly = !state.isFavoriteOnly;
      const btn = $('gbaFavoriteFilterBtn');
      if (btn) {
        if (state.isFavoriteOnly) {
          btn.classList.add('active');
          const icon = btn.querySelector('i');
          if (icon) icon.className = 'fa-solid fa-star';
        } else {
          btn.classList.remove('active');
          const icon = btn.querySelector('i');
          if (icon) icon.className = 'fa-regular fa-star';
        }
      }
      renderGames();
    });

    // 무료 홈브류 모달 바인딩
    $('gbaHomebrewBtn')?.addEventListener('click', openHomebrewModal);
    $('gbaEmptyHomebrewBtn')?.addEventListener('click', openHomebrewModal);
    $('gbaHomebrewCloseBtn')?.addEventListener('click', closeHomebrewModal);
    $('gbaHomebrewSearchBtn')?.addEventListener('click', () => searchHomebrew(1));
    $('gbaHomebrewPlatform')?.addEventListener('change', () => searchHomebrew(1));
    $('gbaHomebrewQuery')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        searchHomebrew(1);
      }
    });
    $('gbaHomebrewPrevBtn')?.addEventListener('click', () => {
      if (state.homebrewPage > 1) searchHomebrew(state.homebrewPage - 1);
    });
    $('gbaHomebrewNextBtn')?.addEventListener('click', () => {
      searchHomebrew(state.homebrewPage + 1);
    });

    // 아트워크 검색 모달 이벤트 바인딩
    $('gbaArtworkCloseBtn')?.addEventListener('click', closeArtworkModal);
    $('gbaArtworkSearchBtn')?.addEventListener('click', () => {
      searchArtwork(state.targetGameForCover, $('gbaArtworkQuery')?.value);
    });
    $('gbaArtworkQuery')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        searchArtwork(state.targetGameForCover, $('gbaArtworkQuery')?.value);
      }
    });
    $('gbaArtworkDirectUploadBtn')?.addEventListener('click', () => {
      closeArtworkModal();
      $('gbaCoverInput').click();
    });

    // 상단 툴바 버튼
    $('gbaUploadBtn').addEventListener('click', () => $('gbaFileInput').click());
    $('gbaBiosUploadBtn')?.addEventListener('click', openBiosModal);
    $('gbaEmptyUploadBtn')?.addEventListener('click', () => $('gbaFileInput').click());
    $('gbaScanBtn').addEventListener('click', async () => {
      const scanBtn = $('gbaScanBtn');
      if (scanBtn.classList.contains('gba-btn-scanning')) return;

      const scanModal = $('gbaScanModal');
      const scanStatus = $('gbaScanStatus');
      const scanProgressBar = $('gbaScanProgressBar');
      const scanDetails = $('gbaScanDetails');

      scanBtn.classList.add('gba-btn-scanning');
      scanBtn.title = '게임 라이브러리 재스캔 진행 중...';

      // 목록을 지우지 않고, 클릭을 방지하는 재스캔 프로그레스바 모달 표시
      if (scanModal) {
        scanModal.style.display = 'flex';
        if (scanStatus) scanStatus.textContent = '게임 파일 및 커버 유효성을 검증하는 중...';
        if (scanProgressBar) scanProgressBar.style.width = '0%';
        if (scanDetails) scanDetails.textContent = '검증 준비 중...';
      }

      try {
        const gamesToCheck = [...(state.games || [])];
        const total = gamesToCheck.length;

        let checkedCount = 0;
        let coverUpdatedCount = 0;
        let deletedCount = 0;

        // 1단계: 동적 작업 풀(Sliding Window Worker Pool)을 이용한 개별 실시간 검증 (동시 4개 유지)
        const CONCURRENCY = 4;
        let cursor = 0;

        const updateProgress = () => {
          const percent = total > 0 ? Math.min(Math.round((checkedCount / total) * 85), 85) : 85;
          if (scanProgressBar) scanProgressBar.style.width = `${percent}%`;
          if (scanDetails) scanDetails.textContent = `${Math.min(checkedCount, total)} / ${total} 게임 검증 완료 (${percent}%)`;
        };

        const checkWorker = async () => {
          while (cursor < gamesToCheck.length) {
            const game = gamesToCheck[cursor++];
            if (!game) break;
            try {
              const res = await apiCall('check_game', { game_id: game.id });
              checkedCount++;
              if (res && res.success && res.result) {
                if (res.result.deleted) {
                  deletedCount++;
                  state.games = state.games.filter((g) => g.id !== game.id);
                } else if (res.result.cover_updated) {
                  coverUpdatedCount++;
                  game.cover_path = true;
                  if (res.result.cover_url) {
                    game.cover_url = res.result.cover_url;
                  }
                }
              }
            } catch (err) {
              checkedCount++;
              console.error('[GBA] Check game error:', game.id, err);
            }
            updateProgress();
          }
        };

        const workerCount = Math.min(CONCURRENCY, total || 1);
        const workers = [];
        for (let w = 0; w < workerCount; w++) {
          workers.push(checkWorker());
        }
        await Promise.all(workers);

        // 2단계: 신규 추가된 ROM 파일 디스크 스캔 및 등록
        if (scanStatus) scanStatus.textContent = '새로운 ROM 파일 및 디렉터리를 스캔하는 중...';
        if (scanProgressBar) scanProgressBar.style.width = '88%';
        if (scanDetails) scanDetails.textContent = '신규 ROM 바이너리 및 DAT 정밀 분석 중...';

        // 실시간 신규 스캔 진행률 폴링
        let newScanPollTimer = setInterval(async () => {
          try {
            const pollRes = await apiCall('scan_progress');
            if (pollRes && pollRes.success && pollRes.progress) {
              const p = pollRes.progress;
              if (p.total > 0 && p.is_running) {
                const subPercent = Math.min(88 + Math.round((p.current / p.total) * 10), 98);
                if (scanProgressBar) scanProgressBar.style.width = `${subPercent}%`;
                if (scanDetails) {
                  scanDetails.textContent = `신규 ROM 등록 중: ${p.current} / ${p.total} (${p.current_file || ''})`;
                }
              }
            }
          } catch (e) {
            // ignore
          }
        }, 350);

        let newRomRes = null;
        try {
          newRomRes = await apiCall('scan_new_roms');
        } finally {
          if (newScanPollTimer) clearInterval(newScanPollTimer);
        }

        if (scanProgressBar) scanProgressBar.style.width = '100%';
        if (scanDetails) scanDetails.textContent = '라이브러리 동기화 완료!';

        // 최종 전체 라이브러리 동기화
        await loadLibrary(true);

        const newCount = (newRomRes && newRomRes.stats && newRomRes.stats.new_count) || 0;
        let resultMsg = '스캔 및 동기화 완료!';
        if (newCount > 0) {
          resultMsg = `🎉 신규 ROM ${newCount}개 등록 완료!`;
          if (coverUpdatedCount > 0 || deletedCount > 0) {
            resultMsg += ` (커버 ${coverUpdatedCount}개 등록, 미존재 ${deletedCount}개 정리)`;
          }
        } else if (coverUpdatedCount > 0 || deletedCount > 0) {
          resultMsg += ` (신규 롬 없음 / 커버 ${coverUpdatedCount}개 등록, 미존재 ${deletedCount}개 정리)`;
        } else {
          resultMsg = '스캔 완료 (새로 추가되거나 변경된 롬 파일이 없습니다)';
        }
        showToast(resultMsg);
      } catch (e) {
        console.error('[GBA] Scan error:', e);
        showToast('스캔 중 오류가 발생했습니다.', true);
        loadLibrary(true);
      } finally {
        scanBtn.classList.remove('gba-btn-scanning');
        scanBtn.title = '폴더 스캔 및 새로고침';
        if (scanModal) {
          scanModal.style.display = 'none';
        }
      }
    });

    // 상단 풀 스캔 (Full Re-scan) 버튼
    $('gbaFullScanBtn')?.addEventListener('click', async () => {
      const fullScanBtn = $('gbaFullScanBtn');
      if (fullScanBtn.classList.contains('gba-btn-scanning')) return;

      if (!confirm('모든 롬 파일을 처음부터 전수 재분석(Full Re-scan)하시겠습니까?\n\n- DAT DB 및 바이너리 헤더를 100% 처음부터 다시 분석합니다.\n- 유저 세이브 파일과 즐겨찾기는 안전하게 보존됩니다.')) {
        return;
      }

      const scanModal = $('gbaScanModal');
      const scanStatus = $('gbaScanStatus');
      const scanProgressBar = $('gbaScanProgressBar');
      const scanDetails = $('gbaScanDetails');

      fullScanBtn.classList.add('gba-btn-scanning');
      fullScanBtn.title = '전체 재스캔 진행 중...';

      let pollTimer = null;
      if (scanModal) {
        scanModal.style.display = 'flex';
        if (scanStatus) scanStatus.textContent = '모든 롬 파일의 전수 재스캔(Full Re-scan)을 진행하는 중...';
        if (scanProgressBar) scanProgressBar.style.width = '5%';
        if (scanDetails) scanDetails.textContent = '통합 DAT DB 및 바이너리 헤더 전수 분석 준비 중...';

        // 0.4초 간격으로 실시간 파일 단위 프로그레스 폴링
        pollTimer = setInterval(async () => {
          try {
            const pollRes = await apiCall('scan_progress');
            if (pollRes && pollRes.success && pollRes.progress) {
              const p = pollRes.progress;
              if (p.total > 0) {
                const percent = Math.min(Math.round((p.current / p.total) * 90), 90);
                if (scanProgressBar) scanProgressBar.style.width = `${percent}%`;
                if (scanDetails) {
                  scanDetails.textContent = `${p.current} / ${p.total} 파일 분석 중 (${percent}%) - ${p.current_file || ''}`;
                }
              }
            }
          } catch (e) {
            // 폴링 오류 무시
          }
        }, 400);
      }

      try {
        const res = await apiCall('full_scan');
        if (pollTimer) clearInterval(pollTimer);

        if (scanProgressBar) scanProgressBar.style.width = '95%';
        if (scanDetails) scanDetails.textContent = '라이브러리 갱신 중...';

        await loadLibrary(true);

        if (scanProgressBar) scanProgressBar.style.width = '100%';
        showToast(res && res.message ? res.message : '전체 재스캔 및 메타데이터 동기화가 완료되었습니다!');
      } catch (err) {
        if (pollTimer) clearInterval(pollTimer);
        console.error('[GBA] Full scan error:', err);
        showToast('전체 재스캔 중 오류가 발생했습니다.', true);
        loadLibrary(true);
      } finally {
        if (pollTimer) clearInterval(pollTimer);
        fullScanBtn.classList.remove('gba-btn-scanning');
        fullScanBtn.title = '모든 롬을 처음부터 전수 정밀 재스캔 (DAT/헤더/바이오스 전체 재동기화)';
        if (scanModal) {
          scanModal.style.display = 'none';
        }
      }
    });
    $('gbaBiosCloseBtn')?.addEventListener('click', closeBiosModal);
    $('gbaBiosOkBtn')?.addEventListener('click', closeBiosModal);
    $('gbaBiosModalUploadBtn')?.addEventListener('click', () => $('gbaBiosModalFileInput').click());
    $('gbaBiosModalFileInput')?.addEventListener('change', (e) => handleFileUpload(e.target.files, 'bios'));

    $('gbaBiosSearchInput')?.addEventListener('input', (e) => {
      state.biosSearch = e.target.value;
      state.biosPage = 1;
      renderBiosModal();
    });

    const biosDropZone = $('gbaBiosDropZone');
    if (biosDropZone) {
      ['dragenter', 'dragover'].forEach((eventName) => {
        biosDropZone.addEventListener(eventName, (e) => {
          e.preventDefault();
          e.stopPropagation();
          biosDropZone.classList.add('dragover');
        });
      });
      ['dragleave', 'drop'].forEach((eventName) => {
        biosDropZone.addEventListener(eventName, (e) => {
          e.preventDefault();
          e.stopPropagation();
          biosDropZone.classList.remove('dragover');
        });
      });
      biosDropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt ? dt.files : null;
        if (files && files.length > 0) {
          handleFileUpload(files, 'bios');
        }
      });
    }

    // 조작키 기종별 탭 클릭
    document.querySelectorAll('.gba-sys-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.gba-sys-tab').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        renderControlsTable(btn.dataset.sys);
      });
    });

    // 조작키 안내 모달
    const openControlsModal = () => {
      const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
      let foundPad = null;
      for (let gp of gamepads) {
        if (gp) {
          foundPad = gp;
          break;
        }
      }
      const padStatusEl = $('gbaGamepadStatus');
      if (foundPad) {
        padStatusEl.innerHTML = `<i class="fa-solid fa-gamepad"></i> <span>연결된 컨트롤러: <strong>${escapeHtml(foundPad.id)}</strong></span>`;
        padStatusEl.style.color = 'var(--gba-success)';
      } else {
        padStatusEl.innerHTML = `<i class="fa-solid fa-gamepad"></i> <span>컨트롤러 연결 대기 중 (패드의 아무 버튼이나 1회 누르세요)</span>`;
        padStatusEl.style.color = 'var(--gba-text-muted)';
      }

      // 현재 실행 중인 게임 기종에 맞추어 탭 자동 선택
      let targetSys = 'snes';
      if (state.activeGame) {
        const core = (state.activeGame.core || '').toLowerCase();
        const platform = (state.activeGame.platform || '').toLowerCase();
        if (core === 'gba' || platform === 'gba') targetSys = 'gba';
        else if (core === 'nes' || core === 'gb' || core === 'gbc' || platform === 'nes' || platform === 'gb' || platform === 'gbc') targetSys = 'nes';
        else if (core === 'n64' || platform === 'n64') targetSys = 'n64';
        else if (core === 'arcade' || core === 'mame2003' || core === 'mame' || platform === 'arcade') targetSys = 'arcade';
        else if (platform === 'neo-geo' || platform === 'neogeo') targetSys = 'neogeo';
        else if (core === 'psx' || platform === 'ps1' || platform === 'psx') targetSys = 'psx';
        else if (core === 'psp' || platform === 'psp') targetSys = 'psp';
        else if (core === 'nds' || platform === 'nds') targetSys = 'nds';
        else if (core === 'segasaturn' || core === 'saturn' || platform === 'saturn') targetSys = 'saturn';
        else if (core === 'pce' || platform === 'pce' || platform === 'supergrafx') targetSys = 'pce';
        else if (core.includes('sega') || platform === 'genesis' || platform === 'megadrive' || platform === 'md' || platform === 'sms' || platform === 'gg') targetSys = 'genesis';
        else targetSys = 'snes';
      }

      document.querySelectorAll('.gba-sys-tab').forEach((b) => {
        b.classList.toggle('active', b.dataset.sys === targetSys);
      });
      renderControlsTable(targetSys);

      $('gbaControlsModal').style.display = 'flex';
    };

    $('gbaControlsHelpBtn').addEventListener('click', openControlsModal);
    $('gbaBtnPlayerControls').addEventListener('click', openControlsModal);
    $('gbaControlsCloseBtn').addEventListener('click', () => ($('gbaControlsModal').style.display = 'none'));
    $('gbaControlsOkBtn').addEventListener('click', () => ($('gbaControlsModal').style.display = 'none'));

    // 파일 인풋 변경
    $('gbaFileInput').addEventListener('change', (e) => handleFileUpload(e.target.files, 'rom'));
    $('gbaBiosFileInput').addEventListener('change', (e) => handleFileUpload(e.target.files, 'bios'));
    $('gbaCoverInput').addEventListener('change', (e) => handleFileUpload(e.target.files, 'cover'));

    // 업로드 모달 닫기
    $('gbaUploadCloseBtn').addEventListener('click', () => {
      $('gbaUploadModal').style.display = 'none';
    });

    // 설정 모달 열기 & 저장
    $('gbaSettingsBtn').addEventListener('click', () => {
      $('gbaSettingCloudSave').checked = state.config.cloud_save_enabled;
      $('gbaSettingInterval').value = state.config.auto_save_interval_sec;
      $('gbaSettingExtraPath').value = state.config.extra_roms_path || '';
      $('gbaSettingCoversPath').value = state.config.covers_path || '';
      $('gbaSettingBiosPath').value = state.config.bios_path || '';
      $('gbaSettingsModal').style.display = 'flex';
    });

    $('gbaSettingsCloseBtn').addEventListener('click', () => {
      $('gbaSettingsModal').style.display = 'none';
    });
    $('gbaSettingsCancelBtn').addEventListener('click', () => {
      $('gbaSettingsModal').style.display = 'none';
    });

    $('gbaSettingsSaveBtn').addEventListener('click', async () => {
      const extraPath = $('gbaSettingExtraPath').value.trim();
      const coversPath = $('gbaSettingCoversPath').value.trim();
      const biosPath = $('gbaSettingBiosPath').value.trim();
      const cloudSave = $('gbaSettingCloudSave').checked ? '1' : '0';
      const interval = $('gbaSettingInterval').value.trim();
      const saveBtn = $('gbaSettingsSaveBtn');

      const prevCoversPath = (state.config.covers_path || '').trim();
      const needCoverMigration = coversPath && coversPath !== prevCoversPath;

      const prevBiosPath = (state.config.bios_path || '').trim();
      const needBiosMigration = biosPath && biosPath !== prevBiosPath;

      saveBtn.disabled = true;
      saveBtn.textContent = '저장 중...';

      const scanModal = $('gbaScanModal');
      const scanStatus = $('gbaScanStatus');
      const scanProgressBar = $('gbaScanProgressBar');
      const scanDetails = $('gbaScanDetails');

      try {
        if (needCoverMigration || needBiosMigration) {
          $('gbaSettingsModal').style.display = 'none';
          if (scanModal) {
            scanModal.style.display = 'flex';
            if (scanStatus) scanStatus.textContent = '기존 파일 마이그레이션 대상을 분석하는 중...';
            if (scanProgressBar) scanProgressBar.style.width = '0%';
            if (scanDetails) scanDetails.textContent = '분석 준비 중...';
          }

          // 1. 커버 마이그레이션
          if (needCoverMigration) {
            const candRes = await apiCall('get_cover_migration_candidates', { target_dir: coversPath });
            const items = (candRes && candRes.success && candRes.items) ? candRes.items : [];
            const total = items.length;

            if (total > 0) {
              if (scanStatus) scanStatus.textContent = `기존 커버 이미지를 새 폴더로 이동하는 중... (${total}개)`;
              const BATCH_SIZE = 10;
              let movedTotal = 0;

              for (let i = 0; i < total; i += BATCH_SIZE) {
                const batch = items.slice(i, i + BATCH_SIZE);
                await apiCall('migrate_cover_batch', {
                  target_dir: coversPath,
                  items: batch,
                });

                movedTotal += batch.length;
                const percent = Math.min(Math.round((movedTotal / total) * 50), 50);
                if (scanProgressBar) scanProgressBar.style.width = `${percent}%`;
                if (scanDetails) scanDetails.textContent = `${movedTotal} / ${total} 커버 파일 이동 완료`;
              }
            }
          }

          // 2. 바이오스 마이그레이션
          if (needBiosMigration) {
            const biosCandRes = await apiCall('get_bios_migration_candidates', { target_dir: biosPath });
            const biosItems = (biosCandRes && biosCandRes.success && biosCandRes.items) ? biosCandRes.items : [];
            const biosTotal = biosItems.length;

            if (biosTotal > 0) {
              if (scanStatus) scanStatus.textContent = `기존 바이오스 파일을 새 폴더로 이동하는 중... (${biosTotal}개)`;
              const BATCH_SIZE = 10;
              let biosMovedTotal = 0;

              for (let i = 0; i < biosTotal; i += BATCH_SIZE) {
                const batch = biosItems.slice(i, i + BATCH_SIZE);
                await apiCall('migrate_bios_batch', {
                  target_dir: biosPath,
                  items: batch,
                });

                biosMovedTotal += batch.length;
                const percent = 50 + Math.min(Math.round((biosMovedTotal / biosTotal) * 40), 40);
                if (scanProgressBar) scanProgressBar.style.width = `${percent}%`;
                if (scanDetails) scanDetails.textContent = `${biosMovedTotal} / ${biosTotal} 바이오스 파일 이동 완료`;
              }
            }
          }

          if (scanStatus) scanStatus.textContent = '설정 저장 및 마무리 중...';
          if (scanProgressBar) scanProgressBar.style.width = '95%';
        }

        const res = await apiCall('save_settings', {
          extra_roms_path: extraPath,
          covers_path: coversPath,
          bios_path: biosPath,
          cloud_save_enabled: cloudSave,
          auto_save_interval_sec: interval,
        });

        if (res && res.success) {
          state.config.extra_roms_path = extraPath;
          state.config.covers_path = coversPath;
          state.config.bios_path = biosPath;
          state.config.cloud_save_enabled = cloudSave === '1';
          state.config.auto_save_interval_sec = parseInt(interval, 10) || 60;
          $('gbaSettingsModal').style.display = 'none';

          if (scanProgressBar) scanProgressBar.style.width = '100%';
          if (scanDetails) scanDetails.textContent = '설정 및 마이그레이션 완료!';

          showToast('설정 및 마이그레이션이 성공적으로 완료되었습니다! 📁');
          loadLibrary(true);
        } else {
          showToast(res && res.error ? res.error : '설정 저장에 실패했습니다.', true);
        }
      } catch (e) {
        showToast(`설정 저장 중 오류 발생: ${e.message || e}`, true);
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '설정 저장';
        if (scanModal) {
          setTimeout(() => {
            scanModal.style.display = 'none';
          }, 300);
        }
      }
    });

    // 플레이어 커스텀 모달 컨트롤 액션
    $('gbaBtnExit').addEventListener('click', exitGame);
    $('gbaBtnPausePlay').addEventListener('click', togglePausePlay);
    $('gbaBtnSpeed').addEventListener('click', cycleSpeed);
    $('gbaBtnMute').addEventListener('click', toggleMuteVolume);
    $('gbaBtnRestart').addEventListener('click', restartCurrentGame);
    // 그래픽 설정 모달 열기 & 이벤트 바인딩
    const openGraphicsModal = () => {
      $('gbaSettingShader').value = state.graphics.shader || 'disabled';
      $('gbaSettingPixelMode').value = state.graphics.pixelMode || 'pixelated';
      $('gbaSettingAspectRatio').value = state.graphics.aspectRatio || '3/2';
      $('gbaGraphicsModal').style.display = 'flex';
    };

    $('gbaBtnGraphics')?.addEventListener('click', openGraphicsModal);
    $('gbaGraphicsCloseBtn')?.addEventListener('click', () => ($('gbaGraphicsModal').style.display = 'none'));
    $('gbaGraphicsOkBtn')?.addEventListener('click', () => ($('gbaGraphicsModal').style.display = 'none'));

    $('gbaSettingShader')?.addEventListener('change', (e) => {
      state.graphics.shader = e.target.value;
      localStorage.setItem('gba_shader', state.graphics.shader);
      applyGraphicsSettings();
      showToast('화면 셰이더가 적용되었습니다.');
    });

    $('gbaSettingPixelMode')?.addEventListener('change', (e) => {
      state.graphics.pixelMode = e.target.value;
      localStorage.setItem('gba_pixel_mode', state.graphics.pixelMode);
      applyGraphicsSettings();
      showToast('픽셀 렌더링 모드가 변경되었습니다.');
    });

    $('gbaSettingAspectRatio')?.addEventListener('change', (e) => {
      state.graphics.aspectRatio = e.target.value;
      localStorage.setItem('gba_aspect_ratio', state.graphics.aspectRatio);
      applyGraphicsSettings();
      showToast('화면 비율이 변경되었습니다.');
    });

    $('gbaBtnFullscreen').addEventListener('click', () => {
      const viewport = $('gbaEmulatorViewport');
      if (!document.fullscreenElement) {
        viewport.requestFullscreen().catch((err) => console.warn(err));
      } else {
        document.exitFullscreen().catch((err) => console.warn(err));
      }
    });

    // 키보드 단축키 (ESC: 맨 위 모달부터 한 번에 딱 1개씩만 닫기)
    let lastEscTimestamp = 0;

    window.addEventListener(
      'keydown',
      (e) => {
        if (e.key === 'Escape') {
          // 키 반복(e.repeat) 및 250ms 디바운스: 한 번 누름에 여러 모달이 연달아 닫히는 현상 완벽 방지
          if (e.repeat) {
            e.preventDefault();
            e.stopImmediatePropagation();
            return;
          }

          const now = Date.now();
          if (now - lastEscTimestamp < 250) {
            e.preventDefault();
            e.stopImmediatePropagation();
            return;
          }

          // 1. 우클릭 콘텍스트 메뉴가 열려있다면 먼저 닫기
          const ctxMenu = $('gbaContextMenu');
          if (ctxMenu && ctxMenu.style.display !== 'none') {
            e.preventDefault();
            e.stopImmediatePropagation();
            lastEscTimestamp = now;
            ctxMenu.style.display = 'none';
            return;
          }

          // 2. 에뮬레이터 화면 위에 떠 있는 서브 모달들 중 현재 보이는 1개만 닫기
          const overlayModalIds = [
            'gbaArtworkModal',
            'gbaGraphicsModal',
            'gbaControlsModal',
            'gbaUploadModal',
            'gbaSettingsModal',
            'gbaBiosWarningModal',
          ];

          for (const id of overlayModalIds) {
            const modal = $(id);
            if (modal && modal.style.display && modal.style.display !== 'none') {
              e.preventDefault();
              e.stopImmediatePropagation();
              lastEscTimestamp = now;
              modal.style.display = 'none';
              return; // 단 1개만 닫고 리턴
            }
          }

          // 3. 기타 열려있는 서브 모달 1개만 닫기
          const otherVisibleModals = Array.from(document.querySelectorAll('.gba-modal')).filter(
            (m) => m.id !== 'gbaPlayerModal' && m.style.display && m.style.display !== 'none'
          );
          if (otherVisibleModals.length > 0) {
            e.preventDefault();
            e.stopImmediatePropagation();
            lastEscTimestamp = now;
            otherVisibleModals[0].style.display = 'none';
            return;
          }

          // 4. 전체화면 상태일 때는 전체화면 먼저 해제
          if (document.fullscreenElement) {
            e.preventDefault();
            e.stopImmediatePropagation();
            lastEscTimestamp = now;
            document.exitFullscreen().catch((err) => console.warn(err));
            return;
          }

          // 5. 게임 플레이 중에는 ESC를 에뮬레이터 코어(MAME 설정 메뉴 닫기 등)로 온전히 전달
          // (게임 플레이어 종료는 상단 툴바의 [나가기] 버튼으로 안전하게 종료)
        }

        // 활성 게임 플레이 중일 때 모든 키보드 이벤트를 에뮬레이터 코어로 직접 전달
        if (state.activeGame && window.EJS_emulator && typeof window.EJS_emulator.keyChange === 'function') {
          try {
            window.EJS_emulator.keyChange(e);
          } catch (err) {}
        }
      },
      true
    );

    window.addEventListener(
      'keyup',
      (e) => {
        if (state.activeGame && window.EJS_emulator && typeof window.EJS_emulator.keyChange === 'function') {
          try {
            window.EJS_emulator.keyChange(e);
          } catch (err) {}
        }
      },
      true
    );

    // --------------------------------------------------------------------------
    // 우클릭 커스텀 콘텍스트 메뉴 및 화면 캡처 이벤트
    // --------------------------------------------------------------------------
    const contextMenu = $('gbaContextMenu');
    const viewport = $('gbaEmulatorViewport');

    const hideContextMenu = () => {
      if (contextMenu) contextMenu.style.display = 'none';
    };

    if (viewport) {
      viewport.addEventListener('contextmenu', (e) => {
        if (!state.activeGame) return;
        e.preventDefault();

        // 뷰포트 영역 내 좌표 보정
        const menuWidth = 290;
        const menuHeight = 130;
        let x = e.clientX;
        let y = e.clientY;

        if (x + menuWidth > window.innerWidth) {
          x = window.innerWidth - menuWidth - 10;
        }
        if (y + menuHeight > window.innerHeight) {
          y = window.innerHeight - menuHeight - 10;
        }

        contextMenu.style.left = `${x}px`;
        contextMenu.style.top = `${y}px`;
        contextMenu.style.display = 'flex';
      });
    }

    document.addEventListener('click', (e) => {
      if (!e.target.closest('#gbaContextMenu')) {
        hideContextMenu();
      }
    });

    $('gbaCtxSetCover')?.addEventListener('click', () => {
      hideContextMenu();
      captureGameScreenshotAndSetCover();
    });

    // 게임패드 연결 이벤트 감지
    window.addEventListener('gamepadconnected', (e) => {
      console.log('[GBA] Gamepad connected:', e.gamepad.id);
      showToast(`게임패드 연결됨: ${e.gamepad.id}`);
      if (state.activeGame) {
        startGamepadPoller();
      }
    });

    window.addEventListener('gamepaddisconnected', (e) => {
      console.log('[GBA] Gamepad disconnected:', e.gamepad.id);
      showToast(`게임패드 연결 해제됨`);
    });
  }

  // --------------------------------------------------------------------------
  // 게임 화면 캡처 및 레터박스(블랙바) 자동 크롭 후 커버 이미지 등록
  // --------------------------------------------------------------------------
  function cropLetterboxFromCanvas(canvas) {
    const width = canvas.width;
    const height = canvas.height;
    if (!width || !height) return canvas;

    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = width;
    tempCanvas.height = height;
    const ctx = tempCanvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(canvas, 0, 0);

    let imgData;
    try {
      imgData = ctx.getImageData(0, 0, width, height);
    } catch (e) {
      console.warn('[GBA] getImageData error:', e);
      return tempCanvas;
    }

    const data = imgData.data;
    const isBlackPixel = (x, y) => {
      const idx = (y * width + x) * 4;
      const r = data[idx];
      const g = data[idx + 1];
      const b = data[idx + 2];
      const a = data[idx + 3];
      return a < 50 || (r <= 25 && g <= 25 && b <= 25);
    };

    let minX = 0, maxX = width - 1;
    let minY = 0, maxY = height - 1;

    // 상단 블랙바 스캔
    topLoop: for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x += 4) {
        if (!isBlackPixel(x, y)) {
          minY = y;
          break topLoop;
        }
      }
    }

    // 하단 블랙바 스캔
    bottomLoop: for (let y = height - 1; y >= minY; y--) {
      for (let x = 0; x < width; x += 4) {
        if (!isBlackPixel(x, y)) {
          maxY = y;
          break bottomLoop;
        }
      }
    }

    // 좌측 블랙바 스캔
    leftLoop: for (let x = 0; x < width; x++) {
      for (let y = minY; y <= maxY; y += 4) {
        if (!isBlackPixel(x, y)) {
          minX = x;
          break leftLoop;
        }
      }
    }

    // 우측 블랙바 스캔
    rightLoop: for (let x = width - 1; x >= minX; x--) {
      for (let y = minY; y <= maxY; y += 4) {
        if (!isBlackPixel(x, y)) {
          maxX = x;
          break rightLoop;
        }
      }
    }

    const cropW = maxX - minX + 1;
    const cropH = maxY - minY + 1;

    // 유효한 크롭 영역이 검출된 경우 크롭 수행
    if (cropW > 40 && cropH > 40 && (cropW < width || cropH < height)) {
      const croppedCanvas = document.createElement('canvas');
      croppedCanvas.width = cropW;
      croppedCanvas.height = cropH;
      const cropCtx = croppedCanvas.getContext('2d');
      cropCtx.drawImage(tempCanvas, minX, minY, cropW, cropH, 0, 0, cropW, cropH);
      return croppedCanvas;
    }

    return tempCanvas;
  }

  async function cropLetterboxFromBlob(rawBlob) {
    if (!rawBlob) return null;
    return new Promise((resolve) => {
      const img = new Image();
      const url = URL.createObjectURL(rawBlob);
      img.onload = () => {
        URL.revokeObjectURL(url);
        try {
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth || img.width;
          canvas.height = img.naturalHeight || img.height;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(img, 0, 0);

          const cropped = cropLetterboxFromCanvas(canvas);
          cropped.toBlob((b) => resolve(b || rawBlob), 'image/png');
        } catch (e) {
          resolve(rawBlob);
        }
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(rawBlob);
      };
      img.src = url;
    });
  }

  function showCustomContextMenu(clientX, clientY) {
    const contextMenu = $('gbaContextMenu');
    if (!contextMenu || !state.activeGame) return;

    const menuWidth = 290;
    const menuHeight = 130;
    let x = clientX;
    let y = clientY;

    if (x + menuWidth > window.innerWidth) {
      x = window.innerWidth - menuWidth - 10;
    }
    if (y + menuHeight > window.innerHeight) {
      y = window.innerHeight - menuHeight - 10;
    }

    contextMenu.style.left = `${x}px`;
    contextMenu.style.top = `${y}px`;
    contextMenu.style.display = 'flex';
  }

  async function captureGameScreenshotAndSetCover() {
    if (!state.activeGame) return;
    const emu = getIframeEmulator();

    showToast('게임 화면을 캡처하여 최적의 커버 이미지를 생성하는 중...');

    try {
      let rawBlob = null;

      // 1. EmulatorJS Canvas 스크린샷 (source: "canvas") with 500ms timeout
      if (emu && typeof emu.screenshot === 'function') {
        try {
          rawBlob = await Promise.race([
            new Promise((resolve) => {
              emu.screenshot((b) => resolve(b), 'canvas', 'png', 1);
            }),
            new Promise((_, reject) => setTimeout(() => reject('timeout'), 500)),
          ]);
        } catch (e) {
          // fallback
        }
      }

      // 2. EmulatorJS 고수준 스크린샷 (source: "retroarch") with 500ms timeout
      if (!rawBlob && emu && typeof emu.screenshot === 'function') {
        try {
          rawBlob = await Promise.race([
            new Promise((resolve) => {
              emu.screenshot((b) => resolve(b), 'retroarch', 'png', 1);
            }),
            new Promise((_, reject) => setTimeout(() => reject('timeout'), 500)),
          ]);
        } catch (e) {
          // fallback
        }
      }

      // 3. iframe 내부의 WebGL / Canvas 직접 프레임 동기화 캡처
      if (!rawBlob) {
        let canvas = null;
        try {
          const iframe = $('gbaEmulatorIframe');
          const innerDoc = iframe ? (iframe.contentDocument || iframe.contentWindow.document) : document;
          canvas = innerDoc.querySelector('#game canvas') || innerDoc.querySelector('canvas') || document.querySelector('#ejs-game-frame canvas') || document.querySelector('canvas');
        } catch (e) {}

        if (canvas) {
          rawBlob = await new Promise((resolve) => {
            requestAnimationFrame(() => {
              try {
                canvas.toBlob((b) => resolve(b), 'image/png');
              } catch (err) {
                try {
                  const dataUrl = canvas.toDataURL('image/png');
                  const byteString = atob(dataUrl.split(',')[1]);
                  const ab = new ArrayBuffer(byteString.length);
                  const ia = new Uint8Array(ab);
                  for (let i = 0; i < byteString.length; i++) {
                    ia[i] = byteString.charCodeAt(i);
                  }
                  resolve(new Blob([ab], { type: 'image/png' }));
                } catch (err2) {
                  resolve(null);
                }
              }
            });
            setTimeout(() => resolve(null), 800);
          });
        }
      }

      // 4. RetroArch 코어 네이티브 버퍼 (gameManager.screenshot) with 500ms timeout
      if (!rawBlob && emu && emu.gameManager && typeof emu.gameManager.screenshot === 'function') {
        try {
          const rawPng = await Promise.race([
            emu.gameManager.screenshot(),
            new Promise((_, reject) => setTimeout(() => reject('timeout'), 500)),
          ]);
          if (rawPng && rawPng.byteLength > 100) {
            rawBlob = new Blob([rawPng], { type: 'image/png' });
          }
        } catch (e) {
          // fallback
        }
      }

      if (!rawBlob || rawBlob.size < 100) {
        showToast('화면 캡처 이미지를 생성할 수 없습니다. 게임 화면이 완전히 렌더링된 후 다시 시도해 주세요.', true);
        return;
      }

      // 5. 검은색 레터박스/필러박스 지능형 완전 제거 크롭
      const finalBlob = (await cropLetterboxFromBlob(rawBlob)) || rawBlob;

      const formData = new FormData();
      formData.append('file', finalBlob, `${state.activeGame.id}.png`);
      formData.append('game_id', state.activeGame.id);

      const res = await fetch(`${API_WEBHOOK}/upload`, {
        method: 'POST',
        body: formData,
      });
      const result = await res.json();
      if (result.success) {
        showToast('현재 화면이 게임 커버 이미지로 성공적으로 등록되었습니다! 📸');
        // 로컬 상태 즉시 갱신
        const gameId = state.activeGame.id;
        const targetGame = state.games.find((g) => g.id === gameId);
        if (targetGame) {
          targetGame.cover_path = `/api/webhook/${PLUGIN_ID}/cover/${gameId}`;
          targetGame.cover_url = `/api/webhook/${PLUGIN_ID}/cover/${gameId}?t=${Date.now()}`;
        }
        // 카드 즉시 재렌더링
        renderGames();
      } else {
        showToast(`커버 등록 실패: ${result.error}`, true);
      }
    } catch (err) {
      console.error('[GBA] Cover capture error:', err);
      showToast('커버 이미지 등록 중 오류가 발생했습니다.', true);
    }
  }

  // --------------------------------------------------------------------------
  // 유틸리티
  // --------------------------------------------------------------------------
  function showLoading(show) {
    const loading = $('gbaLoading');
    if (loading) loading.style.display = show ? 'flex' : 'none';
  }

  function showToast(msg, isError = false) {
    let toast = document.getElementById('gbaToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'gbaToast';
      toast.style.cssText = `
        position: fixed; bottom: 24px; right: 24px; z-index: 99999;
        padding: 12px 20px; border-radius: 8px; font-weight: 600; font-size: 0.9rem;
        box-shadow: 0 10px 25px rgba(0,0,0,0.5); transition: all 0.3s ease;
        opacity: 0; transform: translateY(10px);
      `;
      document.body.appendChild(toast);
    }
    toast.style.background = isError ? '#ef4444' : '#10b981';
    toast.style.color = '#fff';
    toast.textContent = msg;
    toast.style.opacity = '1';
    toast.style.transform = 'translateY(0)';

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
    }, 3000);
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatRelativeTime(dateStr) {
    if (!dateStr) return '플레이 기록 없음';
    try {
      // 한국 표준시 (KST, UTC+9) 기준 파싱
      let isoStr = dateStr.trim().replace(' ', 'T');
      if (!isoStr.includes('+') && !isoStr.includes('Z')) {
        isoStr += '+09:00';
      }
      const targetTime = new Date(isoStr).getTime();
      if (isNaN(targetTime)) return dateStr;

      const nowTime = Date.now();
      const diffSec = Math.floor((nowTime - targetTime) / 1000);

      // 클라이언트-서버 간 미세 시차(미래 시간 포함) 안전 처리
      if (diffSec < 60) return '방금 전';
      if (diffSec < 3600) return `${Math.max(1, Math.floor(diffSec / 60))}분 전`;
      if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}시간 전`;
      if (diffSec < 2592000) return `${Math.floor(diffSec / 86400)}일 전`;
      return dateStr.substring(0, 10);
    } catch (e) {
      return dateStr;
    }
  }

  // --------------------------------------------------------------------------
  // 시작점
  // --------------------------------------------------------------------------
  initDragAndDrop();
  bindEvents();
  loadLibrary();
})();
