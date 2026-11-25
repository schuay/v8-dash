// Copyright 2026 The v8-dash developers
// SPDX-License-Identifier: MIT

'use strict';

const POLL_MS = 3000;
// Relative times and liveness labels age even when no file changes.
const MAX_AGE_MS = 60000;

const board = document.getElementById('board');
const filter = document.getElementById('filter');
const pop = document.getElementById('pop');

function stored(key, fallback) {
  return JSON.parse(sessionStorage.getItem(key) || fallback);
}

// Expanded rows and toggled sections, by data-key. Survives board swaps
// and reloads.
const toggled = new Set(stored('toggled', '[]'));
// Table data-key -> {col, desc}. Tables without an entry keep the
// server's priority order.
const sorts = stored('sorts', '{}');
let lastRefresh = Date.now();

function saveToggled() {
  sessionStorage.setItem('toggled', JSON.stringify([...toggled]));
}

function setRowOpen(row, open) {
  row.classList.toggle('open', open);
  row.nextElementSibling.hidden = !open;
}

function cellValue(row, col, numeric) {
  const td = row.cells[col];
  const text = td.dataset.v !== undefined ? td.dataset.v : td.textContent;
  if (!numeric) return text.trim().toLowerCase();
  return text.trim() === '' ? NaN : Number(text);
}

function applySort(table) {
  const sort = sorts[table.dataset.key];
  const heads = table.tHead.rows[0].cells;
  for (const th of heads) th.removeAttribute('aria-sort');
  if (!sort) return;
  const th = heads[sort.col];
  th.setAttribute('aria-sort', sort.desc ? 'descending' : 'ascending');
  const numeric = th.dataset.sort === 'num';
  const body = table.tBodies[0];
  const rows = [...body.querySelectorAll(':scope > tr.row')].map(row => ({
    row, detail: row.nextElementSibling,
    v: cellValue(row, sort.col, numeric),
  }));
  const empty = v => numeric ? Number.isNaN(v) : v === '';
  rows.sort((a, b) => {
    // Rows without a value stay at the bottom in both directions.
    if (empty(a.v) || empty(b.v)) return empty(a.v) - empty(b.v);
    const c = numeric ? a.v - b.v : a.v.localeCompare(b.v);
    return sort.desc ? -c : c;
  });
  for (const r of rows) body.append(r.row, r.detail);
}

function applyState() {
  for (const row of board.querySelectorAll('tr.row')) {
    setRowOpen(row, toggled.has(row.dataset.key));
  }
  // Sections record a flip of their default state.
  for (const d of board.querySelectorAll('details.bucket')) {
    d.dataset.default = d.open ? '1' : '';
    if (toggled.has(d.dataset.key)) d.open = !d.open;
  }
  for (const table of board.querySelectorAll('table[data-key]')) {
    applySort(table);
  }
  applyFilter();
}

function applyFilter() {
  const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
  for (const el of board.querySelectorAll('[data-search]')) {
    const text = el.dataset.search.toLowerCase();
    const hit = words.every(w => text.includes(w));
    el.classList.toggle('filtered', !hit);
    const detail = el.nextElementSibling;
    if (detail && detail.classList.contains('detail')) {
      detail.classList.toggle('filtered', !hit);
    }
  }
}

async function refresh() {
  const res = await fetch('/board');
  if (!res.ok) return;
  const html = await res.text();
  // Don't replace a form the user is typing into; the next poll retries.
  const active = document.activeElement;
  if (board.contains(active) && active.matches('input') && active.value) {
    return;
  }
  const focusKey = active.closest ? active.closest('tr.row')?.dataset.key : null;
  board.innerHTML = html;
  board.dataset.version = res.headers.get('X-Dash-Version');
  lastRefresh = Date.now();
  ppCache.clear();
  applyState();
  if (ppOpen !== null) ppButton(ppOpen)?.classList.add('active');
  if (focusKey) {
    board.querySelector(`tr.row[data-key="${CSS.escape(focusKey)}"]`)?.focus();
  }
}

async function poll() {
  try {
    const res = await fetch('/version');
    if (res.ok && ((await res.text()) !== board.dataset.version ||
                   Date.now() - lastRefresh > MAX_AGE_MS)) {
      await refresh();
    }
  } catch (e) {
    // Server unreachable; keep the current board and retry.
  }
  setTimeout(poll, POLL_MS);
}

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Dash': '1'},
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.error || `Request failed (${res.status})`);
  }
  return res.ok;
}

