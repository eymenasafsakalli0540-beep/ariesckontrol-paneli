from flask import Flask, render_template, request, jsonify, session

import ast
import operator
import math
import requests
import os
import re
from difflib import get_close_matches
from datetime import datetime

# 🌐 ÇEVİRİ MOTORU (dil modu ve /çevir komutu için)
# Sunucuda kurulu değilse çeviri özellikleri sessizce devre dışı kalır.
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "aries-ai-cok-gizli-anahtar-2026")

# --------------------------------------------------------------------------
# 🛠️ BAKIM MODU (EKLENTİ — mevcut koda dokunmadan eklendi)
# --------------------------------------------------------------------------
# MAINTENANCE_MODE True olduğunda ARIES tamamen durur: /ask endpoint'i
# hiçbir kural tabanlı motoru (matematik, coğrafya, tarih, fen, AI fallback vb.)
# çalıştırmadan direkt aşağıdaki sabit mesajı döner. Kontrol panelinden
# /api/get-logs üzerinden 'maintenance' ve 'resume' action'larıyla açılıp kapanır.
MAINTENANCE_MODE = False
MAINTENANCE_MESSAGE = "Şu an bakım arasındayız."

# 💾 KALICI BAKIM DURUMU (EKLENTİ) — Render gibi platformlarda sunucu belli
# süre istek almayınca uykuya dalıp sonra sıfırdan yeniden başlayabilir; bu
# durumda bellekteki MAINTENANCE_MODE değişkeni sıfırlanır. Bunu önlemek için
# durumu küçük bir dosyaya da yazıyoruz ve başlangıçta oradan okuyoruz.
MAINTENANCE_FILE = "maintenance.flag"


def _load_maintenance_state():
    return os.path.exists(MAINTENANCE_FILE)


def _save_maintenance_state(is_on):
    if is_on:
        with open(MAINTENANCE_FILE, "w", encoding="utf-8") as f:
            f.write("1")
    else:
        if os.path.exists(MAINTENANCE_FILE):
            os.remove(MAINTENANCE_FILE)


# --------------------------------------------------------------------------
# 🔒 GERÇEKTEN KALICI BAKIM DURUMU — RENDER ORTAM DEĞİŞKENİ (EKLENTİ)
# --------------------------------------------------------------------------
# Yerel dosya (maintenance.flag), Render'ın container'ı tamamen sıfırdan
# yeniden başlattığı durumlarda (deploy, çökme, platform bakımı vb.) silinip
# gidebilir. Bunu %100 garantiye almak için durumu Render'ın KENDİ ortam
# değişkeni sisteminde de tutuyoruz — çünkü ortam değişkenleri container ne
# kadar sıfırdan açılırsa açılsın HER ZAMAN aynı kalır, sadece biz (veya sen)
# değiştirene kadar.
#
# NASIL ÇALIŞIR: 'maintenance'/'resume' action'ı tetiklendiğinde, Render'ın
# API'sine bir istek atıp ARIES_MAINTENANCE ortam değişkenini "1" veya "0"
# yapıyoruz. Render bu değişikliği fark edince otomatik olarak servisi kısa
# bir süreliğine (1-2 dakika) yeniden başlatıyor — bu normal ve beklenen bir
# davranıştır, endişelenme.
#
# KURULUM (senin yapman gereken):
#   1) https://dashboard.render.com/u/settings#api-keys adresinden yeni bir
#      API anahtarı oluştur ("Create API Key").
#   2) Render'da bu servisin "Environment" sekmesine gidip RENDER_API_KEY
#      adında yeni bir ortam değişkeni ekle, değerine o anahtarı yapıştır.
#   3) Aşağıdaki anahtar boşsa (RENDER_API_KEY yoksa) hiçbir şey bozulmaz;
#      sistem sessizce sadece yerel dosya yöntemine (maintenance.flag) döner.
RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "rnd_ZTS8MST06j6znv8CRuhINZPbcFzJ")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "srv-d8pfgaj7uimc73a5i2eg")
ARIES_MAINTENANCE_ENV_KEY = "ARIES_MAINTENANCE"


def _set_render_env_maintenance(is_on):
    """Render API üzerinden ARIES_MAINTENANCE ortam değişkenini günceller.
    RENDER_API_KEY tanımlı değilse sessizce hiçbir şey yapmaz (hata vermez)."""
    if not RENDER_API_KEY:
        return False
    try:
        url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars/{ARIES_MAINTENANCE_ENV_KEY}"
        resp = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {RENDER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"value": "1" if is_on else "0"},
            timeout=10,
        )
        return resp.status_code < 300
    except Exception:
        return False


# Başlangıçta önce Render ortam değişkenine bak (en güvenilir kaynak),
# yoksa yerel dosyaya, o da yoksa varsayılan olarak kapalı (False) kabul et.
_env_maintenance = os.environ.get(ARIES_MAINTENANCE_ENV_KEY, "")
if _env_maintenance in ("1", "true", "True"):
    MAINTENANCE_MODE = True
elif _env_maintenance in ("0", "false", "False"):
    MAINTENANCE_MODE = False
else:
    MAINTENANCE_MODE = _load_maintenance_state()


# 🌐 GENEL CORS DESTEĞİ (EKLENTİ) — panel farklı bir adresten (origin) barındırılıyorsa
# tarayıcı istekleri CORS koruması yüzünden engelleyebilir. Bu kural, hangi
# endpoint'ten dönerse dönsün her cevaba otomatik olarak izin başlığı ekler,
# böylece /ask içindeki onlarca farklı cevap noktasının her birini tek tek
# değiştirmeye gerek kalmaz.
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

# --------------------------------------------------------------------------
# 🤖 GELİŞMİŞ YAPAY ZEKA DESTEĞİ (OPSİYONEL — "daha akıllı" cevaplar için)
# --------------------------------------------------------------------------
# ARIES aşağıdaki kural tabanlı sistemde (matematik, coğrafya, tarih, fen vb.)
# bir eşleşme BULAMAZSA, buraya bir API anahtarı girersen o soruyu gerçek bir
# yapay zeka modeline sorup daha akıllı/geniş kapsamlı bir cevap üretir.
# Anahtar boş bırakılırsa hiçbir şey değişmez, mevcut kural tabanlı sistem
# aynen çalışmaya devam eder (kod bozulmaz, sessizce devre dışı kalır).
#
# NASIL KULLANILIR:
#   1) Aşağıya kendi API anahtarınızı yazın (ya da ortam değişkeni olarak verin).
#   2) AI_API_PROVIDER'ı "openai", "anthropic" veya "gemini" olarak seçin.
#   3) Sunucuyu başlatın — artık ARIES bilmediği soruları da cevaplayabilir.
AI_API_KEY = os.environ.get("AQ.Ab8RN6L66M_i2NZ6-G_99hapOmSm8x_U5o00Y2Jv6fbTyW8aRw", "")        # <-- BURAYA KENDİ API ANAHTARINIZI GİRİN
AI_API_PROVIDER = os.environ.get("AI_API_PROVIDER", "gemini")  # "openai", "anthropic" veya "gemini"
AI_MODEL_OPENAI = "gpt-4o-mini"
AI_MODEL_ANTHROPIC = "claude-3-5-haiku-20241022"
AI_MODEL_GEMINI = "gemini-2.0-flash"


def ask_ai_fallback(user_text, buddy_mode=False):
    """Kural tabanlı sistem cevap bulamadığında çağrılır. AI_API_KEY boşsa None döner
    ve ARIES normal 'bulamadım' cevabını verir. Anahtar varsa gerçek bir modele sorar."""
    if not AI_API_KEY:
        return None

    system_prompt = (
        "Sen ARIES AI adında Türkçe konuşan bir yapay zeka asistanısın. "
        "Kısa, net ve doğru cevaplar ver. "
        + ("Samimi ve arkadaşça (kanka diliyle) konuş." if buddy_mode else "Kibar ve profesyonel bir dille konuş.")
    )

    try:
        if AI_API_PROVIDER == "gemini":
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL_GEMINI}:generateContent",
                headers={"Content-Type": "application/json"},
                params={"key": AI_API_KEY},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_text}]}],
                    "generationConfig": {"maxOutputTokens": 500},
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip() or None

        elif AI_API_PROVIDER == "anthropic":
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": AI_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": AI_MODEL_ANTHROPIC,
                    "max_tokens": 500,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_text}],
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip() or None

        else:  # openai
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL_OPENAI,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "max_tokens": 500,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip() or None

    except Exception:
        # API hatası/timeout olursa sessizce None dön, ARIES normal cevabına düşer.
        return None

