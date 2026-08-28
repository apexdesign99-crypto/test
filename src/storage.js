// 保存（Capacitor Preferences / ブラウザは localStorage 実装が使われる）と CSV 入出力

import { Preferences } from '@capacitor/preferences';
import { dayKey, pad2 } from './stats.js';

const KEY = 'bp-voice-tally/records/v1';
const SETTINGS_KEY = 'bp-voice-tally/settings/v1';

export const DEFAULT_SETTINGS = {
  autoSave: true,
  lang: 'ja-JP',
  reminderEnabled: false,
  reminderHour: 7,
  reminderMinute: 0,
};

const uid = () =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

const isRecord = (r) =>
  r && typeof r.at === 'string' && Number(r.systolic) > 0 && Number(r.diastolic) > 0;

async function readJSON(key, fallback) {
  try {
    const { value } = await Preferences.get({ key });
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

const writeJSON = (key, value) =>
  Preferences.set({ key, value: JSON.stringify(value) });

const byNewest = (a, b) => new Date(b.at) - new Date(a.at);

export async function loadRecords() {
  const list = await readJSON(KEY, []);
  return Array.isArray(list) ? list.filter(isRecord).sort(byNewest) : [];
}

export async function saveRecords(records) {
  const sorted = records.filter(isRecord).slice().sort(byNewest);
  await writeJSON(KEY, sorted);
  return sorted;
}

export async function addRecord(record, records) {
  return saveRecords([...records, { id: uid(), ...record }]);
}

export async function deleteRecord(id, records) {
  return saveRecords(records.filter((r) => r.id !== id));
}

export async function loadSettings() {
  return { ...DEFAULT_SETTINGS, ...(await readJSON(SETTINGS_KEY, {})) };
}

export async function saveSettings(settings) {
  await writeJSON(SETTINGS_KEY, settings);
  return settings;
}

/** 取り込み時の重複判定キー（同じ日時・同じ値は 1 件とみなす） */
export const recordKey = (r) => `${r.at}|${r.systolic}|${r.diastolic}`;

/** 既存レコードに取り込み分をマージする（重複は捨てる）。 */
export function mergeRecords(existing, incoming) {
  const known = new Set(existing.map(recordKey));
  const added = incoming.filter((r) => !known.has(recordKey(r)));
  return { merged: [...existing, ...added], added: added.length };
}

/* ---------- CSV ---------- */

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

/** 書き出した CSV を読み戻す（同じ形式の他アプリのデータにも対応）。 */
export function fromCSV(text) {
  const lines = String(text).replace(/^﻿/, '').trim().split(/\r?\n/).filter(Boolean);
  const out = [];
  for (const line of lines.slice(1)) {
    const cells = line
      .match(/("([^"]|"")*"|[^,]*)(,|$)/g)
      ?.map((c) => c.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"'));
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
      timing:
        timing === '晩'
          ? 'evening'
          : timing === '朝'
            ? 'morning'
            : at.getHours() < 12
              ? 'morning'
              : 'evening',
      note: note ?? '',
      source: 'csv',
    });
  }
  return out;
}