function toggleRow(row) {
  const open = !toggled.has(row.dataset.key);
  open ? toggled.add(row.dataset.key) : toggled.delete(row.dataset.key);
  saveToggled();
  setRowOpen(row, open);
}

// Cycles a column through ascending, descending and the priority order.
function cycleSort(th) {
  const table = th.closest('table');
  const key = table.dataset.key;
  const col = th.cellIndex;
  const cur = sorts[key];
  if (!cur || cur.col !== col) {
    // Numbers are most useful largest first.
    sorts[key] = {col, desc: th.dataset.sort === 'num'};
  } else if (cur.desc === (th.dataset.sort === 'num')) {
    sorts[key] = {col, desc: !cur.desc};
  } else {
    delete sorts[key];
  }
  sessionStorage.setItem('sorts', JSON.stringify(sorts));
  // Without a sort the order is the server's; fetch it again.
  sorts[key] ? applySort(table) : refresh();
}

board.addEventListener('click', async e => {
  const dismiss = e.target.closest('button[data-dismiss]');
  if (dismiss) {
    dismiss.disabled = true;
    await post(`/api/notes/${dismiss.dataset.dismiss}/dismiss`, {});
    await refresh();
    return;
  }
  const th = e.target.closest('th[data-sort]');
  if (th) {
    cycleSort(th);
    return;
  }
  if (e.target.closest('a, button, input, select, form')) return;
  const row = e.target.closest('tr.row');
  if (row) toggleRow(row);
});

board.addEventListener('keydown', e => {
  if (e.key !== 'Enter' || !e.target.matches('tr.row')) return;
  toggleRow(e.target);
});

board.addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  let ok;
  if (form.matches('form.ask')) {
    ok = await post('/api/requests', {
      cl: form.dataset.cl,
      kind: form.kind.value,
      summary: form.summary.value,
    });
  } else if (form.matches('form.request')) {
    ok = await post(`/api/requests/${form.dataset.rid}/decision`, {
      decision: e.submitter.value,
      reply: form.reply.value,
    });
  } else {
    return;
  }
  if (ok) {
    form.reset();
    document.activeElement.blur();
    await refresh();
  }
});

// The toggle event doesn't bubble; capture it on the board.
board.addEventListener('toggle', e => {
  const d = e.target;
  if (!d.matches('details.bucket') || d.dataset.default === undefined) return;
  const flipped = d.open !== Boolean(d.dataset.default);
  flipped ? toggled.add(d.dataset.key) : toggled.delete(d.dataset.key);
  saveToggled();
}, true);

// Pinpoint popover: full results of a CL's latest batch, toggled by a click
// on the row's totals. Closes on Escape, any click that isn't on
// another row's totals, or scrolling.
const ppCache = new Map();
let ppOpen = null;

function ppButton(cl) {
  return board.querySelector(`.pp-sum[data-pp="${CSS.escape(cl)}"]`);
}

function hidePop() {
  if (ppOpen === null) return;
  ppButton(ppOpen)?.classList.remove('active');
  ppOpen = null;
  pop.hidden = true;
}

async function showPop(button) {
  const cl = button.dataset.pp;
  hidePop();
  ppOpen = cl;
  button.classList.add('active');
  if (!ppCache.has(cl)) {
    const res = await fetch(`/pp/${cl}`);
    if (!res.ok) {
      if (ppOpen === cl) hidePop();
      return;
    }
    ppCache.set(cl, await res.text());
  }
  if (ppOpen !== cl) return;
  pop.innerHTML = ppCache.get(cl);
  pop.hidden = false;
  pop.scrollTop = 0;
  // Below the totals if it fits, else above; clamped to the viewport.
  const r = button.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  const left = Math.max(8, Math.min(r.left, innerWidth - w - 8));
  let top = r.bottom + 4;
  if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 4);
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

document.addEventListener('click', e => {
  if (pop.contains(e.target)) {
    // Keep it open for a job link or a drag that selected text.
    if (!e.target.closest('a') && getSelection().isCollapsed) hidePop();
    return;
  }
  const button = e.target.closest('.pp-sum');
  if (button && button.dataset.pp !== ppOpen) {
    showPop(button);
  } else {
    hidePop();
  }
});
document.addEventListener('scroll', e => {
  if (e.target !== pop) hidePop();
}, true);

filter.addEventListener('input', applyFilter);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') hidePop();
  if (e.key === '/' && !e.target.matches('input, select, textarea')) {
    e.preventDefault();
    filter.focus();
  }
});

applyState();
setTimeout(poll, POLL_MS);
