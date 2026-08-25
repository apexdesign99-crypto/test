/**
 * 大丸開発株式会社 コーポレートサイト
 * 共通スクリプト（依存ライブラリなし）
 *
 *  - モバイルナビゲーションの開閉
 *  - スクロールに応じたセクションのフェードイン
 *  - お問い合わせフォームのクライアントサイド検証
 *  - フッターの著作権表示の年号更新
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------
   * モバイルナビゲーション
   * ---------------------------------------------------------------- */
  function initNav() {
    var toggle = document.querySelector('[data-nav-toggle]');
    var nav = document.querySelector('[data-nav]');
    if (!toggle || !nav) return;

    function setOpen(open) {
      nav.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'メニューを閉じる' : 'メニューを開く');
    }

    toggle.addEventListener('click', function () {
      setOpen(toggle.getAttribute('aria-expanded') !== 'true');
    });

    // メニュー内のリンクを押したら閉じる
    nav.addEventListener('click', function (event) {
      if (event.target.closest('a')) setOpen(false);
    });

    // Esc で閉じる
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && nav.classList.contains('is-open')) {
        setOpen(false);
        toggle.focus();
      }
    });

    // デスクトップ幅に戻ったら状態をリセット
    var mq = window.matchMedia('(min-width: 1024px)');
    var onChange = function (event) {
      if (event.matches) setOpen(false);
    };
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange);
    } else if (typeof mq.addListener === 'function') {
      mq.addListener(onChange);
    }
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
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.1 });

    Array.prototype.forEach.call(targets, function (el) {
      observer.observe(el);
    });
  }

  /* ------------------------------------------------------------------
   * お問い合わせフォームの検証
   *
   * 送信先が未設定（action が空）の場合は送信を止めて案内を表示する。
   * 実運用ではフォームの action に送信先を設定してください。
   * ---------------------------------------------------------------- */
  function initForm() {
    var form = document.querySelector('[data-contact-form]');
    if (!form) return;

    var status = form.querySelector('[data-form-status]');
    var emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    function fieldError(field) {
      return field.closest('.field').querySelector('.field__error');
    }

    function setError(field, message) {
      var box = fieldError(field);
      field.setAttribute('aria-invalid', 'true');
      if (box) box.textContent = message;
    }

    function clearError(field) {
      var box = fieldError(field);
      field.removeAttribute('aria-invalid');
      if (box) box.textContent = '';
    }

    function validateField(field) {
      var value = (field.value || '').trim();

      if (field.hasAttribute('required') && !value) {
        setError(field, '入力してください。');
        return false;
      }
      if (field.type === 'email' && value && !emailPattern.test(value)) {
        setError(field, 'メールアドレスの形式が正しくありません。');
        return false;
      }
      if (field.type === 'checkbox' && field.hasAttribute('required') && !field.checked) {
        setError(field, '同意が必要です。');
        return false;
      }
      clearError(field);
      return true;
    }

    var fields = form.querySelectorAll('input, select, textarea');
    Array.prototype.forEach.call(fields, function (field) {
      field.addEventListener('blur', function () {
        validateField(field);
      });
    });

    form.addEventListener('submit', function (event) {
      var firstInvalid = null;

      Array.prototype.forEach.call(fields, function (field) {
        if (field.type === 'checkbox') {
          if (field.hasAttribute('required') && !field.checked) {
            setError(field, '同意が必要です。');
            if (!firstInvalid) firstInvalid = field;
          } else {
            clearError(field);
          }
          return;
        }
        if (!validateField(field) && !firstInvalid) firstInvalid = field;
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

      // 送信先が未設定のうちは送信せず案内のみ表示する
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
    initNav();
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
