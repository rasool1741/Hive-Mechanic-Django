# گزارش ممیزی فنی پروژه Hive Mechanic

تاریخ ممیزی: ۳۰ اکتبر ۲۰۲۶  
مبنای ممیزی: Fork کاربر، شاخه `master`، تمام Submoduleهای ثبت‌شده و سند `docs/Urban_Games_HiveMechanic_SRS_FA.md`.

> نام فایل SRS موجود با نام اعلام‌شده `Urban_Games_SRS_FA.md` متفاوت است؛ فایل موجود و هم‌محتوا با نام `Urban_Games_HiveMechanic_SRS_FA.md` به‌عنوان مرجع استفاده شد.

## ۱. خلاصه اجرایی

Hive Mechanic در وضعیت فعلی یک پلتفرم Django برای اجرای فعالیت‌ها و بازی‌های مکالمه‌ای/پیام‌رسانی است. هسته ارزشمند پروژه حاضر است: مدل‌های `Game`، `GameVersion`، `InteractionCard`، `Player` و `Session`، موتور `django_dialog_engine`، Builder گرافی، مدیریت رسانه، لاگ فعالیت و کانال‌های پیام‌رسانی.

اما SRS محصولی کامل‌تر تعریف می‌کند: پلتفرم چندشهری، کاتالوگ عمومی بازی، PWA موبایل، ورود با OTP، ابزارهای GPS/Geofence/QR/Compass، امتیاز و تیم، API نسخه‌دار، انتشار امن، گزارش مدیریتی، فارسی‌سازی کامل و اجرای server-authoritative. بخش عمده این لایه محصول در مخزن فعلی یا وجود ندارد یا فقط به شکل زیرساخت پراکنده و نمونه‌های قدیمی دیده می‌شود.

نتیجه اصلی: موتور بازی و مدل‌های موجود باید حفظ شوند و توسعه باید به‌صورت لایه‌ای روی آن‌ها انجام شود؛ بازنویسی موتور یا مهاجرت بی‌دلیل از Django ریسک مستقیم برای بازی‌ها، نسخه‌ها و Submoduleها دارد.

## ۲. شواهد مخزن و معماری فعلی

### لایه‌ها

| لایه | اجزای فعلی | ارزیابی |
|---|---|---|
| وب و پیکربندی | `hivemechanic/`, `manage.py` | Django مرکزی، URLها و تنظیمات پایه موجود |
| Builder | `builder/`, قالب‌ها و `builder/static/builder-js/` | پنل فعالیت، گراف Cytoscape، فرم‌ها، رسانه، بازیکن، نشست و مجوزها موجود |
| مدل بازی | `builder/models.py` | Game، نسخه، کارت، Player، Session، StateVariable و DataProcessor موجود |
| موتور | `django_dialog_engine/` | Submodule مستقل با `DialogMachine` و Nodeهای مکالمه/شرط/وقفه/HTTP |
| کانال‌ها | `simple_messaging*`, `twilio_support/`, `http_support/` | SMS، Twilio، Azure، Loopback، Switchboard و کلاینت HTTP موجود |
| عملیات | `quicksilver/`, `simple_backup/`, `nagios_monitor/`, `docker_utils/` | صف/وظایف، پشتیبان‌گیری، مانیتورینگ و ابزار Docker موجود |
| نمونه‌ها | `examples/` | QR، مکان، نقاشی/مأموریت، بازی تک‌دکمه‌ای و pygame موجود |
| استقرار | `docker/` | Django، Nginx و cron تعریف شده؛ PostgreSQL در Compose فعلی کامنت است |

تمام ۱۲ Submodule ثبت‌شده در `.gitmodules` مقداردهی شده‌اند و در Commitهای pin‌شده قرار دارند. وضعیت Git والد پس از Clone و ممیزی پاک بود.

## ۳. قابلیت‌های موجود و قابل حفظ

### ۳.۱ موتور و مدل بازی

- `Game`، `GameVersion` و تعریف JSON نسخه بازی.
- اتصال بازی به `InteractionCard` و نگهداری snapshot نسخه.
- `Player`، `Session` و متغیرهای وضعیت در سطح بازی/بازیکن/نشست.
- اجرای Dialog با `DialogMachine` و Nodeهای `Begin`، `Prompt`، `If`، `Loop`، `Random Branch`، `Pause`، `Interrupt`، `Record Variable`، `Update Variable`، `HTTP Response`، `Embed` و `End`.
- مسیر CLI اجرای فعالیت در `cli_support/management/commands/cli_run_activity.py`.
- نسخه‌بندی مهاجرت‌های متعدد مدل‌ها و تست‌های واحد موتور.

