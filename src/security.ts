// アプリの防御層をここに集約する。
//   1. セキュリティヘッダ（CSP ほか）
//   2. Origin 検査（CSRF 対策）
//   3. アクセストークンによるログインとセッション Cookie
//   4. レート制限と同時ストリーム数の制限
//   5. 本文を記録しないアクセスログ
// レート制限とセッションはプロセス内のメモリに持つ。再起動で消え、
// 複数プロセスにスケールさせる場合は共有ストア（Redis 等）が要る。

import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { getConnInfo } from "@hono/node-server/conninfo";
import type { Context, MiddlewareHandler } from "hono";
import { deleteCookie, getCookie, setCookie } from "hono/cookie";
import { authRequired, config, security } from "./config.js";

const SESSION_COOKIE = "voice_session";

// -------------------------------------------------------------- セッション

/** セッション ID → 失効時刻(epoch ms) */
const sessions = new Map<string, number>();

function pruneSessions(now: number): void {
  for (const [id, expiresAt] of sessions) {
    if (expiresAt <= now) sessions.delete(id);
  }
}

function createSession(): { id: string; maxAgeSeconds: number } {
  const now = Date.now();
  pruneSessions(now);
  const id = randomBytes(32).toString("base64url");
  const maxAgeSeconds = security.sessionTtlHours * 3600;
  sessions.set(id, now + maxAgeSeconds * 1000);
  return { id, maxAgeSeconds };
}

function sessionAlive(id: string | undefined): boolean {
  if (!id) return false;
  const expiresAt = sessions.get(id);
  if (expiresAt === undefined) return false;
  if (expiresAt <= Date.now()) {
    sessions.delete(id);
    return false;
  }
  return true;
}

/** 長さの違いを漏らさないよう、ハッシュ同士を固定長で比較する。 */
function sameSecret(a: string, b: string): boolean {
  const left = createHash("sha256").update(a).digest();
  const right = createHash("sha256").update(b).digest();
  return timingSafeEqual(left, right);
}

// -------------------------------------------------------------- 接続元

function isHttps(c: Context): boolean {
  if (security.trustProxy && c.req.header("x-forwarded-proto") === "https") return true;
  return new URL(c.req.url).protocol === "https:";
}

function clientIp(c: Context): string {
  if (security.trustProxy) {
    const forwarded = c.req.header("x-forwarded-for");
    const first = forwarded?.split(",")[0]?.trim();
    if (first) return first;
  }
  return getConnInfo(c).remote.address ?? "unknown";
}

/**
 * レート制限の単位。ログイン済みならセッション、そうでなければ IP。
 * セッション ID をそのまま鍵にすると値がログに乗りうるので短いハッシュにする。
 */
function clientKey(c: Context): string {
  const sid = getCookie(c, SESSION_COOKIE);
  if (sessionAlive(sid)) return "s:" + createHash("sha256").update(sid!).digest("hex").slice(0, 16);
  return "ip:" + clientIp(c);
}

// -------------------------------------------------------------- レート制限

type Bucket = { tokens: number; updatedAt: number };
const buckets = new Map<string, Bucket>();

function takeToken(key: string, perMinute: number): boolean {
  const now = Date.now();

  // 使われなくなった鍵が溜まり続けないよう、たまに掃除する。
  if (buckets.size > 5000) {
    for (const [k, bucket] of buckets) {
      if (now - bucket.updatedAt > 600_000) buckets.delete(k);
    }
  }

  const bucket = buckets.get(key) ?? { tokens: perMinute, updatedAt: now };
  const refilled = ((now - bucket.updatedAt) / 60_000) * perMinute;
  bucket.tokens = Math.min(perMinute, bucket.tokens + refilled);
  bucket.updatedAt = now;

  if (bucket.tokens < 1) {
    buckets.set(key, bucket);
    return false;
  }
  bucket.tokens -= 1;
  buckets.set(key, bucket);
  return true;
}

/** クライアント単位の同時ストリーム数。 */
const activeStreams = new Map<string, number>();

/** 上限に達していれば null。使い終わったら返り値を呼ぶこと。 */
export function acquireStreamSlot(c: Context): (() => void) | null {
  const key = clientKey(c);
  const current = activeStreams.get(key) ?? 0;
  if (current >= security.maxConcurrentStreams) return null;
  activeStreams.set(key, current + 1);

  let released = false;
  return () => {
    if (released) return;
    released = true;
    const left = (activeStreams.get(key) ?? 1) - 1;
    if (left <= 0) activeStreams.delete(key);
    else activeStreams.set(key, left);
  };
}

// -------------------------------------------------------------- ログ

/** 発話内容は決して書かない。誰がいつ何をしてどうなったか、だけ。 */
export function logAccess(c: Context, event: string, extra: Record<string, unknown> = {}): void {
  console.log(
    JSON.stringify({
      ts: new Date().toISOString(),
      event,
      ip: clientIp(c),
      method: c.req.method,
      path: new URL(c.req.url).pathname,
      ...extra,
    }),
  );
}

