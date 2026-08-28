import test from 'node:test';
import assert from 'node:assert/strict';
import {
  average,
  buildSummary,
  classify,
  measurementStreak,
  movingAverage,
  summarizeDaily,
  summarizeMonthly,
  withinDays,
} from '../js/stats.js';

const NOW = new Date(2026, 7, 28, 20, 0);
const at = (day, hour) => new Date(2026, 7, day, hour, 0).toISOString();

const sample = [
  { at: at(26, 7), systolic: 130, diastolic: 82, pulse: 70, timing: 'morning' },
  { at: at(26, 21), systolic: 124, diastolic: 78, pulse: 66, timing: 'evening' },
  { at: at(27, 7), systolic: 138, diastolic: 88, pulse: null, timing: 'morning' },
  { at: at(28, 7), systolic: 134, diastolic: 84, pulse: 72, timing: 'morning' },
];

test('平均を計算する（脈拍は記録がある分だけ）', () => {
  const avg = average(sample);
  assert.equal(avg.count, 4);
  assert.equal(avg.systolic, 131.5);
  assert.equal(avg.diastolic, 83);
  assert.equal(avg.pulse, 69.3);
  assert.equal(average([]), null);
});

test('家庭血圧の区分を判定する', () => {
  assert.equal(classify(112, 70).key, 'normal');
  assert.equal(classify(118, 70).key, 'elevated');
  assert.equal(classify(128, 74).key, 'high');
  assert.equal(classify(120, 78).key, 'high'); // 拡張期だけ高い場合も拾う
  assert.equal(classify(136, 84).key, 'i');
  assert.equal(classify(150, 88).key, 'ii');
  assert.equal(classify(162, 96).key, 'iii');
});

test('日別に朝と晩を分けて集計する', () => {
  const daily = summarizeDaily(sample);
  assert.equal(daily.length, 3);
  assert.equal(daily[0].date, '2026-08-28'); // 新しい順
  const d26 = daily.find((d) => d.date === '2026-08-26');
  assert.equal(d26.morning.systolic, 130);
  assert.equal(d26.evening.systolic, 124);
  assert.equal(d26.all.systolic, 127);
});

test('月別に集計する', () => {
  const monthly = summarizeMonthly(sample);
  assert.equal(monthly.length, 1);
  assert.equal(monthly[0].month, '2026-08');
  assert.equal(monthly[0].days, 3);
});

test('直近N日で絞り込む', () => {
  assert.equal(withinDays(sample, 2, NOW).length, 2);
  assert.equal(withinDays(sample, 7, NOW).length, 4);
});

test('移動平均を計算する', () => {
  const series = [
    { date: 'a', systolic: 120, diastolic: 80 },
    { date: 'b', systolic: 130, diastolic: 84 },
    { date: 'c', systolic: 140, diastolic: 88 },
  ];
  const ma = movingAverage(series, 2);
  assert.equal(ma[0].systolic, 120);
  assert.equal(ma[1].systolic, 125);
  assert.equal(ma[2].systolic, 135);
});

test('連続記録日数を数える', () => {
  assert.equal(measurementStreak(sample, NOW), 3);
  assert.equal(measurementStreak([], NOW), 0);
  // 今日が未測定でも昨日までの連続は保つ
  const yesterdayEnd = sample.filter((r) => new Date(r.at).getDate() < 28);
  assert.equal(measurementStreak(yesterdayEnd, NOW), 2);
  // 2日以上空いたら途切れる
  assert.equal(measurementStreak([{ at: at(20, 7), systolic: 120, diastolic: 80 }], NOW), 0);
});

test('ダッシュボード用のまとめを作る', () => {
  const s = buildSummary(sample, NOW);
  assert.equal(s.total, 4);
  assert.equal(s.latest.systolic, 134);
  assert.equal(s.today.count, 1);
  assert.equal(s.week.count, 4);
  assert.equal(s.weekClass.key, 'high'); // 平均 131.5/83 は高値血圧
  assert.equal(s.streak, 3);
  assert.equal(s.series.length, 3);
  assert.equal(s.series[0].date, '2026-08-26'); // グラフ用は古い順
  assert.equal(s.trend.length, 3);
});