### ۳.۲ Builder و کارت‌ها

- ایجاد فعالیت، ویرایش تعریف، ذخیره `GameVersion` و تغییر نام/slug.
- نمایش و ویرایش گراف با Cytoscape و ابزارهای تبدیل گراف.
- فهرست کارت‌ها، دسته‌بندی، فعال/غیرفعال‌سازی و Repository کارت.
- نصب Repository پیش‌فرض و دستورهای مدیریت کارت مانند load، validate، diff و dump.
- مدیریت رسانه و حذف فایل‌های مرتبط.
- مدیریت نویسندگان، Moderatorها، بازیکنان و نشست‌ها.
- کنترل دسترسی مبتنی بر Django Permission/Group.
- Data Processor و اتصال به داده/API بیرونی.

### ۳.۳ کانال‌ها، عملیات و تست

- Submoduleهای پیام‌رسانی برای Twilio، Azure، Loopback و Switchboard.
- ثبت رخداد و گزارش Activity Logger.
- وظایف زمان‌بندی/پس‌زمینه با Quicksilver.
- پشتیبان‌گیری، لاگ دیتابیسی، مانیتورینگ Nagios و Docker utilities.
- حدود ۱۸۷ فایل مرتبط با migration/test در مخزن و Submoduleها شناسایی شد؛ تست‌های Builder، موتور Dialog، مجوزها و نمونه‌ها وجود دارند.
- نمونه‌های QR و مکان، و کلاینت‌های pygame، انعطاف موتور برای بازی‌های تعاملی را نشان می‌دهند؛ این نمونه‌ها جایگزین ابزار وب موبایل SRS نیستند.

## ۴. تطبیق SRS با کد فعلی

وضعیت‌ها: **موجود** یعنی شواهد مستقیم در کد؛ **ناقص** یعنی بخشی از مسیر وجود دارد اما معیار SRS را کامل نمی‌کند؛ **جدید** یعنی قابلیت قابل اتکایی در کد فعلی پیدا نشد.

| حوزه SRS | وضعیت | شواهد/شکاف اصلی |
|---|---|---|
| حفظ موتور سرور | موجود | `django_dialog_engine`، `GameVersion`، `Session` |
| Builder گرافی | ناقص تا موجود | گراف و CRUD موجود؛ undo/redo، autosave، فرم schemaمحور و انتشار کامل اثبات نشد |
| کارت‌های قابل توسعه | ناقص | کارت، metadata و کد اجرایی وجود دارد؛ قرارداد schema امن و اعتبارسنجی انتشار یکپارچه نیست |
| ثبت‌نام بازیکن | ناقص | جریان User و درخواست دسترسی وجود دارد؛ OTP موبایل و اتصال رسمی User به Player کامل نیست |
| ترتیب شهر ← بازی | جدید | مدل/صفحه City و کاتالوگ عمومی مطابق SRS پیدا نشد |
| مدل City و اتصال Game | جدید | در مدل‌های فعلی City مشاهده نشد؛ migration افزایشی لازم است |
| وضعیت‌های Draft/Testing/Published/Paused | ناقص | آرشیو و metadata وجود دارد؛ چرخه انتشار کامل و رسمی پیدا نشد |
| صفحه معرفی/قوانین بازی | جدید تا ناقص | صفحات Builder وجود دارند؛ تجربه عمومی بازیکن با قوانین و رضایت کامل دیده نشد |
| اجرای وب موبایل بازی | ناقص | مسیرهای Dialog/Integration و نمونه کلاینت وجود دارد؛ API عمومی Session/Event و UI PWA کامل نیست |
| server-authoritative event flow | ناقص | موتور سرور موجود است؛ event_id، idempotency، schema payload و تراکنش یکپارچه برای API وب اثبات نشد |
| نتیجه، امتیاز و دستاورد | ناقص | متغیر و Session وجود دارد؛ Score Ledger، فرمول نسخه‌ای، Badge و نتیجه استاندارد مشخص نیست |
| تیم و بازی گروهی | جدید | مدل Team/TeamSession قابل اتکا پیدا نشد |
| GPS/Geofence | ناقص تا جدید | نمونه مکان وجود دارد؛ اعتبارسنجی server-side دقت، تازگی، چند نمونه و geofence محصولی موجود نیست |
| نقشه بازیکن | جدید | گراف Cytoscape Builder نقشه جغرافیایی بازیکن نیست |
| قطب‌نما | جدید | پیاده‌سازی قابل اتکا در مخزن پیدا نشد |
| QR Scanner/Generator | ناقص | نمونه QR و تولید Python وجود دارد؛ QR امضاشده، محدود به نشست، یک‌بارمصرف و API وب کامل نیست |
| پاسخ متنی/گزینه‌ای | موجود/ناقص | قابلیت‌های Dialog و کارت‌ها موجود؛ نرمال‌سازی فارسی و schema امن باید تکمیل شود |
| رسانه و مدرک | ناقص | مدیریت رسانه موجود؛ capture موبایل، کنترل MIME/حجم، URL امضاشده و review queue نیازمند تکمیل است |
| Hint | ناقص | مفهوم آزادسازی/متغیر قابل مدل‌سازی است؛ سیستم هینت اتمیک با هزینه و سقف استفاده یافت نشد |
| PWA | ناقص | manifest/favicon در قالب‌ها وجود دارد؛ Service Worker، cache امن، IndexedDB و صف آفلاین اثبات نشد |
| API نسخه‌دار | جدید تا ناقص | endpointهای JSON داخلی Builder وجود دارند؛ قرارداد `/api/v1` عمومی SRS وجود ندارد |
| گزارش مدیریتی | ناقص | Activity Log و صفحات مدیریتی وجود دارند؛ داشبورد KPI شهر/بازی/مرحله مطابق SRS کامل نیست |
| فارسی و RTL | ناقص | locale وجود دارد اما پوشش UI، Builder، Admin، PWA و RTL کامل نیست |
| امنیت و حریم خصوصی | ناقص | Permission، CSRF و تنظیمات امنیتی پایه هست؛ rate limit، idempotency، secrets، privacy retention و signed upload نیازمند کار است |
| Docker و محیط تکرارپذیر | ناقص | Compose موجود؛ سرویس DB کامنت، `.env.example` و راه‌اندازی کامل تولیدی ناقص است |