# 📖 OFİS İÇİ (İNTERNETSİZ) TÜRKÇE-RUSÇA SÖZLÜK — dictionary_tr_ru.html'den alınmıştır
# anahtar: normalize edilmiş türkçe kelime -> (rusça, latin okunuş)
RU_DICTIONARY = {
    'merhaba': ('Привет', 'Privet'),
    'gunaydin': ('Доброе утро', 'Dobroye utro'),
    'iyi gunler': ('Добрый день', 'Dobriy den'),
    'iyi aksamlar': ('Добрый вечер', 'Dobriy vecher'),
    'iyi geceler': ('Спокойной ночи', 'Spokoynoy nochi'),
    'nasilsin': ('Как дела?', 'Kak dela?'),
    'iyiyim': ('Хорошо', 'Khorosho'),
    'fena degilim': ('Неплохо', 'Neplokho'),
    'tesekkur ederim': ('Спасибо', 'Spasibo'),
    'rica ederim': ('Пожалуйста', 'Pozhaluysta'),
    'evet': ('Да', 'Da'),
    'hayir': ('Нет', 'Net'),
    'lutfen': ('Пожалуйста', 'Pozhaluysta'),
    'ozur dilerim': ('Извините', 'Izvinite'),
    'gule gule': ('Пока', 'Poka'),
    'gorusuruz': ('До встречи', 'Do vstrechi'),
    'adin ne': ('Как тебя зовут?', 'Kak tebya zovut?'),
    'benim adim': ('Меня зовут...', 'Menya zovut...'),
    'memnun oldum': ('Приятно познакомиться', 'Priyatno poznakomitsya'),
    'kac yasindasin': ('Сколько тебе лет?', 'Skolko tebe let?'),
    '...yasindayim': ('Мне ... лет', 'Mne ... let'),
    'hos geldin': ('Добро пожаловать', 'Dobro pozhalovat'),
    'nerelisin': ('Откуда ты?', 'Otkuda ty?'),
    "ben turkiye'denim": ('Я из Турции', 'Ya iz Turtsii'),
    "ben belarus'tanim": ('Я из Беларуси', 'Ya iz Belarusi'),
    'sifir': ('ноль', 'nol'),
    'bir': ('один', 'odin'),
    'iki': ('два', 'dva'),
    'uc': ('три', 'tri'),
    'dort': ('четыре', 'chetyre'),
    'bes': ('пять', 'pyat'),
    'alti': ('шесть', 'shest'),
    'yedi': ('семь', 'sem'),
    'sekiz': ('восемь', 'vosem'),
    'dokuz': ('девять', 'devyat'),
    'on': ('десять', 'desyat'),
    'on bir': ('одиннадцать', 'odinnadtsat'),
    'on iki': ('двенадцать', 'dvenadtsat'),
    'on uc': ('тринадцать', 'trinadtsat'),
    'on dort': ('четырнадцать', 'chetyrnadtsat'),
    'on bes': ('пятнадцать', 'pyatnadtsat'),
    'on alti': ('шестнадцать', 'shestnadtsat'),
    'on yedi': ('семнадцать', 'semnadtsat'),
    'on sekiz': ('восемнадцать', 'vosemnadtsat'),
    'on dokuz': ('девятнадцать', 'devyatnadtsat'),
    'yirmi': ('двадцать', 'dvadtsat'),
    'otuz': ('тридцать', 'tridtsat'),
    'kirk': ('сорок', 'sorok'),
    'elli': ('пятьдесят', 'pyatdesyat'),
    'yuz': ('сто', 'sto'),
    'kirmizi': ('красный', 'krasniy'),
    'mavi': ('синий', 'siniy'),
    'yesil': ('зелёный', 'zelyoniy'),
    'sari': ('жёлтый', 'zholtiy'),
    'turuncu': ('оранжевый', 'oranzheviy'),
    'mor': ('фиолетовый', 'fioletoviy'),
    'pembe': ('розовый', 'rozoviy'),
    'siyah': ('чёрный', 'chorniy'),
    'beyaz': ('белый', 'beliy'),
    'kahverengi': ('коричневый', 'korichneviy'),
    'gri': ('серый', 'seriy'),
    'anne': ('мама', 'mama'),
    'baba': ('папа', 'papa'),
    'kiz kardes': ('сестра', 'sestra'),
    'erkek kardes': ('брат', 'brat'),
    'buyukanne': ('бабушка', 'babushka'),
    'buyukbaba': ('дедушка', 'dedushka'),
    'teyze / hala': ('тётя', 'tyotya'),
    'amca / dayi': ('дядя', 'dyadya'),
    'arkadas': ('друг', 'drug'),
    'aile': ('семья', 'semya'),
    'okul': ('школа', 'shkola'),
    'ogretmen': ('учитель', 'uchitel'),
    'ogrenci': ('ученик', 'uchenik'),
    'ders': ('урок', 'urok'),
    'odev': ('домашнее задание', 'domashneye zadaniye'),
    'kitap': ('книга', 'kniga'),
    'defter': ('тетрадь', 'tetrad'),
    'kalem (tukenmez)': ('ручка', 'ruchka'),
    'kursun kalem': ('карандаш', 'karandash'),
    'silgi': ('ластик', 'lastik'),
    'sinif': ('класс', 'klass'),
    'tahta': ('доска', 'doska'),
    'soru': ('вопрос', 'vopros'),
    'cevap': ('ответ', 'otvet'),
    'sinav': ('экзамен', 'ekzamen'),
    'teneffus': ('перемена', 'peremena'),
    'okumak': ('читать', 'chitat'),
    'yazmak': ('писать', 'pisat'),
    'dinlemek': ('слушать', 'slushat'),
    'konusmak': ('говорить', 'govorit'),
    'anlamak': ('понимать', 'ponimat'),
    'tekrar eder misin': ('Повтори, пожалуйста', 'Povtori, pozhaluysta'),
    'anlamadim': ('Я не понимаю', 'Ya ne ponimayu'),
    'anladim': ('Я понял / поняла', 'Ya ponyal / ponyala'),
    'yardim eder misin': ('Помоги мне, пожалуйста', 'Pomogi mne, pozhaluysta'),
    'canta': ('сумка', 'sumka'),
    'sira': ('парта', 'parta'),
    'sandalye': ('стул', 'stul'),
    'pencere': ('окно', 'okno'),
    'kapi': ('дверь', 'dver'),
    'pazartesi': ('понедельник', 'ponedelnik'),
    'sali': ('вторник', 'vtornik'),
    'carsamba': ('среда', 'sreda'),
    'persembe': ('четверг', 'chetverg'),
    'cuma': ('пятница', 'pyatnitsa'),
    'cumartesi': ('суббота', 'subbota'),
    'pazar': ('воскресенье', 'voskresenye'),
    'bugun': ('сегодня', 'segodnya'),
    'yarin': ('завтра', 'zavtra'),
    'dun': ('вчера', 'vchera'),
    'ocak': ('январь', 'yanvar'),
    'subat': ('февраль', 'fevral'),
    'mart': ('март', 'mart'),
    'nisan': ('апрель', 'aprel'),
    'mayis': ('май', 'may'),
    'haziran': ('июнь', 'iyun'),
    'temmuz': ('июль', 'iyul'),
    'agustos': ('август', 'avgust'),
    'eylul': ('сентябрь', 'sentyabr'),
    'ekim': ('октябрь', 'oktyabr'),
    'kasim': ('ноябрь', 'noyabr'),
    'aralik': ('декабрь', 'dekabr'),
    'ekmek': ('хлеб', 'khleb'),
    'su': ('вода', 'voda'),
    'sut': ('молоко', 'moloko'),
    'elma': ('яблоко', 'yabloko'),
    'muz': ('банан', 'banan'),
    'cikolata': ('шоколад', 'shokolad'),
    'seker': ('конфета', 'konfeta'),
    'yemek': ('еда', 'yeda'),
    'kahvalti': ('завтрак', 'zavtrak'),
    'ogle yemegi': ('обед', 'obed'),
    'aksam yemegi': ('ужин', 'uzhin'),
    'aciktim': ('Я хочу есть', 'Ya khochu yest'),
    'susadim': ('Я хочу пить', 'Ya khochu pit'),
    'kedi': ('кошка', 'koshka'),
    'kopek': ('собака', 'sobaka'),
    'kus': ('птица', 'ptitsa'),
    'balik': ('рыба', 'ryba'),
    'at': ('лошадь', 'loshad'),
    'tavsan': ('заяц', 'zayats'),
    'ayi': ('медведь', 'medved'),
    'aslan': ('лев', 'lev'),
    'bas': ('голова', 'golova'),
    'el': ('рука', 'ruka'),
    'ayak': ('нога', 'noga'),
    'goz': ('глаз', 'glaz'),
    'kulak': ('ухо', 'ukho'),
    'agiz': ('рот', 'rot'),
    'burun': ('нос', 'nos'),
    'sac': ('волосы', 'volosy'),
    'mutlu': ('счастливый', 'schastliviy'),
    'uzgun': ('грустный', 'grustniy'),
    'kizgin': ('злой', 'zloy'),
    'yorgun': ('усталый', 'ustaliy'),
    'heyecanli': ('взволнованный', 'vzvolnovanniy'),
    'korkmus': ('испуганный', 'ispuganniy'),
    'iyi': ('хорошо', 'khorosho'),
    'kotu': ('плохо', 'plokho'),
    'gitmek': ('идти', 'idti'),
    'gelmek': ('приходить', 'prikhodit'),
    'yemek (fiil)': ('есть', 'yest'),
    'icmek': ('пить', 'pit'),
    'oynamak': ('играть', 'igrat'),
    'kosmak': ('бегать', 'begat'),
    'uyumak': ('спать', 'spat'),
    'gulmek': ('смеяться', 'smeyatsya'),
    'aglamak': ('плакать', 'plakat'),
    'hoslanmak / sevmek': ('нравиться', 'nravitsya'),
    'yardim etmek': ('помогать', 'pomogat'),
    'benimle oynar misin': ('Поиграешь со мной?', 'Poigraesh so mnoy?'),
    'bu ne': ('Что это?', 'Chto eto?'),
    'bu kim': ('Кто это?', 'Kto eto?'),
    'nerede tuvalet': ('Где туалет?', 'Gde tualet?'),
    'yardima ihtiyacim var': ('Мне нужна помощь', 'Mne nuzhna pomoshch'),
    'ben hazirim': ('Я готов / готова', 'Ya gotov / gotova'),
    'sira sende': ('Твоя очередь', 'Tvoya ochered'),
    'harika': ('Отлично!', 'Otlichno!'),
    'tebrikler': ('Поздравляю!', 'Pozdravlyayu!'),
    'gorusmek uzere': ('Увидимся', 'Uvidimsya'),
    'saat kac': ('Который час?', 'Kotoriy chas?'),
    'simdi': ('сейчас', 'seychas'),
    'sonra': ('потом', 'potom'),
    'erken': ('рано', 'rano'),
    'gec': ('поздно', 'pozdno'),
    'gunesli': ('солнечно', 'solnechno'),
    'yagmurlu': ('дождливо', 'dozhdlivo'),
    'karli': ('снежно', 'snezhno'),
    'ruzgarli': ('ветрено', 'vetreno'),
    'sicak': ('жарко', 'zharko'),
    'soguk': ('холодно', 'kholodno'),
    'ne': ('Что?', 'Chto?'),
    'kim': ('Кто?', 'Kto?'),
    'nerede': ('Где?', 'Gde?'),
    'ne zaman': ('Когда?', 'Kogda?'),
    'neden': ('Почему?', 'Pochemu?'),
    'nasil': ('Как?', 'Kak?'),
    'kac tane': ('Сколько?', 'Skolko?'),
}

