// 音声認識テキストから血圧レコードを取り出すパーサ（純関数のみ / DOM 非依存）

const ZEN_TO_HAN = (s) =>
  s.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));

const KANJI_DIGIT = {
  〇: 0, 零: 0, 一: 1, 二: 2, 三: 3, 四: 4, 五: 5,
  六: 6, 七: 7, 八: 8, 九: 9,
};
const KANJI_UNIT = { 十: 10, 百: 100, 千: 1000 };

/**
 * 「百三十」「八十五」「二十」などの漢数字を算用数字に変換する。
 * 変換できない文字列はそのまま返す。
 */
export function kanjiToNumber(text) {
  let total = 0;
  let current = 0;
  let sawAny = false;

  for (const ch of text) {
    if (ch in KANJI_DIGIT) {
      current = KANJI_DIGIT[ch];
      sawAny = true;
    } else if (ch in KANJI_UNIT) {
      const unit = KANJI_UNIT[ch];
      total += (current === 0 ? 1 : current) * unit;
      current = 0;
      sawAny = true;
    } else {
      return null;
    }
  }
  if (!sawAny) return null;
  return total + current;
}

/** 文中の漢数字の連なりを算用数字に置き換える。 */
export function normalizeNumbers(text) {
  return ZEN_TO_HAN(text).replace(/[〇零一二三四五六七八九十百千]+/g, (m) => {
    const n = kanjiToNumber(m);
    return n === null ? m : String(n);
  });
}

/** 認識結果のゆらぎ（区切り文字・助詞・スペース）をならす。 */
export function normalize(text) {
  return normalizeNumbers(String(text ?? ''))
    .replace(/[，、,]/g, ' ')
    .replace(/[／/]/g, ' の ')
    .replace(/[ー－―‐-]/g, ' の ')
    .replace(/\s+/g, ' ')
    .trim();
}

const LABELS = {
  systolic: ['収縮期血圧', '収縮期', '最高血圧', '最高', '上の血圧', '上'],
  diastolic: ['拡張期血圧', '拡張期', '最低血圧', '最低', '下の血圧', '下'],
  pulse: ['脈拍数', '脈拍', '心拍数', '心拍', 'パルス', '脈'],
};

const RANGES = {
  systolic: [60, 260],
  diastolic: [30, 180],
  pulse: [30, 220],
};

const inRange = (kind, n) =>
  Number.isFinite(n) && n >= RANGES[kind][0] && n <= RANGES[kind][1];

function findLabeled(text, labels) {
  for (const label of labels) {
    // 「上が130」「最高血圧は 130」「脈:72」いずれも拾う
    const re = new RegExp(`${label}\\s*(?:が|は|の|:|：)?\\s*(\\d{2,3})`);
    const m = text.match(re);
    if (m) return { value: Number(m[1]), index: m.index, length: m[0].length };
  }
  return null;
}

/** 「朝」「晩」などの時間帯。無指定なら測定時刻から推定する。 */
export function detectTiming(text, now = new Date()) {
  if (/朝|午前|起床|モーニング/.test(text)) return 'morning';
  if (/晩|夜|夕方|就寝|寝る前|午後|イブニング/.test(text)) return 'evening';
  return now.getHours() < 12 ? 'morning' : 'evening';
}

/** 「昨日」「一昨日」「8月28日」などの日付指定を解決する。 */
export function detectDate(text, now = new Date()) {
  const base = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  const md = text.match(/(\d{1,2})\s*月\s*(\d{1,2})\s*日/);
  if (md) {
    const month = Number(md[1]) - 1;
    const day = Number(md[2]);
    const d = new Date(now.getFullYear(), month, day);
    // 未来日になったら前年の同日と解釈する
    if (d > base) d.setFullYear(d.getFullYear() - 1);
    return d;
  }
  if (/一昨日|おととい|おとつい/.test(text)) base.setDate(base.getDate() - 2);
  else if (/昨日|きのう/.test(text)) base.setDate(base.getDate() - 1);
  return base;
}

/**
 * 発話テキストを血圧レコードに変換する。
 * 例: 「今朝の血圧は上が132、下が84、脈は68」
 * @returns {{ok:boolean, record?:object, errors:string[], warnings:string[]}}
 */
export function parseUtterance(text, now = new Date()) {
  const errors = [];
  const warnings = [];
  const src = normalize(text);

  if (!src) return { ok: false, errors: ['音声を聞き取れませんでした'], warnings };

  let consumed = src;
  const take = (hit) => {
    if (!hit) return null;
    // 同じ数字を二重に使わないよう、読み取った箇所を空白で潰す（長さは維持）
    consumed =
      consumed.slice(0, hit.index) +
      ' '.repeat(hit.length) +
      consumed.slice(hit.index + hit.length);
    return hit.value;
  };

  const pulseHit = findLabeled(src, LABELS.pulse);
  let pulse = take(pulseHit);
  const sysHit = findLabeled(consumed, LABELS.systolic);
  let systolic = take(sysHit);
  const diaHit = findLabeled(consumed, LABELS.diastolic);
  let diastolic = take(diaHit);

  // ラベルが無い場合は数字の並び順（上→下→脈）で解釈する
  const rest = (consumed.match(/\d{2,3}/g) || []).map(Number);
  if (systolic == null && diastolic == null && rest.length >= 2) {
    const [a, b] = rest;
    systolic = Math.max(a, b);
    diastolic = Math.min(a, b);
    if (pulse == null && rest.length >= 3) pulse = rest[2];
  } else if (systolic == null && rest.length >= 1) {
    systolic = rest[0];
  } else if (diastolic == null && rest.length >= 1) {
    diastolic = rest[0];
  }

  if (systolic == null) errors.push('最高血圧を聞き取れませんでした');
  else if (!inRange('systolic', systolic)) errors.push(`最高血圧の値が範囲外です: ${systolic}`);

  if (diastolic == null) errors.push('最低血圧を聞き取れませんでした');
  else if (!inRange('diastolic', diastolic)) errors.push(`最低血圧の値が範囲外です: ${diastolic}`);

  if (pulse != null && !inRange('pulse', pulse)) {
    warnings.push(`脈拍の値が範囲外のため無視しました: ${pulse}`);
    pulse = null;
  }
  if (systolic != null && diastolic != null && systolic <= diastolic) {
    errors.push('最高血圧が最低血圧以下です。言い直してください');
  }

  if (errors.length) return { ok: false, errors, warnings };

  const date = detectDate(src, now);
  const at = new Date(date);
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) at.setHours(now.getHours(), now.getMinutes(), 0, 0);
  else at.setHours(8, 0, 0, 0);

  return {
    ok: true,
    errors,
    warnings,
    record: {
      at: at.toISOString(),
      systolic,
      diastolic,
      pulse,
      timing: detectTiming(src, now),
      note: '',
      source: 'voice',
      transcript: String(text).trim(),
    },
  };
}
