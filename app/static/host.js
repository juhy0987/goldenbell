'use strict';
// 진행자 화면. 모든 조작은 WebSocket 메시지로 보내고, 마지막 state 로 다시 그린다.
const ARC_LEN = 163.36;
const WARN_SEC = 5;

let S = null;
let timer = null;
let peek = false;      // 답안 텍스트 펼치기 (화면 공유 중 노출 방지용)
let rowSig = '';       // 설정 시트 행을 만든 팀 구성

const conn = connect('/ws/host', {
  onopen() { setLive(true); },
  onclose(ev) {
    setLive(false);
    if (ev.code === 4401) { location.href = '/host'; return true; }
    return false;
  },
  onmessage(m) {
    if (m.type !== 'state') return;
    if (!S || !S.current || !m.current || S.current.qid !== m.current.qid) peek = false;
    S = m; timer = timerFrom(m.timer); render();
  },
});
const send = (o) => conn.send(o);

function setLive(on) {
  $('live').classList.toggle('on', on);
  $('liveText').textContent = on ? '연결됨' : '재연결 중';
}

const catLabel = (key) => (S.categories.find((c) => c.key === key) || {}).label || key;
const catColor = (key) => CAT_COLOR[key] || 'var(--brass)';

function render() {
  const cur = S.current;
  $('pickView').hidden = !!cur;
  $('qView').hidden = !cur;
  $('controls').hidden = !cur;
  $('prog').textContent = S.progress.used + ' / ' + S.progress.total + ' 문제';
  renderTeams();
  renderSettingsRows();
  if (!cur) renderPick(); else renderQuestion(cur);
  tick();
}

function renderPick() {
  $('nextNo').textContent = S.progress.used + 1;
  $('playUrl').textContent = location.origin + '/';
  $('cats').replaceChildren(...S.categories.map((c, i) => {
    const b = h('button', 'cat'); b.type = 'button'; b.disabled = c.left === 0;
    b.style.setProperty('--cc', catColor(c.key));
    const left = h('div', 'left-n', '남은 문제 '); left.append(h('b', null, String(c.left)), ' / ' + c.total);
    const pips = h('div', 'pips'); pips.append(...c.used.map((u) => h('i', u ? 'used' : '')));
    b.append(h('span', 'key', String(i + 1)), h('span', 'nm', c.label), pips, left);
    b.onclick = () => send({ type: 'pick', cat: c.key });
    return b;
  }));
  $('hist').replaceChildren(...S.history.map((x) => {
    const b = h('button', 'hchip'); b.type = 'button';
    b.style.setProperty('--cc', catColor(x.cat));
    b.append(h('i'), x.no + '번 ' + catLabel(x.cat));
    b.onclick = () => send({ type: 'open', qid: x.qid });
    return b;
  }));
  if (!S.history.length) $('hist').append(h('p', 'empty', '아직 출제한 문제가 없습니다.'));
}

function renderQuestion(q) {
  $('qno').replaceChildren(h('b', null, String(q.no)), '번');
  const chip = $('chip'); chip.textContent = q.catLabel; chip.style.setProperty('--cc', catColor(q.cat));
  $('pts').textContent = '+' + S.points + '점';
  $('qtext').textContent = q.text;
  $('ans').textContent = q.answer;
  $('board').classList.toggle('hidden-ans', !q.revealed);
  $('note').hidden = !(q.revealed && q.note);
  $('note').textContent = q.note || '';
  $('btnReveal').textContent = q.revealed ? '정답 가리기' : '정답 공개';
  $('btnPeek').textContent = peek ? '답안 가리기' : '답안 펼치기';

  const show = peek || q.revealed;
  const subs = S.submissions, hits = new Set(S.correct);
  $('subCount').textContent = Object.keys(subs).length + ' / ' + S.teams.length + ' 제출';
  $('subs').replaceChildren(...S.teams.map((t) => {
    const sub = subs[t.id], hit = hits.has(t.id), match = show && sub && sub.match;
    const b = h('button', 'sub' + (hit ? ' hit' : '') + (match ? ' match' : ''));
    b.type = 'button';
    const tn = h('div', 'tn', null);
    tn.append(h('span', null, t.name));
    if (hit || match) tn.append(h('span', 'mk', hit ? '정답' : '일치'));
    const mini = h('div', 'mini' + (sub ? (show ? '' : ' done') : ' wait'), sub ? (show ? sub.text : '제출 완료') : '대기 중');
    b.append(tn, mini);
    b.onclick = () => send({ type: 'toggle_hit', team: t.id });
    return b;
  }));
  if (document.activeElement !== $('timerLen')) $('timerLen').value = String(S.timerLen);
}

function renderTeams() {
  const ul = $('hostTeams'); ul.replaceChildren();
  const top = Math.max(1, ...S.teams.map((t) => t.score));
  for (const t of S.teams) {
    const li = h('li', 'team');
    li.append(h('span', 'rk' + (t.rank === 1 ? ' r1' : ''), String(t.rank)));
    const nm = h('span', 'nm', t.name);
    if (t.player) nm.append(h('small', 'pl', t.player));
    li.append(nm, h('span', 'sc', String(t.score)));
    const bar = h('div', 'bar'); const i = h('i'); i.style.width = Math.max(0, t.score) / top * 100 + '%';
    bar.append(i); li.append(bar);
    ul.append(li);
  }
}