// -------------------------------------------------------------- ミドルウェア

/**
 * 送るヘッダ:
 * - CSP: 自分自身のファイル以外は読み込ませない。インライン script / style は禁止
 *   （`public/` にインラインの JS は無い。favicon の data: URI のために img-src だけ緩める）
 * - Permissions-Policy: マイク以外のセンサー API を切る
 */
export function securityHeaders(): MiddlewareHandler {
  const csp = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "connect-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  return async (c, next) => {
    await next();
    c.header("Content-Security-Policy", csp);
    c.header("X-Content-Type-Options", "nosniff");
    c.header("X-Frame-Options", "DENY");
    c.header("Referrer-Policy", "no-referrer");
    c.header("Permissions-Policy", "microphone=(self), camera=(), geolocation=(), interest-cohort=()");
    c.header("Cross-Origin-Opener-Policy", "same-origin");
    c.header("Cross-Origin-Resource-Policy", "same-origin");
    if (isHttps(c)) {
      c.header("Strict-Transport-Security", "max-age=15552000; includeSubDomains");
    }
  };
}

/**
 * CSRF 対策。ブラウザは POST に必ず Origin を付けるので、
 * 付いていて食い違うものだけ弾く（curl など Origin を送らない相手は素通し）。
 */
export function originGuard(): MiddlewareHandler {
  return async (c, next) => {
    const origin = c.req.header("origin");
    if (origin) {
      const allowed =
        security.allowedOrigins.length > 0
          ? security.allowedOrigins.includes(origin)
          : origin === `${isHttps(c) ? "https" : "http"}://${c.req.header("host") ?? ""}`;
      if (!allowed) {
        logAccess(c, "origin_rejected", { origin });
        return c.json({ error: "許可されていない接続元です" }, 403);
      }
    }
    return next();
  };
}

export function rateLimit(perMinute: number, scope: "chat" | "login"): MiddlewareHandler {
  return async (c, next) => {
    const key = `${scope}:${scope === "login" ? "ip:" + clientIp(c) : clientKey(c)}`;
    if (!takeToken(key, perMinute)) {
      logAccess(c, "rate_limited", { scope });
      c.header("Retry-After", "60");
      return c.json({ error: "リクエストが多すぎます。少し待ってからお試しください。" }, 429);
    }
    return next();
  };
}

export function requireAuth(): MiddlewareHandler {
  return async (c, next) => {
    if (!authRequired) return next();
    if (sessionAlive(getCookie(c, SESSION_COOKIE))) return next();
    logAccess(c, "unauthorized");
    return c.json({ error: "ログインが必要です", authRequired: true }, 401);
  };
}

// -------------------------------------------------------------- ログイン

export async function handleLogin(c: Context): Promise<Response> {
  if (!authRequired) return c.json({ error: "この環境ではログインは不要です" }, 400);

  let token = "";
  try {
    const body = (await c.req.json()) as { token?: unknown };
    if (typeof body.token === "string") token = body.token;
  } catch {
    return c.json({ error: "リクエストを読み取れませんでした" }, 400);
  }

  if (!token || !sameSecret(token, security.accessToken)) {
    logAccess(c, "login_failed");
    return c.json({ error: "アクセストークンが違います" }, 401);
  }

  const { id, maxAgeSeconds } = createSession();
  setCookie(c, SESSION_COOKIE, id, {
    httpOnly: true,
    sameSite: "Strict",
    path: "/",
    maxAge: maxAgeSeconds,
    secure: isHttps(c),
  });
  logAccess(c, "login_ok");
  return c.json({ ok: true });
}

export function handleLogout(c: Context): Response {
  const sid = getCookie(c, SESSION_COOKIE);
  if (sid) sessions.delete(sid);
  deleteCookie(c, SESSION_COOKIE, { path: "/" });
  return c.json({ ok: true });
}

export function sessionState(c: Context): { authRequired: boolean; authenticated: boolean } {
  return {
    authRequired,
    authenticated: !authRequired || sessionAlive(getCookie(c, SESSION_COOKIE)),
  };
}

// -------------------------------------------------------------- 起動時チェック

const LOOPBACK = new Set(["127.0.0.1", "::1", "localhost"]);

/**
 * 認証なしのまま外向きに bind するのを止める。
 * ここを抜けられると、URL を知っている誰でも API キーを使えてしまう。
 */
export function assertSafeBinding(): void {
  if (LOOPBACK.has(config.host) || authRequired || security.allowInsecureBind) return;
  console.error(
    [
      `[fatal] ${config.host} で待ち受けようとしていますが、アクセストークンが設定されていません。`,
      "  VOICE_ACCESS_TOKEN を設定してログインを有効にするか、HOST=127.0.0.1 でローカルに閉じてください。",
      "  それでも認証なしで公開する場合のみ VOICE_ALLOW_INSECURE=1 を指定してください。",
    ].join("\n"),
  );
  process.exit(1);
}
