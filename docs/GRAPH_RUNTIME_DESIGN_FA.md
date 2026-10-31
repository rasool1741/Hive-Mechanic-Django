# طرح اتصال Graph به Runtime

## انتخاب Runtime

در `game_action` وجود `definition.graph.nodes` در نسخه Published، پس از احراز نسخه و Session، Graph Runtime را فعال می‌کند. تعریف‌های قدیمی فاقد Graph بدون تغییر از مسیر Sequence/InteractionCard استفاده می‌کنند.

## وضعیت Node

برای جلوگیری از Migration غیرضروری، Node جاری در `Session.session_state['graph_node_id']` ذخیره می‌شود. اگر مقدار وجود نداشته باشد، سرور `graph.start` را به‌عنوان Node اولیه انتخاب می‌کند. این مقدار از کلاینت پذیرفته نمی‌شود و بعد از هر Action معتبر فقط سرور آن را تغییر می‌دهد.

## عملیات و ابزارها

Endpoint موجود `POST /builder/api/v1/sessions/<id>/actions` حفظ شده است. Graph Runtime همان کنترل‌های احراز هویت، مالکیت Session، شهر فعال، نسخه Published، قفل تراکنشی، Replay و ScoreLedger را استفاده می‌کند. ابزارهای `gps` و `qr` در metadata Node به‌ترتیب اعتبارسنجی GPS/Geofence و QR امضاشده را اجباری می‌کنند. ابزارهای `hint`، `timer`، `quiz` و `text_input` نیز در همین Endpoint با زمان و تنظیمات Published اجرا می‌شوند؛ Media هنوز metadata است.

Hint از مدل Hint یا تنظیمات Node خوانده و هزینه‌اش سروری ثبت می‌شود؛ Timer زمان Unix سرور را در Session نگه می‌دارد؛ Quiz و Text با تنظیمات Published مقایسه می‌شوند. ابزار ناشناخته fail closed است.

## انتخاب Edge

Edgeها فقط از `source` برابر Node جاری بررسی می‌شوند. شرط امن فعلی فقط فرم `{variable|key, equals}` را می‌پذیرد و مقدار متغیر را از `Session.session_state` می‌خواند؛ نتیجه شرط از کلاینت گرفته نمی‌شود. وجود بیش از یک Edge قابل انتخاب رد می‌شود. Node بعدی، امتیاز، تکمیل و `graph_node_id` همگی توسط سرور تعیین می‌شوند.

## سازگاری و هم‌زمانی

`select_for_update()` و `transaction.atomic()` روی Session از اعمال هم‌زمان جلوگیری می‌کنند. `SessionEvent(session,event_id)` برای Replay یکتا است. نسخه Published در طول Session ثابت می‌ماند. Graph Runtime هنوز با Sequence/InteractionCard نگاشت یک‌به‌یک ندارد؛ بنابراین ابزارهای کارت‌محور قدیمی باید در فاز بعدی به قرارداد Graph متصل شوند.
