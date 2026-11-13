/* Player PWA: city catalogue, game page, and play screen over the builder JSON API.
   The server decides everything (current stage, answers, score); this file only
   shows what the API returns and sends what the player does. */
(function () {
  'use strict';

  const root = document.getElementById('app');
  const API = root.dataset.api;
  const SIGNED_IN = root.dataset.signedIn === '1';
  const LOGIN_URL = root.dataset.loginUrl;
  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  const LABELS = {
    easy: 'آسان', medium: 'متوسط', hard: 'سخت',
  };
  const ERRORS = {
    'Quiz answer is incorrect.': 'پاسخ درست نیست. دوباره امتحان کنید.',
    'Quiz answer is required.': 'پاسخ را وارد کنید.',
    'Text input is incorrect.': 'پاسخ درست نیست. دوباره امتحان کنید.',
    'Text input is required.': 'متن را وارد کنید.',
    'Text input is too long.': 'متن طولانی‌تر از حد مجاز است.',
    'GPS input is required.': 'برای این مرحله باید موقعیت مکانی بفرستید.',
    'GPS validation failed.': 'هنوز به محل این مرحله نرسیده‌اید.',
    'QR input is required.': 'کد QR این مرحله را اسکن کنید.',
    'QR validation failed.': 'این کد QR برای این مرحله نیست.',
    'Timer has not elapsed.': 'هنوز زمان این مرحله تمام نشده.',
    'A valid media submission is required.': 'اول فایل خواسته‌شده را بفرستید.',
    'Stage is not the current graph node.': 'این مرحله دیگر جاری نیست؛ صفحه به‌روز شد.',
    'Stage is not the current server stage.': 'این مرحله دیگر جاری نیست؛ صفحه به‌روز شد.',
    'Session is already completed.': 'این بازی تمام شده است.',
    'Too many wrong answers. Wait a minute.': 'پاسخ‌های غلط زیاد شد؛ یک دقیقه صبر کنید.',
    'File is too large.': 'حجم فایل زیاد است.',
    'File content is invalid.': 'نوع فایل پذیرفته نیست.',
  };

  // ---------- helpers ----------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'class') node.className = value;
      else node.setAttribute(key, value === true ? '' : value);
    });
    children.flat().forEach((child) => {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === 'string' || typeof child === 'number' ? document.createTextNode(String(child)) : child);
    });
    return node;
  }

  function render(...nodes) {
    root.replaceChildren(...nodes);
    window.scrollTo(0, 0);
  }

  function toast(message, kind) {
    const box = document.getElementById('toast');
    box.textContent = message;
    box.className = 'show ' + (kind || '');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { box.className = ''; }, 3500);
  }

  function faNumber(value) {
    return Number(value).toLocaleString('fa-IR');
  }

  function eventId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'e-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  }

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, options) {
    const opts = Object.assign({ credentials: 'same-origin', headers: {} }, options || {});
    if (opts.method && opts.method !== 'GET') opts.headers['X-CSRFToken'] = CSRF;
    if (opts.json !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    let response;
    try {
      response = await fetch(API + path, opts);
    } catch (error) {
      throw new ApiError('اتصال برقرار نشد. اینترنت را بررسی کنید.', 0);
    }
    const type = response.headers.get('Content-Type') || '';
    if (!type.includes('application/json')) {
      // Signed out or terms to accept: Django answered with a redirect to an HTML page.
      if (response.redirected) {
        window.location.href = response.url.includes('/login') ? LOGIN_URL : response.url;
        throw new ApiError('', response.status);
      }
      throw new ApiError(response.status === 404 ? 'پیدا نشد.' : 'خطای سرور (' + response.status + ').', response.status);
    }
    const body = await response.json();
    if (!response.ok) throw new ApiError(ERRORS[body.error] || body.error || 'خطا', response.status);
    return body;
  }

  function errorView(error, retry) {
    return el('div', { class: 'card' },
      el('p', { class: 'error' }, error.message || 'خطا'),
      retry ? el('button', { class: 'primary', onclick: retry }, 'دوباره') : null,
      el('p', {}, el('a', { href: '#/' }, 'بازگشت به شهرها')));
  }

  function needLogin() {
    return el('div', { class: 'card' },
      el('p', {}, 'برای شروع بازی وارد حساب خود شوید.'),
      el('a', { class: 'button primary', href: LOGIN_URL }, 'ورود / ثبت‌نام'));
  }

  // ---------- screens ----------
  async function showCities() {
    render(el('p', { class: 'muted' }, 'در حال بارگذاری شهرها…'));
    try {
      const data = await api('/cities');
      if (!data.cities.length) {
        render(el('h1', {}, 'شهرها'), el('p', { class: 'muted' }, 'هنوز بازی منتشرشده‌ای نیست.'));
        return;
      }
      render(el('h1', {}, 'یک شهر انتخاب کنید'),
        el('div', { class: 'list' }, data.cities.map((city) => el('a', { class: 'card tile', href: '#/city/' + encodeURIComponent(city.slug) },
          el('h2', {}, city.name),
          el('p', { class: 'muted' }, [city.region, city.country].filter(Boolean).join('، ')),
          city.description ? el('p', {}, city.description) : null,
          el('span', { class: 'pill' }, faNumber(city.games_count) + ' بازی')))));
    } catch (error) { render(errorView(error, showCities)); }
  }

  async function showCity(slug) {
    render(el('p', { class: 'muted' }, 'در حال بارگذاری بازی‌ها…'));
    try {
      const data = await api('/cities/' + encodeURIComponent(slug) + '/games');
      render(el('p', {}, el('a', { href: '#/' }, '→ همه شهرها')),
        el('h1', {}, 'بازی‌ها'),
        data.games.length ? el('div', { class: 'list' }, data.games.map(gameTile)) : el('p', { class: 'muted' }, 'بازی فعالی در این شهر نیست.'));
    } catch (error) { render(errorView(error, () => showCity(slug))); }
  }

  function gameMeta(game) {
    return el('div', { class: 'meta' },
      game.difficulty ? el('span', { class: 'pill' }, LABELS[game.difficulty] || game.difficulty) : null,
      game.estimated_minutes ? el('span', { class: 'pill' }, faNumber(game.estimated_minutes) + ' دقیقه') : null,
      game.is_team_game ? el('span', { class: 'pill' }, 'تیمی') : null);
  }

  function gameTile(game) {
    return el('a', { class: 'card tile', href: '#/game/' + encodeURIComponent(game.slug) },
      el('h2', {}, game.name), game.description ? el('p', {}, game.description) : null, gameMeta(game));
  }

  async function showGame(slug) {
    render(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
    try {
      const game = await api('/games/' + encodeURIComponent(slug));
      const start = async (event) => {
        event.target.disabled = true;
        try {
          const session = await api('/games/' + encodeURIComponent(slug) + '/sessions', { method: 'POST' });
          location.hash = '#/session/' + session.session_id;
        } catch (error) {
          if (error.message) toast(error.message, 'bad');
          event.target.disabled = false;
        }
      };
      render(game.city ? el('p', {}, el('a', { href: '#/city/' + encodeURIComponent(game.city) }, '→ بازی‌های این شهر')) : null,
        el('div', { class: 'card' },
          el('h1', {}, game.name),
          game.description ? el('p', {}, game.description) : null,
          gameMeta(game),
          SIGNED_IN ? el('button', { class: 'primary big', onclick: start }, 'شروع یا ادامهٔ بازی') : needLogin()));
    } catch (error) { render(errorView(error, () => showGame(slug))); }
  }

  async function showMine() {
    if (!SIGNED_IN) { render(needLogin()); return; }
    render(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
    try {
      const data = await api('/sessions');
      render(el('h1', {}, 'بازی‌های من'),
        data.sessions.length ? el('div', { class: 'list' }, data.sessions.map((session) => el('a', { class: 'card tile', href: '#/session/' + session.session_id },
          el('h2', {}, session.game_name),
          el('div', { class: 'meta' },
            el('span', { class: 'pill ' + (session.completed ? 'good' : '') }, session.completed ? 'تمام‌شده' : 'در جریان'),
            el('span', { class: 'pill' }, 'امتیاز ' + faNumber(session.score))))))
          : el('p', { class: 'muted' }, 'هنوز بازی‌ای شروع نکرده‌اید. ', el('a', { href: '#/' }, 'یک شهر انتخاب کنید.')));
    } catch (error) { render(errorView(error, showMine)); }
  }

  // ---------- play ----------
  let timerHandle = null;

  async function showSession(id) {
    clearInterval(timerHandle);
    if (!SIGNED_IN) { render(needLogin()); return; }
    render(el('p', { class: 'muted' }, 'در حال بارگذاری مرحله…'));
    let session;
    try {
      session = await api('/sessions/' + id);
    } catch (error) { render(errorView(error, () => showSession(id))); return; }

    const header = el('div', { class: 'hud' },
      el('strong', {}, session.game_name),
      el('span', { class: 'pill' }, 'امتیاز ' + faNumber(session.score)));

    if (session.completed || !session.current_stage) {
      render(header, el('div', { class: 'card done' },
        el('h1', {}, '🎉 بازی تمام شد'),
        el('p', { class: 'score' }, faNumber(session.score)),
        el('p', { class: 'muted' }, 'امتیاز نهایی'),
        el('a', { class: 'button primary', href: '#/me' }, 'بازی‌های من'),
        el('a', { class: 'button', href: '#/' }, 'بازی دیگر')));
      return;
    }

    const stage = session.current_stage;
    if (session.runtime !== 'graph') {
      render(header, el('div', { class: 'card' },
        el('p', {}, 'این بازی هنوز برای اجرا در وب آماده نیست.'),
        el('p', { class: 'muted' }, 'مرحلهٔ ' + faNumber(stage.position) + ' از ' + faNumber(stage.total))));
      return;
    }

    const tools = stage.tools || [];
    const has = (type) => tools.find((tool) => tool.type === type);
    const inputs = {};
    const form = el('form', { class: 'card stage', onsubmit: (event) => { event.preventDefault(); complete(); } });
    form.append(el('h1', {}, stage.title || 'مرحله'));
    if (stage.description) form.append(el('p', { class: 'description' }, stage.description));

    tools.forEach((tool) => {
      const s = tool.settings || {};
      if (s.instructions) form.append(el('p', { class: 'muted' }, s.instructions));
      switch (tool.type) {
        case 'quiz': {
          const options = s.options || s.choices;
          form.append(el('p', { class: 'question' }, s.question || s.prompt || 'پاسخ را وارد کنید'));
          if (Array.isArray(options) && options.length) {
            const group = el('div', { class: 'choices' });
            options.forEach((option, index) => {
              const label = typeof option === 'object' ? (option.label || option.text || option.value) : option;
              const value = typeof option === 'object' ? (option.value !== undefined ? option.value : label) : option;
              group.append(el('label', { class: 'choice' }, el('input', { type: 'radio', name: 'answer', value: String(value), required: index === 0 }), String(label)));
            });
            form.append(group);
            inputs.answer = () => (form.querySelector('input[name="answer"]:checked') || {}).value;
          } else {
            const input = el('input', { name: 'answer', required: true, autocomplete: 'off' });
            form.append(input);
            inputs.answer = () => input.value.trim();
          }
          break;
        }
        case 'text':
        case 'text_input': {
          const area = el('textarea', { name: 'text', rows: 3, maxlength: s.max_length || null, required: true });
          form.append(el('label', {}, s.label || s.prompt || s.question || 'پاسخ شما', area));
          inputs.text = () => area.value;
          break;
        }
        case 'gps': {
          const status = el('span', { class: 'muted' }, 'موقعیت هنگام ثبت فرستاده می‌شود.');
          form.append(el('div', { class: 'tool' }, el('strong', {}, '📍 رسیدن به محل'), status));
          inputs.gps = () => locate(status);
          break;
        }
        case 'qr': {
          const field = el('input', { name: 'qr', placeholder: 'کد QR', dir: 'ltr', autocomplete: 'off' });
          const scan = el('button', { type: 'button', onclick: () => scanQr(field) }, '📷 اسکن');
          form.append(el('div', { class: 'tool' }, el('strong', {}, 'کد QR محل را اسکن کنید'), el('div', { class: 'row' }, field, scan)));
          inputs.qr = () => field.value.trim();
          break;
        }
        case 'timer': {
          const seconds = Number(s.minimum_seconds || 0);
          const clock = el('strong', { class: 'clock' }, '');
          form.append(el('div', { class: 'tool' }, el('span', {}, '⏱ زمان باقی‌مانده: '), clock));
          startTimer(id, stage.stage_id, seconds, clock);
          break;
        }
        case 'hint': {
          const out = el('p', { class: 'hint' });
          const cost = Number(s.score_cost || 0);
          const button = el('button', { type: 'button', onclick: async () => {
            if (cost && !confirm('گرفتن راهنما ' + faNumber(cost) + ' امتیاز کم می‌کند. ادامه می‌دهید؟')) return;
            button.disabled = true;
            try {
              const result = await action(id, { stage_id: stage.stage_id, operation: 'hint' });
              out.textContent = result.content;
            } catch (error) { toast(error.message, 'bad'); button.disabled = false; }
          } }, cost ? 'راهنما (−' + faNumber(cost) + ')' : 'راهنما');
          form.append(el('div', { class: 'tool' }, button, out));
          break;
        }
        case 'image': case 'audio': case 'video': case 'camera': case 'audio_video': {
          const holder = el('div', { class: 'media' });
          form.append(holder);
          if (s.allow_upload) {
            const kind = s.media_type || 'image';
            const accept = { image: 'image/jpeg,image/png,image/webp', audio: 'audio/*', video: 'video/mp4,video/webm' }[kind] || '*/*';
            const file = el('input', { type: 'file', accept, capture: kind === 'image' ? 'environment' : null });
            const state = el('span', { class: 'muted' }, s.required ? 'ارسال فایل الزامی است.' : 'اختیاری');
            form.append(el('div', { class: 'tool' }, el('strong', {}, kind === 'image' ? '📸 عکس بفرستید' : 'فایل بفرستید'), file, state));
            inputs.upload = async () => {
              if (!file.files.length) return null;
              state.textContent = 'در حال ارسال…';
              const data = new FormData();
              data.append('file', file.files[0]);
              data.append('event_id', eventId());
              const result = await api('/sessions/' + id + '/media', { method: 'POST', body: data });
              state.textContent = 'ارسال شد ✓';
              return result.submission_id;
            };
          } else {
            loadMedia(id, stage.stage_id, holder);
          }
          break;
        }
        default:
          break;
      }
    });

    const submit = el('button', { type: 'submit', class: 'primary big' }, has('quiz') || has('text') || has('text_input') ? 'ثبت پاسخ' : 'انجام شد، مرحلهٔ بعد');
    form.append(submit);
    render(header, form);

    async function complete() {
      submit.disabled = true;
      try {
        const payload = { stage_id: stage.stage_id, operation: 'complete' };
        if (inputs.answer) payload.answer = inputs.answer();
        if (inputs.text) payload.text = inputs.text();
        if (inputs.qr) { const token = inputs.qr(); if (token) payload.qr_token = token; }
        if (inputs.gps) payload.gps = await inputs.gps();
        if (inputs.upload) { const submission = await inputs.upload(); if (submission) payload.submission_id = submission; }
        const result = await action(id, payload);
        toast(result.completed ? 'آفرین! بازی تمام شد.' : 'درست بود! +' + faNumber(result.score), 'good');
        showSession(id);
      } catch (error) {
        if (error.message) toast(error.message, 'bad');
        submit.disabled = false;
        if (error.status === 409 && /جاری نیست/.test(error.message)) showSession(id);
      }
    }
  }

  function action(id, payload) {
    return api('/sessions/' + id + '/actions', { method: 'POST', json: Object.assign({ event_id: eventId() }, payload) });
  }

  function locate(status) {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) { reject(new ApiError('مرورگر شما موقعیت مکانی را پشتیبانی نمی‌کند.')); return; }
      status.textContent = 'در حال گرفتن موقعیت…';
      navigator.geolocation.getCurrentPosition((position) => {
        status.textContent = 'موقعیت گرفته شد (دقت ' + faNumber(Math.round(position.coords.accuracy)) + ' متر)';
        resolve({ latitude: position.coords.latitude, longitude: position.coords.longitude, accuracy: position.coords.accuracy });
      }, () => {
        status.textContent = 'اجازهٔ موقعیت داده نشد.';
        reject(new ApiError('برای این مرحله اجازهٔ دسترسی به موقعیت را بدهید.'));
      }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
    });
  }

  async function startTimer(id, stageId, seconds, clock) {
    // The server starts the node's timer on the first completion attempt and refuses until it elapses.
    try { await action(id, { stage_id: stageId, operation: 'complete' }); } catch (error) { /* expected: not elapsed yet */ }
    let left = seconds;
    const tick = () => {
      clock.textContent = left > 0 ? faNumber(Math.floor(left / 60)) + ':' + String(left % 60).padStart(2, '0') : 'تمام شد ✓';
      left -= 1;
      if (left < -1) clearInterval(timerHandle);
    };
    tick();
    timerHandle = setInterval(tick, 1000);
  }

  async function loadMedia(id, stageId, holder) {
    try {
      const media = await action(id, { stage_id: stageId, operation: 'media' });
      const tag = { image: 'img', audio: 'audio', video: 'video' }[media.tool];
      if (!tag) return;
      holder.append(el(tag, { src: media.url, alt: media.filename || '', controls: tag !== 'img', playsinline: tag === 'video' }));
    } catch (error) { holder.append(el('p', { class: 'muted' }, 'رسانهٔ این مرحله در دسترس نیست.')); }
  }

  function loadJsQr() {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    return new Promise((resolve, reject) => {
      const script = el('script', { src: root.dataset.jsqr });
      script.onload = () => resolve(window.jsQR);
      script.onerror = () => reject(new Error('jsQR'));
      document.head.append(script);
    });
  }

  async function scanQr(field) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast('دوربین در دسترس نیست؛ کد زیر QR را وارد کنید.', 'bad');
      return;
    }
    let stream;
    let decode;
    const video = el('video', { playsinline: true, muted: true });
    const close = el('button', { type: 'button', class: 'primary' }, 'بستن');
    const overlay = el('div', { class: 'scanner' }, video, el('p', {}, 'کد QR را جلوی دوربین بگیرید'), close);
    const stop = () => { if (stream) stream.getTracks().forEach((track) => track.stop()); overlay.remove(); };
    close.addEventListener('click', stop);
    try {
      if ('BarcodeDetector' in window && (await window.BarcodeDetector.getSupportedFormats()).includes('qr_code')) {
        const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
        decode = async () => { const codes = await detector.detect(video); return codes.length ? codes[0].rawValue : null; };
      } else {
        const jsQR = await loadJsQr();
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d', { willReadFrequently: true });
        decode = async () => {
          if (!video.videoWidth) return null;
          const scale = Math.min(1, 640 / video.videoWidth);
          canvas.width = Math.round(video.videoWidth * scale);
          canvas.height = Math.round(video.videoHeight * scale);
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          const image = context.getImageData(0, 0, canvas.width, canvas.height);
          const code = jsQR(image.data, image.width, image.height, { inversionAttempts: 'attemptBoth' });
          return code ? code.data : null;
        };
      }
    } catch (error) { toast('اسکنر بارگذاری نشد؛ کد را دستی وارد کنید.', 'bad'); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    } catch (error) { toast('اجازهٔ دوربین داده نشد.', 'bad'); return; }
    document.body.append(overlay);
    video.srcObject = stream;
    await video.play();
    const look = async () => {
      if (!overlay.isConnected) return;
      try {
        const value = await decode();
        if (value) { field.value = value; stop(); toast('کد خوانده شد.', 'good'); return; }
      } catch (error) { /* keep looking */ }
      setTimeout(look, 150);
    };
    look();
  }

  // ---------- router ----------
  function route() {
    clearInterval(timerHandle);
    const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
    if (parts[0] === 'city' && parts[1]) return showCity(parts[1]);
    if (parts[0] === 'game' && parts[1]) return showGame(parts[1]);
    if (parts[0] === 'session' && /^\d+$/.test(parts[1] || '')) return showSession(parts[1]);
    if (parts[0] === 'me') return showMine();
    return showCities();
  }

  // Inside the Android app: let the player switch to another game server.
  if (/UrbanGamesApp\//.test(navigator.userAgent)) {
    const nav = document.querySelector('.bar nav');
    if (nav) nav.append(el('a', { href: 'https://localhost/?setup=1' }, 'سرور'));
  }

  window.addEventListener('hashchange', route);
  route();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register(root.dataset.sw, { scope: root.dataset.scope }).catch(() => {});
  }
})();
