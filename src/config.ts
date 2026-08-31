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
