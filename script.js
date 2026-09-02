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
    statusFilter: 'all',
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
    runtimeGlobalsLoaded: false,
    runtimeGlobalsPromise: null,
    biosPage: 1,
    biosPageSize: 10,
    biosSearch: '',
    biosFilter: 'all',
    launchProgress: {
      visible: false,
      phase: 'idle',
      title: '',
      desc: '',
      meta: '',
      percent: null,
    },
    launchPlan: null,
    analysisDetailGameId: null,
    analysisDetailData: null,
    pageSize: 40,
    totalCount: 0,
    libraryTotalCount: 0,
    pendingDeleteCount: 0,
    nextOffset: 0,
    hasMore: false,
    isLoadingMore: false,
    libraryRequestSeq: 0,
    scrollObserver: null,
    coverQueuePollTimer: null,
    coverQueueSeenActive: false,
    coverVariantPollTimer: null,
    phase6Preflight: null,
    analysisProgressMonitorStart: null,
    coverQueue: {
      is_running: false,
      total: 0,
      completed: 0,
      failed: 0,
      remaining: 0,
      current_title: '',
    },
  };

  let launchSlowTimerId = null;

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
    let data;
    try {
      data = await res.json();
    } catch (err) {
      data = null;
    }
    if (!res.ok) {
      const errMsg = (data && (data.error || data.message)) ? (data.error || data.message) : `HTTP ${res.status}`;
      const errorObj = new Error(errMsg);
      errorObj.response = data;
      errorObj.status = res.status;
      throw errorObj;
    }
    return data;
  }

  // --------------------------------------------------------------------------
  // 데이터 로드 & 렌더링
  // --------------------------------------------------------------------------
  function libraryQueryParams(offset = 0) {
    return {
      offset,
      limit: state.pageSize,
      category: state.category,
      sort: state.sort,
      favorite_only: state.isFavoriteOnly ? 1 : 0,
      status: state.statusFilter,
      q: state.searchQuery,
    };
  }

  function refreshRuntimeCards(gameIds) {
    const ids = new Set((gameIds || []).map((id) => String(id)));
    if (!ids.size) return;
    document.querySelectorAll('.gba-card[data-id]').forEach((card) => {
      const id = String(card.dataset.id || '');
      if (!ids.has(id)) return;
      const game = state.games.find((item) => String(item.id) === id);
      if (game) card.replaceWith(createGameCard(game));
    });
  }

  async function loadRuntimeState(games = [], includeGlobals = false) {
    const gameIds = [...new Set((games || [])
      .map((game) => String(game && game.id ? game.id : '').trim())
      .filter(Boolean))];
    const data = await apiCall('runtime_state', {
      game_ids: gameIds.join(','),
      include_globals: includeGlobals ? 1 : 0,
    });
    if (!data || !data.success) {
      throw new Error((data && data.error) || '실행 상태 조회 실패');
    }

    const gameStates = data.game_states || {};
    const changedIds = [];
    state.games.forEach((game) => {
      const id = String(game.id);
      if (!Object.prototype.hasOwnProperty.call(gameStates, id)) return;
      const runtime = gameStates[id] || {};
      game.has_save = Number(runtime.has_save || 0);
      game.has_state = Number(runtime.has_state || 0);
      game.runtime_state_loaded = true;
      changedIds.push(id);
    });

    if (includeGlobals) {
      if (Array.isArray(data.available_bios)) state.available_bios = data.available_bios;
      if (data.config) state.config = Object.assign(state.config, data.config);
      state.runtimeGlobalsLoaded = true;
    }

    const refreshIds = includeGlobals
      ? state.games.map((game) => String(game.id))
      : changedIds;
    refreshRuntimeCards(refreshIds);
    if (includeGlobals && $('gbaBiosModal') && $('gbaBiosModal').style.display === 'flex') {
      renderBiosModal();
    }
    return data;
  }

  function startRuntimeGlobalsLoad(games = []) {
    if (state.runtimeGlobalsLoaded) return Promise.resolve();
    if (state.runtimeGlobalsPromise) return state.runtimeGlobalsPromise;

    let task = null;
    task = loadRuntimeState(games, true)
      .catch((err) => {
        console.warn('[GBA] Runtime globals load error:', err);
        throw err;
      })
      .finally(() => {
        if (state.runtimeGlobalsPromise === task) state.runtimeGlobalsPromise = null;
      });
    state.runtimeGlobalsPromise = task;
    return task;
  }

  function queueRuntimeStateRefresh(games = [], includeGlobals = false) {
    const targets = (games || []).filter((game) => game && !game.runtime_state_loaded);
    if (includeGlobals && !state.runtimeGlobalsLoaded && !state.runtimeGlobalsPromise) {
      startRuntimeGlobalsLoad(targets).catch(() => {});
      return;
    }
    if (targets.length) {
      loadRuntimeState(targets, false).catch((err) => {
        console.warn('[GBA] Runtime save state load error:', err);
      });
    }
  }

  async function ensureRuntimeState(game) {
    if (!game) return;
    if (!state.runtimeGlobalsLoaded && state.runtimeGlobalsPromise) {
      try {
        await state.runtimeGlobalsPromise;
      } catch (e) {}
    }
    if (!state.runtimeGlobalsLoaded) {
      await startRuntimeGlobalsLoad([game]);
    } else if (!game.runtime_state_loaded) {
      await loadRuntimeState([game], false);
    }
    if (!game.runtime_state_loaded) {
      await loadRuntimeState([game], false);
    }
  }

  async function ensureRuntimeGlobals() {
    if (state.runtimeGlobalsLoaded) return;
    await startRuntimeGlobalsLoad([]);
  }

  async function refreshRuntimeGlobals(games = state.games) {
    if (state.runtimeGlobalsPromise) {
      try {
        await state.runtimeGlobalsPromise;
      } catch (e) {}
    }
    state.runtimeGlobalsLoaded = false;
    return startRuntimeGlobalsLoad(games || []);
  }

  async function loadLibrary(silent = false) {
    const requestSeq = ++state.libraryRequestSeq;
    state.isLoadingMore = false;
    if (!silent) showLoading(true);
    try {
      const data = await apiCall('list_games', libraryQueryParams(0));
      if (requestSeq !== state.libraryRequestSeq) return;
      if (data.success) {
        state.games = data.games || [];
        state.totalCount = Number(data.total_count || 0);
        state.libraryTotalCount = Number(data.library_total_count ?? state.totalCount);
        state.pendingDeleteCount = Number(data.pending_delete_count || 0);
        updateDeleteQueueCount();
        state.nextOffset = Number(data.next_offset ?? state.games.length);
        state.hasMore = !!data.has_more;
        if (data.user_id) state.userId = data.user_id;
        state.isAdmin = !!data.is_admin;

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

        // 첫 화면은 list_games의 DB 응답만으로 즉시 렌더한다. 세이브/BIOS처럼
        // 파일시스템을 확인해야 하는 상태는 렌더 이후 비동기로 보정한다.
        renderGames(true);
        startCoverQueueMonitor();
        if (state.isAdmin && typeof state.analysisProgressMonitorStart === 'function') {
          state.analysisProgressMonitorStart();
        }
      } else {
        showToast('게임 목록을 불러오지 못했습니다: ' + (data.error || '알 수 없는 오류'), true);
      }
    } catch (err) {
      if (requestSeq !== state.libraryRequestSeq) return;
      console.error('[GBA] Load library error:', err);
      showToast('서버와 통신 중 오류가 발생했습니다.', true);
    } finally {
      if (requestSeq === state.libraryRequestSeq && !silent) showLoading(false);
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
          const wasQueueActive = !!state.coverQueueSeenActive;
          const isQueueActive = !!(q.is_running || (q.remaining && q.remaining > 0));
          const didQueueJustFinish = wasQueueActive && !q.is_running && q.remaining === 0 && q.total > 0;

          state.coverQueue = q;
          state.coverQueueSeenActive = isQueueActive;
          updateCoverQueueBadge(q);

          // 큐가 활성 상태였다가 방금 완료된 시점에만 1회 갱신
          if (didQueueJustFinish) {
            loadLibrary(true);
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
    if (!grid) return;

    if (resetPaging) grid.innerHTML = '';

    if (countEl) {
      countEl.textContent = `${state.totalCount}개의 게임 (전체 ${state.libraryTotalCount}개)`;
    }

    if (state.totalCount === 0) {
      grid.style.display = 'none';
      if (sentinel) sentinel.style.display = 'none';
      if (emptyState) emptyState.style.display = 'flex';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    grid.style.display = 'grid';

    const currentLength = resetPaging ? 0 : grid.children.length;
    const targetSlice = state.games.slice(currentLength);
    const fragment = document.createDocumentFragment();
    targetSlice.forEach((game) => fragment.appendChild(createGameCard(game)));
    grid.appendChild(fragment);

    if (sentinel) {
      sentinel.style.display = state.hasMore ? 'block' : 'none';
      if (state.hasMore) initScrollObserver();
    }
  }

  async function loadMoreGames() {
    if (state.isLoadingMore || !state.hasMore) return;
    state.isLoadingMore = true;
    const requestSeq = state.libraryRequestSeq;
    const offset = state.nextOffset;
    try {
      const data = await apiCall('list_games', libraryQueryParams(offset));
      if (requestSeq !== state.libraryRequestSeq || !data.success) return;
      const incoming = data.games || [];
      const known = new Set(state.games.map((g) => g.id));
      incoming.forEach((game) => {
        if (!known.has(game.id)) {
          state.games.push(game);
          known.add(game.id);
        }
      });
      state.totalCount = Number(data.total_count ?? state.totalCount);
      state.libraryTotalCount = Number(data.library_total_count ?? state.libraryTotalCount);
      state.nextOffset = Number(data.next_offset ?? (offset + incoming.length));
      state.hasMore = !!data.has_more;
      renderGames(false);
    } catch (err) {
      if (requestSeq === state.libraryRequestSeq) {
        console.error('[GBA] Load more games error:', err);
      }
    } finally {
      if (requestSeq === state.libraryRequestSeq) state.isLoadingMore = false;
    }
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

  function metadataConfidenceLabel(score) {
    const n = Number(score || 0);
    if (n >= 85) return '높음';
    if (n >= 60) return '중간';
    if (n > 0) return '낮음';
    return '';
  }

  function getPlayStatusInfo(status) {
    const value = String(status || 'untested').toLowerCase();
    const map = {
      booted: { label: '부팅 확인', icon: 'fa-power-off', className: 'booted', desc: 'EmulatorJS의 실제 게임 시작 이벤트가 확인되었습니다.' },
      verified: { label: '플레이 확인', icon: 'fa-circle-check', className: 'verified', desc: '사용자가 실제 플레이 가능함을 확인했습니다.' },
      issue: { label: '실행 문제', icon: 'fa-triangle-exclamation', className: 'issue', desc: '사용자가 실제 플레이 중 문제가 있음을 확인했습니다.' },
      untested: { label: '플레이 미확인', icon: 'fa-circle-question', className: 'untested', desc: '아직 실제 부팅 또는 플레이 결과가 확인되지 않았습니다.' },
    };
    return map[value] || map.untested;
  }

  function playStatusBadgeHtml(game) {
    const status = String(game.play_status || 'untested').toLowerCase();
    if (status === 'untested') return '';
    const info = getPlayStatusInfo(status);
    return `<span class="gba-badge gba-play-badge gba-play-${info.className}" title="${escapeHtml(info.desc)}"><i class="fa-solid ${info.icon}"></i> ${escapeHtml(info.label)}</span>`;
  }

  function applyPlayStateToGame(gameId, play) {
    if (!play) return;
    const game = state.games.find((item) => String(item.id) === String(gameId));
    if (!game) return;
    game.play_status = play.status || 'untested';
    game.play_status_stale = play.stale ? 1 : 0;
    game.play_status_updated_at = play.updated_at || '';
    game.last_booted_at = play.last_booted_at || game.last_booted_at || '';
    refreshRuntimeCards([gameId]);
  }

  function analysisText(value, fallback = '—') {
    const text = String(value ?? '').trim();
    return escapeHtml(text || fallback);
  }

  function analysisList(items) {
    const values = Array.isArray(items) ? items.filter((v) => String(v || '').trim()) : [];
    if (!values.length) return '<span class="gba-analysis-empty">없음</span>';
    return `<div class="gba-analysis-chip-list">${values.map((v) => `<span class="gba-analysis-chip">${escapeHtml(String(v))}</span>`).join('')}</div>`;
  }

  function analysisRow(label, value, options = {}) {
    const content = options.html ? value : analysisText(value);
    return `<div class="gba-analysis-row"><span>${escapeHtml(label)}</span><strong class="${options.mono ? 'mono' : ''}">${content}</strong></div>`;
  }

  function healthStatusInfo(status) {
    const value = String(status || 'unverified').toLowerCase();
    const map = {
      pass: ['진단 통과', 'good'],
      missing_file: ['파일 없음', 'bad'],
      path_mismatch: ['경로 불일치', 'warn'],
      bios_required: ['BIOS 필요', 'warn'],
      chd_required: ['CHD 필요', 'bad'],
      incomplete: ['참조 파일 누락', 'warn'],
      unsupported: ['코어 미지원', 'bad'],
      reclassify_required: ['재분류 필요', 'warn'],
      unverified: ['판정 미확인', 'neutral'],
    };
    const item = map[value] || map.unverified;
    return { label: item[0], className: item[1] };
  }

  async function setRomPlayStatus(gameId, status) {
    try {
      const res = await apiCall('set_play_status', { game_id: gameId, status });
      if (!res || !res.success) throw new Error((res && res.error) || '플레이 상태를 저장하지 못했습니다.');
      applyPlayStateToGame(gameId, res.play);
      if (state.analysisDetailData && String(state.analysisDetailGameId) === String(gameId)) {
        state.analysisDetailData.play = res.play;
        renderRomAnalysisDetail(state.analysisDetailData);
      }
      showToast(status === 'verified' ? '플레이 가능 상태로 확인했습니다.' : status === 'issue' ? '실행 문제 상태로 기록했습니다.' : '플레이 확인 상태를 초기화했습니다.');
    } catch (err) {
      showToast(err && err.message ? err.message : '플레이 상태 저장에 실패했습니다.', true);
    }
  }

  function closeRomAnalysis() {
    const modal = $('gbaAnalysisModal');
    if (modal) modal.style.display = 'none';
    state.analysisDetailGameId = null;
    state.analysisDetailData = null;
  }

  function renderRomAnalysisDetail(data) {
    const content = $('gbaAnalysisContent');
    const loading = $('gbaAnalysisLoading');
    const errorEl = $('gbaAnalysisError');
    if (!content) return;
    if (loading) loading.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';
    content.style.display = 'block';

    const health = data.health || {};
    const healthInfo = healthStatusInfo(health.status);
    const play = data.play || { status: 'untested' };
    const playInfo = getPlayStatusInfo(play.status);
    const analysis = data.analysis || {};
    const identity = data.identity || {};
    const file = data.file || {};
    const hashes = data.hashes || {};
    const bios = data.bios || {};
    const confidence = Number(analysis.metadata_confidence ?? identity.metadata_confidence ?? 0);
    const emulatorSupported = !!analysis.emulatorjs_supported;
    const analyzerPlayable = !!analysis.is_playable;
    const analysisUpdatedAt = health.updated_at || analysis.analysis_updated_at || '';
    const cacheTextBase = data.analysis_cache_reused
      ? '저장된 ROM 분석 사용'
      : (data.analysis_error ? '기존 ROM 분석 표시' : '이번 조회에서 ROM 분석 갱신');
    const cacheText = analysisUpdatedAt ? `${cacheTextBase} · ${analysisUpdatedAt}` : cacheTextBase;

    const warnings = Array.isArray(analysis.analysis_warnings) ? analysis.analysis_warnings : [];
    const conflicts = Array.isArray(analysis.analysis_conflicts) ? analysis.analysis_conflicts : [];
    const missingDisks = Array.isArray(analysis.disk_missing_files) ? analysis.disk_missing_files : [];
    const resolvedDisks = Array.isArray(analysis.resolved_disk_files) ? analysis.resolved_disk_files : [];

    content.innerHTML = `
      <div class="gba-analysis-heading">
        <div>
          <h4>${analysisText(data.title)}</h4>
          <p>${analysisText(file.name)}</p>
        </div>
        <span class="gba-analysis-cache-note">${escapeHtml(cacheText)}</span>
      </div>

      ${data.analysis_error ? `<div class="gba-analysis-notice warn"><i class="fa-solid fa-triangle-exclamation"></i><span>새 상세 분석에 실패해 저장된 정보를 표시합니다. ${escapeHtml(data.analysis_error)}</span></div>` : ''}
      ${play.stale ? `<div class="gba-analysis-notice warn"><i class="fa-solid fa-clock-rotate-left"></i><span>ROM 또는 진단 기준이 변경되어 이전 플레이 확인은 만료되었습니다.</span></div>` : ''}

      <div class="gba-analysis-summary-grid">
        <div class="gba-analysis-summary ${healthInfo.className}"><span>정적 진단</span><strong>${escapeHtml(healthInfo.label)}</strong></div>
        <div class="gba-analysis-summary play-${playInfo.className}"><span>실제 실행</span><strong><i class="fa-solid ${playInfo.icon}"></i> ${escapeHtml(playInfo.label)}</strong></div>
        <div class="gba-analysis-summary"><span>분석 신뢰도</span><strong>${confidence > 0 ? `${confidence}%` : '미확인'}</strong></div>
        <div class="gba-analysis-summary ${emulatorSupported ? 'good' : 'neutral'}"><span>EmulatorJS</span><strong>${emulatorSupported ? '지원 판정' : '지원 미확인'}</strong></div>
      </div>

      <section class="gba-analysis-section gba-play-verify-panel">
        <div class="gba-analysis-section-title"><i class="fa-solid fa-gamepad"></i><span>실제 플레이 확인</span></div>
        <p>${escapeHtml(playInfo.desc)}${play.last_booted_at ? ` · 마지막 부팅 확인 ${escapeHtml(play.last_booted_at)}` : ''}</p>
        <div class="gba-analysis-play-actions">
          <button class="gba-btn gba-btn-secondary ${play.status === 'verified' ? 'active' : ''}" data-play-status="verified"><i class="fa-solid fa-circle-check"></i> 실제 플레이 가능</button>
          <button class="gba-btn gba-btn-secondary ${play.status === 'issue' ? 'active' : ''}" data-play-status="issue"><i class="fa-solid fa-triangle-exclamation"></i> 플레이 문제 있음</button>
          <button class="gba-btn gba-btn-secondary" data-play-status="untested"><i class="fa-solid fa-rotate-left"></i> 확인 초기화</button>
        </div>
      </section>

      <div class="gba-analysis-columns">
        <section class="gba-analysis-section">
          <div class="gba-analysis-section-title"><i class="fa-solid fa-fingerprint"></i><span>ROM 식별</span></div>
          ${analysisRow('플랫폼', analysis.platform || identity.platform)}
          ${analysisRow('코어', analysis.core || identity.core)}
          ${analysisRow('게임 코드', analysis.game_code || identity.game_code, { mono: true })}
          ${analysisRow('시리얼', analysis.serial_code || identity.serial_code, { mono: true })}
          ${analysisRow('정규화 제목', identity.normalized_title)}
          ${analysisRow('지역 / 리비전', [identity.region_tag, identity.revision_tag].filter(Boolean).join(' · '))}
          ${analysisRow('판정 근거', analysis.source_system || identity.source_system)}
          ${analysisRow('분석 출처', analysis.metadata_source || identity.metadata_source)}
          ${analysisRow('Identity 상태', analysis.identity_status)}
        </section>

        <section class="gba-analysis-section">
          <div class="gba-analysis-section-title"><i class="fa-solid fa-file-shield"></i><span>파일 / 해시</span></div>
          ${analysisRow('파일 존재', file.exists ? '확인됨' : '찾을 수 없음')}
          ${analysisRow('파일 크기', formatBytes(Number(file.size_bytes || 0)))}
          ${analysisRow('상대 경로', file.relative_path || file.name, { mono: true })}
          ${file.server_path ? analysisRow('서버 경로', file.server_path, { mono: true }) : ''}
          ${analysisRow('CRC32', hashes.crc32, { mono: true })}
          ${analysisRow('MD5', hashes.md5, { mono: true })}
          ${analysisRow('SHA1', hashes.sha1, { mono: true })}
        </section>
      </div>

      <div class="gba-analysis-columns">
        <section class="gba-analysis-section">
          <div class="gba-analysis-section-title"><i class="fa-solid fa-microchip"></i><span>BIOS / 디스크 구성</span></div>
          ${analysisRow('필요 BIOS', analysis.needed_bios || bios.name)}
          ${analysisRow('BIOS 상태', (analysis.needed_bios || bios.name) ? (bios.available ? '파일 확인됨' : '파일 없음') : '불필요 / 미확인')}
          ${analysisRow('BIOS 필수 판정', analysis.bios_mandatory ? '필수' : (analysis.bios_needed ? '필요 가능성 있음' : '아님'))}
          ${analysisRow('Parent ROM', analysis.parent_hint)}
          ${analysisRow('필수 CHD', analysis.required_chd)}
          ${analysisRow('디스크 / 트랙 수', Number(analysis.disc_count || 0) || '—')}
          ${analysisRow('확인된 참조 파일', analysisList(resolvedDisks), { html: true })}
          ${analysisRow('누락 참조 파일', analysisList(missingDisks), { html: true })}
        </section>

        <section class="gba-analysis-section">
          <div class="gba-analysis-section-title"><i class="fa-solid fa-chart-simple"></i><span>rom-analyzer 판정</span></div>
          ${analysisRow('분석상 플레이 가능', analyzerPlayable ? '가능 판정' : '보장하지 않음')}
          ${analysisRow('분석 신뢰도', confidence > 0 ? `${confidence}%` : '—')}
          ${analysisRow('Arcade 매칭', Number(analysis.total_roms || 0) > 0 ? `${Number(analysis.matched_count || 0)} / ${Number(analysis.total_roms || 0)} (${Number(analysis.match_rate || 0).toFixed(1)}%)` : '해당 없음')}
          ${analysisRow('분석 방법', analysisList(analysis.analysis_methods), { html: true })}
          ${health.reason ? `<div class="gba-analysis-reason"><strong>진단 사유</strong><span>${escapeHtml(health.reason)}</span></div>` : ''}
        </section>
      </div>

      <section class="gba-analysis-section">
        <div class="gba-analysis-section-title"><i class="fa-solid fa-display"></i><span>EmulatorJS 호환성</span></div>
        <div class="gba-analysis-inline-grid">
          ${analysisRow('지원 판정', emulatorSupported ? '지원' : '미지원 / 미확인')}
          ${analysisRow('권장 코어', analysis.emulatorjs_core)}
          ${analysisRow('시스템', analysis.emulatorjs_system)}
        </div>
        ${analysis.emulatorjs_reason ? `<div class="gba-analysis-reason"><strong>판정 이유</strong><span>${escapeHtml(analysis.emulatorjs_reason)}</span></div>` : ''}
      </section>

      <section class="gba-analysis-section">
        <div class="gba-analysis-section-title"><i class="fa-solid fa-triangle-exclamation"></i><span>경고 / 충돌</span></div>
        <div class="gba-analysis-warning-grid">
          <div><strong>경고</strong>${analysisList(warnings)}</div>
          <div><strong>충돌</strong>${analysisList(conflicts)}</div>
        </div>
      </section>
    `;

    content.querySelectorAll('[data-play-status]').forEach((btn) => {
      btn.onclick = () => setRomPlayStatus(data.game_id, btn.dataset.playStatus);
    });
  }

  async function showRomAnalysis(game, forceRefresh = false) {
    const modal = $('gbaAnalysisModal');
    const loading = $('gbaAnalysisLoading');
    const content = $('gbaAnalysisContent');
    const errorEl = $('gbaAnalysisError');
    if (!modal) return;
    state.analysisDetailGameId = game.id;
    state.analysisDetailData = null;
    modal.style.display = 'flex';
    if (loading) loading.style.display = 'flex';
    if (content) content.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';
    if ($('gbaAnalysisCloseBtn')) $('gbaAnalysisCloseBtn').onclick = closeRomAnalysis;
    if ($('gbaAnalysisOkBtn')) $('gbaAnalysisOkBtn').onclick = closeRomAnalysis;
    const refreshBtn = $('gbaAnalysisRefreshBtn');
    if (refreshBtn) {
      refreshBtn.disabled = !!forceRefresh;
      refreshBtn.innerHTML = forceRefresh
        ? '<i class="fa-solid fa-spinner fa-spin"></i> 다시 분석 중...'
        : '<i class="fa-solid fa-rotate"></i> 이 ROM 다시 분석';
      refreshBtn.onclick = () => showRomAnalysis({ id: game.id }, true);
    }
    try {
      const data = await apiCall('analysis_detail', { game_id: game.id, refresh: forceRefresh ? 1 : 0 });
      if (!data || !data.success) throw new Error((data && data.error) || 'ROM 분석 정보를 불러오지 못했습니다.');
      if (String(state.analysisDetailGameId) !== String(game.id)) return;
      state.analysisDetailData = data;
      applyPlayStateToGame(game.id, data.play);
      renderRomAnalysisDetail(data);
      if (forceRefresh) showToast('이 ROM의 분석 데이터를 최신 상태로 갱신했습니다.');
    } catch (err) {
      if (loading) loading.style.display = 'none';
      if (content) content.style.display = 'none';
      if (errorEl) {
        errorEl.style.display = 'flex';
        errorEl.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i><span>${escapeHtml(err && err.message ? err.message : 'ROM 분석 정보를 불러오지 못했습니다.')}</span>`;
      }
    } finally {
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '<i class="fa-solid fa-rotate"></i> 이 ROM 다시 분석';
      }
    }
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
    const metaConfidence = Number(game.metadata_confidence || 0);
    const metaConfidenceLabel = metadataConfidenceLabel(metaConfidence);

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
    const isBiosMissing = state.runtimeGlobalsLoaded && neededBios && !biosList.includes(neededBios);

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
          ${playStatusBadgeHtml(game)}
          ${game.health_status === 'missing_file' ? `<span class="gba-badge" style="background: rgba(220, 38, 38, 0.95); color: #fff; font-weight: 700;" title="DB에 등록된 ROM 파일을 찾을 수 없습니다."><i class="fa-solid fa-file-circle-xmark"></i> 파일 없음</span>` : ''}
          ${game.health_status === 'path_mismatch' ? `<span class="gba-badge" style="background: rgba(217, 119, 6, 0.95); color: #fff; font-weight: 700;" title="DB 경로와 실제 ROM 위치가 다릅니다. 라이브러리 동기화가 필요합니다."><i class="fa-solid fa-route"></i> 경로 불일치</span>` : ''}
          ${game.health_status === 'incomplete' ? `<span class="gba-badge" style="background: rgba(245, 158, 11, 0.9); color: #000; font-weight: 700;" title="M3U/CUE/GDI 등에서 참조하는 파일이 누락되었습니다."><i class="fa-solid fa-triangle-exclamation"></i> 참조 파일 누락</span>` : ''}
          ${game.health_status === 'bios_required' ? `<span class="gba-badge" style="background: rgba(249, 115, 22, 0.92); color: #fff; font-weight: 700;" title="필수 BIOS 또는 시스템 파일이 누락되었습니다."><i class="fa-solid fa-microchip"></i> BIOS 필요</span>` : ''}
          ${game.health_status === 'bad_dump_or_unknown' || game.health_status === 'unverified' ? `<span class="gba-badge" style="background: rgba(107, 114, 128, 0.95); color: #fff; font-weight: 700;" title="rom-analyzer가 충분한 근거로 판정하지 못했습니다."><i class="fa-solid fa-circle-question"></i> 판정 미확인</span>` : ''}
          ${game.health_status === 'reclassify_required' ? `<span class="gba-badge" style="background: rgba(234, 88, 12, 0.95); color: #fff; font-weight: 700;" title="현재 DB 기종과 최신 rom-analyzer 판정 기종이 다릅니다."><i class="fa-solid fa-shuffle"></i> 재분류 필요</span>` : ''}
          ${game.health_status === 'unsupported' ? `<span class="gba-badge" style="background: rgba(124, 58, 237, 0.95); color: #fff; font-weight: 700;" title="ROM/BIOS는 확인되었지만 현재 EmulatorJS Stable 코어의 게임별 호환성 제한으로 구동할 수 없습니다."><i class="fa-solid fa-ban"></i> 코어 미지원</span>` : ''}
          ${game.health_status === 'chd_required' ? `<span class="gba-badge" style="background: rgba(239, 68, 68, 0.9); color: #fff; font-weight: 700;" title="대용량 CHD 음원 디스크 이미지가 필요합니다."><i class="fa-solid fa-compact-disc"></i> CHD 필요</span>` : ''}
          ${metaConfidenceLabel ? `<span class="gba-badge" style="background: var(--gba-accent-soft); color: var(--gba-primary); border: 1px solid var(--gba-border);" title="ROM 분석 신뢰도: ${metaConfidence}"><i class="fa-solid fa-database"></i> 메타 ${escapeHtml(metaConfidenceLabel)}</span>` : ''}
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
          ${game.region_tag ? `<span title="지역 태그">${escapeHtml(game.region_tag)}</span>` : ''}
          ${game.disc_number ? `<span title="디스크 번호">Disc ${escapeHtml(String(game.disc_number))}</span>` : ''}
        </div>
        <div class="gba-card-meta gba-card-analysis-row">
          <button type="button" class="gba-analysis-icon-btn" data-action="analysis-detail" title="ROM 분석 상세 보기" aria-label="ROM 분석 상세 보기"><i class="fa-solid fa-microscope"></i></button>
          ${state.isAdmin ? `
            ${game.relative_path ? `<span title="ROM 상대경로"><i class="fa-regular fa-folder-open"></i> ${escapeHtml(game.relative_path)}</span>` : ''}
            ${game.revision_tag ? `<span title="리비전 태그">Rev: ${escapeHtml(game.revision_tag)}</span>` : ''}
            ${game.content_flags ? `<span title="콘텐츠 플래그">${escapeHtml(game.content_flags)}</span>` : ''}
          ` : ''}
        </div>
      </div>

      <div class="gba-card-footer">
        <span class="gba-card-filesize">${sizeMb}</span>
        <div class="gba-card-actions">
          <button class="gba-card-icon-btn" data-action="edit-title" title="이름 변경"><i class="fa-solid fa-pen"></i></button>
          ${state.isAdmin ? `
            <button class="gba-card-icon-btn" data-action="set-cover" title="커버 이미지 등록"><i class="fa-regular fa-image"></i></button>
            <button class="gba-card-icon-btn gba-btn-danger" data-action="delete" title="게임 삭제 예약"><i class="fa-regular fa-trash-can"></i></button>
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
        } else if (action === 'analysis-detail') {
          e.stopPropagation();
          showRomAnalysis(game);
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

  async function openBiosModal() {
    try {
      await ensureRuntimeGlobals();
    } catch (err) {
      console.warn('[GBA] BIOS runtime state load error:', err);
    }
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
    const hasNeededBios = !!game.has_needed_bios;
    const filename = (game.filename || '').toLowerCase();
    const platform = (game.platform || '').toUpperCase();
    const core = (game.core || '').toLowerCase();
    const rawStem = filename.replace(/\.(zip|7z)$/i, '').toLowerCase();

    // 서버의 rom-analyzer 기반 진단 상태를 단일 진실 공급원으로 사용한다.
    // 파일명/접미사만으로 Parent, CHD, 기판 BIOS를 추측하지 않는다.
    if (game.health_status === 'missing_file') {
      return {
        type: 'missing_file',
        needed: '등록된 ROM 파일',
        systemName: '라이브러리 파일 경로',
        title: 'ROM 파일을 찾을 수 없음',
        reason: `${escapeHtml(game.missing_roms || 'DB에 등록된 ROM 파일을 현재 경로 또는 관리 저장소에서 찾을 수 없습니다.')}`,
        notice: '파일을 다시 배치하거나 라이브러리 동기화를 실행해 등록 상태를 확인하세요.',
        btnText: '',
        hideUpload: true,
        isOptional: false,
      };
    }

    if (game.health_status === 'path_mismatch') {
      return {
        type: 'path_mismatch',
        needed: '라이브러리 경로 동기화',
        systemName: 'DB 경로와 실제 ROM 위치 불일치',
        title: 'ROM 경로 동기화 필요',
        reason: `${escapeHtml(game.missing_roms || 'DB의 ROM 경로와 실제 발견 위치가 다릅니다.')}`,
        notice: '<strong>라이브러리 동기화</strong>를 실행하면 실제 위치를 기준으로 DB 경로를 갱신할 수 있습니다.',
        btnText: '',
        hideUpload: true,
        isOptional: false,
      };
    }

    if (game.health_status === 'bios_required') {
      if (hasNeededBios) return null;
      const requiredBios = (game.needed_bios || game.missing_roms || '').trim() || '필수 BIOS';
      return {
        type: 'bios',
        needed: requiredBios,
        systemName: '시스템 BIOS / 기판 파일',
        title: '필수 BIOS 파일 필요',
        reason: `rom-analyzer가 이 게임의 필수 BIOS로 <code>${escapeHtml(requiredBios)}</code>을(를) 판정했습니다.`,
        notice: `필수 BIOS(<code>${escapeHtml(requiredBios)}</code>)가 없으면 부팅 또는 게임 실행이 실패할 수 있습니다.`,
        btnText: `BIOS (${requiredBios}) 업로드`,
        isOptional: false,
      };
    }

    if (game.health_status === 'reclassify_required') {
      return {
        type: 'reclassify',
        needed: '기종 재분류',
        systemName: '등록 기종과 최신 분석 결과 불일치',
        title: '기종 재분류 필요',
        reason: `${escapeHtml(game.missing_roms || '현재 DB 기종과 최신 rom-analyzer 판정이 다릅니다.')}`,
        notice: 'ROM을 실제로 이동하는 작업은 설정의 <strong>라이브러리 전체 재구축</strong>에서 수행됩니다. 현재 경로로 실행을 강행할 수는 있습니다.',
        btnText: '',
        hideUpload: true,
        isOptional: false,
      };
    }

    if (game.health_status === 'unverified' || game.health_status === 'bad_dump_or_unknown' || game.health_status === 'parent_required') {
      return {
        type: 'unverified',
        needed: '추가 판정 근거',
        systemName: 'rom-analyzer 판정 미확인',
        title: '실행 가능 여부 미확인',
        reason: `${escapeHtml(game.missing_roms || 'rom-analyzer가 충분한 근거로 이 ROM을 판정하지 못했습니다.')}`,
        notice: '손상으로 확정된 것은 아닙니다. 현재 분석 근거가 부족하므로 실행 결과를 보장할 수 없습니다.',
        btnText: '',
        hideUpload: true,
        isOptional: false,
      };
    }

    if (game.health_status === 'unsupported') {
      return {
        type: 'unsupported',
        needed: '현재 EmulatorJS Stable 코어',
        systemName: '에뮬레이터 코어 호환성 제한',
        title: '현재 코어에서 구동 불가',
        reason: `${escapeHtml(game.missing_roms || 'ROM/BIOS 파일은 확인되었지만 현재 EmulatorJS Stable 코어에서 이 게임을 정상 구동할 수 없습니다.')}`,
        notice: 'ROM 또는 BIOS 누락 문제가 아니라 <strong>현재 에뮬레이터 코어의 게임별 호환성 제한</strong>입니다. 실행을 강행하면 검은 화면이나 부팅 정지가 발생할 수 있습니다.',
        btnText: '',
        hideUpload: true,
        isOptional: false,
      };
    }

    if (game.health_status === 'chd_required') {
      const requiredDisk = (game.missing_roms || '').trim() || `${rawStem}.chd`;
      return {
        type: 'chd',
        needed: requiredDisk,
        systemName: '필수 디스크 이미지',
        title: 'CHD / 디스크 이미지 필요',
        reason: `rom-analyzer가 이 게임의 필수 디스크 이미지 <code>${escapeHtml(requiredDisk)}</code>을(를) 찾지 못했습니다.`,
        notice: '필수 디스크 이미지가 없으면 부팅 또는 게임 진행이 실패할 수 있습니다.',
        btnText: '확인',
        hideUpload: true,
        isOptional: false,
      };
    }

    if (game.health_status === 'incomplete') {
      let sampleMissing = '';
      try {
        const parsed = JSON.parse(game.missing_roms || '[]');
        if (Array.isArray(parsed) && parsed.length > 0) sampleMissing = parsed.slice(0, 3).join(', ');
      } catch (e) {
        sampleMissing = game.missing_roms || '';
      }
      return {
        type: 'incomplete',
        needed: sampleMissing || '참조 파일',
        systemName: '디스크/플레이리스트 구성 불완전',
        title: '참조 파일 누락',
        reason: `M3U/CUE/GDI 등에서 참조하는 파일(<code>${escapeHtml(sampleMissing || '일부 파일')}</code>)을 찾지 못했습니다.`,
        notice: '누락된 참조 파일을 같은 게임 번들에 추가한 뒤 전체 ROM 분석을 다시 실행하세요.',
        btnText: '확인',
        hideUpload: true,
        isOptional: false,
      };
    }

    // PS1 BIOS는 현재 코어에서 필수는 아니므로 상태 오류가 아니라 선택 권장만 유지한다.
    if (platform === 'PS1' || core === 'psx') {
      const hasPsxBios = hasNeededBios || biosList.some((b) => b.startsWith('scph'));
      if (!hasPsxBios) {
        return {
          type: 'bios',
          needed: 'scph5501.bin (또는 scph1001.bin)',
          systemName: 'PlayStation 1 (PS1)',
          reason: 'PS1 공식 BIOS가 있으면 일부 게임의 호환성과 사운드 품질이 향상될 수 있습니다.',
          isOptional: true,
        };
      }
    }

    return null;
  }

  function showBiosWarningModal(game, missing, launchPlan = null) {
    const modal = $('gbaBiosWarningModal');
    if (!modal) {
      _startEmulator(game, launchPlan);
      return;
    }
    const isParent = missing.type === 'parent';
    const isUnsupported = missing.type === 'unsupported';
    const isReclassify = missing.type === 'reclassify';
    const isUnverified = missing.type === 'unverified';
    const iconClass = missing.type === 'parent'
      ? 'fa-solid fa-folder-tree'
      : missing.type === 'chd'
        ? 'fa-solid fa-compact-disc'
        : missing.type === 'unknown'
          ? 'fa-solid fa-circle-question'
          : isUnsupported
            ? 'fa-solid fa-ban'
            : isReclassify
              ? 'fa-solid fa-shuffle'
              : isUnverified
                ? 'fa-solid fa-circle-question'
                : 'fa-solid fa-microchip';

    if ($('gbaBiosWarningHeaderSpan')) {
      $('gbaBiosWarningHeaderSpan').textContent = missing.title || (isParent ? '아케이드 부모 롬(Parent ROM) 필요' : '시스템 바이오스(BIOS) 확인');
    }
    if ($('gbaBiosWarningHeaderIcon')) {
      $('gbaBiosWarningHeaderIcon').className = iconClass;
    }
    if ($('gbaBiosWarningBodyIcon')) {
      $('gbaBiosWarningBodyIcon').innerHTML = `<i class="${iconClass}"></i>`;
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
    const uploadBtn = $('gbaBiosWarningUploadBtn');
    if (uploadBtn) uploadBtn.style.display = missing.hideUpload ? 'none' : 'inline-flex';
    const proceedBtn = $('gbaBiosWarningProceedBtn');
    if (proceedBtn) proceedBtn.textContent = isUnsupported ? '그래도 실행' : '무시하고 계속 실행';
    modal.style.display = 'flex';

    $('gbaBiosWarningProceedBtn').onclick = () => {
      modal.style.display = 'none';
      prepareLaunchUi(game);
      _startEmulator(game, launchPlan);
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
  function handleLaunchProgressEvent(payload = {}) {
    const phase = String(payload.phase || 'loading');
    const loaded = Number(payload.loaded || 0);
    const total = Number(payload.total || 0);
    const plan = state.launchPlan || {};
    const deliveryMode = String(payload.deliveryMode || plan.delivery_mode || 'direct');

    if (phase === 'emulator-core') {
      setLaunchProgress('emulator-core', '에뮬레이터 코어 준비 중...', 'EmulatorJS와 실행 코어를 초기화하고 있습니다.', '측정할 수 없는 준비 단계이므로 완료될 때까지 기다려 주세요.', null);
      return;
    }
    if (phase === 'bios-wait') {
      const meta = Number(plan.bios_size || 0) > 0 ? `BIOS 크기 ${formatBytes(plan.bios_size)} · 서버 응답 대기 중` : '서버에서 BIOS 파일을 준비하고 있습니다.';
      setLaunchProgress('bios-wait', 'BIOS 파일 준비 중...', '서버의 BIOS를 브라우저로 전송할 준비를 하고 있습니다.', meta, null);
      return;
    }
    if (phase === 'bios-transfer') {
      if (loaded <= 0) {
        handleLaunchProgressEvent({ phase: 'bios-wait' });
        return;
      }
      const meta = total > 0 ? `${formatBytes(loaded)} / ${formatBytes(total)} 전송` : `${formatBytes(loaded)} 전송됨`;
      const percent = total > 0 ? Math.round((loaded / total) * 100) : null;
      setLaunchProgress('bios-transfer', 'BIOS 전송 중...', '서버의 BIOS를 브라우저 메모리로 전송하고 있습니다.', meta, percent);
      return;
    }
    if (phase === 'bios-ready') {
      setLaunchProgress('bios-ready', 'BIOS 준비 완료', 'BIOS 전송이 완료되었습니다.', '게임 데이터를 준비합니다.', 100);
      return;
    }
    if (phase === 'rom-wait') {
      let title = '게임 데이터 요청 중...';
      let desc = '서버에서 ROM 데이터를 준비하고 있습니다.';
      if (deliveryMode === 'bundle_zip') {
        title = '디스크 번들 준비 중...';
        desc = '서버에서 CUE/GDI와 관련 디스크를 실행용 ZIP으로 구성하고 있습니다.';
      } else if (deliveryMode === 'convert_7z') {
        title = '실행 데이터 변환 중...';
        desc = '서버에서 7z ROM을 EmulatorJS용 ZIP으로 변환하고 있습니다.';
      } else if (deliveryMode === 'zip') {
        desc = '서버에서 압축 ROM 데이터를 준비하고 있습니다.';
      }
      const sourceSize = Number(plan.rom_source_size || 0);
      const meta = sourceSize > 0 ? `원본 데이터 ${formatBytes(sourceSize)} · 첫 응답 대기 중` : '첫 데이터가 도착할 때까지 기다려 주세요.';
      setLaunchProgress('rom-wait', title, desc, meta, null);
      return;
    }
    if (phase === 'rom-transfer') {
      if (loaded <= 0) {
        handleLaunchProgressEvent({ phase: 'rom-wait', deliveryMode });
        return;
      }
      const meta = total > 0 ? `${formatBytes(loaded)} / ${formatBytes(total)} 전송` : `${formatBytes(loaded)} 전송됨`;
      const percent = total > 0 ? Math.round((loaded / total) * 100) : null;
      setLaunchProgress('rom-transfer', '게임 데이터 전송 중...', '서버에서 브라우저로 게임 데이터를 전송하고 있습니다.', meta, percent);
      return;
    }
    if (phase === 'rom-unpack') {
      let title = 'ROM 압축 해제 중...';
      let desc = '브라우저에서 압축된 게임 데이터를 풀고 있습니다.';
      if (deliveryMode === 'bundle_zip') {
        title = '디스크 번들 압축 해제 중...';
        desc = '브라우저에서 실행용 디스크 번들을 준비하고 있습니다.';
      } else if (deliveryMode === 'convert_7z') {
        title = '변환된 게임 데이터 압축 해제 중...';
      }
      const completed = total > 0 ? formatBytes(total) : (loaded > 0 ? formatBytes(loaded) : '완료');
      setLaunchProgress('rom-unpack', title, desc, `전송 완료: ${completed}`, null);
      return;
    }
    if (phase === 'rom-ready') {
      const completed = total > 0 ? formatBytes(total) : (loaded > 0 ? formatBytes(loaded) : '완료');
      setLaunchProgress('rom-ready', '게임 데이터 준비 완료', 'ROM 데이터를 에뮬레이터 코어에 전달하고 있습니다.', `전송 완료: ${completed}`, 100);
      return;
    }
    if (phase === 'state-wait') {
      setLaunchProgress('state-wait', '저장 상태 준비 중...', '이전 플레이 상태를 서버에서 확인하고 있습니다.', '저장 상태 파일 응답을 기다리고 있습니다.', null);
      return;
    }
    if (phase === 'state-transfer') {
      if (loaded <= 0) {
        handleLaunchProgressEvent({ phase: 'state-wait' });
        return;
      }
      const meta = total > 0 ? `${formatBytes(loaded)} / ${formatBytes(total)} 전송` : `${formatBytes(loaded)} 전송됨`;
      const percent = total > 0 ? Math.round((loaded / total) * 100) : null;
      setLaunchProgress('state-transfer', '저장 상태 복원 중...', '저장 상태 데이터를 브라우저로 가져오고 있습니다.', meta, percent);
      return;
    }
    if (phase === 'state-ready') {
      setLaunchProgress('state-ready', '저장 상태 준비 완료', '이전 플레이 상태를 코어에 적용하고 있습니다.', '복원 데이터 전송 완료', 100);
      return;
    }
    if (phase === 'starting') {
      setLaunchProgress('starting', '에뮬레이터 시작 중...', '코어 초기화와 첫 화면 구성을 마무리하고 있습니다.', '거의 완료되었습니다.', null);
      return;
    }
    if (phase === 'error') {
      showLaunchFailure(payload.message || '게임 데이터를 불러오지 못했습니다.', payload.detail || '네트워크 연결과 서버 파일 상태를 확인해 주세요.');
    }
  }

  function prepareLaunchUi(game) {
    if (state.autoSaveIntervalId) {
      clearInterval(state.autoSaveIntervalId);
      state.autoSaveIntervalId = null;
    }
    stopGamepadPoller();
    state.activeGame = game;
    state.isPaused = false;
    state.currentSpeed = 1;
    state.isMuted = false;
    if ($('gbaCurrentGameTitle')) $('gbaCurrentGameTitle').textContent = game.title;
    if ($('gbaPlayerBadge')) {
      const sys = getSystemInfo(game);
      $('gbaPlayerBadge').textContent = sys.label;
    }
    const playerModal = $('gbaPlayerModal');
    if (playerModal) playerModal.style.display = 'flex';
    setSaveStatus('실행 상태 확인 중...', 'saving');
    updatePlayerToolbarUI();
    setLaunchProgress('plan', '게임 실행 준비 중...', '실행 환경을 확인하고 있습니다.', 'ROM, BIOS, 저장 상태를 확인하는 중입니다.', null);
  }

  function applyLaunchPlanToGame(game, plan) {
    if (!game || !plan) return;
    game.has_save = Number(plan.has_save || 0);
    game.has_state = Number(plan.has_state || 0);
    game.runtime_state_loaded = true;
    game.has_needed_bios = plan.bios_available ? 1 : 0;
    if (!game.needed_bios && plan.bios_name) game.needed_bios = plan.bios_name;
    if (plan.rom_url) game.rom_url = plan.rom_url;
    if (plan.state_url) game.state_url = plan.state_url;
  }

  function abortLaunchUi() {
    if (launchSlowTimerId) {
      clearTimeout(launchSlowTimerId);
      launchSlowTimerId = null;
    }
    if (state.autoSaveIntervalId) {
      clearInterval(state.autoSaveIntervalId);
      state.autoSaveIntervalId = null;
    }
    stopGamepadPoller();
    const container = $('gbaEmulatorContainer');
    if (container) container.innerHTML = '';
    window.__GBA_ON_GAME_START__ = null;
    window.__GBA_ON_LAUNCH_PROGRESS__ = null;
    state.launchPlan = null;
    hideLaunchProgress();
    const playerModal = $('gbaPlayerModal');
    if (playerModal) playerModal.style.display = 'none';
    state.activeGame = null;
  }

  function showLaunchFailure(message, detail = '') {
    const safeMessage = String(message || '게임 실행 중 오류가 발생했습니다.');
    const safeDetail = String(detail || '네트워크 연결과 ROM/BIOS 파일 상태를 확인한 뒤 다시 시도하세요.');
    setLaunchProgress('error', '게임 실행에 실패했습니다', safeMessage, safeDetail, null);
  }

  async function launchGame(game, bypassBiosCheck = false) {
    prepareLaunchUi(game);
    try {
      const plan = await apiCall('launch_plan', { game_id: game.id });
      if (!plan || !plan.success) {
        throw new Error((plan && (plan.error || plan.blocked_reason)) || '실행 정보를 확인할 수 없습니다.');
      }
      if (!plan.launchable) {
        showLaunchFailure(plan.blocked_reason || '이 게임은 현재 실행할 수 없습니다.');
        return;
      }

      state.launchPlan = plan;
      applyLaunchPlanToGame(game, plan);

      if (!bypassBiosCheck) {
        const missing = checkMissingBios(game);
        if (missing) {
          if (!missing.isOptional) {
            hideLaunchProgress();
            const playerModal = $('gbaPlayerModal');
            if (playerModal) playerModal.style.display = 'none';
            showBiosWarningModal(game, missing, plan);
            return;
          }
          showToast(`💡 ${missing.systemName}: ${missing.needed} 등록 권장`, false);
        }
      }
      await _startEmulator(game, plan);
    } catch (err) {
      console.error('[GBA] Launch plan error:', err);
      showLaunchFailure(err && err.message ? err.message : '실행 환경 확인에 실패했습니다.');
    }
  }

  async function _startEmulator(game, launchPlan = null) {
    state.activeGame = game;
    state.launchPlan = launchPlan || state.launchPlan || null;
    setLaunchProgress('emulator-core', '에뮬레이터 코어 준비 중...', 'EmulatorJS와 실행 코어를 초기화하고 있습니다.', '코어 준비 시간은 다운로드 환경에 따라 달라질 수 있습니다.', null);

    // 재생 기록은 게임 실행에 필수 작업이 아니므로 코어 준비와 병렬 처리한다.
    apiCall('record_play', { game_id: game.id })
      .then((res) => {
        const found = state.games.find((g) => g.id === game.id);
        if (found) {
          found.play_count = (found.play_count || 0) + 1;
          found.last_played_at = (res && res.last_played_at) || new Date(Date.now() + 9 * 3600 * 1000).toISOString().replace('T', ' ').substring(0, 19);
        }
      })
      .catch((e) => console.warn('[GBA] Record play error:', e));

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

    // 실행 계획이 있으면 서버가 확정한 BIOS/ROM/세이브 경로를 사용한다.
    const activePlan = launchPlan || state.launchPlan || null;
    const biosList = (state.available_bios || []).map((b) => b.toLowerCase());
    let neededBiosFile = activePlan && activePlan.bios_available && activePlan.bios_name ? activePlan.bios_name : null;

    if (!activePlan) {
      if (game.needed_bios) {
        neededBiosFile = game.needed_bios;
      } else {
        if (platformKey === 'neo-geo' || (isArcade && (rawStem.startsWith('mslug') || rawStem.startsWith('kof') || rawStem.startsWith('samsho') || rawStem.startsWith('fatfur') || rawStem.startsWith('garou')))) {
          if (biosList.includes('neogeo.zip')) neededBiosFile = 'neogeo.zip';
        } else if (isArcade && (rawStem.startsWith('olds') || rawStem.startsWith('kov') || rawStem.startsWith('orlegend') || rawStem.startsWith('dmnfrnt'))) {
          if (biosList.includes('pgm.zip')) neededBiosFile = 'pgm.zip';
        } else if (coreToUse === 'psx' || platformKey === 'ps1') {
          const psxBios = biosList.find((b) => b.startsWith('scph5501') || b.startsWith('scph1001') || b.startsWith('scph5500') || b.startsWith('scph5502') || b.startsWith('scph7001'));
          if (psxBios) neededBiosFile = psxBios;
        }
      }
    }

    const biosUrl = activePlan && activePlan.bios_url
      ? window.location.origin + activePlan.bios_url
      : (neededBiosFile ? `${window.location.origin}/api/webhook/bookoasis_gamebooks/bios/${encodeURIComponent(neededBiosFile)}?game_id=${encodeURIComponent(game.id)}` : null);
    const gameUrl = window.location.origin + ((activePlan && activePlan.rom_url) || game.rom_url);
    const gameName = isArcade ? (game.game_code || rawStem) : game.title;
    const loadStateUrl = activePlan && activePlan.state_url
      ? window.location.origin + activePlan.state_url
      : (game.has_state ? window.location.origin + game.state_url : null);
    const deliveryMode = String((activePlan && activePlan.delivery_mode) || 'direct');
    const browserUnpack = !!(activePlan && activePlan.browser_unpack);

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
    const __GBA_PROGRESS__ = {
      lastLoaded: 0,
      lastTotal: 0,
      emit(payload) {
        try {
          if (window.parent && typeof window.parent.__GBA_ON_LAUNCH_PROGRESS__ === 'function') {
            window.parent.__GBA_ON_LAUNCH_PROGRESS__(payload || {});
          }
        } catch (e) {}
      },
      updateDownload(phase, loaded, total) {
        this.lastLoaded = Number(loaded || 0);
        this.lastTotal = Number(total || 0);
        this.emit({ phase, loaded: this.lastLoaded, total: this.lastTotal });
      }
    };

    const __GBA_GAME_URL__ = ${JSON.stringify(gameUrl)};
    const __GBA_BIOS_URL__ = ${JSON.stringify(biosUrl || '')};
    const __GBA_STATE_URL__ = ${JSON.stringify(loadStateUrl || '')};
    const __GBA_DELIVERY_MODE__ = ${JSON.stringify(deliveryMode)};
    const __GBA_BROWSER_UNPACK__ = ${JSON.stringify(browserUnpack)};

    __GBA_PROGRESS__.emit({ phase: 'emulator-core', deliveryMode: __GBA_DELIVERY_MODE__ });

    function __gbaTargetPhase(url, waitPhase) {
      if (url === __GBA_GAME_URL__) return waitPhase === 'wait' ? 'rom-wait' : 'rom-transfer';
      if (__GBA_BIOS_URL__ && url === __GBA_BIOS_URL__) return waitPhase === 'wait' ? 'bios-wait' : 'bios-transfer';
      if (__GBA_STATE_URL__ && url === __GBA_STATE_URL__) return waitPhase === 'wait' ? 'state-wait' : 'state-transfer';
      return '';
    }

    try {
      const origFetch = window.fetch ? window.fetch.bind(window) : null;
      if (origFetch) {
        window.fetch = async function(input, init) {
          const url = typeof input === 'string' ? input : ((input && input.url) || '');
          const waitPhase = __gbaTargetPhase(url, 'wait');
          if (waitPhase) __GBA_PROGRESS__.emit({ phase: waitPhase, deliveryMode: __GBA_DELIVERY_MODE__ });
          try {
            const response = await origFetch(input, init);
            if (!response.ok && waitPhase) {
              __GBA_PROGRESS__.emit({ phase: 'error', message: '서버가 게임 실행 파일 요청을 처리하지 못했습니다.', detail: 'HTTP ' + response.status });
            }
            // fetch()는 응답 헤더 시점에 resolve되므로 전송 완료로 오인하지 않는다.
            // 실제 완료/바이트 진행률은 XHR 이벤트가 제공되는 경우에만 표시한다.
            return response;
          } catch (error) {
            if (waitPhase) __GBA_PROGRESS__.emit({ phase: 'error', message: '게임 실행 데이터를 가져오지 못했습니다.', detail: String(error && error.message ? error.message : error) });
            throw error;
          }
        };
      }
    } catch (e) {}

    try {
      const OrigXHR = window.XMLHttpRequest;
      function ProgressXHR() {
        const xhr = new OrigXHR();
        let trackedUrl = '';
        const origOpen = xhr.open;
        xhr.open = function(method, url) {
          trackedUrl = String(url || '');
          return origOpen.apply(xhr, arguments);
        };
        xhr.addEventListener('loadstart', function() {
          const phase = __gbaTargetPhase(trackedUrl, 'wait');
          if (!phase) return;
          __GBA_PROGRESS__.lastLoaded = 0;
          __GBA_PROGRESS__.lastTotal = 0;
          __GBA_PROGRESS__.emit({ phase, deliveryMode: __GBA_DELIVERY_MODE__ });
        });
        xhr.addEventListener('progress', function(e) {
          const phase = __gbaTargetPhase(trackedUrl, 'transfer');
          if (!phase || Number(e.loaded || 0) <= 0) return;
          __GBA_PROGRESS__.updateDownload(phase, e.loaded, e.lengthComputable ? e.total : 0);
        });
        xhr.addEventListener('load', function() {
          const tracked = __gbaTargetPhase(trackedUrl, 'wait');
          if (!tracked) return;
          if (xhr.status >= 400) {
            __GBA_PROGRESS__.emit({ phase: 'error', message: '서버가 게임 실행 파일 요청을 처리하지 못했습니다.', detail: 'HTTP ' + xhr.status });
            return;
          }
          if (trackedUrl === __GBA_GAME_URL__) {
            __GBA_PROGRESS__.emit({
              phase: __GBA_BROWSER_UNPACK__ ? 'rom-unpack' : 'rom-ready',
              loaded: __GBA_PROGRESS__.lastLoaded,
              total: __GBA_PROGRESS__.lastTotal,
              deliveryMode: __GBA_DELIVERY_MODE__
            });
          } else if (trackedUrl === __GBA_BIOS_URL__) {
            __GBA_PROGRESS__.emit({ phase: 'bios-ready' });
          } else if (__GBA_STATE_URL__ && trackedUrl === __GBA_STATE_URL__) {
            __GBA_PROGRESS__.emit({ phase: 'state-ready' });
          }
        });
        const emitNetworkError = function() {
          if (__gbaTargetPhase(trackedUrl, 'wait')) {
            __GBA_PROGRESS__.emit({ phase: 'error', message: '게임 실행 데이터 전송이 중단되었습니다.', detail: '네트워크 연결 또는 서버 파일 상태를 확인해 주세요.' });
          }
        };
        xhr.addEventListener('error', emitNetworkError);
        xhr.addEventListener('abort', emitNetworkError);
        xhr.addEventListener('timeout', emitNetworkError);
        return xhr;
      }
      ProgressXHR.prototype = OrigXHR.prototype;
      window.XMLHttpRequest = ProgressXHR;
    } catch (e) {}

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
      if (window.parent && typeof window.parent.__GBA_ON_LAUNCH_PROGRESS__ === 'function') {
        window.parent.__GBA_ON_LAUNCH_PROGRESS__({ phase: 'starting' });
      }
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
  <script src="https://cdn.emulatorjs.org/stable/data/loader.js" onerror="__GBA_PROGRESS__.emit({phase:'error',message:'EmulatorJS 로더를 불러오지 못했습니다.',detail:'CDN 연결 상태를 확인해 주세요.'})"><\/script>
</body>
</html>`;

    // 부모-자식 프레임 브릿지 콜백 등록
    window.__GBA_ON_LAUNCH_PROGRESS__ = (payload = {}) => handleLaunchProgressEvent(payload);

    window.__GBA_ON_GAME_START__ = () => {
      setLaunchProgress('started', '게임 시작 완료', '첫 화면 진입을 마무리합니다.', '잠시 후 오버레이가 사라집니다.', 100);
      apiCall('record_boot', { game_id: game.id })
        .then((res) => { if (res && res.success) applyPlayStateToGame(game.id, res.play); })
        .catch((err) => console.warn('[GBA] Boot status record error:', err));
      focusEmulator();
      setTimeout(() => {
        applyGraphicsSettings();
        focusEmulator();
      }, 200);
      setTimeout(focusEmulator, 800);
      setTimeout(() => hideLaunchProgress(), 350);
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
    window.__GBA_ON_LAUNCH_PROGRESS__ = null;
    window.EJS_emulator = null;
    hideLaunchProgress();
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
          `대용량 파일은 설정된 롬 저장소 폴더에 직접 넣고 [라이브러리 동기화]을 실행하시거나, 북오아시스 .env의 MAX_CONTENT_LENGTH_MB를 늘려주세요.`,
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
      if (type === 'rom') formData.append('defer_sync', '1');
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
            `대용량 롬은 설정된 롬 폴더에 직접 복사 후 [라이브러리 동기화]을 이용해 주세요.`,
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

    if (type === 'bios' && completed > 0) {
      try {
        await refreshRuntimeGlobals(state.games);
      } catch (err) {
        console.warn('[GBA] BIOS runtime state refresh error:', err);
      }
    }

    let ingestSyncOk = true;
    if (type === 'rom' && completed > 0) {
      statusEl.textContent = '업로드한 ROM을 라이브러리에 반영하는 중...';
      detailsEl.textContent = `${completed}개 업로드 완료 · 통합 분석/등록 중`;
      try {
        const syncRes = await apiCall('library_sync', { mode: 'ingest' });
        if (!syncRes || !syncRes.success) {
          throw new Error(syncRes && syncRes.error ? syncRes.error : '업로드 후 라이브러리 반영 실패');
        }
      } catch (err) {
        ingestSyncOk = false;
        console.error('[GBA] Upload ingest sync error:', err);
        showToast('ROM 업로드는 완료되었지만 라이브러리 반영 중 오류가 발생했습니다: ' + (err.message || err), true);
      }
    }

    progEl.style.width = '100%';
    detailsEl.textContent = `${completed} / ${total} 파일 완료`;
    if (type === 'rom') {
      statusEl.textContent = completed === 0
        ? '업로드된 ROM이 없습니다.'
        : ingestSyncOk
          ? 'ROM 추가 및 라이브러리 반영 완료!'
          : 'ROM 업로드 완료 · 라이브러리 동기화가 필요합니다.';
    } else {
      statusEl.textContent = '업로드 완료!';
    }

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
          await loadLibrary(true);
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
        await loadLibrary(true);
        showToast('게임 제목이 변경되었습니다.');
      }
    } catch (e) {
      showToast('제목 변경 중 오류가 발생했습니다.', true);
    }
  }

  function updateDeleteQueueCount() {
    const countEl = $('gbaDeleteQueueCount');
    if (countEl) countEl.textContent = String(Number(state.pendingDeleteCount || 0));
  }

  function deletionStatusLabel(status) {
    const value = String(status || '').toLowerCase();
    if (value === 'pending') return '삭제 대기';
    if (value === 'deleting') return '삭제 중';
    if (value === 'failed') return '삭제 실패';
    return value || '알 수 없음';
  }

  async function loadDeleteQueue(openModal = false) {
    const res = await apiCall('delete_queue_status');
    if (!res || !res.success) throw new Error((res && res.error) || '삭제 대기 목록 조회 실패');
    const items = Array.isArray(res.items) ? res.items : [];
    state.pendingDeleteCount = Number(res.count ?? items.length);
    updateDeleteQueueCount();

    const body = $('gbaDeleteQueueBody');
    const empty = $('gbaDeleteQueueEmpty');
    const wrap = $('gbaDeleteQueueTableWrap');
    const summary = $('gbaDeleteQueueSummary');
    if (summary) summary.textContent = `삭제 대기/실패 ${items.length}개`;
    if (empty) empty.style.display = items.length ? 'none' : 'block';
    if (wrap) wrap.style.display = items.length ? 'block' : 'none';
    if (body) {
      body.innerHTML = items.map((item) => {
        const status = String(item.deletion_status || 'pending').toLowerCase();
        const failed = status === 'failed';
        const deleting = status === 'deleting';
        const detail = failed && item.deletion_error
          ? `<div style="color: var(--gba-danger, #ef4444); margin-top: 4px; max-width: 360px; white-space: normal;">${escapeHtml(item.deletion_error)}</div>`
          : '';
        const disabled = deleting ? 'disabled' : '';
        return `
          <tr>
            <td><strong>${escapeHtml(item.title || item.filename || item.id)}</strong><br><small>${escapeHtml(item.filename || '')}</small></td>
            <td>${escapeHtml(deletionStatusLabel(status))}</td>
            <td>${escapeHtml(item.deletion_requested_at || '-')} ${detail}</td>
            <td><button type="button" class="gba-btn gba-btn-secondary gba-delete-cancel-btn" data-game-id="${escapeHtml(String(item.id || ''))}" ${disabled}>삭제 취소</button></td>
          </tr>`;
      }).join('');
      body.querySelectorAll('.gba-delete-cancel-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const gameId = btn.dataset.gameId || '';
          if (!gameId) return;
          btn.disabled = true;
          try {
            const cancelRes = await apiCall('cancel_delete_game', { game_id: gameId });
            if (!cancelRes || !cancelRes.success) throw new Error((cancelRes && cancelRes.error) || '삭제 취소 실패');
            showToast(cancelRes.message || '삭제 예약을 취소했습니다.');
            await loadDeleteQueue(false);
            await loadLibrary(true);
          } catch (err) {
            showToast(err.message || '삭제 취소 중 오류가 발생했습니다.', true);
            btn.disabled = false;
          }
        });
      });
    }
    if (openModal && $('gbaDeleteQueueModal')) $('gbaDeleteQueueModal').style.display = 'flex';
    return items;
  }

  function phase6SampleText(sample) {
    if (!Array.isArray(sample) || !sample.length) return '';
    return sample.slice(0, 5).map((item) => {
      if (item && typeof item === 'object') {
        return item.id || item.path || item.game_id || JSON.stringify(item);
      }
      return String(item ?? '');
    }).filter(Boolean).join(', ');
  }

  function renderPhase6Preflight(data) {
    state.phase6Preflight = data || null;
    const content = $('gbaPhase6PreflightContent');
    const loading = $('gbaPhase6PreflightLoading');
    if (loading) loading.style.display = 'none';
    if (content) content.style.display = 'block';
    const blockers = Array.isArray(data?.blockers) ? data.blockers : [];
    const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
    const ready = !!data?.ready;
    const summary = $('gbaPhase6PreflightSummary');
    if (summary) {
      summary.classList.toggle('is-ready', ready);
      summary.classList.toggle('has-blockers', !ready);
      const journal = data?.migration_journal || {};
      summary.innerHTML = `
        <strong>${ready ? '라이브러리 구조 전환 시작 조건을 충족했습니다.' : '라이브러리 구조 전환 전에 해결할 항목이 있습니다.'}</strong><br>
        게임 ${Number(data?.total_games || 0).toLocaleString()}개 · layout v2 ${Number(data?.layout_v2 || 0).toLocaleString()}개 ·
        DB schema ${escapeHtml(String(data?.schema_version ?? '-'))}/${escapeHtml(String(data?.required_schema_version ?? '-'))} ·
        migration journal ${Number(journal.total || 0).toLocaleString()}건<br>
        <small>통합 루트: ${escapeHtml(String(data?.emulatorjs_root || '-'))}</small>`;
    }
    const renderItems = (items, emptyText) => items.length
      ? items.map((item) => {
          const sample = phase6SampleText(item.sample);
          return `<div class="gba-phase6-item"><div><strong>${escapeHtml(item.label || item.code || '항목')}</strong>${sample ? `<small>${escapeHtml(sample)}</small>` : ''}</div><span class="gba-phase6-count">${Number(item.count || 0).toLocaleString()}개</span></div>`;
        }).join('')
      : `<div class="gba-phase6-empty">${escapeHtml(emptyText)}</div>`;
    if ($('gbaPhase6Blockers')) $('gbaPhase6Blockers').innerHTML = renderItems(blockers, '차단 항목이 없습니다.');
    if ($('gbaPhase6Warnings')) $('gbaPhase6Warnings').innerHTML = renderItems(warnings, '추가 경고가 없습니다.');
    const repairs = Array.isArray(data?.repairs) ? data.repairs : [];
    if ($('gbaPhase6Repairs')) {
      $('gbaPhase6Repairs').innerHTML = repairs.length
        ? `<strong>이번 안전 복구:</strong> ${repairs.map(escapeHtml).join(' · ')}`
        : '안전 복구는 DB 매핑/고아 사용자 기록/복구 가능한 stale ROM 경로만 수정하며 ROM 파일을 이동하지 않습니다.';
    }
  }

  async function runPhase6Preflight(repair = false) {
    const modal = $('gbaPhase6PreflightModal');
    if (modal) modal.style.display = 'flex';
    if ($('gbaPhase6PreflightLoading')) $('gbaPhase6PreflightLoading').style.display = 'flex';
    if ($('gbaPhase6PreflightContent')) $('gbaPhase6PreflightContent').style.display = 'none';
    const action = repair ? 'phase6_repair' : 'phase6_preflight';
    try {
      const res = await apiCall(action);
      if (!res || !res.success) throw new Error((res && res.error) || '라이브러리 구조 전환 준비 점검 실패');
      renderPhase6Preflight(res);
      return res;
    } catch (err) {
      if (modal) modal.style.display = 'none';
      showToast(err.message || '라이브러리 구조 전환 준비 점검 중 오류가 발생했습니다.', true);
      throw err;
    }
  }

  async function createPhase6Backup() {
    const btn = $('gbaPhase6BackupBtn');
    if (btn) btn.disabled = true;
    try {
      const res = await apiCall('phase6_backup');
      if (!res || !res.success) throw new Error((res && res.error) || 'DB 백업 실패');
      showToast(`구조 전환 전 DB 백업을 생성했습니다: ${res.filename || 'backup.sqlite3'}`);
    } catch (err) {
      showToast(err.message || 'DB 백업 생성 중 오류가 발생했습니다.', true);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function updateCoverWebpButton(progress) {
    const btn = $('gbaCoverWebpBtn');
    if (!btn) return;
    const running = !!progress?.is_running;
    const current = Number(progress?.current || 0);
    const total = Number(progress?.total || 0);
    if (running) {
      btn.disabled = true;
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>WebP ${current.toLocaleString()}/${total.toLocaleString()}</span>`;
    } else {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-images"></i> <span>WebP 커버 생성</span>';
    }
  }

  async function pollCoverWebpProgress() {
    if (state.coverVariantPollTimer) {
      clearTimeout(state.coverVariantPollTimer);
      state.coverVariantPollTimer = null;
    }
    try {
      const res = await apiCall('cover_webp_progress');
      const progress = res?.progress || {};
      updateCoverWebpButton(progress);
      if (progress.is_running) {
        state.coverVariantPollTimer = setTimeout(pollCoverWebpProgress, 1000);
      } else if (progress.status === 'completed') {
        showToast(`WebP 커버 생성 완료: ${Number(progress.completed || 0).toLocaleString()}개 · 실패 ${Number(progress.failed || 0).toLocaleString()}개`);
        await loadLibrary(true);
      }
    } catch (err) {
      updateCoverWebpButton({ is_running: false });
      console.warn('[GameBooks] WebP cover progress error:', err);
    }
  }

  async function startCoverWebpRefresh() {
    if (!confirm('원본 커버는 유지하고 future_id 기반 small/large WebP 커버를 생성합니다.\n\n기존 WebP가 정상인 게임은 건너뜁니다. 계속할까요?')) return;
    try {
      const res = await apiCall('cover_webp_refresh');
      if (!res || !res.success) throw new Error((res && res.error) || 'WebP 커버 생성 시작 실패');
      updateCoverWebpButton(res.progress || { is_running: true });
      await pollCoverWebpProgress();
    } catch (err) {
      showToast(err.message || 'WebP 커버 생성 시작 중 오류가 발생했습니다.', true);
      updateCoverWebpButton({ is_running: false });
    }
  }

  async function confirmDeleteGame(game) {
    if (!confirm(`'${game.title}' 게임을 삭제 대기로 전환하시겠습니까?\n\n지금은 실제 ROM 파일을 삭제하지 않습니다.\n다음 라이브러리 동기화 또는 전체 재구축 시 실제 ROM/관련 디스크/세이브 데이터가 삭제됩니다.\n삭제 대기 게임은 목록에서 즉시 숨겨집니다.`)) {
      return;
    }

    try {
      const res = await apiCall('delete_game', { game_id: game.id });
      if (res.success) {
        state.games = state.games.filter((g) => g.id !== game.id);
        state.pendingDeleteCount = Number(state.pendingDeleteCount || 0) + 1;
        updateDeleteQueueCount();
        await loadLibrary(true);
        showToast(res.message || '삭제 대기로 전환했습니다.');
      } else {
        showToast(res.error || '삭제 예약 실패', true);
      }
    } catch (e) {
      showToast('삭제 예약 중 오류가 발생했습니다.', true);
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
    let searchReloadTimer = null;
    searchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value.trim();
      clearBtn.style.display = state.searchQuery ? 'block' : 'none';
      if (searchReloadTimer) clearTimeout(searchReloadTimer);
      searchReloadTimer = setTimeout(() => loadLibrary(true), 250);
    });
    clearBtn.addEventListener('click', () => {
      if (searchReloadTimer) clearTimeout(searchReloadTimer);
      searchInput.value = '';
      state.searchQuery = '';
      clearBtn.style.display = 'none';
      loadLibrary(true);
    });

    // 기종 카테고리 드롭다운 변경
    $('gbaCategorySelect')?.addEventListener('change', (e) => {
      state.category = e.target.value;
      loadLibrary(true);
    });

    // 라이브러리 정렬 선택
    const sortSelect = $('gbaSortSelect');
    if (sortSelect) {
      sortSelect.value = state.sort;
      sortSelect.addEventListener('change', (e) => {
        state.sort = e.target.value;
        localStorage.setItem('gba_library_sort', state.sort);
        loadLibrary(true);
      });
    }

    $('gbaStatusFilterSelect')?.addEventListener('change', (e) => {
      state.statusFilter = e.target.value || 'all';
      loadLibrary(true);
    });

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
      loadLibrary(true);
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
      let pollTimer = null;

      scanBtn.classList.add('gba-btn-scanning');
      scanBtn.title = '라이브러리 동기화 진행 중...';

      if (scanModal) {
        scanModal.style.display = 'flex';
        if (scanStatus) scanStatus.textContent = 'ROM 폴더와 Game Books 라이브러리를 동기화하는 중...';
        if (scanProgressBar) scanProgressBar.style.width = '5%';
        if (scanDetails) scanDetails.textContent = '신규·변경·삭제 항목 확인 중...';

        pollTimer = setInterval(async () => {
          try {
            const pollRes = await apiCall('scan_progress');
            if (pollRes && pollRes.success && pollRes.progress) {
              const p = pollRes.progress;
              if (p.total > 0) {
                let percent = 8;
                if (p.status === 'deleting') {
                  const deleteTotal = Math.max(Number(p.total || 0), 1);
                  const deleteCurrent = Math.min(Math.max(Number(p.current || 0), 0), deleteTotal);
                  percent = Math.min(5 + Math.round((deleteCurrent / deleteTotal) * 12), 17);
                  if (scanDetails) scanDetails.textContent = `삭제 예약 처리 ${deleteCurrent} / ${deleteTotal} - ${p.current_file || ''}`;
                } else if (p.status === 'saving') {
                  const saveTotal = Math.max(Number(p.total || 0), 1);
                  const saveCurrent = Math.min(Math.max(Number(p.current || 0), 0), saveTotal);
                  percent = Math.min(94 + Math.round((saveCurrent / saveTotal) * 5), 99);
                  if (scanDetails) {
                    const currentName = p.current_file ? ` - ${p.current_file}` : '';
                    scanDetails.textContent = `분석 결과 저장 ${saveCurrent} / ${saveTotal}${currentName}`;
                  }
                } else if (p.status === 'completed') {
                  percent = 100;
                  if (scanDetails) scanDetails.textContent = '라이브러리 동기화 완료!';
                } else {
                  percent = Math.min(8 + Math.round((p.current / p.total) * 86), 94);
                  if (scanDetails) scanDetails.textContent = `${p.current} / ${p.total} 처리 중 - ${p.current_file || ''}`;
                }
                if (scanProgressBar) scanProgressBar.style.width = `${percent}%`;
              }
            }
          } catch (e) {
            // 진행률 폴링 오류는 실제 동기화 결과에 영향을 주지 않는다.
          }
        }, 350);
      }

      try {
        const res = await apiCall('library_sync', { mode: 'sync' });
        if (!res || !res.success) {
          throw new Error(res && res.error ? res.error : '라이브러리 동기화 실패');
        }

        if (pollTimer) clearInterval(pollTimer);
        if (scanProgressBar) scanProgressBar.style.width = '100%';
        if (scanDetails) scanDetails.textContent = '라이브러리 갱신 중...';
        await loadLibrary(true);

        const stats = res.stats || {};
        const newCount = Number(stats.new_count || 0);
        const deletedCount = Number(stats.deleted_count || 0);
        const queuedDeletedCount = Number(stats.delete_processed_count || 0);
        const deleteFailedCount = Number(stats.delete_failed_count || 0);
        if (newCount > 0 || deletedCount > 0 || queuedDeletedCount > 0 || deleteFailedCount > 0) {
          const failedText = deleteFailedCount > 0 ? ` / 삭제 실패 ${deleteFailedCount}개` : '';
          showToast(`라이브러리 동기화 완료 (신규 ${newCount}개 / 누락 정리 ${deletedCount}개 / 예약 삭제 ${queuedDeletedCount}개${failedText})`, deleteFailedCount > 0);
        } else {
          showToast('라이브러리가 최신 상태입니다.');
        }
      } catch (e) {
        console.error('[GBA] Library sync error:', e);
        const message = e.message || String(e);
        const isBusyNotice = message.includes('진행 중입니다.') && message.includes('완료 후');
        showToast(isBusyNotice ? message : `라이브러리 동기화 중 오류가 발생했습니다: ${message}`, true);
        loadLibrary(true);
      } finally {
        if (pollTimer) clearInterval(pollTimer);
        scanBtn.classList.remove('gba-btn-scanning');
        scanBtn.title = 'ROM 폴더와 Game Books 라이브러리 동기화';
        if (scanModal) scanModal.style.display = 'none';
      }
    });

    // 설정 > 라이브러리 전체 재구축 버튼
    $('gbaFullScanBtn')?.addEventListener('click', async () => {
      const fullScanBtn = $('gbaFullScanBtn');
      if (fullScanBtn.classList.contains('gba-btn-scanning')) return;

      if (!confirm('라이브러리를 전체 재구축하시겠습니까?\n\n- 삭제 대기 게임의 실제 파일을 먼저 삭제합니다.\n- 모든 ROM을 최신 분석 기준으로 처음부터 다시 분석합니다.\n- 기종 판정에 따라 ROM 파일이 다른 폴더로 이동될 수 있습니다.\n- 7z 파일은 ZIP으로 변환될 수 있습니다.\n- 삭제 예약되지 않은 게임의 유저 세이브와 즐겨찾기는 보존됩니다.')) {
        return;
      }

      if ($('gbaSettingsModal')) $('gbaSettingsModal').style.display = 'none';

      const scanModal = $('gbaScanModal');
      const scanStatus = $('gbaScanStatus');
      const scanProgressBar = $('gbaScanProgressBar');
      const scanDetails = $('gbaScanDetails');

      fullScanBtn.classList.add('gba-btn-scanning');
      fullScanBtn.title = '라이브러리 전체 재구축 진행 중...';

      let pollTimer = null;
      if (scanModal) {
        scanModal.style.display = 'flex';
        if (scanStatus) scanStatus.textContent = '기존 진단 캐시를 무시하고 모든 ROM을 전체 재구축하는 중...';
        if (scanProgressBar) scanProgressBar.style.width = '5%';
        if (scanDetails) scanDetails.textContent = '기존 캐시 무효화 · 통합 DAT DB 및 바이너리 헤더 전수 분석 준비 중...';

        // 0.35초 간격으로 실시간 파일 단위 프로그레스 폴링
        pollTimer = setInterval(async () => {
          try {
            const pollRes = await apiCall('scan_progress');
            if (pollRes && pollRes.success && pollRes.progress) {
              const p = pollRes.progress;
              if (p.total > 0) {
                let percent = 5;
                if (p.status === 'deleting') {
                  const deleteTotal = Math.max(Number(p.total || 0), 1);
                  const deleteCurrent = Math.min(Math.max(Number(p.current || 0), 0), deleteTotal);
                  percent = Math.min(5 + Math.round((deleteCurrent / deleteTotal) * 12), 17);
                  if (scanDetails) scanDetails.textContent = `삭제 예약 처리 ${deleteCurrent} / ${deleteTotal} - ${p.current_file || ''}`;
                } else if (p.status === 'saving') {
                  const saveTotal = Math.max(Number(p.total || 0), 1);
                  const saveCurrent = Math.min(Math.max(Number(p.current || 0), 0), saveTotal);
                  percent = Math.min(94 + Math.round((saveCurrent / saveTotal) * 5), 99);
                  if (scanDetails) {
                    const currentName = p.current_file ? ` - ${p.current_file}` : '';
                    scanDetails.textContent = `분석 결과 저장 ${saveCurrent} / ${saveTotal}${currentName}`;
                  }
                } else if (p.status === 'completed') {
                  percent = 100;
                  if (scanDetails) {
                    scanDetails.textContent = '동기화 완료!';
                  }
                } else {
                  percent = Math.min(Math.round((p.current / p.total) * 90), 90);
                  if (scanDetails) {
                    scanDetails.textContent = `${p.current} / ${p.total} 파일 분석 중 (${percent}%) - ${p.current_file || ''}`;
                  }
                }
                if (scanProgressBar) scanProgressBar.style.width = `${percent}%`;
              }
            }
          } catch (e) {
            // 폴링 오류 무시
          }
        }, 350);
      }

      try {
        const res = await apiCall('library_sync', { mode: 'rebuild' });
        if (!res || !res.success) {
          throw new Error(res && res.error ? res.error : '전체 재구축 시작 실패');
        }

        // 백그라운드 스캔 완료될 때까지 비동기 대기
        await new Promise((resolve) => {
          const checkDone = setInterval(async () => {
            try {
              const pollRes = await apiCall('scan_progress');
              if (pollRes && pollRes.success && pollRes.progress) {
                const p = pollRes.progress;
                if (!p.is_running && p.status === 'completed') {
                  clearInterval(checkDone);
                  resolve();
                }
              }
            } catch (e) {
              // ignore
            }
          }, 500);
        });

        if (pollTimer) clearInterval(pollTimer);

        if (scanProgressBar) scanProgressBar.style.width = '98%';
        if (scanDetails) scanDetails.textContent = '라이브러리 갱신 중...';

        await loadLibrary(true);

        if (scanProgressBar) scanProgressBar.style.width = '100%';
        showToast('라이브러리 전체 재구축과 ROM 분석 캐시 갱신이 완료되었습니다!');
      } catch (err) {
        if (pollTimer) clearInterval(pollTimer);
        console.error('[GBA] Library rebuild error:', err);
        showToast('라이브러리 전체 재구축 중 오류가 발생했습니다: ' + (err.message || err), true);
        loadLibrary(true);
      } finally {
        if (pollTimer) clearInterval(pollTimer);
        fullScanBtn.classList.remove('gba-btn-scanning');
        fullScanBtn.title = '기존 캐시를 무시하고 모든 ROM을 전수 분석해 진단 캐시까지 새로 구성';
        if (scanModal) {
          scanModal.style.display = 'none';
        }
      }
    });
    
    // --------------------------------------------------------------------------
    // ROM 라이브러리 전체 분석 갱신 (내부 Health API 호환)
    // --------------------------------------------------------------------------
    let healthData = null;
    let activeHealthTab = 'issues';

    function renderHealthTable() {
      const tbody = $('gbaHealthTableBody');
      if (!tbody || !healthData) return;

      const q = ($('gbaHealthSearchInput')?.value || '').trim().toLowerCase();
      const list = activeHealthTab === 'issues'
        ? (healthData.issue_list || [])
        : activeHealthTab === 'chd'
          ? (healthData.chd_list || [])
          : activeHealthTab === 'reclassify'
            ? (healthData.reclassify_list || [])
            : activeHealthTab === 'unverified'
              ? (healthData.unverified_list || [])
              : (healthData.unsupported_list || []);
      const confidenceLabel = (score) => metadataConfidenceLabel(score || 0);
      
      const filtered = list.filter((item) => {
        if (!q) return true;
        return (item.title || '').toLowerCase().includes(q) || (item.filename || '').toLowerCase().includes(q) || (item.reason || '').toLowerCase().includes(q);
      });

      if (filtered.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="3" style="text-align: center; padding: 24px; color: var(--gba-text-muted);">
              ${q ? '검색된 항목이 없습니다.' : (activeHealthTab === 'issues' ? '🎉 파일 또는 BIOS 보완이 필요한 게임이 없습니다.' : activeHealthTab === 'chd' ? '🎉 CHD/디스크 이미지가 필요한 게임이 없습니다.' : activeHealthTab === 'reclassify' ? '🎉 기종 재분류가 필요한 게임이 없습니다.' : activeHealthTab === 'unverified' ? '🎉 판정 미확인 게임이 없습니다.' : '🎉 현재 EmulatorJS Stable 코어에서 구동 불가로 판정된 게임이 없습니다.')}
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = filtered.map((item) => {
        const isIssue = activeHealthTab === 'issues';
        const isUnsupported = activeHealthTab === 'unsupported';
        const isReclassify = activeHealthTab === 'reclassify';
        const isUnverified = activeHealthTab === 'unverified';
        const badgeColor = isIssue
          ? 'background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid rgba(245, 158, 11, 0.3);'
          : isUnsupported
            ? 'background: rgba(124, 58, 237, 0.15); color: #a78bfa; border: 1px solid rgba(124, 58, 237, 0.3);'
            : isReclassify
              ? 'background: rgba(234, 88, 12, 0.15); color: #fb923c; border: 1px solid rgba(234, 88, 12, 0.3);'
              : isUnverified
                ? 'background: rgba(107, 114, 128, 0.15); color: #9ca3af; border: 1px solid rgba(107, 114, 128, 0.3);'
                : 'background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3);';
        const metaInfo = [];
        if (item.metadata_source) metaInfo.push(`출처: ${item.metadata_source}`);
        if (item.metadata_confidence) metaInfo.push(`신뢰도: ${confidenceLabel(item.metadata_confidence)} (${item.metadata_confidence})`);
        if (item.source_system) metaInfo.push(`근거: ${item.source_system}`);
        return `
          <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
            <td style="padding: 10px; font-weight: 600; color: var(--gba-text-main);">
              ${escapeHtml(item.title)}
            </td>
            <td style="padding: 10px; color: var(--gba-text-muted); font-family: monospace; font-size: 0.8rem;">
              ${escapeHtml(item.filename)}
            </td>
            <td style="padding: 10px;">
              <span style="display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; ${badgeColor}">
                ${escapeHtml(item.reason)}
              </span>
              ${metaInfo.length ? `<div style="margin-top: 6px; font-size: 0.78rem; color: var(--gba-text-muted);">${escapeHtml(metaInfo.join(' · '))}</div>` : ''}
              <button type="button" class="gba-analysis-icon-btn" data-health-analysis-id="${escapeHtml(String(item.id || ''))}" title="ROM 분석 상세 보기" aria-label="ROM 분석 상세 보기" style="margin-top: 6px;"><i class="fa-solid fa-microscope"></i></button>
            </td>
          </tr>
        `;
      }).join('');
      tbody.querySelectorAll('[data-health-analysis-id]').forEach((btn) => {
        btn.addEventListener('click', () => showRomAnalysis({ id: btn.dataset.healthAnalysisId }));
      });
    }

    let analysisProgressPollTimer = null;
    let analysisProgressRequestInFlight = false;
    let analysisProgressWasActive = false;
    let analysisCompletionHandled = false;

    function analysisProgressValues(progress = {}) {
      const current = Math.max(0, Number(progress.current || 0));
      const total = Math.max(0, Number(progress.total || 0));
      const percent = total > 0 ? Math.min(100, Math.round(current * 100 / total)) : 0;
      const cached = Math.max(0, Number(progress.cached || 0));
      const failed = Math.max(0, Number(progress.failed || 0));
      return { current, total, percent, cached, failed };
    }

    function isAnalysisProgressActive(progress = {}) {
      return !!progress.is_running || progress.status === 'queued';
    }

    function updateAnalysisProgressUi(progress = {}) {
      const badge = $('gbaAnalysisProgressBadge');
      const badgeText = $('gbaAnalysisProgressText');
      const modal = $('gbaHealthCheckModal');
      const loading = $('gbaHealthCheckLoading');
      const loadingText = loading?.querySelector('p');
      const result = $('gbaHealthCheckResult');
      const active = isAnalysisProgressActive(progress);
      const { current, total, percent, cached, failed } = analysisProgressValues(progress);

      if (badge) {
        if (state.isAdmin && active) {
          badge.style.display = 'inline-flex';
          if (badgeText) {
            badgeText.textContent = total > 0
              ? `ROM 분석 중 ${current.toLocaleString()}/${total.toLocaleString()} · ${percent}%`
              : 'ROM 분석 준비 중...';
          }
          const stats = `캐시 재사용 ${cached.toLocaleString()}${failed > 0 ? ` · 실패 ${failed.toLocaleString()}` : ''}`;
          badge.title = total > 0
            ? `백그라운드 ROM 분석 ${current.toLocaleString()} / ${total.toLocaleString()} (${percent}%) · ${stats}\n현재: ${progress.current_file || '준비 중...'}\n클릭하면 진행 화면을 다시 엽니다.`
            : '백그라운드 ROM 분석 준비 중입니다. 클릭하면 진행 화면을 다시 엽니다.';
        } else {
          badge.style.display = 'none';
        }
      }

      if (modal?.style.display === 'flex' && active) {
        if (loading) loading.style.display = 'block';
        if (result) result.style.display = 'none';
        if (loadingText) {
          const stats = `캐시 재사용 ${cached.toLocaleString()}${failed > 0 ? ` · 실패 ${failed.toLocaleString()}` : ''}`;
          loadingText.textContent = total > 0
            ? `ROM 분석 갱신 중... ${current.toLocaleString()} / ${total.toLocaleString()} (${percent}%) · ${stats} · ${progress.current_file || ''}`
            : 'ROM 분석 준비 중...';
        }
      }
    }

    function renderHealthResult(res) {
      healthData = res;
      if ($('gbaHealthPassCount')) $('gbaHealthPassCount').textContent = res.summary?.pass || 0;
      if ($('gbaHealthIncompleteCount')) $('gbaHealthIncompleteCount').textContent = res.summary?.issues || 0;
      if ($('gbaHealthChdCount')) $('gbaHealthChdCount').textContent = res.summary?.chd || 0;
      if ($('gbaHealthReclassifyCount')) $('gbaHealthReclassifyCount').textContent = res.summary?.reclassify || 0;
      if ($('gbaHealthUnsupportedCount')) $('gbaHealthUnsupportedCount').textContent = res.summary?.unsupported || 0;
      if ($('gbaHealthUnverifiedCount')) $('gbaHealthUnverifiedCount').textContent = res.summary?.unverified || 0;

      if ($('gbaHealthIncompleteTabCount')) $('gbaHealthIncompleteTabCount').textContent = res.summary?.issues || 0;
      if ($('gbaHealthChdTabCount')) $('gbaHealthChdTabCount').textContent = res.summary?.chd || 0;
      if ($('gbaHealthReclassifyTabCount')) $('gbaHealthReclassifyTabCount').textContent = res.summary?.reclassify || 0;
      if ($('gbaHealthUnsupportedTabCount')) $('gbaHealthUnsupportedTabCount').textContent = res.summary?.unsupported || 0;
      if ($('gbaHealthUnverifiedTabCount')) $('gbaHealthUnverifiedTabCount').textContent = res.summary?.unverified || 0;

      if ($('gbaHealthCheckLoading')) $('gbaHealthCheckLoading').style.display = 'none';
      if ($('gbaHealthCheckResult')) $('gbaHealthCheckResult').style.display = 'block';
      renderHealthTable();
    }

    async function loadHealthResult({ refreshLibrary = false, showCompletionToast = false, progress = null } = {}) {
      const res = await apiCall('health_check');
      if (!res || !res.success) {
        throw new Error(res && res.error ? res.error : 'ROM 분석 결과 조회 실패');
      }
      renderHealthResult(res);
      if (refreshLibrary) await loadLibrary(true);
      if (showCompletionToast) {
        const cached = Number(progress?.cached ?? res.progress?.cached ?? 0);
        const failed = Number(progress?.failed ?? res.progress?.failed ?? 0);
        showToast(`ROM 분석 갱신 완료: ${res.summary?.total || 0}개 확인 · 캐시 재사용 ${cached}${failed > 0 ? ` · 실패 ${failed}` : ''}`);
      }
      return res;
    }

    function scheduleAnalysisProgressPoll(delay = 1000) {
      if (analysisProgressPollTimer) return;
      analysisProgressPollTimer = setTimeout(() => {
        analysisProgressPollTimer = null;
        pollAnalysisProgress();
      }, delay);
    }

    async function pollAnalysisProgress() {
      if (analysisProgressRequestInFlight) return;
      if (!state.isAdmin) {
        updateAnalysisProgressUi({});
        return;
      }

      analysisProgressRequestInFlight = true;
      try {
        const progressRes = await apiCall('health_progress');
        if (!progressRes || !progressRes.success) return;
        const progress = progressRes.progress || {};
        const active = isAnalysisProgressActive(progress);
        updateAnalysisProgressUi(progress);

        if (active) {
          analysisProgressWasActive = true;
          analysisCompletionHandled = false;
          scheduleAnalysisProgressPoll(1000);
          return;
        }

        if (analysisProgressWasActive && !analysisCompletionHandled && progress.status === 'completed') {
          analysisCompletionHandled = true;
          analysisProgressWasActive = false;
          await loadHealthResult({ refreshLibrary: true, showCompletionToast: true, progress });
          updateAnalysisProgressUi(progress);
          return;
        }

        if (analysisProgressWasActive && !analysisCompletionHandled && progress.status === 'error') {
          analysisCompletionHandled = true;
          analysisProgressWasActive = false;
          updateAnalysisProgressUi(progress);
          const message = progress.current_file || 'ROM 분석 갱신 중 오류가 발생했습니다.';
          showToast('ROM 분석 갱신 중 오류가 발생했습니다: ' + message, true);
          if ($('gbaHealthCheckModal')?.style.display === 'flex') {
            const loadingText = $('gbaHealthCheckLoading')?.querySelector('p');
            if (loadingText) loadingText.textContent = `ROM 분석 오류: ${message}`;
          }
        }
      } catch (err) {
        console.warn('[GBA] ROM analysis progress polling error:', err);
        if (analysisProgressWasActive) scheduleAnalysisProgressPoll(2000);
      } finally {
        analysisProgressRequestInFlight = false;
      }
    }

    function startAnalysisProgressMonitor() {
      if (analysisProgressPollTimer || analysisProgressRequestInFlight) return;
      pollAnalysisProgress();
    }

    async function openHealthAnalysisModal({ startIfIdle = false } = {}) {
      const modal = $('gbaHealthCheckModal');
      const loading = $('gbaHealthCheckLoading');
      const loadingText = loading?.querySelector('p');
      const result = $('gbaHealthCheckResult');
      if (!modal) return;

      modal.style.display = 'flex';
      if (loading) loading.style.display = 'block';
      if (result) result.style.display = 'none';

      try {
        const progressRes = await apiCall('health_progress');
        if (!progressRes || !progressRes.success) throw new Error('ROM 분석 진행 상태 확인 실패');
        let progress = progressRes.progress || {};

        if (isAnalysisProgressActive(progress)) {
          analysisProgressWasActive = true;
          analysisCompletionHandled = false;
          updateAnalysisProgressUi(progress);
          startAnalysisProgressMonitor();
          return;
        }

        if (!startIfIdle) {
          if (progress.status === 'completed' || healthData) {
            await loadHealthResult();
          } else if (loadingText) {
            loadingText.textContent = '현재 진행 중인 전체 ROM 분석이 없습니다.';
          }
          return;
        }

        if (loadingText) loadingText.textContent = '전체 ROM 분석 갱신을 시작하는 중입니다...';
        const startRes = await apiCall('library_sync', { mode: 'diagnose' });
        if (!startRes || !startRes.success) {
          throw new Error(startRes && startRes.error ? startRes.error : '전체 ROM 분석 시작 실패');
        }
        progress = startRes.progress || { status: 'queued', is_running: false };
        analysisProgressWasActive = true;
        analysisCompletionHandled = false;
        updateAnalysisProgressUi(progress);
        startAnalysisProgressMonitor();
        setTimeout(pollAnalysisProgress, 50);
      } catch (err) {
        console.error('[GBA] ROM analysis modal error:', err);
        showToast('ROM 분석 갱신 중 오류가 발생했습니다: ' + (err.message || err), true);
        modal.style.display = 'none';
      }
    }

    $('gbaHealthCheckBtn')?.addEventListener('click', () => openHealthAnalysisModal({ startIfIdle: true }));
    $('gbaAnalysisProgressBadge')?.addEventListener('click', () => openHealthAnalysisModal({ startIfIdle: false }));

    $('gbaHealthCheckCloseBtn')?.addEventListener('click', () => {
      $('gbaHealthCheckModal').style.display = 'none';
    });
    $('gbaHealthCheckOkBtn')?.addEventListener('click', () => {
      $('gbaHealthCheckModal').style.display = 'none';
    });

    // 모달과 독립된 감시기. 최초 라이브러리 로드 후 한 번 확인하고,
    // 실제 분석이 진행 중인 동안에만 1초 폴링을 유지한다.
    state.analysisProgressMonitorStart = startAnalysisProgressMonitor;

    document.querySelectorAll('.gba-health-tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.gba-health-tab-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        activeHealthTab = btn.dataset.tab;
        renderHealthTable();
      });
    });

    $('gbaHealthSearchInput')?.addEventListener('input', () => {
      renderHealthTable();
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

    // 조작키 기종별 드롭다운 선택
    $('gbaControlsSysSelect')?.addEventListener('change', (e) => {
      renderControlsTable(e.target.value || 'snes');
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
        padStatusEl.innerHTML = `<i class="fa-solid fa-gamepad"></i> <span>연결된 컨트롤러: <strong>${escapeHtml(foundPad.id)}</strong> · 아래 표 기준 자동 매핑 적용</span>`;
        padStatusEl.style.color = 'var(--gba-success)';
      } else {
        padStatusEl.innerHTML = `<i class="fa-solid fa-gamepad"></i> <span>컨트롤러 연결 시 아래 표 기준으로 자동 매핑됩니다.</span>`;
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

      const controlsSysSelect = $('gbaControlsSysSelect');
      if (controlsSysSelect) controlsSysSelect.value = targetSys;
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
    $('gbaSettingsBtn').addEventListener('click', async () => {
      try {
        await ensureRuntimeGlobals();
      } catch (err) {
        console.warn('[GBA] Settings runtime state load error:', err);
      }
      $('gbaSettingCloudSave').checked = state.config.cloud_save_enabled;
      $('gbaSettingInterval').value = state.config.auto_save_interval_sec;
      if ($('gbaSettingEmulatorjsRoot')) {
        $('gbaSettingEmulatorjsRoot').value = state.config.emulatorjs_root || '/mnt/gdrive/emulatorjs';
      }
      if ($('gbaSettingExtraPath')) {
        $('gbaSettingExtraPath').value = state.config.extra_roms_path || '';
      }
      if ($('gbaSettingCoversPath')) {
        $('gbaSettingCoversPath').value = state.config.covers_path || '';
      }
      if ($('gbaSettingBiosPath')) {
        $('gbaSettingBiosPath').value = state.config.bios_path || '';
      }
      if (state.isAdmin) {
        try {
          await loadDeleteQueue(false);
        } catch (err) {
          console.warn('[GBA] Delete queue status load error:', err);
        }
      }

      $('gbaSettingsModal').style.display = 'flex';
    });

    $('gbaDeleteQueueBtn')?.addEventListener('click', async () => {
      try {
        await loadDeleteQueue(true);
      } catch (err) {
        showToast(err.message || '삭제 대기 목록을 불러오지 못했습니다.', true);
      }
    });
    $('gbaDeleteQueueCloseBtn')?.addEventListener('click', () => {
      $('gbaDeleteQueueModal').style.display = 'none';
    });
    $('gbaDeleteQueueOkBtn')?.addEventListener('click', () => {
      $('gbaDeleteQueueModal').style.display = 'none';
    });

    $('gbaPhase6PreflightBtn')?.addEventListener('click', () => runPhase6Preflight(false));
    $('gbaPhase6PreflightCloseBtn')?.addEventListener('click', () => { $('gbaPhase6PreflightModal').style.display = 'none'; });
    $('gbaPhase6PreflightOkBtn')?.addEventListener('click', () => { $('gbaPhase6PreflightModal').style.display = 'none'; });
    $('gbaPhase6RepairBtn')?.addEventListener('click', async () => {
      if (!confirm('ROM 파일은 이동하지 않고 DB 매핑, 고아 사용자 기록, 복구 가능한 stale 경로만 안전 복구합니다. 계속할까요?')) return;
      await runPhase6Preflight(true);
    });
    $('gbaPhase6BackupBtn')?.addEventListener('click', createPhase6Backup);
    $('gbaCoverWebpBtn')?.addEventListener('click', startCoverWebpRefresh);

    $('gbaSettingsCloseBtn').addEventListener('click', () => {
      $('gbaSettingsModal').style.display = 'none';
    });
    $('gbaSettingsCancelBtn').addEventListener('click', () => {
      $('gbaSettingsModal').style.display = 'none';
    });

    $('gbaSettingsSaveBtn').addEventListener('click', async () => {
      const emulatorjsRoot = $('gbaSettingEmulatorjsRoot') ? $('gbaSettingEmulatorjsRoot').value.trim() : '';
      const extraPath = $('gbaSettingExtraPath') ? $('gbaSettingExtraPath').value.trim() : '';
      const coversPath = $('gbaSettingCoversPath') ? $('gbaSettingCoversPath').value.trim() : '';
      const biosPath = $('gbaSettingBiosPath') ? $('gbaSettingBiosPath').value.trim() : '';
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
          emulatorjs_root: emulatorjsRoot,
          extra_roms_path: extraPath,
          covers_path: coversPath,
          bios_path: biosPath,
          cloud_save_enabled: cloudSave,
          auto_save_interval_sec: interval,
        });

        if (res && res.success) {
          state.config.emulatorjs_root = emulatorjsRoot;
          state.config.extra_roms_path = extraPath;
          state.config.covers_path = coversPath;
          state.config.bios_path = biosPath;
          state.config.cloud_save_enabled = cloudSave === '1';
          state.config.auto_save_interval_sec = parseInt(interval, 10) || 60;
          try {
            await refreshRuntimeGlobals(state.games);
          } catch (err) {
            console.warn('[GBA] Runtime globals refresh after settings save failed:', err);
          }
          renderGames();
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

  function formatBytes(bytes) {
    const num = Number(bytes || 0);
    if (!Number.isFinite(num) || num <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = num;
    let idx = 0;
    while (value >= 1024 && idx < units.length - 1) {
      value /= 1024;
      idx += 1;
    }
    const digits = value >= 100 || idx === 0 ? 0 : 1;
    return `${value.toFixed(digits)} ${units[idx]}`;
  }

  function setLaunchProgress(phase, title, desc = '', meta = '', percent = null) {
    if (launchSlowTimerId) {
      clearTimeout(launchSlowTimerId);
      launchSlowTimerId = null;
    }

    state.launchProgress = {
      visible: true,
      phase: phase || 'loading',
      title: title || '에뮬레이터 준비 중...',
      desc: desc || '잠시만 기다려 주세요.',
      meta: meta || '',
      percent: Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null,
    };

    const overlay = $('gbaLaunchOverlay');
    const titleEl = $('gbaLaunchTitle');
    const descEl = $('gbaLaunchDesc');
    const metaEl = $('gbaLaunchMeta');
    const barEl = $('gbaLaunchProgressBar');
    const actionsEl = $('gbaLaunchActions');
    const retryBtn = $('gbaLaunchRetryBtn');
    const closeBtn = $('gbaLaunchCloseBtn');
    if (!overlay || !titleEl || !descEl || !metaEl || !barEl) return;

    const isError = state.launchProgress.phase === 'error';
    overlay.style.display = 'flex';
    overlay.classList.toggle('is-error', isError);
    titleEl.textContent = state.launchProgress.title;
    descEl.textContent = state.launchProgress.desc;
    metaEl.textContent = state.launchProgress.meta || '실행 준비 상태를 확인하고 있습니다.';
    if (actionsEl) actionsEl.style.display = isError ? 'flex' : 'none';

    if (retryBtn) {
      retryBtn.onclick = () => {
        const game = state.activeGame;
        if (game) launchGame(game);
      };
    }
    if (closeBtn) closeBtn.onclick = abortLaunchUi;

    if (isError) {
      barEl.classList.remove('indeterminate');
      barEl.style.width = '100%';
    } else if (state.launchProgress.percent === null) {
      barEl.classList.add('indeterminate');
      barEl.style.width = '';
    } else {
      barEl.classList.remove('indeterminate');
      barEl.style.width = `${state.launchProgress.percent}%`;
    }

    if (!isError && state.launchProgress.percent === null && state.launchProgress.phase !== 'started') {
      const expectedPhase = state.launchProgress.phase;
      launchSlowTimerId = setTimeout(() => {
        if (!state.launchProgress.visible || state.launchProgress.phase !== expectedPhase) return;
        const current = state.launchProgress.meta || '작업을 계속 진행하고 있습니다.';
        const suffix = '응답이 평소보다 오래 걸리고 있습니다.';
        if (!current.includes(suffix)) metaEl.textContent = `${current} · ${suffix}`;
      }, 8000);
    }
  }

  function hideLaunchProgress() {
    if (launchSlowTimerId) {
      clearTimeout(launchSlowTimerId);
      launchSlowTimerId = null;
    }
    state.launchProgress.visible = false;
    state.launchProgress.phase = 'idle';
    state.launchProgress.percent = null;
    const overlay = $('gbaLaunchOverlay');
    const barEl = $('gbaLaunchProgressBar');
    const actionsEl = $('gbaLaunchActions');
    if (overlay) {
      overlay.style.display = 'none';
      overlay.classList.remove('is-error');
    }
    if (actionsEl) actionsEl.style.display = 'none';
    if (barEl) {
      barEl.classList.add('indeterminate');
      barEl.style.width = '';
    }
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
