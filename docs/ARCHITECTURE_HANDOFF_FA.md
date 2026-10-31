# معماری و تحویل فنی

## لایه‌های اصلی

- `builder/models.py`: مدل‌های City، Game، GameVersion، InteractionCard، Player، Session، SessionEvent، ScoreLedger، Hint و Team.
- `builder/views.py` و `builder/urls.py`: صفحات Builder، کاتالوگ عمومی، APIهای شهر/بازی و عملیات Session.
- `builder/static/builder-js`: Builder قدیمی، Sequence editor و Cytoscape.
- `builder/templates/builder_js.html`: پوسته رابط Builder و پنل Graph جدید.
- `django_dialog_engine`: قرارداد اجرای کارت‌ها و جریان پیام/تعامل موجود.
- Submoduleها و مدل‌های پیام‌رسان: وابستگی‌های قدیمی پروژه هستند و نباید بدون بررسی تغییر کنند.

## جریان نسخه و اجرا

مدیر تعریف JSON بازی را در نسخه Draft ذخیره می‌کند. `GameVersion.validate_definition()` توالی‌ها، کارت‌ها، کارت شروع و در صورت وجود `definition.graph` را بررسی می‌کند. `publish()` فقط تعریف معتبر را Published می‌کند.

در Runtime، `GameVersion.process_incoming()` و `Session.process_incoming()` بر اساس Sequence و InteractionCard عمل می‌کنند. API سرورمحور `POST /builder/api/v1/sessions/<id>/actions` مالکیت و وضعیت Session را کنترل می‌کند، شرایط GPS/QR را اعتبارسنجی می‌کند، امتیاز را سمت سرور ثبت می‌کند و با `stage_index` ترتیب Sequence را جلو می‌برد.

## Graph و Builder جدید

Graph در کلید `definition.graph` با ساختار `nodes`, `edges`, `start`, `end` ذخیره می‌شود. Nodeها می‌توانند `tools` و `conditions` داشته باشند و Edge می‌تواند `condition` داشته باشد. API زیر فقط برای کاربر مجاز Builder است:

`/builder/api/v1/games/<slug>/graph`

GET گراف را می‌خواند و POST یک نسخه Draft جدید ایجاد می‌کند. پنل `urban_graph.js` با Cytoscape Node/Edge و metadata ابزارها را مدیریت می‌کند. Preview فقط کلاینتی است و Session یا امتیاز ایجاد نمی‌کند.

## مرز صریح Graph و Runtime

وجود Graph و موفقیت اعتبارسنجی انتشار به معنی اجرای گراف نیست. Graph هنوز منبع تعیین مرحله فعلی، انتخاب Edge، اجرای شرط یا پایان Session نیست. هرگونه توسعه بعدی باید ابتدا قرارداد نگاشت `graph node -> sequence/card`، رفتار مسیرهای چندشاخه، خطایابی و مهاجرت تعریف‌های قدیمی را مشخص کند.

## دیتابیس و Migration

Migrationهای `0050` تا `0056` برای Release metadata، City/catalog، Player، SessionEvent، Hint/ScoreLedger، Team و `Game.Meta` ثبت شده‌اند. Migration `0036` به Migration واقعی `filer.0001_initial` وابسته شده است. تست‌های بعدی فقط روی دیتابیس آزمایشی مستقل اجرا شوند.

## ریسک‌های اصلی

- تفاوت معنای شناسه Node گراف با شناسه Sequence/Card.
- Race Condition هنگام تغییر مسیر و Event؛ API فعلی قفل تراکنشی دارد اما Graph runtime هنوز ندارد.
- نسخه‌های قدیمی تعریف بازی که Graph ندارند؛ fallback فعلی باید حفظ شود.
- تفاوت SQLite مکانی با Production PostGIS/SpatiaLite.
- نبود Browser toolchain در محیط فعلی.

## تغییر فاز Graph Runtime

Endpoint عملیات اکنون برای نسخه‌های دارای Graph مسیر جداگانه دارد. تابع `_graph_game_action` Node جاری را از Session می‌خواند، ابزارهای GPS/QR را بررسی می‌کند، Edge معتبر را فقط سرور انتخاب می‌کند و نتیجه را در SessionEvent/ScoreLedger ثبت می‌کند. نسخه‌های فاقد Graph همچنان مسیر Legacy را طی می‌کنند. جزئیات قرارداد در `docs/GRAPH_RUNTIME_DESIGN_FA.md` آمده است.

برای اجرای واقعی تست‌ها باید Test Runner پیش‌فرض `NoDbTestRunner` با `django.test.runner.DiscoverRunner` جایگزین شود؛ در غیر این صورت جدول‌های تست ساخته نمی‌شوند. با Runner استاندارد، تست Graph و ۱۱ تست Backend/GameVersion/Urban Tools موفق شدند. Browser به Firefox/geckodriver نیاز دارد.

قرارداد ابزارها در همان `nodes[].tools` باقی مانده است. `operation=hint` برای Hint اجرا می‌شود؛ عملیات تکمیل Node ابزارهای Timer، Quiz و Text را با داده Published و زمان سرور بررسی می‌کند. مقدار امتیاز و نتیجه از کلاینت معتبر نیست. Media و ابزارهای تصویری هنوز Runtime ندارند.
