/** サーバー設定。すべて環境変数で上書きできる。 */

function envInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

const EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const;
export type Effort = (typeof EFFORT_LEVELS)[number];

function envEffort(name: string, fallback: Effort): Effort {
  const raw = process.env[name];
  return EFFORT_LEVELS.includes(raw as Effort) ? (raw as Effort) : fallback;
}

export const config = {
  host: process.env["HOST"] ?? "127.0.0.1",
  port: envInt("PORT", 3000),

  /** 会話用モデル。 */
  model: process.env["VOICE_MODEL"] ?? "claude-opus-5",

  /**
   * 音声会話は応答の速さが体験を左右するので、既定は low。
   * 込み入った相談をさせたいときは VOICE_EFFORT=high などに上げる。
   */
  effort: envEffort("VOICE_EFFORT", "low"),

  /** 読み上げ前提なので長い返答は不要。 */
  maxTokens: envInt("VOICE_MAX_TOKENS", 4096),

  /** リクエスト 1 件あたりに受け付ける会話履歴の上限。 */
  maxHistoryMessages: envInt("MAX_HISTORY_MESSAGES", 40),

  /** 1 発話あたりの文字数上限。 */
  maxMessageChars: envInt("MAX_MESSAGE_CHARS", 4000),
} as const;

/** セキュリティ関連の設定。既定はローカル 1 人用（認証なし・緩めの制限）。 */
export const security = {
  /**
   * 設定するとアクセストークンによるログインが必須になる。
   * 空のままなら認証なし（ループバックに閉じている前提）。
   */
  accessToken: process.env["VOICE_ACCESS_TOKEN"] ?? "",

  /** ブラウザからの POST で許可する Origin。空なら Host と一致するものだけ許可。 */
  allowedOrigins: (process.env["VOICE_ALLOWED_ORIGINS"] ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),

  /** リバースプロキシ配下で X-Forwarded-For / -Proto を信用する。直接公開時は 0 のまま。 */
  trustProxy: process.env["VOICE_TRUST_PROXY"] === "1",

  /** 1 分あたりの会話リクエスト数（クライアント単位）。 */
  chatPerMinute: envInt("VOICE_RATE_LIMIT_PER_MIN", 20),

  /** ログイン試行の 1 分あたり上限（IP 単位）。 */
  loginPerMinute: envInt("VOICE_LOGIN_RATE_LIMIT_PER_MIN", 5),

  /** 同時に走らせてよいストリーム数（クライアント単位）。 */
  maxConcurrentStreams: envInt("VOICE_MAX_CONCURRENT_STREAMS", 2),

  /** ログインセッションの有効期間（時間）。 */
  sessionTtlHours: envInt("VOICE_SESSION_TTL_HOURS", 12),

  /** ループバック以外に bind しつつ認証なしで起動することを許す（非推奨）。 */
  allowInsecureBind: process.env["VOICE_ALLOW_INSECURE"] === "1",
} as const;

export const authRequired = security.accessToken !== "";