// 설정 시트의 입력칸은 입력 중 지워지지 않도록 시트를 열 때와 팀 구성이 바뀔 때만 다시 만든다.
function renderSettingsRows() {
  if ($('settings').hidden) return;
  const sig = S.teams.map((t) => t.id).join(',');
  if (sig !== rowSig) { rowSig = sig; buildRows(); }
  for (const t of S.teams) { // 점유 표시는 매번 갱신
    const d = document.querySelector('[data-conn="' + t.id + '"]');
    if (d) { d.querySelector('.dot').classList.toggle('on', t.connected); d.querySelector('button').disabled = !t.connected; }
  }
}

function buildRows() {
  $('teamCount').value = S.teams.length;
  $('points').value = S.points;
  $('teamRows').replaceChildren(...S.teams.map((t, i) => {
    const row = h('div', 'trow t');
    const name = h('input'); name.type = 'text'; name.value = t.name; name.placeholder = (i + 1) + '팀';
    const player = h('input'); player.type = 'text'; player.value = t.player;
    const adj = h('input'); adj.type = 'number'; adj.value = t.adj;
    const upd = (k, v) => send({ type: 'team_update', team: t.id, [k]: v });
    name.onchange = () => upd('name', name.value);
    player.onchange = () => upd('player', player.value);
    adj.onchange = () => upd('adj', Number(adj.value) || 0);
    const c = h('span'); c.dataset.conn = t.id;
    const rel = h('button', 'tbtn', '해제'); rel.type = 'button';
    rel.onclick = () => send({ type: 'release', team: t.id });
    c.append(h('i', 'dot'), ' ', rel);
    row.append(h('span', null, String(i + 1)), name, player, adj, c);
    return row;
  }));
}

// 타이머 표시만 한다. 0 확정은 서버가 보낸다.
function tick() {
  if (!S || !S.current) return;
  const len = S.timer.lenSec * 1000;
  const r = remainMs(timer);
  const shown = r == null ? len : r;
  const sec = Math.ceil(shown / 1000);
  $('tnum').textContent = sec;
  $('ring').classList.toggle('warn', S.timer.running && sec <= WARN_SEC);
  $('arc').style.strokeDashoffset = String(ARC_LEN * (1 - Math.min(1, shown / len)));
  $('btnTimer').textContent = S.timer.running ? '일시정지' : (r != null && r > 0 ? '이어서' : '타이머 시작');
}
setInterval(tick, 200);

$('btnBack').onclick = () => send({ type: 'back' });
$('btnReveal').onclick = () => send({ type: 'reveal' });
$('btnTimer').onclick = () => send({ type: 'timer_toggle' });
$('timerLen').onchange = (e) => send({ type: 'timer_len', sec: Number(e.target.value) });
$('btnAuto').onclick = () => send({ type: 'auto_grade' });
$('btnPeek').onclick = () => { peek = !peek; render(); };
$('btnFull').onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch((e) => toast('전체화면을 열 수 없어요: ' + e.message));
};

$('btnSettings').onclick = () => { $('settings').hidden = false; $('resetConfirm').hidden = true; rowSig = ''; if (S) render(); };
$('btnClose').onclick = () => { $('settings').hidden = true; };
$('teamCount').onchange = (e) => send({ type: 'settings', teamCount: Number(e.target.value) });
$('points').onchange = (e) => send({ type: 'settings', points: Number(e.target.value) });
$('btnReset').onclick = () => { $('resetConfirm').hidden = false; };
$('btnResetNo').onclick = () => { $('resetConfirm').hidden = true; };
$('btnResetYes').onclick = () => { send({ type: 'reset' }); $('resetConfirm').hidden = true; $('settings').hidden = true; };

$('btnResult').onclick = () => {
  if (!S) return;
  const sorted = [...S.teams].sort((a, b) => a.rank - b.rank);
  $('winner').textContent = sorted.filter((t) => t.rank === 1).map((t) => t.name).join(' · ');
  $('rank').replaceChildren(...sorted.map((t) => {
    const li = h('li', t.rank === 1 ? 'first' : '');
    li.append(h('span', 'r', String(t.rank)), h('span', null, t.name), h('span', 's', String(t.score)));
    return li;
  }));
  $('result').hidden = false;
};
$('btnResultClose').onclick = () => { $('result').hidden = true; };

$('keys').textContent = '1-5 분야 · Space 정답 · T 타이머 · Esc 뒤로';
document.addEventListener('keydown', (e) => {
  if (!S || e.ctrlKey || e.metaKey || e.altKey) return;
  if (!$('settings').hidden || !$('result').hidden) return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  // Space 는 포커스된 버튼의 keyup click 을 일으키므로 단축키 키면 포커스를 먼저 뗀다 (keyup 이 body 로 가서 click 없음).
  if (e.target.tagName === 'BUTTON' && [' ', 't', 'T', 'Escape'].includes(e.key)) e.target.blur();
  if (!S.current) {
    const c = S.categories[Number(e.key) - 1];
    if (c && c.left > 0) send({ type: 'pick', cat: c.key });
  } else if (e.key === ' ') { e.preventDefault(); send({ type: 'reveal' }); }
  else if (e.key === 't' || e.key === 'T') send({ type: 'timer_toggle' });
  else if (e.key === 'Escape') send({ type: 'back' });
});
