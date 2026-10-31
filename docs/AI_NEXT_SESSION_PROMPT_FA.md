# Prompt تحویل به هوش مصنوعی بعدی

تو روی مخزن `D:\github\Hive-Mechanic-Django` و شاخه `feature/urban-games-platform` کار می‌کنی. هدف، تکمیل Hive Mechanic به پلتفرم بازی شهری است؛ پروژه را از صفر بازنویسی نکن و قابلیت‌های سالم موتور فعلی را حفظ کن.

## وضعیت شروع

آخرین Commit `4063333` است. ابتدا `git status`, `git log -12` و این سه سند را بخوان:

- `docs/PROJECT_STATUS_FA.md`
- `docs/ARCHITECTURE_HANDOFF_FA.md`
- `docs/Urban_Games_HiveMechanic_SRS_FA.md`

Runtime فعلی بر اساس Sequence و InteractionCard است. `definition.graph` فعلاً فقط برای Builder، Preview و اعتبارسنجی انتشار است و هنوز منبع اجرای Session نیست.

## اولویت توسعه

1. قرارداد نگاشت Graph به Sequence/InteractionCard را طراحی و مستند کن.
2. اجرای مرحله و انتخاب Edge شرطی را سرورمحور و تراکنشی کن.
3. نسخه‌های قدیمی بدون Graph را با fallback حفظ کن.
4. تست کامل اجرای Graph، Session، Replay، GPS، QR، امتیاز و پایان بازی بنویس.
5. Preview را به شبیه‌ساز امن Draft تبدیل کن، بدون ثبت داده Production.
6. تست Browser Builder را با Firefox و geckodriver اجرا کن.
7. سپس استقرار و تفاوت SQLite مکانی با Production را بررسی کن.

## دستورات محیط

در PowerShell و قبل از Django:

```powershell
$env:GDAL_DATA='D:\OSGeo4W\apps\gdal\share\gdal'
$env:PROJ_LIB='D:\OSGeo4W\share\proj'
$env:GDAL_LIBRARY_PATH='D:\OSGeo4W\bin\gdal313.dll'
$env:GEOS_LIBRARY_PATH='D:\OSGeo4W\bin\geos_c.dll'
$env:PATH='D:\OSGeo4W\bin;' + $env:PATH
```

از `.venv\Scripts\python.exe` استفاده کن. دیتابیس آزمایشی مستقل بساز، `migrate` را روی آن اجرا کن، سپس:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run --skip-checks
python manage.py test builder
```

Browser tests فقط با Firefox/geckodriver قابل اجرا هستند؛ نبود آن را Pass گزارش نکن. هیچ تستی را حذف یا Skip نکن.

## قوانین ایمنی

- به دیتابیس واقعی دست نزن و Push نکن.
- قبل از تغییر، Working Tree و Commit پایه را بررسی کن.
- Migration غیرضروری نساز.
- موتور `django_dialog_engine` و Sequence/InteractionCard را بازنویسی نکن.
- امتیاز، مرحله بعد، GPS، QR و وضعیت Session را از کلاینت معتبر ندان.
- هر فاز را تست کن، Roadmap را با نتیجه واقعی به‌روز کن و Commit محلی مستقل بساز.
- تست اجرا نشده را موفق اعلام نکن و محدودیت محیط را در مستندات ثبت کن.

## وضعیت آخرین فاز

Graph Runtime در `builder/views.py` به‌صورت حداقلی و سرورمحور پیاده‌سازی شده است. ابتدا `docs/GRAPH_RUNTIME_DESIGN_FA.md` را بخوان. برای تست واقعی از `--testrunner django.test.runner.DiscoverRunner` استفاده کن؛ تست Graph و مجموعه ۱۱تایی Backend/GameVersion/Urban Tools موفق شده‌اند. کار بعدی: افزودن تست شاخه شرطی و تکمیل نگاشت Graph به InteractionCard بدون پذیرش داده حساس از کلاینت.
