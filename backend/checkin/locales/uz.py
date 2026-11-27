"""Бот преподавателей — oʻzbekcha (lotin). Тексты стоит вычитать носителю языка."""

MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
          "sentabr", "oktabr", "noyabr", "dekabr"]

TEXTS = {
    # Bogʻlash
    "start_unbound": "👋 Bu oʻqituvchilar davomati boti.\n\n"
                     "Boshlash uchun telefon kamerasini xodim kartochkasidagi QR-kodga qarating "
                     "yoki kartochkadagi kodni (8 belgi) shu yerga yuboring.",
    "code_not_found": "😕 Bunday kod yoʻq. Xodim kartochkasidagi kodni tekshiring "
                      "yoki administratordan yangi kartochka soʻrang.",
    "code_taken": "🔒 Bu kod boshqa Telegramga bogʻlangan. Agar kartochka sizniki boʻlsa — "
                  "administratordan yangi kod soʻrang.",
    "code_other_user": "🔒 Bu Telegram boshqa xodimga bogʻlangan. "
                       "Agar bu xato boʻlsa, administratorga yozing.",
    "code_ok": "Assalomu alaykum, {name}! Telegram bogʻlandi.",
    "choose_lang": "Выберите язык / Tilni tanlang",
    "lang_set": "Til: oʻzbekcha.",
    "consent": "Boshlashdan oldin — davomat qanday ishlaydi:\n\n"
               "• Darsga kelganingizda «📍 Darsga keldim» tugmasini bosasiz — bot shu paytdagi "
               "joylashuvingizni oladi. Hech kim sizni doimiy kuzatmaydi.\n"
               "• Soʻng 5 soniyalik dumaloq video yozasiz: oʻzingiz va maktabga kirish joyi.\n"
               "• Belgi va videoni administratorlar koʻradi, nusxasi yopiq guruhda saqlanadi.\n"
               "• Belgilar asosida jadval boʻyicha soatlaringiz hisoblanadi.\n\n"
               "Rozimisiz?",
    "consent_button": "✅ Roziman",
    "consent_needed": "Davomat belgilash uchun rozilik kerak — xabar ostidagi tugmani bosing.",
    "consent_ok": "Tayyor! Har bir dars oldidan «📍 Darsga keldim» tugmasini bosing.",
    "not_tracked": "Hozircha siz botda davomat belgilamaysiz. Agar bu xato boʻlsa, administratorga yozing.",
    "help": "ℹ️ Qanday belgilash kerak:\n\n"
            "1. Maktabga kelgach, «📍 Darsga keldim» tugmasini bosing (telefondan).\n"
            "2. 5 soniyalik dumaloq video yozing: oʻzingiz va maktabga kirish joyi.\n"
            "3. Bot dars hisoblangan-hisoblanmaganini aytadi.\n\n"
            "Belgi darsdan bir soat oldin ochiladi. Bir maktabdagi ketma-ket darslar — bitta belgi.",

    # Menyu
    "menu_here": "📍 Darsga keldim",
    "menu_today": "📅 Bugungi darslarim",
    "menu_month": "⏱ Oylik soatlarim",
    "menu_lang": "🌐 Til",
    "menu_panel": "🖥 Panel",
    "menu_hint": "Quyidagi menyudan amalni tanlang.",

    # Oʻqituvchi paneli Telegramning oʻzida (Web App)
    "panel_text": "🖥 Oʻqituvchi paneli — jurnal, dars jadvali va soatlar bilan «Mening kabinetim» — "
                  "login va parolsiz, toʻgʻridan-toʻgʻri Telegramda ochiladi.",
    "panel_button": "Panelni ochish",
    "panel_menu_button": "Panel",
    "panel_no_https": "🖥 Panel saytda ochiladi: {url}",

    # Joylashuv
    "loc_await": "🏫 {school}: {lessons}.\n{distance}\n\n"
                 "Endi 5 soniyalik dumaloq video yozing: oʻzingiz va maktabga kirish joyi. Buning uchun {minutes} bor.",
    "dist_ok": "📏 Maktabgacha taxminan {meters} m.",
    "dist_far": "⚠️ Siz maktabdan uzoqdasiz (taxminan {meters} m) — belgi tekshiruvga yuboriladi.",
    "dist_no_coords": "ℹ️ Maktabning xaritadagi joyi belgilanmagan — belgini administrator tekshiradi.",
    "loc_already": "✅ Siz bu darsga allaqachon belgilangansiz.",
    "loc_no_lesson": "Hozir belgilanadigan dars yoʻq: belgi dars boshlanishidan {window} daqiqa oldin ochiladi. "
                     "«📅 Bugungi darslarim»ni koʻring.",
    "loc_forwarded": "Uzatilgan joylashuv qabul qilinmaydi. «📍 Darsga keldim» tugmasini bosing.",
    "loc_venue": "Bu roʻyxatdagi joy, sizning joylashuvingiz kerak. «📍 Darsga keldim» tugmasini bosing.",
    "loc_live": "Jonli joylashuv mos emas. «📍 Darsga keldim» tugmasini bosing — oddiy joylashuv yuboriladi.",

    # Dumaloq video
    "vid_no_pending": "Avval «📍 Darsga keldim» tugmasini bosing, soʻng dumaloq video yozing.",
    "vid_not_note": "Aynan dumaloq video (videoxabar) kerak: oʻngdagi yozish tugmasini kameraga "
                    "oʻtkazing va bosib turing.",
    "vid_forwarded": "Uzatilgan video mos emas — yangisini yozing.",
    "vid_duplicate": "Bu video avval yuborilgan. Yangisini yozing.",
    "vid_late": "⌛ Video uchun vaqt tugadi — dars tekshiruvga yuboriladi. Agar joyida boʻlsangiz, "
                "«📍 Darsga keldim»ni yana bosing va darhol video yozing.",
    "done_header": "✅ Video qabul qilindi.",
    "line_ok": "• {time} {cls} — oʻz vaqtida",
    "line_late": "• {time} {cls} — {minutes} daqiqa kechikish",
    "line_review": "• {time} {cls} — tekshiruvda: {reasons}",
    "reason_far": "maktabdan uzoqda",
    "reason_no_coords": "maktab xaritada belgilanmagan",
    "reason_ended": "dars allaqachon tugagan",
    "reason_after_reject": "dars avval hisoblanmagan",
    "reason_no_video": "video yoʻq",

    # Darslarim va soatlarim
    "today_header": "📅 Bugun, {date}:",
    "today_empty": "Bugun jadval boʻyicha dars yoʻq.",
    "today_line": "• {start}–{end} {school}, {cls} — {status}",
    "status_planned": "oldinda",
    "status_checked_in": "belgilangan",
    "status_late": "{late} daqiqa kechikish",
    "status_no_checkin": "belgi yoʻq",
    "status_needs_review": "tekshiruvda",
    "status_approved": "hisoblandi",
    "status_rejected": "hisoblanmadi",
    "status_cancelled": "bekor qilingan",
    "month": "⏱ {month}:\n\n"
             "Jadval boʻyicha darslar: {planned}\n"
             "Tasdiqlangan: {confirmed}\n"
             "Kechikishlar: {late}\n"
             "Qaror kutmoqda: {pending}\n"
             "Soatlar: {hours}",
    "unknown": "Tushunmadim. Quyidagi menyudan amalni tanlang.",

    # Eslatma va administrator
    "late_reminder": "⏰ Soat {time} dagi dars ({school}, {cls}) boshlandi, lekin belgi yoʻq. "
                     "Agar joyida boʻlsangiz — «📍 Darsga keldim» tugmasini bosing va dumaloq video yozing.",
    "admin_bound": "Telegram bogʻlandi, {name}. Siz davomat administratorisiz: bu botni yopiq «Отметки» "
                   "guruhiga qoʻshing va u yerda /setchat buyrugʻini yuboring. U yerga belgilar va "
                   "tugmali dars kartochkalari keladi.",
}
