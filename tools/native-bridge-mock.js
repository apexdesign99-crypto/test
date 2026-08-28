// ネイティブ WebView の Capacitor ブリッジを模擬するスタブ。
// 実機ビルドなしで、音声認識・保存・通知・共有まわりの native 分岐を
// ブラウザで確認するために使う（`npm run preview:native`）。
(function () {
  window.androidBridge = { postMessage() {} }; // これで platform は 'android' になる
  const log = (window.__calls = []);
  const store = new Map();
  const listeners = new Map(); // `${plugin}:${event}` -> callback

  const plugins = {
    SpeechRecognition: {
      available: async () => ({ available: true }),
      requestPermissions: async () => ({ speechRecognition: 'granted' }),
      checkPermissions: async () => ({ speechRecognition: 'granted' }),
      start: async () => {
        // 実機と同じく partialResults 経由で結果を返し、start() 自体は空で解決する
        setTimeout(() => emit('SpeechRecognition', 'listeningState', { status: 'started' }), 0);
        setTimeout(() => emit('SpeechRecognition', 'partialResults', { matches: ['朝の血圧'] }), 10);
        setTimeout(
          () => emit('SpeechRecognition', 'partialResults', { matches: ['朝の血圧 上が142 下が91 脈は77'] }),
          20,
        );
        setTimeout(() => emit('SpeechRecognition', 'listeningState', { status: 'stopped' }), 30);
        return {};
      },
      stop: async () => {},
    },
    Preferences: {
      get: async ({ key }) => ({ value: store.has(key) ? store.get(key) : null }),
      set: async ({ key, value }) => void store.set(key, value),
      remove: async ({ key }) => void store.delete(key),
    },
    LocalNotifications: {
      requestPermissions: async () => ({ display: 'granted' }),
      checkPermissions: async () => ({ display: 'granted' }),
      schedule: async () => ({ notifications: [] }),
      cancel: async () => {},
    },
    Filesystem: { writeFile: async ({ path }) => ({ uri: `file:///cache/${path}` }) },
    Share: { share: async () => ({ activityType: 'test' }) },
  };

  function emit(plugin, event, data) {
    const cb = listeners.get(`${plugin}:${event}`);
    if (cb) cb(data);
  }

  const headers = Object.entries(plugins).map(([name, impl]) => ({
    name,
    methods: [
      ...Object.keys(impl).map((m) => ({ name: m, rtype: 'promise' })),
      { name: 'addListener', rtype: 'callback' },
      { name: 'removeListener', rtype: 'callback' },
    ],
  }));

  window.Capacitor = {
    PluginHeaders: headers,
    nativePromise: async (plugin, method, options) => {
      log.push(`${plugin}.${method}`);
      const fn = plugins[plugin]?.[method];
      if (!fn) throw new Error(`${plugin}.${method} is not implemented in harness`);
      return fn(options ?? {});
    },
    nativeCallback: (plugin, method, options, callback) => {
      log.push(`${plugin}.${method}(${options?.eventName ?? ''})`);
      if (method === 'addListener') {
        listeners.set(`${plugin}:${options.eventName}`, callback);
        return `${plugin}:${options.eventName}`;
      }
      if (method === 'removeListener') listeners.delete(`${plugin}:${options.eventName}`);
      return null;
    },
  };
})();
