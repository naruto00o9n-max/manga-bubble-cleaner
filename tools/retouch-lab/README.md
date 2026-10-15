# بوت Discord لخدمة Retouch ومختبر المحاكي

البوت يرسل صورة وقناع تحديد إلى خادم Retouch ويعيد PNG. لا ينزّل نموذج ترميم ولا يشغّل GPU. جميع الملفات الجديدة محصورة في هذا المجلد، ولا يستوردها البوت الموجود في المستودع.

**حالة التحقق:** نجحت ثلاث عمليات حقيقية بالحذف العادي على صور ملونة ومعقدة. وضع AI في النسخة الحالية يحتاج رصيدًا أكبر من هدية حساب الضيف؛ لم ينتج صورة ناجحة في هذا الاختبار. بروتوكول نسخة Drive القديمة اختُبر بجلسة ضيف عادية وأعاد تقييد إصدار العميل. راجع [تفاصيل النتائج](FINDINGS.md).

## الملفات المقصودة

| ملف التطبيق | الهوية | مسار المعالجة |
| --- | --- | --- |
| `Retouch_2.2.4.2.apk` من Drive | `com.magic.retouch` | محلي + خادم؛ بروتوكول `v3` |
| `Retouch-v5.0 beta-MOD-GMA-(Getmodsapk.com).apk` من Drive | `com.advasoft.touchretouch` | مكتبات ونماذج محلية؛ لا يطلب صلاحية الإنترنت |
| Retouch 2.3.8.0 من المصدر الأحدث | `com.magic.retouch` | بروتوكول خادم `v5` ونظام نقاط |

`v5` في عنوان API رقم بروتوكول للخادم، وليس اسم تطبيق TouchRetouch 5.0. لا يثبت اسم المسار `fooocusinpaint` وحده اسم النموذج أو إصداره أو أوزانه.

## تشغيل البوت

يتطلب Python 3.11+، وتطبيق Discord أنشأته في Developer Portal، وملف جلسة خاص بخدمة Retouch.

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/retouch-lab/requirements.txt
export RETOUCH_PROFILE=/private/retouch-profile.json
export DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN
export DISCORD_GUILD_ID=YOUR_TEST_GUILD_ID
export DISCORD_ALLOWED_USERS=YOUR_DISCORD_USER_ID
.venv/bin/python tools/retouch-lab/bot.py
```

أضف البوت إلى خادمك بنطاقي `bot` و`applications.commands` وصلاحيتي إرسال الرسائل وإرفاق الملفات. لا يحتاج Message Content Intent. عند تعيين `DISCORD_GUILD_ID` يتزامن الأمر داخل خادم الاختبار؛ وإلا يستخدم المزامنة العامة. يرد بنتائج خاصة بالمستخدم. لا تضع التوكن أو ملف الجلسة في Git.

الأمر `/retouch` يقبل:

- `image` مع `mask`: قناع بنفس الأبعاد؛ الأبيض للحذف والأسود للحفظ.
- أو `image` مع `x`, `y`, `width`, `height` لمنطقة النص.
- `mode`: AI أو عادي. الوضع الافتراضي AI.

التحديد يدوي في هذا النموذج؛ البوت لا يكشف جميع نصوص الفصل تلقائيًا. يحافظ على بكسلات الصورة خارج القناع كما هي، ويقص المنطقة المحيطة لتقليل تصغير الصفحة. لا يرجع إلى نموذج محلي عند رفض الخادم. يعرض فشل الإصدار أو الرصيد.

## تجهيز جلسة واحدة تخصك

استخدم ملف APK فعليًا من الإصدار المدعوم. إذا نزّلت XAPK للإصدار 2.3.8.0، استخرج `com.magic.retouch.apk` منه. المصدر الأحدث الذي فُحص: [صفحة الإصدار على APKPure](https://apkpure.com/retouch-remove-objects-editor/com.magic.retouch/download/2.3.8.0)، مع مطابقة الاسم ورقم الإصدار بصفحة Google Play.

احصل على معرف تثبيتك التجريبي الثابت من جهاز Android المخصص لك:

```bash
adb -s emulator-5554 shell settings get secure android_id
```

ثم:

```bash
.venv/bin/python tools/retouch-lab/setup_profile.py \
  --apk /private/com.magic.retouch.apk \
  --android-id YOUR_16_HEX_ANDROID_ID \
  --output /private/retouch-profile.json
