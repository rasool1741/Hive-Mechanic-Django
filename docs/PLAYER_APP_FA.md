# اپ بازیکن (PWA و اندروید)

بازیکن‌ها از دو راه بازی می‌کنند و هر دو به همان سرور و همان حساب وصل‌اند:

| راه | نصب | توضیح |
|---|---|---|
| **PWA** | نشانی `https://<سرور>/play/` را در Chrome یا Safari باز کنید و «افزودن به صفحهٔ اصلی» را بزنید | بدون فروشگاه؛ روی اندروید و iOS |
| **اپ اندروید** | فایل `urban-games-player.apk` از Release با نام `android-latest` | در اولین اجرا نشانی سرور را می‌پرسد (یا از `PLAYER_SERVER_URL` پر می‌شود) |

پیشرفت بازی روی سرور ذخیره می‌شود؛ بازیکن می‌تواند در PWA شروع کند و در اپ ادامه دهد.

## ابزارهای مرحله

| ابزار | PWA | اپ اندروید |
|---|---|---|
| سؤال و متن | ✓ | ✓ |
| GPS | ✓ (فقط روی HTTPS) | ✓ مجوز مکان |
| QR | دوربین؛ BarcodeDetector یا jsQR، و ورود دستی | ✓ مجوز دوربین |
| تایمر، راهنما، نمایش رسانه | ✓ | ✓ |
| ارسال عکس/صدا/ویدیو | ✓ | ✓ |

## راه‌اندازی سرور (Production)

1. **HTTPS اجباری است.** GPS، دوربین، Service Worker و کوکی‌های امن بدون HTTPS کار نمی‌کنند.
2. تنظیمات پیشنهادی در `local_settings.py`:

```python
PLAYER_SIGNUP_ENABLED = True              # بستن ثبت‌نام عمومی: False
PLAYER_SIGNUP_LIMIT = (5, 3600)           # حداکثر ثبت‌نام از یک IP در ساعت
PLAYER_LOGIN_FAILURE_LIMIT = (10, 900)    # ورود ناموفق از یک IP در ۱۵ دقیقه
GAME_WRONG_ANSWER_LIMIT = (10, 60)        # پاسخ غلط در هر مرحله در دقیقه
PLAYER_MEDIA_ROOT = '/app/private_player_media'  # فایل‌های بازیکن، بیرون از پوشهٔ کد و بیرون از MEDIA_ROOT
PLAYER_TRUST_X_FORWARDED_FOR = True       # فقط اگر پشت Nginx/پروکسی معتبر هستید
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': 'redis://127.0.0.1:6379/1'}}
```

   محدودیت‌ها روی Cache نگه داشته می‌شوند. Cache پیش‌فرض Django فقط در همان پروسه است؛ با چند Worker از Redis یا Memcached استفاده کنید.
3. `python manage.py migrate` و `python manage.py collectstatic`.
4. بازیکنی که ثبت‌نام می‌کند هیچ دسترسی‌ای به Builder ندارد (مجوز `builder.builder_login` لازم است).

## ساخت اپ اندروید

ساخت خودکار در GitHub Actions: فایل `mobile/ci/android-apk.yml` را در `.github/workflows/` کپی کنید (توکن فعلی اجازهٔ Push فایل Workflow را ندارد؛ یا در GitHub با Add file بسازید، یا به توکن مجوز Workflows: Read and write بدهید):

- با هر Push روی پوشهٔ `mobile/` یا اجرای دستی (Run workflow).
- خروجی: Artifact و Release پیش‌انتشار `android-latest` با فایل `urban-games-player.apk`.
- برای اینکه اپ مستقیم به سرور شما وصل شود، در GitHub به Settings → Secrets and variables → Actions → Variables بروید و `PLAYER_SERVER_URL` (مثلاً `https://play.example.com`) را بسازید.

ساخت دستی (نیازمند JDK 21 و Android SDK):

```bash
cd mobile
npm ci
npx cap sync android
cd android && ./gradlew assembleDebug
```

### نکته‌ها

- APK فعلی با کلید Debug امضا شده؛ برای نصب مستقیم (Sideload) کافی است. برای Google Play یا Bazaar باید Keystore انتشار ساخته و `assembleRelease` امضا شود.
- در اپ، پیوند «سرور» در نوار بالا صفحهٔ تغییر نشانی سرور را باز می‌کند.
- iOS: همان PWA در Safari کار می‌کند. ساخت اپ iOS به macOS و حساب Apple Developer نیاز دارد (`npx cap add ios`).