# Ters yönde arama için (Rusça -> Türkçe)
RU_TO_TR_DICTIONARY = {
    'привет': 'Merhaba',
    'доброе утро': 'Günaydın',
    'добрый день': 'İyi günler',
    'добрый вечер': 'İyi akşamlar',
    'спокойной ночи': 'İyi geceler',
    'как дела': 'Nasılsın?',
    'хорошо': 'İyiyim',
    'неплохо': 'Fena değilim',
    'спасибо': 'Teşekkür ederim',
    'пожалуйста': 'Rica ederim',
    'да': 'Evet',
    'нет': 'Hayır',
    'извините': 'Özür dilerim',
    'пока': 'Güle güle',
    'до встречи': 'Görüşürüz',
    'как тебя зовут': 'Adın ne?',
    'меня зовут': 'Benim adım...',
    'приятно познакомиться': 'Memnun oldum',
    'сколько тебе лет': 'Kaç yaşındasın?',
    'мне ... лет': '...yaşındayım',
    'добро пожаловать': 'Hoş geldin',
    'откуда ты': 'Nerelisin?',
    'я из турции': "Ben Türkiye'denim",
    'я из беларуси': "Ben Belarus'tanım",
    'ноль': 'sıfır',
    'один': 'bir',
    'два': 'iki',
    'три': 'üç',
    'четыре': 'dört',
    'пять': 'beş',
    'шесть': 'altı',
    'семь': 'yedi',
    'восемь': 'sekiz',
    'девять': 'dokuz',
    'десять': 'on',
    'одиннадцать': 'on bir',
    'двенадцать': 'on iki',
    'тринадцать': 'on üç',
    'четырнадцать': 'on dört',
    'пятнадцать': 'on beş',
    'шестнадцать': 'on altı',
    'семнадцать': 'on yedi',
    'восемнадцать': 'on sekiz',
    'девятнадцать': 'on dokuz',
    'двадцать': 'yirmi',
    'тридцать': 'otuz',
    'сорок': 'kırk',
    'пятьдесят': 'elli',
    'сто': 'yüz',
    'красный': 'kırmızı',
    'синий': 'mavi',
    'зелёный': 'yeşil',
    'жёлтый': 'sarı',
    'оранжевый': 'turuncu',
    'фиолетовый': 'mor',
    'розовый': 'pembe',
    'чёрный': 'siyah',
    'белый': 'beyaz',
    'коричневый': 'kahverengi',
    'серый': 'gri',
    'мама': 'anne',
    'папа': 'baba',
    'сестра': 'kız kardeş',
    'брат': 'erkek kardeş',
    'бабушка': 'büyükanne',
    'дедушка': 'büyükbaba',
    'тётя': 'teyze / hala',
    'дядя': 'amca / dayı',
    'друг': 'arkadaş',
    'семья': 'aile',
    'школа': 'okul',
    'учитель': 'öğretmen',
    'ученик': 'öğrenci',
    'урок': 'ders',
    'домашнее задание': 'ödev',
    'книга': 'kitap',
    'тетрадь': 'defter',
    'ручка': 'kalem (tükenmez)',
    'карандаш': 'kurşun kalem',
    'ластик': 'silgi',
    'класс': 'sınıf',
    'доска': 'tahta',
    'вопрос': 'soru',
    'ответ': 'cevap',
    'экзамен': 'sınav',
    'перемена': 'teneffüs',
    'читать': 'okumak',
    'писать': 'yazmak',
    'слушать': 'dinlemek',
    'говорить': 'konuşmak',
    'понимать': 'anlamak',
    'повтори, пожалуйста': 'Tekrar eder misin?',
    'я не понимаю': 'Anlamadım',
    'я понял / поняла': 'Anladım',
    'помоги мне, пожалуйста': 'Yardım eder misin?',
    'сумка': 'çanta',
    'парта': 'sıra',
    'стул': 'sandalye',
    'окно': 'pencere',
    'дверь': 'kapı',
    'понедельник': 'Pazartesi',
    'вторник': 'Salı',
    'среда': 'Çarşamba',
    'четверг': 'Perşembe',
    'пятница': 'Cuma',
    'суббота': 'Cumartesi',
    'воскресенье': 'Pazar',
    'сегодня': 'bugün',
    'завтра': 'yarın',
    'вчера': 'dün',
    'январь': 'Ocak',
    'февраль': 'Şubat',
    'март': 'Mart',
    'апрель': 'Nisan',
    'май': 'Mayıs',
    'июнь': 'Haziran',
    'июль': 'Temmuz',
    'август': 'Ağustos',
    'сентябрь': 'Eylül',
    'октябрь': 'Ekim',
    'ноябрь': 'Kasım',
    'декабрь': 'Aralık',
    'хлеб': 'ekmek',
    'вода': 'su',
    'молоко': 'süt',
    'яблоко': 'elma',
    'банан': 'muz',
    'шоколад': 'çikolata',
    'конфета': 'şeker',
    'еда': 'yemek',
    'завтрак': 'kahvaltı',
    'обед': 'öğle yemeği',
    'ужин': 'akşam yemeği',
    'я хочу есть': 'Acıktım',
    'я хочу пить': 'Susadım',
    'кошка': 'kedi',
    'собака': 'köpek',
    'птица': 'kuş',
    'рыба': 'balık',
    'лошадь': 'at',
    'заяц': 'tavşan',
    'медведь': 'ayı',
    'лев': 'aslan',
    'голова': 'baş',
    'рука': 'el',
    'нога': 'ayak',
    'глаз': 'göz',
    'ухо': 'kulak',
    'рот': 'ağız',
    'нос': 'burun',
    'волосы': 'saç',
    'счастливый': 'mutlu',
    'грустный': 'üzgün',
    'злой': 'kızgın',
    'усталый': 'yorgun',
    'взволнованный': 'heyecanlı',
    'испуганный': 'korkmuş',
    'плохо': 'kötü',
    'идти': 'gitmek',
    'приходить': 'gelmek',
    'есть': 'yemek (fiil)',
    'пить': 'içmek',
    'играть': 'oynamak',
    'бегать': 'koşmak',
    'спать': 'uyumak',
    'смеяться': 'gülmek',
    'плакать': 'ağlamak',
    'нравиться': 'hoşlanmak / sevmek',
    'помогать': 'yardım etmek',
    'поиграешь со мной': 'Benimle oynar mısın?',
    'что это': 'Bu ne?',
    'кто это': 'Bu kim?',
    'где туалет': 'Nerede tuvalet?',
    'мне нужна помощь': 'Yardıma ihtiyacım var',
    'я готов / готова': 'Ben hazırım',
    'твоя очередь': 'Sıra sende',
    'отлично': 'Harika!',
    'поздравляю': 'Tebrikler!',
    'увидимся': 'Görüşmek üzere',
    'который час': 'Saat kaç?',
    'сейчас': 'şimdi',
    'потом': 'sonra',
    'рано': 'erken',
    'поздно': 'geç',
    'солнечно': 'güneşli',
    'дождливо': 'yağmurlu',
    'снежно': 'karlı',
    'ветрено': 'rüzgarlı',
    'жарко': 'sıcak',
    'холодно': 'soğuk',
    'что': 'Ne?',
    'кто': 'Kim?',
    'где': 'Nerede?',
    'когда': 'Ne zaman?',
    'почему': 'Neden?',
    'как': 'Nasıl?',
    'сколько': 'Kaç tane?',
}

