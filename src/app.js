// 画面まわり：音声入力・確認・集計表示

import { Capacitor } from '@capacitor/core';
import { exportCSV } from './export.js';
import { parseUtterance } from './parser.js';
import { applyReminder, reminderSupported } from './reminder.js';
import { createRecognizer } from './speech.js';
import { buildSummary, classify, dayKey, pad2 } from './stats.js';
import {
  addRecord,
  deleteRecord,
  fromCSV,
  loadRecords,
  loadSettings,
  mergeRecords,
  saveRecords,
  saveSettings,
  toCSV,
} from './storage.js';

const $ = (id) => document.getElementById(id);
const el = {
  mic: $('mic'),
  micLabel: $('mic-label'),
  micError: $('mic-error'),
  transcript: $('transcript'),
  banner: $('today-banner'),
  autoSave: $('auto-save'),
  manualToggle: $('manual-toggle'),
  confirm: $('confirm'),
  sys: $('f-sys'),
  dia: $('f-dia'),
  pulse: $('f-pulse'),
  timing: $('f-timing'),
  at: $('f-at'),
  note: $('f-note'),
  confirmClass: $('confirm-class'),
  save: $('save'),
  cancel: $('cancel'),
  countdown: $('countdown'),
  summary: $('summary'),
  chart: $('chart'),
  range: $('range'),
  table: $('table'),
  exportBtn: $('export'),
  importInput: $('import'),
  clearBtn: $('clear'),
  reminderRow: $('reminder-row'),
  reminderEnabled: $('reminder-enabled'),
  reminderTime: $('reminder-time'),
  reminderNote: $('reminder-note'),
};

let records = [];
let settings = null;
let recognizer = null;
let view = 'daily';
let countdownTimer = null;

const showError = (message) => {
  el.micError.textContent = message || '';
  el.micError.hidden = !message;
};

const setStatus = (text) => {
  el.transcript.textContent = text;
};

/* ---------- 音声入力 ---------- */

async function initSpeech() {
  recognizer = createRecognizer({
    lang: settings.lang,
    onPartial: (text) => setStatus(text),
    onStateChange: (listening) => {
      el.mic.classList.toggle('is-listening', listening);
      el.micLabel.textContent = listening ? '聞いています…' : 'タップして話す';
      el.mic.dataset.listening = String(listening);
    },
    onError: showError,
    onResult: handleMatches,
  });

  const ready = await recognizer.prepare();
  if (!ready.ok) {
    el.mic.disabled = true;
    el.micLabel.textContent = '音声入力に非対応';
    showError(ready.reason);
  }
}

/** 認識候補のうち、血圧として読めた最初のものを採用する。 */
function handleMatches(matches) {
  const texts = matches.filter(Boolean);
  if (!texts.length) return;
  setStatus(texts[0]);

  const now = new Date();
  const parsed =
    texts.map((t) => parseUtterance(t, now)).find((p) => p.ok) ||
    parseUtterance(texts[0], now);

  if (!parsed.ok) {
    showError(`${parsed.errors.join(' / ')}（例：「上が132、下が84、脈は68」）`);
    return;
  }
  showError(parsed.warnings.join(' / '));
  openConfirm(parsed.record, settings.autoSave);
}

el.mic.addEventListener('click', async () => {
  if (!recognizer || el.mic.disabled) return;
  showError('');
  if (el.mic.dataset.listening === 'true') {
    await recognizer.stop();
    return;
  }
  setStatus('…');
  await recognizer.start();
});

/* ---------- 確認カード ---------- */

