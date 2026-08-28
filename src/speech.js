// 音声認識の抽象化
// ネイティブ（iOS / Android）は Capacitor プラグイン、ブラウザは Web Speech API を使う。

import { Capacitor } from '@capacitor/core';
import { SpeechRecognition } from '@capacitor-community/speech-recognition';

const WebSpeech =
  typeof window !== 'undefined'
    ? window.SpeechRecognition || window.webkitSpeechRecognition || null
    : null;

export const isNative = () => Capacitor.isNativePlatform();

/**
 * 音声認識バックエンドを作る。
 * @param {{lang:string, onPartial:(text:string)=>void, onResult:(matches:string[])=>void,
 *          onStateChange:(listening:boolean)=>void, onError:(message:string)=>void}} handlers
 */
export function createRecognizer(handlers) {
  return isNative() ? nativeRecognizer(handlers) : webRecognizer(handlers);
}

const ERROR_MESSAGES = {
  'not-allowed': 'マイクの使用が許可されていません。設定から許可してください。',
  'service-not-allowed': 'マイクの使用が許可されていません。設定から許可してください。',
  'no-speech': '声を検出できませんでした。もう一度お試しください。',
  network: 'ネットワークエラーで音声認識できませんでした。',
  aborted: '',
};

/* ---------- ネイティブ（@capacitor-community/speech-recognition） ---------- */

function nativeRecognizer({ lang, onPartial, onResult, onStateChange, onError }) {
  let listening = false;
  let listeners = [];
  // partialResults を使うと確定結果はイベント側にしか来ないため、
  // 最後に受け取った候補を保持しておき、停止時にそれを確定結果として扱う。
  let latestMatches = [];
  let delivered = true;

  const setListening = (on) => {
    listening = on;
    onStateChange(on);
  };

  const finish = () => {
    if (delivered) return;
    delivered = true;
    if (latestMatches.length) onResult(latestMatches);
    else onError('声を検出できませんでした。もう一度お試しください。');
  };

  return {
    kind: 'native',

    async prepare() {
      const { available } = await SpeechRecognition.available();
      if (!available) return { ok: false, reason: 'この端末では音声認識を利用できません。' };
      listeners = [
        await SpeechRecognition.addListener('partialResults', (data) => {
          const matches = (data?.matches ?? []).filter(Boolean);
          if (!matches.length) return;
          latestMatches = matches;
          onPartial(matches[0]);
        }),
        await SpeechRecognition.addListener('listeningState', (data) => {
          const started = data?.status === 'started';
          setListening(started);
          if (!started) finish();
        }),
      ];
      return { ok: true };
    },

    async requestPermission() {
      const status = await SpeechRecognition.requestPermissions();
      return status.speechRecognition === 'granted';
    },

    async start() {
      if (listening) return;
      if (!(await this.requestPermission())) {
        onError('マイクと音声認識の許可が必要です。設定から許可してください。');
        return;
      }
      latestMatches = [];
      delivered = false;
      setListening(true);
      try {
        const result = await SpeechRecognition.start({
          language: lang,
          maxResults: 5,
          partialResults: true,
          popup: false,
        });
        // 端末によっては start() の戻り値で確定結果が返る
        const matches = (result?.matches ?? []).filter(Boolean);
        if (matches.length) {
          latestMatches = matches;
          delivered = true;
          setListening(false);
          onResult(matches);
        }
      } catch (error) {
        delivered = true;
        setListening(false);
        onError(error?.message || '音声認識に失敗しました。');
      }
    },

    async stop() {
      try {
        await SpeechRecognition.stop();
      } catch {
        /* 停止済みは無視 */
      }
      setListening(false);
      finish();
    },

    async destroy() {
      await Promise.all(listeners.map((l) => l.remove()));
      listeners = [];
    },
  };
}

/* ---------- ブラウザ（Web Speech API） ---------- */

function webRecognizer({ lang, onPartial, onResult, onStateChange, onError }) {
  let recognition = null;

  return {
    kind: 'web',

    async prepare() {
      if (!WebSpeech) {
        return {
          ok: false,
          reason:
            'このブラウザは音声認識に対応していません（Chrome / Edge / Safari 推奨）。手入力をお使いください。',
        };
      }
      recognition = new WebSpeech();
      recognition.lang = lang;
      recognition.interimResults = true;
      recognition.continuous = false;
      recognition.maxAlternatives = 5;
      recognition.onstart = () => onStateChange(true);
      recognition.onend = () => onStateChange(false);
      recognition.onerror = (event) => {
        onStateChange(false);
        const message = ERROR_MESSAGES[event.error] ?? `音声認識エラー: ${event.error}`;
        if (message) onError(message);
      };
      recognition.onresult = (event) => {
        const last = event.results[event.results.length - 1];
        if (!last.isFinal) {
          onPartial(last[0].transcript);
          return;
        }
        onResult([...last].map((alt) => alt.transcript));
      };
      return { ok: true };
    },

    async requestPermission() {
      return true; // ブラウザは start 時に許可を求める
    },

    async start() {
      try {
        recognition.start();
      } catch {
        /* 二重 start は無視 */
      }
    },

    async stop() {
      recognition?.stop();
    },

    async destroy() {
      recognition?.abort?.();
      recognition = null;
    },
  };
}