# 🌍 COĞRAFYA VERİ TABANI

world_countries = {
    "turkiye": {"b": "Ankara", "k": "Asya/Avrupa", "lat": 39.93, "lon": 32.85, "bilgi": "Asya ve Avrupa'yı birbirine bağlayan stratejik bir köprü ülkedir."},
    "hindistan": {"b": "Yeni Delhi", "k": "Asya", "lat": 28.61, "lon": 77.20, "bilgi": "Güney Asya'da yer alan, dünyanın en kalabalık nüfusuna sahip ülkesidir."},
    "kuba": {"b": "Havana", "k": "Karayipler", "lat": 23.11, "lon": -82.36, "bilgi": "Karayip Denizi'nde yer alan bir ada devletidir."},
    "abd": {"b": "Washington D.C.", "k": "Kuzey Amerika", "lat": 38.90, "lon": -77.03, "bilgi": "50 eyaletten oluşan küresel bir güçtür."},
    "rusya": {"b": "Moskova", "k": "Asya/Avrupa", "lat": 55.75, "lon": 37.61, "bilgi": "Yüzölçümü bakımından dünyanın en büyük ülkesidir."},
    "almanya": {"b": "Berlin", "k": "Avrupa", "lat": 52.52, "lon": 13.40, "bilgi": "Orta Avrupa'da yer alan sanayi devidir."},
    "fransa": {"b": "Paris", "k": "Avrupa", "lat": 48.85, "lon": 2.35, "bilgi": "Batı Avrupa'da bulunan; sanat ve moda merkezidir."},
    "ingiltere": {"b": "Londra", "k": "Avrupa", "lat": 51.50, "lon": -0.12, "bilgi": "Büyük Britanya adasında yer alan köklü bir ülkedir."},
    "azerbaycan": {"b": "Bakü", "k": "Asya", "lat": 40.40, "lon": 49.86, "bilgi": "Kafkasya'da yer alan kardeş canı ülkedir."},

    # Avrupa
    "italya": {"b": "Roma", "k": "Avrupa", "lat": 41.90, "lon": 12.49, "bilgi": "Akdeniz'de çizme şeklindeki yarımadada yer alan, tarih ve sanatıyla ünlü bir ülkedir."},
    "ispanya": {"b": "Madrid", "k": "Avrupa", "lat": 40.42, "lon": -3.70, "bilgi": "İber Yarımadası'nda yer alan, flamenko ve boğa güreşiyle bilinen bir ülkedir."},
    "portekiz": {"b": "Lizbon", "k": "Avrupa", "lat": 38.72, "lon": -9.14, "bilgi": "İber Yarımadası'nın batısında, Atlas Okyanusu kıyısında yer alan bir ülkedir."},
    "yunanistan": {"b": "Atina", "k": "Avrupa", "lat": 37.98, "lon": 23.73, "bilgi": "Antik uygarlığın beşiklerinden biri olan, Ege'de yer alan bir ülkedir."},
    "hollanda": {"b": "Amsterdam", "k": "Avrupa", "lat": 52.37, "lon": 4.90, "bilgi": "Deniz seviyesinin altındaki topraklarıyla ve lale tarlalarıyla bilinen bir ülkedir."},
    "belcika": {"b": "Brüksel", "k": "Avrupa", "lat": 50.85, "lon": 4.35, "bilgi": "Avrupa Birliği'nin merkezi kabul edilen, Batı Avrupa'da yer alan bir ülkedir."},
    "isvicre": {"b": "Bern", "k": "Avrupa", "lat": 46.95, "lon": 7.45, "bilgi": "Alp Dağları'nda yer alan, tarafsızlığı ve bankacılığıyla bilinen bir ülkedir."},
    "avusturya": {"b": "Viyana", "k": "Avrupa", "lat": 48.21, "lon": 16.37, "bilgi": "Orta Avrupa'da yer alan, klasik müzik geleneğiyle bilinen bir ülkedir."},
    "polonya": {"b": "Varşova", "k": "Avrupa", "lat": 52.23, "lon": 21.01, "bilgi": "Orta Avrupa'da yer alan, Baltık Denizi'ne kıyısı olan bir ülkedir."},
    "ukrayna": {"b": "Kiev", "k": "Avrupa", "lat": 50.45, "lon": 30.52, "bilgi": "Doğu Avrupa'da yer alan, yüzölçümü bakımından Avrupa'nın en büyük ikinci ülkesidir."},
    "isvec": {"b": "Stockholm", "k": "Avrupa", "lat": 59.33, "lon": 18.07, "bilgi": "İskandinav Yarımadası'nda yer alan bir Kuzey Avrupa ülkesidir."},
    "norvec": {"b": "Oslo", "k": "Avrupa", "lat": 59.91, "lon": 10.75, "bilgi": "Fiyortlarıyla ünlü, İskandinav Yarımadası'nda yer alan bir ülkedir."},
    "finlandiya": {"b": "Helsinki", "k": "Avrupa", "lat": 60.17, "lon": 24.94, "bilgi": "Binlerce gölüyle bilinen, Kuzey Avrupa'da yer alan bir ülkedir."},
    "danimarka": {"b": "Kopenhag", "k": "Avrupa", "lat": 55.68, "lon": 12.57, "bilgi": "İskandinavya'nın güneyinde yer alan bir Kuzey Avrupa ülkesidir."},
    "irlanda": {"b": "Dublin", "k": "Avrupa", "lat": 53.35, "lon": -6.26, "bilgi": "Yeşil manzaralarıyla bilinen, Büyük Britanya'nın batısındaki bir ada ülkesidir."},
    "cekya": {"b": "Prag", "k": "Avrupa", "lat": 50.08, "lon": 14.44, "bilgi": "Orta Avrupa'da yer alan, tarihi mimarisiyle bilinen bir ülkedir."},
    "macaristan": {"b": "Budapeşte", "k": "Avrupa", "lat": 47.50, "lon": 19.04, "bilgi": "Orta Avrupa'da, Tuna Nehri kıyısında yer alan bir ülkedir."},
    "romanya": {"b": "Bükreş", "k": "Avrupa", "lat": 44.43, "lon": 26.10, "bilgi": "Balkanlar'ın kuzeyinde, Karadeniz'e kıyısı olan bir ülkedir."},
    "bulgaristan": {"b": "Sofya", "k": "Avrupa", "lat": 42.70, "lon": 23.32, "bilgi": "Balkanlar'da yer alan, Türkiye'nin komşusu olan bir ülkedir."},
    "sirbistan": {"b": "Belgrad", "k": "Avrupa", "lat": 44.79, "lon": 20.45, "bilgi": "Balkanlar'ın merkezinde yer alan, denize kıyısı olmayan bir ülkedir."},
    "kibris": {"b": "Lefkoşa", "k": "Asya/Avrupa", "lat": 35.19, "lon": 33.38, "bilgi": "Akdeniz'in doğusunda yer alan bir ada ülkesidir."},

    # Asya
    "japonya": {"b": "Tokyo", "k": "Asya", "lat": 35.68, "lon": 139.69, "bilgi": "Pasifik Okyanusu'nda yer alan, teknolojisiyle bilinen bir ada ülkesidir."},
    "cin": {"b": "Pekin", "k": "Asya", "lat": 39.90, "lon": 116.41, "bilgi": "Nüfus bakımından dünyanın en kalabalık ülkelerinden biridir."},
    "guney kore": {"b": "Seul", "k": "Asya", "lat": 37.57, "lon": 126.98, "bilgi": "Kore Yarımadası'nın güneyinde yer alan, teknoloji ve pop kültürüyle bilinen bir ülkedir."},
    "kuzey kore": {"b": "Pyongyang", "k": "Asya", "lat": 39.03, "lon": 125.75, "bilgi": "Kore Yarımadası'nın kuzeyinde yer alan bir ülkedir."},
    "endonezya": {"b": "Cakarta", "k": "Asya", "lat": -6.21, "lon": 106.85, "bilgi": "Binlerce adadan oluşan, Güneydoğu Asya'da yer alan bir ülkedir."},
    "pakistan": {"b": "İslamabad", "k": "Asya", "lat": 33.68, "lon": 73.05, "bilgi": "Güney Asya'da, Hindistan'ın komşusu olan bir ülkedir."},
    "banglades": {"b": "Dakka", "k": "Asya", "lat": 23.81, "lon": 90.41, "bilgi": "Güney Asya'da, nüfus yoğunluğu en yüksek ülkelerden biridir."},
    "iran": {"b": "Tahran", "k": "Asya", "lat": 35.69, "lon": 51.39, "bilgi": "Orta Doğu'da yer alan, köklü bir uygarlık tarihine sahip ülkedir."},
    "irak": {"b": "Bağdat", "k": "Asya", "lat": 33.31, "lon": 44.36, "bilgi": "Orta Doğu'da, Dicle ve Fırat nehirleri arasında yer alan bir ülkedir."},
    "suudi arabistan": {"b": "Riyad", "k": "Asya", "lat": 24.71, "lon": 46.68, "bilgi": "Arap Yarımadası'nın büyük bölümünü kaplayan, petrol rezervleriyle bilinen bir ülkedir."},
    "arap emirlikleri": {"b": "Abu Dabi", "k": "Asya", "lat": 24.47, "lon": 54.37, "bilgi": "Arap Yarımadası'nda yedi emirlikten oluşan bir ülkedir."},
    "katar": {"b": "Doha", "k": "Asya", "lat": 25.29, "lon": 51.53, "bilgi": "Arap Yarımadası'nda, Basra Körfezi'ne kıyısı olan küçük ama zengin bir ülkedir."},
    "misir": {"b": "Kahire", "k": "Afrika", "lat": 30.04, "lon": 31.24, "bilgi": "Nil Nehri kıyısında yer alan, antik piramitleriyle ünlü bir ülkedir."},
    "gurcistan": {"b": "Tiflis", "k": "Asya", "lat": 41.72, "lon": 44.79, "bilgi": "Kafkasya'da, Karadeniz'e kıyısı olan bir ülkedir."},
    "ermenistan": {"b": "Erivan", "k": "Asya", "lat": 40.18, "lon": 44.51, "bilgi": "Güney Kafkasya'da yer alan, denize kıyısı olmayan bir ülkedir."},
    "kazakistan": {"b": "Astana", "k": "Asya", "lat": 51.18, "lon": 71.45, "bilgi": "Orta Asya'da yer alan, yüzölçümü bakımından dünyanın en büyük dokuzuncu ülkesidir."},
    "ozbekistan": {"b": "Taşkent", "k": "Asya", "lat": 41.30, "lon": 69.24, "bilgi": "Orta Asya'da yer alan bir ülkedir."},
    "suriye": {"b": "Şam", "k": "Asya", "lat": 33.51, "lon": 36.28, "bilgi": "Orta Doğu'da, Türkiye'nin güney komşusu olan bir ülkedir."},
    "urdun": {"b": "Amman", "k": "Asya", "lat": 31.95, "lon": 35.93, "bilgi": "Orta Doğu'da yer alan, Petra antik kentiyle bilinen bir ülkedir."},
    "lubnan": {"b": "Beyrut", "k": "Asya", "lat": 33.89, "lon": 35.50, "bilgi": "Doğu Akdeniz kıyısında yer alan küçük bir Orta Doğu ülkesidir."},
    "israil": {"b": "Kudüs / Tel Aviv", "k": "Asya", "lat": 31.77, "lon": 35.21, "bilgi": "Orta Doğu'da yer alan bir ülkedir; başkent statüsü uluslararası düzeyde tartışmalıdır, birçok ülke büyükelçiliğini Tel Aviv'de bulundurur."},

    # Afrika
    "fas": {"b": "Rabat", "k": "Afrika", "lat": 34.02, "lon": -6.84, "bilgi": "Kuzey Afrika'da, Cebelitarık Boğazı'na yakın konumda yer alan bir ülkedir."},
    "cezayir": {"b": "Cezayir", "k": "Afrika", "lat": 36.75, "lon": 3.06, "bilgi": "Kuzey Afrika'da, Akdeniz kıyısında yer alan, yüzölçümü açısından Afrika'nın en büyük ülkesidir."},
    "tunus": {"b": "Tunus", "k": "Afrika", "lat": 36.81, "lon": 10.18, "bilgi": "Kuzey Afrika'da, Akdeniz kıyısında yer alan küçük bir ülkedir."},
    "nijerya": {"b": "Abuja", "k": "Afrika", "lat": 9.08, "lon": 7.40, "bilgi": "Batı Afrika'da yer alan, nüfusu en kalabalık Afrika ülkesidir."},
    "guney afrika": {"b": "Pretoria", "k": "Afrika", "lat": -25.75, "lon": 28.19, "bilgi": "Afrika kıtasının en güneyinde yer alan, üç başkenti olan bir ülkedir."},
    "kenya": {"b": "Nairobi", "k": "Afrika", "lat": -1.29, "lon": 36.82, "bilgi": "Doğu Afrika'da yer alan, safari turizmiyle bilinen bir ülkedir."},
    "etiyopya": {"b": "Addis Ababa", "k": "Afrika", "lat": 9.03, "lon": 38.74, "bilgi": "Doğu Afrika'da yer alan, hiç sömürge olmamış nadir Afrika ülkelerinden biridir."},

    # Amerika
    "brezilya": {"b": "Brasilia", "k": "Güney Amerika", "lat": -15.79, "lon": -47.88, "bilgi": "Güney Amerika'da yer alan, Amazon Ormanları'nın büyük kısmını barındıran ülkedir."},
    "arjantin": {"b": "Buenos Aires", "k": "Güney Amerika", "lat": -34.60, "lon": -58.38, "bilgi": "Güney Amerika'da yer alan, tango ve futbolla özdeşleşmiş bir ülkedir."},
    "sili": {"b": "Santiago", "k": "Güney Amerika", "lat": -33.45, "lon": -70.65, "bilgi": "And Dağları boyunca uzanan, ince ve uzun şekliyle bilinen bir Güney Amerika ülkesidir."},
    "meksika": {"b": "Meksiko", "k": "Kuzey Amerika", "lat": 19.43, "lon": -99.13, "bilgi": "Kuzey Amerika'nın güneyinde yer alan, Aztek ve Maya mirasına sahip bir ülkedir."},
    "kanada": {"b": "Ottava", "k": "Kuzey Amerika", "lat": 45.42, "lon": -75.70, "bilgi": "Yüzölçümü bakımından dünyanın en büyük ikinci ülkesidir."},

    # Okyanusya
    "avustralya": {"b": "Kanberra", "k": "Okyanusya", "lat": -35.28, "lon": 149.13, "bilgi": "Hem kıta hem ülke olan, kendine özgü hayvan türleriyle bilinen bir ülkedir."},
    "yeni zelanda": {"b": "Wellington", "k": "Okyanusya", "lat": -41.29, "lon": 174.78, "bilgi": "Pasifik Okyanusu'nda yer alan, doğal manzaralarıyla ünlü bir ada ülkesidir."}
}

