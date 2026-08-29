(function () {
  'use strict';

  const root = container.querySelector('[data-statistics-root]');
  if (!root) return;
  if (container.__statisticsCleanup) {
    try { container.__statisticsCleanup(); } catch (_) {}
  }

  const ACTION_URL = '/api/media/context-menu/book/plugins/action';
  const ECHARTS_URL = 'https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js';
  const MUURI_URL = 'https://cdn.jsdelivr.net/npm/muuri@0.9.5/dist/muuri.min.js';
  const SUPPORTED_SESSIONS = new Set(['general', 'adult', 'audiobook', 'video']);
  const rawSessionType = String(window.currentLibraryType || 'general').trim().toLowerCase();
  const SESSION_TYPE = SUPPORTED_SESSIONS.has(rawSessionType) ? rawSessionType : 'general';
  const ITEM_UNIT = SESSION_TYPE === 'general' || SESSION_TYPE === 'adult' ? '권' : '개';
  const CARD_ORDER_KEY = `bookoasis.statistics.cardOrder.v2.${SESSION_TYPE}`;
  const SESSION_UI = {
    general: { hidden: [], titles: {} },
    adult: { hidden: [], titles: {} },
    audiobook: {
      hidden: ['genres', 'genre-chord', 'top-series'],
      titles: {
        format: '트랙 포맷 분포',
        largest: '용량이 큰 오디오북',
        'storage-format': '트랙 포맷별 저장 공간',
        'added-time': '오디오북 추가 추이',
        'publication-decade': '출시 시대',
        'page-count': '트랙 수 분포',
        'publication-timeline': '출시 연도 타임라인',
        'format-time': '트랙 포맷 비중 변화'
      }
    },
    video: {
      hidden: ['top-authors', 'top-series', 'top-publishers'],
      titles: {
        format: '에피소드 포맷 분포',
        largest: '용량이 큰 비디오',
        'storage-format': '에피소드 포맷별 저장 공간',
        'added-time': '비디오 추가 추이',
        'publication-decade': '출시 시대',
        'page-count': '에피소드 수 분포',
        'publication-timeline': '출시 연도 타임라인',
        'format-time': '에피소드 포맷 비중 변화'
      }
    }
  };
  const selectEl = root.querySelector('[data-role="library-select"]');
  const refreshEl = root.querySelector('[data-role="refresh"]');
  const layoutResetEl = root.querySelector('[data-role="layout-reset"]');
  const statusEl = root.querySelector('[data-role="status"]');
  const statusTextEl = root.querySelector('[data-role="status-text"]');
  const kpisEl = root.querySelector('[data-role="kpis"]');
  const chartsEl = root.querySelector('[data-role="charts"]');
  const emptyEl = root.querySelector('[data-role="empty"]');
  const charts = new Map();
  const layoutMedia = window.matchMedia('(min-width: 721px)');
  applySessionUi();
  const defaultCardOrder = Array.from(chartsEl.querySelectorAll('[data-card]:not([hidden])')).map((el) => el.dataset.card);
  let cardGrid = null;
  let cardResizeObserver = null;
  let layoutRefreshFrame = 0;
  let latestState = null;
  let currentScopeId = 'all';
  let pollTimer = null;
  let disposed = false;

  function applySessionUi() {
    const settings = SESSION_UI[SESSION_TYPE] || SESSION_UI.general;
    const hidden = new Set(settings.hidden || []);
    chartsEl.querySelectorAll('[data-card]').forEach((card) => {
      const cardId = card.dataset.card;
      card.hidden = hidden.has(cardId);
      const title = card.querySelector('h2');
      if (title && settings.titles[cardId]) title.textContent = settings.titles[cardId];
    });
    const subtitle = root.querySelector('.bo-stats__hero p');
    const sessionLabels = { adult: '성인 도서', audiobook: '오디오북', video: '비디오' };
    if (subtitle && sessionLabels[SESSION_TYPE]) {
      subtitle.textContent = `BookOasis ${sessionLabels[SESSION_TYPE]} 라이브러리를 백그라운드에서 미리 집계한 결과입니다.`;
    }
  }

  function loadMuuri() {
    if (window.Muuri) return Promise.resolve(window.Muuri);
    if (window.__bookoasisStatisticsMuuriPromise) return window.__bookoasisStatisticsMuuriPromise;
    window.__bookoasisStatisticsMuuriPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-bookoasis-statistics-muuri="0.9.5"]');
      if (existing) {
        existing.addEventListener('load', () => resolve(window.Muuri), { once: true });
        existing.addEventListener('error', () => reject(new Error('Muuri 로드 실패')), { once: true });
        return;
      }
      const script = document.createElement('script');
      script.src = MUURI_URL;
      script.async = true;
      script.dataset.bookoasisStatisticsMuuri = '0.9.5';
      script.onload = () => window.Muuri ? resolve(window.Muuri) : reject(new Error('Muuri 초기화 실패'));
      script.onerror = () => reject(new Error('Muuri CDN을 불러오지 못했습니다.'));
      document.head.appendChild(script);
    });
    return window.__bookoasisStatisticsMuuriPromise;
  }

  function readStoredCardOrder() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(CARD_ORDER_KEY) || '[]');
      return Array.isArray(parsed) ? parsed.filter((value) => typeof value === 'string') : [];
    } catch (_) {
      return [];
    }
  }

  function reorderCardDom(order) {
    const byId = new Map(Array.from(chartsEl.querySelectorAll('[data-card]')).map((el) => [el.dataset.card, el]));
    const seen = new Set();
    (order || []).forEach((cardId) => {
      const el = byId.get(cardId);
      if (el && !seen.has(cardId)) {
        chartsEl.appendChild(el);
        seen.add(cardId);
      }
    });
    defaultCardOrder.forEach((cardId) => {
      const el = byId.get(cardId);
      if (el && !seen.has(cardId)) chartsEl.appendChild(el);
    });
  }

  function prepareCardDragHandles() {
    chartsEl.querySelectorAll('.bo-stats__card').forEach((card) => {
      const header = card.querySelector('header');
      if (!header || header.querySelector('[data-role="card-drag-handle"]')) return;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'bo-stats__drag-handle';
      handle.dataset.role = 'card-drag-handle';
      handle.title = '드래그하여 카드 이동';
      handle.setAttribute('aria-label', `${card.querySelector('h2')?.textContent || '통계'} 카드 이동`);
      const icon = document.createElement('i');
      icon.className = 'fa-solid fa-grip-vertical';
      icon.setAttribute('aria-hidden', 'true');
      handle.appendChild(icon);
      header.appendChild(handle);
    });
  }

  function saveCardOrder() {
    const order = cardGrid
      ? cardGrid.getItems().map((item) => item.getElement().dataset.card).filter(Boolean)
      : Array.from(chartsEl.querySelectorAll('[data-card]')).map((el) => el.dataset.card);
    try { window.localStorage.setItem(CARD_ORDER_KEY, JSON.stringify(order)); } catch (_) {}
  }

  function resizeAllCharts() {
    charts.forEach((chart) => { try { chart.resize(); } catch (_) {} });
  }

  function cardDragStartPredicate(item, event) {
    const target = event && (event.target || (event.srcEvent && event.srcEvent.target));
    if (!target || !target.closest || !target.closest('[data-role="card-drag-handle"]')) return false;
    if (!window.Muuri || !window.Muuri.ItemDrag) return false;
    return window.Muuri.ItemDrag.defaultStartPredicate(item, event, { distance: 4, delay: 0 });
  }

  function forceCardLayout() {
    if (!cardGrid) return;
    try { cardGrid.refreshItems().layout(true); } catch (_) {}
  }

  function scheduleCardLayout() {
    if (!cardGrid || disposed) return;
    if (layoutRefreshFrame) window.cancelAnimationFrame(layoutRefreshFrame);
    layoutRefreshFrame = window.requestAnimationFrame(() => {
      layoutRefreshFrame = 0;
      forceCardLayout();
    });
  }

  function startCardResizeObserver() {
    if (cardResizeObserver || typeof ResizeObserver === 'undefined') return;
    cardResizeObserver = new ResizeObserver(() => scheduleCardLayout());
    chartsEl.querySelectorAll('.bo-stats__card').forEach((card) => cardResizeObserver.observe(card));
  }

  function stopCardResizeObserver() {
    if (cardResizeObserver) {
      try { cardResizeObserver.disconnect(); } catch (_) {}
      cardResizeObserver = null;
    }
    if (layoutRefreshFrame) {
      window.cancelAnimationFrame(layoutRefreshFrame);
      layoutRefreshFrame = 0;
    }
  }

  function destroyCardGrid() {
    stopCardResizeObserver();
    if (!cardGrid) {
      chartsEl.classList.remove('bo-stats__charts--muuri');
      return;
    }
    try { cardGrid.synchronize(); } catch (_) {}
    try { cardGrid.destroy(); } catch (_) {}
    cardGrid = null;
    chartsEl.classList.remove('bo-stats__charts--muuri');
    resizeAllCharts();
  }

  function initCardGrid() {
    if (disposed || chartsEl.hidden || !layoutMedia.matches || !window.Muuri) return;
    if (cardGrid) {
      forceCardLayout()
      return;
    }
    chartsEl.classList.add('bo-stats__charts--muuri');
    cardGrid = new window.Muuri(chartsEl, {
      items: '.bo-stats__card:not([hidden])',
      dragEnabled: true,
      layout: {
        fillGaps: true,
        horizontal: false,
        alignRight: false,
        alignBottom: false,
        rounding: true
      },
      dragStartPredicate: cardDragStartPredicate,
      layoutDuration: 260,
      layoutEasing: 'ease',
      dragSortInterval: 45,
      dragRelease: { duration: 220, easing: 'ease' }
    });
    cardGrid.on('move', saveCardOrder);
    cardGrid.on('dragReleaseEnd', () => {
      try { cardGrid.synchronize(); } catch (_) {}
      saveCardOrder();
    });
    cardGrid.on('layoutEnd', resizeAllCharts);
    startCardResizeObserver();
    window.requestAnimationFrame(() => forceCardLayout());
  }

  function syncCardLayoutMode() {
    if (layoutMedia.matches && window.Muuri && !chartsEl.hidden) initCardGrid();
    else destroyCardGrid();
  }

  function refreshCardLayout() {
    if (!cardGrid) return;
    scheduleCardLayout();
    window.setTimeout(() => {
      if (cardGrid && !disposed) forceCardLayout();
    }, 80);
  }

  function resetCardLayout() {
    try { window.localStorage.removeItem(CARD_ORDER_KEY); } catch (_) {}
    destroyCardGrid();
    reorderCardDom(defaultCardOrder);
    syncCardLayoutMode();
  }

  reorderCardDom(readStoredCardOrder());
  prepareCardDragHandles();

  function css(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function theme() {
    return {
      text: css('--app-text-primary', '#f8fafc'),
      secondary: css('--app-text-secondary', '#cbd5e1'),
      muted: css('--app-text-muted', '#94a3b8'),
      border: css('--app-border', 'rgba(255,255,255,.1)'),
      borderLight: css('--app-border-light', 'rgba(255,255,255,.06)'),
      accent: css('--app-accent', '#8b5cf6'),
      card: css('--app-bg-card', '#171a24')
    };
  }

  function formatNumber(value) {
    return new Intl.NumberFormat('ko-KR').format(Number(value || 0));
  }

  function formatItemCount(value) {
    return formatNumber(value) + ITEM_UNIT;
  }

  function formatBytes(value) {
    let bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let idx = 0;
    while (bytes >= 1024 && idx < units.length - 1) { bytes /= 1024; idx += 1; }
    const digits = idx >= 3 ? 2 : (idx >= 2 ? 1 : 0);
    return bytes.toFixed(digits).replace(/\.0+$/, '') + ' ' + units[idx];
  }

  function formatDuration(value) {
    const seconds = Math.max(0, Number(value || 0));
    const hours = Math.floor(seconds / 3600);
    const days = Math.floor(hours / 24);
    const remainingHours = hours % 24;
    if (days) return `${formatNumber(days)}일 ${formatNumber(remainingHours)}시간`;
    if (hours) return `${formatNumber(hours)}시간`;
    return `${Math.floor(seconds / 60)}분`;
  }

  function storageScale(maxBytes) {
    const bytes = Math.max(0, Number(maxBytes || 0));
    const gb = 1024 * 1024 * 1024;
    const mb = 1024 * 1024;
    const useGb = bytes >= gb;
    const divisor = useGb ? gb : mb;
    const unit = useGb ? 'GB' : 'MB';
    const maxValue = divisor ? bytes / divisor : 0;
    const digits = maxValue < 10 ? 2 : (maxValue < 100 ? 1 : 0);
    return { divisor, unit, digits };
  }

  function escapeTooltip(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function rpc(op) {
    const response = await fetch(ACTION_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        type: SESSION_TYPE,
        plugin_id: pluginId,
        action_id: 'statistics_rpc',
        context: { op: op || 'snapshot' }
      })
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok || !body.success) {
      throw new Error(body.error || ('통계 요청 실패: HTTP ' + response.status));
    }
    return body;
  }

  function loadEcharts() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (window.__bookoasisStatisticsEchartsPromise) return window.__bookoasisStatisticsEchartsPromise;
    window.__bookoasisStatisticsEchartsPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-bookoasis-statistics-echarts="6.1.0"]');
      if (existing) {
        existing.addEventListener('load', () => resolve(window.echarts), { once: true });
        existing.addEventListener('error', () => reject(new Error('ECharts 로드 실패')), { once: true });
        return;
      }
      const script = document.createElement('script');
      script.src = ECHARTS_URL;
      script.async = true;
      script.dataset.bookoasisStatisticsEcharts = '6.1.0';
      script.onload = () => window.echarts ? resolve(window.echarts) : reject(new Error('ECharts 초기화 실패'));
      script.onerror = () => reject(new Error('ECharts CDN을 불러오지 못했습니다.'));
      document.head.appendChild(script);
    });
    return window.__bookoasisStatisticsEchartsPromise;
  }

  function setStatus(state, message) {
    statusEl.dataset.state = state || 'idle';
    statusTextEl.textContent = message || '';
  }

  function statusMessage(state) {
    const snap = state && state.snapshot;
    const generated = snap && snap.generated_at ? new Date(snap.generated_at) : null;
    const when = generated && !Number.isNaN(generated.getTime())
      ? generated.toLocaleString('ko-KR', { hour12: false }) : '';
    const took = snap && snap.generation_ms != null ? ` · 집계 ${formatNumber(snap.generation_ms)}ms` : '';
    if (state && state.status === 'refreshing') return `백그라운드에서 통계를 갱신 중입니다.${when ? ' · 마지막 정상 집계 ' + when : ''}`;
    if (state && state.status === 'error') return `최근 갱신 실패: ${state.last_error || '알 수 없는 오류'}${when ? ' · 마지막 정상 집계 ' + when : ''}`;
    if (!snap) return '최초 통계를 백그라운드에서 집계하고 있습니다.';
    if (state && state.refresh_scheduled) return `통계 재집계가 예약되어 있습니다. · 현재 표시 기준 ${when}${took}`;
    return `마지막 집계 ${when}${took}`;
  }

  function populateLibraries(snapshot) {
    const previous = currentScopeId;
    selectEl.textContent = '';
    const all = document.createElement('option');
    all.value = 'all';
    all.textContent = '전체 보관함';
    selectEl.appendChild(all);
    (snapshot.libraries || []).forEach((library) => {
      const option = document.createElement('option');
      option.value = String(library.id);
      option.textContent = library.name || ('보관함 #' + library.id);
      selectEl.appendChild(option);
    });
    const valid = Array.from(selectEl.options).some((option) => option.value === previous);
    currentScopeId = valid ? previous : 'all';
    selectEl.value = currentScopeId;
  }

  function renderKpis(summary) {
    const pubRange = summary.publication_year_min && summary.publication_year_max
      ? `${summary.publication_year_min}–${summary.publication_year_max}` : '–';
    let items = [
      ['fa-book', '도서', formatNumber(summary.book_count)],
      ['fa-user-pen', '저자', formatNumber(summary.author_count)],
      ['fa-layer-group', '시리즈', formatNumber(summary.series_count)],
      ['fa-building', '출판사', formatNumber(summary.publisher_count)],
      ['fa-hard-drive', '저장 공간', formatBytes(summary.storage_bytes)],
      ['fa-tags', '장르', formatNumber(summary.genre_count)],
      ['fa-book-open', '보관함', formatNumber(summary.library_count)],
      ['fa-calendar-days', '출판 연도', pubRange],
      ['fa-arrow-trend-up', '올해 추가', formatNumber(summary.added_this_year)]
    ];
    if (SESSION_TYPE === 'adult') {
      items[0][1] = '성인 도서';
    } else if (SESSION_TYPE === 'audiobook') {
      items = [
        ['fa-headphones', '오디오북', formatNumber(summary.item_count)],
        ['fa-user-pen', '저자', formatNumber(summary.author_count)],
        ['fa-list-ol', '트랙', formatNumber(summary.child_count)],
        ['fa-building', '출판사', formatNumber(summary.publisher_count)],
        ['fa-hard-drive', '저장 공간', formatBytes(summary.storage_bytes)],
        ['fa-clock', '총 재생시간', formatDuration(summary.duration_seconds)],
        ['fa-book-open', '보관함', formatNumber(summary.library_count)],
        ['fa-calendar-days', '출시 연도', pubRange],
        ['fa-arrow-trend-up', '올해 추가', formatNumber(summary.added_this_year)]
      ];
    } else if (SESSION_TYPE === 'video') {
      items = [
        ['fa-film', '비디오', formatNumber(summary.item_count)],
        ['fa-list-ol', '에피소드', formatNumber(summary.child_count)],
        ['fa-hard-drive', '저장 공간', formatBytes(summary.storage_bytes)],
        ['fa-clock', '총 재생시간', formatDuration(summary.duration_seconds)],
        ['fa-tags', '장르', formatNumber(summary.genre_count)],
        ['fa-book-open', '보관함', formatNumber(summary.library_count)],
        ['fa-calendar-days', '출시 연도', pubRange],
        ['fa-arrow-trend-up', '올해 추가', formatNumber(summary.added_this_year)]
      ];
    }
    kpisEl.textContent = '';
    items.forEach(([icon, label, value]) => {
      const card = document.createElement('div');
      card.className = 'bo-stats__kpi';
      const labelEl = document.createElement('div');
      labelEl.className = 'bo-stats__kpi-label';
      const iconEl = document.createElement('i');
      iconEl.className = `fa-solid ${icon}`;
      const textEl = document.createElement('span');
      textEl.textContent = label;
      labelEl.append(iconEl, textEl);
      const valueEl = document.createElement('div');
      valueEl.className = 'bo-stats__kpi-value';
      valueEl.title = String(value);
      valueEl.textContent = String(value);
      card.append(labelEl, valueEl);
      kpisEl.appendChild(card);
    });
  }

  function disposeChart(key) {
    const chart = charts.get(key);
    if (chart) {
      try { chart.dispose(); } catch (_) {}
      charts.delete(key);
    }
  }

  function emptyChart(key, message) {
    disposeChart(key);
    const el = root.querySelector(`[data-chart="${key}"]`);
    if (!el) return;
    el.textContent = '';
    const empty = document.createElement('div');
    empty.className = 'bo-stats__chart-empty';
    empty.textContent = message || '표시할 데이터가 없습니다.';
    el.appendChild(empty);
  }

  function makeChart(key, option) {
    const el = root.querySelector(`[data-chart="${key}"]`);
    if (!el || !window.echarts) return;
    disposeChart(key);
    el.textContent = '';
    const chart = window.echarts.init(el, null, { renderer: 'svg' });
    chart.setOption(option, { notMerge: true });
    charts.set(key, chart);
  }

  function baseOption() {
    const t = theme();
    return {
      animationDuration: 450,
      backgroundColor: 'transparent',
      textStyle: { color: t.secondary, fontFamily: 'inherit' },
      tooltip: {
        confine: true,
        backgroundColor: t.card,
        borderColor: t.border,
        textStyle: { color: t.text, fontSize: 11 }
      }
    };
  }

  function axisStyle() {
    const t = theme();
    return {
      axisLine: { lineStyle: { color: t.border } },
      axisTick: { show: false },
      axisLabel: { color: t.muted, fontSize: 10 },
      splitLine: { lineStyle: { color: t.borderLight } }
    };
  }

  function pieOption(rows, valueKey, valueFormatter) {
    const t = theme();
    return Object.assign(baseOption(), {
      tooltip: Object.assign(baseOption().tooltip, {
        trigger: 'item',
        formatter: (p) => `${escapeTooltip(p.name)}<br><b>${valueFormatter(p.value)}</b> · ${p.percent}%`
      }),
      legend: { type: 'scroll', bottom: 4, textStyle: { color: t.muted, fontSize: 10 } },
      series: [{
        type: 'pie', radius: ['46%', '69%'], center: ['50%', '43%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: t.card, borderWidth: 2, borderRadius: 3 },
        label: { show: false }, emphasis: { scaleSize: 5 },
        data: rows.map((row) => ({ name: row.label, value: Number(row[valueKey] || 0) }))
      }]
    });
  }

  function horizontalBarOption(rows, valueFormatter, maxRows) {
    const t = theme();
    const data = (rows || []).slice(0, maxRows || 15).slice().reverse();
    return Object.assign(baseOption(), {
      grid: { left: 12, right: 20, top: 12, bottom: 12, containLabel: true },
      tooltip: { ...baseOption().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (params) => {
        const p = params[0]; return `${escapeTooltip(p.name)}<br><b>${valueFormatter(p.value)}</b>`;
      } },
      xAxis: { type: 'value', ...axisStyle(), splitLine: { lineStyle: { color: t.borderLight } } },
      yAxis: { type: 'category', data: data.map((row) => row.label || row.title || '–'), ...axisStyle(), axisLabel: { color: t.muted, fontSize: 10, width: 125, overflow: 'truncate' } },
      series: [{ type: 'bar', data: data.map((row) => Number(row.count != null ? row.count : row.bytes || 0)), barMaxWidth: 16, itemStyle: { borderRadius: [0, 4, 4, 0] } }]
    });
  }

  function largestBooksOption(rows) {
    const t = theme();
    const data = (rows || []).slice(0, 12).slice().reverse();
    const maxBytes = data.reduce((max, row) => Math.max(max, Number(row.bytes || 0)), 0);
    const largestStorageScale = storageScale(maxBytes);
    const scale = largestStorageScale;
    const scaled = data.map((row) => ({
      name: row.label || row.title || '–',
      rawBytes: Number(row.bytes || 0),
      value: Number(row.bytes || 0) / scale.divisor
    }));
    const numberLabel = (value) => {
      const n = Number(value || 0);
      if (!Number.isFinite(n)) return '';
      return n.toFixed(scale.digits).replace(/\.0+$/, '');
    };
    return Object.assign(baseOption(), {
      grid: { left: 12, right: 24, top: 12, bottom: 18, containLabel: true },
      tooltip: {
        ...baseOption().tooltip,
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const p = params[0];
          const rawBytes = p && p.data ? Number(p.data.rawBytes || 0) : 0;
          return `${escapeTooltip(p.name)}<br><b>${formatBytes(rawBytes)}</b>`;
        }
      },
      xAxis: {
        type: 'value',
        ...axisStyle(),
        axisLabel: { color: t.muted, fontSize: 9, formatter: (value) => `${numberLabel(value)} ${scale.unit}` },
        splitLine: { lineStyle: { color: t.borderLight } }
      },
      yAxis: {
        type: 'category',
        data: scaled.map((row) => row.name),
        ...axisStyle(),
        axisLabel: { color: t.muted, fontSize: 10, width: 132, overflow: 'truncate' }
      },
      series: [{
        type: 'bar',
        data: scaled.map((row) => ({ value: row.value, rawBytes: row.rawBytes })),
        barMaxWidth: 16,
        itemStyle: { borderRadius: [0, 4, 4, 0] }
      }]
    });
  }

  function renderCharts(scope) {
    if (!window.echarts) return;
    const t = theme();

    if ((scope.format_distribution || []).length) makeChart('format', pieOption(scope.format_distribution, 'count', formatNumber));
    else emptyChart('format');

    const completeness = scope.metadata_completeness || [];
    if (completeness.length) {
      const avg = completeness.reduce((sum, row) => sum + Number(row.percent || 0), 0) / completeness.length;
      makeChart('metadata-gauge', Object.assign(baseOption(), {
        series: [{
          type: 'gauge', startAngle: 205, endAngle: -25, min: 0, max: 100,
          radius: '82%', center: ['50%', '58%'],
          progress: { show: true, width: 13 }, axisLine: { lineStyle: { width: 13, color: [[1, t.borderLight]] } },
          axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, pointer: { show: false },
          detail: { valueAnimation: true, formatter: '{value}%', color: t.text, fontSize: 25, fontWeight: 800, offsetCenter: [0, '5%'] },
          title: { color: t.muted, fontSize: 10, offsetCenter: [0, '35%'] },
          data: [{ value: Math.round(avg * 10) / 10, name: '평균 필드 완성도' }]
        }]
      }));
    } else emptyChart('metadata-gauge');

    const score = scope.metadata_score_distribution || [];
    if (score.some((row) => row.count)) {
      makeChart('metadata-score', Object.assign(baseOption(), {
        grid: { left: 40, right: 12, top: 18, bottom: 32 },
        tooltip: { ...baseOption().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'category', data: score.map((r) => r.label), ...axisStyle() },
        yAxis: { type: 'value', ...axisStyle() },
        series: [{ type: 'bar', data: score.map((r) => r.count), barMaxWidth: 28, itemStyle: { borderRadius: [4, 4, 0, 0] } }]
      }));
    } else emptyChart('metadata-score');

    const genres = scope.genre_distribution || [];
    if (genres.length) {
      makeChart('genres', Object.assign(baseOption(), {
        tooltip: { ...baseOption().tooltip, formatter: (p) => `${escapeTooltip(p.name)}<br><b>${formatItemCount(p.value)}</b>` },
        series: [{ type: 'treemap', roam: false, nodeClick: false, breadcrumb: { show: false }, label: { show: true, color: '#fff', fontSize: 11 }, upperLabel: { show: false }, itemStyle: { borderColor: t.card, borderWidth: 2, gapWidth: 2 }, data: genres.map((row) => ({ name: row.label, value: row.count })) }]
      }));
    } else emptyChart('genres');

    const largest = scope.largest_books || [];
    if (largest.length) makeChart('largest', largestBooksOption(largest));
    else emptyChart('largest');

    const heat = scope.library_metadata_completeness || {};
    if ((heat.libraries || []).length && (heat.fields || []).length) {
      const heatVisibleRows = 15;
      const heatNeedsScroll = heat.libraries.length > heatVisibleRows;
      const heatEndPercent = heatNeedsScroll
        ? Math.max(0, Math.min(100, (heatVisibleRows / heat.libraries.length) * 100))
        : 100;
      const heatDataZoom = heatNeedsScroll ? [
        {
          type: 'inside',
          yAxisIndex: 0,
          start: 0,
          end: heatEndPercent,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: true
        },
        {
          type: 'slider',
          yAxisIndex: 0,
          orient: 'vertical',
          right: 5,
          top: 26,
          bottom: 72,
          width: 11,
          start: 0,
          end: heatEndPercent,
          showDetail: false,
          brushSelect: false,
          borderColor: t.border,
          fillerColor: 'rgba(148,163,184,.16)',
          backgroundColor: 'rgba(148,163,184,.05)'
        }
      ] : [];
      makeChart('metadata-heatmap', Object.assign(baseOption(), {
        grid: { left: 105, right: heatNeedsScroll ? 58 : 34, top: 24, bottom: 68 },
        tooltip: { ...baseOption().tooltip, position: 'top', formatter: (p) => {
          const field = heat.fields[p.value[0]]; const lib = heat.libraries[p.value[1]];
          return `${escapeTooltip(lib ? lib.name : '')}<br>${escapeTooltip(field ? field.label : '')}: <b>${p.value[2]}%</b>`;
        } },
        xAxis: { type: 'category', data: heat.fields.map((f) => f.label), ...axisStyle(), axisLabel: { color: t.muted, fontSize: 10, rotate: 35 } },
        yAxis: { type: 'category', data: heat.libraries.map((l) => l.name), ...axisStyle(), axisLabel: { color: t.muted, fontSize: 10, width: 88, overflow: 'truncate' } },
        dataZoom: heatDataZoom,
        visualMap: { min: 0, max: 100, calculable: false, orient: 'horizontal', left: 'center', bottom: 3, text: ['100%', '0%'], textStyle: { color: t.muted, fontSize: 9 }, inRange: { color: ['#ef4444', '#f59e0b', t.accent] } },
        series: [{ type: 'heatmap', data: heat.values || [], label: { show: true, formatter: (p) => Math.round(p.value[2]) + '%', fontSize: 9 }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,.25)' } } }]
      }));
    } else emptyChart('metadata-heatmap');

    if ((scope.storage_by_format || []).length) makeChart('storage-format', pieOption(scope.storage_by_format, 'bytes', formatBytes));
    else emptyChart('storage-format');

    const chord = scope.genre_cooccurrence || {};
    if ((chord.nodes || []).length > 1 && (chord.links || []).length) {
      makeChart('genre-chord', Object.assign(baseOption(), {
        tooltip: { ...baseOption().tooltip, formatter: (p) => p.dataType === 'edge' ? `${escapeTooltip(p.data.source)} ↔ ${escapeTooltip(p.data.target)}<br><b>${formatItemCount(p.data.value)}</b>` : `${escapeTooltip(p.name)}<br><b>${formatItemCount(p.data.value)}</b>` },
        series: [{ type: 'chord', clockwise: false, radius: ['22%', '67%'], center: ['50%', '51%'], label: { show: true, color: t.secondary, fontSize: 9 }, lineStyle: { color: 'target', opacity: .55 }, emphasis: { focus: 'adjacency' }, data: chord.nodes, links: chord.links }]
      }));
    } else emptyChart('genre-chord', '두 개 이상의 장르가 함께 지정된 항목이 충분하지 않습니다.');

    if ((scope.top_series || []).length) makeChart('top-series', horizontalBarOption(scope.top_series, formatItemCount, 15));
    else emptyChart('top-series');

    const added = scope.books_added_over_time || [];
    if (added.length) {
      makeChart('added-time', Object.assign(baseOption(), {
        grid: { left: 50, right: 20, top: 20, bottom: added.length > 24 ? 55 : 32 },
        tooltip: { ...baseOption().tooltip, trigger: 'axis' },
        xAxis: { type: 'category', boundaryGap: false, data: added.map((r) => r.period), ...axisStyle() },
        yAxis: { type: 'value', ...axisStyle() },
        dataZoom: added.length > 24 ? [{ type: 'inside', start: 45, end: 100 }, { type: 'slider', height: 14, bottom: 6, borderColor: t.border, textStyle: { color: t.muted } }] : [],
        series: [{ type: 'line', smooth: true, symbol: 'none', data: added.map((r) => r.count), areaStyle: { opacity: .10 }, lineStyle: { width: 2 } }]
      }));
    } else emptyChart('added-time');

    if ((scope.top_authors || []).length) makeChart('top-authors', horizontalBarOption(scope.top_authors, formatItemCount, 15));
    else emptyChart('top-authors');

    const decades = scope.publication_decade || [];
    if (decades.length) {
      makeChart('publication-decade', Object.assign(baseOption(), {
        grid: { left: 48, right: 15, top: 18, bottom: 35 },
        tooltip: { ...baseOption().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'category', data: decades.map((r) => r.label), ...axisStyle(), axisLabel: { color: t.muted, fontSize: 9, rotate: decades.length > 10 ? 35 : 0 } },
        yAxis: { type: 'value', ...axisStyle() },
        series: [{ type: 'bar', data: decades.map((r) => r.count), barMaxWidth: 24, itemStyle: { borderRadius: [4,4,0,0] } }]
      }));
    } else emptyChart('publication-decade');

    const pages = scope.page_count_distribution || [];
    if (pages.some((r) => r.count)) {
      makeChart('page-count', Object.assign(baseOption(), {
        grid: { left: 48, right: 14, top: 18, bottom: 35 },
        tooltip: { ...baseOption().tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'category', data: pages.map((r) => r.label), ...axisStyle(), axisLabel: { color: t.muted, fontSize: 9 } },
        yAxis: { type: 'value', ...axisStyle() },
        series: [{ type: 'bar', data: pages.map((r) => r.count), barMaxWidth: 30, itemStyle: { borderRadius: [4,4,0,0] } }]
      }));
    } else emptyChart('page-count');

    if ((scope.top_publishers || []).length) makeChart('top-publishers', horizontalBarOption(scope.top_publishers, formatItemCount, 15));
    else emptyChart('top-publishers');

    const metadataMissing = scope.metadata_missing || [];
    if (metadataMissing.length) makeChart('metadata-missing', horizontalBarOption(metadataMissing, formatItemCount, 8));
    else emptyChart('metadata-missing', '누락된 주요 메타데이터가 없습니다.');

    const years = scope.publication_year_timeline || [];
    if (years.length) {
      makeChart('publication-timeline', Object.assign(baseOption(), {
        grid: { left: 50, right: 20, top: 20, bottom: years.length > 25 ? 55 : 32 },
        tooltip: { ...baseOption().tooltip, trigger: 'axis' },
        xAxis: { type: 'category', boundaryGap: false, data: years.map((r) => String(r.year)), ...axisStyle() },
        yAxis: { type: 'value', ...axisStyle() },
        dataZoom: years.length > 25 ? [{ type: 'inside', start: 55, end: 100 }, { type: 'slider', height: 14, bottom: 6, borderColor: t.border, textStyle: { color: t.muted } }] : [],
        series: [{ type: 'line', smooth: .18, showSymbol: years.length < 30, symbolSize: 5, data: years.map((r) => r.count), areaStyle: { opacity: .08 }, lineStyle: { width: 2 } }]
      }));
    } else emptyChart('publication-timeline');

    const fmtTime = scope.format_share_over_time || [];
    if (fmtTime.length) {
      const periods = Array.from(new Set(fmtTime.map((r) => r.period))).sort();
      const totals = {};
      const periodTotals = {};
      fmtTime.forEach((r) => {
        const count = Number(r.count || 0);
        totals[r.label] = (totals[r.label] || 0) + count;
        periodTotals[r.period] = (periodTotals[r.period] || 0) + count;
      });
      const formats = Object.keys(totals).sort((a, b) => totals[b] - totals[a]).slice(0, 8);
      const matrix = {};
      fmtTime.forEach((r) => { if (formats.includes(r.label)) matrix[r.period + '\u0000' + r.label] = Number(r.count || 0); });
      makeChart('format-time', Object.assign(baseOption(), {
        grid: { left: 50, right: 20, top: 30, bottom: periods.length > 24 ? 65 : 38 },
        legend: { type: 'scroll', top: 2, textStyle: { color: t.muted, fontSize: 9 } },
        tooltip: { ...baseOption().tooltip, trigger: 'axis', valueFormatter: (value) => Number(value || 0).toFixed(1) + '%' },
        xAxis: { type: 'category', boundaryGap: false, data: periods, ...axisStyle() },
        yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: t.muted, fontSize: 10, formatter: '{value}%' }, axisLine: { lineStyle: { color: t.border } }, axisTick: { show: false }, splitLine: { lineStyle: { color: t.borderLight } } },
        dataZoom: periods.length > 24 ? [{ type: 'inside', start: 45, end: 100 }, { type: 'slider', height: 14, bottom: 6, borderColor: t.border, textStyle: { color: t.muted } }] : [],
        series: formats.map((label) => ({
          name: label,
          type: 'line',
          stack: 'formats',
          symbol: 'none',
          areaStyle: { opacity: .12 },
          lineStyle: { width: 1.4 },
          data: periods.map((period) => {
            const denominator = periodTotals[period] || 0;
            const value = matrix[period + '\u0000' + label] || 0;
            return denominator ? Math.round((value / denominator) * 1000) / 10 : 0;
          })
        }))
      }));
    } else emptyChart('format-time');
    refreshCardLayout();
  }

  function renderSnapshot(snapshot) {
    if (!snapshot || !snapshot.scopes || !snapshot.scopes.all) {
      kpisEl.textContent = '';
      chartsEl.hidden = true;
      destroyCardGrid();
      emptyEl.hidden = false;
      return;
    }
    populateLibraries(snapshot);
    const scope = snapshot.scopes[currentScopeId] || snapshot.scopes.all;
    renderKpis(scope.summary || {});
    emptyEl.hidden = true;
    chartsEl.hidden = false;
    syncCardLayoutMode();
    if (window.echarts) renderCharts(scope);
  }

  function updateState(state) {
    latestState = state;
    const stateName = state.status === 'error' ? 'error' : (state.status === 'refreshing' ? 'refreshing' : 'idle');
    setStatus(stateName, statusMessage(state));
    refreshEl.disabled = state.status === 'refreshing';
    refreshEl.classList.toggle('is-spinning', state.status === 'refreshing');
    renderSnapshot(state.snapshot);
  }

  async function poll() {
    if (disposed) return;
    try {
      const state = await rpc('snapshot');
      updateState(state);
    } catch (error) {
      setStatus('error', error.message || String(error));
    } finally {
      if (!disposed) {
        const fast = !latestState || !latestState.snapshot || latestState.status === 'refreshing' || latestState.refresh_scheduled;
        pollTimer = window.setTimeout(poll, fast ? 3500 : 30000);
      }
    }
  }

  selectEl.addEventListener('change', () => {
    currentScopeId = selectEl.value || 'all';
    if (latestState && latestState.snapshot) {
      const scope = latestState.snapshot.scopes[currentScopeId] || latestState.snapshot.scopes.all;
      renderKpis(scope.summary || {});
      if (window.echarts) renderCharts(scope);
    }
  });

  refreshEl.addEventListener('click', async () => {
    refreshEl.disabled = true;
    refreshEl.classList.add('is-spinning');
    setStatus('refreshing', '통계 재집계를 요청하고 있습니다...');
    try {
      const state = await rpc('refresh');
      updateState(state);
      window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(poll, 900);
    } catch (error) {
      setStatus('error', error.message || String(error));
      refreshEl.disabled = false;
      refreshEl.classList.remove('is-spinning');
    }
  });

  if (layoutResetEl) layoutResetEl.addEventListener('click', resetCardLayout);

  const layoutMediaHandler = () => syncCardLayoutMode();
  if (layoutMedia.addEventListener) layoutMedia.addEventListener('change', layoutMediaHandler);
  else if (layoutMedia.addListener) layoutMedia.addListener(layoutMediaHandler);

  const resizeHandler = () => {
    resizeAllCharts();
    refreshCardLayout();
  };
  window.addEventListener('resize', resizeHandler, { passive: true });

  let themeTimer = null;
  const themeObserver = new MutationObserver(() => {
    window.clearTimeout(themeTimer);
    themeTimer = window.setTimeout(() => {
      if (latestState && latestState.snapshot && window.echarts) {
        const scope = latestState.snapshot.scopes[currentScopeId] || latestState.snapshot.scopes.all;
        renderCharts(scope);
      }
    }, 80);
  });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });

  const removalObserver = new MutationObserver(() => {
    if (!container.isConnected) cleanup();
  });
  if (document.body) removalObserver.observe(document.body, { childList: true, subtree: true });

  function cleanup() {
    if (disposed) return;
    disposed = true;
    window.clearTimeout(pollTimer);
    window.clearTimeout(themeTimer);
    window.removeEventListener('resize', resizeHandler);
    if (layoutMedia.removeEventListener) layoutMedia.removeEventListener('change', layoutMediaHandler);
    else if (layoutMedia.removeListener) layoutMedia.removeListener(layoutMediaHandler);
    destroyCardGrid();
    themeObserver.disconnect();
    removalObserver.disconnect();
    charts.forEach((chart) => { try { chart.dispose(); } catch (_) {} });
    charts.clear();
  }
  container.__statisticsCleanup = cleanup;

  Promise.allSettled([loadEcharts(), loadMuuri(), rpc('snapshot')]).then((results) => {
    if (disposed) return;
    const chartResult = results[0];
    const muuriResult = results[1];
    const stateResult = results[2];
    if (stateResult.status === 'fulfilled') updateState(stateResult.value);
    else setStatus('error', stateResult.reason && stateResult.reason.message ? stateResult.reason.message : '통계를 불러오지 못했습니다.');
    if (chartResult.status === 'rejected') {
      setStatus('error', '통계 데이터는 준비되었지만 ECharts를 불러오지 못했습니다: ' + (chartResult.reason && chartResult.reason.message ? chartResult.reason.message : 'CDN 오류'));
    } else if (latestState && latestState.snapshot) {
      const scope = latestState.snapshot.scopes[currentScopeId] || latestState.snapshot.scopes.all;
      renderCharts(scope);
    }
    if (muuriResult.status === 'fulfilled') syncCardLayoutMode();
    else console.warn('[Statistics] Muuri layout unavailable; CSS grid fallback is active.', muuriResult.reason);
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(poll, (!latestState || !latestState.snapshot) ? 2500 : 30000);
  });
})();
