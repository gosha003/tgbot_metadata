"""Russian translations of the bot's user-facing strings.

Keys are the English source strings passed to tr() / N_(); values are their
Russian versions. Placeholders (%s, %d, {name}) must match in order.
"""

RU = {
    "\n\nSuch a file can be cleaned by regeneration through LibreOffice, but it was not found on this machine. Install LibreOffice and send the file again.":
        "\n\nПочистить такой файл можно пересозданием через LibreOffice, но на этой машине он не найден. Установите LibreOffice и пришлите файл снова.",
    "\n   🔒 cannot be removed — %s":
        "\n   🔒 убрать нельзя — %s",
    "\nThe detailed report below judges the remainder by the critical-findings counter; the result above is by values: the remainder consists of new values, not original ones.\n":
        "\nПодробный отчёт ниже оценивает остаток по счётчику критичных находок; итог выше -- по значениям: остаток состоит из новых значений, не из исходных.\n",
    "\n🔴 Directly reveal a person or machine: <b>%d</b>":
        "\n🔴 Прямо выдают личность или машину: <b>%d</b>",
    "    cleaning traces: %s":
        "    следы чистки: %s",
    "    in the archive as: ":
        "    в архиве как: ",
    "    reason: ":
        "    причина: ",
    "  ! %s (%s) -> found: %s":
        "  ! %s (%s) -> найдено: %s",
    "  ! report section failed to render: %r":
        "  ! секция отчёта не собралась: %r",
    "  ... and %d more":
        "  ... и ещё %d",
    "  of those not cleaned: refused %d, format not supported %d, nested archives %d, cleanliness not proven %d, rejected by safeguards %d.":
        "  из не очищенных: отказов %d, формат не поддержан %d, вложенных архивов %d, чистота не доказана %d, отклонено защитой %d.",
    " (for example, the regenerated trailer /ID)":
        " (например, перегенерированный trailer /ID)",
    " (planned: phase %d)":
        " (в плане: фаза %d)",
    " -- Chrome milestone %s":
        " -- майлстоун Chrome %s",
    " Also, the stealth profile doesn't blank everything: empty metadata gives cleaning away by itself.":
        " Профиль stealth к тому же не обнуляет всё подряд: пустые метаданные сами выдают чистку.",
    " Cleaning leaves these fields alone.":
        " Чистка эти поля не трогает.",
    " In the source, created and modified match to the second: that's a property of the file itself; cleaning didn't create it.":
        " В исходнике created и modified совпадают до секунды: это свойство самого файла, чистка его не создавала.",
    " Meanwhile app.xml claims Application=%s.":
        " При этом app.xml заявляет Application=%s.",
    " Output file not created.":
        " Выходной файл не создан.",
    " The critical findings the inspector still sees are new values%s: they are not related to the originals.":
        " Критичное, что инспектор ещё видит, -- новые значения%s: с исходными они не связаны.",
    " The element is missing from core.xml.":
        " Элемент в core.xml отсутствует.",
    " You asked to keep: %s.":
        " Вы просили сохранить: %s.",
    "! cannot be removed: %s":
        "! убрать нельзя: %s",
    "\"%s\", %d bytes":
        "«%s», %d байт",
    "%%EOF / startxref markers":
        "Маркеров %%EOF / startxref",
    "%.1f KB":
        "%.1f КБ",
    "%.1f MB":
        "%.1f МБ",
    "%.1f x %.1f mm%s":
        "%.1f x %.1f мм%s",
    "%04d-%02d-%02d %02d:%02d:%02d … %04d-%02d-%02d %02d:%02d:%02d (%d of %d entries)":
        "%04d-%02d-%02d %02d:%02d:%02d … %04d-%02d-%02d %02d:%02d:%02d (%d из %d записей)",
    "%d (the only tag of the minimal EXIF)":
        "%d (единственный тег минимального EXIF)",
    "%d B":
        "%d Б",
    "%d MB":
        "%d МБ",
    "%d OLE object(s) embedded in the RTF: inside each one is a separate container with its own metadata.":
        "В RTF встроено %d OLE-объект(ов): внутри каждого отдельный контейнер со своими метаданными.",
    "%d authors":
        "%d авторов",
    "%d bytes":
        "%d байт",
    "%d bytes of settings":
        "%d байт настроек",
    "%d bytes%s":
        "%d байт%s",
    "%d bytes%s, starts with: %s":
        "%d байт%s, начало: %s",
    "%d bytes, byte for byte":
        "%d байт, байт в байт",
    "%d bytes, markers: %s":
        "%d байт, метки: %s",
    "%d bytes, part %d of %d%s":
        "%d байт, часть %d из %d%s",
    "%d bytes, starts with: %s":
        "%d байт, начало: %s",
    "%d bytes: %s":
        "%d байт: %s",
    "%d change region(s)":
        "%d область(ей) правок",
    "%d custom properties, parsing the first %d":
        "пользовательских свойств %d, разбираем первые %d",
    "%d entry(ies)":
        "%d запись(ей)",
    "%d file(s): %s":
        "%d файл(ов): %s",
    "%d h":
        "%d ч",
    "%d identifier(s): %s":
        "%d идентификатор(ов): %s",
    "%d in total, %d of them in the trailer":
        "%d всего, из них в трейлере: %d",
    "%d min":
        "%d мин",
    "%d object(s) in the RTF are linked to an external file (\\objlink) rather than embedded: the document reaches outside itself.":
        "В RTF %d объект(ов) связаны с внешним файлом (\\objlink), а не внедрены: документ обращается за пределы себя самого.",
    "%d object(s): %s":
        "%d объект(ов): %s",
    "%d of %d entries":
        "%d из %d записей",
    "%d of %d entries with real times":
        "%d из %d записей с реальным временем",
    "%d pages, the first %d were parsed page by page.":
        "Страниц %d, постранично разобраны первые %d.",
    "%d part(s): %s":
        "%d часть(ей): %s",
    "%d parts":
        "%d частей",
    "%d parts, %d bytes":
        "%d част., %d байт",
    "%d pcs":
        "%d шт",
    "%d pcs%s":
        "%d шт.%s",
    "%d pcs, %s … %s":
        "%d шт., %s … %s",
    "%d properties":
        "%d свойств(а)",
    "%d revisions":
        "%d ревизий",
    "%d revisions (by %%%%EOF), /Prev in the trailer: %d":
        "ревизий (по %%%%EOF): %d, /Prev в трейлере: %d",
    "%d s":
        "%d с",
    "%d segm., %d bytes":
        "%d сегм., %d байт",
    "%d tables: %s":
        "%d шт: %s",
    "%d tag(s)":
        "%d тег(ов)",
    "%d time(s). Revisions (w:ins/w:del) and comments stay: they're the user's content; only the authorship was removed. \"Author\" is what Word itself writes with \"Remove personal information on save\" enabled.":
        "%d раз(а). Правки (w:ins/w:del) и комментарии остаются: это контент пользователя, убрано только авторство. «Author» -- то, что пишет сам Word при включённой «Удалять личные данные при сохранении».",
    "%d total: %s":
        "%d шт.: %s",
    "%d x %d, units %d":
        "%d x %d, единицы %d",
    "%dx%d, %d frames":
        "%dx%d, кадров %d",
    "%s   (original: %s)":
        "%s   (оригинал: %s)",
    "%s \"%s\"":
        "%s «%s»",
    "%s (\"%s\")":
        "%s («%s»)",
    "%s (%d B)":
        "%s (%d Б)",
    "%s (%d B)%s":
        "%s (%d Б)%s",
    "%s (%d edits)":
        "%s (%d правок)",
    "%s (%s bytes)":
        "%s (%s байт)",
    "%s (active: %s)":
        "%s (активен %s)",
    "%s (total %d s)":
        "%s (всего %d с)",
    "%s /PieceInfo can't be read: %s":
        "%s /PieceInfo не читается: %s",
    "%s Critical (identity, environment): %s":
        "%s Критичные (личность, окружение): %s",
    "%s Keep: %s":
        "%s Сохранить: %s",
    "%s Needing cleaning (incl. provenance): %s":
        "%s Требующие чистки (с провенансом): %s",
    "%s can't be read: %s":
        "%s не читается: %s",
    "%s changed during saving (the cleaning itself would become a fingerprint)":
        "%s изменился при сохранении (чистка сама стала бы отпечатком)",
    "%s contains no parsable XML":
        "%s не содержит разбираемого XML",
    "%s could not be parsed: %r":
        "%s не разобрался: %r",
    "%s could not be read: %r":
        "%s не прочиталось: %r",
    "%s d":
        "%s дн",
    "%s h":
        "%s ч",
    "%s is missing -- the container does not conform to the ODF specification":
        "нет %s -- контейнер не соответствует спецификации ODF",
    "%s min":
        "%s мин",
    "%s mo":
        "%s мес",
    "%s report section failed to render (%s)":
        "%s секция отчёта не собралась (%s)",
    "%s s":
        "%s с",
    "%s y":
        "%s г",
    "%s — password hash":
        "%s — хеш пароля",
    "%s — when it was refreshed":
        "%s — когда обновляли",
    "%s … %s (%d of %d entries)":
        "%s … %s (%d из %d записей)",
    "%s … %s (%d total)":
        "%s … %s (%d шт.)",
    "%s, %d bytes":
        "%s, %d байт",
    "%s, metadata not extracted, %s":
        "%s, метаданные не извлечены, %s",
    "%s, no metadata found":
        "%s, метаданных не обнаружено",
    "%s, profile %s: %s":
        "%s, профиль %s: %s",
    "%s: %s -- %d more similar tag parsing errors suppressed":
        "%s: %s -- ещё %d однотипных ошибок разбора тегов подавлено",
    "%s: %s declares %d tags, parsing the first %d":
        "%s: %s объявляет %d тегов, разбираем первые %d",
    "%s: %s is truncated":
        "%s: %s обрезан",
    "%s: %s points outside the block (offset %d)":
        "%s: %s указывает за пределы блока (смещение %d)",
    "%s: %s points to an IFD that was already parsed (looped EXIF)":
        "%s: %s ссылается на уже разобранный IFD (зацикленный EXIF)",
    "%s: %s tag 0x%04X declares %d bytes, skipped":
        "%s: %s тег 0x%04X объявляет %d байт, пропущен",
    "%s: %s tag 0x%04X points outside the block":
        "%s: %s тег 0x%04X указывает за пределы блока",
    "%s: %s truncated at tag %d":
        "%s: %s обрезан на теге %d",
    "%s: BigTIFF is not supported, only the header was parsed":
        "%s: BigTIFF не поддерживается, разобран только заголовок",
    "%s: EXIF is shorter than a TIFF header (%d bytes)":
        "%s: EXIF короче заголовка TIFF (%d байт)",
    "%s: IPTC record 2:%d is truncated":
        "%s: запись IPTC 2:%d обрезана",
    "%s: TIFF header is truncated":
        "%s: обрезан заголовок TIFF",
    "%s: box %s declares %d bytes, %d available":
        "%s: бокс %s объявляет %d байт, доступно %d",
    "%s: chunk %s is truncated":
        "%s: чанк %s обрезан",
    "%s: corrupt zlib stream (%s)":
        "%s: битый zlib-поток (%s)",
    "%s: declared %d bytes, decoded %d":
        "%s: объявлено %d байт, декодировалось %d",
    "%s: hex profile cannot be decoded (%s)":
        "%s: hex-профиль не декодируется (%s)",
    "%s: hex profile declares %d bytes, skipped":
        "%s: hex-профиль объявляет %d байт, пропущен",
    "%s: inflation cut off at the limit of %d bytes":
        "%s: распаковка оборвана на пределе %d байт",
    "%s: invalid TIFF signature (%d)":
        "%s: неверная сигнатура TIFF (%d)",
    "%s: largesize box %s is truncated":
        "%s: largesize-бокс %s обрезан",
    "%s: marker %s not found, the file is truncated":
        "%s: не найден маркер %s, файл обрезан",
    "%s: parsing aborted: %r":
        "%s: разбор прерван: %r",
    "%s: parsing stopped (%s), revisions beyond this limit did not make it into the report":
        "%s: разбор остановлен (%s), правки за этим пределом в отчёт не попали",
    "%s: reached the limit of %d IFDs, walk stopped":
        "%s: достигнут предел в %d IFD, обход остановлен",
    "%s: recursive inspection of the attachment crashed: %s: %s":
        "%s: рекурсивная инспекция вложения упала: %s: %s",
    "%s: resource 0x%04X declares %d bytes, truncated":
        "%s: ресурс 0x%04X объявляет %d байт, обрезан",
    "%s: stream too large (%d compressed bytes), not decompressed.":
        "%s: поток слишком велик (%d байт сжатых), не распаковываем.",
    "%s: suspicious compression ratio %dx":
        "%s: подозрительный коэффициент сжатия %dx",
    "%s: the stream expands beyond %d MB or its size can't be bounded, not decompressed.":
        "%s: при распаковке поток превысит %d МБ или его размер не удаётся ограничить, не распаковываем.",
    "%s: unknown byte order %r":
        "%s: неизвестный порядок байтов %r",
    "(VT type 0x%02X, cannot be parsed)":
        "(тип VT 0x%02X, не разбирается)",
    "(empty)":
        "(пусто)",
    "(entry could not be displayed)":
        "(запись не отобразилась)",
    "(error text could not be read)":
        "(текст ошибки не прочитался)",
    "(untitled)":
        "(без названия)",
    "(value could not be parsed)":
        "(значение не разобралось)",
    ", altitude %.1f m":
        ", высота %.1f м",
    ", classes: ":
        ", классы: ",
    ", description: ":
        ", описание: ",
    ", vendor ":
        ", вендор ",
    "- %s (confidence: %s)":
        "- %s (уверенность: %s)",
    ". The source has no SOF: the image didn't decode before cleaning either; dimensions weren't checked":
        ". В исходнике нет SOF: изображение не декодировалось и до чистки, размеры не проверялись",
    "/ByteRange (\"%s\")":
        "/ByteRange («%s»)",
    "/Encrypt parameters can't be read: %s":
        "Параметры /Encrypt не читаются: %s",
    "/Encrypt permissions can't be read: %s":
        "Права /Encrypt не читаются: %s",
    "/Fields can't be read: %s":
        "/Fields не читается: %s",
    "/ID is still in the trailer":
        "/ID остался в трейлере",
    "/ID not regenerated: %s: %s":
        "/ID не перегенерирован: %s: %s",
    "/ID[0] (permanent, set at creation)":
        "/ID[0] (постоянный, задаётся при создании)",
    "/ID[1] (changes on every save)":
        "/ID[1] (меняется при каждом сохранении)",
    "/Info %s can't be read: %s":
        "/Info %s не читается: %s",
    "/Info can't be read: %s":
        "/Info не читается: %s",
    "/Info is empty but XMP is in place: only the /Info dictionary was cleaned and XMP was forgotten; everything is still there":
        "/Info пуст, а XMP на месте: чистили только словарь /Info, а XMP забыли -- там всё осталось",
    "/Info is filled in but XMP is missing: either a very old producer, or XMP was cut out separately":
        "/Info заполнен, а XMP отсутствует: либо очень старый продюсер, либо XMP вырезали отдельно",
    "/MarkInfo (tagging declaration)":
        "/MarkInfo (пометка о тегировании)",
    "/MediaBox of the first page can't be read: %s":
        "/MediaBox первой страницы не читается: %s",
    "/OCProperties /D /OFF can't be read: %s":
        "/OCProperties /D /OFF не читается: %s",
    "/OCProperties /OCGs can't be read: %s":
        "/OCProperties /OCGs не читается: %s",
    "/Prev is a regular outline-item key (a link to the previous bookmark), so its occurrences across the file say NOTHING about revisions. Only /Prev inside the trailer does.":
        "/Prev -- штатный ключ элемента оглавления (ссылка на предыдущую закладку), поэтому его вхождения по всему файлу о ревизиях НЕ говорят. О ревизиях говорит только /Prev внутри трейлера.",
    "/Prev is still in the trailer: earlier revisions were not dropped":
        "в трейлере остался /Prev: прошлые ревизии не сброшены",
    "/Prev occurrences":
        "Вхождений /Prev",
    "/URI link":
        "Ссылка /URI",
    "/lang switches the language of the bot's replies (Русский / English).":
        "/lang переключает язык ответов бота (Русский / English).",
    "0 (file never edited or counter reset)":
        "0 (файл не редактировали или счётчик сброшен)",
    "0Table/1Table missing":
        "0Table/1Table отсутствует",
    "12 bytes":
        "12 байт",
    ": re-inspection of the output":
        ": повторная инспекция результата",
    "; archive comment %d bytes":
        "; комментарий архива %d байт",
    "; the animation loop extension was kept (STRUCTURAL): without it the animation plays once":
        "; расширение зацикливания анимации оставлено (STRUCTURAL): без него анимация проиграется один раз",
    "<%d bytes: %s>":
        "<%d байт: %s>",
    "<%d bytes>":
        "<%d байт>",
    "<b>Bot status</b>":
        "<b>Состояние бота</b>",
    "<compressed, %d bytes; not decompressed: bomb protection>":
        "<сжато, %d байт; не распаковываем: защита от бомб>",
    "<compressed, %d bytes>":
        "<сжато, %d байт>",
    "<empty>":
        "<пусто>",
    "<i>(empty)</i>":
        "<i>(пусто)</i>",
    "<i>Values are hidden; tap to reveal. But the report text has already reached Telegram's servers and stays in the chat history: if the findings are sensitive, delete the conversation and use the CLI instead.</i>":
        "<i>Значения скрыты — нажмите, чтобы показать. Но текст отчёта уже ушёл на серверы Telegram и лежит в истории чата: если находки чувствительные, удалите переписку, а разбор делайте через CLI.</i>",
    "<not parsed>":
        "<не разобрано>",
    "<not read>":
        "<не прочитано>",
    "<path hidden>":
        "<путь скрыт>",
    "<pre>%s</pre>\nDetails for each file -- in the document below.":
        "<pre>%s</pre>\nПодробно по каждому файлу -- документом ниже.",
    "<stream not decompressed: it may expand beyond the limit>":
        "<поток не распаковываем: он может раздуться сверх лимита>",
    "<stream too large to decompress: %d compressed bytes>":
        "<поток слишком велик для распаковки: %d байт сжатых>",
    "<unparsed>":
        "<не разобрано>",
    "<unreadable value>":
        "<нечитаемое значение>",
    "> %d MB":
        "> %d МБ",
    "A .doc (OLE2) file cannot be cleaned in place. Editing the document properties removes neither the revision author table, nor the deleted text of earlier versions, nor foreign fragments in the free sectors of the container. The only reliable route is to re-save the content in a modern format (.docx) or export it to PDF, and clean that instead.":
        "Файл .doc (OLE2) нельзя чистить на месте. Правка свойств документа не убирает ни таблицу авторов правок, ни удалённый текст предыдущих версий, ни чужие фрагменты в свободных секторах контейнера. Единственный надёжный путь -- пересохранить содержимое в современный формат (.docx) или отдать PDF, и чистить уже его.",
    "A BOM reveals the editor or platform the file was saved with.":
        "BOM выдаёт редактор или платформу, которой сохраняли файл.",
    "A Microsoft Purview label: the label GUID, the time it was set, SetBy is a real email, SiteId is the tenant GUID. The tenant may notice the label was removed: that's the user's decision.":
        "Метка Microsoft Purview: GUID метки, время установки, SetBy -- реальный email, SiteId -- GUID тенанта. Снятие метки тенант может заметить: это решение пользователя.",
    "A PDF/A or PDF/UA conformance declaration. Usually kept when cleaning: without it the file stops being archival/accessible.":
        "Декларация соответствия PDF/A или PDF/UA. При чистке обычно сохраняем: без неё файл перестаёт быть архивным/доступным.",
    "A SharePoint/document management property or an Office service property: ties to a portal, library or correspondence.":
        "Свойство SharePoint/СЭД или служебное свойство Office: привязка к порталу, библиотеке, переписке.",
    "A VBA project. It holds the module source code, the project name and its path on the developer's machine, and often old commented-out code too.":
        "Проект VBA. Внутри лежат исходники модулей, имя проекта и путь к нему на машине разработчика, а нередко и закомментированный старый код.",
    "A Word service binary block: among other things it collects settings that the document properties don't reflect.":
        "Служебный двоичный блок Word: сюда складываются в том числе настройки, не отражённые в свойствах документа.",
    "A binding to a non-standard schema: reveals the SharePoint portal or the organization's content type.":
        "Привязка к нестандартной схеме: выдаёт портал SharePoint или тип контента организации.",
    "A bit mask: password, a 'read-only recommended' flag and the like.":
        "Битовая маска: пароль, рекомендация 'только чтение' и т.п.",
    "A block left by a specific program: the application name alone already tells what made the file":
        "Блок, оставленный конкретной программой: само имя приложения уже говорит, чем сделан файл",
    "A chunk the decoder doesn't need: an application's private data":
        "Чанк, который декодеру не нужен: приватные данные приложения",
    "A classic Excel leak: the name of the person who refreshed the cache lies outside docProps.":
        "Классическая утечка Excel: имя человека, обновившего кэш, лежит вне docProps.",
    "A classic Excel leak: the name of the person who refreshed the pivot table cache sits outside docProps and survives the cleaning of the document properties.":
        "Классическая утечка Excel: имя человека, обновившего кэш сводной таблицы, лежит вне docProps и переживает чистку свойств документа.",
    "A classic hiding place: decoders ignore the tail":
        "Классический тайник: декодеры хвост игнорируют",
    "A classic hiding place: decoders ignore the tail. It may hold MPF frames, Motion Photo video, an Ultra HDR gain map, an archive or someone else's document":
        "Классический тайник: декодеры хвост игнорируют. Могли быть кадры MPF, видео Motion Photo, gain map Ultra HDR, архив или чужой документ",
    "A comment author or discussion participant.":
        "Автор комментария или участник обсуждения.",
    "A comment in the document. It contains the author's name, the time and the text -- often the most candid piece in the whole file.":
        "Комментарий в документе. Содержит имя автора, время и текст -- нередко самый откровенный кусок во всём файле.",
    "A connection string to a data source.":
        "Строка подключения к источнику данных.",
    "A container of several images in one file: the second frame may lie inside in full, with its own metadata":
        "Контейнер нескольких изображений в одном файле: внутри может лежать второй кадр целиком, со своими метаданными",
    "A corporate certificate authority reveals the organization.":
        "Корпоративный удостоверяющий центр выдаёт организацию.",
    "A cryptographically signed provenance manifest (C2PA / Content Credentials) was removed. The removal itself is DETECTABLE: the recipient sees that a signature existed and was stripped; with a soft binding or a watermark, provenance can be recovered even without this block":
        "Криптографически подписанный манифест происхождения (C2PA / Content Credentials) удалён. Сам срез ДЕТЕКТИРУЕМ: получатель видит, что подпись была и её убрали; при наличии soft-binding или водяного знака происхождение восстанавливается и без этого блока",
    "A custom XML part of unknown origin (%s). This is how EDMS data and content-control bindings are stored; it cannot be removed blindly -- the document text may depend on it.":
        "Пользовательская XML-часть неизвестного происхождения (%s). Так хранят данные СЭД и привязку элементов содержимого; убрать её вслепую нельзя -- на неё может опираться текст документа.",
    "A custom XML part that could not be parsed. The content was not checked -- we assume the worst case.":
        "Пользовательская XML-часть, которую не удалось разобрать. Содержимое не проверено -- считаем худший случай.",
    "A date in PDF format D:YYYYMMDDHHmmSS+TZ. The time zone offset reveals the author's geography.":
        "Дата в формате PDF D:YYYYMMDDHHmmSS+TZ. Смещение часового пояса выдаёт географию автора.",
    "A decoder must understand a critical chunk: removing it would damage the file":
        "Критический чанк обязан понимать декодер: убрать его значит испортить файл",
    "A digital signature in the properties: the signer's identity.":
        "Цифровая подпись в свойствах: личность подписанта.",
    "A digital signature. Signature metadata is inside the signed range: any cleaning of the file breaks the signature. Cleaning and keeping the signature are mutually exclusive; the user has to choose.":
        "Цифровая подпись. Метаданные подписи входят в подписанный диапазон: любая чистка файла ломает подпись. Чистка и сохранение подписи -- взаимоисключающие операции, выбирать должен пользователь.",
    "A direct email address in an external relationship.":
        "Прямой email-адрес во внешнем отношении.",
    "A direct email in an external relationship. The link was removed; the visible text in the document stayed (it's content).":
        "Прямой email во внешнем отношении. Ссылка снята, видимый текст в документе остался (он -- содержимое).",
    "A document variable: macros, add-ins and document management systems put full names, numbers and paths here.":
        "Переменная документа: макросы, надстройки и СЭД кладут сюда ФИО, номера и пути.",
    "A duplicate of the comment in RTF/HTML: it stays even if /Contents was cleaned.":
        "Дубль комментария в RTF/HTML: остаётся, даже если /Contents почистили.",
    "A file system path right in the document body: reveals the user name, the disk layout and network shares. It gets here from hyperlinks, INCLUDEPICTURE fields and embedded objects.":
        "Путь файловой системы прямо в теле документа: выдаёт имя пользователя, структуру дисков и сетевые шары. Попадает сюда из гиперссылок, полей INCLUDEPICTURE и встроенных объектов.",
    "A form field can't be read: %s":
        "Поле формы не читается: %s",
    "A form field value. Fields visually \"cleared\" by erasing their appearance usually keep /V: the data stays in the file.":
        "Значение поля формы. Поля, визуально «очищенные» стиранием внешнего вида, обычно сохраняют /V -- данные остаются в файле.",
    "A free-form field filled in by hand or by a corporate template: full names, contract numbers, department codes and email addresses end up here.":
        "Произвольное поле, заполняемое вручную или корпоративным шаблоном: сюда попадают ФИО, номера договоров, коды подразделений, адреса почты.",
    "A free-form field filled in by hand or by an organization template: full names, contract numbers and internal department codes show up here.":
        "Произвольное поле, заполненное вручную или шаблоном организации: тут встречаются ФИО, номера договоров и внутренние коды подразделений.",
    "A free-form field filled in by hand or by an organization template: it can hold full names, emails in MSIP classification labels, contract numbers, internal department codes.":
        "Произвольное поле, заполненное вручную или шаблоном организации: тут встречаются ФИО, email в метках классификации MSIP, номера договоров, внутренние коды подразделений.",
    "A full snapshot of the print settings: driver, tray, paper format.":
        "Полный слепок настроек печати: драйвер, лоток, формат.",
    "A hidden sheet is workbook content, not metadata: left alone.":
        "Скрытый лист -- содержимое книги, не метаданные: не трогаем.",
    "A key outside the PDF specification: a specific application or corporate document management system writes it, so it identifies the file's source especially well.":
        "Ключ вне спецификации PDF: его пишет конкретное приложение или корпоративная система документооборота, поэтому он особенно хорошо опознаёт источник файла.",
    "A link to an external workbook with a local path: only the workbook name was kept.":
        "Ссылка на внешнюю книгу с локальным путём: оставлено только имя книги.",
    "A name from office:change-info. It stays in the file even after the changes are accepted, unless the change regions are purged.":
        "Имя из office:change-info. Остаётся в файле, даже когда сами правки уже приняты, если области изменений не вычищены.",
    "A name from the revision author table. It stays in the file even after the changes themselves are accepted: editing the document properties doesn't touch this table.":
        "Имя из таблицы авторов правок. Остаётся в файле и после того, как сами исправления приняты: правка свойств документа эту таблицу не трогает.",
    "A name from the revision table. The service entry Unknown stays: Word always writes it, and an empty table is more conspicuous.":
        "Имя из таблицы правок. Служебная запись Unknown остаётся: Word пишет её всегда, и пустая таблица заметнее.",
    "A named layer. Layer names often reveal internal version names, clients and drafts (\"Draft\", \"Client A\", \"Lawyer's edits\").":
        "Именованный слой. Имена слоёв часто выдают внутренние названия версий, заказчиков и черновиков («Draft», «Клиент А», «Правки юриста»).",
    "A named range refers to an external workbook — often with a full local path.":
        "Именованный диапазон ссылается на внешнюю книгу — нередко с полным локальным путём.",
    "A page thumbnail goes stale after an edit and shows a previous version of the page.":
        "Миниатюра страницы устаревает при правке и показывает прошлую версию страницы.",
    "A path in the body or in a field. After decoding it's still a path, not a fragment of a control word.":
        "Путь в теле или в поле. После декодирования это всё ещё путь, а не огрызок управляющего слова.",
    "A persistent GUID links all versions and all files made from one template.":
        "Постоянный GUID связывает все версии и все файлы одного шаблона.",
    "A persistent document GUID. It does NOT change on re-save and links all versions of the file and all files made from one template: through it two different-looking documents are tied together.":
        "Постоянный GUID документа. Он НЕ меняется при пересохранении и связывает между собой все версии файла и все файлы, сделанные из одного шаблона: по нему два разных на вид документа сводятся в один.",
    "A portfolio schema is meaningless without the attachments.":
        "Схема портфолио без вложений теряет смысл.",
    "A prepared but unsigned field: there's no signature yet, nothing to break; it can be removed like a regular form field.":
        "Подготовленное, но не подписанное поле: подписи ещё нет, ломать нечего, можно убирать как обычное поле формы.",
    "A public OOXML schema address. The same for all documents, says nothing about the organization.":
        "Публичный адрес схемы OOXML. Одинаков у всех документов и об организации не говорит ничего.",
    "A ready-made point on the map: shooting address, home, office. The most dangerous finding in an image -- it leads to a person, not to a file":
        "Готовая точка на карте: адрес съёмки, дом, офис. Самая опасная находка в картинке -- по ней находят человека, а не файл",
    "A reference to the template, almost always a local path.":
        "Ссылка на шаблон, почти всегда локальный путь.",
    "A required schema attribute: it can't be removed without damaging the part, and substituting it would forge provenance.":
        "Обязательный атрибут схемы: убрать нельзя без порчи части, подменять нельзя -- это фальсификация провенанса.",
    "A reserved or private marker: the decoder doesn't need it":
        "Резервный или приватный маркер: декодеру не нужен",
    "A specific application's private chunk with arbitrary content":
        "Приватный чанк конкретного приложения, содержимое произвольное",
    "A specific program's block: the application name alone says what made the file":
        "Блок конкретной программы: имя приложения само говорит, чем сделан файл",
    "A trailer without /ID: either the file was built by hand, or it was already rewritten by a tool that didn't restore /ID.":
        "Трейлер без /ID: либо файл собран вручную, либо его уже переписывали инструментом, который /ID не восстановил.",
    "A4 (ISO, everywhere except North America)":
        "A4 (ISO, весь мир кроме Северной Америки)",
    "APNG: control":
        "APNG: управление",
    "APNG: data":
        "APNG: данные",
    "APNG: frame":
        "APNG: кадр",
    "APP1 segment with a foreign signature: something appended it by hand":
        "Сегмент APP1 с чужой сигнатурой: чем-то дописан вручную",
    "ASSESSMENT: the file looks built by a program rather than saved by an office application. For many purposes this is worse than a cleaning trace.":
        "ОЦЕНКА: файл выглядит собранным программой, а не сохранённым в офисном приложении. Для многих задач это хуже следа чистки.",
    "ASSESSMENT: the file looks scrubbed. No original values leak, but the fact of processing is visible.":
        "ОЦЕНКА: файл выглядит чищеным. Утечки исходных значений нет, но сам факт обработки виден.",
    "ASSESSMENT: the inspector sees no signs of processing. This is not a guarantee: it only sees metadata, not writing style or watermarks.":
        "ОЦЕНКА: инспектор не видит признаков обработки. Это не гарантия: он видит только метаданные, не стиль текста и не водяные знаки.",
    "ATTACHMENT REMOVED: a whole source document may have been inside.":
        "ВЛОЖЕНИЕ УДАЛЕНО: внутри мог лежать исходный документ целиком.",
    "ATTACHMENTS REMOVED: a whole source document may have been inside.":
        "ВЛОЖЕНИЯ УДАЛЕНЫ: внутри мог лежать исходный документ целиком.",
    "Absolute path":
        "Абсолютный путь",
    "Access to the bot is not configured yet, so it serves no one.\n\nYour Telegram user_id: <code>%s</code>\n\nIf this is your bot, add this line to the <code>.env</code> file next to <code>bot.py</code>:\n<code>TG_OWNER_ID=%s</code>\nand restart the bot.":
        "Доступ к боту ещё не настроен, поэтому он никого не обслуживает.\n\nВаш Telegram user_id: <code>%s</code>\n\nЕсли это ваш бот — добавьте в файл <code>.env</code> рядом с <code>bot.py</code> строку\n<code>TG_OWNER_ID=%s</code>\nи перезапустите бота.",
    "Access to this bot is restricted. Your user_id: <code>%s</code>":
        "Доступ к этому боту ограничен. Ваш user_id: <code>%s</code>",
    "Accessibility tags (tagged PDF): without them a screen reader cannot read the document.":
        "Теги доступности (тегированный PDF): без них скринридер не читает документ.",
    "Accessibility tags: without them a screen reader can't read the document. Kept when cleaning.":
        "Теги доступности: без них скринридер не читает документ. При чистке сохраняем.",
    "Account provider":
        "Провайдер учётной записи",
    "Action %s":
        "Действие %s",
    "Action entries (%d) are not shown: there is no cleaned file, and presenting them as done would be untrue.":
        "Записи о действиях (%d) не показаны: почищенного файла нет, и выдавать их за сделанное было бы неправдой.",
    "Actions: ":
        "Действия: ",
    "Active content":
        "Активное содержимое",
    "Active content in an embedded control.":
        "Активное содержимое во встроенном элементе управления.",
    "Active content in the PDF: %d object(s) (JS/OpenAction/AA/Launch)":
        "Активное содержимое в PDF: %d объект(ов) (JS/OpenAction/AA/Launch)",
    "Active or external action: launching a program, a script, sending data or a link to a local path.":
        "Активное или внешнее действие: запуск программы, скрипт, отправка данных или ссылка на локальный путь.",
    "ActiveX control":
        "ActiveX-элемент",
    "Added by exiftool (optional enrichment).":
        "Дополнено exiftool (необязательное обогащение).",
    "Additional document actions":
        "Дополнительные действия документа",
    "Additionally found by exiftool (optional enrichment).":
        "Дополнительно найдено exiftool (необязательное обогащение).",
    "Adobe marker":
        "Маркер Adobe",
    "Adobe marker (transform=%d)":
        "Маркер Adobe (transform=%d)",
    "After cleaning no property is left in the part: %s":
        "После чистки в части не осталось ни одного свойства: %s",
    "After conversion the format isn't supported by the second pass: document properties may remain.":
        "После конвертации формат не поддержан дочисткой: свойства документа могли остаться.",
    "All timestamps are zeroed to 1980-01-01. That is atypical for ODF: LibreOffice writes the real time. The file was most likely rebuilt by a program or already cleaned.":
        "Все отметки времени обнулены до 1980-01-01. Для ODF это нетипично: LibreOffice пишет реальное время. Файл, скорее всего, пересобран программой или уже чистили.",
    "Allowed operations":
        "Разрешённые операции",
    "Already empty.":
        "Уже пусто.",
    "Already empty. ":
        "Уже пусто. ",
    "An ODBC/OLEDB connection string or a query: contains the server address, the database name and the login.":
        "Строка подключения ODBC/OLEDB или запрос: содержит адрес сервера, имя базы и логин.",
    "An XMP date. xmp:MetadataDate is often newer than xmp:ModifyDate: the difference shows the metadata was touched separately from the content.":
        "Дата XMP. xmp:MetadataDate часто новее xmp:ModifyDate -- разница показывает, что метаданные трогали отдельно от содержимого.",
    "An absolute path or UNC reveals the user name, the drive and the organization's server.":
        "Абсолютный путь или UNC выдаёт имя пользователя, диск и сервер организации.",
    "An absolute path or file:// URI in the document settings: reveals the user name, the disk layout and network shares.":
        "Абсолютный путь или file:// URI в настройках документа: выдаёт имя пользователя, структуру дисков и сетевые шары.",
    "An absolute path/UNC reveals the user name, the drive or the organization's server.":
        "Абсолютный путь/UNC выдаёт имя пользователя, диск или сервер организации.",
    "An active action in an annotation: submitting data or executing code.":
        "Активное действие в аннотации: отправка данных или исполнение кода.",
    "An address in the document properties.":
        "Адрес в свойствах документа.",
    "An application wrote its own block into the file. The content is arbitrary":
        "Приложение записало в файл свой блок. Содержимое произвольное",
    "An application wrote its own block of arbitrary form":
        "Приложение записало свой блок произвольного вида",
    "An arbitrary number set by the application.":
        "Произвольный номер, проставленный приложением.",
    "An embedded object has ITS OWN docProps (author, company, path). A recursive inspection by the OLE inspector is needed.":
        "У вложенного объекта СВОИ docProps (автор, компания, путь). Нужна рекурсивная инспекция OLE-инспектором.",
    "An empty element is an ordinary state (Word writes it like that when no name is set).":
        "Пустой элемент -- рядовое состояние (Word пишет так при незаполненном имени).",
    "An external link. file:// and UNC paths reveal the internal network; tracking links reveal corporate systems.":
        "Внешняя ссылка. file:// и UNC-пути выдают внутреннюю сеть; трекинговые ссылки выдают корпоративные системы.",
    "An image of the first page. If the text was edited afterwards, the thumbnail may show the PREVIOUS version of the content.":
        "Картинка первой страницы. Если текст потом отредактировали, миниатюра может показывать ПРЕДЫДУЩУЮ версию содержимого.",
    "An index of several frames in one file; the frames themselves sit after EOI and go with the tail":
        "Индекс нескольких кадров в одном файле; сами кадры лежат за EOI и уходят вместе с хвостом",
    "An internal connection to the workbook's own data ($Workbook$): it has no external server. The query code lives in customXml (DataMashup), see the separate action.":
        "Внутреннее подключение к данным самой книги ($Workbook$): внешнего сервера в нём нет. Код запросов лежит в customXml (DataMashup), см. отдельное действие.",
    "An internal document ID from an EDMS, or a GUID.":
        "Внутренний ID документа в СЭД или GUID.",
    "An item with an arbitrary MIME type: most often this is an XMP packet":
        "Элемент с произвольным MIME: чаще всего это XMP-пакет",
    "An opaque block of settings of a specific driver. The format is undocumented and differs between drivers, so it is not fully parsed — but the print queue, port and print server usually sit right here.":
        "Непрозрачный блок настроек конкретного драйвера. Формат недокументирован и у разных драйверов разный, поэтому разобран он не полностью — но очередь печати, порт и сервер печати обычно лежат именно здесь.",
    "An xmpMM:History edit log event: the actual history of what the file was edited with, and when.":
        "Событие журнала правок xmpMM:History -- фактическая история того, чем и когда файл редактировали.",
    "Animated WebP: the frames may differ in content":
        "Анимированный WebP: кадры могут различаться содержимым",
    "Animation":
        "Анимация",
    "Animation looping":
        "Зацикливание анимации",
    "Annotation ":
        "Аннотация ",
    "Annotation author %s /T":
        "Автор аннотации %s /T",
    "Annotation by: %s":
        "Аннотация от: %s",
    "Annotation popup %s":
        "Popup-окно аннотации %s",
    "Annotation tag":
        "Метка аннотации",
    "AppVersion — application version":
        "AppVersion — версия приложения",
    "Application Extension is truncated":
        "Application Extension обрезан",
    "Application language (lid)":
        "Язык приложения (lid)",
    "Application private data":
        "Приватные данные приложения",
    "Application — application":
        "Application — приложение",
    "Application, dates, title and structural declarations: consistent with /Info.":
        "Приложение, даты, заголовок и структурные декларации: согласованы с /Info.",
    "Application=%s: a different application that may write real times itself; normalizing to 1980-01-01 would break the profile's consistency. The save time thus remains visible through the container.":
        "Application=%s: чужое приложение, оно могло писать реальное время само, приведение к 1980-01-01 нарушило бы согласованность профиля. Время сохранения при этом остаётся видимым через контейнер.",
    "Aptos Display is a Microsoft 365 typeface since 2023":
        "Aptos Display -- гарнитура Microsoft 365 с 2023 года",
    "Aptos is the Microsoft 365 default font since 2023: the document was created NO EARLIER than 2023, whatever the declared dates":
        "Aptos -- шрифт по умолчанию Microsoft 365 с 2023 года: документ создан НЕ РАНЬШЕ 2023-го, чем бы ни были заявленные даты",
    "Arbitrary organization fields. An ordinary file has no such group, so removing it entirely stands out in no way.":
        "Произвольные поля организации. Группы нет у обычного файла, поэтому удаление целиком ничем не выделяется.",
    "Associated files (PDF/A-3, invoices): a whole source xlsx/XML may be inside.":
        "Связанные файлы (PDF/A-3, счета): внутри может лежать исходный xlsx/XML целиком.",
    "Attachment %s can't be read: %s":
        "Вложение %s не читается: %s",
    "Attachment checksum":
        "Контрольная сумма вложения",
    "Attachment creation date":
        "Дата создания вложения",
    "Attachment description \"%s\"":
        "Описание вложения «%s»",
    "Attachment larger than %d MB: not loaded into memory; metadata inside remains.":
        "Вложение больше %d МБ: в память не берём, метаданные внутри остались.",
    "Attachment modification date":
        "Дата изменения вложения",
    "Attachment name. The attachment itself is a full document with its own metadata (PDF/A-3 and e-invoices carry the whole source XML/xlsx inside).":
        "Имя вложения. Само вложение -- полноценный документ со своими метаданными (у PDF/A-3 и электронных счетов внутри лежит исходный XML/xlsx целиком).",
    "Attachment size":
        "Размер вложения",
    "Author":
        "Автор",
    "Author email":
        "Email автора",
    "Author from Windows Explorer (UTF-16LE)":
        "Автор из проводника Windows (UTF-16LE)",
    "Author initials":
        "Инициалы автора",
    "Author initials (w:initials)":
        "Инициалы авторов (w:initials)",
    "Author name":
        "Имя автора",
    "Author of the shot, set in the camera or in an editor":
        "Автор кадра, прописан в камере или редакторе",
    "Auto-reload/link":
        "Автоперезагрузка/ссылка",
    "Auto-update of linked objects":
        "Автообновление связанных объектов",
    "BATCH CLEANING REPORT":
        "ОТЧЁТ О ПАКЕТНОЙ ЧИСТКЕ",
    "BIFF record walk aborted: %r":
        "обход записей BIFF прерван: %r",
    "BUT THE FILE IS NOW RECOGNIZABLE AS SCRUBBED. Nothing leaks, but cleaning introduced signs of processing (%s) that weren't in the original. If it matters that the cleaning itself stays invisible, this result is not enough.":
        "НО ФАЙЛ ТЕПЕРЬ ОПОЗНАЁТСЯ КАК ЧИЩЕНЫЙ. Утечки нет, однако после чистки появились признаки обработки (%s), которых в исходнике не было. Если вам важно, чтобы сам факт чистки не был виден, этого результата недостаточно.",
    "Backup":
        "Резервная копия",
    "Basic JPEG header. The density sometimes gives away a scanner or a specific converter":
        "Базовый заголовок JPEG. Плотность иногда выдаёт сканер или конкретный конвертер",
    "Basic code/scripts inside the document. They store the source code, the library name and often the path to the developer's machine.":
        "Код Basic/скрипты внутри документа. Хранит исходники, имя библиотеки и нередко путь к машине разработчика.",
    "Basic/ and Scripts/":
        "Basic/ и Scripts/",
    "Batch cleaning did not finish within {} min and was aborted. There is no archive with a result: do not send anything on as cleaned. Split the archive into parts and send again.":
        "Пакетная чистка не уложилась в {} мин и прервана. Архива с результатом нет: ничего не отправляйте как почищенное. Разбейте архив на части и пришлите снова.",
    "Bibliography":
        "Библиография",
    "Bibliography (customXml)":
        "Библиография (customXml)",
    "Bit depth of the source data: hints at the source (scanner, render)":
        "Глубина исходных данных: намекает на источник (сканер, рендер)",
    "Block map (%d)":
        "Карта блоков (%d)",
    "Body serial number: ties the file to one specific camera unit":
        "Серийный номер тушки: привязывает файл к конкретному экземпляру камеры",
    "Both identifiers match: the file was never re-saved after creation, or it was built in a single pass (typical of generators).":
        "Оба идентификатора совпадают: файл ни разу не пересохраняли после создания, либо его собрали одним проходом (типично для генераторов).",
    "Broken reference to XMP.":
        "Битая ссылка на XMP.",
    "Button expired: more than 30 minutes have passed or the bot was restarted. Send the file again.":
        "Кнопка устарела: прошло больше 30 минут или бот перезапускался. Пришлите файл ещё раз.",
    "Byte order mark":
        "Метка порядка байт",
    "Byte range covered by the signature. Nothing inside it can be changed. ":
        "Диапазон байт, накрытый подписью. Всё, что внутри него, изменить нельзя. ",
    "C2PA / Content Credentials manifest":
        "Манифест C2PA / Content Credentials",
    "CANNOT be removed without re-encoding with quality loss. At the same time the tables fingerprint the encoder and the quality setting: they reveal what re-saved the file and at what quality":
        "Убрать НЕЛЬЗЯ без перекодирования с потерей качества. При этом таблицы фингерпринтят энкодер и настройку качества: по ним определяют, чем и с каким качеством пересохраняли файл",
    "CLEAN: no original value survived cleaning (critical %s).":
        "ЧИСТО: ни одно исходное значение не пережило чистку (критичных %s).",
    "CLEAN: re-inspection of the output found no critical findings (%s).":
        "ЧИСТО: повторная инспекция результата не нашла критичных находок (%s).",
    "CLEANED":
        "ОЧИЩЕН",
    "CLEANED: no original value found in the output":
        "ПОЧИЩЕНО: ни одно исходное значение не найдено в результате",
    "CLEANLINESS NOT PROVEN: no original values found, but the check could not be completed (%s)":
        "ЧИСТОТА НЕ ДОКАЗАНА: исходных значений не найдено, но проверку провести полностью не удалось (%s)",
    "CLEANLINESS NOT PROVEN: reason unknown":
        "ЧИСТОТА НЕ ДОКАЗАНА: причина не определена",
    "COULD NOT PARSE — %d":
        "НЕ УДАЛОСЬ РАЗОБРАТЬ — %d",
    "CRITICAL FINDINGS LEFT: %d (%s). The action list explains them via the entries below; the file is not declared clean.":
        "ОСТАЛОСЬ КРИТИЧНЫХ НАХОДОК: %d (%s). По списку действий они объясняются записями ниже; файл не объявляется чистым.",
    "CRLF (typical of Windows)":
        "CRLF (типично для Windows)",
    "CRLF almost always means the file was edited or built on Windows.":
        "CRLF почти всегда означает, что файл правили или собирали на Windows.",
    "CRYPTOGRAPHICALLY SIGNED provenance manifest (C2PA / Content Credentials). Removing it is itself detectable: the recipient sees that a signature existed and was stripped. Moreover, with a soft binding or a watermark the provenance can be recovered from a registry even without this block in the file":
        "КРИПТОГРАФИЧЕСКИ ПОДПИСАННЫЙ манифест происхождения (C2PA / Content Credentials). Его удаление само по себе детектируемо: получатель видит, что подпись была и её срезали. Более того, при наличии soft-binding или водяного знака происхождение восстанавливается по реестру и без этого блока в файле",
    "Cache source (%s)":
        "Источник кэша (%s)",
    "Calibri is the Office 2007-2023 default font: the document comes from that time window":
        "Calibri -- шрифт по умолчанию Office 2007-2023: документ из этого окна времени",
    "Camera manufacturer":
        "Производитель камеры",
    "Camera model according to DNG":
        "Модель камеры по DNG",
    "Camera model: narrows down the circle of possible owners":
        "Модель камеры: сужает круг владельцев",
    "Camera serial number according to DNG":
        "Серийный номер камеры по DNG",
    "Can't be removed without lossy re-encoding. The tables fingerprint the encoder, but replacing them with ours would leave our library's fingerprint, which is worse than the original":
        "Убрать нельзя без перекодирования с потерей качества. Таблицы фингерпринтят энкодер, но замена их нашими дала бы отпечаток нашей библиотеки -- хуже исходного",
    "Category":
        "Категория",
    "Caveats: %s. Details in the sections below.":
        "Оговорки: %s. Подробности в разделах ниже.",
    "Chain of tools the file went through: the whole pipeline is visible":
        "Цепочка инструментов, через которые прошёл файл: видно весь пайплайн",
    "Change tracking is on: all subsequent edits will be saved together with the author's name.":
        "Режим записи правок включён: все последующие изменения будут сохраняться вместе с именем автора.",
    "Change tracking is turned on in settings.xml.":
        "В settings.xml включён режим записи исправлений.",
    "Change tracking mode. Text that looks deleted physically stays in the file, together with the name of whoever deleted it and the time of the edit.":
        "Режим записи исправлений. Текст, который выглядит удалённым, физически лежит в файле вместе с именем того, кто его удалил, и временем правки.",
    "Changed: %s":
        "Изменено: %s",
    "Changed: %s (no cleaned file)":
        "Изменено: %s (почищенного файла нет)",
    "Characters":
        "Символов",
    "Characters with spaces":
        "Символов с пробелами",
    "Characters — characters":
        "Characters — символов",
    "CharactersWithSpaces — characters with spaces":
        "CharactersWithSpaces — символов с пробелами",
    "Checking external links failed, regeneration cancelled: LibreOffice could have followed them over the network.":
        "Проверка внешних ссылок сорвалась, пересоздание отменено: LibreOffice мог бы пойти по ним в сеть.",
    "Choose the language of the bot's replies:":
        "Выберите язык ответов бота:",
    "Chromaticity":
        "Хроматичность",
    "Chunk map (%d)":
        "Карта чанков (%d)",
    "Clean (Paranoid)":
        "Почистить (Paranoid)",
    "Clean (Stealth)":
        "Почистить (Stealth)",
    "Clean all (Paranoid)":
        "Почистить всё (Paranoid)",
    "Clean all (Stealth)":
        "Почистить всё (Stealth)",
    "Cleaner message: %s":
        "Сообщение чистильщика: %s",
    "Cleaning did not finish within {}s and was aborted -- the file is too complex or damaged. There is no cleaned file: do not send anything on as cleaned. The original file is unchanged; to retry, send it again.":
        "Чистка не уложилась в {}с и прервана -- файл слишком сложный или повреждён. Почищенного файла нет: ничего не отправляйте как почищенное. Исходный файл не изменён; чтобы повторить, пришлите его ещё раз.",
    "Cleaning for this format is planned for phase %d.":
        "Чистка этого формата запланирована на фазу %d.",
    "Cleaning is a separate command: python -m core.clean <file>.":
        "Чистка -- отдельная команда: python -m core.clean <файл>.",
    "Cleaning the attachment crashed (%s): metadata inside remains.":
        "Чистка вложения упала (%s): метаданные внутри остались.",
    "Cleaning these fields failed: assume they are still in the file.":
        "Чистка этих полей не удалась: считайте, что они остались в файле.",
    "Cleaning…":
        "Чищу…",
    "Clipboard format":
        "Формат буфера обмена",
    "Clipping path name":
        "Имя clipping path",
    "Code page":
        "Кодовая страница",
    "Color profile (/OutputIntents, ICC)":
        "Цветовой профиль (/OutputIntents, ICC)",
    "Color profile hidden in a text chunk":
        "Цветовой профиль, спрятанный в текстовый чанк",
    "Color profile: usually kept":
        "Цветовой профиль: обычно сохраняем",
    "Color profile: usually kept when cleaning, but the profile name may name a specific monitor or scanner":
        "Цветовой профиль: при чистке обычно сохраняем, но имя профиля может называть конкретный монитор или сканер",
    "Comment":
        "Комментарий",
    "Comment author":
        "Автор комментария",
    "Comment author or participant":
        "Автор комментария или участник",
    "Comment creation date":
        "Дата создания комментария",
    "Comment date":
        "Дата комментария",
    "Comment date (dT, created)":
        "Дата комментария (dT, created)",
    "Comment dates":
        "Даты комментариев",
    "Comment from Windows Explorer (UTF-16LE)":
        "Комментарий из проводника Windows (UTF-16LE)",
    "Comment subject.":
        "Тема комментария.",
    "Comment text":
        "Текст комментария",
    "Comments":
        "Комментарии",
    "Comments and authors":
        "Комментарии и авторы",
    "CompObj could not be parsed: %r":
        "CompObj не разобрался: %r",
    "CompObj records which application the object was created with -- even if the document properties were erased.":
        "CompObj хранит, каким приложением объект был создан -- даже если свойства документа стёрли.",
    "Company — organization":
        "Company — организация",
    "Compression methods":
        "Методы сжатия",
    "Conclusions":
        "Выводы",
    "Container CLSID: %s":
        "CLSID контейнера: %s",
    "Container creation time":
        "Время создания контейнера",
    "Container entry times":
        "Времена записей контейнера",
    "Container entry timestamps":
        "Таймстемпы записей контейнера",
    "Container flags":
        "Флаги контейнера",
    "Container items (%d)":
        "Элементы контейнера (%d)",
    "Container modification time":
        "Время изменения контейнера",
    "Container part list":
        "Перечень частей контейнера",
    "Content parsing skipped for security reasons":
        "Разбор содержимого пропущен из соображений безопасности",
    "Content type":
        "Тип содержимого",
    "Continuation of an XMP packet that did not fit into 64 KB":
        "Продолжение XMP-пакета, не поместившегося в 64 КБ",
    "Contradictions in the metadata":
        "Противоречия в метаданных",
    "Converters write their name here, and sometimes the author's":
        "Сюда пишут имя конвертера, а иногда и автора",
    "Coordinates (decimal)":
        "Координаты (десятичные)",
    "Copyright: almost always a person's full name or a company name":
        "Копирайт: почти всегда ФИО или название фирмы",
    "Could not build the report: %s":
        "Не удалось собрать отчёт: %s",
    "Could not clean the archive because of an internal error. The archive with the result was NOT sent: do not consider the files cleaned. The original archive is unchanged and not stored on the bot's server.":
        "Не получилось почистить архив из-за внутренней ошибки. Архив с результатом НЕ отправлен: не считайте файлы почищенными. Исходный архив не изменён и не сохранён на сервере бота.",
    "Could not clean the file because of an internal error. The cleaned file was NOT sent: do not consider the file cleaned. The original file is unchanged and not stored on the bot's server.":
        "Не получилось почистить файл из-за внутренней ошибки. Почищенный файл НЕ отправлен: не считайте файл почищенным. Исходный файл не изменён и не сохранён на сервере бота.",
    "Could not delete. Delete by hand.":
        "Не получилось удалить. Удалите вручную.",
    "Could not enable personal-data removal in the LibreOffice profile.":
        "Не удалось включить снятие личных данных в профиле LibreOffice.",
    "Could not get the file size: %s":
        "Не удалось получить размер файла: %s",
    "Could not inspect the file because of an internal error. The file is unchanged and not stored on the bot's server.":
        "Не получилось разобрать файл из-за внутренней ошибки. Файл не изменён и не сохранён на сервере бота.",
    "Could not write the output archive (errno %s): it's locked or there's no permission.":
        "Не удалось записать выходной архив (errno %s): он занят или нет прав.",
    "Could not write the output file (errno %s): it's locked by another process or there's no permission. File not handed over.":
        "Не удалось записать выходной файл (errno %s): он занят другим процессом или нет прав. Файл не выдан.",
    "Created":
        "Создан",
    "Creating application":
        "Приложение-создатель",
    "Creating application: %s":
        "Приложение-создатель: %s",
    "Creation date":
        "Дата создания",
    "CreationDate == ModDate (%s): the file wasn't edited after creation; either it's one-off machine generation, or the dates were rewritten wholesale":
        "CreationDate == ModDate (%s): файл не редактировали после создания -- либо это одноразовая машинная генерация, либо даты переписали скопом",
    "Creator (authoring application)":
        "Creator (приложение-автор)",
    "Creator = Microsoft Word, but there's no /StructTreeRoot. Word always tags an exported PDF, so the file was rewritten by a third-party tool":
        "Creator = Microsoft Word, но нет /StructTreeRoot. Word всегда тегирует экспортируемый PDF -- файл переписывали сторонним инструментом",
    "Creator = Microsoft Word, but there's no XMP in the file. A Word export always writes an XMP packet, so the metadata has already been scrubbed":
        "Creator = Microsoft Word, но XMP в файле нет. Экспорт Word всегда пишет XMP-пакет -- значит, метаданные уже вычищали",
    "Creator claims an authoring application (%r), while Producer is a service rewriter (%r): the file has already been post-processed":
        "Creator заявляет авторское приложение (%r), а Producer -- служебный переписыватель (%r): файл уже прогоняли через постобработку",
    "Critical findings in the output: %d. Of these, %d have the same value as in the original (%s): they weren't part of the leak comparison (short or service values). Check them yourself in the list below.":
        "Критичных находок в результате: %d. Из них %d с тем же значением, что и в исходнике (%s): в сверку утечек они не попали (короткое или служебное значение). Проверьте их сами в списке ниже.",
    "Critical findings in the output: %d. These are NEW values that weren't in the original, e.g. a regenerated trailer /ID. They are unrelated to the source file.":
        "Критичных находок в результате: %d. Это НОВЫЕ значения, которых в исходнике не было -- например перегенерированный trailer /ID. С исходным файлом они не связаны.",
    "Critical findings: %d -> %d   All sensitive: %d -> %d":
        "Критичных находок: %d -> %d   Всех чувствительных: %d -> %d",
    "Critical in the output with original values: %d":
        "Критичное в результате с исходными значениями: %d",
    "Critical in the output, but the values are new (not in the source, unrelated to it): %d (%s)":
        "Критичное в результате, но значения новые (в исходнике таких нет, с ним не связаны): %d (%s)",
    "Current User stream could not be parsed: %r":
        "поток Current User не разобрался: %r",
    "Custom XML part":
        "Пользовательская XML-часть",
    "Custom XML part (empty)":
        "Пользовательская XML-часть (пустая)",
    "Custom numbering (i, ii, 1, 2...). Kept.":
        "Пользовательская нумерация (i, ii, 1, 2...). Сохраняем.",
    "Custom page numbering.":
        "Пользовательская нумерация страниц.",
    "Custom parts that didn't parse as XML":
        "Пользовательские части, не разобранные как XML",
    "Custom properties":
        "Пользовательские свойства",
    "Custom property (type %s).":
        "Пользовательское свойство (тип %s).",
    "Custom property: %s":
        "Польз. свойство: %s",
    "DEVMODE block":
        "Блок DEVMODE",
    "Data after %s":
        "Данные после %s",
    "Data after EOI":
        "Данные после EOI",
    "Data after IEND":
        "Данные после IEND",
    "Data after RIFF":
        "Данные после RIFF",
    "Data after the trailer":
        "Данные после trailer",
    "Data connection":
        "Подключение к данным",
    "Data source: %s":
        "Источник данных: %s",
    "DateTime (%s) does not match DateTimeOriginal (%s): the file was re-saved after capture":
        "DateTime (%s) не совпадает с DateTimeOriginal (%s): файл пересохраняли после съёмки",
    "Dating and geolocation from indirect evidence":
        "Датировка и геолокация по косвенным признакам",
    "Declares that the PDF is tagged. Kept together with /StructTreeRoot.":
        "Объявление, что PDF тегированный. Сохраняем вместе с /StructTreeRoot.",
    "Default language":
        "Язык по умолчанию",
    "Default value \"%s\"":
        "Значение по умолчанию «%s»",
    "Default value: the picture displays the same without the tag":
        "Значение по умолчанию: без тега картинка отображается так же",
    "Defused before launching LibreOffice.":
        "Обезврежены до запуска LibreOffice.",
    "Delete reports from the chat":
        "Удалить отчёты из чата",
    "Density":
        "Плотность",
    "Description":
        "Описание",
    "Develop settings and technical fields: not PII, but they fingerprint a specific editor and its version":
        "Настройки проявки и технические поля: не PII, но фингерпринтят конкретный редактор и его версию",
    "Device time zone: hints at the region":
        "Часовой пояс устройства: намекает на регион",
    "DigSig — digital signature":
        "DigSig — цифровая подпись",
    "Digital signature":
        "Цифровая подпись",
    "Digital signature \"%s\"":
        "Цифровая подпись «%s»",
    "Display name":
        "Отображаемое имя",
    "Display parameters":
        "Параметры отображения",
    "DocSecurity — protection flag":
        "DocSecurity — флаг защиты",
    "Document body":
        "Тело документа",
    "Document certification signature":
        "Сертифицирующая подпись документа",
    "Document comments":
        "Комментарии к документу",
    "Document data store":
        "Хранилище данных документа",
    "Document event handlers (print, save, close).":
        "Обработчики событий документа (печать, сохранение, закрытие).",
    "Document event handlers: print, save, close.":
        "Обработчики событий документа: печать, сохранение, закрытие.",
    "Document is encrypted":
        "Документ зашифрован",
    "Document language /Lang":
        "Язык документа /Lang",
    "Document language: needed by screen readers and for hyphenation.":
        "Язык документа: нужен скринридерам и переносам.",
    "Document macros":
        "Макросы документа",
    "Document navigation. Kept, but section headings by themselves may reveal the internal structure of a project.":
        "Навигация по документу. Сохраняем, но заголовки разделов сами по себе могут выдавать внутреннюю структуру проекта.",
    "Document preview":
        "Превью документа",
    "Document statistics":
        "Статистика документа",
    "Document statistics and composition":
        "Статистика и состав документа",
    "Document status":
        "Состояние документа",
    "Document thumbnail":
        "Превью документа",
    "Document version":
        "Версия документа",
    "Document version number":
        "Номер версии документа",
    "Document-level script":
        "Скрипт уровня документа",
    "Document-level script \"%s\"":
        "Скрипт уровня документа «%s»",
    "DocumentSummaryInformation stream":
        "Поток DocumentSummaryInformation",
    "Don't clean":
        "Не чистить",
    "Draft / under review: reveals an internal process.":
        "Черновик/на согласовании — выдаёт внутренний процесс.",
    "Duplicate of the comment text.":
        "Дубль текста комментария.",
    "Duplicates the Purview label as a separate part: the label GUID, tenant, email. The tenant may notice the label was removed: that's the user's decision.":
        "Дублирует метку Purview отдельной частью: GUID метки, тенанта, email. Снятие метки тенант может заметить: это решение пользователя.",
    "Duplicates the sensitivity label as a separate package part; cleaning custom.xml alone does not remove it.":
        "Дублирует метку конфиденциальности отдельной частью пакета; чистка только custom.xml её не убирает.",
    "During conversion LibreOffice loads external images by itself, revealing your IP to the link's owner. Defused before launch.":
        "LibreOffice при конвертации сам загружает внешние картинки, раскрывая ваш IP владельцу ссылки. Обезврежены до запуска.",
    "EXIF (not parsed)":
        "EXIF (не разобран)",
    "EXIF cut off (%s)":
        "EXIF оборван (%s)",
    "EXIF version: a fingerprint of the firmware/software":
        "Версия EXIF: фингерпринт прошивки/софта",
    "Each picture carries its own metadata (EXIF, XMP, ICC, GPS). A recursive inspection by the image inspector is required: the dispatcher calls it, not this module.":
        "Каждая картинка несёт собственные метаданные (EXIF, XMP, ICC, GPS). Требуется рекурсивная инспекция инспектором изображений: вызывает её диспетчер, не этот модуль.",
    "Each rsid is a separate editing session. Their number reveals how many times the document was opened and edited, and the set itself links different documents from one Word installation.":
        "Каждый rsid — отдельная сессия редактирования. Их количество выдаёт, сколько раз документ открывали и правили, а сам набор связывает разные документы одной инсталляции Word.",
    "Earlier page versions and the text under a \"cover-up\" are physically discarded by the full rewrite (QPDF garbage collection); there is no /Prev in the trailer.":
        "Прошлые версии страниц и текст под «замазкой» физически выброшены полной перезаписью (сборка мусора QPDF); /Prev в трейлере нет.",
    "Edit dates":
        "Даты правок",
    "Edit history xmpMM:History":
        "История правок xmpMM:History",
    "Edit history: the softwareAgent and when of every edit.":
        "Журнал правок: softwareAgent и when каждой правки.",
    "Editing cycles":
        "Циклов редактирования",
    "Editing minutes":
        "Минут редактирования",
    "Editing session table (rsid)":
        "Таблица сеансов правки (rsid)",
    "Editing time":
        "Время редактирования",
    "Editing time (%d s) exceeds the span between creation and save (%d s): the document is older than its dates claim.":
        "Время редактирования (%d с) больше промежутка между созданием и сохранением (%d с): документ старше, чем утверждают его даты.",
    "Editing time in minutes. ":
        "Время редактирования в минутах. ",
    "Email in field %s":
        "Email в поле %s",
    "Embedded JFIF thumbnail":
        "Вшитая миниатюра JFIF",
    "Embedded OLE object":
        "Вложенный OLE-объект",
    "Embedded OLE object: it has its own docProps inside (author, company, path), and OLE2 can't be cleaned in place, so the metadata INSIDE the object remains. The paranoid profile removes such an object entirely.":
        "Вложенный OLE-объект: внутри свои docProps (автор, компания, путь), а OLE2 на месте не чистится, поэтому метаданные ВНУТРИ объекта остались. Профиль paranoid удаляет такой объект целиком.",
    "Embedded OLE objects":
        "Встроенные OLE-объекты",
    "Embedded file":
        "Вложенный файл",
    "Embedded file of a format that has no cleaner (%s): its content wasn't parsed.":
        "Вложенный файл формата, для которого чистки нет (%s): содержимое не разбиралось.",
    "Embedded file without metadata":
        "Вложенный файл без метаданных",
    "Embedded image":
        "Вложенное изображение",
    "Embedded images":
        "Встроенные изображения",
    "Embedded item data":
        "Встроенные данные элементов",
    "Embedded item larger than %d MB: not loaded into memory; the metadata inside remains.":
        "Вложение больше %d МБ: в память не берём, метаданные внутри остались.",
    "Embedded object":
        "Вложенный объект",
    "Embedded objects":
        "Вложенные объекты",
    "Embedded objects (\\objemb)":
        "Внедрённые объекты (\\objemb)",
    "Embedded thumbnail":
        "Встроенная миниатюра",
    "Empty custom XML parts":
        "Пустые пользовательские XML-части",
    "Empty identity fields (%s). For %s this is the default state: the suite is installed with blank user data, so this is not a trace of cleaning.":
        "Пустые поля личности (%s). Для %s это состояние по умолчанию: пакет ставится с незаполненными данными пользователя, так что следом чистки это не является.",
    "Encoders write their name and version here, people write anything":
        "Кодировщики пишут сюда имя и версию, люди -- что угодно",
    "Encrypted parts":
        "Зашифрованные части",
    "Encryption":
        "Шифрование",
    "Encryption parameters":
        "Параметры шифрования",
    "Entries in the archive":
        "Записей в архиве",
    "Entries in the package":
        "Записей в пакете",
    "Entry timestamps":
        "Таймстемпы записей",
    "Environment and paths":
        "Окружение и пути",
    "Errors during cleaning":
        "Ошибки при чистке",
    "Europe and the rest of the world":
        "Европа и остальной мир",
    "Event-handler scripts of an annotation or field.":
        "Скрипты-обработчики событий аннотации или поля.",
    "Every embedded object (an Excel sheet, a chart, a pasted document) is a separate OLE container WITH ITS OWN metadata: its own author, its own dates and its own residual data. Cleaning the outer file doesn't touch them.":
        "Каждый встроенный объект (лист Excel, диаграмма, вставленный документ) -- это отдельный OLE-контейнер СО СВОИМИ метаданными: своим автором, своими датами и своими остаточными данными. Чистка внешнего файла их не трогает.",
    "Exact time in the entries' extra fields":
        "Точное время в extra-полях записей",
    "Excel connections":
        "Подключения Excel",
    "Excel silently writes the full path of the workbook's folder, usually C:\\Users\\NAME\\...: it is not visible in the document properties.":
        "Excel молча пишет полный путь к папке книги, обычно C:\\Users\\ИМЯ\\...: в свойствах документа его не видно.",
    "Excel specifics":
        "Специфика Excel",
    "Excel user name":
        "Имя пользователя Excel",
    "Excel writes the folder where the workbook was saved here, with the OS user name. The inspector doesn't see this place.":
        "Excel пишет сюда каталог, где книгу сохраняли, с именем пользователя ОС. Инспектор этого места не видит.",
    "Exif item":
        "Элемент Exif",
    "Extended XMP":
        "Расширенный XMP",
    "External file link":
        "Внешняя ссылка на файл",
    "External http(s) hyperlinks":
        "Внешние гиперссылки http(s)",
    "External hyperlink":
        "Внешняя гиперссылка",
    "External link records":
        "Записи внешних связей",
    "External link to a file":
        "Внешняя ссылка на файл",
    "External link to a file (%s)":
        "Внешняя ссылка на файл (%s)",
    "External links":
        "Внешние ссылки",
    "External links could not be defused, regeneration cancelled: LibreOffice would have followed them over the network.":
        "Внешние ссылки обезвредить не удалось, пересоздание отменено: LibreOffice пошёл бы по ним в сеть.",
    "External relationship (%s)":
        "Внешнее отношение (%s)",
    "External relationship; corporate URLs reveal the portal and the account.":
        "Внешнее отношение; корпоративные URL выдают портал и учётную запись.",
    "External workbook link":
        "Связь с внешней книгой",
    "FIB flags could not be parsed: %r":
        "флаги FIB не разобрались: %r",
    "FIB of the WordDocument stream could not be read: %r":
        "FIB потока WordDocument не читается: %r",
    "FILE NOT CLEANED: %s.":
        "ФАЙЛ НЕ ПОЧИЩЕН: %s.",
    "FILE NOT CLEANED: cleaning did not finish.":
        "ФАЙЛ НЕ ПОЧИЩЕН: чистка не завершилась.",
    "FILE NOT CLEANED: cleaning returned no result.":
        "ФАЙЛ НЕ ПОЧИЩЕН: чистка не вернула результата.",
    "FILE NOT CLEANED: the verdict could not be built.":
        "ФАЙЛ НЕ ПОЧИЩЕН: вердикт не собрался.",
    "FILEPASS record":
        "запись FILEPASS",
    "Fast-save count":
        "Счётчик быстрых сохранений",
    "Field \"%s\"":
        "Поле «%s»",
    "Field \"%s\" (%s)":
        "Поле «%s» (%s)",
    "File NOT cleaned: ":
        "Файл НЕ очищен: ",
    "File NOT cleaned: %s Output file not created.":
        "Файл НЕ очищен: %s Выходной файл не создан.",
    "File NOT cleaned: format \"%s\" is inspect-only for now; cleaning isn't implemented yet. %s Output file not created.":
        "Файл НЕ очищен: формат «%s» пока только инспектируется, чистка ещё не реализована. %s Выходной файл не создан.",
    "File NOT cleaned: there is no cleaning for format \"%s\". Output file not created.":
        "Файл НЕ очищен: для формата «%s» чистки нет. Выходной файл не создан.",
    "File is not readable":
        "Файл недоступен для чтения",
    "File larger than %d MB: cleaning not performed.":
        "Файл больше %d МБ: чистка не выполнена.",
    "File not cleaned: ":
        "Файл не очищен: ",
    "File not cleaned: %s":
        "Файл не очищен: %s",
    "Files embedded in the PDF: %d. An attachment is a separate document with its own metadata; PDF inspection doesn't open its metadata":
        "В PDF вложено файлов: %d. Вложение -- это отдельный документ со своими метаданными, инспекция PDF его метаданные не вскрывает",
    "Findings: %d, needing cleaning: %d":
        "Находок: %d, из них требуют чистки: %d",
    "Findings: <b>%d</b>, needing cleaning: <b>%d</b>":
        "Находок: <b>%d</b>, из них требуют чистки: <b>%d</b>",
    "First ZIP entry is %s, not [Content_Types].xml: the package was built by a third-party tool or rebuilt.":
        "Первая запись ZIP — %s, а не [Content_Types].xml: пакет собран сторонним инструментом или пересобран.",
    "First ZIP entry is %s: the normal order for %s, whereas Microsoft Office puts [Content_Types].xml first.":
        "Первая запись ZIP — %s: штатный порядок для %s, Microsoft Office кладёт первым [Content_Types].xml.",
    "First container entry":
        "Первая запись контейнера",
    "Flag showing whether the label is active.":
        "Флаг активности метки.",
    "Flag that signatures are present: there are no signatures any more.":
        "Флаг наличия подписей: подписей больше нет.",
    "Flag that the form contains signatures.":
        "Флаг наличия подписей в форме.",
    "Font":
        "Шрифт",
    "Font (subset)":
        "Шрифт (сабсет)",
    "Font table: a part required for a file from Word.":
        "Таблица шрифтов: часть, обязательная для файла от Word.",
    "Font that dates the document":
        "Шрифт, датирующий документ",
    "For docx, xlsx and pptx this is a limit of the format, not a shortcoming: empty dc:creator and cp:lastModifiedBy together are a trace in themselves, keeping the real name means not cleaning, and substituting someone else's means forging provenance. Only regenerating the file with a stock application gives invisibility (Regenerate mode, phase 4).":
        "Для docx, xlsx и pptx это предел формата, а не недоработка: пустые dc:creator и cp:lastModifiedBy вместе сами являются признаком, а оставить настоящее имя значит не почистить, подставить чужое -- подделать провенанс. Незаметность даёт только пересоздание файла стоковым приложением (режим Regenerate, фаза 4).",
    "For example 'Draft' -- reveals the stage of work.":
        "Например 'Черновик' -- выдаёт стадию работы.",
    "Format version":
        "Версия формата",
    "Format version (nFib)":
        "Версия формата (nFib)",
    "Format:  %s":
        "Формат:  %s",
    "Format: %s   Profile: %s":
        "Формат: %s   Профиль: %s",
    "Formats the image cleaner doesn't parse (EMF, WMF, SVG, video, audio): metadata in them was neither searched for nor removed.":
        "Форматы, которые чистильщик картинок не разбирает (EMF, WMF, SVG, видео, звук): метаданные в них не искались и не убирались.",
    "Formats the image cleaner doesn't parse (SVM, EMF, WMF, SVG): metadata in them was neither searched for nor removed.":
        "Форматы, которые чистильщик картинок не разбирает (SVM, EMF, WMF, SVG): метаданные в них не искались и не убирались.",
    "Formatted duplicate of the comment text.":
        "Форматированный дубль текста комментария.",
    "Found by the external exiftool: our parsing does not reach this field":
        "Найдено внешним exiftool: наш разбор до этого поля не добирается",
    "Found in the driver's private block (offset %d). The print server and queue name: this is a host name on your network.":
        "Найден в приватном блоке драйвера (смещение %d). Имя сервера печати и очереди: это имя хоста в вашей сети.",
    "Fractions of a second of the capture":
        "Доли секунды съёмки",
    "Fractions of a second of the digitization":
        "Доли секунды оцифровки",
    "Fractions of a second: refine the timestamp":
        "Доли секунды: уточняют временную метку",
    "Frame description: often the author's full name, or the name of a project or client":
        "Описание кадра: часто ФИО автора, название проекта или заказчика",
    "Free-form user comment":
        "Свободный комментарий пользователя",
    "Full list of RIFF chunks with sizes":
        "Полный список RIFF-чанков с размерами",
    "Full list of chunks with sizes":
        "Полный список чанков с размерами",
    "Full list of markers with sizes: shows what exactly is about to be cut out and what will remain":
        "Полный список маркеров с размерами: по нему видно, что именно предстоит вырезать и что при этом останется",
    "GIF comment":
        "Комментарий GIF",
    "GIF comment: the converter's name goes here, and sometimes the author's":
        "Комментарий GIF: сюда пишут имя конвертера, а иногда и автора",
    "GIF: extension is truncated":
        "GIF: расширение обрезано",
    "GIF: file is shorter than the logical screen":
        "GIF: файл короче логического экрана",
    "GIF: image descriptor is truncated":
        "GIF: дескриптор изображения обрезан",
    "GIF: no frames, nothing to clean":
        "GIF: нет кадров, чистить нечего",
    "GIF: unknown block 0x%02X at offset %d":
        "GIF: неизвестный блок 0x%02X на смещении %d",
    "GPS coordinates, altitude, time and tags: they locate a person, not a file":
        "Координаты, высота, время и метки GPS: по ним находят человека, а не файл",
    "GUID of the labeling operation.":
        "GUID операции маркировки.",
    "Gamma":
        "Гамма",
    "Generator":
        "Генератор",
    "Generic ZIP archive: cleaning archives is not planned.":
        "ZIP-архив общего вида: чистка архивов в плане не предусмотрена.",
    "Geolocation data of the shot":
        "Геоданные снимка",
    "HDR brightness":
        "яркость HDR",
    "HDR mastering":
        "мастеринг HDR",
    "HEIF/HEIC cleaning isn't supported yet: the metadata lives in iloc items, and rebuilding without dedicated code risks damaging the file":
        "HEIF/HEIC пока не поддержан чисткой: метаданные лежат в элементах iloc, пересборка без специального кода рискует испортить файл",
    "HEIF/HEIC: limited support -- we list the boxes and items, EXIF values inside iloc are not extracted":
        "HEIF/HEIC: поддержка ограниченная -- перечисляем боксы и элементы, значения EXIF внутри iloc не извлекаем",
    "HeadingPairs — document composition":
        "HeadingPairs — состав документа",
    "Help":
        "Справка",
    "Hidden and very hidden sheets usually contain draft calculations and source data.":
        "Скрытые и особо скрытые листы обычно содержат черновые расчёты и исходные данные.",
    "Hidden bytes outside container entries":
        "Скрытые байты вне записей контейнера",
    "Hidden sheets":
        "Скрытые листы",
    "Hidden slides":
        "Скрытых слайдов",
    "Hidden slides stay in the file in full.":
        "Скрытые слайды остаются в файле целиком.",
    "HiddenSlides — hidden slides":
        "HiddenSlides — скрытых слайдов",
    "How long people really worked on the document. A strong leak: it reveals both the effort and that the document is not a template.":
        "Сколько реально работали над документом. Сильная утечка: выдаёт и трудоёмкость, и то, что документ не шаблонный.",
    "How many bytes went through the streaming parse.":
        "Сколько байт прошло через потоковый разбор.",
    "How many times the document was opened for editing and saved.":
        "Сколько раз документ открывали на правку и сохраняли.",
    "How many times the document was saved.":
        "Сколько раз документ сохраняли.",
    "How many times the file was saved in fast-save mode. Anything above zero means pieces of past versions appended to the end of the file.":
        "Сколько раз файл сохранялся в режиме быстрого сохранения. Всё, что больше нуля, означает дописанные в конец файла куски прошлых версий.",
    "How much time in total the document was open for editing. Shows whether the text was written over hours or pasted in ready-made within a minute.":
        "Сколько суммарно времени документ был открыт на правку. Выдаёт, писали текст часами или вставили готовый за минуту.",
    "How the label was applied: manually or by policy.":
        "Как применена метка: вручную или политикой.",
    "How to open the document. Harmless, kept.":
        "Как открывать документ. Безобидно, сохраняем.",
    "How to open the document; harmless.":
        "Как открывать документ; безобидно.",
    "How to use":
        "Как пользоваться",
    "Hyperlink base":
        "База гиперссылок",
    "Hyperlinks":
        "Гиперссылки",
    "Hyperlinks often point to local and network paths.":
        "Гиперссылки нередко указывают на локальные и сетевые пути.",
    "I/O error: %s":
        "ошибка ввода-вывода: %s",
    "ICC profile":
        "ICC-профиль",
    "ICC profile (hex)":
        "ICC-профиль (hex)",
    "IDAT has %d bytes but IHDR requires %d: the image is truncated":
        "в IDAT %d байт, а IHDR требует %d: изображение обрезано",
    "IPTC-IIM (caption, keywords, authorship, address), Photoshop thumbnail and an XMP copy":
        "IPTC-IIM (подпись, ключевые слова, авторство, адрес), миниатюра Photoshop и XMP-копия",
    "IPTC-IIM: the old photo caption format; editors carry it between files along with the picture":
        "IPTC-IIM: старый формат подписи фотографий, редакторы его переносят между файлами вместе с картинкой",
    "ISOBMFF container structure":
        "Структура контейнера ISOBMFF",
    "Identity and PII":
        "Личность и PII",
    "Identity blanked, elements kept: their absence would itself be a cleaning trace. Generator %s.":
        "Личность обнулена, элементы оставлены: их отсутствие само признак чистки. Генератор %s.",
    "Identity or environment in XMP: survives the cleaning of /Info.":
        "Идентичность или окружение в XMP: переживает чистку /Info.",
    "Identity or environment: removed in every profile.":
        "Личность или окружение: убирается в любом профиле.",
    "Identity. The element was left EMPTY rather than removed: Word writes it empty when no user name is set, and a missing element is a cleaning trace.":
        "Личность. Элемент оставлен ПУСТЫМ, а не удалён: Word пишет его пустым, когда имя пользователя не заполнено, а отсутствие самого элемента -- признак чистки.",
    "Identity: PARANOID removes the element.":
        "Личность: PARANOID убирает элемент.",
    "Identity: blanked, the element was left empty (that's how Word writes an unset field).":
        "Личность: обнулено, элемент оставлен пустым (так Word пишет незаполненное поле).",
    "Image (object %d) is larger than %d MB: not parsed.":
        "Картинка (объект %d) больше %d МБ: не разобрана.",
    "Image (object %d): %s: %s":
        "Картинка (объект %d): %s: %s",
    "Image data":
        "Данные изображения",
    "Images":
        "Картинки",
    "In ODF dc:creator is NOT the author but whoever saved the file last.":
        "В ODF dc:creator -- это НЕ автор, а тот, кто последним сохранял файл.",
    "In RTF \\operator is whoever saved the file last. It often differs from \\author and reveals a second participant.":
        "В RTF \\operator -- это тот, кто последним сохранял файл. Часто отличается от \\author и выдаёт второго участника.",
    "In many producers ID[0] is derived from the file path and name and links files. Both values are new and random; the pair is %s, as in the source (a diverged pair betrays a re-save). Not derived from the content: otherwise cleaning the same file again would give the same /ID.":
        "ID[0] у многих продюсеров выведен из пути и имени файла и связывает файлы. Оба значения новые и случайные; пара %s, как у исходника (разошедшаяся пара выдаёт пересохранение). Не из содержимого: иначе повторная чистка того же файла давала бы тот же /ID.",
    "Inactive table stream":
        "Неактивный поток таблицы",
    "Incremental revisions":
        "Инкрементальные ревизии",
    "Incremental updates":
        "Инкрементальные апдейты",
    "Initials are part of a comment author's name.":
        "Инициалы -- часть имени автора комментария.",
    "Initials of the comment's author.":
        "Инициалы автора примечания.",
    "Inside is an encoded package with the query code: server addresses, file paths and source names. Cleaning this container isn't implemented; the metadata remains.":
        "Внутри закодированный пакет с кодом запросов: адреса серверов, пути к файлам и названия источников. Чистка этого контейнера не реализована, метаданные остались.",
    "Inspection did not finish within {}s and was aborted -- the file is too complex or damaged.":
        "Разбор не уложился в {}с и был прерван -- файл слишком сложный или повреждён.",
    "Inspection phase. The file was not modified, but it couldn't be read.":
        "Фаза инспекции. Файл не изменён, но прочитать его не удалось.",
    "Inspection phase: the file was read and not modified.":
        "Фаза инспекции: файл прочитан и не изменён.",
    "Interactive form":
        "Интерактивная форма",
    "Internal ODF cleaning error: %s":
        "Внутренняя ошибка чистки ODF: %s",
    "Internal OOXML cleaning error: %s: %s":
        "Внутренняя ошибка чистки OOXML: %s: %s",
    "Internal PDF cleaning error: %s: %s":
        "Внутренняя ошибка чистки PDF: %s: %s",
    "Internal RTF cleaning error: %s":
        "Внутренняя ошибка чистки RTF: %s",
    "Internal Regenerate error.":
        "Внутренняя ошибка Regenerate.",
    "Internal batch cleaning error: %s: %s":
        "Внутренняя ошибка пакетной чистки: %s: %s",
    "Internal error in the cleaning dispatcher: %s: %s":
        "Внутренняя ошибка диспетчера чистки: %s: %s",
    "Internal error. The file is not cleaned, send it again.":
        "Внутренняя ошибка. Файл не чищен, пришлите его ещё раз.",
    "Internal identifier":
        "Внутренний идентификатор",
    "Internal version":
        "Внутренняя версия",
    "Item data block: thumbnails live there too":
        "Блок данных элементов: там же лежат миниатюры",
    "Item types inside HEIF":
        "Типы элементов внутри HEIF",
    "JFIF extension with an embedded thumbnail":
        "Расширение JFIF со встроенной миниатюрой",
    "JFIF header":
        "JFIF-заголовок",
    "JFXX thumbnail":
        "JFXX-миниатюра",
    "JFXX thumbnail or a foreign block":
        "JFXX-миниатюра или чужой блок",
    "JPEG comment":
        "Комментарий JPEG",
    "JPEG with no EXIF, XMP or IPTC at all: the file was either already cleaned or went through a messenger that recompresses images":
        "JPEG вообще без EXIF, XMP и IPTC: файл либо уже чистили, либо он прошёл через мессенджер, который пережимает картинки",
    "JPEG: %s was not parsed (%s: %s)":
        "JPEG: %s не разобран (%s: %s)",
    "JPEG: a marker was expected at offset %d, found 0x%02X":
        "JPEG: на смещении %d ожидался маркер, найдено 0x%02X",
    "JPEG: marker 0x%02X declares length %d":
        "JPEG: маркер 0x%02X объявляет длину %d",
    "JPEG: marker 0x%02X has no length (file truncated)":
        "JPEG: маркер 0x%02X без длины (файл обрезан)",
    "JPEG: no image data (SOS), nothing to clean":
        "JPEG: нет данных изображения (SOS), чистить нечего",
    "JPEG: reached the limit of %d segments":
        "JPEG: достигнут предел в %d сегментов",
    "JPEG: segment 0x%02X is truncated (needs %d bytes, have %d)":
        "JPEG: сегмент 0x%02X обрезан (нужно %d байт, есть %d)",
    "JPEG: the stream ends at a marker":
        "JPEG: поток обрывается на маркере",
    "JUMBF container without C2PA labels":
        "JUMBF-контейнер без меток C2PA",
    "JUMBF container without C2PA markers":
        "JUMBF-контейнер без меток C2PA",
    "JavaScript runs when the file is opened and can \"phone home\".":
        "JavaScript выполняется при открытии файла и может «звонить домой».",
    "JavaScript runs when the file is opened. The code often contains field names, server addresses and internal system identifiers.":
        "JavaScript выполняется при открытии файла. Код часто содержит имена полей, адреса серверов и внутренние идентификаторы системы.",
    "Kept per your keep list: %d. Your choice; these fields stayed as they were.":
        "Оставлено по вашему списку keep: %d. Это ваш выбор, поля остались как были.",
    "Keyword":
        "Ключевое слово",
    "Keywords":
        "Ключевые слова",
    "Keywords from Windows Explorer (UTF-16LE)":
        "Ключевые слова из проводника Windows (UTF-16LE)",
    "LF (typical of Unix/Mac)":
        "LF (типично для Unix/Mac)",
    "LZW data, palettes and frame control copied without re-encoding":
        "LZW-данные, палитры и управление кадрами скопированы без перекодирования",
    "LabelInfo (Purview label)":
        "LabelInfo (метка Purview)",
    "Language":
        "Язык",
    "Language: Русский / English":
        "Язык: Русский / English",
    "Last editor name (CurrentUserAtom)":
        "Имя последнего редактора (CurrentUserAtom)",
    "Last modification time":
        "Время последней правки",
    "Last printed":
        "Последняя печать",
    "Last saved":
        "Последнее сохранение",
    "Last saved by":
        "Кто сохранял последним",
    "Launch external program /Launch":
        "Запуск внешней программы /Launch",
    "Layer (optional content)":
        "Слой (optional content)",
    "Layer TURNED OFF":
        "ВЫКЛЮЧЕННЫЙ слой",
    "Legacy OLE2 (doc/xls/ppt) can't be cleaned in place at all: metadata and revision history are embedded in the container itself, so conversion through a stock application is required: phase 4.":
        "Легаси OLE2 (doc/xls/ppt) на месте не чистится в принципе: метаданные и история правок вшиты в сам контейнер, нужна конвертация через стоковое приложение -- фаза 4.",
    "Legal (US)":
        "Legal (США)",
    "Lens manufacturer":
        "Производитель объектива",
    "Lens model":
        "Модель объектива",
    "Lens serial number":
        "Серийный номер объектива",
    "Letter (US/Canada)":
        "Letter (США/Канада)",
    "Library columns, content type schema, form templates: employee names, dates, portal addresses. The content was blanked down to an empty root; the part stayed in place.":
        "Колонки библиотеки, схема типа контента, шаблоны форм: имена сотрудников, даты, адреса портала. Содержимое обнулено до пустого корня, часть осталась на месте.",
    "LibreOffice didn't respond in time. File not created.":
        "LibreOffice не ответил за отведённое время. Файл не создан.",
    "LibreOffice didn't save the file (code %s).":
        "LibreOffice не сохранил файл (код %s).",
    "LibreOffice failed to start. File not created.":
        "LibreOffice не запустился. Файл не создан.",
    "LibreOffice not found. Regenerate is not replaced with in-place cleaning.":
        "LibreOffice не найден. Regenerate не подменяется чисткой на месте.",
    "LibreOffice not found: \"Regenerate\" and doc/xls/ppt are unavailable":
        "LibreOffice не найден: «Пересоздать» и doc/xls/ppt недоступны",
    "LibreOffice/OpenOffice write the REAL local save time into the ZIP (Word in OOXML puts in a 1980-01-01 placeholder). These timestamps give away the exact moment the file was worked on even after meta.xml is fully cleaned, and no document properties dialog shows them.":
        "LibreOffice/OpenOffice записывают в ZIP НАСТОЯЩЕЕ локальное время сохранения (Word в OOXML ставит заглушку 1980-01-01). Эти отметки выдают точный момент работы над файлом даже после полной очистки meta.xml, и их не видно ни в одном диалоге свойств документа.",
    "LibreOffice: \"Regenerate\" and old doc/xls/ppt":
        "LibreOffice: «Пересоздать» и старые doc/xls/ppt",
    "Line-ending style":
        "Стиль переводов строк",
    "Linearization":
        "Линеаризация",
    "Linearization is a sign of saving through Acrobat or an explicit qpdf --linearize. It also drops all incremental revisions.":
        "Линеаризация -- признак сохранения через Acrobat или явного qpdf --linearize. Заодно она выбрасывает все инкрементальные ревизии.",
    "Linearized (fast web view)":
        "Линеаризован (fast web view)",
    "Lines":
        "Строк",
    "Lines — lines":
        "Lines — строк",
    "Link base":
        "База ссылок",
    "Linked objects (\\objlink)":
        "Связанные объекты (\\objlink)",
    "Linked objects are updated automatically when the file is opened -- that is active behavior, not a static insert.":
        "Связанные объекты обновляются автоматически при открытии файла -- это активное поведение, а не статичная вставка.",
    "Links to other workbooks. The path to the source file (often \\\\server\\dept\\budget.xls or C:\\Users\\...) is stored inside together with the value cache.":
        "Связи с другими книгами. Путь к исходному файлу (часто \\\\сервер\\отдел\\бюджет.xls или C:\\Users\\...) хранится внутри вместе с кешем значений.",
    "List of authors (people.xml)":
        "Список авторов (people.xml)",
    "Local path: %s":
        "Локальный путь: %s",
    "Long-term signature validation data: certificate chains.":
        "Данные долгосрочной валидации подписи: цепочки сертификатов.",
    "METADATA CLEANING REPORT":
        "ОТЧЁТ О ЧИСТКЕ МЕТАДАННЫХ",
    "METADATA INSPECTION REPORT":
        "ОТЧЁТ ОБ ИНСПЕКЦИИ МЕТАДАННЫХ",
    "Macro stream":
        "Поток макросов",
    "MakerNote of %d bytes: a proprietary camera block, usually holding the unit's serial number and the shutter count":
        "MakerNote на %d байт: проприетарный блок камеры, внутри обычно серийник экземпляра и счётчик затвора",
    "Manager":
        "Руководитель",
    "Manager — manager":
        "Manager — руководитель",
    "Many producers derive /ID[0] from the full path and name of the source file: the same /ID[0] in two PDFs means a common source.":
        "У многих продюсеров /ID[0] выводится из полного пути и имени исходного файла: одинаковый /ID[0] у двух PDF означает общий источник.",
    "Marker %¿÷¢þ is a well-known qpdf signature; replaced with the source's marker of equal length.":
        "Маркер %¿÷¢þ -- общеизвестная подпись qpdf; заменён равным по длине маркером исходника.",
    "Media and attachments":
        "Медиа и вложения",
    "Merge data source: a path to a database or table, or a connection string with a login and server address.":
        "Источник данных слияния: путь к базе, таблице или строка подключения с логином и адресом сервера.",
    "Messages deleted: %d":
        "Удалено сообщений: %d",
    "Messages:":
        "Сообщения:",
    "Metadata of a pasted image or a page from another file. Survives cleaning of /Info and the root XMP unless removed separately.":
        "Метаданные вставленной картинки или страницы из другого файла. Переживают чистку /Info и корневого XMP, если их не снять отдельно.",
    "Metadata was not extracted.":
        "Метаданные не извлечены.",
    "Microsoft Purview sensitivity label.":
        "Метка конфиденциальности Microsoft Purview.",
    "Missing parts":
        "Отсутствующие части",
    "ModDate (%s) is EARLIER than CreationDate (%s): the dates were edited by hand":
        "ModDate (%s) РАНЬШЕ CreationDate (%s): даты правили вручную",
    "Modification date":
        "Дата изменения",
    "Modification date (%s) is earlier than creation date (%s): the metadata was edited by hand.":
        "Дата изменения (%s) раньше даты создания (%s): метаданные правили вручную.",
    "Modification date /M":
        "Дата изменения /M",
    "Modification time of a file inside the archive: a build stamp that ties the archive to when it was created.":
        "Время модификации файла внутри архива -- метка сборки, привязывает архив к моменту создания.",
    "Moment of capture":
        "Момент съёмки",
    "Moment of digitization":
        "Момент оцифровки",
    "Moment the image was last changed":
        "Момент последнего изменения картинки",
    "More external hyperlinks":
        "Ещё внешних гиперссылок",
    "More than %d JPEG images; the rest were not parsed.":
        "Картинок-JPEG больше %d: остальные не разобраны.",
    "More than %d annotations: cleaning not performed, so as not to leave some of them unprocessed.":
        "Аннотаций больше %d: чистка не выполнена, чтобы не оставить часть необработанной.",
    "More than %d annotations; the rest were not parsed.":
        "Аннотаций больше %d, остальные не разобраны.",
    "More than %d form fields; the rest were not parsed.":
        "Полей формы больше %d, остальные не разобраны.",
    "More than %d images: cleaning not performed, so as not to leave some of them unprocessed.":
        "Картинок больше %d: чистка не выполнена, чтобы не оставить часть необработанной.",
    "More than %d objects with their own XMP: the first ones were parsed.":
        "Объектов со своим XMP больше %d: разобраны первые.",
    "More than %d objects: object XMP was not parsed across the whole file.":
        "Объектов больше %d: XMP объектов разобран не во всём файле.",
    "More than %d pages: cleaning not performed, so as not to leave some of them unprocessed.":
        "Страниц больше %d: чистка не выполнена, чтобы не оставить часть необработанной.",
    "More than %d resources: not all fonts of nested forms were parsed.":
        "Ресурсов больше %d: шрифты вложенных форм разобраны не все.",
    "NO CRITICAL FINDINGS LEFT, BUT WITH CAVEATS (%s).":
        "КРИТИЧНОГО НЕ ОСТАЛОСЬ, НО ЕСТЬ ОГОВОРКИ (%s).",
    "NO ORIGINAL VALUES LEFT, BUT WITH CAVEATS (critical %s).":
        "ИСХОДНЫХ ЗНАЧЕНИЙ НЕ ОСТАЛОСЬ, НО ЕСТЬ ОГОВОРКИ (критичных %s).",
    "NOT CLEAN: critical findings after cleaning: %d (%s), not all explained.":
        "НЕ ЧИСТО: критичных находок после чистки: %d (%s), и не всё объяснено.",
    "NOT CLEAN: original values survived cleaning: %d.":
        "НЕ ЧИСТО: исходных значений пережило чистку: %d.",
    "NOT CLEANED (cleanliness not proven)":
        "НЕ ОЧИЩЕН (чистота не доказана)",
    "NOT CLEANED (format not supported)":
        "НЕ ОЧИЩЕН (формат не поддержан)",
    "NOT CLEANED (nested archive)":
        "НЕ ОЧИЩЕН (вложенный архив)",
    "NOT CLEANED (refused)":
        "НЕ ОЧИЩЕН (отказ)",
    "NOT CLEANED (rejected by safeguards)":
        "НЕ ОЧИЩЕН (отклонён защитой)",
    "NOT CLEANED: file not handed over":
        "НЕ ПОЧИЩЕНО: файл не выдан",
    "NOT EXPLAINED by the action list: at least %d. Until it's clear what this is, the file can't be considered clean: re-inspect the output.":
        "НЕ ОБЪЯСНЕНО списком действий: не менее %d. Пока не выяснено, что это, файл чистым считать нельзя: прогоните инспекцию результата.",
    "NOT FULLY CLEANED: %d original values remain":
        "ПОЧИЩЕНО НЕ ПОЛНОСТЬЮ: исходных значений осталось %d",
    "NOT clean: %d original values survived":
        "НЕ чисто: выжило исходных значений %d",
    "NOT cleaned, cleaning did not finish":
        "НЕ почищен, чистка не завершилась",
    "NOT cleaned, verdict could not be built":
        "НЕ почищен, вердикт не собрался",
    "NOT cleaned: %s":
        "НЕ почищен: %s",
    "Name of the annotation's author (the account name in Acrobat/Reader).":
        "Имя автора аннотации (имя учётки в Acrobat/Reader).",
    "Name of the camera owner, written into the firmware":
        "Имя владельца камеры, вписано в прошивку",
    "Name of the clipping path: set by a person, can be revealing":
        "Имя обтравочного контура: задавал человек, бывает говорящим",
    "Name of the computer or machine model that processed the file":
        "Имя компьютера или модель машины, где обрабатывали файл",
    "Name/rights holder in XMP: survives cleaning of /Info.":
        "Имя/правообладатель в XMP: переживает чистку /Info.",
    "Names in archive: %s":
        "Имена в архиве: %s",
    "Names of sheets/slides/sections -- they often reveal the internal structure.":
        "Имена листов/слайдов/разделов -- часто выдают внутреннюю структуру.",
    "Names, logins, emails or initials replaced with neutral ones (%d time(s)).":
        "Имя, логин, email или инициалы заменены на нейтральные (%d раз(а)).",
    "Narrows it down to a specific Office build.":
        "Сужает круг до конкретной сборки Office.",
    "Needed for spell checking and accessibility -- usually kept.":
        "Нужен для проверки орфографии и доступности -- обычно сохраняем.",
    "Needed to read the metadata strings correctly.":
        "Нужна для корректного чтения строк метаданных.",
    "Needed to read the text correctly -- kept.":
        "Нужна для корректного чтения текста -- сохраняем.",
    "Neither 'Author' nor 'Last saved by' is filled in: the file was either cleaned already or produced by a program.":
        "Ни 'Автор', ни 'Кто сохранял последним' не заполнены: файл либо уже чистили, либо делала программа.",
    "Neither /Creator nor /Producer is set: no known producer does that, so the file has almost certainly been scrubbed already":
        "Ни /Creator, ни /Producer не заполнены: так не делает ни один известный продюсер -- файл почти наверняка уже чистили",
    "Neither meta:initial-creator nor dc:creator is filled in -- a sign that cleaning has already been done.":
        "Ни meta:initial-creator, ни dc:creator не заполнены -- признак уже выполненной чистки.",
    "Nested OLE objects":
        "Вложенные OLE-объекты",
    "Nested documents (formulas, charts, tables) -- each has ITS OWN meta.xml with its own author and its own dates.":
        "Вложенные документы (формулы, диаграммы, таблицы) -- у каждого СВОЙ meta.xml со своим автором и своими датами.",
    "Nested images":
        "Вложенные изображения",
    "Nesting deeper than %d: the inner package isn't cleaned; its metadata remains.":
        "Вложенность глубже %d: внутренний пакет не чистится, его метаданные остались.",
    "Network links before conversion":
        "Ссылки на сеть до конвертации",
    "No docProps/app.xml — Office does not write a package like this.":
        "Нет docProps/app.xml — Office такой пакет не пишет.",
    "No docProps/core.xml — Office does not write a package like this.":
        "Нет docProps/core.xml — Office такой пакет не пишет.",
    "No explicit metadata found.":
        "Явных метаданных не обнаружено.",
    "No file handed over: some cleaning sections failed, cleanliness is not guaranteed.":
        "Файл не выдан: часть секций чистки завершилась сбоем, чистота не гарантирована.",
    "No identity or environment found in meta.xml and settings.xml.":
        "Личности и окружения в meta.xml и settings.xml не нашлось.",
    "No proof: the file was not cleaned, the output was not re-inspected.":
        "Доказательства нет: файл не почищен, повторная инспекция результата не выполнялась.",
    "No properties are left: Word never writes an empty custom.xml (and the inspector takes it for a parse error), so the part was removed together with its reference in .rels and its Override.":
        "Свойств не осталось: пустой custom.xml Word не пишет (а инспектор принимает его за ошибку разбора), поэтому часть убрана вместе со ссылкой в .rels и Override.",
    "Non-raster images":
        "Нерастровые изображения",
    "Non-raster media files":
        "Нерастровые медиафайлы",
    "Non-standard app.xml field.":
        "Нестандартное поле app.xml.",
    "Non-standard key %s":
        "Нестандартный ключ %s",
    "Non-standard segment":
        "Нестандартный сегмент",
    "Non-standard segment (%s)":
        "Нестандартный сегмент (%s)",
    "Normal Office behavior: the save time does not leak through the ZIP.":
        "Штатное поведение Office: время сохранения через ZIP не течёт.",
    "Not XML, so the content wasn't checked or cleaned. The inspector shows what's there.":
        "Не XML, поэтому содержимое не проверено и не вычищено. Что там -- показывает инспектор.",
    "Not a single edit session identifier: Word does not write that.":
        "Ни одного идентификатора сессии правки: Word такого не пишет.",
    "Not a single metadata field was found in the RTF: the file was either cleaned already or built from scratch by a program.":
        "В RTF не нашлось ни одного поля метаданных: файл либо уже чистили, либо его собрала программа с нуля.",
    "Not all are shown, the rest are of the same kind.":
        "Показаны не все, остальные того же рода.",
    "Not all are shown; the rest are of the same kind.":
        "Показаны не все, остальные того же рода.",
    "Not an OOXML package (format \"%s\"): docx/xlsx/pptx cleaning doesn't apply to it.":
        "Не пакет OOXML (формат «%s»): чистка docx/xlsx/pptx к нему неприменима.",
    "Notes — notes":
        "Notes — заметок",
    "Numeric \\info property. PARANOID removes it.":
        "Числовое свойство \\info. PARANOID его убирает.",
    "ODF didn't open as ZIP: %s":
        "ODF не открылся как ZIP: %s",
    "ODF generator: %s":
        "Генератор ODF: %s",
    "ODF parsing aborted: %r":
        "разбор ODF прерван: %r",
    "OK, not cleaning the file. It was not stored on the bot's server.":
        "Хорошо, файл не чищу. Он не сохранялся на сервере бота.",
    "OLE2 container (type not recognized)":
        "OLE2-контейнер (тип не опознан)",
    "OLE2 date cross-check failed: %r":
        "сверка дат OLE2 не удалась: %r",
    "OLE2 parsing aborted: %r":
        "разбор OLE2 прерван: %r",
    "OS error code not available":
        "код ошибки ОС не получен",
    "Object has its own XMP packet":
        "Свой XMP-пакет у объекта",
    "Object sweep: %s: %s":
        "Обход объектов: %s: %s",
    "Object type name (UserType)":
        "Имя типа объекта (UserType)",
    "ObjectPool holds nested OLE objects (%d of them); their metadata has to be inspected separately.":
        "Внутри ObjectPool лежат вложенные OLE-объекты (%d шт.); их метаданные надо инспектировать отдельно.",
    "Objects are packed into compressed streams (PDF 1.5+). A plain grep won't show the metadata, but parsing extracts it completely.":
        "Объекты упакованы в сжатые потоки (PDF 1.5+). Простым grep-ом по файлу метаданные не видны, но при разборе достаются полностью.",
    "Office compresses all parts with deflate; a mix of methods is a sign of rebuilding by a third-party tool.":
        "Office сжимает все части deflate; смесь методов — признак пересборки сторонним инструментом.",
    "Office never writes an empty app.xml, and the inspector takes it for a parse error; the part was removed together with its reference in .rels and its Override. A file without app.xml reads as processed, and the profile doesn't hide that.":
        "пустой app.xml Office не пишет, а инспектор принимает за ошибку разбора; часть убрана вместе со ссылкой в .rels и Override. Файл без app.xml читается как обработанный -- этого профиль не скрывает.",
    "Office never writes an empty core.xml, and the inspector takes it for a parse error; the part was removed together with its reference in .rels and its Override.":
        "пустой core.xml Office не пишет, а инспектор принимает за ошибку разбора; часть убрана вместе со ссылкой в .rels и Override.",
    "Office sets 1980-01-01 00:00 on every entry. Real times mean the package was built by a library: the working time leaks through the container even with a clean core.xml. Normalized to Word's form (this isn't substituting the document's date but a container artifact).":
        "Office ставит всем записям 1980-01-01 00:00. Реальное время означает, что пакет собран библиотекой: время работы утекает через контейнер даже при вычищенном core.xml. Приведено к виду Word (это не подмена даты документа, а контейнерный артефакт).",
    "Office stamps every entry with 1980-01-01 00:00. Real dates mean the package was built or rebuilt by a library: the time spent working on the file leaks even when docProps/core.xml is completely scrubbed.":
        "Office ставит всем записям 1980-01-01 00:00. Реальные даты означают, что пакет собран или пересобран библиотекой: время работы над файлом утекает даже при полностью вычищенном docProps/core.xml.",
    "Often differs from the author and reveals a second person in the chain.":
        "Часто не совпадает с автором и выдаёт второго человека в цепочке.",
    "Often holds the original file name or a project name.":
        "Нередко хранит исходное имя файла или название проекта.",
    "One %%EOF: the file was written in a single pass.":
        "Один %%EOF -- файл писали одним проходом.",
    "Only inspection is available for it right now: it shows what's in the file but removes nothing. There is no cleaned copy; don't send this file as cleaned.":
        "Сейчас для него доступна только инспекция: она показывает, что лежит в файле, но ничего не убирает. Почищенной копии нет, не отправляйте этот файл как почищенный.",
    "Open action":
        "Действие при открытии",
    "Optional schema attribute.":
        "Необязательный атрибут схемы.",
    "Organization":
        "Организация",
    "Orientation (frame orientation)":
        "Orientation (ориентация кадра)",
    "Original author":
        "Первоначальный автор",
    "Original values that survived cleaning: %d":
        "Исходных значений, пережившихся чистку: %d",
    "Other (signal kind not specified)":
        "Прочее (вид сигнала не указан)",
    "Other APP0 segment (%s)":
        "Прочий сегмент APP0 (%s)",
    "Other EXIF tags":
        "Прочие теги EXIF",
    "Other XMP fields (%d)":
        "Прочие поля XMP (%d)",
    "Other tags (%d)":
        "Прочие теги (%d)",
    "Other: ":
        "Прочее: ",
    "Outline (bookmarks)":
        "Оглавление (закладки)",
    "Output: ":
        "Результат: ",
    "PARANOID removes all custom properties.":
        "PARANOID убирает все пользовательские свойства.",
    "PARANOID removes all provenance (size, part composition, sheet and slide names).":
        "PARANOID убирает всё из провенанса (объём, состав частей, названия листов и слайдов).",
    "PARANOID removes all provenance: the file reads as processed, and the profile doesn't hide that.":
        "PARANOID убирает всё из провенанса: файл читается как обработанный -- этого профиль не скрывает.",
    "PARANOID removes all rsids: they link documents of one Word installation. A document that claims Word and has no rsid at all reads as not created by Word, and our inspector detects that (the ai signal).":
        "PARANOID убирает все rsid: они связывают документы одной инсталляции Word. Документ, заявляющий Word и не имеющий ни одного rsid, читается как созданный не Word -- наш инспектор это детектирует (сигнал ai).",
    "PARANOID removes everything except structural declarations.":
        "PARANOID убирает всё, кроме структурных деклараций.",
    "PARANOID removes everything: the file reads as processed (a Word PDF without Creator/Producer) -- the profile does not hide that.":
        "PARANOID убирает всё: файл читается как обработанный (Word-PDF без Creator/Producer) -- этого профиль не скрывает.",
    "PARANOID removes rsid. The file may then look built by a library rather than Word.":
        "PARANOID убирает rsid. Файл после этого может выглядеть собранным библиотекой, а не Word.",
    "PARANOID removes rsids. ":
        "PARANOID убирает rsid. ",
    "PARANOID removes the application and version: without them the file reads as processed (Word always writes app.xml with Application), and the profile doesn't hide that.":
        "PARANOID убирает приложение и версию: файл без них читается как обработанный (Word всегда пишет app.xml с Application) -- этого профиль не скрывает.",
    "PARANOID removes the application string.":
        "PARANOID убирает строку приложения.",
    "PARANOID removes the embedded OLE object entirely together with the object in the text: it has its own docProps (author, company, path), and OLE2 can't be cleaned in place.":
        "PARANOID удаляет вложенный OLE-объект целиком вместе с объектом в тексте: у него свои docProps (автор, компания, путь), а OLE2 на месте не чистится.",
    "PARANOID removes the macros entirely: they contain source code, module names and the project path. Document functionality that relied on them is lost.":
        "PARANOID удаляет макросы целиком: в них исходники, имена модулей и путь проекта. Функциональность документа, которая на них держалась, потеряна.",
    "PARANOID: /ID removed altogether (it is optional). In many producers ID[0] is derived from the file path and name and links files; a file without /ID looks processed.":
        "PARANOID: /ID убран совсем (он необязателен). У многих продюсеров ID[0] выведен из пути и имени файла и связывает файлы; файл без /ID выглядит обработанным.",
    "PARANOID: annotations are removed entirely (author, text, dates, popup). In the regular profile they stay and only the author is removed.":
        "PARANOID: аннотации удаляются целиком (авторство, текст, даты, popup). В обычном профиле остаются, убирается только авторство.",
    "PARANOID: the form field value. The viewer will redraw the field's appearance.":
        "PARANOID: значение поля формы. Внешний вид поля перерисует просмотрщик.",
    "PARANOID: the packet is empty, the stream is removed entirely.":
        "PARANOID: пакет пуст, поток убран целиком.",
    "PDF version (%PDF-)":
        "Версия PDF (%PDF-)",
    "PDF version (as parsed)":
        "Версия PDF (по разбору)",
    "PDF version can't be read: %s":
        "Версия PDF не читается: %s",
    "PNG text chunk. Written by both people and generators: prompts, model parameters and the path to the source file end up here too":
        "Текстовый чанк PNG. Пишут и люди, и генераторы: сюда же складывают промпты, параметры моделей и путь к исходнику",
    "PNG: bad CRC in chunk %s":
        "PNG: неверная CRC чанка %s",
    "PNG: chunk %s declares %d bytes":
        "PNG: чанк %s объявляет %d байт",
    "PNG: chunk %s is truncated (needs %d bytes, have %d)":
        "PNG: чанк %s обрезан (нужно %d байт, есть %d)",
    "PNG: chunk %s was not parsed (%s: %s)":
        "PNG: чанк %s не разобран (%s: %s)",
    "PNG: no image data (IDAT), nothing to clean":
        "PNG: нет данных изображения (IDAT), чистить нечего",
    "PNG: reached the limit of %d chunks":
        "PNG: достигнут предел в %d чанков",
    "PREVIOUS VERSIONS of the document PHYSICALLY REMAIN in the file: old objects, old xrefs and, in particular, text under \"redacted\" black boxes: the box is drawn on top, while the text stays in the previous revision and can be found with an ordinary text search. Re-saving with linearize (or a full rewrite of the objects) drops all previous revisions; that's how to remove them.":
        "В файле ФИЗИЧЕСКИ ЛЕЖАТ ПРЕДЫДУЩИЕ ВЕРСИИ документа: старые объекты, старые xref и, в частности, текст под «заредактированными» чёрными прямоугольниками -- прямоугольник рисуется поверх, а текст остаётся в предыдущей ревизии и достаётся обычным текстовым поиском. Пересохранение с linearize (или полная перезапись объектов) выбрасывает все прошлые ревизии -- это и есть способ их убрать.",
    "Package digital signature":
        "Цифровая подпись пакета",
    "Package signature":
        "Подпись пакета",
    "Page %d can't be read: %s":
        "Страница %d не читается: %s",
    "Page %d: %s can't be read: %s":
        "Стр. %d: %s не читается: %s",
    "Page %d: %s: %s":
        "Стр. %d: %s: %s",
    "Page %d: /Annots can't be read: %s":
        "Стр. %d: /Annots не читается: %s",
    "Page %d: /PieceInfo can't be read: %s":
        "Стр. %d: /PieceInfo не читается: %s",
    "Page %d: /Resources /Font can't be read: %s":
        "Стр. %d: /Resources /Font не читается: %s",
    "Page %d: active content can't be read: %s":
        "Стр. %d: активное содержимое не читается: %s",
    "Page %d: an annotation can't be read: %s":
        "Стр. %d: аннотация не читается: %s",
    "Page %d: annotation appearance (/AP) can't be read: %s":
        "Стр. %d: внешний вид аннотаций (/AP) не читается: %s",
    "Page actions":
        "Действия страницы",
    "Page numbering (/PageLabels)":
        "Нумерация страниц (/PageLabels)",
    "Page size":
        "Формат страницы",
    "Pages":
        "Страниц",
    "Pages / objects":
        "Страниц / объектов",
    "Pages — pages":
        "Pages — страниц",
    "Paper size %s: the document was prepared for the North American standard":
        "Формат бумаги %s -- документ готовили под североамериканский стандарт",
    "Paper size gives away the country: Letter means US/Canada, A4 everywhere else. This works even if every date and name has been removed.":
        "Формат бумаги выдаёт страну: Letter -- США/Канада, A4 -- всё остальное. Это работает, даже если все даты и имена вычищены.",
    "Paragraphs":
        "Абзацев",
    "Paragraphs — paragraphs":
        "Paragraphs — абзацев",
    "Parsed stream size":
        "Размер разобранного потока",
    "Parsing didn't finish (errors below), so an empty result here does <b>not</b> mean the file is clean: anything could be in the unread parts. Don't judge the file's cleanliness by this report.":
        "Разбор дошёл не до конца (ошибки ниже), поэтому пустой результат здесь <b>не</b> значит, что файл чист: в непрочитанных частях может лежать что угодно. Судить о чистоте файла по такому отчёту нельзя.",
    "Parsing interrupted: %s: %s":
        "Разбор прерван: %s: %s",
    "Part titles":
        "Названия частей",
    "Part titles contained personal data: removed together with HeadingPairs.":
        "Названия частей содержали личные данные: убраны вместе с HeadingPairs.",
    "Password hash and salt. Removing them lifts the protection itself, so they cannot be removed without losing functionality.":
        "Хеш и соль пароля. Удаление снимает саму защиту, поэтому убирать нельзя без потери функциональности.",
    "Photoshop resources (%d)":
        "Ресурсы Photoshop (%d)",
    "Photoshop thumbnail":
        "Миниатюра Photoshop",
    "Physical size: sometimes gives away the scanner's original DPI":
        "Физический размер: иногда выдаёт исходный DPI сканера",
    "Pictures are put into the container AS IS, together with their own Exif: GPS coordinates, camera model, serial number and shooting date. The Exif of each picture is shown as separate Pictures/... findings and cleaned in a separate pass.":
        "Картинки кладутся в контейнер КАК ЕСТЬ, вместе со своим Exif: GPS-координатами, моделью камеры, серийным номером и датой съёмки. Exif каждой картинки показан отдельными находками Pictures/... и чистится отдельным проходом.",
    "Pictures lie in the RTF as they are, together with their own Exif: GPS coordinates, camera model, serial number, shooting date. Cleaning the document properties doesn't touch them.":
        "Картинки лежат в RTF как есть, вместе со своим Exif: GPS-координаты, модель камеры, серийный номер, дата съёмки. Чистка свойств документа их не касается.",
    "Pivot tables":
        "Сводные таблицы",
    "Pixel data, palette and transparency copied without re-encoding; CRCs recomputed":
        "Пиксельные данные, палитра и прозрачность скопированы без перекодирования; CRC пересчитаны",
    "Pixel density":
        "Плотность пикселей",
    "Power Query connection":
        "Подключение Power Query",
    "Power Query stores the data source and the path to it.":
        "Power Query хранит источник данных и путь к нему.",
    "PowerPoint writes the user name into a separate stream of the container. It isn't shown in the document properties and stays in place after they are cleaned.":
        "PowerPoint пишет имя пользователя в отдельный поток контейнера. Оно не показывается в свойствах документа и остаётся на месте после их очистки.",
    "Presentation stream":
        "Поток презентации",
    "Presentation target":
        "Целевой формат показа",
    "Preview":
        "Превью",
    "Preview thumbnail":
        "Миниатюра предпросмотра",
    "Print ICC profile. Kept: without it color rendering breaks. The profile name sometimes names the print shop, though.":
        "ICC-профиль печати. Сохраняем: без него ломается цветопередача. Имя профиля при этом иногда называет типографию.",
    "Print color profile (ICC): without it color reproduction breaks.":
        "Цветовой профиль печати (ICC): без него ломается цветопередача.",
    "Print date":
        "Дата печати",
    "Printed by":
        "Кто печатал",
    "Printer name and local paths. Other settings are untouched.":
        "Имя принтера и локальные пути. Остальные настройки на месте.",
    "Printer settings":
        "Настройки принтера",
    "Printer settings (DEVMODE)":
        "Настройки принтера (DEVMODE)",
    "Printer: %s":
        "Принтер: %s",
    "Private application data (Acrobat/Distiller/Illustrator), sometimes with an editable source.":
        "Приватные данные приложения (Acrobat/Distiller/Illustrator), иногда с редактируемым исходником.",
    "Private chunk: written by a specific application, the content is arbitrary":
        "Приватный чанк: пишет конкретное приложение, содержимое произвольное",
    "Producer (writing library)":
        "Producer (библиотека-писатель)",
    "Producer not recognized by the fingerprint database: Creator=%r, Producer=%r":
        "Продюсер не опознан по базе отпечатков: Creator=%r, Producer=%r",
    "Profile in hex form (%s)":
        "Профиль в hex-виде (%s)",
    "Profile parts":
        "Частей профиля",
    "Profile: %s":
        "Профиль: %s",
    "Profile: %s. Neutral names: %s.":
        "Профиль: %s. Нейтральные имена: %s.",
    "Proof":
        "Доказательство",
    "Proof: no original sensitive value was found in the output. The comparison uses values, not counters.":
        "Доказательство: ни одно исходное чувствительное значение не найдено в результате. Сверка идёт значениями, а не счётчиками.",
    "Proprietary camera block. We don't look inside, but this is usually where the unit's serial number, the frame counter and personal settings live":
        "Проприетарный блок камеры. Внутрь не лезем, но именно там обычно лежат серийный номер экземпляра, счётчик кадров и персональные настройки",
    "Proprietary camera block: the unit's serial number, shutter count, settings":
        "Проприетарный блок камеры: серийник экземпляра, счётчик затвора, настройки",
    "Provenance and application":
        "Провенанс и приложение",
    "Provenance: STEALTH keeps it, PARANOID removes it.":
        "Провенанс: STEALTH оставляет, PARANOID убирает.",
    "Python/CLI PDF post-processing library":
        "python/CLI-библиотека постобработки PDF",
    "Quantization tables":
        "Таблицы квантования",
    "Query table (Power Query)":
        "Таблица запроса (Power Query)",
    "RESULT NOT VERIFIED: the file can't be claimed clean.":
        "РЕЗУЛЬТАТ НЕ ПРОВЕРЕН: утверждать, что файл чист, нельзя.",
    "REVEAL A PERSON OR MACHINE: %d":
        "ВЫДАЮТ ЛИЧНОСТЬ ИЛИ МАШИНУ: %d",
    "RGB in JPEG would be read as YCbCr":
        "RGB в JPEG прочтётся как YCbCr",
    "RIFF declares %d bytes, the file has %d: the file is truncated":
        "RIFF объявляет %d байт, в файле %d: файл обрезан",
    "RTF generator: %s":
        "Генератор RTF: %s",
    "RTF stream":
        "поток RTF",
    "Raw byte scan failed: %s: %s":
        "Сырой скан байтов не удался: %s: %s",
    "Raw scan: the file can't be read: %s":
        "Сырой скан: файл не читается: %s",
    "Re-inspection: no original identity or environment value was found in the result.":
        "Повторная инспекция: ни одно исходное значение личности и окружения в результате не найдено.",
    "Reader Extensions rights":
        "права Reader Extensions",
    "Real save time of entries":
        "Реальное время сохранения записей",
    "Reason: %s":
        "Причина: %s",
    "Regenerate (LibreOffice)":
        "Пересоздать (LibreOffice)",
    "Regenerate all (LibreOffice)":
        "Пересоздать всё (LibreOffice)",
    "Regenerate doesn't apply to this extension.":
        "Regenerate для этого расширения не применяется.",
    "Regeneration can't do \"Keep\": LibreOffice writes the file anew. Turn the toggles off or choose Stealth/Paranoid.":
        "Пересоздание не умеет «Сохранить»: LibreOffice пишет файл заново. Снимите переключатели или выберите Stealth/Paranoid.",
    "Relationships (.rels)":
        "Отношения (.rels)",
    "Reminder: the <b>file name</b> itself is metadata too. It doesn't look like a technical auto-generated name, so when you pass the file on, it's worth renaming it separately.":
        "Напоминание: само <b>имя файла</b> -- тоже метаданные. Оно не похоже на техническое авто-имя, так что при дальнейшей передаче файла его стоит поменять отдельно.",
    "Report for each file (with the original names).":
        "Отчёт по каждому файлу (с исходными именами).",
    "ReportLab Python library":
        "python-библиотека ReportLab",
    "Required Word parts are missing":
        "Отсутствуют обязательные части Word",
    "Residual text and revision author table":
        "Остаточный текст и таблица авторов правок",
    "Result: ":
        "Итог: ",
    "Revision and comment dates (w:date, dateUtc)":
        "Даты правок и комментариев (w:date, dateUtc)",
    "Revision author":
        "Автор правки",
    "Revision author (SttbfRMark)":
        "Автор правки (SttbfRMark)",
    "Revision author (w:ins/w:del)":
        "Автор исправления (w:ins/w:del)",
    "Revision author names blanked, comments taken out: the inspector counts comment text as an identity leak.":
        "Имена авторов правок обнулены, комментарии вынуты: текст комментария инспектор считает утечкой личности.",
    "Revision authors (the rest)":
        "Авторы исправлений (остальные)",
    "Revision dates":
        "Даты исправлений",
    "Revision number":
        "Номер ревизии",
    "Revision or comment author":
        "Автор исправления или комментария",
    "Rich text /RC":
        "Форматированный текст /RC",
    "Root \"%s\" wasn't recognized, and there are values inside. It can't be removed blindly: content control data binding (w:dataBinding) relies on customXml, and blanking the part would erase visible document text. The values remain in the file.":
        "Корень «%s» не опознан, а внутри есть значения. Убрать вслепую нельзя: на customXml опирается привязка элементов содержимого (w:dataBinding), и обнуление части стёрло бы видимый текст документа. Значения остались в файле.",
    "Root storage CLSID":
        "CLSID корневого хранилища",
    "SIGNALS":
        "ВЫВОДЫ",
    "SIGNATURE DESTROYED: any metadata cleaning breaks the signature, and force_signed=True allowed it. The field is left as an empty placeholder.":
        "ПОДПИСЬ УНИЧТОЖЕНА: любая чистка метаданных ломает подпись, force_signed=True это разрешил. Поле осталось пустой заготовкой.",
    "SOF not found":
        "SOF не найден",
    "SOF, DHT, DQT, DRI and the entropy-coded scan data were copied without re-encoding":
        "SOF, DHT, DQT, DRI и энтропийные данные сканов скопированы без перекодирования",
    "STEALTH: Word always writes cp:revision; its absence or zeroing is a cleaning trace. PARANOID removes it.":
        "STEALTH: Word пишет cp:revision всегда, его отсутствие или обнуление -- признак чистки. PARANOID убирает.",
    "STEALTH: dates are left alone. Collapsing them into one second is a cleaning trace, and substituting a date is forging provenance.":
        "STEALTH: даты не трогаем. Обнуление в одну секунду -- признак чистки, а подмена даты -- фальсификация провенанса.",
    "STEALTH: dates are left alone. CreationDate == ModDate (zeroing to the same second) is a sign of scrubbing, and faking a date falsifies provenance.":
        "STEALTH: даты не трогаем. CreationDate == ModDate (обнуление в одну секунду) -- признак чистки, а подмена даты -- фальсификация провенанса.",
    "STEALTH: document size and flags are left alone; this is provenance without personal data.":
        "STEALTH: объём документа и флаги не трогаем, это провенанс без личных данных.",
    "STEALTH: kept as is. The file really was made by this application, and an empty or substituted Application with the part structure intact is an instant sign of processing.":
        "STEALTH: оставлено как есть. Файл действительно сделан этим приложением, а пустой или подменённый Application при сохранённой структуре частей -- мгновенный признак обработки.",
    "STEALTH: kept. These are random session IDs without direct personal data, and their total absence in a document claiming Word means \"not created by Word\". Removing rsids is a PARANOID option.":
        "STEALTH: оставлено. Это случайные ID сессий без прямых личных данных, а их полное отсутствие в документе, заявляющем Word, означает «создан не Word». Удаление rsid -- опция PARANOID.",
    "STEALTH: left as it was. The file really was made by this application, and a Creator=Word + Producer=qpdf pair immediately shows that the file was processed.":
        "STEALTH: оставлено как было. Файл действительно сделан этим приложением, а пара вида Creator=Word + Producer=qpdf сразу выдаёт, что файл обрабатывали.",
    "STEALTH: provenance is left alone. A zero value in a large document is a cleaning trace, and an invented one is forged provenance; PARANOID removes the real editing time.":
        "STEALTH: провенанс не трогаем. Нулевое значение у большого документа -- признак чистки, а выдуманное -- подделка провенанса; реальное время редактирования убирает PARANOID.",
    "STEALTH: the application and version are the truth about the file. An empty generator reads as a cleaning trace.":
        "STEALTH: приложение и версия -- правда о файле. Пустой генератор читается как след чистки.",
    "STEALTH: the truth about the file, like Application/AppVersion; without it a workbook from Excel looks processed.":
        "STEALTH: правда о файле, как Application/AppVersion; без неё книга от Excel выглядит обработанной.",
    "STRUCTURAL: affects color or printed size, kept in every profile":
        "STRUCTURAL: влияет на цвет или размер при печати, сохраняется в любом профиле",
    "STRUCTURAL: background, significant bits and HDR tags affect how the picture is shown":
        "STRUCTURAL: фон, значимые биты и HDR-метки влияют на показ картинки",
    "STRUCTURAL: needed for correct color rendering":
        "STRUCTURAL: нужен для правильной цветопередачи",
    "STRUCTURAL: needed for correct color rendering. The profile name is sometimes named after a monitor or printer; kept deliberately; use PARANOID for full cleanliness":
        "STRUCTURAL: нужен для правильной цветопередачи. Имя профиля иногда названо по монитору или принтеру -- оставлено осознанно; для полной чистоты используйте PARANOID",
    "STRUCTURAL: needed for correct color rendering. The profile name is sometimes named after a monitor or scanner; kept deliberately; use PARANOID for full cleanliness":
        "STRUCTURAL: нужен для правильной цветопередачи. Имя профиля иногда названо по монитору или сканеру -- оставлено осознанно; для полной чистоты используйте PARANOID",
    "STRUCTURAL: needed for correct color rendering. The profile name is sometimes named after the device; kept deliberately; use PARANOID for full cleanliness":
        "STRUCTURAL: нужен для правильной цветопередачи. Имя профиля иногда названо по устройству -- оставлено осознанно; для полной чистоты используйте PARANOID",
    "STRUCTURAL: pixel density sets the printed size, and having neither EXIF nor JFIF stands out by itself. Kept deliberately":
        "STRUCTURAL: плотность пикселей нужна для размера при печати, а отсутствие и EXIF, и JFIF само заметно. Оставлен осознанно",
    "STRUCTURAL: the shot is stored rotated (%s). Without this tag the picture would display on its side. Physical rotation would require re-encoding, so one tag is kept and the rest removed":
        "STRUCTURAL: снимок лежит в файле повёрнутым (%s). Без этого тега картинка отобразится на боку. Физический поворот потребовал бы перекодирования, поэтому оставлен один тег, остальное удалено",
    "Save date (%s) is earlier than creation date (%s): the metadata was edited by hand or the system clock was changed.":
        "Дата сохранения (%s) раньше даты создания (%s): метаданные правили вручную или переносили системное время.",
    "Saved without linearization: the first xref section of a linearized file has /Prev, and linearization itself is an Acrobat sign.":
        "Сохранено без линеаризации: в первой xref-секции линеаризованного файла /Prev, а сама линеаризация -- признак Acrobat.",
    "Says the file went through Adobe software. For default YCbCr the colors won't change":
        "Говорит, что файл прошёл через софт Adobe. Для YCbCr по умолчанию цвета не изменятся",
    "Schema references (schemaRef)":
        "Ссылки на схемы (schemaRef)",
    "Screen readers and hyphenation need the language, so it's kept. But by itself it reveals the language settings of the author's machine (ru-RU, en-GB).":
        "Язык нужен скринридерам и переносам -- сохраняем. Но сам по себе он выдаёт языковые настройки машины автора (ru-RU, en-GB).",
    "Scripts that fire when a page is opened/closed.":
        "Скрипты, срабатывающие при открытии/закрытии страницы.",
    "Section \"%s\": %s":
        "Секция «%s»: %s",
    "Section \"%s\": %s: %s":
        "Секция «%s»: %s: %s",
    "Section %s: %s: %s":
        "Секция %s: %s: %s",
    "Section headings and the sheet names of a workbook.":
        "Заголовки разделов и имена листов книги.",
    "Security flag":
        "Флаг защиты",
    "Segment map (%d)":
        "Карта сегментов (%d)",
    "Sensitivity label (LabelInfo.xml)":
        "Метка конфиденциальности (LabelInfo.xml)",
    "Sequence of GIF blocks":
        "Последовательность блоков GIF",
    "Session table":
        "Таблица сеансов",
    "SharePoint library columns: employee names, dates, the portal address and the organization's content type.":
        "Колонки библиотеки SharePoint: имена сотрудников, даты, адрес портала и тип контента организации.",
    "SharePoint metadata (customXml)":
        "Метаданные SharePoint (customXml)",
    "SharePoint/EDMS part (empty)":
        "Часть SharePoint/СЭД (пустая)",
    "SharePoint/EDMS property: a tie to a portal and library.":
        "Свойство SharePoint/СЭД: привязка к порталу и библиотеке.",
    "Shared workbook revision log (%s)":
        "Журнал изменений общей книги (%s)",
    "Shared-workbook participant (xl/revisions)":
        "Участник общей книги (xl/revisions)",
    "SharedDoc — shared":
        "SharedDoc — общий доступ",
    "Sheet and presentation protection":
        "Защита листов и презентации",
    "Sheet names, including hidden and very hidden ones.":
        "Имена листов, включая скрытые и очень скрытые.",
    "Shooting parameters and geometry: together they fingerprint a specific camera":
        "Параметры съёмки и геометрия: вместе фингерпринтят конкретную камеру",
    "Shows whether the author finished the spell check.":
        "Показывает, дочитал ли автор проверку орфографии.",
    "Signals section: %s: %s":
        "Секция сигналов: %s: %s",
    "Signature field \"%s\"":
        "Поле подписи «%s»",
    "Signature format":
        "Формат подписи",
    "Signature handler":
        "Обработчик подписи",
    "Signature validation data: the signers' certificate chains.":
        "Данные валидации подписи: цепочки сертификатов подписантов.",
    "Signer":
        "Подписант",
    "Signer certificate: issuer":
        "Сертификат подписанта: издатель",
    "Signer certificate: subject":
        "Сертификат подписанта: владелец",
    "Signer contact":
        "Контакты подписанта",
    "Significant bits":
        "Значащие биты",
    "Signing location":
        "Место подписания",
    "Signing reason":
        "Причина подписания",
    "Signing time":
        "Время подписания",
    "Signs of earlier cleaning: %s. Check the other places (ZIP, settings.xml, rels) — they are usually forgotten.":
        "Признаки предыдущей чистки: %s. Проверьте остальные площадки (ZIP, settings.xml, rels) — их обычно забывают.",
    "Signs of earlier scrubbing":
        "Признаки предыдущей чистки",
    "Signs of library or AI generation":
        "Признаки генерации библиотекой или ИИ",
    "Size:    %s":
        "Размер:  %s",
    "Slides":
        "Слайдов",
    "Slides with notes":
        "Слайдов с заметками",
    "Slides — slides":
        "Slides — слайдов",
    "Sometimes holds a printer name or a screen model.":
        "Иногда содержит имя принтера или модель экрана.",
    "Speaker notes are a separate source of text leaks.":
        "Заметки докладчика -- отдельный источник утечки текста.",
    "Standard animation extension":
        "Штатное расширение анимации",
    "Statistics and flags":
        "Статистика и флаги",
    "Stores macro sources, module names and often the project path on the author's machine.":
        "Хранит исходники макросов, имена модулей и нередко путь к проекту на машине автора.",
    "Structural (usually kept)":
        "Структурное (обычно сохраняем)",
    "Structural declarations (PDF/A, PDF/UA).":
        "Структурные декларации (PDF/A, PDF/UA).",
    "Structural: kept in every profile. ":
        "Структурное: сохранено во всех профилях. ",
    "Structural: spell checking and screen readers need the document language.":
        "Структурное: язык документа нужен проверке орфографии и скринридерам.",
    "Structure":
        "Структура",
    "Structure not recognized: to be parsed by hand in phase 2":
        "Структуру не опознали: разбирать вручную на фазе 2",
    "Structure tree (tagged PDF)":
        "Дерево структуры (тегированный PDF)",
    "SttbfRMark could not be read: %r":
        "SttbfRMark не читается: %r",
    "SttbfRMark is shorter than declared in the FIB: %d of %d":
        "SttbfRMark короче, чем заявлено в FIB: %d из %d",
    "Styles: document content.":
        "Стили: содержимое документа.",
    "Styles: workbook content.":
        "Стили: содержимое книги.",
    "Subject":
        "Тема",
    "Subject from Windows Explorer (UTF-16LE)":
        "Тема из проводника Windows (UTF-16LE)",
    "Subset prefixes":
        "Префиксы сабсетов",
    "SummaryInformation stream":
        "Поток SummaryInformation",
    "Suspected zip bomb: %d bytes uncompressed from %d compressed. File not cleaned.":
        "Подозрение на zip-бомбу: распакованный объём %d байт при сжатом %d. Файл не очищен.",
    "Suspected zip bomb: uncompressed size %d bytes, compression ratio %.0f. The contents of the parts were not decompressed.":
        "Подозрение на zip-бомбу: несжатый объём %d байт, коэффициент сжатия %.0f. Содержимое частей не распаковывалось.",
    "TIFF cleaning isn't supported yet: the metadata lives in the IFDs themselves together with references to the data, and rebuilding without dedicated code risks damaging the file":
        "TIFF пока не поддержан чисткой: метаданные лежат в самих IFD вместе со ссылками на данные, пересборка без специального кода рискует испортить файл",
    "Table of contents (bookmarks): navigation through the document.":
        "Оглавление (закладки): навигация по документу.",
    "Tabloid (US)":
        "Tabloid (США)",
    "Tag not kept: viewers don't use this EXIF block (it comes after the image data, repeats one already kept, or sits in a WebP without VP8X)":
        "Тег не сохранён: этот EXIF-блок просмотрщики не используют (стоит после данных изображения, повторяет уже сохранённый или лежит в WebP без VP8X)",
    "Tagging marker; goes together with /StructTreeRoot.":
        "Пометка о тегировании, идёт вместе с /StructTreeRoot.",
    "TargetMode=External, type %s":
        "TargetMode=External, тип %s",
    "TeX banner":
        "Баннер TeX",
    "TeX toolchain":
        "TeX-тулчейн",
    "Technical shooting parameters and geometry. Not PII on their own, but together they fingerprint a specific camera and its settings":
        "Технические параметры съёмки и геометрия. Сами по себе не PII, но вместе фингерпринтят конкретную камеру и её настройки",
    "Telegram did not accept the token from TG_BOT_TOKEN.\nCheck .env: the whole token from @BotFather, of the form 123456789:AAH..., with no spaces or quotes around it. If the token was reissued (/revoke), you need the new one.\n":
        "Telegram не принял токен из TG_BOT_TOKEN.\nПроверьте .env: токен от @BotFather целиком, вида 123456789:AAH..., без пробелов и кавычек вокруг. Если токен перевыпускали (/revoke), нужен новый.\n",
    "Template":
        "Шаблон",
    "Template and auto-reload store a path in the xlink:href attribute: usually file:///C:/Users/NAME/... or a network share.":
        "Шаблон и автоперезагрузка хранят путь в атрибуте xlink:href: обычно это file:///C:/Users/ИМЯ/... или сетевой ресурс.",
    "Template name":
        "Название шаблона",
    "Template name. ":
        "Имя шаблона. ",
    "Template path. An empty group looks like a file without a template.":
        "Путь к шаблону. Пустая группа -- как у файла без шаблона.",
    "Template — template":
        "Template — шаблон",
    "Text chunk ":
        "Текстовый чанк ",
    "Text comment. Encoders write their own name and version here, people write anything":
        "Текстовый комментарий. Кодировщики пишут сюда своё имя и версию, люди -- всё что угодно",
    "Text/CSV":
        "Текст/CSV",
    "Text/CSV: such a file has no structured document metadata; cleaning this format is not planned.":
        "Текст/CSV: структурированных метаданных документа в таком файле нет, чистка этого формата в плане не предусмотрена.",
    "That isn't necessarily good news: a clean file is itself a sign that it has already been processed. Normally created documents almost always carry at least the application and dates.":
        "Это не обязательно хорошая новость: чистый файл сам по себе является признаком того, что его уже обрабатывали. Штатно созданные документы почти всегда несут хотя бы приложение и даты.",
    "The %s prefix tags a glyph subset. Prefixes are generated deterministically by a specific producer and match across files built by the same installation.":
        "Префикс %s -- метка подмножества глифов. Префиксы генерируются детерминированно конкретным продюсером и совпадают у файлов, собранных одной установкой.",
    "The 'Manager' field in the document properties.":
        "Поле 'Руководитель' в свойствах документа.",
    "The .doc format physically stores more than it shows. The table stream (%s) holds the SttbfRMark revision author table: a list of EVERYONE who ever edited the document, even if the changes were accepted long ago. When saving in fast-save mode Word doesn't rewrite the file from scratch but appends the changes at the end: the DELETED TEXT of earlier versions stays in the file physically and can be extracted with any hex editor. On top of that, unused sectors of the OLE container could previously have belonged to OTHER files on the same disk, and their fragments travel with the document. This cannot be cleaned by editing the properties.":
        "Формат .doc физически хранит больше, чем показывает. В потоке таблицы (%s) лежит таблица авторов правок SttbfRMark -- список ВСЕХ, кто когда-либо редактировал документ, даже если сами правки давно приняты. При сохранении в режиме быстрого сохранения Word не переписывает файл заново, а дописывает изменения в конец: УДАЛЁННЫЙ ТЕКСТ предыдущих версий остаётся в файле физически и извлекается любым hex-редактором. Сверх того, неиспользованные секторы OLE-контейнера ранее могли принадлежать ДРУГИМ файлам с того же диска, и их фрагменты уезжают вместе с документом. Вычистить это редактированием свойств невозможно.",
    "The /Metadata stream expands beyond %d MB or its size can't be bounded: not decompressed, bomb protection.":
        "Поток /Metadata при распаковке превысит %d МБ или его размер не удаётся ограничить -- не распаковываем, защита от бомбы.",
    "The /Metadata stream is too large (%d bytes compressed): not decompressed, bomb protection.":
        "Поток /Metadata слишком велик (%d байт в сжатом виде) -- не распаковываем, защита от бомбы.",
    "The Excel serial date of the cache refresh.":
        "Серийная дата Excel обновления кэша.",
    "The Excel version that worked with the pivot.":
        "Версия Excel, работавшая со сводной.",
    "The FIB structure version number: it shows exactly which version of Word wrote the file last.":
        "Номер версии структуры FIB -- по нему видно, какая именно версия Word писала файл последней.",
    "The ICC profile is split across several APP2 segments; the description is read from the first part":
        "ICC-профиль разрезан на несколько APP2; описание читаем из первой части",
    "The Microsoft 365 tenant GUID: uniquely identifies the organization even if everything else is scrubbed.":
        "GUID тенанта Microsoft 365 — однозначно идентифицирует организацию, даже если всё остальное вычищено.",
    "The ODF container is damaged: a part won't unpack (%s). No output created.":
        "ODF-контейнер повреждён: часть не распаковывается (%s). Выход не создан.",
    "The ODF manifest: a list of all parts and their types. Needed to open the file -- kept.":
        "Манифест ODF: список всех частей и их типов. Нужен для открытия файла -- сохраняем.",
    "The OLE class directly names the application that created the container.":
        "Класс OLE прямо называет приложение, создавшее контейнер.",
    "The PDF contains %d revisions: earlier page versions and \"crossed-out\" text can be extracted straight from the file":
        "PDF содержит %d ревизий: прошлые версии страниц и «зачёркнутый» текст извлекаются из файла напрямую",
    "The PDF does not open (%s): the file is damaged or is not a PDF.":
        "PDF не открывается (%s): файл повреждён или это не PDF.",
    "The PDF has %d layers turned off: the hidden content is one click away":
        "В PDF %d выключенных слоёв: скрытое содержимое включается одним кликом",
    "The PDF is digitally signed (%d /FT /Sig field(s)): cleaning the metadata WILL invalidate the signature":
        "PDF подписан электронной подписью (%d поле/полей /FT /Sig): чистка метаданных ГАРАНТИРОВАННО сделает подпись недействительной",
    "The PDF is encrypted and does NOT open with an empty password: content and metadata are unavailable without the owner/user password.":
        "PDF зашифрован и НЕ открывается пустым паролем -- содержимое и метаданные недоступны без пароля владельца/пользователя.",
    "The PDF is encrypted and does not open with an empty password: it cannot be cleaned without the password.":
        "PDF зашифрован и не открывается пустым паролем: чистка невозможна без пароля.",
    "The PDF is encrypted: encrypted files are not cleaned, so as not to strip the protection silently.":
        "PDF зашифрован: чистка зашифрованных файлов не выполняется, чтобы не снять защиту молча.",
    "The PDF is structurally damaged: QPDF refused to parse it":
        "PDF структурно повреждён: QPDF отказался его разбирать",
    "The RTF has no \\info group: the document properties are absent -- either they were cleaned already or the file was written by a program.":
        "В RTF нет группы \\info: свойства документа отсутствуют -- либо их уже чистили, либо файл писала программа.",
    "The TeX Live version string with its build date gives away the author's OS and distribution.":
        "Строка версии TeX Live с датой сборки выдаёт ОС и дистрибутив автора.",
    "The WRITEACCESS record stores the user name from the Excel settings separately from the document properties: it isn't visible in the properties dialog and it survives their cleaning.":
        "Запись WRITEACCESS хранит имя пользователя из настроек Excel отдельно от свойств документа: её не видно в диалоге свойств и она переживает их очистку.",
    "The Windows user name or the signature in the Office settings.":
        "Имя пользователя Windows или подпись в настройках Office.",
    "The Word locale identifier on the machine where the file was saved: indirectly reveals the country and the language build of Office.":
        "Идентификатор локали Word на машине, где сохраняли файл: косвенно выдаёт страну и языковую сборку Office.",
    "The ZIP container cannot be opened: %s: %s":
        "ZIP-контейнер не открывается: %s: %s",
    "The ZIP container doesn't parse: the file is damaged or truncated. File not cleaned.":
        "ZIP-контейнер не разбирается: файл повреждён или обрезан. Файл не очищен.",
    "The ZIP container doesn't parse: the file is damaged, truncated or not a ZIP.":
        "ZIP-контейнер не разбирается: файл повреждён, обрезан или это не ZIP.",
    "The ZIP container won't open: %s.":
        "ZIP-контейнер не открывается: %s.",
    "The ZIP holds real timestamps (%s): they will remain even if meta.xml is cleaned.":
        "В ZIP лежат реальные отметки времени (%s): они останутся даже если очистить meta.xml.",
    "The account name that is allowed to edit the region.":
        "Имя учётной записи, которой разрешено править область.",
    "The address the document itself points to.":
        "Адрес, на который документ сам ссылается.",
    "The annotation tries to launch a program or open a file by a local path: the path reveals the author's machine.":
        "Аннотация пытается запустить программу или открыть файл по локальному пути -- путь выдаёт машину автора.",
    "The application (Acrobat, Distiller, Illustrator, InDesign) stores its service data here, up to an editable source of the document. The key's presence unambiguously names the application the file was edited with.":
        "Приложение (Acrobat, Distiller, Illustrator, InDesign) хранит тут свои служебные данные -- вплоть до редактируемого исходника документа. Наличие ключа однозначно называет приложение, которым файл правили.",
    "The archive has %d files, more than the limit of %d: cleaning not performed. Split it into parts.":
        "В архиве %d файлов, больше предела %d: чистка не выполнена. Разбейте на части.",
    "The archive is empty: it contains no files.":
        "Архив пуст: в нём нет ни одного файла.",
    "The archive is larger than %d MB: cleaning not performed.":
        "Архив больше %d МБ: чистка не выполнена.",
    "The attachment could not be cleaned (%s): metadata inside remains.":
        "Вычистить вложение не удалось (%s): метаданные внутри остались.",
    "The attachment's cleaner removed nothing.":
        "Чистильщик вложения ничего не убирал.",
    "The attachments name tree can't be read: %s":
        "Дерево имён вложений не читается: %s",
    "The author is in /Info, but dc:creator in XMP is empty":
        "Автор есть в /Info, но dc:creator в XMP пуст",
    "The base path for relative hyperlinks -- almost always local.":
        "Базовый путь для относительных гиперссылок -- почти всегда локальный.",
    "The base path for relative links -- usually local.":
        "Базовый путь для относительных ссылок -- обычно локальный.",
    "The block couldn't be parsed (%s) and was removed entirely; the orientation can't be restored":
        "Блок не разобрался (%s) и удалён целиком; ориентацию восстановить нельзя",
    "The block stores object settings and sometimes identifiers of earlier insertions.":
        "Блок хранит настройки объектов и иногда идентификаторы предыдущих вставок.",
    "The bot is already running in another window or process on this machine.\nTwo instances with one token interfere with each other: Telegram hands messages to one, then to the other.\nStop the first (Ctrl+C in its window) and start again.\n":
        "Бот уже запущен в другом окне или процессе на этой машине.\nДва экземпляра с одним токеном мешают друг другу: Telegram отдаёт сообщения то одному, то другому.\nОстановите первый (Ctrl+C в его окне) и запустите снова.\n",
    "The certifying signature was removed together with the others.":
        "Сертифицирующая подпись снята вместе с остальными.",
    "The cleaner reported success without a single action: this can't be trusted.":
        "Чистильщик отчитался об успехе без единого действия: этому нельзя верить.",
    "The cleaner reported success, but there is no output file.":
        "Чистильщик сообщил об успехе, но выходного файла нет.",
    "The cleaning report could not be built (%s). Consider the file NOT cleaned.":
        "Отчёт о чистке не собрался (%s). Считайте файл НЕ почищенным.",
    "The color profile is normally KEPT. But the description name is often that of a specific monitor, printer or scanner -- which already points to a workplace":
        "Цветовой профиль обычно СОХРАНЯЕМ. Но имя описания часто названо по конкретному монитору, принтеру или сканеру -- это уже указание на рабочее место",
    "The color profile is usually kept, but its name is often that of a specific monitor or scanner":
        "Цветовой профиль обычно сохраняем, но его имя нередко названо по конкретному монитору или сканеру",
    "The comment text is document content, the user may have left it deliberately; authorship and dates are removed. To remove the text as well, use the PARANOID profile.":
        "Текст комментариев -- содержимое документа, пользователь мог оставить его осознанно; авторство и даты убраны. Чтобы убрать и текст -- профиль PARANOID.",
    "The comment's popup window: a separate object that drags along the same /T and /Contents.":
        "Всплывающее окно комментария: отдельный объект, который тащит за собой те же /T и /Contents.",
    "The company name from the Office license.":
        "Название компании из лицензии Office.",
    "The company name from the Office settings.":
        "Название компании из настроек Office.",
    "The compressed frame data was copied without re-encoding":
        "Сжатые данные кадров скопированы без перекодирования",
    "The connection string (server, database, login), the query and paths were anonymized: the connection no longer refreshes, but the workbook structure and cached data are intact.":
        "Строка подключения (сервер, база, логин), запрос и пути обезличены: подключение больше не обновляется, но структура книги и кэшированные данные целы.",
    "The container has macros (%s): active content.":
        "В контейнере есть макросы (%s): активное содержимое.",
    "The container has no meta.xml -- the document properties were deleted or never written.":
        "В контейнере нет meta.xml -- свойства документа удалены или никогда не писались.",
    "The container holds a full EXIF -- so most likely GPS, serial numbers and the camera model are there too. An exact parse needs an iloc walk, which phase 0 does not have":
        "В контейнере лежит полноценный EXIF -- значит, там же с высокой вероятностью GPS, серийники и модель камеры. Для точного разбора нужен обход iloc, которого в фазе 0 нет",
    "The content is encrypted with a password; it cannot be parsed without the password.":
        "Содержимое зашифровано паролем; разобрать его без пароля нельзя.",
    "The content is password-protected.":
        "Содержимое защищено паролем.",
    "The content is password-protected: it cannot be parsed.":
        "Содержимое защищено паролем: разобрать его нельзя.",
    "The content of switched-off layers IS in the file and turns on with one click. It can only be removed by rewriting the page content streams -- cleaning does not do that.":
        "Содержимое выключенных слоёв ЛЕЖИТ в файле и включается одним кликом. Убрать его можно только переписыванием потоков содержимого страниц -- чистка этого не делает.",
    "The creating application looks like a library/script (marker '%s'): %s":
        "Приложение-создатель похоже на библиотеку/скрипт (маркер '%s'): %s",
    "The creation date in /Info (%s) doesn't match xmp:CreateDate (%s): one of the sources was edited":
        "Дата создания в /Info (%s) не совпадает с xmp:CreateDate (%s): один из источников правили",
    "The database path, table and connection string with the login and server. The document stops being a mail merge main document; the text and fields stay.":
        "Путь к базе, таблица и строка подключения с логином и сервером. Документ перестаёт быть главным документом слияния; текст и поля остаются.",
    "The declared creation date %s contradicts the font %s: Aptos only appeared in 2023, so the date was rewritten":
        "Заявленная дата создания %s противоречит шрифту %s: Aptos появился только в 2023 году -- дату переписали",
    "The declared creation date %s contradicts the font %s: Calibri only appeared in 2007, so the date was rewritten":
        "Заявленная дата создания %s противоречит шрифту %s: Calibri появился только в 2007 году -- дату переписали",
    "The decoder doesn't need it: an application's private data":
        "Декодеру не нужно: приватные данные приложения",
    "The document body contains saved revisions (track changes) from %d author(s), parts: %s.":
        "В теле документа сохранены исправления (track changes) от %d автор(ов), части: %s.",
    "The document has %d comments with authors: the visible text and the hidden discussion diverge.":
        "В документе %d комментариев с авторами: видимый текст и скрытое обсуждение расходятся.",
    "The document has %d document-level JavaScript script(s)":
        "В документе %d JavaScript-скрипт(ов) уровня документа",
    "The document has %d editing sessions (rsid): the text was put together in at least that many sittings, even if the dates say otherwise.":
        "В документе %d сеансов правки (rsid): текст собирался минимум за столько заходов, даже если даты говорят иначе.",
    "The document has %d event handlers (script:event-listener): active content that runs on opening.":
        "В документе %d обработчиков событий (script:event-listener): активное содержимое, запускающееся при открытии.",
    "The document has %d words, but TotalTime %s: the editing time was zeroed or never set.":
        "В документе %d слов, а TotalTime %s: время редактирования обнулено или не проставлялось.",
    "The document is certified. ANY metadata edit breaks the signature: cleaning and the signature are mutually exclusive.":
        "Документ сертифицирован. ЛЮБАЯ правка метаданных ломает подпись: чистка и подпись взаимоисключающи.",
    "The document is digitally signed (%d): any metadata cleaning would destroy the signature. A signature and cleaning are mutually exclusive; to clean anyway and lose the signature, pass force_signed=True.":
        "Документ подписан цифровой подписью (%d): любая чистка метаданных её уничтожит. Подпись и чистка взаимоисключающи; чтобы всё же очистить и потерять подпись, передайте force_signed=True.",
    "The document is labeled with Microsoft Purview: the label carries the tenant GUID and the email of the employee who applied it.":
        "Документ промаркирован Microsoft Purview: метка несёт GUID тенанта и email применившего её сотрудника.",
    "The document variable holds a person: a full name from a template or an EDMS.":
        "Переменная документа хранит человека: ФИО из шаблона или СЭД.",
    "The document was saved in complex (fast) mode: the text lies in pieces mixed with deleted fragments.":
        "Документ сохранён в сложном (быстром) режиме: текст лежит кусками вперемешку с удалёнными фрагментами.",
    "The document's list of references. This is content visible to the reader, not metadata: cleaning deliberately leaves it alone. The author names here are the authors of the cited works, not yours.":
        "Список литературы документа. Это содержимое, видимое читателю, а не метаданные: чистка его не трогает намеренно. Имена авторов здесь -- авторы цитируемых работ, не ваши.",
    "The document's list of references: visible content, not metadata. The names in it are the authors of the cited works, not yours. Left alone on purpose.":
        "Список литературы документа: видимое содержимое, а не метаданные. Имена в нём -- авторы цитируемых работ, а не ваши. Не трогаем намеренно.",
    "The document's personal strings":
        "Личные строки документа",
    "The element referencing the relationship could not be removed safely: the relationship was kept.":
        "Элемент, ссылающийся на отношение, убрать безопасно не удалось: отношение оставлено.",
    "The embedded item could not be cleaned (%s): the metadata inside remains.":
        "Вычистить вложение не удалось (%s): метаданные внутри остались.",
    "The embedded item's cleaner removed nothing: only structural elements remained (%d).":
        "Чистильщик вложения ничего не убирал: остались только структурные элементы (%d).",
    "The encryption revision and algorithm indirectly date the Acrobat version.":
        "Ревизия и алгоритм шифрования косвенно датируют версию Acrobat.",
    "The exact Excel build (rupBuild). ":
        "Точная сборка Excel (rupBuild). ",
    "The exact application build down to the revision number is a strong fingerprint: it identifies a specific installation and even the distribution.":
        "Точная сборка приложения вплоть до номера ревизии -- сильный отпечаток: по нему опознаётся конкретная установка и даже дистрибутив.",
    "The exact application string and its version -- a strong fingerprint of a specific installation.":
        "Точная строка приложения и его версии -- сильный отпечаток конкретной установки.",
    "The exact application string and its version.":
        "Точная строка приложения и его версии.",
    "The exact moment of every edit: they let one reconstruct the schedule of work on the document.":
        "Точные моменты каждой правки: по ним восстанавливается график работы над документом.",
    "The exact time of each edit.":
        "Точное время каждой правки.",
    "The exact time of every revision. The attribute is optional in the schema; the revisions stay in place.":
        "Точное время каждой правки. Атрибут необязателен по схеме, правки остаются на месте.",
    "The exact time the document was signed.":
        "Точное время подписания документа.",
    "The exact time the label was applied.":
        "Точное время применения метки.",
    "The fact of printing and its exact time.":
        "Сам факт печати и её точное время.",
    "The file contains VBA macros (%s): active content.":
        "Файл содержит макросы VBA (%s): активное содержимое.",
    "The file contains a VBA project (%s): active content, plus a source of metadata separate from the document.":
        "В файле есть проект VBA (%s): активное содержимое, плюс отдельный от документа источник метаданных.",
    "The file has an XFA form: the data is duplicated in XML and survives cleaning of the fields":
        "В файле XFA-форма: данные дублируются в XML и переживают чистку полей",
    "The file has both Calibri and Aptos. Calibri belongs to 2007–2023, Aptos to 2023 or later. Together: the document was edited after Aptos appeared, on top of an older base.":
        "В файле одновременно Calibri и Aptos. Calibri -- окно 2007–2023, Aptos -- не раньше 2023. Вместе документ правили уже после появления Aptos, поверх более старой основы.",
    "The file head, gaps between entries, junk after the end of the archive%s. Office doesn't write them: it's a classic hiding place.":
        "Голова файла, щели между записями, мусор после конца архива%s. Office их не пишет: это классический тайник.",
    "The file is damaged: QPDF had to rebuild its structure. Cleaning not performed, so as not to hand you a file with lost parts.":
        "Файл повреждён: QPDF пришлось восстанавливать структуру. Чистка не выполнена, чтобы не отдать вам файл с потерянными частями.",
    "The file is empty (0 bytes); it may not have finished downloading. File not cleaned.":
        "Файл пуст (0 байт), возможно, не докачался. Файл не очищен.",
    "The file is empty: possibly the download did not finish.":
        "Файл пуст: возможно, не докачался.",
    "The file is encrypted, but the user password is empty: the typical \"print/copy protection\" that protects nothing but records that the file was prepared in Acrobat or a similar editor.":
        "Файл зашифрован, но пароль пользователя пустой: это типичная «защита от печати/копирования», которая ничего не защищает, зато фиксирует, что файл готовили в Acrobat или похожем редакторе.",
    "The file is protected by a user password: inspection is impossible":
        "Файл защищён паролем пользователя: инспекция невозможна",
    "The file name is metadata too, and Telegram keeps it: if the name contains a person's name or a project title, rename the file before sending.":
        "Имя файла — тоже метаданные, и Telegram его сохраняет: если в имени есть ФИО или название проекта, переименуйте файл перед отправкой.",
    "The file was generated programmatically, not \"printed\" by a person from an editor: %s. For a document presented as an office one, that alone is an anomaly.":
        "Файл сгенерирован программно, а не «напечатан» человеком из редактора: %s. Для документа, который выдают за офисный, это само по себе аномалия.",
    "The file was regenerated by LibreOffice: a fresh file has no custom properties, so this one was carried over from the source.":
        "Файл пересоздан LibreOffice: у свежего файла пользовательских свойств нет, это свойство перенесено из исходника.",
    "The file was saved again by LibreOffice: the package was built by a stock application, so its structure is consistent throughout. The claimed application is now LibreOffice, not the original. The layout may have changed.":
        "Файл заново сохранён LibreOffice: пакет собран стоковым приложением, поэтому его структура согласована насквозь. Заявленное приложение теперь LibreOffice, не исходное. Вёрстка могла измениться.",
    "The file was written and opens, but the output was not re-inspected: the number of critical findings after cleaning is unknown. Re-inspect the output before treating the file as cleaned.":
        "Файл записан и открывается, но повторная инспекция результата не прогонялась: число критичных находок после чистки неизвестно. Прогоните инспекцию результата, прежде чем считать файл почищенным.",
    "The file weighs {mb} MB. The Telegram Bot API doesn't deliver files larger than {limit} MB to bots at all -- that is a limit of the Bot API itself, not of the bot. Send a smaller file.":
        "Файл весит {mb} МБ. Telegram Bot API вообще не отдаёт ботам файлы крупнее {limit} МБ -- это ограничение самого Bot API, а не бота. Пришлите файл меньшего размера.",
    "The first 32 characters of the structure are the printer name: often a host name, room number or office. The part was removed with its references from .rels (and r:id in the pageSetup of Excel sheets).":
        "Первые 32 символа структуры -- имя принтера: часто имя хоста, номер кабинета или офис. Часть убрана с ссылками из .rels (и r:id в pageSetup листов Excel).",
    "The flag of an embedded OLE object on every \\object group. The object itself and its metadata sit in the neighbouring \\objdata of the same group.":
        "Флаг внедрённого OLE-объекта у каждой группы \\object. Сам объект и его метаданные лежат в соседнем \\objdata этой же группы.",
    "The flags declare which metadata blocks are in the file. A mismatch between the flags and the actual chunks is a sign of manual editing":
        "Флаги объявляют, какие блоки метаданных лежат в файле. Расхождение флагов и реальных чанков -- признак ручной правки",
    "The flags now match the remaining chunks: otherwise a sign of crude cleaning would remain (a flag without its chunk)":
        "Флаги приведены в соответствие с оставшимися чанками: иначе остался бы признак грубой чистки (флаг есть, чанка нет)",
    "The format was detected as %s, but the top level of the container holds %s streams: an embedded object probably contains streams of another application. Parsing follows the top level.":
        "Формат определён как %s, но на верхнем уровне контейнера лежат потоки %s: вероятно, во встроенном объекте есть потоки другого приложения. Разбор идёт по верхнему уровню.",
    "The full TeX Live version string with the distribution's build date: it dates the author's machine and reveals their OS/distribution.":
        "Полная строка версии TeX Live с датой сборки дистрибутива: датирует рабочую машину автора и выдаёт его ОС/дистрибутив.",
    "The generator looks like a library/script (marker '%s'): %s":
        "Генератор похож на библиотеку/скрипт (маркер '%s'): %s",
    "The group was left empty: a missing field is more conspicuous than an empty value.":
        "Группа осталась пустой: отсутствие самого поля заметнее, чем пустое значение.",
    "The hash lets the password be cracked offline, and passwords are often reused. The protection stays on, but WITHOUT a password: anyone can now turn it off.":
        "Хеш позволяет подобрать пароль офлайн, а пароль часто используют повторно. Защита остаётся включённой, но БЕЗ пароля: снять её теперь может любой.",
    "The hash lets the password be cracked offline. The element was removed entirely: write protection is off.":
        "Хеш позволяет подобрать пароль офлайн. Элемент убран целиком: защита записи снята.",
    "The hash lets the password be cracked offline. The protection stays on, but without a password: anyone can now turn it off.":
        "Хеш позволяет подобрать пароль офлайн. Защита остаётся включённой, но без пароля: снять её теперь может любой.",
    "The header version (%s) doesn't match the parsed version (%s): the file was rewritten by a third-party tool":
        "Версия в заголовке (%s) не совпадает с версией по разбору (%s): файл переписывали сторонним инструментом",
    "The header version often disagrees with the file's features: a specific producer sets it, and it identifies that producer.":
        "Версия в заголовке часто расходится с возможностями файла: её выставляет конкретный продюсер и по ней он опознаётся.",
    "The human-readable name of the sensitivity label.":
        "Человекочитаемое имя метки конфиденциальности.",
    "The identifier in rdf:about duplicates xmpMM:DocumentID.":
        "Идентификатор в rdf:about дублирует xmpMM:DocumentID.",
    "The input archive changed during processing: the result is unreliable, archive not handed over.":
        "Входной архив изменился во время работы: результат ненадёжен, архив не выдан.",
    "The input archive was not found or is not a file.":
        "Входной архив не найден или это не файл.",
    "The input file changed during cleaning (sha256 before and after differ).":
        "Входной файл изменился во время чистки (sha256 до и после не совпал).",
    "The input file changed during processing: the result is unreliable, file not handed over.":
        "Входной файл изменился во время работы: результат ненадёжен, файл не выдан.",
    "The input file changed during processing: the result is unreliable, no file handed over.":
        "Входной файл изменился во время работы: результат ненадёжен, файл не выдан.",
    "The input file is empty (0 bytes); it may not have finished downloading.":
        "Входной файл пуст (0 байт), возможно, не докачался.",
    "The inspection core (core/inspect.py) is not ready yet. Try again a little later.":
        "Ядро разбора (core/inspect.py) пока не готово -- его пишет параллельный агент. Попробуйте чуть позже.",
    "The inspector considers the file built by a library or generator, not saved by an office application.":
        "Инспектор считает файл собранным библиотекой или генератором, а не сохранённым в офисном приложении.",
    "The inspector couldn't parse part of the file (%d); the proof is incomplete:":
        "Инспектор не разобрал часть файла (%d), доказательство неполное:",
    "The internal version number of the application.":
        "Внутренний номер версии приложения.",
    "The layer is off by default: its content IS in the file but isn't displayed. That's how drafts, comments and service notes get hidden; one click in any viewer turns it on.":
        "Слой выключен по умолчанию: его содержимое ЕСТЬ в файле, но не отображается. Так прячут черновики, комментарии и служебные пометки -- включается одним кликом в любом просмотрщике.",
    "The layers are visible, no content is hidden.":
        "Слои видимы, содержимое не скрыто.",
    "The list of authors. A name in the comment text itself (usually the bold first line) is content and is left alone.":
        "Список авторов. Имя в тексте самого комментария (обычно жирная первая строка) -- содержимое, его не трогаем.",
    "The locale identifier of the application that saved the file: indirectly reveals the country and the language build of Office. It cannot be removed: reading the text correctly depends on it.":
        "Идентификатор локали приложения, сохранившего файл: косвенно выдаёт страну и языковую сборку Office. Убрать нельзя -- от него зависит корректное чтение текста.",
    "The manager's full name from the document properties.":
        "ФИО руководителя из свойств документа.",
    "The mere fact of printing sometimes shows that the document left the building.":
        "Сам факт печати иногда выдаёт, что документ уходил наружу.",
    "The mere presence of 8BIM means the file was opened in Photoshop or a compatible editor":
        "Сам факт наличия 8BIM означает, что файл открывали в Photoshop или совместимом редакторе",
    "The message is unavailable. Send the file again.":
        "Сообщение недоступно. Пришлите файл ещё раз.",
    "The message is unavailable: delete it by hand.":
        "Сообщение недоступно: удалите его вручную.",
    "The meta.xml part is missing from the container: the document properties were either cut out or the file was not built by an office application.":
        "Части meta.xml в контейнере нет: свойства документа либо вырезаны, либо файл собран не офисным приложением.",
    "The metadata contradicts itself (%d): dates, versions or fonts disagree in a way that only happens after manual editing.":
        "Метаданные противоречат сами себе (%d): даты, версии или шрифты не сходятся так, как бывает только после ручной правки.",
    "The moment of the last save (UTC).":
        "Момент последнего сохранения (UTC).",
    "The moment of the last save.":
        "Момент последнего сохранения.",
    "The moment the document was created (UTC).":
        "Момент создания документа (UTC).",
    "The moment the document was created.":
        "Момент создания документа.",
    "The name and description of an external connection.":
        "Имя и описание внешнего подключения.",
    "The name of the template the document was created from.":
        "Имя шаблона, из которого создан документ.",
    "The name of the user who sent the document to print.":
        "Имя пользователя, отправившего документ на печать.",
    "The name of the user who turned on 'read-only recommended' or a password to modify.":
        "Имя пользователя, включившего «рекомендовать только чтение» или пароль на изменение.",
    "The name of whoever last pressed 'Save' -- often NOT the author.":
        "Имя того, кто последним нажал 'Сохранить' -- часто НЕ автор.",
    "The name the person is logged in with in Acrobat/Reader. Usually a real full name or a domain account, and nobody cleans it.":
        "Имя, под которым человек залогинен в Acrobat/Reader. Обычно это настоящее ФИО или доменная учётка, и её никто не чистит.",
    "The names of all participants of the shared workbook and the time of each of their saves.":
        "Имена всех участников общей книги и время каждого их сохранения.",
    "The number of objects relative to pages is a rough producer fingerprint: generators produce predictably few objects per page.":
        "Число объектов относительно числа страниц -- грубый отпечаток продюсера: генераторы дают предсказуемо мало объектов на страницу.",
    "The number of pages, words, characters, tables and images. Indirectly reveals the volume of cut text if compared with the visible content.":
        "Число страниц, слов, символов, таблиц, картинок. Косвенно выдаёт объём вырезанного текста, если сравнить со видимым содержимым.",
    "The object is laid into the RTF as a hex dump of a whole OLE container. Inside is ITS OWN metadata (author, dates, the path to the source file) and, if it's a pasted document, its full content, not just the visible picture.":
        "Объект уложен в RTF как hex-дамп целого OLE-контейнера. Внутри -- СВОИ метаданные (автор, даты, путь к исходному файлу) и, если это вставленный документ, его полное содержимое, а не только видимая картинка.",
    "The object is not embedded but LINKED to an external file: the document stores the path to the source and goes to it when the field is updated -- that is a path on someone else's machine, not the file's content.":
        "Объект не внедрён, а СВЯЗАН с внешним файлом: документ хранит путь к исходнику и обращается к нему при обновлении поля -- это путь на чужой машине, а не содержимое файла.",
    "The output is detected as %s, not %s: file not handed over.":
        "Результат определяется как %s, а не %s: файл не выдан.",
    "The output path equals the input path: the input is never overwritten.":
        "Путь результата совпадает с путём входного файла: вход не перезаписываем.",
    "The output path is a directory; a file path is required.":
        "Выходной путь -- каталог, нужен путь к файлу.",
    "The output path matches the input path: the input archive is never overwritten.":
        "Выходной путь совпадает с входным: входной архив не перезаписывается никогда.",
    "The output path matches the input path: the input file is never overwritten.":
        "Выходной путь совпадает с входным: входной файл не перезаписывается никогда.",
    "The output was not re-inspected, so there was nothing to compare the original values against. Run inspection before treating the file as cleaned.":
        "Повторная инспекция результата не прогонялась, сверить исходные значения с содержимым результата было нечем. Прогоните инспекцию, прежде чем считать файл почищенным.",
    "The package contains ActiveX controls.":
        "В пакете есть ActiveX-элементы управления.",
    "The package has %d embedded OLE object(s) with their own metadata.":
        "В пакете %d вложенных OLE-объект(ов) со своими метаданными.",
    "The package has %d entries, only the first %d were processed":
        "В пакете %d записей, обработаны первые %d",
    "The package is digitally signed (%d parts in _xmlsignatures/): cleaning the metadata WILL invalidate the signature; the cleaner refuses without force_signed":
        "Пакет подписан цифровой подписью (частей в _xmlsignatures/: %d): очистка метаданных сделает подпись недействительной; без force_signed очистка отклоняется",
    "The package is signed (%d parts in _xmlsignatures/): any edit invalidates the signature. File NOT cleaned; to clean it anyway and remove the signature, force_signed is required.":
        "Пакет подписан (%d частей в _xmlsignatures/): любая правка делает подпись недействительной. Файл НЕ очищен; чтобы всё равно очистить и убрать подпись, нужен force_signed.",
    "The package layout alone reveals both the generator and the document structure.":
        "Состав пакета сам по себе выдаёт и генератор, и структуру документа.",
    "The package was built by a library (python-docx/docxtpl or an AI generator), not by Word: %s.":
        "Пакет собран библиотекой (python-docx/docxtpl или генератором ИИ), а не Word: %s.",
    "The package was built in %s, not in Word: %s. This is normal for that suite.":
        "Пакет собран в %s, а не в Word: %s. Для этого пакета это штатно.",
    "The package was rebuilt by a library on top of an Office template: the real save time leaked through the ZIP timestamps.":
        "Пакет пересобран библиотекой поверх шаблона Office: реальное время сохранения утекло через таймстемпы ZIP.",
    "The page list can't be read: %s":
        "Список страниц не читается: %s",
    "The paper format in the print settings (%s) points to a region: %s. An indirect sign: the format is set from the system locale rather than chosen deliberately.":
        "Формат бумаги в настройках печати (%s) указывает на регион: %s. Косвенный признак: формат ставится по локали системы, а не выбирается осознанно.",
    "The paper size code from the DEVMODE.":
        "Код формата бумаги из DEVMODE.",
    "The part exists, there are no values inside. The mere fact of its presence tells about the application or system that created the file.":
        "Часть есть, значений внутри нет. Сам факт её наличия говорит о приложении или системе, которая файл создала.",
    "The part is larger than %d MB: editing is impossible, the protection password hash remains.":
        "Часть больше %d МБ: правка невозможна, хеш пароля защиты остался.",
    "The part was kept as is, byte for byte.":
        "Часть сохранена как есть, байт в байт.",
    "The part was kept. Only the Overrides of removed parts (%d) and Defaults for extensions no longer in the package were removed.":
        "Часть сохранена. Убраны только Override удалённых частей (%d) и Default расширений, которых в пакете больше нет.",
    "The path reveals the user name, drive and server. The link stayed as working structure (formulas and objects rely on it), but the directory was removed: only the file name was kept.":
        "Путь выдаёт имя пользователя, диск и сервер. Ссылка осталась рабочей структурой (формулы и объекты на неё опираются), но каталог убран: оставлено имя файла.",
    "The path to an external Excel file, often a full local or UNC one. Sheets: %s":
        "Путь к внешнему файлу Excel, часто полный локальный или UNC. Листы: %s",
    "The path to the .dot(x): either the local user profile or the organization's network share.":
        "Путь к .dot(x): либо локальный профиль пользователя, либо сетевая шара организации.",
    "The post-conversion pass crashed: document properties may remain.":
        "Дочистка после конвертации сорвалась: свойства документа могли остаться.",
    "The post-conversion pass failed: document properties may remain in the file. %s":
        "Дочистка после конвертации не прошла: свойства документа могли остаться в файле. %s",
    "The post-conversion pass is unavailable: document properties may remain.":
        "Дочистка после конвертации недоступна: свойства документа могли остаться.",
    "The presentation body. Speaker notes, hidden slides and objects moved off the slide area also stay here.":
        "Тело презентации. Здесь же остаются заметки докладчика, скрытые слайды и объекты, вынесенные за границу слайда.",
    "The preview image may show an earlier version of the page.":
        "Картинка предпросмотра может показывать прежнюю страницу.",
    "The preview is rendered at save time and may show the PREVIOUS version of the document — including text that has since been deleted. It is also an image with its own metadata.":
        "Превью рендерится при сохранении и может показывать ПРЕДЫДУЩУЮ версию документа — в том числе уже удалённый текст. Кроме того, это картинка со своими метаданными.",
    "The print form name. A non-standard value may have been set up by an administrator and then carries the name of an organization or department; a standard one reveals the region.":
        "Имя формы печати. Нестандартное значение бывает заведено администратором и тогда несёт название организации или отдела; стандартное выдаёт регион.",
    "The printer name and its settings. This points straight at a specific workstation: the print queue name often contains a host name, a department or an inventory number.":
        "Имя принтера и его настройки. Это прямой указатель на конкретное рабочее место: имя очереди печати часто содержит имя хоста, отдела или инвентарный номер.",
    "The printer name from the DEVMODE structure. It regularly contains a host name, an office number, a city or an office name — that is, the physical location of the author.":
        "Имя принтера из структуры DEVMODE. Регулярно содержит имя хоста, номер кабинета, город или название офиса — то есть физическое расположение автора.",
    "The profile is hidden in a text chunk as hex: this is what ImageMagick does. Viewers don't show it, but the metadata inside is live":
        "Профиль спрятан в текстовый чанк как hex: так делает ImageMagick. Просмотрщики его не показывают, но метаданные внутри живые",
    "The property holds a person (author, executor, signer) — a full name from a template or an EDMS card.":
        "Свойство хранит человека (автор, исполнитель, подписант) — ФИО из шаблона или карточки СЭД.",
    "The property holds a person (author, executor, signer).":
        "Свойство хранит человека (автор, исполнитель, подписант).",
    "The protection stays on, but without a password: anyone can now turn it off.":
        "Защита остаётся включённой, но без пароля: снять её теперь может любой.",
    "The ranges stay, but the binding to specific people was removed: the protection exceptions no longer apply to them.":
        "Диапазоны остаются, но привязка к конкретным людям снята: исключения из защиты для них больше не действуют.",
    "The raw /Metadata stream can't be read: %s":
        "Сырой поток /Metadata не читается: %s",
    "The real email of the user who applied the label. The most direct identity leak in OOXML.":
        "Реальный email пользователя, применившего метку. Самая прямая утечка личности в OOXML.",
    "The reference to the part from the owner's XML could not be removed safely, so the part was kept. ":
        "Ссылку на часть из XML владельца безопасно убрать не удалось, часть оставлена. ",
    "The references to the project could not be removed, so the macros were kept.":
        "Ссылки на проект убрать не удалось, макросы оставлены.",
    "The regenerate profile (re-creation via LibreOffice) doesn't apply to format \"%s\", phase 4. File NOT cleaned, output file not created.":
        "Профиль regenerate (пересоздание через LibreOffice) для формата «%s» не применяется, фаза 4. Файл НЕ очищен, выходной файл не создан.",
    "The regenerate profile is not supported for PDF (it is for office formats).":
        "Профиль regenerate для PDF не поддерживается (он для офисных форматов).",
    "The regenerate profile requires LibreOffice, which isn't installed on this machine, phase 4. In-place cleaning is not substituted: the invisibility would be different. File NOT cleaned, output file not created.":
        "Профиль regenerate требует LibreOffice, его на машине нет, фаза 4. Чистка на месте не подменяется: незаметность была бы другой. Файл НЕ очищен, выходной файл не создан.",
    "The report covers metadata only: it doesn't assess writing style or the content itself.":
        "Отчёт касается только метаданных: стиль текста и сам контент он не оценивает.",
    "The result has %d %%%%EOF markers (expected 1): possibly a string inside a stream.":
        "В результате %d маркеров %%%%EOF (ожидался 1): возможно, строка внутри потока.",
    "The revision author table lists %d people: more than one person worked on the file, although the document properties name a single author.":
        "В таблице авторов правок %d человек(а): над файлом работал не один, хотя в свойствах документа автор указан один.",
    "The revision author table. The names stay in the file even after the changes themselves are accepted or rejected.":
        "Таблица авторов правок. Имена остаются в файле даже после того, как сами исправления приняты или отклонены.",
    "The revision table lists %d author(s): more than one person worked on the file, although \\author names just one.":
        "В таблице правок %d автор(ов): над файлом работал не один человек, хотя \\author указан один.",
    "The root wasn't recognized and there are no values inside. The part stayed in place: its absence would itself be a sign of package editing.":
        "Корень не опознан, значений внутри нет. Часть осталась на месте: её отсутствие само было бы признаком правки пакета.",
    "The schema binding part exists, but there is no reference in it.":
        "Часть привязки схемы есть, самой ссылки в ней нет.",
    "The segment is written by Photoshop and Adobe libraries: it shows which software the file went through":
        "Сегмент пишут Photoshop и библиотеки Adobe: указывает, через какой софт прошёл файл",
    "The set of restrictions (printing, text extraction, editing) is also a fingerprint of who prepared the file and with what.":
        "Набор запретов (печать, извлечение текста, правка) -- это тоже отпечаток того, кто и чем готовил файл.",
    "The set of subset prefixes is a stable fingerprint of a \"producer + font set\" pair: a match between two files points to a common source.":
        "Набор префиксов сабсетов -- устойчивый отпечаток связки «продюсер + набор шрифтов»: совпадение у двух файлов указывает на общий источник.",
    "The signature carries the signer's certificate (name or organization, email, validity period). It can be removed only together with the signature: the file stops being signed, and cleaning is refused without force_signed.":
        "Подпись содержит сертификат подписанта (имя или организацию, email, срок действия). Убрать его можно только вместе с подписью: файл перестанет быть подписанным, а очистка без force_signed отклоняется.",
    "The signature contains the signer's certificate (full name, organization, email). It cannot be removed without losing the signature.":
        "Подпись содержит сертификат подписанта (ФИО, организация, почта). Удалить её без потери подписи невозможно.",
    "The signature contains the signer's certificate: a full name or organization name, email address, validity period, thumbprint. It can be removed only together with the signature itself: the file stops being signed.":
        "Подпись содержит сертификат подписанта: ФИО или название организации, адрес почты, срок действия, отпечаток. Убрать её можно только вместе с самой подписью -- файл перестанет быть подписанным.",
    "The signature covered the file's content: cleaning breaks it, and the recipient can see that it was signed":
        "Подпись покрывала содержимое файла: чистка её ломает, а сам факт подписи виден получателю",
    "The signature covers the file content: any cleaning is guaranteed to break it, and the very fact of a signature is visible to the recipient":
        "Подпись покрывает содержимое файла: любая чистка её гарантированно ломает, а сам факт подписи виден получателю",
    "The signature doesn't survive a metadata edit, so it was removed entirely (force_signed).":
        "Подпись не переживает правку метаданных, поэтому убрана целиком (force_signed).",
    "The source file is damaged: entry \"%s\" can't be read or fails the CRC check.":
        "Исходный файл повреждён: запись «%s» не читается или не проходит проверку CRC.",
    "The source file was not modified by cleaning: it only writes a new copy.":
        "Исходный файл чисткой не изменён: она пишет только в новую копию.",
    "The standard container of document properties.":
        "Стандартный контейнер свойств документа.",
    "The stream cannot be parsed safely: replaced with an empty packet so that the XMP stays in place.":
        "Поток не разбирается безопасно: заменён пустым пакетом, чтобы XMP остался на месте.",
    "The stream did not parse as JPEG: %s":
        "Поток не разобран как JPEG: %s",
    "The stream is too large or uses a non-standard encoding.":
        "Поток слишком велик или в нестандартном кодировании.",
    "The template name; if it is a path, the file system leaks.":
        "Имя шаблона; если это путь — утекает файловая система.",
    "The template path is usually file:///C:/Users/NAME/... or UNC: the user name and the organization's server. Without an attached template Word uses Normal.dotm.":
        "Путь к шаблону обычно file:///C:/Users/ИМЯ/... или UNC: имя пользователя и сервер организации. Без присоединённого шаблона Word использует Normal.dotm.",
    "The template path reveals the author's file system.":
        "Путь к шаблону выдаёт файловую систему автора.",
    "The template path reveals the author's user name and directories: only the file name was kept.":
        "Путь к шаблону выдаёт имя пользователя и каталоги автора: оставлено только имя файла.",
    "The template path usually contains the Windows user name or a UNC path to the organization's server.":
        "Путь к шаблону обычно содержит имя пользователя Windows или UNC-путь к серверу организации.",
    "The thumbnail is rendered on save and may show a PREVIOUS version of the document, including text already deleted; it's also an image with its own metadata.":
        "Превью рендерится при сохранении и может показывать ПРЕДЫДУЩУЮ версию документа, в том числе уже удалённый текст; кроме того, это картинка со своими метаданными.",
    "The thumbnail may have been made BEFORE retouching and cropping":
        "Миниатюра могла быть сделана ДО ретуши и кропа",
    "The thumbnail may have been made BEFORE retouching and cropping: it sometimes shows the whole original frame, including what was painted over":
        "Миниатюра могла быть сделана ДО ретуши и кропа: в ней иногда видно исходный кадр целиком, включая то, что замазали",
    "The thumbnail may have been made before retouching and cropping":
        "Миниатюра могла быть сделана до ретуши и кропа",
    "The thumbnail was saved by the editor and may have been made BEFORE the edits: it sometimes shows the original frame":
        "Миниатюра сохранена редактором и могла быть сделана ДО правок: в ней иногда виден исходный кадр",
    "The time of the OLE storage itself, separate from the date in the document properties: the two often diverge and reveal that the metadata was edited.":
        "Время самого OLE-хранилища, отдельное от даты в свойствах документа: они часто расходятся и выдают правку метаданных.",
    "The time the backup copy was created.":
        "Время создания резервной копии.",
    "The title is needed by PDF/UA (XMP declares pdfuaid) -- structural, kept.":
        "Заголовок нужен PDF/UA (в XMP заявлен pdfuaid) -- структурное, сохраняем.",
    "The total editing time in minutes. Shows whether the text was written over hours or pasted in ready-made within a minute.":
        "Суммарное время правки в минутах. Выдаёт, писали текст часами или вставили готовый за минуту.",
    "The total time spent working on the document. Shows whether the text was written over hours or pasted in ready-made within a minute.":
        "Суммарное время работы над документом. Выдаёт, писали текст часами или вставили готовый за минуту.",
    "The uploaded file is not a ZIP archive or is damaged: nothing to clean.":
        "Присланный файл не ZIP-архив или повреждён: чистить нечего.",
    "The usage-rights signature (UR3) is invalid after any edit; Reader will merely disable the extended features.":
        "Подпись прав использования (UR3) недействительна после любой правки; Reader лишь отключит расширенные функции.",
    "The user name of the application that created the document.":
        "Имя пользователя приложения, создавшего документ.",
    "The user name or login of whoever created the document.":
        "Имя пользователя или логин того, кто создал документ.",
    "The value contained personal data (an email, a path or a name found in other properties): blanked.":
        "Значение содержало личные данные (email, путь или имя, найденное в других свойствах): обнулено.",
    "The value contained personal data: blanked.":
        "Значение содержало личные данные: обнулено.",
    "The value contains a local path: it reveals the user name and disk layout.":
        "В значении локальный путь: выдаёт имя пользователя и структуру диска.",
    "The value contains a personal string from other document properties.":
        "Значение содержит личную строку из других свойств документа.",
    "The value contains an email.":
        "Значение содержит email.",
    "The value looks like a local or network path.":
        "Значение похоже на локальный или сетевой путь.",
    "The value looks like a local path: it reveals the user name and disk layout.":
        "Значение похоже на локальный путь: выдаёт имя пользователя и структуру диска.",
    "The values are form content, the user filled them in deliberately. PARANOID clears them.":
        "Значения -- содержимое формы, пользователь заполнил их осознанно. PARANOID очищает.",
    "The version QPDF sees. A mismatch with the %PDF- header means the file was rewritten.":
        "Версия, которую видит QPDF. Расхождение с заголовком %PDF- означает, что файл переписывали.",
    "The viewer is told to redraw fields from their values: field values exist in the file even where it looks empty.":
        "Просмотрщику приказано перерисовать поля по их значениям: значения полей в файле есть даже там, где визуально пусто.",
    "The whole comment body: working correspondence that's invisible in normal viewing.":
        "Тело комментария целиком: рабочая переписка, которую не видно при обычном просмотре.",
    "The workbook body in BIFF format: formulas, hidden sheets, range names and the external link cache live here, not in the properties.":
        "Тело книги в формате BIFF: формулы, скрытые листы, имена диапазонов и кеш внешних связей лежат здесь, а не в свойствах.",
    "Theme: its absence means \"not created by Excel\".":
        "Тема оформления: её отсутствие означает «создан не Excel».",
    "Theme: its absence means \"not created by PowerPoint\".":
        "Тема оформления: её отсутствие означает «создан не PowerPoint».",
    "Theme: its absence means \"not created by Word\".":
        "Тема оформления: её отсутствие означает «создан не Word».",
    "There are more bytes after the end of the image. Decoders ignore them, so anything can be hidden there -- from an archive to someone else's document":
        "После конца изображения есть ещё байты. Декодеры их игнорируют, поэтому там прячут что угодно -- от архива до чужого документа",
    "There are no values inside, but the part itself remained: the document lived in a SharePoint library or in an EDMS.":
        "Значений внутри нет, но сама часть осталась: документ лежал в библиотеке SharePoint или в СЭД.",
    "There are signs of scrubbing: empty identity fields or a container the claimed application doesn't write.":
        "Есть признаки чистки: пустые поля личности или контейнер, который так не пишет заявленное приложение.",
    "There is an embedded preview (%s): it may visually differ from the current content.":
        "Есть встроенное превью (%s): визуально может отличаться от текущего содержимого.",
    "There is no cleaned copy: don't send anything as cleaned.":
        "Почищенной копии нет: ничего не отправляйте как почищенное.",
    "There is only one editing cycle (%s), yet the editing time is non-empty (%s): the file seems to have been rebuilt by a program from someone else's source.":
        "Цикл редактирования всего один (%s), но время правки непустое (%s): файл, похоже, пересобран программой из чужого исходника.",
    "These are parts of the file that couldn't be read. Not the same as having no metadata: anything could be in there.":
        "Это части файла, которые не прочитались. Не путать с отсутствием метаданных: там может лежать что угодно.",
    "These are values from the original file found in the output: cleaning didn't remove them. Don't send this file.":
        "Это значения из самого исходного файла, найденные в результате: чистка их не убрала. Такой файл отправлять нельзя.",
    "These fields were kept deliberately, not forgotten. Structural data (document language, accessibility tags, ICC profile, outline) is needed for reading, printing and screen readers; some you may have asked to keep yourself (keep).":
        "Эти поля оставлены осознанно, а не забыты. Структурное (язык документа, теги доступности, ICC-профиль, оглавление) нужно для чтения, печати и скринридеров; часть вы могли сами попросить сохранить (keep).",
    "These parts of the file couldn't be parsed or processed: metadata may remain in them even if re-inspection found nothing.":
        "Эти части файла не разобрались или не обработались: в них могли остаться метаданные, даже если повторная инспекция ничего не нашла.",
    "This arrived as a <b>photo</b>, not a file. Telegram already recompressed and re-encoded the image on its side before the bot saw it -- the original metadata (EXIF, GPS, camera model and so on) has already been stripped or rewritten. There is nothing to inspect here, and the result would be false.\n\nSend the same file again via the paperclip → <b>File</b> (Document), not through the \"Photo\" section.":
        "Это пришло как <b>фото</b>, а не файл. Telegram уже пережал и перекодировал картинку на своей стороне до того, как её увидел бот -- исходные метаданные (EXIF, GPS, модель камеры и т.п.) уже срезаны или переписаны. Инспектировать тут нечего, а результат будет ложным.\n\nПришлите тот же файл ещё раз через скрепку → <b>Файл</b> (Document), не через раздел «Фото».",
    "This button is not for you.":
        "Эта кнопка не для вас.",
    "This data stays in the file and cleaning won't remove it: either the format works that way, or removing it would break the file or its verification. Before sending the file, decide whether that's acceptable.":
        "Эти данные остаются в файле, и чистка их не уберёт: так устроен формат, либо удаление сломало бы файл или его проверку. Прежде чем отправлять файл, решите, приемлемо ли это.",
    "This is a metadata inspector and cleaner for documents.\n\nSend a document (docx/xlsx/pptx, PDF, doc/xls/ppt, ODF, RTF, an image) -- the bot will show what is inside right now: author, application, dates, local paths, GPS in photos and so on. The file is <b>not changed and not stored</b>.\n\nFor <b>PDF, images (JPEG, PNG, WebP, GIF), docx/xlsx/pptx, RTF and ODF</b> cleaning buttons appear after the report: <b>Stealth</b> (removes identity and environment, keeps the application and version) or <b>Paranoid</b> (removes everything, but the result looks processed). The bot sends a cleaning report and a new copy of the file as a document, with a neutral name.\n\nEven Stealth can't make docx/xlsx/pptx fully undetectable: an empty author is itself a sign of cleaning. The bot says so honestly in the report rather than keeping quiet.\n\nBefore cleaning you can use the \"Keep\" toggles to leave the title, the author or the ICC profile in place. That is your deliberate choice: a file with the author kept <b>will not be clean</b>, and the bot will say so.\n\nSend a <b>ZIP</b> -- the bot will clean every document inside and return an archive with only the cleaned and verified ones (a report for each file will be in the chat).\n\nOld <b>doc/xls/ppt</b> can't be cleaned in place at all: the metadata and the deleted text of earlier versions are embedded in the container itself. They get a single button -- <b>Regenerate</b> through LibreOffice, producing docx/xlsx/pptx. If LibreOffice isn't on the machine, the bot says so and leaves the file alone.\n\nAutomatic deletion of reports from the chat: {ttl}. To delete right away use the \"Delete report\" button under a report or the /clear command. Cleaned files are not deleted. /status shows what is available on this machine.\n\nThe key requirement: send the file via the paperclip → <b>File</b> (Document), not as a \"Photo\". If you send an image as a photo, Telegram recompresses it before the bot sees the file, and the metadata is already lost -- there will be nothing to look at.":
        "Это инспектор и чистильщик метаданных документов.\n\nПришлите документ (docx/xlsx/pptx, PDF, doc/xls/ppt, ODF, RTF, картинку) -- бот покажет, что сейчас лежит внутри: автор, приложение, даты, локальные пути, GPS в фото и т.п. Файл при этом <b>не меняется и не сохраняется</b>.\n\nДля <b>PDF, картинок (JPEG, PNG, WebP, GIF), docx/xlsx/pptx, RTF и ODF</b> после отчёта появятся кнопки чистки: <b>Stealth</b> (убирает личность и окружение, оставляет приложение и версию) или <b>Paranoid</b> (убирает всё, но результат выглядит обработанным). Бот пришлёт отчёт о чистке и новую копию файла документом, с нейтральным именем.\n\nУ docx/xlsx/pptx полной незаметности не даёт даже Stealth: пустой автор сам по себе признак чистки. Бот честно пишет это в отчёте, а не умалчивает.\n\nПеред чисткой можно переключателями «Сохранить» оставить заголовок, автора или ICC-профиль. Это ваш осознанный выбор: файл с сохранённым автором <b>не будет чистым</b>, и бот так и скажет.\n\nПришлите <b>ZIP</b> -- бот почистит каждый документ внутри и вернёт архив только с почищенным и проверенным (отчёт по каждому файлу будет в чате).\n\nСтарые <b>doc/xls/ppt</b> на месте не чистятся в принципе: метаданные и удалённый текст прошлых версий вшиты в сам контейнер. Для них одна кнопка -- <b>Пересоздать</b> через LibreOffice, на выходе docx/xlsx/pptx. Нет LibreOffice на машине -- бот так и скажет и файл не тронет.\n\nАвтоудаление отчётов из чата: {ttl}. Удалить сразу -- кнопка «Удалить отчёт» под отчётом или команда /clear. Почищенные файлы не удаляются. /status -- что доступно на этой машине.\n\nГлавное условие: отправляйте файл через скрепку → <b>Файл</b> (Document), а не как «Фото». Если отправить картинку как фото, Telegram пережмёт её раньше, чем бот увидит файл, и метаданные будут уже потеряны -- смотреть будет не на чём.",
    "This is an internal failure, not \"nothing to clean\". There is no cleaned copy: don't send anything as cleaned.":
        "Это внутренний сбой, а не «нечего чистить». Почищенной копии нет: ничего не отправляйте как почищенное.",
    "This is document content, not metadata: left alone. Corporate SharePoint and OneDrive addresses with the account name in them remain.":
        "Это содержимое документа, не метаданные: не трогаем. Корпоративные адреса SharePoint и OneDrive с именем учётной записи в них остаются.",
    "This is the inspection phase. The file was not modified, but it couldn't be read either.":
        "Это фаза инспекции. Файл не изменён, но и прочитать его не удалось.",
    "This is the inspection phase: the file was read and not modified.":
        "Это фаза инспекции: файл прочитан и не изменён.",
    "This isn't metadata but a decoding parameter: without the marker the decoder guesses the default model (%s) and colors get distorted. Version and flags carry no identity":
        "Это не метаданные, а параметр декодирования: без маркера декодер угадывает модель по умолчанию (%s) и цвета исказятся. Версия и флаги не несут идентичности",
    "Thumbnail":
        "Превью",
    "Ties to portal schemas; meaningless once the part's data is blanked.":
        "Привязка к схемам портала; после обнуления данных части смысла не имеют.",
    "Time of the file's last edit":
        "Время последней правки файла",
    "Time of the last edit":
        "Время последней правки",
    "Time of the last print. Word doesn't write this element for documents never printed, so its absence stands out in no way.":
        "Время последней печати. Word не пишет этот элемент для ненапечатанных документов, поэтому отсутствие ничем не выделяется.",
    "Time saved by the participant":
        "Время сохранения участником",
    "Time the comment was created.":
        "Время создания комментария.",
    "Time the comment was edited.":
        "Время правки комментария.",
    "Time the pivot cache was refreshed.":
        "Время обновления кэша сводной.",
    "Time zone at digitization":
        "Часовой пояс при оцифровке",
    "Time zone at the moment of capture":
        "Часовой пояс в момент съёмки",
    "Title":
        "Заголовок",
    "Title from Windows Explorer (UTF-16LE)":
        "Заголовок из проводника Windows (UTF-16LE)",
    "TitlesOfParts — part titles":
        "TitlesOfParts — названия частей",
    "Top-level boxes (%d)":
        "Боксы верхнего уровня (%d)",
    "Total editing time":
        "Общее время редактирования",
    "Total files: %d. Cleaned and verified: %d. NOT cleaned: %d.":
        "Всего файлов: %d. Очищено и проверено: %d. НЕ очищено: %d.",
    "Total uncompressed size %d MB with an archive size of %d KB: looks like an archive bomb or exceeds the %d MB limit. Cleaning not performed.":
        "Суммарный несжатый объём %d МБ при размере архива %d КБ: похоже на архивную бомбу или превышен предел %d МБ. Чистка не выполнена.",
    "TotalTime — editing time, min":
        "TotalTime — время редактирования, мин",
    "Track changes stores the full name or login of the editor right in the document body — outside docProps.":
        "Track changes хранит ФИО или логин правившего прямо в теле документа — вне docProps.",
    "Tracked changes":
        "Отслеженные изменения",
    "Trapped (trapping for print)":
        "Trapped (треппинг для печати)",
    "UNC path in the print driver data":
        "UNC-путь в данных драйвера печати",
    "URL embedded in the Photoshop resources":
        "Ссылка, вписанная в ресурсы Photoshop",
    "URL of the organization's portal.":
        "URL портала организации.",
    "US and Canada":
        "США и Канада",
    "UT/NTFS/Unix fields in ZIP headers store the save time to the second and survive normalizing the DOS time. They can't be removed without rebuilding the headers, and rebuilding would give the cleaning away: the fields were kept.":
        "Поля UT/NTFS/Unix в заголовках ZIP хранят время сохранения с точностью до секунды и переживают приведение DOS-времени. Убрать их без пересборки заголовков нельзя, а пересборка выдала бы чистку: поля оставлены.",
    "UTF-8 text":
        "текст UTF-8",
    "Unexpected inspection failure: %s: %s":
        "Непредвиденный сбой инспекции: %s: %s",
    "Unique frame identifier: links files to each other":
        "Уникальный идентификатор кадра: связывает файлы между собой",
    "Unknown ancillary chunk":
        "Неизвестный вспомогательный чанк",
    "Unknown chunk":
        "Неизвестный чанк",
    "Unknown cleaning profile.":
        "Неизвестный профиль чистки.",
    "Unknown critical chunk":
        "Неизвестный критический чанк",
    "Unknown extension 0x%02X":
        "Неизвестное расширение 0x%02X",
    "Unknown marker":
        "Неизвестный маркер",
    "Unrecognized APP1":
        "Неопознанный APP1",
    "Unrecognized APP1 (%s)":
        "Неопознанный APP1 (%s)",
    "Unrecognized APP2":
        "Неопознанный APP2",
    "Unrecognized APP2 (%s)":
        "Неопознанный APP2 (%s)",
    "Unrecognized format":
        "Неопознанный формат",
    "Unrecognized hex profile":
        "Неопознанный hex-профиль",
    "Usually a path to a .dot(x): either local or \\\\server\\share.":
        "Обычно путь к .dot(x): либо локальный, либо \\\\сервер\\шара.",
    "Usually kept: it affects spell-checking and accessibility.":
        "Обычно сохраняем: влияет на проверку орфографии и доступность.",
    "Usually worth keeping: it affects spell checking.":
        "Обычно нужно сохранить: влияет на проверку орфографии.",
    "VBA macros":
        "Макросы VBA",
    "VBA project (macros)":
        "Проект VBA (макросы)",
    "VP8X declares EXIF, but there is no EXIF chunk in the file: the metadata was cut out crudely and the flag stayed":
        "VP8X объявляет EXIF, но чанка EXIF в файле нет: метаданные вырезали грубо, флаг остался",
    "VP8X flags don't match the chunks (%s)":
        "флаги VP8X не совпадают с чанками (%s)",
    "Values regenerated by cleaning (%d), e.g. trailer /ID: they are new and unrelated to the originals. Not a leak, but the inspector counts them as findings.":
        "Перегенерированные значения (%d), например trailer /ID: они новые и с исходными не связаны, это не утечка, но инспектор считает их находками.",
    "Variables of macros, add-ins and EDMS templates: they regularly hold the full names of approvers, contract numbers and database paths.":
        "Переменные макросов, надстроек и шаблонов СЭД: там регулярно лежат ФИО согласующих, номера договоров и пути к базам.",
    "Verification of the result failed: no file handed over.":
        "Проверка результата не пройдена: файл не выдан.",
    "Viewer preferences":
        "Настройки просмотрщика",
    "WARNING: colors may change":
        "ВНИМАНИЕ: цвета могут измениться",
    "WARNING: colors may change -- without a profile the viewer assumes sRGB":
        "ВНИМАНИЕ: цвета могут измениться -- без профиля просмотрщик считает картинку sRGB",
    "WARNING: colors may change -- without a profile the viewer assumes sRGB. The profile name named the device":
        "ВНИМАНИЕ: цвета могут измениться -- без профиля просмотрщик считает картинку sRGB. Имя профиля называло устройство",
    "WARNING: colors may change -- without a profile the viewer relies on gAMA/sRGB or assumes sRGB":
        "ВНИМАНИЕ: цвета могут измениться -- без профиля просмотрщик опирается на gAMA/sRGB или считает картинку sRGB",
    "WARNING: files that were not cleaned %s. They are in their original form, with all their metadata.":
        "ВНИМАНИЕ: не очищенные файлы %s. Они в исходном виде, со всеми метаданными.",
    "WRITTEN WITHOUT VERIFICATION (verify=False): cleanliness not proven":
        "ЗАПИСАНО БЕЗ ПРОВЕРКИ (verify=False): чистота не доказана",
    "Warning: parsing didn't finish (see errors below), so an empty result does NOT mean the file is clean. Anything could be in the unread parts; don't judge the file's cleanliness by this report.":
        "Внимание: разбор дошёл не до конца (см. ошибки ниже), поэтому пустой результат НЕ означает, что файл чист. В непрочитанных частях может лежать что угодно; судить о чистоте файла по такому отчёту нельзя.",
    "Warning: the absence of metadata is itself a sign the file was processed before. Normally created documents almost always carry at least the generating application and dates.":
        "Внимание: отсутствие метаданных само по себе является признаком предыдущей обработки файла. Штатно созданные документы почти всегда несут хотя бы приложение-генератор и даты.",
    "Web settings: a part required for a file from Word.":
        "Веб-настройки: часть, обязательная для файла от Word.",
    "WebP: RIFF declares %d bytes, the file has %d":
        "WebP: RIFF объявляет %d байт, в файле %d",
    "WebP: chunk %s declares %d bytes":
        "WebP: чанк %s объявляет %d байт",
    "WebP: chunk %s is truncated":
        "WebP: чанк %s обрезан",
    "WebP: chunk %s was not parsed (%s: %s)":
        "WebP: чанк %s не разобран (%s: %s)",
    "WebP: file is shorter than a RIFF header":
        "WebP: файл короче заголовка RIFF",
    "WebP: no image data, nothing to clean":
        "WebP: нет данных изображения, чистить нечего",
    "WebP: the VP8X chunk is truncated":
        "WebP: чанк VP8X обрезан",
    "What is available on this machine":
        "Что доступно на этой машине",
    "What made or modified the file, and when":
        "Чем и когда сделан или изменён файл",
    "What processed the file":
        "Чем обработан файл",
    "What produced the file":
        "Чем сделан файл",
    "What the file does by itself as soon as it's opened.":
        "Что файл делает сам, как только его открыли.",
    "What the file does on its own when opened.":
        "Что файл делает сам при открытии.",
    "When the comment was last edited.":
        "Когда комментарий правили в последний раз.",
    "When the document was last printed.":
        "Когда документ печатали в последний раз.",
    "When the document was printed.":
        "Когда документ печатали.",
    "When the picture was last modified":
        "Момент последнего изменения картинки",
    "Where the pivot takes its data from: a workbook, a sheet or a URL.":
        "Откуда сводная берёт данные: книга, лист или URL.",
    "Which parts of the document are labeled (header/footer, watermark).":
        "Какие части документа промаркированы (колонтитул, ватермарк).",
    "Which program exactly saved the file.":
        "Чем именно сохранён файл.",
    "Who created the document. Survives all later edits.":
        "Кто создал документ. Переживает все последующие правки.",
    "Who modified the comment":
        "Кем изменён комментарий",
    "Who the certificate was issued to: usually a full name, an organization and an email.":
        "Кому выдан сертификат: обычно полное имя, организация и email.",
    "Windows Installer (MSI database)":
        "Windows Installer (база MSI)",
    "Windows Installer (MSP patch)":
        "Windows Installer (патч MSP)",
    "Word always puts these parts into a .docx. They are missing — the package was built programmatically.":
        "Word всегда кладёт эти части в .docx. Их нет — пакет собран программно.",
    "Word creates a new rsid for every editing session and tags every piece of text with it. The table shows how many sessions the document took, and the text markup shows which paragraphs were added later than the rest and therefore where they were copied from.":
        "Word заводит новый rsid на каждый сеанс редактирования и помечает им каждый кусок текста. По таблице видно, за сколько заходов сделан документ, а по разметке текста -- какие абзацы дописаны позже остальных и, значит, откуда скопированы.",
    "Word keeps two table streams and switches between them on save. The inactive stream is an OLD version of the service structures from the previous save, including the previous list of revision authors. No 'document properties' tool shows it.":
        "Word держит два потока таблиц и переключается между ними при сохранении. Неактивный поток -- это СТАРАЯ версия служебных структур предыдущего сохранения, включая предыдущий список авторов правок. Никакой программой 'свойств документа' он не виден.",
    "Word marks every paragraph and run with an edit session identifier. A complete absence of rsid means the document was not created by Word. Body parts scanned: %d (%s).":
        "Word помечает каждый абзац и прогон идентификатором сессии правки. Полное отсутствие rsid означает, что документ создан не Word. Просканировано частей тела: %d (%s).",
    "Word places it at the position of the last edit. Its presence confirms the file was saved from Word.":
        "Word ставит её на позицию последней правки. Факт её наличия подтверждает сохранение именно из Word.",
    "Word's save signature. ":
        "Подпись сохранения из Word. ",
    "Word/Excel/PowerPoint always write [Content_Types].xml first.":
        "Word/Excel/PowerPoint всегда пишут [Content_Types].xml первым.",
    "Words":
        "Слов",
    "Words — words":
        "Words — слов",
    "Workbook is encrypted":
        "Книга зашифрована",
    "Workbook sheets":
        "Листы книги",
    "Workbook stream":
        "Поток рабочей книги",
    "Written by people and generators alike: prompts, model parameters and the source path end up here too":
        "Пишут и люди, и генераторы: сюда же складывают промпты, параметры моделей и путь к исходнику",
    "XFA duplicates the entered data in parallel with the regular fields.":
        "XFA дублирует введённые данные параллельно обычным полям.",
    "XFA form":
        "XFA-форма",
    "XFA is a full XML copy of the form together with the entered data. It lives in parallel with the regular fields: even if /V is cleaned, the data remains in XFA.":
        "XFA -- это полная XML-копия формы вместе с введёнными данными. Она живёт параллельно обычным полям: даже если /V вычистить, данные останутся в XFA.",
    "XML copy of the form":
        "XML-копия формы",
    "XML edits overlap":
        "правки XML пересекаются",
    "XML nesting deeper than %d: Office doesn't write files like that":
        "вложенность XML глубже %d: так файлы Office не пишутся",
    "XMP declares digitalSourceType=%s: the file is marked as generated or edited by AI":
        "XMP объявляет digitalSourceType=%s: файл помечен как сгенерированный или доработанный ИИ",
    "XMP doesn't parse as XML: %s":
        "XMP не разбирается как XML: %s",
    "XMP duplicates EXIF and IPTC and carries the document GUID (xmpMM) and edit history":
        "XMP дублирует EXIF и IPTC, несёт GUID документа (xmpMM) и историю правок",
    "XMP field. XMP is duplicated in EXIF and IPTC and survives re-saving in most editors":
        "Поле XMP. XMP дублируется в EXIF и IPTC и переживает пересохранение в большинстве редакторов",
    "XMP is empty or completely unreadable.":
        "XMP пуст или полностью нечитаем.",
    "XMP lives in a separate stream and survives cleaning of /Info: this is most often where everything \"already deleted\" remains.":
        "XMP лежит отдельным потоком и переживает чистку /Info: чаще всего именно тут остаётся всё, что «уже удалили».",
    "XMP packet":
        "XMP-пакет",
    "XMP packet (iTXt \"%s\")":
        "XMP-пакет (iTXt «%s»)",
    "XMP: authorship, document GUID, edit history":
        "XMP: авторство, GUID документа, история правок",
    "XMP: document GUID, authorship, edit history":
        "XMP: GUID документа, авторство, история правок",
    "XMP: showing the first %d of %d keys.":
        "XMP: показаны первые %d ключей из %d.",
    "YCCK would be read as CMYK and the Adobe inversion would be lost":
        "YCCK прочтётся как CMYK, инверсия Adobe потеряется",
    "ZIP archive":
        "ZIP-архив",
    "ZIP archive with a suspiciously high compression ratio (%.0f).":
        "ZIP-архив с подозрительно высоким коэффициентом сжатия (%.0f).",
    "ZIP archive won't open: %s: %s":
        "ZIP-архив не открывается: %s: %s",
    "ZIP container":
        "ZIP-контейнер",
    "ZIP entry times are zeroed (1980-01-01), although ODF normally stores real ones: the container was rebuilt by something other than LibreOffice.":
        "Времена записей ZIP обнулены (1980-01-01), хотя ODF обычно хранит настоящие: контейнер пересобран не LibreOffice.",
    "ZIP entry timestamp":
        "Таймстемп записи ZIP",
    "ZIP times left as written by %s (%s). Changing them would itself be a processing trace.":
        "Время ZIP оставлено как записал %s (%s). Смена времени сама была бы следом обработки.",
    "ZIP timestamps":
        "Таймстемпы ZIP",
    "[Content_Types].xml is no longer the first entry":
        "[Content_Types].xml перестал быть первой записью",
    "\\objupdate is present: objects are updated when the file is opened -- active content.":
        "Есть \\objupdate: объекты обновляются при открытии файла -- активное содержимое.",
    "_GoBack bookmark":
        "Закладка _GoBack",
    "a central directory record runs past its bounds":
        "запись центрального каталога выходит за его границы",
    "a chunk runs past the end of RIFF":
        "чанк выходит за пределы RIFF",
    "a marker was expected at offset %d, found 0x%02X":
        "на смещении %d ожидался маркер, найдено 0x%02X",
    "a part still has an r:id of a removed relationship":
        "в части остался r:id убранного отношения",
    "a relationship points to a removed part":
        "отношение ведёт на убранную часть",
    "a replaced part doesn't parse":
        "заменённая часть не разбирается",
    "absent":
        "отсутствует",
    "action entries not read: %d, the list above is incomplete":
        "записей о действиях не прочитано: %d, список выше неполон",
    "action list could not be read: the report is incomplete":
        "список действий не прочитался: отчёт неполон",
    "active content and attachments":
        "активное содержимое и вложения",
    "after %d h":
        "через %d ч",
    "after %d min":
        "через %d мин",
    "after cleaning: ":
        "после чистки: ",
    "after marker %s there are %d more bytes of data":
        "после маркера %s ещё %d байт данных",
    "after the edit the XML doesn't parse: %s":
        "после правки XML не разбирается: %s",
    "after the edit the XML fails strict parsing (%s)":
        "после правки XML не проходит строгий разбор (%s)",
    "after the edit the part's encoding isn't UTF-8":
        "после правки кодировка части не UTF-8",
    "all 1980-01-01 00:00":
        "все 1980-01-01 00:00",
    "all visible":
        "все видимы",
    "alpha":
        "альфа",
    "an Override points to a removed part":
        "Override ведёт на убранную часть",
    "an entry name can't be decoded":
        "имя записи не декодируется",
    "an entry's data descriptor was not recognized":
        "не распознан data descriptor записи",
    "an entry's local header lies outside the data":
        "локальный заголовок записи лежит за пределами данных",
    "an output entry fails the CRC check":
        "запись выхода не проходит проверку CRC",
    "animation":
        "анимация",
    "app.xml claims a Microsoft product (%s), but the ZIP entries are stamped with a real time (the latest is %s) instead of 1980-01-01. Office does not save like that.":
        "app.xml заявляет продукт Microsoft (%s), но записи ZIP помечены реальным временем (последняя %s) вместо 1980-01-01. Office так не сохраняет.",
    "app.xml has no root element":
        "в app.xml нет корневого элемента",
    "arbitrary vendor block":
        "произвольный блок вендора",
    "are NOT included in this archive":
        "в этот архив НЕ включены",
    "are in the %s/ folder; don't pass it on":
        "лежат в папке %s/, не отдавайте её дальше",
    "attachment %d":
        "вложение %d",
    "attachments left in the file: %d":
        "вложений осталось в файле: %d",
    "attribute %s is not in the tag":
        "атрибута %s нет в теге",
    "author":
        "автор",
    "authorId — reference to the author list":
        "authorId — ссылка на список авторов",
    "background":
        "фон",
    "batch ZIP cleaning":
        "пакетная чистка ZIP",
    "batch cleaning: module failed to load":
        "пакетная чистка: модуль не загрузился",
    "before cleaning: ":
        "до чистки: ",
    "blanked":
        "обнулено",
    "broken ZIP64 EOCD locator":
        "битый локатор ZIP64 EOCD",
    "broken ZIP64 EOCD record":
        "битая запись ZIP64 EOCD",
    "broken ZIP64 field in an entry":
        "битое ZIP64-поле записи",
    "broken central directory record":
        "битая запись центрального каталога",
    "broken extra field in an entry":
        "битое extra-поле записи",
    "broken local header of an entry":
        "битый локальный заголовок записи",
    "byte for byte":
        "байт в байт",
    "cannot be removed":
        "убрать невозможно",
    "cannot be removed: %d":
        "убрать невозможно: %d",
    "category":
        "категория",
    "certifying signature":
        "сертифицирующая подпись",
    "changed %d":
        "изменено %d",
    "chromaticity":
        "хроматичность",
    "chunk %s is truncated (%d bytes needed, %d available)":
        "чанк %s обрезан (нужно %d байт, есть %d)",
    "chunks with a bad CRC: %d. Either the file is damaged or the metadata was edited byte by byte by hand":
        "битых CRC чанков: %d. Либо файл повреждён, либо метаданные правили байтами вручную",
    "cleaning Stealth / Paranoid":
        "чистка Stealth / Paranoid",
    "cleaning report could not be built: consider the file NOT cleaned":
        "отчёт о чистке не собрался: файл считать НЕ почищенным",
    "cleaning returned no result: file NOT cleaned":
        "чистка не вернула результата: файл НЕ почищен",
    "cleaning: module failed to load":
        "чистка: модуль не загрузился",
    "code %d":
        "код %d",
    "coding parameters":
        "параметры кодирования",
    "compressed text":
        "сжатый текст",
    "compression method %d is not allowed in OOXML/ODF (only STORED and DEFLATED)":
        "метод сжатия %d не допускается в OOXML/ODF (только STORED и DEFLATED)",
    "container brand declaration":
        "объявление бренда контейнера",
    "core.xml has no root element":
        "в core.xml нет корневого элемента",
    "could not check the OLE2 signature: %r":
        "не удалось проверить сигнатуру OLE2: %r",
    "could not find the tag boundaries":
        "не удалось найти границы тега",
    "could not get the file size: %s":
        "не удалось узнать размер файла: %s",
    "could not match the tag's attributes":
        "не удалось сопоставить атрибуты тега",
    "could not read SummaryInformation: %r":
        "не читается SummaryInformation: %r",
    "could not read the OLE2 directory: %r":
        "не читается директория OLE2: %r",
    "could not write the output file (errno %s)":
        "не удалось записать выходной файл (errno %s)",
    "could not write the result: %s":
        "не удалось записать результат: %s",
    "cp:category — category":
        "cp:category — категория",
    "cp:contentStatus — status":
        "cp:contentStatus — статус",
    "cp:keywords — keywords":
        "cp:keywords — ключевые слова",
    "cp:lastModifiedBy — last saved by":
        "cp:lastModifiedBy — кто сохранил последним",
    "cp:lastPrinted — last printed":
        "cp:lastPrinted — последняя печать",
    "cp:revision — revision number":
        "cp:revision — номер ревизии",
    "cp:revision=1 with a volume of %d words: a document of that size is not made in a single save.":
        "cp:revision=1 при объёме %d слов: документ такого размера за одно сохранение не делают.",
    "created":
        "создан",
    "custom properties could not be read: %r":
        "пользовательские свойства не прочитались: %r",
    "custom properties cut off at a corrupt structure: %s":
        "пользовательские свойства оборваны на битой структуре: %s",
    "customXml data":
        "Данные customXml",
    "customXml data (SharePoint/EDMS)":
        "Данные customXml (SharePoint/СЭД)",
    "customXml schema":
        "Схема customXml",
    "customXml schema (standard)":
        "Схема customXml (стандартная)",
    "data remains after the end of the image (%d bytes)":
        "после конца изображения остались данные (%d байт)",
    "dc:creator in XMP is set (%s), while /Author in /Info is cleaned out: a classic half-done scrub":
        "dc:creator в XMP заполнен (%s), а /Author в /Info вычищен: классическая половинчатая чистка",
    "dc:creator — author":
        "dc:creator — автор",
    "dc:description — description":
        "dc:description — описание",
    "dc:identifier — identifier":
        "dc:identifier — идентификатор",
    "dc:language — language":
        "dc:language — язык",
    "dc:subject — subject":
        "dc:subject — тема",
    "dc:title — title":
        "dc:title — заголовок",
    "dcterms:created and dcterms:modified match (%s), meaning the document was created and saved within one minute, while TotalTime claims %d minutes of editing. One of the two is untrue: usually the dates were substituted.":
        "dcterms:created и dcterms:modified совпадают (%s), то есть документ создан и сохранён в пределах одной минуты, а TotalTime заявляет %d минут редактирования. Одно из двух неправда: обычно это подставленные даты.",
    "dcterms:created — created":
        "dcterms:created — создан",
    "dcterms:modified — modified":
        "dcterms:modified — изменён",
    "density":
        "плотность",
    "description":
        "описание",
    "different":
        "различна",
    "dimensions changed: %s -> %s":
        "размеры изменились: %s -> %s",
    "directory":
        "директория",
    "disabled (REPORT_TTL_MIN=0)":
        "выключено (REPORT_TTL_MIN=0)",
    "dispatcher proof could not be read: verdict by heuristic":
        "доказательство диспетчера не прочиталось: вердикт по эвристике",
    "dmDeviceName — printer name":
        "dmDeviceName — имя принтера",
    "dmDriverExtra — driver private data":
        "dmDriverExtra — приватные данные драйвера",
    "dmFormName — paper format":
        "dmFormName — формат бумаги",
    "dmPaperSize — paper size code":
        "dmPaperSize — код формата бумаги",
    "docProps/app.xml: the part exists but no property could be parsed (corrupted or non-XML content)":
        "docProps/app.xml: часть есть, но ни одного свойства не разобрано (повреждённый или не-XML контент)",
    "docProps/core.xml: the part exists but no property could be parsed (corrupted or non-XML content)":
        "docProps/core.xml: часть есть, но ни одного свойства не разобрано (повреждённый или не-XML контент)",
    "docProps/custom.xml: the part exists but no property could be parsed (corrupted or non-XML content)":
        "docProps/custom.xml: часть есть, но ни одного свойства не разобрано (повреждённый или не-XML контент)",
    "document properties":
        "свойства документа",
    "document title":
        "заголовок документа",
    "dots per cm":
        "точек на см",
    "dots per inch":
        "точек на дюйм",
    "drop: a set of part names is required":
        "drop: нужно множество имён частей",
    "duplicate entry name: %s":
        "дублирующееся имя записи: %s",
    "elements: %d":
        "элементов: %d",
    "embedded thumbnail of %d bytes: it may have been made before retouching and show the original frame":
        "встроенная миниатюра на %d байт: могла быть сделана до ретуши и показывать исходный кадр",
    "empty":
        "пусто",
    "empty (not signed)":
        "пустое (подпись не поставлена)",
    "empty Company":
        "пустой Company",
    "empty cp:lastModifiedBy":
        "пустой cp:lastModifiedBy",
    "empty dc:creator":
        "пустой dc:creator",
    "empty shell":
        "пустая оболочка",
    "enabled":
        "включена",
    "end":
        "конец",
    "end of archive (EOCD) not found: not a ZIP, or the file is truncated":
        "не найден конец архива (EOCD): не ZIP или файл обрезан",
    "entries overlap or data runs past the bounds":
        "записи перекрываются или данные выходят за границы",
    "entry order doesn't match the source":
        "порядок записей не совпал с исходным",
    "entry times":
        "время записей",
    "equal":
        "совпадает",
    "equipment serial number: ":
        "серийный номер техники: ",
    "error list could not be read":
        "список ошибок не прочитался",
    "errors":
        "ошибок",
    "errors during cleaning: %d":
        "ошибок при чистке: %d",
    "exiftool added %d fields not covered by pikepdf parsing":
        "exiftool добавил %d полей, не покрытых разбором pikepdf",
    "exiftool enrichment failed (not critical): %s":
        "Обогащение exiftool не удалось (не критично): %s",
    "exiftool failed (not critical): %s":
        "exiftool не отработал (не критично): %s",
    "exiftool failed: %s":
        "exiftool не отработал: %s",
    "exiftool supplemented the parse with %d field(s), mostly decoded MakerNotes":
        "exiftool дополнил разбор %d полем(ями), в основном расшифрованные MakerNotes",
    "exiftool: decoding MakerNotes in photos":
        "exiftool: расшифровка MakerNotes в фото",
    "extension 0x%02X is cut off":
        "расширение 0x%02X оборвано",
    "external links":
        "внешние ссылки",
    "extra bytes in the central directory after the last record":
        "в центральном каталоге лишние байты после последней записи",
    "fComplex flag (fast save)":
        "Флаг fComplex (быстрое сохранение)",
    "fEncrypted flag":
        "флаг fEncrypted",
    "failed":
        "не удалось",
    "failed to process: %d":
        "не удалось обработать: %d",
    "field values":
        "значения полей",
    "file":
        "файл",
    "file is empty":
        "файл пустой",
    "file is larger than %d MB: only the first %d MB were parsed":
        "файл больше %d МБ: разобраны только первые %d МБ",
    "file links (%s)":
        "ссылки на файлы (%s)",
    "file not found":
        "файл не найден",
    "file not found (deleted or renamed before the check started).":
        "файл не найден (удалён или переименован до начала проверки).",
    "file not found.":
        "файл не найден.",
    "fileSharing — who reserved the workbook":
        "fileSharing — кто зарезервировал книгу",
    "fileSharing — write reservation name and password":
        "fileSharing — имя и пароль резервирования записи",
    "fileVersion — Excel version and build":
        "fileVersion — версия и билд Excel",
    "files inside: %d":
        "файлов внутри: %d",
    "filterPrivacy=1 means the workbook was already marked as containing personal data.":
        "filterPrivacy=1 означает, что книгу уже помечали как содержащую персональные данные.",
    "first 100 listed, the whole tree removed":
        "перечислены первые 100, дерево удалено целиком",
    "form":
        "форма",
    "format %r is not a raster image, the image inspector skips the file":
        "формат %r не растровое изображение, инспектор картинок пропускает файл",
    "format %s is deliberately not cleaned: rebuilding it without re-encoding risks damaging the file":
        "формат %s чистка намеренно не берёт: пересборка без перекодирования рискует испортить файл",
    "format %s is inspect-only for now; cleaning arrives in phase %d":
        "формат %s пока только инспектируется, чистка появится в фазе %d",
    "format changed: %s -> %s":
        "формат изменился: %s -> %s",
    "format not recognized":
        "формат не опознан",
    "format not recognized by signature: deep inspection is impossible, only the size and the fact that it wasn't recognized are known.":
        "формат не опознан по сигнатуре -- глубокая инспекция невозможна, известны только размер и факт нераспознавания.",
    "format not recognized; nothing to clean it with.":
        "формат не опознан, чистить нечем.",
    "format not specified":
        "формат не указан",
    "format unknown":
        "формат неизвестен",
    "fpdf2/PyFPDF Python library":
        "python-библиотека fpdf2/PyFPDF",
    "gamma":
        "гамма",
    "gamma %.5f":
        "гамма %.5f",
    "handed over, but cleanliness not proven: %d original values in the output, %d inspector notes":
        "выдан, но чистота не доказана: исходных значений в результате %d, замечаний инспектора %d",
    "hazard: the file has macros (active content). STEALTH doesn't remove them: that would break the document, and the decision is the user's. The project may contain the author's names and path.":
        "hazard: в файле макросы (активное содержимое). STEALTH их не удаляет: это сломало бы документ, решение за пользователем. Внутри проекта могут быть имена и путь автора.",
    "header":
        "заголовок",
    "hidden":
        "скрытый",
    "high":
        "высокая",
    "histogram":
        "гистограмма",
    "identifier":
        "идентификатор",
    "image":
        "картинка",
    "image (object %d)":
        "картинка (объект %d)",
    "image (object %d) ":
        "картинка (объект %d) ",
    "image inside the PDF: ":
        "картинка внутри PDF: ",
    "images":
        "картинки",
    "infe v%d (type only in content_type)":
        "infe v%d (тип только в content_type)",
    "inspection":
        "инспекция",
    "inspection: module failed to load":
        "инспекция: модуль не загрузился",
    "internal cleaning error: %s: %s":
        "внутренняя ошибка чистки: %s: %s",
    "internal error while processing: %s":
        "внутренняя ошибка при обработке: %s",
    "internal error: %s":
        "внутренняя ошибка: %s",
    "internal error: negative span":
        "внутренняя ошибка: отрицательный отрезок",
    "invalid CRC of chunk %s: the file is damaged or was edited by hand":
        "неверная CRC чанка %s: файл повреждён или правился вручную",
    "invalid TIFF signature":
        "неверная сигнатура TIFF",
    "invalid chunk type at offset %d":
        "некорректный тип чанка на смещении %d",
    "invalid entry name (escapes the folder or absolute path): %s":
        "недопустимое имя записи (выход за каталог или абсолютный путь): %s",
    "is 0":
        "равен 0",
    "is absent":
        "отсутствует",
    "item list: Exif and XMP are visible here":
        "список элементов: тут видно Exif и XMP",
    "item offsets":
        "смещения элементов",
    "item properties (incl. ICC)":
        "свойства элементов (в т.ч. ICC)",
    "kept":
        "сохранён",
    "kept on purpose":
        "сохранено намеренно",
    "kept on purpose %d":
        "сохранено намеренно %d",
    "kept per the user's whitelist (keep)":
        "оставлено по whitelist пользователя (keep)",
    "keys: %d":
        "ключей: %d",
    "keywords":
        "ключевые слова",
    "language":
        "язык",
    "last printed":
        "последняя печать",
    "last saved by":
        "кто сохранил последним",
    "layers":
        "слои",
    "layers and structural":
        "слои и структурное",
    "layers switched off":
        "выключенные слои",
    "less than a minute":
        "меньше минуты",
    "limit of %d elements":
        "предел %d элементов",
    "list of new signals could not be read":
        "список новых сигналов не прочитался",
    "low":
        "низкая",
    "macOS (Quartz PDFContext): the file was made on a Mac":
        "macOS (Quartz PDFContext) -- файл сделан на Mac",
    "made with %s":
        "сделан через %s",
    "mailto link":
        "Ссылка mailto",
    "marker 0x%02X declares length %d":
        "маркер 0x%02X объявляет длину %d",
    "marker 0x%02X without a length (the file is truncated)":
        "маркер 0x%02X без длины (файл обрезан)",
    "medium":
        "средняя",
    "meta boxes (%d)":
        "Боксы meta (%d)",
    "meta holds the item descriptions: thumbnails, EXIF, XMP":
        "Внутри meta лежат описания элементов: миниатюры, EXIF, XMP",
    "meta:generator is empty or missing: usually a sign that the metadata has already been cleaned.":
        "meta:generator пуст или отсутствует: обычно признак того, что метаданные уже чистили.",
    "metadata container":
        "контейнер метаданных",
    "mime item (usually XMP)":
        "Элемент mime (обычно XMP)",
    "mirrored and rotated 270°":
        "зеркало и поворот на 270°",
    "mirrored and rotated 90°":
        "зеркало и поворот на 90°",
    "mirrored horizontally":
        "зеркально по горизонтали",
    "mirrored vertically":
        "зеркально по вертикали",
    "missing parts: %s":
        "нет частей: %s",
    "mixed CRLF and LF":
        "смешанный CRLF и LF",
    "modification time":
        "время правки",
    "modified":
        "изменён",
    "more than %d blocks":
        "больше %d блоков",
    "more than %d chunks":
        "больше %d чанков",
    "more than %d segments":
        "больше %d сегментов",
    "multi-volume archives are not supported":
        "многотомный архив не поддерживается",
    "nested archive (%s): contents NOT cleaned, archives are not unpacked":
        "вложенный архив (%s): содержимое НЕ чищено, архивы не разворачиваются",
    "neutral":
        "нейтральные",
    "no":
        "нет",
    "no GIF signature":
        "нет сигнатуры GIF",
    "no IDAT":
        "нет IDAT",
    "no PNG signature":
        "нет сигнатуры PNG",
    "no RIFF/WEBP header":
        "нет заголовка RIFF/WEBP",
    "no SOI marker":
        "нет маркера SOI",
    "no SOS":
        "нет SOS",
    "no TIFF header":
        "нет заголовка TIFF",
    "no cleaning is provided for file type \"%s\"":
        "для типа файла «%s» чистка не предусмотрена",
    "no exiftool: MakerNotes are not decoded (how to install -- docs/GUIDE.md, section 7)":
        "exiftool нет: MakerNotes не расшифровываются (как поставить -- docs/GUIDE.md, раздел 7)",
    "no line breaks":
        "переводов строк нет",
    "no meta.xml: nothing to clean, no output created.":
        "нет meta.xml: чистить нечего, выход не создан.",
    "no namespace":
        "без пространства имён",
    "no read access (errno 13): either there's no permission for the file or it's locked by another process (most often open in Word or Excel). Close the file, check permissions and try again.":
        "нет доступа на чтение (errno 13): либо нет прав на файл, либо он занят другим процессом (чаще всего открыт в Word или Excel). Закройте файл, проверьте права доступа и повторите проверку.",
    "no read access: no permission, or the file is locked by another process (most often open in Word or Excel).":
        "нет доступа на чтение: нет прав или файл занят другим процессом (чаще всего открыт в Word или Excel).",
    "no read access: no permission, or the file is locked by another process.":
        "нет доступа на чтение: нет прав или файл занят другим процессом.",
    "no read permission":
        "нет прав на чтение",
    "no reason given":
        "причина не указана",
    "no rsid at all":
        "нет ни одного rsid",
    "no schemaRef":
        "без schemaRef",
    "no units":
        "без единиц",
    "no units (aspect ratio)":
        "без единиц (пропорция)",
    "no values":
        "значений нет",
    "no {\\rtf signature at the start of the file -- parsing as is":
        "нет сигнатуры {\\rtf в начале файла -- разбираем как есть",
    "non-standard field":
        "нестандартное поле",
    "none":
        "нет",
    "normalized":
        "нормализовано",
    "not a raster image (format %s): image cleaning doesn't apply":
        "не растровое изображение (формат %s): чистка картинок не применима",
    "not a stream":
        "не поток",
    "not assessed":
        "не оценены",
    "not parsed as XML":
        "не разобрано как XML",
    "not set":
        "не задано",
    "not specified":
        "не указано",
    "not verified":
        "не проверено",
    "nothing to remove":
        "нечего убирать",
    "noticeable on a closer look":
        "заметны при внимательном взгляде",
    "object":
        "объект",
    "object %d":
        "объект %d",
    "object sweep":
        "сквозной обход объектов",
    "obvious: the file is recognizable as processed (a limit of the format, see the regenerate profile)":
        "очевидны: файл опознаётся как обработанный (предел формата, см. профиль regenerate)",
    "off":
        "выключено",
    "offset %d is outside the stream (%d bytes)":
        "смещение %d вне потока (%d байт)",
    "olefile is not installed -- OLE2 parsing is impossible":
        "olefile не установлен -- разбор OLE2 невозможен",
    "on":
        "включено",
    "online service (Canva/Figma/Google)":
        "онлайн-сервис (Canva/Figma/Google)",
    "orientation lost: %s -> %s":
        "ориентация потеряна: %s -> %s",
    "original":
        "исходные",
    "other XMP fields":
        "прочие поля XMP",
    "p. %d":
        "стр. %d",
    "p. %d /AA":
        "стр. %d /AA",
    "p. %d /Annots":
        "стр. %d /Annots",
    "p. %d /Annots /A":
        "стр. %d /Annots /A",
    "p. %d /FileAttachment":
        "стр. %d /FileAttachment",
    "p. 1 /MediaBox":
        "стр. 1 /MediaBox",
    "p:modifyVerifier — write password":
        "p:modifyVerifier — пароль на запись",
    "page":
        "страница",
    "page count changed: %d -> %d":
        "число страниц изменилось: %d -> %d",
    "pages and annotations":
        "страницы и аннотации",
    "palette":
        "палитра",
    "parsing of %s aborted: %s: %s":
        "разбор %s прерван: %s: %s",
    "parsing of changes in %s aborted: %r":
        "разбор правок в %s прерван: %r",
    "part %s could not be read: %r":
        "часть %s не читается: %r",
    "part %s is not in the package":
        "части %s нет в пакете",
    "part %s is too large (%d MB): editing is impossible":
        "часть %s слишком велика (%d МБ): правка невозможна",
    "part %s is too large (%d bytes), skipped":
        "часть %s слишком велика (%d байт), пропущена",
    "part %s skipped: %d bytes, over the limit":
        "часть %s пропущена: %d байт, больше лимита",
    "part %s skipped: compression ratio %.0f looks like a bomb":
        "часть %s пропущена: коэффициент сжатия %.0f похож на бомбу",
    "paths and printer":
        "пути и принтер",
    "pdf.open_metadata() can't be read: %s":
        "pdf.open_metadata() не читается: %s",
    "pdfTeX/LaTeX: the file has /PTEX.Fullbanner: %s":
        "pdfTeX/LaTeX: в файле есть /PTEX.Fullbanner -- %s",
    "personId — reference to the author list":
        "personId — ссылка на список авторов",
    "pikepdf could not open the file (%s): %s":
        "pikepdf не смог открыть файл (%s): %s",
    "pixels":
        "пиксели",
    "portfolio":
        "портфолио",
    "present":
        "есть",
    "present, %d bytes":
        "присутствует, %d байт",
    "present, opens with an empty password":
        "есть, открывается пустым паролем",
    "printed from headless Chrome/Chromium (Skia/PDF engine): Puppeteer/Playwright or \"Save as PDF\"":
        "печать из headless Chrome/Chromium (движок Skia/PDF) -- Puppeteer/Playwright или «Сохранить как PDF»",
    "producer not recognized; %s":
        "продюсер не опознан; %s",
    "properties":
        "свойства",
    "property %s could not be parsed: %r":
        "свойство %s не разобралось: %r",
    "px/m":
        "пикс/м",
    "python -m core.clean <file>":
        "python -m core.clean <файл>",
    "python -m core.inspect <file>":
        "python -m core.inspect <файл>",
    "qpdf binary marker":
        "бинарный маркер qpdf",
    "r:id=%s (not resolved)":
        "r:id=%s (не разрешён)",
    "raw bytes":
        "сырые байты",
    "real save time":
        "реальное время сохранения",
    "rebuilding the ODF failed":
        "пересборка ODF не удалась",
    "recursion: %s can't be written to the temp folder (errno %s), skipped":
        "рекурсия: %s не удалось записать во временную папку (errno %s), пропущен",
    "recursion: %s container failed to reopen: %s":
        "рекурсия: %s-контейнер повторно не открылся: %s",
    "recursion: %s exceeds the %d MB limit, skipped":
        "рекурсия: %s больше лимита %d МБ, пропущен",
    "recursion: PDF attachment %r can't be read: %s":
        "рекурсия: вложение PDF %r не читается: %s",
    "recursion: PDF attachment %r exceeds the %d MB limit once decompressed, or its size can't be bounded, skipped":
        "рекурсия: вложение PDF %r после распаковки превысит лимит %d МБ или его размер не удаётся ограничить, пропущено",
    "recursion: embedded-object budget exhausted in %s; some embedded items were not inspected":
        "рекурсия: бюджет вложенных объектов исчерпан в %s, часть вложений не инспектирована",
    "recursion: embedded-object budget exhausted in the PDF; some attachments were not inspected":
        "рекурсия: бюджет вложенных объектов исчерпан в PDF, часть вложений не инспектирована",
    "recursion: the PDF failed to reopen: %s":
        "рекурсия: PDF повторно не открылся: %s",
    "recursion: walking PDF attachments was interrupted: %s":
        "рекурсия: обход вложений PDF прерван: %s",
    "recursive inspection of embedded items crashed entirely: %s: %s":
        "рекурсивная инспекция вложений упала целиком: %s: %s",
    "refreshedBy — who refreshed the pivot":
        "refreshedBy — кто обновлял сводную",
    "refreshedBy — who refreshed the pivot table":
        "refreshedBy — кто обновлял сводную",
    "refreshedDate — when it was refreshed":
        "refreshedDate — когда обновляли",
    "regenerated":
        "перегенерировано",
    "regeneration":
        "пересоздание",
    "remainder not explained":
        "остаток не объяснён",
    "removed":
        "удалено",
    "replace: a {part name: bytes} dict is required":
        "replace: нужен словарь {имя части: bytes}",
    "replacing a part that doesn't exist: %s":
        "замена несуществующей части: %s",
    "replacing an entry with bit 3 and real ZIP64 sizes in the local header is not supported":
        "замена записи с битом 3 и настоящими ZIP64-размерами в локальном заголовке не поддерживается",
    "report failed to build: %r":
        "отчёт не собрался: %r",
    "result not verified":
        "результат не проверен",
    "result: ":
        "результат: ",
    "revision author table could not be parsed: %r":
        "таблица авторов правок не разобралась: %r",
    "revision number":
        "номер ревизии",
    "revisions and comments":
        "правки и комментарии",
    "root entry":
        "корневая запись",
    "rotated 180°":
        "поворот на 180°",
    "rotated 270° clockwise (90° counterclockwise)":
        "поворот на 270° по часовой (90° против)",
    "rotated 90° clockwise":
        "поворот на 90° по часовой стрелке",
    "rsid attributes":
        "rsid-атрибуты",
    "rsid attributes in the body":
        "rsid-атрибутов в теле",
    "rsid entirely absent":
        "rsid отсутствуют полностью",
    "rupBuild gives the exact Excel build, lastEdited and lowestEdited — the range of versions in which the workbook was edited.":
        "rupBuild указывает точную сборку Excel, lastEdited и lowestEdited — диапазон версий, в которых книгу правили.",
    "sRGB color space":
        "Цветовое пространство sRGB",
    "section failed (%s), details in errors":
        "сбой секции (%s), подробности в errors",
    "section failed: %s":
        "сбой секции: %s",
    "segment 0x%02X is truncated (%d bytes needed, %d available)":
        "сегмент 0x%02X обрезан (нужно %d байт, есть %d)",
    "server-side PDF generation library":
        "серверная библиотека генерации PDF",
    "set":
        "установлен",
    "settings.xml still holds %d entries with paths/printer: they aren't visible in the document properties and survive their cleaning.":
        "В settings.xml осталось %d записей с путями/принтером: они не видны в свойствах документа и переживают их очистку.",
    "several EOCD signatures at the end of the file: parsers would read different archives":
        "в конце файла несколько сигнатур EOCD: парсеры прочтут разный архив",
    "signals were not built: %s: %s":
        "сигналы не построены: %s: %s",
    "signature":
        "подпись",
    "significant bits":
        "значащие биты",
    "size unknown":
        "размер неизвестен",
    "software field names an AI tool: %s":
        "поле софта называет ИИ-инструмент: %s",
    "software field: %s":
        "поле софта: %s",
    "source: ":
        "исходник: ",
    "status":
        "статус",
    "stream %s could not be read: %r":
        "поток %s не читается: %r",
    "streaming parse limit of %d MB":
        "предел потокового разбора %d МБ",
    "streaming pass over the file aborted: %r":
        "потоковый проход по файлу прерван: %r",
    "streams":
        "потоки",
    "structure":
        "структура",
    "subject":
        "тема",
    "suggested palette":
        "доп. палитра",
    "suspected archive bomb (compression ratio above %d)":
        "подозрение на архивную бомбу (коэффициент сжатия выше %d)",
    "suspected zip bomb: %d bytes uncompressed from %d compressed":
        "подозрение на zip-бомбу: распакованный объём %d байт при сжатом %d",
    "table %d (%d-bit)":
        "таблица %d (%d бит)",
    "target not resolved":
        "цель не разрешена",
    "text":
        "текст",
    "the \"Microsoft Print to PDF\" virtual printer":
        "виртуальный принтер «Microsoft Print to PDF»",
    "the EOCD says ZIP64, but there's no ZIP64 EOCD record":
        "в EOCD указан ZIP64, но записи ZIP64 EOCD нет",
    "the IDAT stream doesn't decompress (%s)":
        "поток IDAT не распаковывается (%s)",
    "the IDAT stream is cut off: the image is truncated":
        "поток IDAT оборван: изображение обрезано",
    "the ODF container did not open as a ZIP: %r":
        "ODF-контейнер не открылся как ZIP: %r",
    "the OLE2 container failed to open: %r":
        "OLE2-контейнер не открылся: %r",
    "the RIFF tail is shorter than a chunk header":
        "хвост RIFF короче заголовка чанка",
    "the XML doesn't parse: %s":
        "XML не разбирается: %s",
    "the ZIP entry list could not be read: %r":
        "список записей ZIP не читается: %r",
    "the ZIP64 EOCD overlaps the locator":
        "ZIP64 EOCD налезает на локатор",
    "the \\info group could not be parsed: %r":
        "группа \\info не разобралась: %r",
    "the archive directory can't be read, cleaning will most likely refuse":
        "каталог архива не читается, чистка, скорее всего, откажет",
    "the archive has %d entries, the first %d were parsed":
        "в архиве %d записей, разобраны первые %d",
    "the archive looks like a zip bomb: %d bytes declared at a compression ratio of %.0f; entry contents are not unpacked":
        "архив похож на zip-бомбу: заявлено %d байт при коэффициенте сжатия %.0f -- содержимое записей не распаковываем",
    "the central directory doesn't fit before the end of the archive (data prepended to the ZIP with shifted offsets, or corruption)":
        "центральный каталог не укладывается до конца архива (данные перед ZIP со сдвинутыми смещениями или порча)",
    "the central directory is cut off":
        "центральный каталог оборван",
    "the central directory is larger than %d MB":
        "центральный каталог больше %d МБ",
    "the central directory parsed differently than expected":
        "центральный каталог разобрался не так, как ожидалось",
    "the cleaning module (core/clean.py) did not load":
        "модуль чистки (core/clean.py) не загрузился",
    "the compressed image data changed: the file was re-encoded or damaged":
        "сжатые данные изображения изменились: файл перекодирован или повреждён",
    "the compression method in the local header doesn't match the directory":
        "метод сжатия в локальном заголовке не совпадает с каталогом",
    "the container has %d entries, parsing the first %d":
        "в контейнере %d записей, разбираем первые %d",
    "the custom property dictionary is truncated: %s":
        "словарь пользовательских свойств оборван: %s",
    "the edit broke the RTF structure. No output created.":
        "правка сломала структуру RTF. Выход не создан.",
    "the entry grew beyond %d MB when decompressed":
        "запись при распаковке выросла больше %d МБ",
    "the entry is damaged or won't decompress":
        "запись повреждена или не распаковывается",
    "the entry is encrypted":
        "запись зашифрована",
    "the entry is encrypted and can't be read":
        "запись зашифрована, прочитать нельзя",
    "the entry is larger than %d MB":
        "запись больше %d МБ",
    "the entry offset exceeds 4 GB":
        "смещение записи превышает 4 ГБ",
    "the extension is truncated":
        "расширение обрезано",
    "the file can't be read (errno %s)":
        "файл не читается (errno %s)",
    "the file can't be read (errno %s).":
        "файл не читается (errno %s).",
    "the file can't be read: %s":
        "файл не читается: %s",
    "the file cannot be read: %s":
        "файл не читается: %s",
    "the file contains GPS coordinates: %s -- the shooting location can be pinned down to a house":
        "в файле есть GPS-координаты: %s -- точка съёмки восстанавливается до дома",
    "the file contains a signed C2PA manifest: provenance is documented cryptographically, and stripping it is a detectable event":
        "в файле лежит подписанный манифест C2PA: происхождение задокументировано криптографически, а его срез -- детектируемое событие",
    "the file could not be read (%s): content unavailable, the check is impossible.":
        "файл не удалось прочитать (%s) -- содержимое недоступно, проверка невозможна.",
    "the file did not open: %r":
        "файл не открылся: %r",
    "the file didn't open: %s":
        "файл не открылся: %s",
    "the file exceeds the %d-byte limit (actual size %d bytes); inspection was not run":
        "файл больше лимита %d байт (фактический размер %d байт), инспекция не выполнена",
    "the file is a cloud placeholder: open it in Explorer or choose \"Always keep on this device\"":
        "файл является облачным плейсхолдером: откройте его в проводнике или выберите «Всегда хранить на этом устройстве»",
    "the file is a cloud provider placeholder (OneDrive and the like): there's no content on disk. Open it in Explorer or choose \"Always keep on this device\", wait for the download and try again.":
        "файл -- плейсхолдер облачного провайдера (OneDrive и аналоги): на диске нет содержимого. Откройте его в проводнике или выберите «Всегда хранить на этом устройстве», дождитесь скачивания и повторите.",
    "the file is a cloud provider placeholder (OneDrive and the like, Files On-Demand mode): only metadata is on disk, there's no content, so it can't be checked. This doesn't mean the file is damaged. Open the file in Explorer or choose \"Always keep on this device\", wait for the download and check again.":
        "файл -- плейсхолдер облачного провайдера (OneDrive и аналоги, режим Files On-Demand): на диске лежат только метаданные, содержимого нет, поэтому проверить его нельзя. Это не значит, что файл повреждён. Откройте файл в проводнике или выберите «Always keep on this device» («Всегда хранить на этом устройстве»), дождитесь скачивания и повторите проверку.",
    "the file is a cloud provider placeholder: there's no content on disk.":
        "файл -- плейсхолдер облачного провайдера: на диске нет содержимого.",
    "the file is a link or reparse point.":
        "файл -- ссылка или точка повторной обработки.",
    "the file is a link or reparse point; its content can't be read.":
        "файл -- ссылка или точка повторной обработки, содержимое не читается.",
    "the file is a link or reparse point; its content can't be read. If the file comes from the cloud, open it in Explorer, wait for the download and check again.":
        "файл -- ссылка или точка повторной обработки (reparse point), её содержимое не читается. Если файл из облака, откройте его в проводнике, дождитесь скачивания и повторите проверку.",
    "the file is cut off: no EOI marker":
        "файл оборван: нет маркера EOI",
    "the file is cut off: no IEND chunk":
        "файл оборван: нет чанка IEND",
    "the file is cut off: no trailing 0x3B byte":
        "файл оборван: нет завершающего байта 0x3B",
    "the file is empty":
        "файл пуст",
    "the file is empty (0 bytes): it holds neither data nor metadata. It may not have finished downloading or saving; check the source.":
        "файл пустой (0 байт): в нём нет ни данных, ни метаданных. Возможно, он не докачался или не сохранился -- проверьте источник.",
    "the file is empty (0 bytes); it may not have finished downloading.":
        "файл пуст (0 байт), возможно, не докачался.",
    "the file is empty or the format wasn't recognized":
        "файл пустой или формат не опознан",
    "the file is larger than %d MB":
        "файл больше %d МБ",
    "the file is larger than %d MB: not loaded into memory":
        "файл больше %d МБ: не берём в память",
    "the file is larger than %d MB: partial cleaning isn't allowed, no output created.":
        "файл больше %d МБ: чистить частично нельзя, выход не создан.",
    "the file is larger than %d MB; BOM and line endings were determined from the first %d MB":
        "файл больше %d МБ, BOM и переводы строк определены по первым %d МБ",
    "the file is not an OLE2 container (no D0CF11E0 signature)":
        "файл не является OLE2-контейнером (нет сигнатуры D0CF11E0)",
    "the file is recognizable as processed":
        "файл опознаётся как обработанный",
    "the file is shorter than the smallest ZIP: not a ZIP":
        "файл короче минимального ZIP: это не ZIP",
    "the file looks scrubbed: the fact of processing is visible":
        "файл выглядит чищеным: факт обработки виден",
    "the file was cut off while reading":
        "файл оборван при чтении",
    "the file was made by a library or converter: %s":
        "файл сделан библиотекой или конвертером: %s",
    "the file was not read completely":
        "файл прочитан не полностью",
    "the file went through a graphics editor: %s":
        "файл прошёл через графический редактор: %s",
    "the file's content would be lost":
        "потеряется содержимое файла",
    "the first chunk must be IHDR of length 13":
        "первым чанком должен идти IHDR длиной 13",
    "the global palette is truncated":
        "обрезана глобальная палитра",
    "the image data is cut off":
        "данные изображения оборваны",
    "the image data itself":
        "сами данные изображения",
    "the image descriptor is truncated":
        "дескриптор изображения обрезан",
    "the input file changed during processing: the result is unreliable":
        "входной файл изменился во время работы: результат ненадёжен",
    "the input file couldn't be re-read (errno %s)":
        "входной файл не перечитался (errno %s)",
    "the inspector crashed entirely: %s: %s":
        "инспектор упал целиком: %s: %s",
    "the inspector doesn't see any":
        "инспектор их не видит",
    "the inspector sees no signs of processing":
        "инспектор признаков обработки не видит",
    "the name in the local header doesn't match the central directory":
        "имя в локальном заголовке не совпадает с центральным каталогом",
    "the output file on disk doesn't match what was built":
        "выходной файл на диске не совпал с собранным",
    "the output matches the input: the input is never modified":
        "выход совпадает со входом: вход не модифицируется никогда",
    "the output path matches the input path: the input file is never overwritten":
        "выходной путь совпадает с входным: входной файл не перезаписывается никогда",
    "the output won't open with the standard zipfile: %s":
        "выход не открывается стандартным zipfile: %s",
    "the output's entries or their order don't match expectations":
        "состав или порядок записей выхода не совпал с ожидаемым",
    "the package has %d entries, the limit is %d":
        "записей в пакете %d, предел %d",
    "the package has no [Content_Types].xml":
        "в пакете нет [Content_Types].xml",
    "the part being replaced is larger than %d MB":
        "заменяемая часть больше %d МБ",
    "the part being replaced is too large":
        "заменяемая часть слишком велика",
    "the part has a DOCTYPE or an entity declaration: OOXML never has them":
        "в части есть DOCTYPE или объявление сущности: в OOXML их не бывает",
    "the part's encoding isn't UTF-8: byte-level editing is impossible":
        "кодировка части не UTF-8: правка по байтам невозможна",
    "the producer":
        "производитель",
    "the rebuild failed":
        "пересборка не удалась",
    "the replacement was written incorrectly":
        "замена записана неверно",
    "the result doesn't open as ZIP":
        "результат не открывается как ZIP",
    "the result doesn't specify the file format; cleaning not confirmed":
        "в результате не указан формат файла, чистка не подтверждена",
    "the result fails the CRC check":
        "результат не проходит проверку CRC",
    "the result has no [Content_Types].xml":
        "в результате нет [Content_Types].xml",
    "the result is linearized":
        "результат линеаризован",
    "the same part is in both replace and drop":
        "одна и та же часть и в replace, и в drop",
    "the stream ends on a marker":
        "поток обрывается на маркере",
    "there is no cleaning for format \"%s\"":
        "для формата «%s» чистки нет",
    "third-party PDF editor":
        "сторонний PDF-редактор",
    "this is a directory, not a file":
        "это каталог, а не файл",
    "this is a directory, not a file.":
        "это каталог, а не файл.",
    "this is not RTF: no {\\rtf signature. No output created.":
        "это не RTF: нет сигнатуры {\\rtf. Выход не создан.",
    "thumbnail":
        "миниатюра",
    "timestamps shown for the first %d of %d entries":
        "таймстемпы показаны для первых %d записей из %d",
    "title":
        "заголовок",
    "total uncompressed size exceeded %d bytes; the remaining entries are not parsed":
        "суммарный несжатый размер превысил %d байт -- дальше записи не разбираем",
    "track tables (as in video)":
        "таблицы дорожек (как в видео)",
    "trailer /ID can't be read: %s":
        "trailer /ID не читается: %s",
    "transparency":
        "прозрачность",
    "truncated EOCD: the archive comment is longer than the rest of the file":
        "EOCD обрезан: комментарий архива длиннее остатка файла",
    "two directory records point to the same data":
        "две записи каталога указывают на одни данные",
    "unchanged":
        "без изменений",
    "unexpected marker 0x%02X outside entropy-coded data":
        "неожиданный маркер 0x%02X вне энтропийных данных",
    "unknown block 0x%02X at offset %d":
        "неизвестный блок 0x%02X на смещении %d",
    "unknown cleaning profile: %s":
        "неизвестный профиль чистки: %s",
    "unnamed":
        "без имени",
    "unreadable XMP":
        "нечитаемый XMP",
    "unremovable":
        "не убрать",
    "unsafe entry name (escapes the folder, drive letter, special characters)":
        "небезопасное имя записи (выход за каталог, диск, спецсимволы)",
    "userId — usually a real email":
        "userId — как правило, реальный email",
    "userName is the name of the user who reserved writing; the password is its hash.":
        "userName -- имя пользователя, зарезервировавшего запись; пароль -- его хеш.",
    "verification didn't finish: %s":
        "проверка не завершилась: %s",
    "verification doesn't support format %s":
        "формат %s проверка не поддерживает",
    "verification not performed: %s":
        "проверка не выполнена: %s",
    "verification of the result failed":
        "проверка результата не пройдена",
    "version":
        "версия",
    "version %d, transform=%s":
        "версия %d, transform=%s",
    "version %d.%02d, density %dx%d, %s":
        "версия %d.%02d, плотность %dx%d, %s",
    "version %d.%02d, density %dx%d, units %d":
        "версия %d.%02d, плотность %dx%d, единицы %d",
    "very hidden":
        "особо скрытый",
    "w15:presenceInfo/@userId is usually a real work email. The part is pure identity and was removed entirely together with its reference in document.xml.rels and its Override in [Content_Types].xml.":
        "w15:presenceInfo/@userId -- как правило, реальный рабочий email. Часть -- чистая идентичность, убрана целиком вместе со ссылкой в document.xml.rels и Override в [Content_Types].xml.",
    "w15:presenceInfo/@userId — in the overwhelming majority of cases this is a real work email.":
        "w15:presenceInfo/@userId — в подавляющем большинстве случаев это реальный рабочий email.",
    "w:%s — document protection":
        "w:%s — защита документа",
    "w:%s — password hash and salt":
        "w:%s — хеш и соль пароля",
    "w:%s — shape defaults":
        "w:%s — умолчания фигур",
    "w:attachedTemplate — attached template":
        "w:attachedTemplate — присоединённый шаблон",
    "w:docVars — document variables":
        "w:docVars — переменные документа",
    "w:mailMerge — mail merge data source":
        "w:mailMerge — источник данных слияния",
    "w:permStart — permitted editor":
        "w:permStart — разрешённый редактор",
    "w:permStart/@ed — name of the permitted editor":
        "w:permStart/@ed — имя разрешённого редактора",
    "w:proofState — proofing state":
        "w:proofState — состояние проверки",
    "w:rsid — edit session identifiers":
        "w:rsid — идентификаторы сессий правки",
    "w:rsid — editing session IDs":
        "w:rsid — идентификаторы сессий правки",
    "w:trackRevisions — change tracking":
        "w:trackRevisions — запись исправлений",
    "was %s":
        "было %s",
    "was %s, now 0":
        "было %s, стало 0",
    "wkhtmltopdf (HTML -> PDF via Qt WebKit)":
        "wkhtmltopdf (HTML -> PDF через Qt WebKit)",
    "workbookProtection — password hash":
        "workbookProtection — хеш пароля",
    "x15ac:absPath — absolute path of the save folder":
        "x15ac:absPath — абсолютный путь папки сохранения",
    "x15ac:absPath — folder where the workbook was saved":
        "x15ac:absPath — папка, где сохраняли книгу",
    "yes":
        "да",
    "• %s (%s) → found: %s":
        "• %s (%s) → найдено: %s",
    "• %s <i>(confidence: %s)</i>":
        "• %s <i>(уверенность: %s)</i>",
    "• <b>Regenerate</b> -- save anew through LibreOffice. There is no cleaning trace because nobody cleaned the file, but the application becomes LibreOffice and the layout may change. No LibreOffice -- a refusal, not a different cleaning. The \"Keep\" toggles don't work with regeneration: the bot will refuse rather than silently ignore them.\n":
        "• <b>Пересоздать</b> -- сохранить заново через LibreOffice. Следа чистки нет, потому что файл никто не чистил, но приложение становится LibreOffice и вёрстка может измениться. Нет LibreOffice -- отказ, а не другая чистка. Переключатели «Сохранить» с пересозданием не работают: бот откажет, а не проигнорирует их молча.\n",
    "• <i>(entry didn't fit in one Telegram message and was skipped; see the text report)</i>":
        "• <i>(запись не влезла в одно сообщение Telegram и пропущена, смотрите текстовый отчёт)</i>",
    "• <i>(line didn't fit in one Telegram message and was skipped; see the text report)</i>":
        "• <i>(строка не влезла в одно сообщение Telegram и пропущена — смотрите текстовый отчёт)</i>",
    "… and %d more":
        "… и ещё %d",
    "ℹ <b>No explicit metadata found.</b>":
        "ℹ <b>Явных метаданных не обнаружено.</b>",
    "ℹ The report is empty.":
        "ℹ Отчёт пуст.",
    "⚠ <b>Could not parse</b> — %d":
        "⚠ <b>Не удалось разобрать</b> — %d",
    "⚠ <b>Metadata was not extracted.</b>":
        "⚠ <b>Метаданные не извлечены.</b>",
    "⚠ <b>Report truncated</b>: showing the first %d messages of %d.\nThe rest is not lost -- it is in the full report: <code>%s</code>.\n<i>The limit keeps Telegram from blocking the bot for a flood of messages.</i>":
        "⚠ <b>Отчёт обрезан</b>: показаны первые %d сообщений из %d.\nОстальное не потерялось -- оно есть в полном отчёте: <code>%s</code>.\n<i>Лимит стоит, чтобы бот не был заблокирован Telegram за поток сообщений.</i>",
    "⚠ <b>The report arrived incompletely</b>: messages not sent -- %d of %d.\nTreat the report as incomplete, not empty. Full report: <code>%s</code>.":
        "⚠ <b>Отчёт дошёл не полностью</b>: не отправлено сообщений -- %d из %d.\nСчитайте отчёт неполным, а не пустым. Полный: <code>%s</code>.",
    "⚠ <i>report section failed to render: %s</i>":
        "⚠ <i>секция отчёта не собралась: %s</i>",
    "⚠ Cleanliness is not proven.":
        "⚠ Чистота не доказана.",
    "⚠ Could not build the report: <code>%s</code>":
        "⚠ Не удалось собрать отчёт: <code>%s</code>",
    "⚠ Critical findings with their previous values remain in the result: %d. Short values (up to 5 characters) are not compared by the substring check, see the report below.":
        "⚠ В результате остались критичные находки с прежними значениями: %d. Короткие значения (до 5 символов) проверка по подстроке не сравнивает, смотрите отчёт ниже.",
    "⚠ Profile <b>%s</b>: cleanliness is NOT proven, read the warning above.":
        "⚠ Профиль <b>%s</b>: чистота НЕ доказана, прочтите предупреждение выше.",
    "⚠ Profile <b>%s</b>: the archive contains only what is cleaned and verified -- %d of %d. The rest are <b>NOT included</b> and you have them in their original form: see the report.":
        "⚠ Профиль <b>%s</b>: в архиве только очищенное и проверенное -- %d из %d. Остальные <b>НЕ включены</b> и у вас в исходном виде: см. отчёт.",
    "⚠ The cleaning report could not be built (%s). Consider the file NOT cleaned.":
        "⚠ Отчёт о чистке не собрался (%s). Считайте файл НЕ почищенным.",
    "⚠ The inspector did not parse part of the file (%d): the proof is incomplete, something could have remained in the unparsed part.":
        "⚠ Инспектор не разобрал часть файла (%d): доказательство неполное, в неразобранном могло остаться.",
    "⚠ The report is empty. Consider the file NOT cleaned.":
        "⚠ Отчёт пуст. Считайте файл НЕ почищенным.",
    "⚠ The result was written without re-inspection: cleanliness can't be claimed.":
        "⚠ Результат записан без повторной инспекции: чистоту утверждать нельзя.",
    "⛔ <b>Archive NOT cleaned</b> -- no output archive, the bot sends nothing. Reason: %s":
        "⛔ <b>Архив НЕ почищен</b> -- выходного архива нет, бот ничего не отправляет. Причина: %s",
    "⛔ <b>File NOT cleaned</b> -- cleaning failed, there is no cleaned copy, the bot sends no file. The reason and details are below.":
        "⛔ <b>Файл НЕ почищен</b> -- чистка не удалась, почищенной копии нет, бот никакой файл не отправляет. Причина и подробности ниже.",
    "⛔ <b>No batch cleaning</b>: the core/clean_batch.py module failed to load. The ZIP is not cleaned and not changed.":
        "⛔ <b>Пакетной чистки нет</b>: модуль core/clean_batch.py не загрузился. ZIP не чищен и не изменён.",
    "⛔ <b>No file was cleaned</b> -- there is no archive with a result. The reasons for each file are in the report above.":
        "⛔ <b>Ни один файл не очищен</b> -- архива с результатом нет. Причины по каждому файлу -- в отчёте выше.",
    "⛔ <b>Original identity or environment values remained in the file: %d.</b>":
        "⛔ <b>Исходные значения личности или окружения остались в файле: %d.</b>",
    "⛔ <b>Result not sent</b>: the archive weighs %.1f MB, and the Telegram Bot API doesn't let bots send files larger than %d MB (that is an API limit, not the bot's). The files are cleaned, but they can't be delivered: split the original archive into parts and send again.":
        "⛔ <b>Результат не отправлен</b>: архив весит %.1f МБ, а Telegram Bot API не даёт ботам отправлять файлы крупнее %d МБ (это лимит API, а не бота). Файлы очищены, но доставить их нельзя: разбейте исходный архив на части и пришлите снова.",
    "⛔ <b>There is no cleaning for this file.</b> %s. The file was only inspected and not changed: do not pass it on as cleaned.":
        "⛔ <b>Чистки для этого файла нет.</b> %s. Файл только просмотрен и не изменён: не отправляйте его дальше как почищенный.",
    "⛔ NOT FULLY CLEANED: some of the original values remained in the file. Do not send it any further.":
        "⛔ ПОЧИЩЕНО НЕ ПОЛНОСТЬЮ: часть исходных значений осталась в файле. Не отправляйте его дальше.",
    "✅ Profile <b>%s</b>. %s The file name is neutral, the original one was not used.":
        "✅ Профиль <b>%s</b>. %s Имя файла нейтральное, исходное не использовано.",
    "✅ Profile <b>%s</b>: cleaned and verified files -- %d of %d. The archive name is neutral.":
        "✅ Профиль <b>%s</b>: очищено и проверено файлов -- %d из %d. Имя архива нейтральное.",
    "👁 <b>Cleaning detectability</b> (cleaned %d): not visible -- %d, noticeable -- %d, obvious -- %d.":
        "👁 <b>Заметность чистки</b> (очищено %d): не видны -- %d, заметны -- %d, очевидны -- %d.",
    "👁 <b>Cleaning detectability</b>: %s.":
        "👁 <b>Заметность чистки</b>: %s.",
    "👤 Users allowed: %d":
        "👤 Допущено пользователей: %d",
    "📌 <b>Kept by your choice (%s): original values in the file -- %d.</b> This is not a cleaning shortfall: you asked for these fields to stay. But because of that the file is <b>NOT clean</b>, and it should be passed on only deliberately. The report below may say \"NOT CLEAN\": its verdict is computed without taking your list into account.":
        "📌 <b>Сохранено по вашему выбору (%s): исходных значений в файле -- %d.</b> Это не недочистка: вы просили эти поля оставить. Но файл поэтому <b>НЕ чистый</b>, и отправлять его дальше можно только сознательно. Отчёт ниже может написать «НЕ ЧИСТО»: его вердикт считается без учёта вашего списка.",
    "📌 Profile <b>%s</b>: kept by your choice: %s. The file is NOT clean: these fields stayed as they were.":
        "📌 Профиль <b>%s</b>: по вашему выбору сохранено: %s. Файл НЕ чистый: эти поля остались как были.",
    "📌 You asked to keep: %s. Files left out of the archive because their cleanliness was not proven for this reason: %d -- this is a consequence of your choice. Clean them one at a time if needed.":
        "📌 Вы просили сохранить: %s. Файлов, не включённых в архив из-за чистоты, не доказанной по этой причине: %d -- это следствие вашего выбора. Очистите их по одному, если нужно.",
    "📦 <b>This is a ZIP archive: {n}</b>. The bot will clean every file inside (nested archives are not unpacked) and send a new archive <b>with only those whose cleanliness is proven</b> by re-inspection. Anything not cleaned does not go into the archive: it can't be handed over as clean by mistake. A report for each file is in the chat.\n\n• <b>Stealth</b> / <b>Paranoid</b> -- as for a single file.\n• <b>Regenerate</b> -- through LibreOffice, file by file: slow.\n• File names are metadata too: \"neutral\" ones -- doc_0001.docx and so on (the mapping of numbers to names will exist only in the chat).\n• \"Keep\": a file with a kept field is not considered clean and will not go into the archive -- clean such files one at a time.":
        "📦 <b>Это ZIP-архив: {n}</b>. Бот почистит каждый файл внутри (вложенные архивы не разворачиваются) и пришлёт новый архив <b>только с теми, чья чистота доказана</b> повторной инспекцией. Не очищенное в архив не попадает: его не отдать по ошибке как чистое. Отчёт по каждому файлу -- в чате.\n\n• <b>Stealth</b> / <b>Paranoid</b> -- как для одиночного файла.\n• <b>Пересоздать</b> -- через LibreOffice, файл за файлом: долго.\n• Имена файлов тоже метаданные: «нейтральные» -- doc_0001.docx и т.п. (соответствие номеров именам будет только в чате).\n• «Сохранить»: файл с сохранённым полем не считается чистым и в архив не попадёт -- такие файлы чистите по одному.",
    "📦 Cleaning the archive, profile <b>%s</b>: this may take minutes…":
        "📦 Чищу архив, профиль <b>%s</b>: это может занять минуты…",
    "🔎 <b>SIGNALS</b>":
        "🔎 <b>ВЫВОДЫ</b>",
    "🔎 Got it, inspecting the metadata…":
        "🔎 Принято, разбираю метаданные…",
    "🗑 Auto-deletion of reports: %s":
        "🗑 Автоудаление отчётов: %s",
    "🗑 Delete report from chat":
        "🗑 Удалить отчёт из чата",
    "🗑 Report messages deleted: %d.\n<i>The bot doesn't remember reports sent before its last restart -- delete those by hand. The bot doesn't touch cleaned files or your own messages.</i>":
        "🗑 Удалено сообщений с отчётами: %d.\n<i>Отчёты, отправленные до последнего перезапуска бота, он не помнит -- их удалите вручную. Почищенные файлы и ваши собственные сообщения бот не трогает.</i>",
    "🧹 <b>Clean the file?</b> The old Office format can't be cleaned in place: the metadata and the deleted text of earlier versions are embedded in the container itself. There is only one honest way -- <b>Regenerate</b>: LibreOffice will save the document anew in a modern format (docx/xlsx/pptx) without the edit history and without your name.\n\n<i>The application will become LibreOffice and the layout may change a little. The original file does not change: the bot will send a new copy.</i>":
        "🧹 <b>Почистить файл?</b> Старый формат Office на месте не чистится: метаданные и удалённый текст прошлых версий вшиты в сам контейнер. Честный путь один -- <b>Пересоздать</b>: LibreOffice сохранит документ заново в современном формате (docx/xlsx/pptx) без истории правок и без вашего имени.\n\n<i>Приложением станет LibreOffice, вёрстка может немного измениться. Исходный файл не меняется: бот пришлёт новую копию.</i>",
    "🧹 <b>Clean the file?</b> The original file will not change: the bot will send a new copy (as a document, not a photo -- otherwise Telegram would recompress it) and a cleaning report.\n\n• <b>Stealth</b> (recommended) -- removes identity and environment: author, paths, printer names, GPS, serial numbers. It keeps the application and its version: empty metadata gives away on its own that the file was cleaned.\n• <b>Paranoid</b> -- removes everything, including the application and dates. Cleaner, but the result looks processed.\n{regen}\n<i>\"Keep\" toggles: the field stays as it was, so the file will NOT be clean -- the report will call this your choice.</i>":
        "🧹 <b>Почистить файл?</b> Исходный файл не изменится: бот пришлёт новую копию (документом, не фото -- иначе Telegram пережмёт её) и отчёт о чистке.\n\n• <b>Stealth</b> (рекомендуется) -- убирает личность и окружение: автора, пути, имена принтеров, GPS, серийники. Приложение и его версию оставляет: пустые метаданные сами выдают, что файл чистили.\n• <b>Paranoid</b> -- убирает всё, включая приложение и даты. Чище, но результат выглядит обработанным.\n{regen}\n<i>Переключатели «Сохранить»: поле остаётся как было, поэтому файл НЕ будет чистым -- отчёт назовёт это вашим выбором.</i>",
    "🧹 <b>Metadata cleaning</b> · %s · profile %s":
        "🧹 <b>Чистка метаданных</b> · %s · профиль %s",
    "🧹 Cleaning the file, profile <b>%s</b>…":
        "🧹 Чищу файл, профиль <b>%s</b>…",
}