## ۵. قابلیت‌هایی که باید توسعه پیدا کنند

این موارد هسته موجود دارند اما برای معیار SRS کافی نیستند:

1. Builder: اعتبارسنجی گراف، مسیرهای چندخروجی، شرط‌ها، مرحله‌های زمان‌دار/اختیاری، schema کارت، پیش‌نمایش، ذخیره خودکار و انتشار.
2. انتشار: snapshot کامل کارت/تنظیمات، checksum، rollback و سیاست نشست‌های فعال.
3. اجرای بازی: API رویداد، کنترل مالکیت نشست، idempotency، تراکنش اتمیک و عدم افشای پاسخ/مختصات/شاخه محرمانه.
4. حساب بازیکن: اتصال امن User/Player بدون حذف شناسه‌های پیامکی، OTP، خروج و لغو نشست.
5. کارت‌های پاسخ و رسانه: نرمال‌سازی فارسی، اعتبارسنجی schema، کنترل فایل و صف بررسی.
6. QR و مکان: token امن، محدودیت زمان/نشست، دقت و تازگی GPS و fallback دستگاه.
7. رسانه، Activity Log، گزارش‌ها، Permissionهای دامنه‌ای و فارسی‌سازی.
8. Docker/CI: محیط توسعه، PostgreSQL/PostGIS، داده نمونه، مهاجرت و تست قابل تکرار.

## ۶. قابلیت‌هایی که باید کاملاً اضافه شوند

- مدل و پنل `City`، اتصال بازی به شهر و کاتالوگ عمومی با فیلتر.
- تجربه بازیکن مستقل از پنل Builder: ورود، انتخاب شهر، بازی‌های شهر، قوانین، اجرای بازی و نتیجه.
- API عمومی نسخه‌دار برای City/Game/Session/Event/Hint.
- PWA واقعی با service worker، manifest بازیکن، مجوزهای موبایل و صف کنترل‌شده آفلاین.
- Score Ledger، Achievement و در فاز بعد Team/TeamSession.
- ابزارهای Compass، Map/Geofence محصولی و QR Scanner وب.
- Audit/Security تکمیلی، rate limiting، signed URL و سیاست نگهداری مکان.

## ۷. ناسازگاری‌ها و ریسک‌های معماری

### وابستگی‌ها

