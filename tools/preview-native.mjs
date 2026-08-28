// ネイティブ分岐をブラウザで確認するためのプレビューページを作って配信する。
// 実機と同じコードパス（Capacitor プラグイン呼び出し）をスタブ越しに動かせる。
import { readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join } from 'node:path';

const OUT = 'www/__native-preview.html';
const html = readFileSync('www/index.html', 'utf8');
const mock = readFileSync('tools/native-bridge-mock.js', 'utf8');

writeFileSync(
  OUT,
  html.replace(
    '<script src="js/app.js"></script>',
    `<script>${mock}</script>\n    <script src="js/app.js"></script>`,
  ),
);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.webmanifest': 'application/manifest+json',
  '.map': 'application/json',
};

const port = Number(process.env.PORT || 8080);
createServer((req, res) => {
  const path = join('www', decodeURIComponent(req.url.split('?')[0]));
  try {
    const body = readFileSync(path);
    res.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404).end('not found');
  }
}).listen(port, () => {
  console.log(`ネイティブ模擬プレビュー: http://localhost:${port}/__native-preview.html`);
});
