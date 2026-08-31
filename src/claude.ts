import Anthropic from "@anthropic-ai/sdk";
import { config } from "./config.js";

export type ChatLang = "ja" | "en";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export type ReplyChunk =
  | { type: "delta"; text: string }
  | { type: "done"; model: string; refused: boolean };

/**
 * 返答はそのまま音声合成に流し込まれるため、記号や装飾を避けて
 * 「声に出して自然な文」を書かせることがこのプロンプトの目的。
 */
const SYSTEM_PROMPTS: Record<ChatLang, string> = {
  ja: [
    "あなたは音声で会話する相手です。あなたの返答はそのまま音声合成で読み上げられ、ユーザーの発話は音声認識を通って届きます。",
    "",
    "話し方のルール:",
    "- 話し言葉で答える。ふつうは 2〜3 文、120 文字程度まで。",
    "- Markdown 記法（**、#、-、箇条書き、コードブロック、表）は使わない。読み上げると雑音になる。",
    "- 記号は声に出せる形に開く。「3km」は「3キロメートル」、「&」は「アンド」。",
    "- 長い内容は要点だけ話し、続きが要るか一言たずねる。",
    "- 箇条書きにしたいときは「1つめは…、2つめは…」のように文で数え上げる。",
    "- URL やコードは読み上げに向かないので、内容を言葉で要約する。",
    "- 音声認識の誤りが疑われるときは、聞き取れた内容を推測して確認する。",
    "- ユーザーが話した言語に合わせて返答する。",
  ].join("\n"),
  en: [
    "You are a voice conversation partner. Your replies are read aloud by speech synthesis, and the user's turns arrive through speech recognition.",
    "",
    "How to speak:",
    "- Answer in spoken language. Usually 2-3 sentences, about 60 words at most.",
    "- Never use Markdown (**, #, -, bullet lists, code blocks, tables). It becomes noise when read aloud.",
    "- Spell out symbols so they can be spoken: '3km' becomes '3 kilometers', '&' becomes 'and'.",
    "- For long topics, give the key point and ask whether to continue.",
    "- Count items in prose ('first..., second...') instead of bullet points.",
    "- URLs and code do not read well aloud, so summarize them in words.",
    "- If a turn looks like a speech recognition error, guess what was meant and confirm.",
    "- Reply in the language the user spoke.",
  ].join("\n"),
};

const client = new Anthropic();

const FALLBACK_BETA = "server-side-fallback-2026-07-01";

/** サーバー側フォールバックが使えない環境（未対応アカウント等）を検知したら以後は付けない。 */
let fallbackSupported = true;

async function* runStream(
  params: { messages: ChatTurn[]; lang: ChatLang; signal: AbortSignal },
  withFallback: boolean,
): AsyncGenerator<ReplyChunk> {
  const stream = client.beta.messages.stream(
    {
      model: config.model,
      max_tokens: config.maxTokens,
      system: SYSTEM_PROMPTS[params.lang],
      messages: params.messages,
      output_config: { effort: config.effort },
      // ポリシー上の拒否が起きたときは、同じ呼び出しの中で
      // サーバー側が既定のフォールバックモデルに引き継ぐ。
      ...(withFallback ? { betas: [FALLBACK_BETA], fallbacks: "default" as const } : {}),
    },
    { signal: params.signal },
  );

  for await (const event of stream) {
    if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
      yield { type: "delta", text: event.delta.text };
    }
  }

  const final = await stream.finalMessage();
  yield {
    type: "done",
    model: final.model,
    refused: final.stop_reason === "refusal",
  };
}

/**
 * Claude の返答をテキスト差分として流す。
 * 呼び出し側が中断したときは signal で API 呼び出しごと打ち切る。
 */
export async function* streamReply(params: {
  messages: ChatTurn[];
  lang: ChatLang;
  signal: AbortSignal;
}): AsyncGenerator<ReplyChunk> {
  let emitted = false;
  try {
    for await (const chunk of runStream(params, fallbackSupported)) {
      emitted = true;
      yield chunk;
    }
  } catch (error) {
    // まだ何も返していないうちのリクエスト不正なら、
    // フォールバック指定が原因の可能性が高いので一度だけ外して再試行する。
    if (emitted || !fallbackSupported || !(error instanceof Anthropic.BadRequestError)) throw error;
    fallbackSupported = false;
    console.warn("[claude] サーバー側フォールバックを無効化して再試行します:", error.message);
    yield* runStream(params, false);
  }
}