```

تستخرج الأداة إعدادات النقل من APK وتُنشئ ملفًا خاصًا بصلاحية 0600. مع 2.3.8.0 تطلب حساب ضيف من مسار التسجيل المعتاد وتحتفظ بمعرف الرصيد الذي أصدره الخادم. لا تستخدم بريدًا، ولا تدوّر هوية الجهاز أو الحساب للحصول على نقاط جديدة. أعد استخدام ملف الجلسة نفسه. ترفض الكتابة فوق ملف موجود؛ إن فشل الاتصال بعد حفظ الهوية، احتفظ بالملف للتشخيص.

مع ملف Drive 2.2.4.2 تنشئ جلسة للبروتوكول القديم بلا تسجيل نقاط. لا يضمن إنشاء الملف قبول الخادم لهذا الإصدار. الملفات المعدلة قد تختلف عن حالات الجلسة العادية التي اختُبرت؛ لم تُختبر مطالبة VIP مصطنعة في الموصل.

في 15 أكتوبر 2026 منح الخادم الضيف 10 نقاط. جدول المستخدم العادي الذي أعاده الخادم: الحذف العادي 3 نقاط، وAI المتقدم 25 نقطة للصورة. القيم تخص الإصدار الأحدث وقابلة للتغيير. لم تُشتر نقاط في هذا العمل.

## اختبار الخدمة دون Discord

```bash
.venv/bin/python tools/retouch-lab/cloud_cli.py \
  --profile /private/retouch-profile.json --mode standard \
  /private/input.png /private/mask.png /private/result.png
```

اختر `--mode ai` لاختبار المسار المتقدم إذا قبل الخادم جلستك ورصيدها. يجب أن تكون أبعاد القناع مطابقة للصورة. الحد 20 MB للمرفق و25 مليون بكسل للصورة. النتيجة PNG بالدقة الأصلية؛ مناطق الترميم الكبيرة تُعالج ضمن قصاصة ذات ضلع أقصى يقارب 900 بكسل، ثم تُعاد إلى موضعها. لذلك يفضل اختيار مناطق صغيرة ومتقاربة.

## المحاكي وأداة ADB

يتطلب Java 17+ وAndroid SDK. ثبّت حزم SDK التالية بعد مراجعة تراخيص Android:

```bash
sdkmanager 'platform-tools' 'platforms;android-30' 'emulator' 'system-images;android-30;google_apis;x86_64'
export ANDROID_SDK_ROOT=/path/to/android-sdk
bash tools/retouch-lab/start-emulator.sh
```

بعد اكتمال الإقلاع، في طرفية أخرى:

```bash
python3 tools/retouch-lab/lab.py inspect /private/Retouch.apk
python3 tools/retouch-lab/lab.py install /private/Retouch.apk
python3 tools/retouch-lab/lab.py import-image /private/test.jpg
python3 tools/retouch-lab/lab.py open com.magic.retouch
python3 tools/retouch-lab/lab.py screenshot /private/before.png
python3 tools/retouch-lab/lab.py ui /private/screen.xml
python3 tools/retouch-lab/lab.py tap 200 400
python3 tools/retouch-lab/lab.py swipe 100 300 200 300 --duration 500
```

لفتح التطبيق الآخر استخدم `com.advasoft.touchretouch`. أداة ADB وحدها لا تحتاج Discord. استخدم مفسر البيئة الافتراضية في أمر `inspect`.

ملفا Drive يستهدفان ARM، ويلزم دعم ترجمة ARM في صورة x86 أو محاكي/جهاز ARM مناسب. المحاكاة البرمجية دون KVM قد تكون بطيئة أو غير مستقرة. لا تتضمن الأداة تجاوز ترخيص التطبيق أو تعديل ملف APK.

## تفسير جودة النتائج

اختبر نصًا فوق لون موحد، وتدرج، وخطوط أو أوراق وزخارف. يجب أن يشمل القناع حدود الحروف والظل والتوهج. البقايا خارج القناع لا يزيلها البوت. لقطة الشاشة توثق التفاعل؛ الحكم على جودة التصدير يحتاج الصورة المحفوظة نفسها. لا يمكن إثبات التطابق مع الرسم الأصلي المخفي دون نسخة خالية من النص.

للتحقق من الأداة:

```bash
cd tools/retouch-lab
python3 -m unittest -v test_cloud
```

الاختبارات تفحص حفظ البكسلات خارج القناع، وانتظار المهمة، ورفض القناع غير الصالح، وعدم إرجاع نتيجة عند رفض الرصيد. لا تغني عن اختبار API حقيقي أو تشغيل Discord بتوكنك.
