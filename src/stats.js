// 集計ロジック（純関数のみ / DOM 非依存）

export const pad2 = (n) => String(n).padStart(2, '0');

/** ローカル時刻の YYYY-MM-DD */
export function dayKey(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export const monthKey = (iso) => dayKey(iso).slice(0, 7);

const round1 = (n) => Math.round(n * 10) / 10;

/** 平均値。データが無ければ null。 */
export function average(records) {
  const list = records.filter(Boolean);
  if (!list.length) return null;
  const pulses = list.filter((r) => Number.isFinite(r.pulse));
  return {
    count: list.length,
    systolic: round1(list.reduce((s, r) => s + r.systolic, 0) / list.length),
    diastolic: round1(list.reduce((s, r) => s + r.diastolic, 0) / list.length),
    pulse: pulses.length
      ? round1(pulses.reduce((s, r) => s + r.pulse, 0) / pulses.length)
      : null,
  };
}

/**
 * 家庭血圧の区分（日本高血圧学会 JSH2019 の家庭血圧基準）。
 * 収縮期・拡張期のうち重い方の区分を返す。
 */
export function classify(systolic, diastolic) {
  const levels = [
    { key: 'iii', label: 'III度高血圧', sys: 160, dia: 100 },
    { key: 'ii', label: 'II度高血圧', sys: 145, dia: 90 },
    { key: 'i', label: 'I度高血圧', sys: 135, dia: 85 },
    { key: 'high', label: '高値血圧', sys: 125, dia: 75 },
    { key: 'elevated', label: '正常高値血圧', sys: 115, dia: 0 },
  ];
  for (const lv of levels) {
    if (systolic >= lv.sys || (lv.dia > 0 && diastolic >= lv.dia)) {
      return { key: lv.key, label: lv.label };
    }
  }
  return { key: 'normal', label: '正常血圧' };
}

/** 日ごとの集計（朝／晩それぞれの平均つき）。日付の新しい順。 */
export function summarizeDaily(records) {
  const byDay = new Map();
  for (const r of records) {
    const key = dayKey(r.at);
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(r);
  }
  return [...byDay.entries()]
    .map(([date, list]) => ({
      date,
      records: list.slice().sort((a, b) => new Date(a.at) - new Date(b.at)),
      all: average(list),
      morning: average(list.filter((r) => r.timing === 'morning')),
      evening: average(list.filter((r) => r.timing === 'evening')),
    }))
    .sort((a, b) => (a.date < b.date ? 1 : -1));
}

/** 月ごとの集計。新しい順。 */
export function summarizeMonthly(records) {
  const byMonth = new Map();
  for (const r of records) {
    const key = monthKey(r.at);
    if (!byMonth.has(key)) byMonth.set(key, []);
    byMonth.get(key).push(r);
  }
  return [...byMonth.entries()]
    .map(([month, list]) => ({
      month,
      days: new Set(list.map((r) => dayKey(r.at))).size,
      all: average(list),
      morning: average(list.filter((r) => r.timing === 'morning')),
      evening: average(list.filter((r) => r.timing === 'evening')),
    }))
    .sort((a, b) => (a.month < b.month ? 1 : -1));
}

/** 直近 days 日ぶんのレコードを抜き出す。 */
export function withinDays(records, days, now = new Date()) {
  const from = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  from.setDate(from.getDate() - (days - 1));
  return records.filter((r) => new Date(r.at) >= from);
}

/** 日次平均系列に対する移動平均。系列は日付の古い順で渡す。 */
export function movingAverage(series, window = 7) {
  return series.map((point, i) => {
    const slice = series.slice(Math.max(0, i - window + 1), i + 1);
    return {
      date: point.date,
      systolic: round1(slice.reduce((s, p) => s + p.systolic, 0) / slice.length),
      diastolic: round1(slice.reduce((s, p) => s + p.diastolic, 0) / slice.length),
    };
  });
}

/** 今日（または直近の測定日）から遡って連続で記録できている日数。 */
export function measurementStreak(records, now = new Date()) {
  const days = new Set(records.map((r) => dayKey(r.at)));
  if (!days.size) return 0;
  const cursor = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const key = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  // 今日まだ未測定でも、昨日までの連続記録は途切れていないものとして数える
  if (!days.has(key(cursor))) cursor.setDate(cursor.getDate() - 1);
  let streak = 0;
  while (days.has(key(cursor))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
}

/** ダッシュボード用のまとめ。 */
export function buildSummary(records, now = new Date()) {
  const sorted = records.slice().sort((a, b) => new Date(a.at) - new Date(b.at));
  const daily = summarizeDaily(sorted);
  const series = daily
    .slice()
    .reverse()
    .map((d) => ({ date: d.date, systolic: d.all.systolic, diastolic: d.all.diastolic }));

  const week = average(withinDays(sorted, 7, now));
  const month = average(withinDays(sorted, 30, now));
  const latest = sorted[sorted.length - 1] ?? null;

  return {
    total: sorted.length,
    latest,
    today: average(sorted.filter((r) => dayKey(r.at) === dayKey(now.toISOString()))),
    week,
    month,
    weekClass: week ? classify(week.systolic, week.diastolic) : null,
    streak: measurementStreak(sorted, now),
    daily,
    monthly: summarizeMonthly(sorted),
    series,
    trend: movingAverage(series, 7),
  };
}
