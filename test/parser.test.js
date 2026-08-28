import test from 'node:test';
import assert from 'node:assert/strict';
import { detectTiming, kanjiToNumber, normalize, parseUtterance } from '../src/parser.js';

const NOW = new Date(2026, 7, 28, 7, 30); // 2026-08-28 07:30

test('漢数字を算用数字に変換する', () => {
  assert.equal(kanjiToNumber('百三十'), 130);
  assert.equal(kanjiToNumber('八十五'), 85);
  assert.equal(kanjiToNumber('二百'), 200);
  assert.equal(kanjiToNumber('十二'), 12);
  assert.equal(kanjiToNumber('あ'), null);
});

test('全角数字と区切り文字をならす', () => {
  assert.equal(normalize('１３２、８４'), '132 84');
  assert.equal(normalize('132/84'), '132 の 84');
});

test('ラベル付きの発話を解釈する', () => {
  const r = parseUtterance('朝の血圧、上が132、下が84、脈は68', NOW);
  assert.ok(r.ok, r.errors.join(','));
  assert.equal(r.record.systolic, 132);
  assert.equal(r.record.diastolic, 84);
  assert.equal(r.record.pulse, 68);
  assert.equal(r.record.timing, 'morning');
});

test('最高・最低という言い方も解釈する', () => {
  const r = parseUtterance('最高血圧145 最低血圧92 脈拍数80', NOW);
  assert.ok(r.ok);
  assert.deepEqual(
    [r.record.systolic, r.record.diastolic, r.record.pulse],
    [145, 92, 80],
  );
});

test('ラベルなしは大きい方を最高血圧として並び順で解釈する', () => {
  const r = parseUtterance('128 82 70', NOW);
  assert.ok(r.ok);
  assert.deepEqual(
    [r.record.systolic, r.record.diastolic, r.record.pulse],
    [128, 82, 70],
  );
});

test('「130の85」のような言い方も解釈する', () => {
  const r = parseUtterance('血圧は130の85', NOW);
  assert.ok(r.ok);
  assert.equal(r.record.systolic, 130);
  assert.equal(r.record.diastolic, 85);
  assert.equal(r.record.pulse, null);
});

test('漢数字の発話も解釈する', () => {
  const r = parseUtterance('上が百三十、下が八十五', NOW);
  assert.ok(r.ok);
  assert.equal(r.record.systolic, 130);
  assert.equal(r.record.diastolic, 85);
});

test('脈拍の数字を血圧として二重に使わない', () => {
  const r = parseUtterance('脈は68、上が132、下が84', NOW);
  assert.ok(r.ok);
  assert.deepEqual(
    [r.record.systolic, r.record.diastolic, r.record.pulse],
    [132, 84, 68],
  );
});

test('数字が足りなければエラーを返す', () => {
  const r = parseUtterance('今日は調子がいい', NOW);
  assert.equal(r.ok, false);
  assert.equal(r.errors.length, 2);
});

test('範囲外の値はエラーにする', () => {
  const r = parseUtterance('上が532、下が84', NOW);
  assert.equal(r.ok, false);
  assert.match(r.errors.join(), /範囲外/);
});

test('最高が最低以下ならエラーにする', () => {
  const r = parseUtterance('上が84、下が132', NOW);
  assert.equal(r.ok, false);
  assert.match(r.errors.join(), /最低血圧以下/);
});

test('範囲外の脈拍は警告つきで無視する', () => {
  const r = parseUtterance('上が132、下が84、脈は12', NOW);
  assert.ok(r.ok);
  assert.equal(r.record.pulse, null);
  assert.equal(r.warnings.length, 1);
});

test('時間帯は発話から、無ければ時刻から決める', () => {
  assert.equal(detectTiming('夜の血圧', NOW), 'evening');
  assert.equal(detectTiming('血圧', NOW), 'morning');
  assert.equal(detectTiming('血圧', new Date(2026, 7, 28, 21, 0)), 'evening');
});

test('「昨日」の測定として記録できる', () => {
  const r = parseUtterance('昨日の夜、上が128、下が80', NOW);
  assert.ok(r.ok);
  assert.equal(new Date(r.record.at).getDate(), 27);
  assert.equal(r.record.timing, 'evening');
});

test('日付を明示した発話も解釈する', () => {
  const r = parseUtterance('8月20日の朝、上が126、下が78', NOW);
  assert.ok(r.ok);
  const at = new Date(r.record.at);
  assert.equal(at.getMonth(), 7);
  assert.equal(at.getDate(), 20);
});
