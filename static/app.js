/* ===== 智能课表助手 · 前端逻辑 ===== */
const API = {
  state: '/api/state',
  settings: '/api/settings',
  week: '/api/timetable',
  parse: '/api/parse',
  preview: '/api/preview',
  apply: '/api/apply',
  del: id => `/api/adjustments/${id}`,
  clear: '/api/adjustments/clear',
  ocrParse: '/api/ocr_parse',
  importCsv: '/api/import_courses'
};

const SAMPLE_NOTICE = `各单位：
为更好满足学生的现实需求，根据《国务院办公厅关于2026年部分节假日安排的通知》精神，现将2026年中秋节、国庆节放假及调休安排通知如下：
一、放假时间
2026年9月25日（星期五）至10月7日（星期三），共13天。
二、调休补课安排
1.9月20日（星期日）：按10月6日（星期二）课表上课；
2.10月10日（星期六）：按10月7日（星期三）课表上课。`;

const WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const KIND_CN = { holiday: '放假', makeup: '补课', cancel: '停课', move: '调课', room_change: '换教室' };
// 默认节次时间表（后端兜底值，实际以 state.settings.section_times 为准）
const DEFAULT_SECTION_TIMES = [
  { start: '08:00', end: '08:50' }, { start: '08:55', end: '09:45' },
  { start: '10:00', end: '10:50' }, { start: '10:55', end: '11:45' },
  { start: '14:00', end: '14:50' }, { start: '14:55', end: '15:45' },
  { start: '16:00', end: '16:50' }, { start: '16:55', end: '17:45' },
  { start: '19:00', end: '19:50' }, { start: '19:55', end: '20:45' },
  { start: '20:50', end: '21:40' }, { start: '21:45', end: '22:35' },
];
// 获取第 n 节（1-based）的起止时间
function sectionTime(n) {
  const arr = (state.settings && state.settings.section_times) || DEFAULT_SECTION_TIMES;
  const t = arr[n - 1] || { start: '', end: '' };
  return t;
}
// 课程背景色：12 色，色相均匀分布且两两差异最大化（最小色距 20.5）
const PALETTE = [
  '#F2CACA', // 浅红
  '#F9ECD2', // 浅杏
  '#E7F5D6', // 浅黄绿
  '#D9F7C5', // 浅草绿
  '#C7F4D9', // 浅绿
  '#D6F5E7', // 浅青绿
  '#C5F7F3', // 浅青
  '#D6E2F5', // 浅蓝
  '#D2C5F7', // 浅靛
  '#E4D2F4', // 浅紫
  '#F9D2F6', // 浅品红
  '#F5D6DE', // 浅粉
];

let state = { courses: [], adjustments: [], settings: { term_start: '2026-09-14', total_weeks: 20 }, today: '' };
let weekData = null;
let currentWeek = 1;
let pendingOps = [];        // 待应用的操作（文字指令）
let ocrOps = [];            // 图片识别得到的操作
let pendingSource = 'manual';

