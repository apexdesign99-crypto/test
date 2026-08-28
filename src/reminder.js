// 毎日の測定リマインダー（ネイティブはローカル通知、ブラウザは Web 通知）

import { Capacitor } from '@capacitor/core';
import { LocalNotifications } from '@capacitor/local-notifications';

const ID = 1;

export const reminderSupported = () => Capacitor.isNativePlatform();

/**
 * 毎日同じ時刻に通知を出す。enabled が false なら解除する。
 * @returns {Promise<{ok:boolean, reason?:string}>}
 */
export async function applyReminder({ reminderEnabled, reminderHour, reminderMinute }) {
  if (!reminderSupported()) {
    return { ok: false, reason: 'この環境では通知を設定できません（アプリ版で利用できます）。' };
  }
  await LocalNotifications.cancel({ notifications: [{ id: ID }] }).catch(() => {});
  if (!reminderEnabled) return { ok: true };

  const permission = await LocalNotifications.requestPermissions();
  if (permission.display !== 'granted') {
    return { ok: false, reason: '通知が許可されていません。設定から許可してください。' };
  }
  await LocalNotifications.schedule({
    notifications: [
      {
        id: ID,
        title: '血圧を測りましょう',
        body: 'アプリを開いて、話しかけるだけで記録できます。',
        schedule: {
          on: { hour: reminderHour, minute: reminderMinute },
          repeats: true,
          allowWhileIdle: true,
        },
      },
    ],
  });
  return { ok: true };
}
