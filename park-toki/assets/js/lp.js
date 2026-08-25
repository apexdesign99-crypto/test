/**
 * 大丸パーク土岐 ランディングページ
 * 専用スクリプト（依存ライブラリなし）
 *
 *  - オープンまでのカウントダウン
 *  - スクロールに応じたフェードイン
 *  - 問い合わせフォームのクライアントサイド検証
 *  - 著作権表示の年号更新
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------
   * カウントダウン
   *
   * 目標日時は HTML 側の data-countdown 属性（ISO 8601、タイムゾーン付き）
   * で指定する。オープン日を変更する場合は HTML だけを直せばよい。
   * ---------------------------------------------------------------- */
  function initCountdown() {
    var root = document.querySelector('[data-countdown]');
    if (!root) return;

    var target = new Date(root.getAttribute('data-countdown'));
    if (isNaN(target.getTime())) return;

    var list = root.querySelector('[data-countdown-list]');
    var opened = root.querySelector('[data-countdown-opened]');
    var units = {
      days: root.querySelector('[data-unit="days"]'),
      hours: root.querySelector('[data-unit="hours"]'),
      minutes: root.querySelector('[data-unit="minutes"]'),
      seconds: root.querySelector('[data-unit="seconds"]')
    };

    function pad(value, length) {
      var text = String(value);
      while (text.length < length) text = '0' + text;
      return text;
    }

    function showOpened() {
      if (list) list.hidden = true;
      if (opened) opened.hidden = false;
    }

    function tick() {
      var diff = target.getTime() - Date.now();

      if (diff <= 0) {
        showOpened();
        return false;
      }

      var totalSeconds = Math.floor(diff / 1000);
      var days = Math.floor(totalSeconds / 86400);
      var hours = Math.floor((totalSeconds % 86400) / 3600);
      var minutes = Math.floor((totalSeconds % 3600) / 60);
      var seconds = totalSeconds % 60;

      if (units.days) units.days.textContent = pad(days, 3);
      if (units.hours) units.hours.textContent = pad(hours, 2);
      if (units.minutes) units.minutes.textContent = pad(minutes, 2);
      if (units.seconds) units.seconds.textContent = pad(seconds, 2);
      return true;
    }

    if (!tick()) return;
    var timer = setInterval(function () {
      if (!tick()) clearInterval(timer);
    }, 1000);
  }

  /* ------------------------------------------------------------------
   * スクロール連動のフェードイン
   * ---------------------------------------------------------------- */
  function initReveal() {
    var targets = document.querySelectorAll('.reveal');
    if (!targets.length) return;

    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || typeof IntersectionObserver === 'undefined') {
      Array.prototype.forEach.call(targets, function (el) {
        el.classList.add('is-visible');
      });
      return;
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });

    Array.prototype.forEach.call(targets, function (el) {
      observer.observe(el);
    });
  }

  /* ------------------------------------------------------------------
   * 問い合わせフォームの検証
   *
   * 送信先（action）が未設定の間は送信せず、その旨を表示する。
   * ---------------------------------------------------------------- */
  function initForm() {
    var form = document.querySelector('[data-lp-form]');
    if (!form) return;

    var status = form.querySelector('[data-form-status]');
    var emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    var fields = form.querySelectorAll('input, select, textarea');

    function errorBox(field) {
      var wrapper = field.closest('.field');
      return wrapper ? wrapper.querySelector('.field__error') : null;
    }

    function setError(field, message) {
      var box = errorBox(field);
      field.setAttribute('aria-invalid', 'true');
      if (box) box.textContent = message;
    }

    function clearError(field) {
      var box = errorBox(field);
      field.removeAttribute('aria-invalid');
      if (box) box.textContent = '';
    }

    function validate(field) {
      if (field.type === 'checkbox') {
        if (field.hasAttribute('required') && !field.checked) {
          setError(field, '同意が必要です。');
          return false;
        }
        clearError(field);
        return true;
      }

      var value = (field.value || '').trim();
      if (field.hasAttribute('required') && !value) {
        setError(field, '入力してください。');
        return false;
      }
      if (field.type === 'email' && value && !emailPattern.test(value)) {
        setError(field, 'メールアドレスの形式が正しくありません。');
        return false;
      }
      clearError(field);
      return true;
    }

    Array.prototype.forEach.call(fields, function (field) {
      field.addEventListener('blur', function () {
        validate(field);
      });
    });

    form.addEventListener('submit', function (event) {
      var firstInvalid = null;

      Array.prototype.forEach.call(fields, function (field) {
        if (!validate(field) && !firstInvalid) firstInvalid = field;
      });

      if (firstInvalid) {
        event.preventDefault();
        if (status) {
          status.setAttribute('data-state', 'error');
          status.textContent = '未入力または形式に誤りのある項目があります。内容をご確認ください。';
        }
        firstInvalid.focus();
        return;
      }

      if (!form.getAttribute('action')) {
        event.preventDefault();
        if (status) {
          status.setAttribute('data-state', 'info');
          status.textContent =
            '入力内容に問題はありません。現在このフォームは送信先が未設定のため送信されません（form 要素の action に送信先を設定してください）。';
        }
      }
    });
  }

  /* ------------------------------------------------------------------
   * 著作権表示の年号
   * ---------------------------------------------------------------- */
  function initYear() {
    var el = document.querySelector('[data-current-year]');
    if (el) el.textContent = String(new Date().getFullYear());
  }

  function init() {
    initCountdown();
    initReveal();
    initForm();
    initYear();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