/* ---------- 工具 ---------- */
const $ = s => document.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2000);
}
async function api(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || '请求失败');
  return data;
}
const post = (url, body) => api(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
});
function colorOf(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 99991;
  return PALETTE[h % PALETTE.length];
}
function parseDate(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function weekOf(dateStr, termStart) {
  const a = parseDate(dateStr), b = parseDate(termStart);
  return Math.floor((a - b) / 86400000 / 7) + 1;
}
function mondayOfWeek(w) {
  const d = parseDate(state.settings.term_start);
  d.setDate(d.getDate() + (w - 1) * 7);
  return d;
}

/* ---------- 数据加载 ---------- */
async function loadState() {
  state = await api(API.state);
  $('#termStart').value = state.settings.term_start;
  $('#totalWeeks').value = state.settings.total_weeks;
  currentWeek = Math.min(Math.max(weekOf(state.today, state.settings.term_start), 1), state.settings.total_weeks);
  renderRules();
  await loadWeek(currentWeek);
}

/* ---------- 图例：仅保留状态图例（调入/已调走/停课/换教室），渲染在课表顶部右侧 ---------- */
function renderLegendStatus() {
  const box = $('#legendStatus');
  if (!box) return;
  box.innerHTML = '';
  const items = [
    { status: 'moved-in', cls: 'brand', label: '调入' },
    { status: 'moved-out', cls: 'amber', label: '已调走' },
    { status: 'cancelled', cls: 'red', label: '停课' },
    { status: 'room-change', cls: 'green', label: '换教室' },
  ];
  items.forEach(it => {
    const span = el('span', 'legend-item');
    span.dataset.status = it.status;
    span.innerHTML = `<i class="tag-dot ${it.cls}"></i>${it.label}`;
    span.addEventListener('mouseenter', () => {
      document.querySelectorAll(`.course.${it.status}`).forEach(c => c.classList.add('legend-highlight'));
    });
    span.addEventListener('mouseleave', () => {
      document.querySelectorAll(`.course.${it.status}`).forEach(c => c.classList.remove('legend-highlight'));
    });
    box.appendChild(span);
  });
}

async function loadWeek(w) {
  const total = state.settings.total_weeks;
  currentWeek = Math.min(Math.max(w, 1), total);
  const tt = $('#timetable');
  const ws = document.querySelector('.week-switch');
  tt.classList.add('fade');            // 课表淡出
  if (ws) { ws.style.opacity = '0'; }  // 周切换区淡出
  // 等待一帧，确保浏览器绘制 opacity:0 后再请求数据
  await new Promise(r => requestAnimationFrame(r));
  weekData = await api(`${API.week}?week=${currentWeek}`);
  renderTimetable();
  renderWeekBar();
  // 下一帧淡入
  requestAnimationFrame(() => {
    tt.classList.remove('fade');
    if (ws) { ws.style.opacity = '1'; }
  });
}

/* ---------- 课表渲染 ---------- */
function renderWeekBar() {
  const mon = mondayOfWeek(currentWeek);
  const sun = new Date(mon); sun.setDate(sun.getDate() + 6);
  const isThisWeek = currentWeek === weekOf(state.today, state.settings.term_start);
  const weekText = `第 ${currentWeek} 周（${iso(mon).slice(5)} ~ ${iso(sun).slice(5)}）`;
  $('#weekLabel').innerHTML = weekText + (isThisWeek ? ' <span style="display:inline-block;margin-left:6px;padding:1px 8px;border-radius:999px;background:var(--brand-soft);color:var(--brand);font-size:11px;font-weight:700;vertical-align:middle">本周</span>' : '');
  $('#weekRange').textContent = '';

  const count = weekData.days.reduce((n, d) => n + d.items.filter(i => i.status !== 'moved_out').length, 0);
  $('#pillCount').textContent = `${count} 节课`;

  // 渲染状态图例到顶部右侧
  renderLegendStatus();

  const box = $('#weekAlert');
  box.innerHTML = '';
  // 调休提示去重：同一条安排（同类型+同日期+同参考）只显示一次
  const seen = new Set();
  const notes = [];
  weekData.days.forEach(d => {
    d.flags.forEach(f => {
      if (f.type === 'holiday') {
        const key = `h-${d.date}`;
        if (!seen.has(key)) { seen.add(key); notes.push(`<span class="pill red">${d.date.slice(5)} 放假</span>`); }
      }
      if (f.type === 'makeup') {
        const key = `m-${d.date}-${f.label}`;
        if (!seen.has(key)) { seen.add(key); notes.push(`<span class="pill amber">${d.date.slice(5)} ${f.label}</span>`); }
      }
    });
  });
  if (notes.length) {
    box.innerHTML = `<div class="alert"><span>⏰</span><div>本周有调休安排：${notes.join(' ')}</div></div>`;
  }
}

function renderTimetable() {
  const wrap = $('#timetable');
  wrap.innerHTML = '';
  const SECTIONS = ((state.settings && state.settings.section_times) || DEFAULT_SECTION_TIMES).length;
  const todayIso = state.today;

  // 计算每天需要的列数（处理同一时段多门课重叠）
  let maxCol = 1;
  weekData.days.forEach(d => {
    const items = d.items.slice().sort((a, b) => a.start - b.start);
    const colEnd = [];
    items.forEach(it => {
      let placed = false;
      for (let i = 0; i < colEnd.length; i++) {
        if (it.start > colEnd[i]) { colEnd[i] = it.end; it._col = i; placed = true; break; }
      }
      if (!placed) { colEnd.push(it.end); it._col = colEnd.length - 1; }
    });
    d._cols = Math.max(colEnd.length, 1);
    maxCol = Math.max(maxCol, d._cols);
  });

  // 固定列宽：时间列 64px，每天列硬编码 128px，禁用 fr 自动分配，保证缩放/文字变化时列宽稳定
  const cols = `64px repeat(7, 128px)`;

  // 表头
  const head = el('div', 'tt-head');
  head.style.gridTemplateColumns = cols;
  head.appendChild(el('div', 'tt-cell-time', '<b>节次</b>'));
  weekData.days.forEach(d => {
    const cls = ['day-head'];
    if (d.date === todayIso) cls.push('today');
    if (d.flags.some(f => f.type === 'holiday')) cls.push('holiday');
    else if (d.flags.some(f => f.type === 'makeup')) cls.push('makeup');
    const flag = d.flags.find(f => f.type === 'holiday' || f.type === 'makeup');
    const badge = flag ? `<span class="badge-mini ${flag.type}">${flag.type === 'holiday' ? '放假' : '补课'}</span>` : '';
    const n = el('div', cls.join(' '),
      `<div class="dw">${WEEKDAY_CN[d.weekday - 1]}</div><div class="dd">${d.date.slice(5).replace('-', '/')}</div>${badge}`);
    head.appendChild(n);
  });
  wrap.appendChild(head);

  // 网格主体：每天一列容器（行高 1fr 自动填满，与左侧面板等高）
  const body = el('div', 'tt-row');
  body.style.gridTemplateColumns = cols;
  const timeCol = el('div', 'time-col');
  for (let s = 1; s <= SECTIONS; s++) {
    // 节次数字 + 起止时间（从 settings.section_times 动态获取，导入文件时自动解析）
    const t = sectionTime(s);
    timeCol.appendChild(el('div', 'tt-cell-time', `<b>${s}</b><span>${t.start || ''}-${t.end || ''}</span>`));
  }
  body.appendChild(timeCol);

  weekData.days.forEach(d => {
    const col = el('div', 'day-col' + (d.flags.some(f => f.type === 'holiday') ? ' is-holiday' : ''));
    col.style.gridTemplateColumns = `repeat(${maxCol}, minmax(0, 1fr))`;
    d.items.forEach(it => {
      const c = el('div', 'course');
      c.style.gridRow = `${it.start} / ${it.end + 1}`;
      c.style.gridColumn = `${it._col + 1} / span ${maxCol - d._cols + 1}`;
      c.style.background = colorOf(it.course_name);
      if (it.status === 'cancelled') c.classList.add('cancelled');
      if (it.status === 'moved_out') c.classList.add('moved-out');
      if (it.status === 'moved_in') c.classList.add('moved-in');
      if (it.tags.includes('换教室')) c.classList.add('room-change');
      let tag = '';
      if (it.status === 'cancelled') tag = '<span class="tag red">停课</span>';
      else if (it.status === 'moved_out') tag = '<span class="tag amber">已调走</span>';
      else if (it.status === 'moved_in') tag = '<span class="tag brand">调入</span>';
      else if (it.tags.includes('换教室')) tag = '<span class="tag green">换教室</span>';
      c.innerHTML = `${tag}<div class="cn">${it.course_name}</div>` +
        (it.classroom ? `<div class="cm">${it.classroom}</div>` : '');
      // 点击弹窗展示课程完整信息
      c.onclick = () => openCourseDetail(it, d);
      col.appendChild(c);
    });
    // 当日无课：浅灰占位提示
    if (d.items.length === 0 && !d.flags.some(f => f.type === 'holiday')) {
      col.appendChild(el('div', 'empty-day', '当日无课'));
    }
    body.appendChild(col);
  });
  wrap.appendChild(body);
}

/* ---------- 课程详情弹窗 ---------- */
function openCourseDetail(it, d) {
  const statusMap = {
    normal: '正常',
    cancelled: '停课',
    moved_out: '已调走',
    moved_in: '调入',
  };
  const statusText = statusMap[it.status] || (it.tags.includes('换教室') ? '换教室' : '正常');
  const st = sectionTime(it.start), et = sectionTime(it.end);
  const secText = `第 ${it.start}–${it.end} 节（${st.start || ''} ~ ${et.end || ''}）`;
  const wdText = `${WEEKDAY_CN[d.weekday - 1]}（${d.date}）`;
  const body = $('#courseModalBody');
  body.innerHTML = `
    <div class="course-info">
      <div class="row"><div class="k">课程名</div><div class="v bold">${it.course_name}</div></div>
      <div class="row"><div class="k">星期</div><div class="v">${wdText}</div></div>
      <div class="row"><div class="k">节次</div><div class="v">${secText}</div></div>
      <div class="row"><div class="k">地点</div><div class="v">${it.classroom || '—'}</div></div>
      <div class="row"><div class="k">状态</div><div class="v">${statusText}</div></div>
    </div>`;
  $('#courseModalTitle').textContent = '课程详情';
  $('#courseMask').classList.add('show');
}

/* ---------- 操作列表渲染 ---------- */
function renderOps(container, ops, removable, onChange) {
  container.innerHTML = '';
  if (!ops.length) { container.innerHTML = '<div class="empty">暂无识别结果</div>'; return; }
  ops.forEach((op, i) => {
    const row = el('div', 'op');
    row.appendChild(el('span', `kind ${op.kind}`, KIND_CN[op.kind] || op.kind));
    const body = el('div', 'body', `<div class="lbl">${op.label}</div>`);
    row.appendChild(body);
    if (removable) {
      const del = el('button', 'del', '×');
      del.title = '移除这一条';
      del.onclick = () => { ops.splice(i, 1); onChange && onChange(); renderOps(container, ops, removable, onChange); };
      row.appendChild(del);
    }
    container.appendChild(row);
  });
}

function renderRules() {
  const box = $('#ruleList');
  const list = state.adjustments || [];
  if (!list.length) { box.innerHTML = '<div class="empty">还没有任何调整，试试上传通知或输入一句指令</div>'; return; }
  box.innerHTML = '';
  list.slice().reverse().forEach(a => {
    const row = el('div', 'rule');
    row.appendChild(el('span', `kind ${a.kind}`, KIND_CN[a.kind] || a.kind));
    const src = { ocr: '图片识别', text: '文字指令', manual: '手动' }[a.source] || a.source;
    row.appendChild(el('div', 'body', `<div class="lbl">${a.label}</div><div class="src">来自${src} · ${a.created_at || ''}</div>`));
    const del = el('button', 'del', '×');
    del.onclick = async () => {
      const res = await api(API.del(a.id), { method: 'DELETE' });
      state.adjustments = res.adjustments || [];
      renderRules();
      loadWeek(currentWeek);
      toast('已删除该调整');
    };
    row.appendChild(del);
    box.appendChild(row);
  });
}

/* ---------- 变更预览 ---------- */
async function openPreview(ops, source) {
  if (!ops.length) { toast('没有可应用的调整'); return; }
  const data = await post(API.preview, { ops });
  pendingOps = ops; pendingSource = source;
  const body = $('#modalBody');
  body.innerHTML = '';
  if (!data.changes.length) {
    body.innerHTML = '<div class="empty">这些调整不会影响任何上课日</div>';
  } else {
    data.changes.slice(0, 40).forEach(c => {
      const d = el('div', 'diff');
      const wd = WEEKDAY_CN[c.weekday - 1];
      let head = `${c.date} ${wd}（第${c.week}周）`;
      const ul = document.createElement('ul');
      (c.removed || []).forEach(x => ul.appendChild(el('li', 'rm', `取消：${x}`)));
      (c.added || []).forEach(x => ul.appendChild(el('li', 'add', `新增：${x}`)));
      if (!ul.children.length) ul.appendChild(el('li', '', '课表内容变化（时段/教室调整）'));
      d.appendChild(el('div', 'dhead', head));
      d.appendChild(ul);
      body.appendChild(d);
    });
    if (data.changes.length > 40) body.appendChild(el('div', 'hint', `仅显示前 40 天，共 ${data.changes.length} 天受影响`));
  }
  $('#modalTitle').textContent = `变更预览 · ${ops.length} 条调整，影响 ${data.changes.length} 天`;
  $('#mask').classList.add('show');
}

async function doApply(ops, source) {
  if (!ops.length) { toast('没有可应用的调整'); return; }
  const res = await post(API.apply, { ops, source });
  state.adjustments = res.adjustments;
  pendingOps = []; ocrOps = [];
  $('#cmdOps').innerHTML = '';
  $('#ocrOps').innerHTML = '';
  $('#btnPreview').disabled = $('#btnApply').disabled = true;
  $('#cmdText').value = '';
  $('#mask').classList.remove('show');
  renderRules();
  await loadWeek(currentWeek);
  toast('调整已应用到课表');
}

async function confirmApply() {
  await doApply(pendingOps, pendingSource);
}

/* ---------- 事件绑定 ---------- */
function bind() {
  $('#prevWeek').onclick = () => loadWeek(currentWeek - 1);
  $('#nextWeek').onclick = () => loadWeek(currentWeek + 1);
  $('#btnToday').onclick = () => loadWeek(weekOf(state.today, state.settings.term_start));

  $('#termStart').onchange = async e => {
    state.settings = (await post(API.settings, { term_start: e.target.value })).settings;
    await loadWeek(currentWeek);
    toast('学期起始日已更新');
  };
  $('#totalWeeks').onchange = async e => {
    state.settings = (await post(API.settings, { total_weeks: parseInt(e.target.value, 10) })).settings;
    await loadWeek(currentWeek);
  };

  document.querySelectorAll('.tab').forEach(t => {
    t.onclick = () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      $(`#panel-${t.dataset.tab}`).classList.add('active');
    };
  });

  // 图片识别
  const drop = $('#dropZone'), input = $('#imgInput');
  drop.onclick = () => input.click();
  input.onchange = () => input.files[0] && uploadImage(input.files[0]);
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('hover'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('hover'); }));
  drop.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) uploadImage(f); });

  $('#btnSample').onclick = () => { $('#ocrText').value = SAMPLE_NOTICE; toast('已填入示例通知'); };
  $('#btnClearOcr').onclick = () => { $('#ocrText').value = ''; ocrOps = []; $('#ocrOps').innerHTML = ''; };
  $('#btnOcrParse').onclick = async () => {
    const text = $('#ocrText').value.trim();
    if (!text) { toast('请先上传图片或粘贴通知文字'); return; }
    const hint = $('#ocrHint');
    hint.className = 'hint'; hint.textContent = '解析中…';
    try {
      const r = await post(API.parse, { text, mode: 'auto' });
      ocrOps = r.ops || [];
      renderOps($('#ocrOps'), ocrOps, true, () => { ocrOps = ocrOps; });
      hint.className = r.ops.length ? 'hint ok' : 'hint err';
      hint.textContent = r.explain.join('；') || r.question || '未识别到调整';
      if (ocrOps.length) {
        const box = $('#ocrOps');
        const btn = el('button', 'btn primary', '预览并应用');
        btn.style.marginTop = '8px';
        btn.onclick = () => openPreview(ocrOps, 'ocr');
        box.appendChild(btn);
      }
    } catch (e) { hint.className = 'hint err'; hint.textContent = e.message; }
  };

  // 文字调课
  document.querySelectorAll('.chip[data-demo]').forEach(c => {
    c.onclick = () => { $('#cmdText').value = c.dataset.demo; };
  });
  $('#btnParse').onclick = async () => {
    const text = $('#cmdText').value.trim();
    if (!text) { toast('请输入一句指令'); return; }
    const hint = $('#cmdHint');
    hint.className = 'hint'; hint.textContent = '解析中…';
    try {
      const r = await post(API.parse, { text, mode: 'auto' });
      pendingOps = r.ops || [];
      renderOps($('#cmdOps'), pendingOps, true, () => {
        $('#btnPreview').disabled = $('#btnApply').disabled = pendingOps.length === 0;
      });
      $('#btnPreview').disabled = $('#btnApply').disabled = pendingOps.length === 0;
      hint.className = pendingOps.length ? 'hint ok' : 'hint err';
      hint.textContent = r.explain.join('；') || r.question || '未识别到调整';
    } catch (e) { hint.className = 'hint err'; hint.textContent = e.message; }
  };
  $('#btnPreview').onclick = () => openPreview(pendingOps, 'text');
  $('#btnApply').onclick = () => doApply(pendingOps, 'text');

  // 弹窗
  $('#modalClose').onclick = $('#modalCancel').onclick = () => $('#mask').classList.remove('show');
  $('#modalOk').onclick = confirmApply;
  $('#mask').onclick = e => { if (e.target === $('#mask')) $('#mask').classList.remove('show'); };

  // 课程详情弹窗
  $('#courseModalClose').onclick = () => $('#courseMask').classList.remove('show');
  $('#courseMask').onclick = e => { if (e.target === $('#courseMask')) $('#courseMask').classList.remove('show'); };

  // 图例 hover 联动已在 renderLegendStatus 中动态绑定，此处无需重复

  // 规则
  $('#btnClearRules').onclick = async () => {
    if (!confirm('确定清空全部调整记录？')) return;
    state.adjustments = (await post(API.clear)).adjustments;
    renderRules(); await loadWeek(currentWeek); toast('已清空');
  };

  // 导入课表（支持 docx/xlsx/ics/txt/csv）
  $('#btnImport').onclick = () => $('#csvInput').click();
  $('#csvInput').onchange = async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    const btn = $('#btnImport');
    const originalText = btn.textContent;
    btn.disabled = true; btn.textContent = '导入中…';
    const fd = new FormData();
    // 保留原始扩展名，后端据此分发解析器
    fd.append('file', f);
    try {
      const r = await api(API.importCsv, { method: 'POST', body: fd });
      const kindMap = { docx: 'Word', xlsx: 'Excel', ics: '日历', txt: '文本', csv: 'CSV' };
      const kind = kindMap[r.source] || r.source;
      toast(`已从${kind}导入 ${r.valid_count} 条课程` +
        (r.replaced ? `（替换原 ${r.replaced} 条）` : '') +
        (r.error_count ? `，${r.error_count} 条有误` : '') +
        (r.section_times ? '，并已自动识别节次时间' : ''));
      await loadState();
      await loadWeek(1);
    } catch (err) { toast('导入失败：' + err.message); }
    finally { btn.disabled = false; btn.textContent = originalText; }
  };

  // 节次时间表弹窗
  $('#btnSectionTime').onclick = openSectionTimeModal;
  $('#sectionTimeClose').onclick = () => $('#sectionTimeMask').classList.remove('show');
  $('#sectionTimeCancel').onclick = () => $('#sectionTimeMask').classList.remove('show');
  $('#sectionTimeSave').onclick = saveSectionTimes;
  $('#sectionTimeMask').onclick = e => { if (e.target === $('#sectionTimeMask')) $('#sectionTimeMask').classList.remove('show'); };

  // 帮助折叠面板
  $('#helpToggle').onclick = () => {
    $('#helpBody').classList.toggle('show');
    $('#helpArrow').classList.toggle('open');
  };
}