# 📜 TARİH VERİ TABANI

historical_events = {
    "istanbulun fethi": "<b>1453 - İstanbul'un Fethi:</b> Fatih Sultan Mehmed liderliğindeki Osmanlı ordusu Bizans'ı yıktı. Orta Çağ kapandı, Yeni Çağ başladı.",
    "cumhuriyetin ilani": "<b>29 Ekim 1923 - Cumhuriyetin İlanı:</b> Gazi Mustafa Kemal Atatürk önderliğinde Türkiye Cumhuriyeti resmen kuruldu. 🇹🇷",
    "malazgirt": "<b>1071 - Malazgirt Meydan Muharebesi:</b> Sultan Alparslan komutasındaki Büyük Selçuklu ordusu, Anadolu'nun kapılarını Türklere açtı.",
    "buyuk taarruz": "<b>1922 - Büyük Taarruz:</b> Türk Kurtuluş Savaşı'nın son evresi. Anadolu düşman işgalinden tamamen temizlendi."
}

# 🕋 DİNİ TERİMLER VERİ TABANI

religious_database = {
    "hicret": "<b>Hicret (622):</b> Hz. Muhammed (s.a.v.) ve Müslümanların Mekke'den Medine'ye göç etmesidir. Hicri takvimin başlangıcıdır.",
    "bedir savasi": "<b>Bedir Savaşı (624):</b> Müslümanlar ile Mekkeli müşrikler arasındaki ilk büyük savaştır. Müslümanlar zafer kazanmıştır.",
    "mekkenin fethi": "<b>Mekke'nin Fethi (630):</b> Hz. Muhammed liderliğindeki İslam ordusu kan dökmeden Mekke'ye girdi.",
    "siyer": "<b>Siyer:</b> Peygamber Efendimiz Hz. Muhammed'in (s.a.v.) hayatını inceleyen bilim dalıdır."
}

