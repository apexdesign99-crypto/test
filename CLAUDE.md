# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

音声会話アプリ。ブラウザの Web Speech API で音声認識・読み上げを行い、Hono (Node) のサーバー経由で Claude API を呼ぶ。

## コマンド

```bash
npm install
npm run build       # tsc: src/ → dist/
npm start           # dist/server.js を起動（.env があれば読む）
npm run dev         # tsc --watch + node --watch
npm run typecheck   # tsc --noEmit
```

自動テストは無い。変更後は `npm run typecheck` に加えて、`npm start` してブラウザで実際に会話するのが唯一の検証手段。
`curl -N -X POST localhost:3000/api/chat -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"こんにちは"}],"lang":"ja"}'`
で SSE だけを確認できる（マイク不要）。

## アーキテクチャ

1 ターンの流れは 3 ファイルにまたがる。

```
public/app.js   音声認識 → history に push → POST /api/chat
src/server.ts   検証 → streamReply() → streamSSE (delta / done / error)
src/claude.ts   client.beta.messages.stream() → text_delta を yield
public/app.js   吹き出しに追記 + 文単位で読み上げキューへ → 読み上げ完了でハンズフリー再開
```

押さえておくべき点:

- **サーバーは状態を持たない。** 会話履歴はブラウザのメモリにあり、毎ターン全部送られる。件数上限は
  `src/config.ts` の `maxHistoryMessages` と `public/app.js` の `MAX_HISTORY` の両方にあるので、片方だけ変えない。
- **SSE のイベント名 `delta` / `done` / `error`** は `src/server.ts` と `public/app.js` に直書きされている。対で変更する。
- **返答はそのまま音声合成に流れる。** `src/claude.ts` のシステムプロンプトが Markdown 禁止・短文・記号を読める形に
  開く、を指示している。ここを緩めると読み上げが記号だらけになる。
- **読み上げは文が完成した時点で開始する。** `app.js` の `tts` が句点で区切ってキューに積み、
  キューが空になった時 `drain()` → `onDrain` でハンズフリーのマイク再開を行う。`sendTurn()` の `finally` では
  `sending = false` より先に `onDrain` を登録すること（最後の発話が先に終わると取りこぼす）。
- **話し終わりの判定は自前で持っている。** `continuous = true` でマイクを開けたまま、`onresult` のたびに
  無音タイマー（既定 700ms、設定で 0.3〜2 秒）を引き直す。満了時に確定テキストがあれば `dispatchTurn()` で送信、
  確定がまだなら `recognition.stop()` で確定を促す。`onspeechend` が来たらタイマーを待たずに送る。
  ブラウザ任せの終話検知（1〜2 秒）より速いのが狙いなので、ここを `onend` 送信に戻すと目に見えて遅くなる。
  `onend` の送信は continuous 非対応ブラウザ向けの受け皿として残してある。
- 送信前にマイクを閉じる（`abort()`）のは自分の読み上げを拾わないため。二重送信は `dispatched` で防ぐ。
  声が届かないまま 15 秒経つとマイクを閉じる。

`src/server.ts` は Hono + `@hono/node-server`。ここで気をつける点:

- `streamSSE()` の `stream.onAbort()` でブラウザの切断を拾い、`AbortController` 経由で Claude 呼び出しごと止める。
- ルートは登録順に照合される。`app.post("/api/chat")` の後ろに `app.all("/api/chat")` を置いて 405 を返している。
  この 2 つを入れ替えると POST も 405 になる。
- `serveStatic({ root })` の root は絶対パス（`import.meta.url` から算出）。相対パスにすると起動時の
  カレントディレクトリに依存する。パストラバーサルは serveStatic 側で弾かれる。

## 環境変数と API の作法

- アプリの設定は `VOICE_*` プレフィックス（`VOICE_MODEL` / `VOICE_EFFORT` / `VOICE_MAX_TOKENS`）。
  `CLAUDE_EFFORT` などの名前は使わない — Claude Code 自身が同名の変数を設定しており、実行環境で衝突する。
- モデルは `claude-opus-5`、`effort` の既定は会話のテンポ優先で `low`。
- `fallbacks: "default"` とベータ `server-side-fallback-2026-07-01` は必ず対。片方だけ渡すと 400 になる。
  `src/claude.ts` はこの 400 を検知したら以後フォールバック指定を外して再試行する（`fallbackSupported`）。
- エラー文言は SDK の型付き例外（`Anthropic.AuthenticationError` など）で分岐する。文字列マッチはしない。

## セキュリティ層

防御は `src/security.ts` に集約してある。ルートに足すときは `originGuard()` → `requireAuth()` →
`rateLimit()` の順に並べる（Origin を弾いてから認証、最後に流量制御）。

- **既定はローカル 1 人用**（認証なし）。`VOICE_ACCESS_TOKEN` を設定すると `authRequired` が立ち、
  ログイン必須になる。`assertSafeBinding()` が、ループバック以外に bind × 認証なしの組み合わせで起動を止める。
- **CSP はインライン JS / CSS を禁止**している。`public/` に `<script>` の中身や `style=""` を書くと動かなくなる。
  favicon の data: URI のために `img-src` だけ緩めてある。
- **レート制限とセッションはプロセス内メモリ。** 複数プロセスに分散させるなら共有ストアが要る。
- **ログに発話内容を入れない。** `logAccess()` は時刻・IP・パス・結果だけを出す。ここに `content` を
  足すと、会話の中身がサーバーのログに残る。
- アクセストークンの比較は `sameSecret()`（SHA-256 → `timingSafeEqual`）を使う。`===` で比較しない。
- `requireAuth()` は Cookie セッションに加えて `Authorization: Bearer <token>` も受け付ける
  （`bearerToken()`）。Cookie を持てないクライアント（iOS ショートカット等）向けの経路で、
  `/api/say` はこれを使う想定。

## `/api/say`（ブラウザ以外向け）

`/api/chat` は SSE 前提なので、SSE を読めないクライアント（iOS ショートカット等）向けに単発の
`POST /api/say` を用意してある。会話履歴を持たず、`streamReply()` の delta をサーバー側で
連結してプレーンテキストで一度に返すだけ。ここに履歴やストリーミングを足したくなったら、
それはもう `/api/chat` の役目なので、`/api/say` を複雑にせず素直に `/api/chat` を使わせること。

## ブラウザ側の前提

- マイクは `https://` か `localhost` でのみ使える。
- 音声認識は Chrome / Edge / Safari のみ。Firefox では mic ボタンを無効化し、テキスト入力に倒す。
- `public/` はビルドしない素の ES モジュール。`tsc` の対象は `src/` だけ。
