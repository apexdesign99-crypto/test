import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import Anthropic from "@anthropic-ai/sdk";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";
import { streamReply, type ChatLang, type ChatTurn } from "./claude.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(here, "..", "public");

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".json": "application/json; charset=utf-8",
};

class BadRequest extends Error {}

/** 本文を読み切る。上限を超えたら即座に打ち切る。 */
async function readBody(req: IncomingMessage, limitBytes: number): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limitBytes) throw new BadRequest("リクエストが大きすぎます");
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function parseChatRequest(raw: string): { messages: ChatTurn[]; lang: ChatLang } {
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new BadRequest("JSON として解釈できません");
  }
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

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

function sendEvent(res: ServerResponse, event: string, data: unknown): void {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

async function handleChat(req: IncomingMessage, res: ServerResponse): Promise<void> {
  let request: { messages: ChatTurn[]; lang: ChatLang };
  try {
    request = parseChatRequest(await readBody(req, 256 * 1024));
  } catch (error) {
    const message = error instanceof BadRequest ? error.message : "リクエストを読み取れませんでした";
    sendJson(res, 400, { error: message });
    return;
  }

  // ブラウザが読み込みを中断したら Claude への呼び出しも止める（言い直し・バージイン対策）。
  const controller = new AbortController();
  res.on("close", () => controller.abort());

  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });

  try {
    for await (const chunk of streamReply({ ...request, signal: controller.signal })) {
      if (res.writableEnded) break;
      if (chunk.type === "delta") {
        sendEvent(res, "delta", { text: chunk.text });
      } else {
        sendEvent(res, "done", { model: chunk.model, refused: chunk.refused });
      }
    }
  } catch (error) {
    if (!controller.signal.aborted) {
      console.error("[chat]", error);
      if (!res.writableEnded) {
        sendEvent(res, "error", { message: describeError(error) });
      }
    }
  } finally {
    res.end();
  }
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

async function serveStatic(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const requestPath = new URL(req.url ?? "/", "http://localhost").pathname;
  const relative = requestPath === "/" ? "index.html" : decodeURIComponent(requestPath).replace(/^\/+/, "");
  const filePath = path.resolve(publicDir, relative);

  // publicDir の外に出るパスは拒否する。
  if (filePath !== publicDir && !filePath.startsWith(publicDir + path.sep)) {
    sendJson(res, 403, { error: "forbidden" });
    return;
  }

  try {
    const info = await stat(filePath);
    if (!info.isFile()) throw new Error("not a file");
    res.writeHead(200, {
      "content-type": MIME_TYPES[path.extname(filePath)] ?? "application/octet-stream",
      "content-length": info.size,
      "cache-control": "no-cache",
    });
    createReadStream(filePath).pipe(res);
  } catch {
    sendJson(res, 404, { error: "not found" });
  }
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");

  if (url.pathname === "/api/chat") {
    if (req.method !== "POST") {
      sendJson(res, 405, { error: "POST を使ってください" });
      return;
    }
    void handleChat(req, res);
    return;
  }

  if (url.pathname === "/api/health") {
    sendJson(res, 200, { ok: true, model: config.model, effort: config.effort });
    return;
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    sendJson(res, 405, { error: "method not allowed" });
    return;
  }

  void serveStatic(req, res);
});

if (!process.env["ANTHROPIC_API_KEY"] && !process.env["ANTHROPIC_AUTH_TOKEN"]) {
  console.warn("[warn] ANTHROPIC_API_KEY が未設定です。.env に設定するか、ant auth login のプロファイルを使ってください。");
}

server.listen(config.port, config.host, () => {
  console.log(`音声会話サーバー: http://${config.host}:${config.port}  (model: ${config.model}, effort: ${config.effort})`);
});