# 🧬 ANATOMİ VE FEN VERİ TABANI

science_database = {
    "kalp": "<b>Anatomi - Kalp:</b> Göğüs boşluğunda yer alan, kaslı bir pompadır. Vücuda kan pompalar. Üstte iki kulakçık, altta iki karıncık olmak üzere 4 odacıktan oluşur.",
    "akciyer": "<b>Anatomi - Akciğerler:</b> Solunum sisteminin ana organıdır. Göğüs kafesinde sağ ve sol olmak üzere iki adettir. Kana oksijen sağlar, karbondioksiti dışarı atar.",
    "karaciyer": "<b>Anatomi - Karaciğer:</b> Vücudun en büyük iç organıdır ve adeta bir kimya fabrikası gibi çalışır. Safra üretir, toksinleri temizler ve glikoz depolar.",
    "hucre": "<b>Fen Bilgisi - Hücre:</b> Canlıların canlılık özelliği gösteren en küçük yapı taşıdır. Hücre zarı, sitoplazma ve çekirdek olmak üzere üç temel kısımdan oluşur.",
    "fotosentez": "<b>Fen Bilgisi - Fotosentez:</b> Bitkilerin kloroplast organelinde, güneş ışığı yardımıyla su ve karbondioksiti birleştirerek besin (glikoz) ve oksijen üretmesi olayıdır.",
    "mitokondri": "<b>Fen Bilgisi - Mitokondri:</b> Hücrenin enerji santralidir. Oksijenli solunum yaparak hücre için gerekli olan ATP (enerji) molekülünü üretir."
}

# ⚡ FİZİK VE GEOMETRİ VERİ TABANI

physics_geometry_database = {
    "yercekimi": "<b>Fizik - Yerçekimi Kuvveti:</b> Kütlesi olan cisimlerin birbirini çekmesidir. Dünyadaki yerçekimi ivmesi yaklaşık olarak $g = 9.81 m/s^2$ kabul edilir. Keşfeden bilim insanı Isaac Newton'dır.",
    "surtunme": "<b>Fizik - Sürtünme Kuvveti:</b> Harekete karşı koyan zorlayıcı kuvvettir. Temas eden yüzeyler arasında oluşur ve kinetik enerjiyi ısı enerjisine dönüştürür.",
    "ohm kanunu": "<b>Fizik - Ohm Kanunu:</b> Bir elektrik devresinde gerilim (V), akım (I) ve direnç (R) arasındaki ilişkiyi açıklar. Formülü: $V = I \\cdot R$ şeklindedir.",
    "ucgen": "<b>Geometri - Üçgen:</b> Üç doğrunun kesişmesiyle oluşan kapalı şekildir. İç açılarının toplamı her zaman **180°**, dış açılarının toplamı ise **360°**'dir.",
    "kare": "<b>Geometri - Kare:</b> Tüm kenarları birbirine eşit ve tüm iç açıları **90°** olan düzgün bir dörtgendir. Alanı bir kenarının karesidir ($A = a^2$).",
    "dikdortgen": "<b>Geometri - Dikdörtgen:</b> Karşılıklı kenarları eşit ve paralel, tüm iç açıları **90°** olan dörtgendir. Çevresi: $2(a+b)$, Alanı: $a \\cdot b$ formülüyle bulunur."
}

# 👋 SELAMLAŞMA KELİMELERİ (fuzzy eşleşme için)
GREETING_WORDS = ["selam", "merhaba", "naber", "selamlar", "merhabalar", "hey", "hi"]

# 🙏 TEŞEKKÜR / NEZAKET KELİMELERİ (fuzzy eşleşme için)
THANKS_WORDS = ["tesekkurler", "tesekkur", "sagol", "sagolasin", "eyvallah", "sagolun", "minnettarim", "ellerinesaglik"]

# 😊 "RİCA EDERİM" TÜRÜ KARŞILIK KALIPLARI (kullanıcı bota teşekkür ettiğinde bot cevap veriyor;
# ama kullanıcı "rica ederim" derse bota bir onay/nezaket cevabı gerekiyor)
YOURE_WELCOME_WORDS = ["ricaederim", "ricaederiz", "birseydegil", "nedemek", "onemlidegil"]

# 🏗️ "KİM YAPTI" SORU KALIPLARI (boşluksuz/bitişik hâliyle de kontrol edilecek)
CREATOR_PHRASES = ["kim yapti", "yapimcin", "kim gelistirdi", "kurucun", "sahibin", "sen kimsin", "adini kim verdi"]

# 🗣️ "DO YOU SPEAK ENGLISH/RUSSIAN" TÜRÜ DİL SORULARI
LANGUAGE_PHRASES = {
    "english": ["do you speak english", "can you speak english", "speak english", "ingilizce biliyor musun", "ingilizce konusuyor musun"],
    "russian": ["do you speak russian", "can you speak russian", "speak russian", "rusca biliyor musun", "rusca konusuyor musun",
                "ты говоришь порусски", "говоришь порусски", "вы говорите порусски"],
}

# 🇹🇷 TEKRAR TÜRKÇEYE DÖNME KALIPLARI
LANGUAGE_RESET_PHRASES = ["turkce konus", "turkceye don", "turkce devam et", "speak turkish", "turkish konus"]

# 🔁 "X'i İngilizceye/Rusçaya çevir" / "translate X to english/russian" KALIPLARI
TRANSLATE_TO_EN_TR = re.compile(r'^(.+?)\s*(?:kelimesini|ifadesini|cümlesini|cumlesini)?\s*ingilizceye\s*çevir\.?$', re.IGNORECASE)
TRANSLATE_TO_RU_TR = re.compile(r'^(.+?)\s*(?:kelimesini|ifadesini|cümlesini|cumlesini)?\s*rusçaya\s*çevir\.?$', re.IGNORECASE)
TRANSLATE_TO_EN_ENG = re.compile(r'^translate\s+(.+?)\s+to\s+english\.?$', re.IGNORECASE)
TRANSLATE_TO_RU_ENG = re.compile(r'^translate\s+(.+?)\s+to\s+russian\.?$', re.IGNORECASE)


def normalize_tr(s):
    """Sözlük anahtarlarıyla birebir aynı normalizasyonu uygular
    (büyük 'İ' harfi sorunu dahil doğru şekilde ele alınır)."""
    s = s.replace("İ", "i").replace("I", "ı")
    s = s.lower().strip()
    s = s.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    s = s.rstrip("?!.,")
    return s


def parse_translation_command(text):
    """'X'i ingilizceye çevir' / 'translate X to russian' gibi doğrudan çeviri
    komutlarını yakalar. Eşleşme varsa (çevrilecek_metin, hedef_dil_kodu) döner."""
    text = text.strip()
    for pattern, target in [(TRANSLATE_TO_EN_TR, 'en'), (TRANSLATE_TO_RU_TR, 'ru'),
                             (TRANSLATE_TO_EN_ENG, 'en'), (TRANSLATE_TO_RU_ENG, 'ru')]:
        m = pattern.match(text)
        if m:
            return m.group(1).strip(), target
    return None, None


def translate_html_preserving_tags(html_text, target_lang):
    """HTML etiketlerini (<b>, <span> vb.) bozmadan sadece metin kısımlarını çevirir."""
    if not TRANSLATOR_AVAILABLE:
        return html_text
    try:
        translator = GoogleTranslator(source='tr', target=target_lang)
        segments = re.split(r'(<[^>]+>)', html_text)
        translated_segments = []
        for seg in segments:
            if seg == '' or seg.startswith('<'):
                translated_segments.append(seg)
            else:
                translated_segments.append(translator.translate(seg))
        return ''.join(translated_segments)
    except Exception:
        return html_text


# 🇷🇺➡️🇹🇷 LOG EKRANI İÇİN OTOMATİK ÇEVİRİ (EKLENTİ)
# Panelde loglara bakarken Rusça (Kiril alfabeli) bir soru görürsen anlaman
# için, log satırının sonuna otomatik olarak Türkçe çevirisini ekliyoruz.
CYRILLIC_PATTERN = re.compile(r'[\u0400-\u04FF]')


