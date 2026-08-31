import path from "node:path";
import { fileURLToPath } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";
import { bodyLimit } from "hono/body-limit";
import { streamSSE } from "hono/streaming";
import { authRequired, config, security } from "./config.js";
import { streamReply, type ChatLang, type ChatTurn } from "./claude.js";
import {
  acquireStreamSlot,
  assertSafeBinding,
  handleLogin,
  handleLogout,
  logAccess,
  originGuard,
  rateLimit,
  requireAuth,
  securityHeaders,
  sessionState,
} from "./security.js";

const here = path.dirname(fileURLToPath(import.meta.url));
// 起動時のカレントディレクトリに依存しないよう絶対パスで渡す。
const publicDir = path.resolve(here, "..", "public");

class BadRequest extends Error {}

function parseChatRequest(body: unknown): { messages: ChatTurn[]; lang: ChatLang } {
  if (typeof body !== "object" || body === null) throw new BadRequest("オブジェクトを送ってください");

  const { messages, lang } = body as { messages?: unknown; lang?: unknown };
  if (!Array.isArray(messages) || messages.length === 0) {
    throw new BadRequest("messages が空です");
  }
  if (messages.length > config.maxHistoryMessages) {
    throw new BadRequest(`会話履歴は ${config.maxHistoryMessages} 件までです`);
  }

  const turns: ChatTurn[] = messages.map((entry, index) => {
    const turn = entry as { role?: unknown; content?: unknown };
    if (turn.role !== "user" && turn.role !== "assistant") {
      throw new BadRequest(`messages[${index}].role が不正です`);
    }
    if (typeof turn.content !== "string" || turn.content.trim() === "") {
      throw new BadRequest(`messages[${index}].content が空です`);
    }
    if (turn.content.length > config.maxMessageChars) {
      throw new BadRequest(`messages[${index}].content が長すぎます`);
    }
    return { role: turn.role, content: turn.content };
  });

  if (turns[turns.length - 1]?.role !== "user") {
    throw new BadRequest("最後の発話は user である必要があります");
  }

  return { messages: turns, lang: lang === "en" ? "en" : "ja" };
}

function describeError(error: unknown): string {
  if (error instanceof Anthropic.AuthenticationError) {
    return "API キーが受け付けられませんでした。ANTHROPIC_API_KEY を確認してください。";
  }
  if (error instanceof Anthropic.RateLimitError) {
    return "レート制限に達しました。少し待ってからもう一度話しかけてください。";
  }
  if (error instanceof Anthropic.APIConnectionError) {
    return "Claude API に接続できませんでした。ネットワークを確認してください。";
  }
  if (error instanceof Anthropic.APIError) {
    return `Claude API がエラーを返しました (${error.status ?? "不明"})。`;
  }
  return "返答の生成中にエラーが発生しました。";
}

const app = new Hono();

app.use("/*", securityHeaders());

app.get("/api/health", (c) => c.json({ ok: true, model: config.model, effort: config.effort }));

app.get("/api/session", (c) => c.json(sessionState(c)));

app.post("/api/login", originGuard(), rateLimit(security.loginPerMinute, "login"), handleLogin);
app.post("/api/logout", originGuard(), (c) => handleLogout(c));

app.post(
  "/api/chat",
  originGuard(),
  requireAuth(),
  rateLimit(security.chatPerMinute, "chat"),
  bodyLimit({
    maxSize: 256 * 1024,
    onError: (c) => c.json({ error: "リクエストが大きすぎます" }, 413),
  }),
  async (c) => {
    let request: { messages: ChatTurn[]; lang: ChatLang };
    try {
      request = parseChatRequest(await c.req.json());
    } catch (error) {
      const message = error instanceof BadRequest ? error.message : "リクエストを読み取れませんでした";
      return c.json({ error: message }, 400);
    }

    const releaseSlot = acquireStreamSlot(c);
    if (!releaseSlot) {
      logAccess(c, "stream_limit");
      return c.json({ error: "同時に処理できる会話数を超えました。少し待ってからお試しください。" }, 429);
    }

    c.header("X-Accel-Buffering", "no"); // リバースプロキシに束ねさせない

    const startedAt = Date.now();
    return streamSSE(c, async (stream) => {
      // ブラウザが読み込みを中断したら Claude への呼び出しも止める（言い直し・バージイン対策）。
      const controller = new AbortController();
      stream.onAbort(() => controller.abort());

      try {
        for await (const chunk of streamReply({ ...request, signal: controller.signal })) {
          if (stream.aborted) break;
          if (chunk.type === "delta") {
            await stream.writeSSE({ event: "delta", data: JSON.stringify({ text: chunk.text }) });
          } else {
            await stream.writeSSE({
              event: "done",
              data: JSON.stringify({ model: chunk.model, refused: chunk.refused }),
            });
          }
        }
        logAccess(c, "chat_ok", { turns: request.messages.length, ms: Date.now() - startedAt });
      } catch (error) {
        if (controller.signal.aborted) {
          logAccess(c, "chat_aborted", { ms: Date.now() - startedAt });
          return;
        }
        console.error("[chat]", error);
        logAccess(c, "chat_error", { ms: Date.now() - startedAt });
        await stream.writeSSE({ event: "error", data: JSON.stringify({ message: describeError(error) }) });
      } finally {
        releaseSlot();
      }
    });
  },
);

app.all("/api/chat", (c) => c.json({ error: "POST を使ってください" }, 405));

app.use("/*", serveStatic({ root: publicDir }));

app.notFound((c) => c.json({ error: "not found" }, 404));

assertSafeBinding();

if (!process.env["ANTHROPIC_API_KEY"] && !process.env["ANTHROPIC_AUTH_TOKEN"]) {
  console.warn("[warn] ANTHROPIC_API_KEY が未設定です。.env に設定するか、ant auth login のプロファイルを使ってください。");
}

serve({ fetch: app.fetch, hostname: config.host, port: config.port }, (info) => {
  console.log(`音声会話サーバー: http://${config.host}:${info.port}  (model: ${config.model}, effort: ${config.effort})`);
  console.log(
    authRequired
      ? "  認証: アクセストークンによるログインが必要です"
      : "  認証: なし（ループバック専用。外部に出す場合は VOICE_ACCESS_TOKEN を設定してください）",
  );
});