async function uploadImage(file) {
  const hint = $('#ocrHint');
  hint.className = 'hint'; hint.textContent = `正在识别「${file.name}」…请稍候约 10 秒`;
  const fd = new FormData(); fd.append('image', file);
  try {
    const r = await api(API.ocrParse, { method: 'POST', body: fd });
    $('#ocrText').value = r.text || '';
    ocrOps = r.ops || [];
    renderOps($('#ocrOps'), ocrOps, true, () => { });
    hint.className = ocrOps.length ? 'hint ok' : 'hint err';
    const conf = r.confidence != null ? `<span class="conf-tag">置信度 ${r.confidence}%</span>` : '';
    hint.innerHTML = `${(r.explain || []).join('；') || (r.question || '未识别到调整')}${conf}`;
    if (ocrOps.length) {
      const btn = el('button', 'btn primary', '预览并应用');
      btn.style.marginTop = '8px';
      btn.onclick = () => openPreview(ocrOps, 'ocr');
      $('#ocrOps').appendChild(btn);
    }
  } catch (e) {
    hint.className = 'hint err';
    hint.textContent = e.message + ' 你可以把通知文字直接粘贴到下方文本框里解析。';
  }
}

/* ---------- 自定义节次时间表弹窗 ---------- */
function openSectionTimeModal() {
  const arr = (state.settings && state.settings.section_times) || DEFAULT_SECTION_TIMES;
  const body = $('#sectionTimeModalBody');
  body.innerHTML = '<div class="section-time-grid"></div>';
  const grid = body.querySelector('.section-time-grid');
  arr.forEach((t, i) => {
    const row = el('div', 'st-row');
    row.innerHTML = `
      <span class="st-num">${i + 1}</span>
      <input type="time" class="st-start" value="${t.start || ''}" />
      <span class="st-sep">—</span>
      <input type="time" class="st-end" value="${t.end || ''}" />`;
    grid.appendChild(row);
  });
  $('#sectionTimeMask').classList.add('show');
}

async function saveSectionTimes() {
  const rows = document.querySelectorAll('#sectionTimeModalBody .st-row');
  const times = [];
  rows.forEach(r => {
    const s = r.querySelector('.st-start').value;
    const e = r.querySelector('.st-end').value;
    if (s && e) times.push({ start: s, end: e });
  });
  if (!times.length) { toast('请至少填写一节的时间'); return; }
  try {
    state.settings = (await post(API.settings, { section_times: times })).settings;
    renderTimetable();
    $('#sectionTimeMask').classList.remove('show');
    toast(`已更新 ${times.length} 节课的时间`);
  } catch (e) { toast('保存失败：' + e.message); }
}

bind();
loadState().catch(e => toast('加载失败：' + e.message));