def add_turkish_translation_to_log_line(log_line):
    """Log satırında Kiril alfabesi (Rusça) tespit edilirse, satırın sonuna
    '(TR: ...)' şeklinde Türkçe çevirisini ekler. Türkçe/başka dil ise veya
    çeviri motoru yoksa/başarısız olursa satırı olduğu gibi bırakır."""
    if not CYRILLIC_PATTERN.search(log_line):
        return log_line
    if not TRANSLATOR_AVAILABLE:
        return log_line
    try:
        # Log satırı "... -> Soru: <mesaj>" formatında; sadece soru kısmını çeviriyoruz
        if "-> Soru: " in log_line:
            prefix, question_part = log_line.split("-> Soru: ", 1)
            translated = GoogleTranslator(source='ru', target='tr').translate(question_part)
            return f"{prefix}-> Soru: {question_part} (TR: {translated})"
        return log_line
    except Exception:
        return log_line


def build_reply(text):
    """Kullanıcı 'do you speak english/russian' dediyse, o dil modu session'da
    kayıtlıdır; bu fonksiyon her cevabı otomatik olarak o dile çevirip döner."""
    target = session.get('lang')
    if target in ('en', 'ru'):
        text = translate_html_preserving_tags(text, target)
    return jsonify({"reply": text})


def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c)


def fetch_country_from_api(country_name):
    try:
        url = f"https://restcountries.com/v3.1/name/{country_name}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()[0]
            name_tr = data.get("translations", {}).get("tur", {}).get("common", country_name).upper()
            capital = data.get("capital", ["Bilinmiyor"])[0]
            region = data.get("continents", ["Bilinmiyor"])[0]
            population = data.get("population", 0)
            flag = data.get("flag", "🌐")
            latlng = data.get("latlng", [0, 0])
            return {
                "name": name_tr, "b": capital, "k": region, "lat": latlng[0], "lon": latlng[1],
                "bilgi": f"{flag} {name_tr}, {region} kıtasında yer alan bir ülkedir."
            }
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# 🔒 GÜVENLİ MATEMATİK MOTORU (eval() yerine)
# eval() kullanıcı girdisini doğrudan çalıştırdığı için güvenlik riski
# taşır. Bunun yerine sadece +,-,*,/ ve parantezlere izin veren bir
# ast tabanlı hesaplayıcı kullanıyoruz. Ayrıca çok uzun / çok büyük
# işlemleri (DoS riski) baştan engelliyoruz.
# --------------------------------------------------------------------------

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

MAX_EXPRESSION_LENGTH = 60          # aşırı uzun ifadeleri reddet
MAX_NUMBER_LENGTH = 15               # tek bir sayı en fazla 15 haneli olabilir


def _safe_eval_node(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            if len(str(node.value).replace(".", "").replace("-", "")) > MAX_NUMBER_LENGTH:
                raise ValueError("Sayı çok büyük.")
            return node.value
        raise ValueError("Geçersiz değer.")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        return _ALLOWED_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval_node(node.operand))
    raise ValueError("Desteklenmeyen işlem.")


def safe_math_eval(expression):
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("İfade çok uzun.")
    tree = ast.parse(expression, mode="eval")
    return _safe_eval_node(tree.body)


def fuzzy_word_in(word, candidates, cutoff=0.8):
    """Kelimeyi ve adaylarını difflib ile karşılaştırıp yazım hatalarını tolere eder."""
    if word in candidates:
        return True
    return bool(get_close_matches(word, candidates, n=1, cutoff=cutoff))


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/get-logs', methods=['POST', 'OPTIONS'])
def get_logs():
    global MAINTENANCE_MODE
    response_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, response_headers

    data = request.json or {}
    password = data.get('password', '')
    action = data.get('action', 'get')

    if password != "4235":
        return jsonify({"success": False, "message": "Hatalı şifre!"}), 403, response_headers

    if action == 'clear':
        if os.path.exists("sorular.txt"):
            os.remove("sorular.txt")
        return jsonify({"success": True, "logs": []}), 200, response_headers

    # 🛠️ BAKIM MODU action'ları (EKLENTİ)
    if action == 'maintenance':
        MAINTENANCE_MODE = True
        _save_maintenance_state(True)
        render_synced = _set_render_env_maintenance(True)
        return jsonify({"success": True, "maintenance": True, "render_synced": render_synced}), 200, response_headers

    if action == 'resume':
        MAINTENANCE_MODE = False
        _save_maintenance_state(False)
        render_synced = _set_render_env_maintenance(False)
        return jsonify({"success": True, "maintenance": False, "render_synced": render_synced}), 200, response_headers

    if action == 'status':
        return jsonify({"success": True, "maintenance": MAINTENANCE_MODE}), 200, response_headers

    if os.path.exists("sorular.txt"):
        with open("sorular.txt", "r", encoding="utf-8") as file:
            logs = file.readlines()
        clean_logs = [line.strip() for line in logs if line.strip()]
        # 🇷🇺➡️🇹🇷 Rusça (Kiril) log satırlarına otomatik Türkçe çeviri ekle (EKLENTİ)
        clean_logs = [add_turkish_translation_to_log_line(line) for line in clean_logs]
        return jsonify({"success": True, "logs": list(reversed(clean_logs)) if clean_logs else ["Henüz hiç soru sorulmadı."]}), 200, response_headers
    return jsonify({"success": True, "logs": ["Henüz hiç soru sorulmadı."]}), 200, response_headers


