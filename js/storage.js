// 保存（localStorage）と CSV 入出力

import { dayKey, pad2 } from './stats.js';

const KEY = 'bp-voice-tally/records/v1';
const SETTINGS_KEY = 'bp-voice-tally/settings/v1';

const uid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export function loadRecords() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list.filter((r) => r && r.at && r.systolic) : [];
  } catch {
    return [];
  }
}

export function saveRecords(records) {
  const sorted = records
    .slice()
    .sort((a, b) => new Date(b.at) - new Date(a.at));
  localStorage.setItem(KEY, JSON.stringify(sorted));
  return sorted;
}

export function addRecord(record) {
  const records = loadRecords();
  records.push({ id: uid(), ...record });
  return saveRecords(records);
}

export function deleteRecord(id) {
  return saveRecords(loadRecords().filter((r) => r.id !== id));
}

export function loadSettings() {
  const defaults = { autoSave: true, lang: 'ja-JP', reminderHour: 7 };
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') };
  } catch {
    return defaults;
  }
}

export function saveSettings(settings) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  return settings;
}

const CSV_HEADER = '日付,時刻,時間帯,最高血圧,最低血圧,脈拍,メモ';

const csvCell = (v) => {
  const s = String(v ?? '');
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function toCSV(records) {
  const rows = records
    .slice()
    .sort((a, b) => new Date(a.at) - new Date(b.at))
    .map((r) => {
      const d = new Date(r.at);
      return [
        dayKey(r.at),
        `${pad2(d.getHours())}:${pad2(d.getMinutes())}`,
        r.timing === 'morning' ? '朝' : '晩',
        r.systolic,
        r.diastolic,
        r.pulse ?? '',
        r.note ?? '',
      ]
        .map(csvCell)
        .join(',');
    });
  return [CSV_HEADER, ...rows].join('\n');
}

/** 書き出した CSV を読み戻す（他アプリの同形式にも対応）。 */
export function fromCSV(text) {
  const lines = String(text).trim().split(/\r?\n/).filter(Boolean);
  const out = [];
  for (const line of lines.slice(1)) {
    const cells = line.match(/("([^"]|"")*"|[^,]*)(,|$)/g)?.map((c) =>
      c.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"'),
    );
    if (!cells || cells.length < 5) continue;
    const [date, time, timing, sys, dia, pulse, note] = cells;
    const at = new Date(`${date}T${(time || '08:00').padStart(5, '0')}:00`);
    if (Number.isNaN(at.getTime()) || !Number(sys) || !Number(dia)) continue;
    out.push({
      id: uid(),
      at: at.toISOString(),
      systolic: Number(sys),
      diastolic: Number(dia),
      pulse: Number(pulse) || null,
      timing: timing === '晩' ? 'evening' : timing === '朝' ? 'morning' : at.getHours() < 12 ? 'morning' : 'evening',
      note: note ?? '',
      source: 'csv',
    });
  }
  return out;
}
