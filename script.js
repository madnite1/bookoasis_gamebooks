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
    filter: 'all',
    searchQuery: '',
    userId: 1,
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
    graphics: {
      shader: localStorage.getItem('gba_shader') || 'disabled',
      pixelMode: localStorage.getItem('gba_pixel_mode') || 'pixelated',
      aspectRatio: localStorage.getItem('gba_aspect_ratio') || '3/2',
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
  async function loadLibrary() {
    showLoading(true);
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

        state.isAdmin = !!data.is_admin;
        const settingsBtn = $('gbaSettingsBtn');
        if (settingsBtn) {
          settingsBtn.style.display = state.isAdmin ? 'inline-flex' : 'none';
        }

        renderGames();
      } else {
        showToast('게임 목록을 불러오지 못했습니다: ' + (data.error || '알 수 없는 오류'), true);
      }
    } catch (err) {
      console.error('[GBA] Load library error:', err);
      showToast('서버와 통신 중 오류가 발생했습니다.', true);
    } finally {
      showLoading(false);
    }
  }

  function renderGames() {
    const grid = $('gbaGameGrid');
    const emptyState = $('gbaEmptyState');
    const countEl = $('gbaGameCount');

    // 검색 및 필터 적용
    let filtered = state.games.filter((g) => {
      // 필터
      if (state.filter === 'favorite' && !g.is_favorite) return false;
      if (state.filter === 'recent' && !g.last_played_at) return false;
      if (state.filter === 'snes' && g.core !== 'snes' && g.platform !== 'SNES') return false;
      if (state.filter === 'gba' && g.core !== 'gba' && g.platform !== 'GBA') return false;
      if (state.filter === 'nes' && g.core !== 'nes' && g.platform !== 'NES' && g.platform !== 'FDS') return false;
      if (state.filter === 'gb' && g.core !== 'gb' && g.core !== 'gbc' && g.platform !== 'GB' && g.platform !== 'GBC') return false;
      if (state.filter === 'genesis' && g.core !== 'segaMD' && g.core !== 'segaMS' && g.core !== 'segaGG' && g.platform !== 'Genesis') return false;
      if (state.filter === 'nds' && g.core !== 'nds' && g.platform !== 'NDS') return false;
      if (state.filter === 'psx' && g.core !== 'psx' && g.platform !== 'PS1') return false;

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

    countEl.textContent = `${filtered.length}개의 게임 (전체 ${state.games.length}개)`;

    if (filtered.length === 0) {
      grid.style.display = 'none';
      emptyState.style.display = 'flex';
      return;
    }

    emptyState.style.display = 'none';
    grid.style.display = 'grid';
    grid.innerHTML = '';

    filtered.forEach((game) => {
      const card = createGameCard(game);
      grid.appendChild(card);
    });
  }

  function createGameCard(game) {
    const card = document.createElement('div');
    card.className = 'gba-card';
    card.dataset.id = game.id;

    // 포맷팅
    const sizeMb = (game.size_bytes / (1024 * 1024)).toFixed(1) + ' MB';
    const lastPlayed = formatRelativeTime(game.last_played_at);
    const hasCover = !!game.cover_path;
    const platformLabel = game.platform || (game.core === 'snes' ? 'SNES' : 'GBA');

    // 커버 영역
    let coverHtml = '';
    if (hasCover) {
      coverHtml = `
        <img src="${game.cover_url}${game.cover_url.includes('?') ? '&' : '?'}t=${Date.now()}" alt="${escapeHtml(game.title)}" class="gba-card-cover" loading="lazy" onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';">
        <div class="gba-card-default-cover" style="display: none;">
          <i class="fa-solid fa-gamepad"></i>
          <span>${escapeHtml(platformLabel)}</span>
        </div>
      `;
    } else {
      coverHtml = `
        <div class="gba-card-default-cover">
          <i class="fa-solid fa-gamepad"></i>
          <span>${escapeHtml(platformLabel)}</span>
        </div>
      `;
    }

    card.innerHTML = `
      <div class="gba-card-cover-wrap" data-action="play">
        ${coverHtml}
        <div class="gba-card-overlay">
          <button class="gba-play-overlay-btn" title="게임 실행">
            <i class="fa-solid fa-play"></i>
          </button>
        </div>
        <div class="gba-card-badges">
          <span class="gba-badge">${escapeHtml(platformLabel)}</span>
          ${game.has_save ? `<span class="gba-badge gba-badge-save" title="클라우드 세이브 보관됨"><i class="fa-solid fa-floppy-disk"></i> SAVE</span>` : ''}
        </div>
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
          <button class="gba-card-icon-btn" data-action="set-cover" title="커버 이미지 등록"><i class="fa-regular fa-image"></i></button>
          <button class="gba-card-icon-btn gba-btn-danger" data-action="delete" title="게임 삭제"><i class="fa-regular fa-trash-can"></i></button>
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
  // 에뮬레이터 실행 & 플레이어 관리 (커스텀 툴바 연동)
  // --------------------------------------------------------------------------
  async function launchGame(game) {
    state.activeGame = game;
    state.isPaused = false;
    state.currentSpeed = 1;
    state.isMuted = false;

    if ($('gbaCurrentGameTitle')) {
      $('gbaCurrentGameTitle').textContent = game.title;
    }
    if ($('gbaPlayerBadge')) {
      $('gbaPlayerBadge').textContent = game.platform || (game.core === 'snes' ? 'SNES' : 'GBA');
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

    // EmulatorJS 컨테이너 엘리먼트 생성
    const emuDiv = document.createElement('div');
    emuDiv.id = 'ejs-game-frame';
    emuDiv.style.width = '100%';
    emuDiv.style.height = '100%';
    container.appendChild(emuDiv);

    // EmulatorJS 설정 (기본 하단 툴바는 모두 비활성화하고 자체 툴바에 연동)
    window.EJS_player = '#ejs-game-frame';
    window.EJS_core = game.core || 'gba';
    window.EJS_gameName = game.title;
    window.EJS_gameUrl = window.location.origin + game.rom_url;
    window.EJS_pathtodata = 'https://cdn.emulatorjs.org/stable/data/';
    window.EJS_startOnLoaded = true;
    window.EJS_color = '#6366f1';
    window.EJS_alignStartButton = 'center';
    window.EJS_gamepad = true;
    window.EJS_mouse = false;
    window.EJS_pointerLock = false;
    window.EJS_hideSettings = true;

    // EmulatorJS 기본 하단 메뉴 버튼 전체 비활성화
    window.EJS_Buttons = {
      playPause: false,
      restart: false,
      mute: false,
      settings: false,
      fullscreen: false,
      saveState: false,
      loadState: false,
      screenRecord: false,
      gamepad: false,
      cheat: false,
      volume: false,
      saveSavFiles: false,
      loadSavFiles: false,
      quickSave: false,
      quickLoad: false,
      screenshot: false,
      cacheManager: false,
      contextMenu: false,
      disks: false,
      netplay: false,
    };

    window.EJS_onGameStart = () => {
      const emu = window.EJS_emulator;
      if (emu && emu.elements) {
        if (emu.elements.menu) emu.elements.menu.style.display = 'none';
        if (emu.elements.menuToggle) emu.elements.menuToggle.style.display = 'none';
        if (emu.elements.contextmenu) emu.elements.contextmenu.style.display = 'none';
      }
      document.querySelectorAll('#ejs-game-frame .ejs_menu_bar, #ejs-game-frame [class*="ejs_menu"]').forEach((el) => {
        el.style.display = 'none';
      });
      setTimeout(() => {
        applyGraphicsSettings();
      }, 200);
    };

    // 저장된 실시간 스냅샷이 있다면 로드 설정 (그 화면 그대로 즉시 이어하기)
    if (game.has_state) {
      window.EJS_loadStateURL = window.location.origin + game.state_url;
    }

    // EmulatorJS 스크립트 로드
    const script = document.createElement('script');
    script.src = 'https://cdn.emulatorjs.org/stable/data/loader.js';
    script.onload = () => {
      setSaveStatus('클라우드 세이브 준비됨', 'ready');
      setupAutoSave(game);
      startGamepadPoller();
      setTimeout(() => {
        applyGraphicsSettings();
      }, 500);
    };
    script.onerror = () => {
      setSaveStatus('에뮬레이터 코어 로드 실패', 'saving');
      showToast('에뮬레이터 코어를 불러오지 못했습니다. 네트워크를 확인해주세요.', true);
    };
    document.body.appendChild(script);
  }

  function applyGraphicsSettings() {
    const emu = window.EJS_emulator;
    if (emu && typeof emu.enableShader === 'function') {
      try {
        emu.enableShader(state.graphics.shader || 'disabled');
      } catch (err) {
        console.warn('[GBA] enableShader error:', err);
      }
    }

    const canvas = document.querySelector('#ejs-game-frame canvas') || document.querySelector('canvas');
    if (canvas) {
      canvas.style.imageRendering = state.graphics.pixelMode || 'pixelated';
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
    const emu = window.EJS_emulator;
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
    const emu = window.EJS_emulator;
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
    const emu = window.EJS_emulator;
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
    const emu = window.EJS_emulator;
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
    const emu = window.EJS_emulator;

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
        // 볼륨 0으로 즉시 차단
        if (typeof emu.setVolume === 'function') {
          emu.setVolume(0);
        }
        // 에뮬레이션 루프 중지
        if (emu.gameManager && typeof emu.gameManager.toggleMainLoop === 'function') {
          emu.gameManager.toggleMainLoop(0);
        }
        // WebAssembly 모듈 및 OpenAL 오디오 컨텍스트 닫기
        if (emu.Module) {
          if (typeof emu.Module.pauseMainLoop === 'function') {
            emu.Module.pauseMainLoop();
          }
          if (emu.Module.AL && emu.Module.AL.currentCtx) {
            try {
              emu.Module.AL.currentCtx.suspend();
              emu.Module.AL.currentCtx.close();
            } catch (err) {}
          }
          if (typeof emu.Module.abort === 'function') {
            try {
              emu.Module.abort();
            } catch (err) {}
          }
        }
      } catch (e) {
        console.warn('[GBA] Emulator shutdown error:', e);
      }
      window.EJS_emulator = null;
    }

    // 3. 타이머 및 패드 폴러 정지
    if (state.autoSaveIntervalId) {
      clearInterval(state.autoSaveIntervalId);
      state.autoSaveIntervalId = null;
    }
    stopGamepadPoller();

    // 4. DOM 컨테이너 비우기 및 플레이어 모달 닫기
    const container = $('gbaEmulatorContainer');
    if (container) {
      container.innerHTML = '';
    }
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

    const canvas = document.querySelector('#ejs-game-frame canvas') || document.querySelector('canvas');
    if (canvas) {
      canvas.dispatchEvent(event);
    }
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
  // 파일 업로드 (ROM & 커버 아트)
  // --------------------------------------------------------------------------
  async function handleFileUpload(files) {
    if (!files || files.length === 0) return;

    $('gbaUploadModal').style.display = 'flex';
    const statusEl = $('gbaUploadStatus');
    const progEl = $('gbaProgressBar');
    const detailsEl = $('gbaUploadDetails');

    let completed = 0;
    const total = files.length;

    for (let i = 0; i < total; i++) {
      const file = files[i];
      statusEl.textContent = `'${file.name}' 업로드 중...`;
      progEl.style.width = `${Math.round((i / total) * 100)}%`;
      detailsEl.textContent = `${i} / ${total} 파일 완료`;

      const formData = new FormData();
      formData.append('file', file);
      if (state.targetGameForCover) {
        formData.append('game_id', state.targetGameForCover);
      }

      try {
        const res = await fetch(`${API_WEBHOOK}/upload`, {
          method: 'POST',
          body: formData,
        });
        const result = await res.json();
        if (result.success) {
          completed++;
        } else {
          showToast(`업로드 실패 (${file.name}): ${result.error}`, true);
        }
      } catch (err) {
        console.error('[GBA] Upload error:', err);
        showToast(`업로드 에러 (${file.name})`, true);
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
    state.targetGameForCover = gameId;
    $('gbaCoverInput').click();
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
      dragCounter++;
      dropZone.classList.add('active');
    });

    window.addEventListener('dragleave', (e) => {
      e.preventDefault();
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
      name: "패미컴 (FC / NES / GB)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A 버튼", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "B 버튼", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "터보(연사) A / B", key: "S / A", pad: "X / Y" },
        { action: "START / SELECT", key: "Enter / Shift", pad: "Start / Back" },
      ],
    },
    genesis: {
      name: "메가드라이브 (MD / Genesis)",
      rows: [
        { action: "방향키 (D-Pad)", key: "↑ ↓ ← →", pad: "십자키 / 왼쪽 스틱" },
        { action: "A 버튼", key: "A", pad: "Y (Xbox) / △ (PS) / X (스위치)" },
        { action: "B 버튼", key: "Z", pad: "B (Xbox) / ○ (PS) / A (스위치)" },
        { action: "C 버튼", key: "X", pad: "A (Xbox) / ✕ (PS) / B (스위치)" },
        { action: "X / Y / Z (6버튼)", key: "Q / S / W", pad: "LB / X / RB" },
        { action: "START", key: "Enter", pad: "Start / Menu" },
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

    // 필터 탭 클릭
    document.querySelectorAll('.gba-tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.gba-tab-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        state.filter = btn.dataset.filter || 'all';
        renderGames();
      });
    });

    // 상단 툴바 버튼
    $('gbaUploadBtn').addEventListener('click', () => $('gbaFileInput').click());
    $('gbaEmptyUploadBtn')?.addEventListener('click', () => $('gbaFileInput').click());
    $('gbaScanBtn').addEventListener('click', () => {
      showToast('ROM 폴더를 다시 스캔합니다...');
      loadLibrary();
    });

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
        const core = state.activeGame.core || '';
        if (core === 'gba') targetSys = 'gba';
        else if (core === 'nes' || core === 'gb' || core === 'gbc') targetSys = 'nes';
        else if (core === 'segaMD' || core === 'segaMS' || core === 'segaGG') targetSys = 'genesis';
        else if (core === 'psx' || core === 'psp') targetSys = 'psx';
        else if (core === 'nds') targetSys = 'nds';
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
    $('gbaFileInput').addEventListener('change', (e) => handleFileUpload(e.target.files));
    $('gbaCoverInput').addEventListener('change', (e) => handleFileUpload(e.target.files));

    // 업로드 모달 닫기
    $('gbaUploadCloseBtn').addEventListener('click', () => {
      $('gbaUploadModal').style.display = 'none';
    });

    // 설정 모달 열기 & 저장
    $('gbaSettingsBtn').addEventListener('click', () => {
      $('gbaSettingCloudSave').checked = state.config.cloud_save_enabled;
      $('gbaSettingInterval').value = state.config.auto_save_interval_sec;
      $('gbaSettingExtraPath').value = state.config.extra_roms_path || '';
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
      const cloudSave = $('gbaSettingCloudSave').checked ? '1' : '0';
      const interval = $('gbaSettingInterval').value.trim();

      try {
        const res = await apiCall('save_settings', {
          extra_roms_path: extraPath,
          cloud_save_enabled: cloudSave,
          auto_save_interval_sec: interval,
        });
        if (res.success) {
          state.config.extra_roms_path = extraPath;
          state.config.cloud_save_enabled = cloudSave === '1';
          state.config.auto_save_interval_sec = parseInt(interval, 10) || 60;
          $('gbaSettingsModal').style.display = 'none';
          showToast('설정이 저장되었습니다.');
          loadLibrary();
        }
      } catch (e) {
        showToast('설정 저장 중 오류가 발생했습니다.', true);
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
            'gbaGraphicsModal',
            'gbaControlsModal',
            'gbaUploadModal',
            'gbaSettingsModal',
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

          // 5. 상위에 열린 모달이 전혀 없을 때만 게임 플레이어 종료
          if (state.activeGame) {
            e.preventDefault();
            e.stopImmediatePropagation();
            lastEscTimestamp = now;
            exitGame();
          }
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
      return a < 50 || (r <= 15 && g <= 15 && b <= 15);
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

  async function captureGameScreenshotAndSetCover() {
    if (!state.activeGame) return;
    const emu = window.EJS_emulator;
    if (!emu || !emu.gameManager) {
      showToast('에뮬레이터 코어가 로딩 중입니다. 잠시 후 다시 시도해 주세요.', true);
      return;
    }

    showToast('게임 화면을 캡처하여 최적의 커버 이미지를 생성하는 중...');

    try {
      let blob = null;

      // 1. RetroArch 코어 네이티브 렌더러 스크린샷 (레터박스 없는 무손실 순수 픽셀 완벽 추출)
      if (typeof emu.gameManager.screenshot === 'function') {
        try {
          const rawPng = await emu.gameManager.screenshot();
          if (rawPng && rawPng.byteLength > 100) {
            blob = new Blob([rawPng], { type: 'image/png' });
          }
        } catch (err) {
          console.warn('[GBA] Native screenshot error:', err);
        }
      }

      // 2. 만약 네이티브 캡처 실패 시 WebGL 캔버스 캡처 + 레터박스 자동 크롭 폴백
      if (!blob) {
        const canvas = document.querySelector('#ejs-game-frame canvas') || document.querySelector('canvas');
        if (canvas) {
          const croppedCanvas = cropLetterboxFromCanvas(canvas);
          blob = await new Promise((res) => croppedCanvas.toBlob(res, 'image/png'));
        }
      }

      if (!blob || blob.size < 100) {
        showToast('화면 캡처 이미지를 생성할 수 없습니다.', true);
        return;
      }

      const formData = new FormData();
      formData.append('file', blob, `${state.activeGame.id}.png`);
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