@app.route('/ask', methods=['POST', 'OPTIONS'])
def ask():
    # 🌐 CORS DESTEĞİ (EKLENTİ) — panel farklı bir adresten (origin) barındırılıyorsa
    # tarayıcı bu isteği CORS koruması yüzünden engelleyebilir. get-logs
    # endpoint'indeki gibi izin başlıklarını burada da ekliyoruz.
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, cors_headers

    # 🧪 ADMİN TEST BYPASS (EKLENTİ) — bakım modundayken bile, panel üzerinden
    # doğru şifre ("4235") ile gönderilen istekler gerçek motoru çalıştırır.
    # Böylece bakım sırasında geliştirmeleri canlıda test edebilirsin;
    # şifresiz/normal kullanıcı istekleri bakım mesajını almaya devam eder.
    is_admin_test = request.json.get("admin_password") == "4235"

    # 🛠️ BAKIM MODU KONTROLÜ (EKLENTİ) — MAINTENANCE_MODE açıkken hiçbir motor
    # (çeviri, matematik, coğrafya, tarih, fen, AI fallback vb.) çalıştırılmaz;
    # log bile tutulmadan direkt sabit mesaj döner. ARIES bu sırada tamamen durmuş sayılır.
    # (Admin test bypass'ı hariç.)
    if MAINTENANCE_MODE and not is_admin_test:
        return jsonify({"reply": MAINTENANCE_MESSAGE, "maintenance": True}), 200, cors_headers

    user_message = request.json.get("message", "").lower().strip()
    raw_message = request.json.get("message", "").strip()
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_ip = request.remote_addr

    def save_log(status_msg):
        with open("sorular.txt", "a", encoding="utf-8") as file:
            file.write(f"[{current_time}] IP: {user_ip} | DURUM: {status_msg} -> Soru: {raw_message}\n")

    # Noktalama temizliği
    user_message = re.sub(r'[.,\?!;\(\)"\'’\-]', '', user_message)
    norm_msg = user_message.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")

    # 🛠️ YANLIŞ YAZIM VE KISALTMA TOLERANS MOTORU (nber, slm, naber, marhaba...)
    typo_rules = {
        "nber": "naber", "nbr": "naber", "slm": "selam", "mrb": "merhaba",
        "mrhb": "merhaba", "knk": "kanka", "kgo": "coğrafya", "mat": "matematik",
        "fzk": "fizik", "gmt": "geometri", "antm": "anatomi", "akciger": "akciyer",
        "marhaba": "merhaba", "mehraba": "merhaba", "selm": "selam",
    }
    words = norm_msg.split()
    fixed_words = [typo_rules.get(w, w) for w in words]
    norm_msg = " ".join(fixed_words)
    norm_msg_nospace = norm_msg.replace(" ", "")  # bitişik yazımları yakalamak için (örn. "kimyapti")

    # Kanka modu SADECE kullanıcı gerçekten "kanka" derse aktif olur.
    # ("naber" artık kanka modunu tetiklemiyor; varsayılan ton ciddi/nazik kalır.)
    is_buddy_mode = "kanka" in norm_msg

    # 🔁 Doğrudan Çeviri Komutu ("kedi'yi ingilizceye çevir", "translate cat to russian")
    phrase_to_translate, translate_target = parse_translation_command(raw_message)
    if phrase_to_translate:
        # Rusça için önce internetsiz yerel sözlüğe bakıyoruz (hızlı ve internetsiz çalışır)
        if translate_target == 'ru':
            key = normalize_tr(phrase_to_translate)
            if key in RU_DICTIONARY:
                ru_word, translit = RU_DICTIONARY[key]
                save_log("CEVAPLANDI")
                return jsonify({"reply": f'<span class="expert-badge badge-sozel">Sözlük (RU)</span><br><b>{phrase_to_translate}</b> → <b>{ru_word}</b><br><span style="opacity:0.7;font-style:italic;">({translit})</span>'})
        # İngilizce veya sözlükte bulunamayan Rusça için (varsa) internet üzerinden çeviri
        if TRANSLATOR_AVAILABLE:
            try:
                translated = GoogleTranslator(source='auto', target=translate_target).translate(phrase_to_translate)
                save_log("CEVAPLANDI")
                badge_label = "Translation" if translate_target == "en" else "Перевод"
                return jsonify({"reply": f'<span class="expert-badge badge-sozel">{badge_label}</span><br><b>{phrase_to_translate}</b> → <b>{translated}</b>'})
            except Exception:
                save_log("HATA")
                return jsonify({"reply": "Çeviri sırasında bir hata oluştu, lütfen tekrar deneyin. / Translation error, please try again."})
        else:
            save_log("HATA")
            return jsonify({"reply": "Bu kelime yerel sözlükte bulunamadı ve online çeviri şu an kullanılamıyor."})

    # 🇹🇷 Tekrar Türkçeye Dönme Kontrolü
    if any(p in norm_msg for p in LANGUAGE_RESET_PHRASES):
        session['lang'] = None
        save_log("CEVAPLANDI")
        return jsonify({"reply": "Tamam, Türkçe devam ediyorum. 🇹🇷"})

    # 🗣️ Dil Sorusu Kontrolü ("do you speak english/russian" vb.) — cevap verdikten sonra
    # o dilde konuşmaya devam etmek için session'a dil modu kaydediliyor.
    if any(p in norm_msg for p in LANGUAGE_PHRASES["english"]):
        session['lang'] = 'en'
        save_log("CEVAPLANDI")
        return jsonify({"reply": "Yes, I speak English! From now on I'll reply in English — ask me anything. Say 'türkçe konuş' anytime to switch back. 🇬🇧"})
    if any(p in norm_msg for p in LANGUAGE_PHRASES["russian"]):
        session['lang'] = 'ru'
        save_log("CEVAPLANDI")
        return jsonify({"reply": "Да, я говорю по-русски! Теперь буду отвечать по-русски — спрашивайте что угодно. Скажите «türkçe konuş», чтобы вернуться к турецкому. 🇷🇺"})

    # 🏗️ Yapımcı Kontrolü (bitişik yazımı da destekler: "seni kimyaptı")
    if any(p in norm_msg for p in CREATOR_PHRASES) or any(p.replace(" ", "") in norm_msg_nospace for p in CREATOR_PHRASES):
        save_log("CEVAPLANDI")
        if is_buddy_mode:
            return build_reply('<span class="expert-badge badge-sozel">Sistem Çekirdeği</span><br>Beni tam bir dahi olmam için <b>MİC</b> geliştirdi kanka! Adım <b>ARIES AI</b>. 🚀')
        return build_reply('<span class="expert-badge badge-sozel">Sistem Çekirdeği</span><br>Beni <b>MİC</b> geliştirdi. Adım <b>ARIES AI</b>.')

    # 👋 Selamlaşma Kontrolü (fuzzy: "naber", "marhaba dostum" gibi yazım hatalarını da yakalar)
    if any(fuzzy_word_in(w, GREETING_WORDS) for w in fixed_words):
        save_log("CEVAPLANDI")
        if is_buddy_mode:
            return build_reply("Naber kanka! ARIES AI hazır, ne soruyoruz? 😎")
        return build_reply("Merhaba, ben ARIES AI. Size nasıl yardımcı olabilirim?")

    # 🙏 Teşekkür Kontrolü ("teşekkürler", "sağol", "eyvallah" vb. — fuzzy eşleşme ile yazım hatalarını da tolere eder)
    if any(fuzzy_word_in(w, THANKS_WORDS, cutoff=0.75) for w in fixed_words):
        save_log("CEVAPLANDI")
        if is_buddy_mode:
            return build_reply("Rica ederim kanka, başka bir sorun olursa buradayım! 🙌")
        return build_reply("Rica ederim, başka bir konuda yardımcı olabilirim.")

    # 😊 "Rica ederim / bir şey değil" Kontrolü (kullanıcı bota bu şekilde karşılık verdiğinde)
    if any(p in norm_msg_nospace for p in YOURE_WELCOME_WORDS):
        save_log("CEVAPLANDI")
        return build_reply("Ne demek, her zaman yardımcı olmaktan memnuniyet duyarım. 😊")

    # 🔢 Matematik Motoru (güvenli hesaplayıcı ile)
    math_message = user_message.replace(",", ".")
    math_chars = set("0123456789+-*/(). ")
    if any(char in math_message for char in ['+', '-', '*', '/']) and set(math_message).issubset(math_chars):
        try:
            result = safe_math_eval(math_message)
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sayisal">Matematiksel Analiz</span><br><div class="formula-box">{user_message} = {result}</div>')
        except Exception:
            save_log("HATA")
            if is_buddy_mode:
                return build_reply("İşlem hesaplanamadı kanka, sayılar çok büyük olabilir ya da ifade geçersiz. Kontrol et.")
            return build_reply("İşlem hesaplanamadı. Sayılar çok büyük olabilir ya da ifade geçersiz görünüyor, lütfen kontrol edin.")

    # 🧬 Anatomi ve Fen Bilgisi Kontrolü
    for key, response in science_database.items():
        if key in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sayisal" style="background-color:#00e676; color:black;">Fen Bilimleri & Anatomi</span><br>{response}')

    # ⚡ Fizik ve Geometri Kontrolü
    for key, response in physics_geometry_database.items():
        if key in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sayisal" style="background-color:#ff9100; color:black;">Fizik & Geometri</span><br>{response}')

    # 🕋 Dini Terimler Kontrolü
    for key, response in religious_database.items():
        if key in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sozel" style="background-color:#9c27b0;">İslami Tarih</span><br>{response}')

    # 📜 Tarih Kontrolü
    for key, response in historical_events.items():
        if key.replace("ı", "i").replace("ğ", "g") in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sozel">Tarih Bilgisi</span><br>{response}')

    # 🌍 Coğrafya Kontrolü
    matched_countries = []
    for country, data in world_countries.items():
        if country in norm_msg:
            matched_countries.append({"name": country.upper(), "b": data["b"], "k": data["k"], "lat": data["lat"], "lon": data["lon"], "bilgi": data["bilgi"]})

    if len(matched_countries) >= 2:
        distance = calculate_haversine(matched_countries[0]["lat"], matched_countries[0]["lon"], matched_countries[1]["lat"], matched_countries[1]["lon"])
        save_log("CEVAPLANDI")
        return build_reply(f'<span class="expert-badge badge-cografya">Rota Analizi</span><br>📐 <b>Mesafe:</b> ~{distance} Kilometre')
    elif len(matched_countries) == 1:
        save_log("CEVAPLANDI")
        return build_reply(f'<span class="expert-badge badge-cografya">Coğrafya</span><br><b>Ülke:</b> {matched_countries[0]["name"]}<br><b>Başkent:</b> {matched_countries[0]["b"]}')

    # 🤖 Kural tabanlı sistemde eşleşme bulunamadı — AI_API_KEY girilmişse
    # gerçek bir yapay zekaya sorup daha akıllı/geniş kapsamlı cevap üretmeyi dene.
    ai_reply = ask_ai_fallback(raw_message, buddy_mode=is_buddy_mode)
    if ai_reply:
        save_log("CEVAPLANDI (AI)")
        return build_reply(f'<span class="expert-badge badge-sozel" style="background-color:#8e44ad;">Genişletilmiş Zeka</span><br>{ai_reply}')

    save_log("CEVAPLANAMADI")
    if is_buddy_mode:
        return build_reply("ARIES bu soruyu analiz etti ama tam bir eşleşme bulamadı kanka. Matematik, fen, fizik, geometri, anatomi, tarih veya coğrafya sormayı dene!")
    return build_reply("ARIES bu soruyu analiz etti ancak tam bir eşleşme bulamadı. Matematik, fen bilimleri, fizik, geometri, anatomi, tarih veya coğrafya ile ilgili bir soru sormayı deneyebilirsiniz.")


if __name__ == '__main__':
    app.run(debug=True)