- محیط فعلی Python `3.11.0` و Node `24.18.0` است، اما Django نصب نیست و `python manage.py check` با `ModuleNotFoundError` متوقف شد.
- Submoduleها برای Python 3.10+ نسخه‌هایی مانند Django `5.2.18` را شرطی اعلام می‌کنند، درحالی‌که کد و docstringهای اصلی میراث Django 1.11 را نشان می‌دهند. سازگاری واقعی باید با نصب در محیط ایزوله و تست migration ثابت شود؛ ارتقای سراسری بدون تست خطرناک است.
- `requirements.txt` نسخه‌های بسیار جدید/آینده‌نگر برای Python 3.11، numpy، psycopg2 و برخی کتابخانه‌ها دارد و چند Submodule همین وابستگی‌ها را تکرار می‌کنند؛ lock کامل Python وجود ندارد.
- `package-lock.json` موجود است، ولی `package.json` عمدتاً ابزار lint است و محصول PWA/فرانت‌اند مستقل ندارد.

### معماری

- تنظیمات پایه SQLite است، اما SRS و README برای PostgreSQL/PostGIS طراحی شده‌اند؛ قابلیت‌های مکانی بدون تصمیم رسمی دیتابیس قابل اتکا نیستند.
- `local_settings.py` واقعی وجود ندارد و Compose به `.env` وابسته است.
- موتور Dialog و کارت‌ها با اجرای کد/تعریف‌های JSON قدرتمندند؛ بدون sandbox، allowlist و کنترل Repository، سطح حمله بالا است.
- مدل‌های فعلی برای پیام‌رسانی/CLI ساخته شده‌اند و API عمومی موبایل، event log اتمیک و idempotency باید به‌صورت افزایشی به آن‌ها متصل شوند؛ ایجاد موتور یا مدل موازی ممنوع است.

## ۸. ریسک دیتابیس و Migration

- افزودن `City` و FK به `Game` خطر شکستن بازی‌های قدیمی دارد؛ FK باید ابتدا nullable باشد، رکورد «تخصیص‌نیافته»/فرایند انتساب تعریف شود و بازی قدیمی خودکار Published نشود.
- اتصال User به Player باید جدول پیوند یا FK افزایشی باشد؛ شناسه‌های Player پیامکی و Sessionهای تاریخی نباید بازنویسی شوند.
- تغییر `GameVersion.definition` می‌تواند snapshot و پاسخ‌های محرمانه را خراب کند؛ نسخه منتشرشده باید immutable بماند.
- تبدیل SQLite به PostgreSQL/PostGIS تفاوت GIS، index، timezone و نوع داده دارد و باید در backup/restore آزمایشی و محیط داده واقعی تست شود.
- تعداد رکوردهای Game، Version، Player و Session قبل/بعد migration باید مقایسه شود؛ migration destructive، data rewrite بی‌صدا و حذف ستون ممنوع است.
- افزودن امتیاز، رویداد و تیم بدون unique constraint و تراکنش می‌تواند duplicate score، replay event و race condition تولید کند.
- هر migration باید روی دیتابیس خالی و snapshot داده واقعی، با rollback مستند و تست خودکار اجرا شود.

## ۹. اقلامی که باید حتماً حفظ شوند

1. `DialogMachine` و Nodeهای موجود؛ موتور جدید یا جایگزین ساخته نشود.
2. مدل‌های `Game`، `GameVersion`، `InteractionCard`، `Player`، `Session` و `StateVariable`.
3. Interaction Cardها، شناسه‌ها، metadata و Repositoryها؛ کارت غیرفعال فقط با سیاست سازگاری مدیریت شود.
4. Graph/Activity Map و JSON تعریف بازی؛ UI جدید باید backward-compatible باشد.
5. کانال‌های SMS/Twilio/Azure/Loopback و CLI؛ PWA مکمل است نه جایگزین.
6. migrationهای تاریخی، slugها، Sessionهای فعال و داده رسانه.

## ۱۰. جمع‌بندی تشخیصی

پروژه برای «هسته اجرای بازی و پنل طراحی فعالیت» پایه مناسبی دارد، اما برای SRS فعلی هنوز محصول عمومی چندشهری و موبایل نیست. بیشترین ریسک در سه نقطه است: ناسازگاری/عدم نصب وابستگی‌ها، تصمیم نامشخص SQLite در برابر PostgreSQL/PostGIS، و نبود مرز امن و تراکنشی بین کلاینت موبایل و موتور سرور. ابتدا باید baseline قابل اجرا و قراردادهای داده/نسخه تثبیت شود؛ سپس City و مسیر بازیکن روی موتور فعلی ساخته شود.
