# وضعیت پروژه Hive Mechanic Urban Games

## وضعیت تحویل

- شاخه: `feature/urban-games-platform`
- آخرین Commit: `4063333 Add draft graph builder editor`
- Working Tree در زمان تهیه این سند پاک است.
- هیچ Push یا تغییر دیتابیس واقعی انجام نشده است.

## قابلیت‌های پیاده‌سازی‌شده

- مدل‌های شهر، بازی، نسخه بازی، کارت تعاملی، بازیکن، نشست، رویداد نشست، دفتر امتیاز، Hint و Team.
- مدیریت نسخه Draft/Published و جلوگیری از تغییر مستقیم نسخه Published.
- اعتبارسنجی تعریف بازی، کارت‌های مفقود و کارت شروع.
- قرارداد اختیاری `definition.graph` با اعتبارسنجی start/end، یال‌های نامعتبر، دسترسی‌ناپذیری و حلقه.
- APIهای کاتالوگ شهر و بازی و API عملیات سرورمحور Session.
- کنترل احراز هویت، مالکیت Session، شهر فعال، نسخه Published، GPS/Geofence، QR، تکرار `event_id` و قفل تراکنشی.
- ثبت امتیاز سروری و تعیین مرحله بعد/پایان Session در Endpoint عملیات.
- Builder قدیمی Sequence/InteractionCard، Cytoscape موجود، ذخیره Draft و Preview ساختار کارت‌ها.
- ویرایشگر جدید Graph برای Draft: Node، Edge، شروع/پایان، مسیر شرطی، ابزارهای metadata و Preview JSON.
- ابزارهای تست بدون اینترنت با Repository محلی کارت‌ها.
- اصلاح وابستگی Migration مربوط به `filer.File` و Migration `0056` برای `Game.Meta`.

## محدودیت معماری حیاتی

`definition.graph` اکنون برای طراحی، Preview و اعتبارسنجی انتشار استفاده می‌شود؛ Runtime فعلی هنوز از `sequences` و `InteractionCard` و منطق `process_incoming` استفاده می‌کند. Graph منبع تعیین مرحله فعلی Session یا مرحله بعدی نیست. اتصال واقعی Graph به Runtime مهم‌ترین کار باقی‌مانده است.

## ناقص یا باقی‌مانده

- تبدیل Graph به منبع معتبر اجرای Session و حفظ سازگاری با نسخه‌های قدیمی Sequence.
- همگام‌سازی Nodeهای Graph با InteractionCardها و تعریف قرارداد یکتا برای شناسه مراحل.
- تکمیل فرم‌های اختصاصی و اعتبارسنجی واقعی تنظیمات هر ابزار؛ فعلاً ابزارها در metadata گراف ذخیره می‌شوند.
- Preview تعاملی اجرای مسیر، شرایط و ابزارها هنوز شبیه‌ساز کامل بازی نیست.
- تست مرورگری Builder اجرا نشده؛ Firefox و geckodriver نصب نیستند.
- استقرار Production، تنظیمات PostGIS/SpatiaLite، صف‌ها، Storage و مشاهده‌پذیری نیازمند بررسی جداگانه‌اند.

## تست‌ها و اعتبارسنجی

طبق Commitها و Roadmap:

- Migration زنجیره Builder تا `0056` روی دیتابیس آزمایشی مستقل اجرا شده است.
- `makemigrations --check --dry-run --skip-checks` پس از `0056` بدون اختلاف گزارش شده است.
- تست‌های GameVersion، City، ابزارهای شهری، Permission و Backend E2E در اجراهای ثبت‌شده موفق بوده‌اند؛ Backend E2E مسیر GPS، QR، Hint، ScoreLedger، Session و خطاهای امنیتی را پوشش می‌دهد.
- تست‌های Builder غیرمرورگری در اجرای ثبت‌شده موفق بوده‌اند.
- تست Browser به‌دلیل نبود Firefox/geckodriver اجرا نشده و موفق اعلام نمی‌شود.
- `Django check` در آخرین اجرای این فاز به دیتابیس آزمایشی بدون جدول Migration برخورد کرد؛ خروجی موفقی برای آن ادعا نمی‌شود.

## محدودیت محیط

- Python 3.11 و Django 5.2.18 در `.venv` موجود است.
- GIS با OSGeo4W در `D:\OSGeo4W` و تنظیمات محیطی DLL استفاده می‌شود.
- Cache و Temp نصب روی درایو D تنظیم شده‌اند.
- اجرای تست و Check به دیتابیس آزمایشی Migrated و متغیرهای GDAL/GEOS/PROJ نیاز دارد.
- Docker نصب نشده و نباید برای این تحویل نصب شود.

## وضعیت Graph Runtime

- Graph Runtime در API عملیات Session فعال شده است؛ Sessionهای نسخه Published دارای Graph از `graph.start` و Edgeهای همان Graph استفاده می‌کنند.
- Node جاری در `Session.session_state['graph_node_id']` نگهداری می‌شود و Stage دلخواه، پرش مسیر، GPS/QR نامعتبر و Edge مبهم رد می‌شوند.
- ابزارهای GPS، QR، Hint، Timer، Quiz و Text در Graph Runtime واقعاً بررسی می‌شوند؛ Media هنوز فقط metadata است.
- Test Runner پیش‌فرض پروژه دیتابیس را ایجاد نمی‌کند؛ برای تست واقعی از `--testrunner django.test.runner.DiscoverRunner` استفاده شد.
- تست Graph E2E: `1` تست موفق. مجموعه Backend E2E، GameVersion و Urban Tools: `11` تست موفق.
- `makemigrations --check --dry-run --skip-checks`: `No changes detected`.
- Django Check در زمان تست Error ندارد، اما ۲۸ هشدار محیطی/پیکربندی ثبت می‌کند.
- تست ابزارهای Graph برای Hint، Timer، Quiz و Text: `1` تست موفق؛ Replay Hint، هزینه سروری، Timer زودهنگام، پاسخ غلط، طول متن و امتیاز جعلی پوشش داده شد.
