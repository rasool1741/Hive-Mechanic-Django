# ممیزی نهایی پروژه

تاریخ ممیزی: ۱۴۰۵/۰۸/۰۹ — شاخه `feature/urban-games-platform` — آخرین Commit: `a763291`

## قابلیت‌های واقعاً آماده

- هسته اصلی Hive Mechanic شامل `City`، `Game`، `GameVersion`، `InteractionCard`، `Player`، `Session`، `SessionEvent`، `Hint`، `ScoreLedger` و `Team` موجود و فعال است.
- مسیر Legacy با `Sequence`، `InteractionCard`، `GameVersion.process_incoming` و `Session.process_incoming` حفظ شده است.
- API عملیات سرورمحور Session در `builder/views.py` مالکیت، انتشار، GPS/QR، ترتیب مرحله، Replay، امتیاز و پایان Session را کنترل می‌کند.
- Graph Runtime برای نسخه‌های Published دارای `definition.graph` در همان API فعال است: Node جاری در `Session.session_state`، Edge و ابزارها سمت سرور، و fallback برای بازی بدون Graph.
- ابزارهای پایه Graph یعنی Hint، Timer، Quiz و Text در کد و تست‌های ثبت‌شده پوشش دارند؛ Media مدیر با `django-filer.File` نیز مسیر امن reference دارد.
- Player Image Media با `SessionMediaSubmission`، محدودیت byte، بررسی Pillow، hash، Storage خصوصی، دانلود مالک‌محور و پاک‌سازی orphan پیاده‌سازی شده است. Audio/Video فقط اعتبارسنجی امضای اولیه دارند و آماده اعلام‌شدن کامل نیستند.
- زنجیره Migration تا `0057` و `makemigrations --check` در اجرای ثبت‌شده موفق بوده‌اند. مجموعه ثبت‌شده Backend/Graph/GameVersion/Urban Tools شامل ۱۴ تست موفق است.

## رابط‌ها

- **رابط بازیکن:** کامل نیست. APIهای Session، کاتالوگ، عملیات و Media وجود دارند، اما رابط یکپارچه بازیکن برای انتخاب شهر، اجرای مرحله، نمایش ابزارها، ارسال پاسخ و نمایش نتیجه/PWA محصولی کامل نیست.
- **پنل مدیریت و Builder:** قابلیت‌های قدیمی Sequence/InteractionCard، Draft/Published، Preview کارت‌ها و صفحات مدیریتی موجودند. ویرایشگر Graph در `urban_graph.js` برای Node، Edge، شروع/پایان، شرط و metadata ابزار کار می‌کند، اما UI آن حداقلی است و فرم‌های اختصاصی، پیش‌نمایش تعاملی و جریان کامل بدون کدنویسی هنوز کامل نیستند.

## آنچه از Hive Mechanic اصلی استفاده شده

مدل‌ها، Migrationها، احراز هویت Django، `django_dialog_engine`، Repository کارت‌ها، Sequence/InteractionCard، `process_incoming`، Builder قدیمی، Django Admin، Filer و مسیرهای پیام‌رسان/Integration حفظ و استفاده شده‌اند. Graph، عملیات server-authoritative، ابزارهای اولویت‌دار و Player Media افزوده‌های این توسعه‌اند؛ جایگزین هسته اصلی نشده‌اند.

## ضروری برای MVP

1. ساخت یا تکمیل رابط واقعی بازیکن و اتصال آن به APIهای موجود، بدون اعتماد به امتیاز، مرحله یا نتیجه ارسالی کلاینت.
2. تثبیت قرارداد نگاشت `graph node → Sequence/InteractionCard` یا مستندسازی صریح adapter آن؛ سپس تست شاخه‌های شرطی و اجرای کامل Graph در برابر Legacy.
3. تکمیل اعتبارسنجی و تست واقعی Audio/Video و اتصال Media Submission به شرایط تکمیل مرحله.
4. اجرای تست Browser با Firefox/geckodriver و رفع شکاف‌های UI Builder؛ تست Browser تاکنون اجرا نشده است.
5. تعیین و آزمایش تنظیمات Production برای PostgreSQL/PostGIS یا SpatiaLite، Storage خصوصی، قفل تراکنشی، Backup و مشاهده‌پذیری.
6. رفع هشدارهای محیطی Django و مستندسازی اجرای تکرارپذیر Migration و تست روی دیتابیس آزمایشی مستقل.

## نقطه شروع AI بعدی

ابتدا `builder/views.py`، `builder/models.py`، `builder/urls.py`، `builder/static/builder-js/js/app/urban_graph.js` و تست‌های `builder/tests/tests_backend_e2e.py` را بخواند. سپس بدون تغییر Legacy، رابط بازیکن را روی endpointهای موجود بسازد و تست End-to-End Graph و Legacy را با `DiscoverRunner` اجرا کند. بعد به‌ترتیب Audio/Video واقعی، تست Browser و تنظیمات Production را انجام دهد. از ایجاد Runtime موازی، پذیرش URL/امتیاز/مرحله از کلاینت، حذف تست‌ها یا تغییر دیتابیس واقعی خودداری شود.

## وضعیت این ممیزی

در این ممیزی هیچ کد، Migration، وابستگی یا دیتابیس تغییر داده نشد؛ تست جدید اجرا نشد و Push یا Commit ایجاد نشد. Working Tree پیش از این گزارش فقط فایل untracked همین گزارش را داشت. تست Browser به‌دلیل نبود Firefox/geckodriver اجرا نشده و Django Check موفقِ مستقل در مستندات موجود تأیید نشده است.
