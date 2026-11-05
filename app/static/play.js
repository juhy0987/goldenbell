'use strict';
// 참여자 화면. 마지막 state 하나로 다시 그리되, 작성 중인 입력은 문제나 팀이 바뀔 때만 덮는다.
const SAVED_TEAM_KEY = 'gb_team';
const WARN_SEC = 5;

let lobbyTeams = [];
let joined = false;
let state = null;
let timer = null;
let shownQid = null;      // textarea 를 마지막으로 채운 문제
let shownTeam = null;     // 그 textarea 가 속한 팀 (팀이 바뀌면 다시 채운다)
let connected = false;
let autoJoinTried = false;

function savedTeam() { try { return sessionStorage.getItem(SAVED_TEAM_KEY); } catch (e) { return null; } }
function saveTeam(id) { try { id ? sessionStorage.setItem(SAVED_TEAM_KEY, id) : sessionStorage.removeItem(SAVED_TEAM_KEY); } catch (e) { /* 저장 불가 환경은 자동 재입장만 포기 */ } }

const conn = connect('/ws/play', {
  onopen() { autoJoinTried = false; connected = true; setLive(true); },
  onclose() { connected = false; setLive(false); joined = false; render(); },
  onmessage(m) {
    switch (m.type) {
      case 'lobby':
        joined = false; state = null; lobbyTeams = m.teams;
        if (!autoJoinTried && savedTeam()) { autoJoinTried = true; conn.send({ type: 'join', team: savedTeam() }); }
        break;
      case 'joined': joined = true; saveTeam(m.team); break;
      case 'state': joined = true; state = m; timer = timerFrom(m.timer); break;
      case 'kicked': saveTeam(null); toast(m.reason === 'released' ? '진행자가 연결을 해제했어요.' : '팀이 삭제되어 로비로 돌아갑니다.'); break;
      case 'error': if (m.code === 'team_taken' || m.code === 'no_such_team') saveTeam(null); break;
    }
    render();
  },
});

function setLive(on) {
  $('live').classList.toggle('on', on);
  $('liveText').textContent = on ? '연결됨' : '재연결 중';
}

function renderTeams(ul, list, myId) {
  ul.replaceChildren();
  const top = Math.max(1, ...list.map((t) => t.score));
  for (const t of list) {
    const li = h('li', 'team' + (t.id === myId ? ' me' : ''));
    li.append(h('span', 'rk' + (t.rank === 1 ? ' r1' : ''), String(t.rank)));
    const nm = h('span', 'nm', t.name);
    if (t.player) nm.append(h('small', 'pl', t.player));
    li.append(nm, h('span', 'sc', String(t.score)));
    const bar = h('div', 'bar'); const i = h('i'); i.style.width = Math.max(0, t.score) / top * 100 + '%';
    bar.append(i); li.append(bar);
    ul.append(li);
  }
}

function render() {
  $('pJoin').hidden = joined;
  $('pMain').hidden = !joined || !state;
  $('pBoard').hidden = !joined || !state;
  if (!joined) {
    $('teamPick').replaceChildren(...lobbyTeams.map((t) => {
      const b = h('button', null, t.name);
      b.type = 'button'; b.disabled = t.taken || !connected;
      b.append(h('small', null, !connected ? '재연결 중' : t.taken ? '이미 입장함' : (t.player || '입장 가능')));
      b.onclick = () => conn.send({ type: 'join', team: t.id });
      return b;
    }));
    return;
  }
  if (!state) return;
  const s = state, q = s.question;
  const who = $('pWho'); who.replaceChildren(s.team.name);
  if (s.team.player) who.append(' ', h('small', null, s.team.player));
  $('pProg').textContent = s.progress.used + ' / ' + s.progress.total + ' 문제';
  renderTeams($('playTeams'), s.scoreboard, s.team.id);
  $('pWait').hidden = !!q;
  $('pQ').hidden = !q;
  if (!q) { shownQid = null; tick(); return; }

  $('pChip').textContent = q.catLabel;
  $('pChip').style.setProperty('--cc', CAT_COLOR[q.cat] || 'var(--brass)');
  $('pNo').textContent = q.no + '번 문제 · ' + s.points + '점';
  $('pText').textContent = q.text;
  const ta = $('pAnswer');
  if (shownQid !== q.qid || shownTeam !== s.team.id) { // 문제나 팀이 바뀔 때만 입력을 덮어쓴다
    shownQid = q.qid; shownTeam = s.team.id; ta.value = s.mySubmission ? s.mySubmission.text : '';
  }
  ta.disabled = s.locked;
  $('pSubmit').disabled = s.locked;
  $('pSubmit').textContent = s.mySubmission ? '다시 제출하기' : '제출하기';
  $('pCount').textContent = ta.value.length + ' / 40';
  const st = $('pStatus');
  st.className = 'status';
  if (s.locked) st.textContent = q.revealed ? '정답이 공개되었어요.' : '시간이 끝났어요. 제출이 잠겼습니다.';
  else if (s.mySubmission) { st.textContent = '제출함: ' + s.mySubmission.text + ' (공개 전까지 고쳐서 다시 낼 수 있어요)'; st.classList.add('ok'); }
  else st.textContent = '답을 쓰고 제출하세요.';

  const v = $('pVerdict');
  v.hidden = !q.revealed;
  if (q.revealed) {
    v.className = 'verdict' + (q.hit ? ' hit' : '');
    v.replaceChildren(h('div', 'big', q.hit ? '정답입니다!' : '아쉬워요'), h('div', null, '정답'), h('div', 'ca', q.answer));
    if (q.note) v.append(h('div', 'status', q.note));
  }
  tick();
}

// 타이머 표시만 한다. 0 확정과 잠금은 서버가 보낸다.
function tick() {
  const el = $('pTime');
  const r = state && state.question ? remainMs(timer) : null;
  el.hidden = r == null;
  if (r == null) return;
  const sec = Math.ceil(r / 1000);
  el.textContent = sec + '초';
  el.classList.toggle('warn', sec <= WARN_SEC);
}
setInterval(tick, 200);

$('pAnswer').addEventListener('input', () => { $('pCount').textContent = $('pAnswer').value.length + ' / 40'; });
$('pForm').addEventListener('submit', (e) => {
  e.preventDefault();
  if (!state || !state.question) return;
  conn.send({ type: 'submit', qid: state.question.qid, text: $('pAnswer').value });
});
$('btnLeave').onclick = () => { saveTeam(null); conn.send({ type: 'leave' }); };