const toLocalInput = (iso) => {
  const d = new Date(iso);
  return `${dayKey(iso)}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};

function stopCountdown() {
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = null;
  el.countdown.textContent = '';
}

function openConfirm(record, autoSave = false) {
  stopCountdown();
  el.confirm.hidden = false;
  el.sys.value = record.systolic;
  el.dia.value = record.diastolic;
  el.pulse.value = record.pulse ?? '';
  el.timing.value = record.timing;
  el.at.value = toLocalInput(record.at);
  el.note.value = record.note ?? '';
  updateConfirmClass();
  el.confirm.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  if (!autoSave) return;
  let left = 3;
  el.countdown.textContent = `（${left}）`;
  countdownTimer = setInterval(() => {
    left -= 1;
    el.countdown.textContent = left > 0 ? `（${left}）` : '';
    if (left <= 0) {
      stopCountdown();
      commit();
    }
  }, 1000);
}

function closeConfirm() {
  stopCountdown();
  el.confirm.hidden = true;
}

function updateConfirmClass() {
  const sys = Number(el.sys.value);
  const dia = Number(el.dia.value);
  if (!sys || !dia) {
    el.confirmClass.textContent = '';
    return;
  }
  const c = classify(sys, dia);
  el.confirmClass.textContent = `区分の目安：${c.label}`;
  el.confirmClass.dataset.level = c.key;
}

async function commit() {
  const sys = Number(el.sys.value);
  const dia = Number(el.dia.value);
  if (!sys || !dia || sys <= dia) {
    showError('最高血圧と最低血圧を確認してください。');
    return;
  }
  const at = el.at.value ? new Date(el.at.value) : new Date();
  records = await addRecord(
    {
      at: at.toISOString(),
      systolic: sys,
      diastolic: dia,
      pulse: Number(el.pulse.value) || null,
      timing: el.timing.value,
      note: el.note.value.trim(),
      source: 'voice',
    },
    records,
  );
  closeConfirm();
  setStatus('記録しました。');
  render();
}

[el.sys, el.dia].forEach((input) => {
  input.addEventListener('input', () => {
    stopCountdown();
    updateConfirmClass();
  });
});
el.save.addEventListener('click', commit);
el.cancel.addEventListener('click', () => {
  closeConfirm();
  setStatus('取り消しました。');
});
el.manualToggle.addEventListener('click', () => {
  const now = new Date();
  openConfirm({
    at: now.toISOString(),
    systolic: 120,
    diastolic: 78,
    pulse: null,
    timing: now.getHours() < 12 ? 'morning' : 'evening',
    note: '',
  });
});
el.autoSave.addEventListener('change', async () => {
  settings = await saveSettings({ ...settings, autoSave: el.autoSave.checked });
});

/* ---------- リマインダー ---------- */

async function updateReminder() {
  const [hour, minute] = el.reminderTime.value.split(':').map(Number);
  settings = await saveSettings({
    ...settings,
    reminderEnabled: el.reminderEnabled.checked,
    reminderHour: Number.isFinite(hour) ? hour : 7,
    reminderMinute: Number.isFinite(minute) ? minute : 0,
  });
  const result = await applyReminder(settings);
  if (!result.ok) {
    el.reminderEnabled.checked = false;
    settings = await saveSettings({ ...settings, reminderEnabled: false });
    el.reminderNote.textContent = result.reason;
    return;
  }
  el.reminderNote.textContent = settings.reminderEnabled
    ? `毎日 ${pad2(settings.reminderHour)}:${pad2(settings.reminderMinute)} に通知します。`
    : '通知は設定されていません。';
}

el.reminderEnabled.addEventListener('change', updateReminder);
el.reminderTime.addEventListener('change', updateReminder);

/* ---------- 集計表示 ---------- */

const fmt = (avg) => (avg ? `${avg.systolic} / ${avg.diastolic}` : '—');

const statCard = (label, value, sub = '') =>
  `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;

function renderSummary(summary) {
  if (!summary.total) {
    el.summary.innerHTML =
      '<p class="empty">まだ記録がありません。マイクをタップして話しかけてください。</p>';
    return;
  }
  const latest = summary.latest;
  const latestClass = classify(latest.systolic, latest.diastolic);
  el.summary.innerHTML = [
    statCard(
      '直近の測定',
      `${latest.systolic} / ${latest.diastolic}`,
      `${dayKey(latest.at)}・${latest.timing === 'morning' ? '朝' : '晩'}・${latestClass.label}`,
    ),
    statCard('今日の平均', fmt(summary.today), summary.today ? `${summary.today.count}回` : '未測定'),
    statCard(
      '7日平均',
      fmt(summary.week),
      summary.weekClass ? `${summary.week.count}回・${summary.weekClass.label}` : '—',
    ),
    statCard('30日平均', fmt(summary.month), summary.month ? `${summary.month.count}回` : '—'),
    statCard('連続記録', `${summary.streak}日`, `通算 ${summary.total} 件`),
  ].join('');
}

function renderBanner(summary) {
  el.banner.hidden = Boolean(summary.today) && summary.total > 0;
  if (!el.banner.hidden) {
    el.banner.textContent = summary.total
      ? '今日はまだ測定していません。'
      : 'まずは1回、話しかけて記録してみましょう。';
  }
}

function renderChart(series, trend) {
  if (series.length < 2) {
    el.chart.innerHTML = '<p class="empty">2日ぶん以上記録すると推移グラフが表示されます。</p>';
    return;
  }
  const W = 640;
  const H = 220;
  const pad = { top: 16, right: 12, bottom: 26, left: 34 };
  const values = series.flatMap((p) => [p.systolic, p.diastolic]);
  const min = Math.min(90, Math.floor(Math.min(...values) / 10) * 10 - 5);
  const max = Math.max(140, Math.ceil(Math.max(...values) / 10) * 10 + 5);
  const x = (i) => pad.left + (i * (W - pad.left - pad.right)) / (series.length - 1);
  const y = (v) => pad.top + ((max - v) * (H - pad.top - pad.bottom)) / (max - min);
  const path = (list, key) =>
    list.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ');

  const gridLines = [];
  for (let v = Math.ceil(min / 20) * 20; v <= max; v += 20) {
    gridLines.push(
      `<line x1="${pad.left}" y1="${y(v)}" x2="${W - pad.right}" y2="${y(v)}" stroke="currentColor" stroke-opacity="0.12"/>`,
      `<text x="4" y="${y(v) + 4}" font-size="10" fill="currentColor" fill-opacity="0.55">${v}</text>`,
    );
  }
  // 家庭血圧の高血圧基準（135/85）の目安線
  const guides = [135, 85]
    .filter((v) => v >= min && v <= max)
    .map(
      (v) =>
        `<line x1="${pad.left}" y1="${y(v)}" x2="${W - pad.right}" y2="${y(v)}" stroke="#b42318" stroke-opacity="0.5" stroke-dasharray="4 4"/>`,
    );

  const labels = [0, series.length - 1].map(
    (i) =>
      `<text x="${x(i)}" y="${H - 8}" font-size="10" text-anchor="${i ? 'end' : 'start'}" fill="currentColor" fill-opacity="0.55">${series[i].date.slice(5)}</text>`,
  );

  el.chart.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="血圧の推移グラフ">
      ${gridLines.join('')}
      ${guides.join('')}
      <path d="${path(series, 'systolic')}" fill="none" stroke="#d92d20" stroke-width="2"/>
      <path d="${path(series, 'diastolic')}" fill="none" stroke="#1d4ed8" stroke-width="2"/>
      <path d="${path(trend, 'systolic')}" fill="none" stroke="#d92d20" stroke-width="1.5" stroke-opacity="0.45" stroke-dasharray="5 4"/>
      <path d="${path(trend, 'diastolic')}" fill="none" stroke="#1d4ed8" stroke-width="1.5" stroke-opacity="0.45" stroke-dasharray="5 4"/>
      ${labels.join('')}
    </svg>
    <p class="note">実線＝日別平均（赤：最高／青：最低）、点線＝7日移動平均、赤い破線＝家庭血圧の高血圧基準 135/85。</p>
  `;
}

function renderTable(summary) {
  if (!summary.total) {
    el.table.innerHTML = '<p class="empty">記録がありません。</p>';
    return;
  }
  if (view === 'daily') {
    el.table.innerHTML = `<table><thead><tr>
      <th>日付</th><th>朝</th><th>晩</th><th>1日平均</th><th>回数</th></tr></thead><tbody>
      ${summary.daily
        .slice(0, 31)
        .map(
          (d) =>
            `<tr><td>${d.date}</td><td>${fmt(d.morning)}</td><td>${fmt(d.evening)}</td><td>${fmt(d.all)}</td><td>${d.all.count}</td></tr>`,
        )
        .join('')}
      </tbody></table>`;
    return;
  }
  if (view === 'monthly') {
    el.table.innerHTML = `<table><thead><tr>
      <th>月</th><th>朝平均</th><th>晩平均</th><th>月平均</th><th>測定日数</th></tr></thead><tbody>
      ${summary.monthly
        .map(
          (m) =>
            `<tr><td>${m.month}</td><td>${fmt(m.morning)}</td><td>${fmt(m.evening)}</td><td>${fmt(m.all)}</td><td>${m.days}日</td></tr>`,
        )
        .join('')}
      </tbody></table>`;
    return;
  }
  el.table.innerHTML = `<table><thead><tr>
    <th>日時</th><th>時間帯</th><th>血圧</th><th>脈拍</th><th></th></tr></thead><tbody>
    ${records
      .slice(0, 100)
      .map((r) => {
        const d = new Date(r.at);
        return `<tr><td>${dayKey(r.at)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}</td>
          <td>${r.timing === 'morning' ? '朝' : '晩'}</td>
          <td>${r.systolic} / ${r.diastolic}</td>
          <td>${r.pulse ?? '—'}</td>
          <td><button type="button" data-delete="${r.id}">削除</button></td></tr>`;
      })
      .join('')}
    </tbody></table>`;
}

function render() {
  const summary = buildSummary(records, new Date());
  const days = Number(el.range.value);
  const series = days ? summary.series.slice(-days) : summary.series;
  const trend = days ? summary.trend.slice(-days) : summary.trend;

  renderBanner(summary);
  renderSummary(summary);
  renderChart(series, trend);
  renderTable(summary);
}

/* ---------- タブ・データ操作 ---------- */

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('is-active'));
    tab.classList.add('is-active');
    view = tab.dataset.view;
    render();
  });
});
el.range.addEventListener('change', render);

el.table.addEventListener('click', async (event) => {
  const id = event.target.dataset?.delete;
  if (!id) return;
  if (!confirm('この記録を削除しますか？')) return;
  records = await deleteRecord(id, records);
  render();
});

el.exportBtn.addEventListener('click', async () => {
  if (!records.length) {
    showError('書き出す記録がありません。');
    return;
  }
  try {
    const result = await exportCSV(
      toCSV(records),
      `blood-pressure-${dayKey(new Date().toISOString())}.csv`,
    );
    setStatus(result.message);
  } catch (error) {
    showError(`書き出しに失敗しました: ${error?.message ?? error}`);
  }
});

el.importInput.addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const imported = fromCSV(await file.text());
  event.target.value = '';
  if (!imported.length) {
    showError('取り込めるデータが見つかりませんでした。');
    return;
  }
  const { merged, added } = mergeRecords(records, imported);
  records = await saveRecords(merged);
  setStatus(`${added} 件を取り込みました（重複 ${imported.length - added} 件はスキップ）。`);
  render();
});

el.clearBtn.addEventListener('click', async () => {
  if (!confirm('すべての記録を削除します。よろしいですか？')) return;
  records = await saveRecords([]);
  render();
});

/* ---------- 起動 ---------- */

async function main() {
  document.body.dataset.platform = Capacitor.getPlatform();
  settings = await loadSettings();
  records = await loadRecords();

  el.autoSave.checked = settings.autoSave;
  el.reminderEnabled.checked = settings.reminderEnabled;
  el.reminderTime.value = `${pad2(settings.reminderHour)}:${pad2(settings.reminderMinute)}`;
  if (!reminderSupported()) {
    el.reminderEnabled.disabled = true;
    el.reminderTime.disabled = true;
    el.reminderNote.textContent = '通知はアプリ版（iOS / Android）で利用できます。';
  } else {
    // 端末の再起動などで通知が消えている場合に備えて設定し直す
    await applyReminder(settings);
    el.reminderNote.textContent = settings.reminderEnabled
      ? `毎日 ${pad2(settings.reminderHour)}:${pad2(settings.reminderMinute)} に通知します。`
      : '通知は設定されていません。';
  }

  render();
  await initSpeech();

  if (!Capacitor.isNativePlatform() && 'serviceWorker' in navigator && location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
}

main();
