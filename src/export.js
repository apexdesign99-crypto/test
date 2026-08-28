// CSV の受け渡し：ネイティブは共有シート、ブラウザはダウンロード

import { Capacitor } from '@capacitor/core';
import { Directory, Encoding, Filesystem } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';

/** Excel で文字化けしないよう BOM を付ける */
const withBOM = (csv) => `﻿${csv}`;

export async function exportCSV(csv, fileName) {
  if (!Capacitor.isNativePlatform()) {
    const blob = new Blob([withBOM(csv)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    a.click();
    URL.revokeObjectURL(url);
    return { ok: true, message: 'CSV を書き出しました。' };
  }

  const { uri } = await Filesystem.writeFile({
    path: fileName,
    data: withBOM(csv),
    directory: Directory.Cache,
    encoding: Encoding.UTF8,
  });
  await Share.share({
    title: '血圧の記録',
    text: '血圧の記録（CSV）',
    url: uri,
    dialogTitle: '血圧の記録を共有',
  });
  return { ok: true, message: 'CSV を共有しました。' };
}
