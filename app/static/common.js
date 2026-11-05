'use strict';
// 진행자 · 참여자 화면 공통: DOM 도우미, 토스트, 재연결 WebSocket, 타이머 표시.
const $ = (id) => document.getElementById(id);

// 사용자 입력이 섞이는 텍스트는 항상 textContent 로 넣는다 (XSS 방지).
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const CAT_COLOR = {
  '시사': 'var(--c-news)', '영화': 'var(--c-movie)', '밈': 'var(--c-meme)',
  '넌센스': 'var(--c-nonsense)', 'CS': 'var(--c-cs)',
};

function toast(msg) {
  let box = $('toasts');
  if (!box) { box = h('div', 'toast-box'); box.id = 'toasts'; document.body.appendChild(box); }
  const t = h('div', 'toast', msg);
  box.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

const RECONNECT_MAX_MS = 5000;

// 끊기면 지수 백오프로 다시 연결한다. handlers: onopen / onmessage(obj) / onclose(event, openedBefore)
function connect(path, handlers) {
  const url = (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + path;
  let ws = null;
  let delay = 500;
  const api = { send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); } };
  function open() {
    let opened = false;
    ws = new WebSocket(url);
    ws.onopen = () => { opened = true; delay = 500; handlers.onopen && handlers.onopen(); };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { console.error('bad message', e); return; }
      if (msg.type === 'error') toast(msg.message || msg.code);
      handlers.onmessage(msg);
    };
    ws.onclose = (ev) => {
      const stop = handlers.onclose && handlers.onclose(ev, opened);
      if (stop) return;
      setTimeout(open, delay);
      delay = Math.min(delay * 2, RECONNECT_MAX_MS);
    };
  }
  open();
  return api;
}

// 받은 시각 + remainMs 로 로컬 종료 시각을 정한다 (서버와 시계 차이 무시).
function timerFrom(timer) {
  return { timer, recvAt: Date.now() };
}
function remainMs(t) {
  if (!t) return null;
  const { timer, recvAt } = t;
  if (timer.running) return Math.max(0, timer.remainMs - (Date.now() - recvAt));
  return timer.remainMs;
}
