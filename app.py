from flask import Flask, render_template, request, jsonify, session, redirect

import ast
import operator
import math
import requests
import os
import re
import html
import json
import uuid
from difflib import get_close_matches
from datetime import datetime, timedelta

# 🌐 ÇEVİRİ MOTORU (dil modu ve /çevir komutu için)
# Sunucuda kurulu değilse çeviri özellikleri sessizce devre dışı kalır.
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False

# 🔑 GOOGLE İLE GİRİŞ (EKLENTİ) — Authlib kurulu değilse sessizce devre dışı kalır
try:
    from authlib.integrations.flask_client import OAuth
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "aries-ai-cok-gizli-anahtar-2026")

# 🕒 KALICI OTURUM (EKLENTİ)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


@app.before_request
def _make_session_permanent():
    session.permanent = True

# --------------------------------------------------------------------------
# 🔑 GOOGLE İLE GİRİŞ + GİRİŞ YAPMAYANLARA MESAJ SINIRI (EKLENTİ)
# --------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_LOGIN_ENABLED = OAUTH_AVAILABLE and bool(GOOGLE_CLIENT_ID) and bool(GOOGLE_CLIENT_SECRET)

GUEST_MESSAGE_LIMIT = 15

oauth = None
if GOOGLE_LOGIN_ENABLED:
    oauth = OAuth(app)
    oauth.register(
        name='google',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )


@app.route('/login/google')
def login_google():
    if not GOOGLE_LOGIN_ENABLED:
        return jsonify({"success": False, "error": "Google girişi ayarlanmamış."}), 503
    redirect_uri = request.url_root.rstrip('/') + '/login/google/callback'
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/login/google/callback')
def login_google_callback():
    if not GOOGLE_LOGIN_ENABLED:
        return jsonify({"success": False, "error": "Google girişi ayarlanmamış."}), 503
    token = oauth.google.authorize_access_token()
    user_info = token.get('userinfo', {})
    session['google_user'] = {
        "email": user_info.get("email", ""),
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
    }
    session['guest_message_count'] = 0
    return redirect(request.url_root.rstrip('/') + '/')


@app.route('/logout/google')
def logout_google():
    session.pop('google_user', None)
    return redirect(request.url_root.rstrip('/') + '/')


@app.route('/api/auth-status')
def auth_status():
    user = session.get('google_user')
    return jsonify({
        "logged_in": bool(user),
        "user": user,
        "google_login_enabled": GOOGLE_LOGIN_ENABLED,
        "guest_message_count": session.get('guest_message_count', 0),
        "guest_message_limit": GUEST_MESSAGE_LIMIT,
    })

# --------------------------------------------------------------------------
# 🖥️ BİLGİSAYAR AJANI ALTYAPISI (EKLENTİ) — SINIRLI VE KONTROLLÜ
# --------------------------------------------------------------------------
AGENT_SECRET = os.environ.get("AGENT_SECRET", "")

APP_CLOSE_WHITELIST = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "krom": "chrome.exe",
    "firefox": "firefox.exe",
    "edge": "msedge.exe",
    "notepad": "notepad.exe",
    "not defteri": "notepad.exe",
    "hesap makinesi": "CalculatorApp.exe",
    "spotify": "Spotify.exe",
    "discord": "Discord.exe",
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
}

pending_agent_commands = []

APP_CLOSE_PATTERN = re.compile(
    r'\b([a-zçğıöşü ]{2,20}?)\s*(?:yi|yı|i|ı|u|ü)?\s*kapat',
    re.IGNORECASE
)


def try_queue_app_close_command(norm_msg, raw_message):
    match = APP_CLOSE_PATTERN.search(norm_msg)
    if not match:
        return None
    app_name_raw = match.group(1).strip()
    if app_name_raw not in APP_CLOSE_WHITELIST:
        return None
    process_name = APP_CLOSE_WHITELIST[app_name_raw]
    pending_agent_commands.append({
        "action": "close_app",
        "target": process_name,
        "issued_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    return f"🖥️ Tamam, <b>{app_name_raw}</b> kapatılıyor... (bilgisayarındaki ajan programı bir sonraki kontrolünde bunu uygulayacak)"

# --------------------------------------------------------------------------
# 🛠️ BAKIM MODU (EKLENTİ)
# --------------------------------------------------------------------------
MAINTENANCE_MODE = False
MAINTENANCE_MESSAGE = "Şu an bakım arasındayız."

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


RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "srv-d8pfgaj7uimc73a5i2eg")
ARIES_MAINTENANCE_ENV_KEY = "ARIES_MAINTENANCE"


def _set_render_env_maintenance(is_on):
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


_env_maintenance = os.environ.get(ARIES_MAINTENANCE_ENV_KEY, "")
if _env_maintenance in ("1", "true", "True"):
    MAINTENANCE_MODE = True
elif _env_maintenance in ("0", "false", "False"):
    MAINTENANCE_MODE = False
else:
    MAINTENANCE_MODE = _load_maintenance_state()

# --------------------------------------------------------------------------
# 🚫 KARA LİSTE / BANLAMA SİSTEMİ (EKLENTİ)
# --------------------------------------------------------------------------
# İki türde ban yapılabilir:
#   - "ip"     -> istekteki IP adresine göre banlar (VPN/ağ değişirse aşılabilir)
#   - "device" -> tarayıcıya bıraktığımız kalıcı bir çerez (cihaz kimliği) ile
#                 banlar; IP değişse bile aynı cihazı/tarayıcıyı tanır.
# Süreli (dakika) veya süresiz ban desteklenir. banlist.json dosyasında saklanır.
BANLIST_FILE = "banlist.json"
DEVICE_COOKIE_NAME = "aries_device_id"


def _load_banlist():
    if not os.path.exists(BANLIST_FILE):
        return {}
    try:
        with open(BANLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_banlist(data):
    with open(BANLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


banlist = _load_banlist()  # { "ip:1.2.3.4": {...}, "device:uuid...": {...} }


def _ban_key(kind, value):
    return f"{kind}:{value}"


def is_banned(ip, device_id):
    """IP veya cihaz kimliği kara listede mi (ve süresi dolmamış mı) kontrol eder.
    Süresi dolmuş kayıtları otomatik temizler."""
    now = datetime.now()
    changed = False
    result = None
    for key in (_ban_key("ip", ip), _ban_key("device", device_id)):
        entry = banlist.get(key)
        if not entry:
            continue
        until = entry.get("until")
        if until is None:
            result = entry
            continue
        try:
            if now < datetime.fromisoformat(until):
                result = entry
            else:
                del banlist[key]  # süresi dolmuş, temizle
                changed = True
        except Exception:
            continue
    if changed:
        _save_banlist(banlist)
    return result


@app.before_request
def _ensure_device_id():
    request.aries_device_id = request.cookies.get(DEVICE_COOKIE_NAME) or uuid.uuid4().hex


@app.after_request
def _set_device_cookie(response):
    if request.cookies.get(DEVICE_COOKIE_NAME) != getattr(request, "aries_device_id", None):
        response.set_cookie(
            DEVICE_COOKIE_NAME, request.aries_device_id,
            max_age=60 * 60 * 24 * 730, httponly=True, samesite="Lax"
        )
    return response


@app.route('/api/banlist', methods=['POST', 'OPTIONS'])
def manage_banlist():
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, cors_headers

    data = request.json or {}
    if data.get('password') != "4275":
        return jsonify({"success": False, "message": "Hatalı şifre!"}), 403, cors_headers

    action = data.get('action', 'list')

    if action == 'list':
        return jsonify({"success": True, "bans": banlist}), 200, cors_headers

    if action == 'ban':
        kind = data.get('kind', 'ip')  # "ip" veya "device"
        value = (data.get('value') or '').strip()
        reason = data.get('reason', '').strip()
        duration_minutes = data.get('duration_minutes')  # boş/None = süresiz
        if not value:
            return jsonify({"success": False, "message": "Değer boş olamaz."}), 400, cors_headers
        until = None
        if duration_minutes:
            until = (datetime.now() + timedelta(minutes=int(duration_minutes))).isoformat()
        banlist[_ban_key(kind, value)] = {
            "kind": kind, "value": value, "reason": reason,
            "until": until, "created_at": datetime.now().isoformat()
        }
        _save_banlist(banlist)
        return jsonify({"success": True, "bans": banlist}), 200, cors_headers

    if action == 'unban':
        key = data.get('key')
        if key in banlist:
            del banlist[key]
            _save_banlist(banlist)
        return jsonify({"success": True, "bans": banlist}), 200, cors_headers

    return jsonify({"success": False, "message": "Bilinmeyen işlem."}), 400, cors_headers


LOG_LINE_PATTERN = re.compile(
    r'\[(?P<time>[^\]]+)\]\s*IP:\s*(?P<ip>\S+)\s*\|\s*CIHAZ:\s*(?P<device>\S*)\s*\|\s*DURUM:\s*(?P<status>[^-]+?)\s*->\s*Soru:\s*(?P<question>.*)$'
)


@app.route('/api/recent-visitors', methods=['POST', 'OPTIONS'])
def recent_visitors():
    """Log dosyasından son benzersiz ziyaretçileri (IP + cihaz kombinasyonu)
    çıkarır. Panel bunu tek tıkla banlama listesi olarak kullanır."""
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, cors_headers

    data = request.json or {}
    if data.get('password') != "4275":
        return jsonify({"success": False, "message": "Hatalı şifre!"}), 403, cors_headers

    if not os.path.exists("sorular.txt"):
        return jsonify({"success": True, "visitors": []}), 200, cors_headers

    with open("sorular.txt", "r", encoding="utf-8") as f:
        lines = f.readlines()

    # ip+cihaz kombinasyonuna göre en son görülen mesajı tut (en yeniden en eskiye doğru tara)
    visitors = {}
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        m = LOG_LINE_PATTERN.match(line)
        if not m:
            continue
        ip = m.group("ip")
        device = m.group("device") or ""
        key = f"{ip}|{device}"
        if key in visitors:
            continue  # bu kombinasyonun daha yeni bir kaydını zaten gördük
        ban_entry = is_banned(ip, device)
        visitors[key] = {
            "ip": ip,
            "device": device,
            "time": m.group("time"),
            "question": m.group("question")[:80],
            "is_banned": bool(ban_entry),
        }
        if len(visitors) >= 50:  # son 50 benzersiz ziyaretçiyle sınırla
            break

    return jsonify({"success": True, "visitors": list(visitors.values())}), 200, cors_headers


@app.route('/api/whoami', methods=['GET'])
def whoami():
    # Kendi IP/cihaz kimliğini doğrulamak istersen kullanılabilir (opsiyonel yardımcı endpoint)
    return jsonify({
        "ip": request.remote_addr,
        "device_id": getattr(request, "aries_device_id", request.cookies.get(DEVICE_COOKIE_NAME))
    })


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

# --------------------------------------------------------------------------
# 🤖 GELİŞMİŞ YAPAY ZEKA DESTEĞİ
# --------------------------------------------------------------------------
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_API_PROVIDER = os.environ.get("AI_API_PROVIDER", "gemini").strip().lower()
AI_MODEL_OPENAI = "gpt-4o-mini"
AI_MODEL_ANTHROPIC = "claude-3-5-haiku-20241022"
AI_MODEL_GEMINI = "gemini-2.5-flash"

if AI_API_KEY:
    if AI_API_KEY.startswith("sk-ant-"):
        AI_API_PROVIDER = "anthropic"
    elif AI_API_KEY.startswith("sk-"):
        AI_API_PROVIDER = "openai"
    elif AI_API_KEY.startswith("AIza"):
        AI_API_PROVIDER = "gemini"


def ask_ai_fallback(user_text, buddy_mode=False, history=None):
    """Kural tabanlı sistem cevap bulamadığında çağrılır."""
    if not AI_API_KEY:
        return None

    history = history or []

    system_prompt = (
        "Sen ARIES AI adında, Türkçe konuşan, çok yönlü ve son derece yetkin bir yapay zeka "
        "asistanısın. Matematik, tarih (Osmanlı, Türk Kurtuluş Savaşı, dünya tarihi), coğrafya, "
        "fen bilimleri, fizik gibi konularda derinlemesine bilgin var; ama bunlarla sınırlı "
        "değilsin — teknoloji, kodlama, yazım/düzenleme, tavsiye, özetleme, analiz, günlük "
        "hayat soruları dahil HER konuda elinden gelenin en iyisini yaparak yardımcı olursun. "
        "Karmaşık bir soru geldiğinde önce sessizce adım adım düşün, sonra net ve düzenli bir "
        "cevap ver (gerekiyorsa madde işaretleri veya kısa paragraflarla). Cevapların gereksiz "
        "yere uzun olmasın; kısa ama bilgi yoğunluğu yüksek, doğrudan ve anlaşılır olsun. "
        "Emin olmadığın veya kesin bilmediğin bir bilgiyi kesinmiş gibi uydurma; belirsizse "
        "bunu açıkça belirt. Önceki mesajlar sağlanmışsa konuşmanın bağlamını dikkate al ve "
        "tutarlı, önceki cevaplarınla çelişmeyen bir cevap ver. Kullanıcıya karşı her zaman "
        "saygılı, sabırlı ve yardımsever ol. "
        "KODLAMA KONUSUNDA (EKLENTİ): Kullanıcı senden kod istediğinde ya da bir hata/bug "
        "sorduğunda, mutlaka ```dil ... ``` şeklinde bir kod bloğu içinde, çalışır durumda, "
        "gerekli yerlerde kısa Türkçe yorum satırları eklenmiş, temiz ve okunabilir kod yaz. "
        "Kodun ne işe yaradığını 1-2 cümlelik kısa bir açıklamayla başta veya sonda özetle; "
        "kod bloğunu gereksiz uzun anlatımlarla şişirme. Eğer bir hata ayıklaması (debug) "
        "isteniyorsa önce hatanın kök nedenini net şekilde belirt, sonra düzeltilmiş kodu ver. "
        + ("Samimi ve arkadaşça (kanka diliyle) konuş." if buddy_mode else "Kibar ve profesyonel bir dille konuş.")
    )

    try:
        if AI_API_PROVIDER == "gemini":
            gemini_contents = [
                {"role": ("model" if h.get("role") == "assistant" else "user"), "parts": [{"text": h.get("content", "")}]}
                for h in history
            ]
            gemini_contents.append({"role": "user", "parts": [{"text": user_text}]})
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL_GEMINI}:generateContent",
                headers={"Content-Type": "application/json"},
                params={"key": AI_API_KEY},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": gemini_contents,
                    "generationConfig": {"maxOutputTokens": 1500},
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip() or None

        elif AI_API_PROVIDER == "anthropic":
            anthropic_messages = list(history) + [{"role": "user", "content": user_text}]
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": AI_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": AI_MODEL_ANTHROPIC,
                    "max_tokens": 1500,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip() or None

        else:  # openai
            openai_messages = [{"role": "system", "content": system_prompt}] + list(history) + [{"role": "user", "content": user_text}]
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL_OPENAI,
                    "messages": openai_messages,
                    "max_tokens": 1500,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip() or None

    except Exception as e:
        try:
            error_detail = str(e)
            if hasattr(e, "response") and e.response is not None:
                error_detail += f" | HTTP {e.response.status_code}: {e.response.text[:500]}"
            with open("ai_errors.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sağlayıcı: {AI_API_PROVIDER} | Hata: {error_detail}\n")
        except Exception:
            pass
        return None

# 📖 OFİS İÇİ (İNTERNETSİZ) TÜRKÇE-RUSÇA SÖZLÜK
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
}

RU_TO_TR_DICTIONARY = {
    'привет': 'Merhaba',
    'спасибо': 'Teşekkür ederim',
    'да': 'Evet',
    'нет': 'Hayır',
}

EN_DICTIONARY = {
    'merhaba': 'Hello',
    'gunaydin': 'Good morning',
    'tesekkur ederim': 'Thank you',
    'evet': 'Yes',
    'hayir': 'No',
}

# 🌍 COĞRAFYA VERİ TABANI (kısaltıldı — orijinal projedeki tam sözlüğü buraya taşıyabilirsin)
world_countries = {
    "turkiye": {"b": "Ankara", "k": "Asya/Avrupa", "lat": 39.93, "lon": 32.85, "bilgi": "Asya ve Avrupa'yı birbirine bağlayan stratejik bir köprü ülkedir."},
    "almanya": {"b": "Berlin", "k": "Avrupa", "lat": 52.52, "lon": 13.40, "bilgi": "Orta Avrupa'da yer alan sanayi devidir."},
    "japonya": {"b": "Tokyo", "k": "Asya", "lat": 35.68, "lon": 139.69, "bilgi": "Pasifik Okyanusu'nda yer alan, teknolojisiyle bilinen bir ada ülkesidir."},
}

historical_events = {
    "istanbulun fethi": "<b>1453 - İstanbul'un Fethi:</b> Fatih Sultan Mehmed liderliğindeki Osmanlı ordusu Bizans'ı yıktı. Orta Çağ kapandı, Yeni Çağ başladı.",
    "cumhuriyetin ilani": "<b>29 Ekim 1923 - Cumhuriyetin İlanı:</b> Gazi Mustafa Kemal Atatürk önderliğinde Türkiye Cumhuriyeti resmen kuruldu. 🇹🇷",
}

religious_database = {
    "hicret": "<b>Hicret (622):</b> Hz. Muhammed (s.a.v.) ve Müslümanların Mekke'den Medine'ye göç etmesidir. Hicri takvimin başlangıcıdır.",
}

science_database = {
    "kalp": "<b>Anatomi - Kalp:</b> Göğüs boşluğunda yer alan, kaslı bir pompadır. Vücuda kan pompalar.",
    "fotosentez": "<b>Fen Bilgisi - Fotosentez:</b> Bitkilerin kloroplast organelinde, güneş ışığı yardımıyla su ve karbondioksiti birleştirerek besin (glikoz) ve oksijen üretmesi olayıdır.",
}

physics_geometry_database = {
    "yercekimi": "<b>Fizik - Yerçekimi Kuvveti:</b> Kütlesi olan cisimlerin birbirini çekmesidir. $g = 9.81 m/s^2$",
    "ucgen": "<b>Geometri - Üçgen:</b> Üç doğrunun kesişmesiyle oluşan kapalı şekildir. İç açılarının toplamı **180°**'dir.",
}

# 👋 SELAMLAŞMA / TEŞEKKÜR / ARGO KALIPLARI
GREETING_WORDS = ["selam", "merhaba", "naber", "selamlar", "merhabalar", "hey", "hi", "hello", "gunaydin", "iyi gunler", "iyi aksamlar"]
THANKS_WORDS = ["tesekkurler", "tesekkur", "sagol", "sagolasin", "eyvallah", "sagolun", "minnettarim", "harikasin", "super", "mukemmel"]
YOURE_WELCOME_WORDS = ["ricaederim", "ricaederiz", "birseydegil", "nedemek", "onemlidegil"]
CREATOR_PHRASES = ["kim yapti", "yapimcin", "kim gelistirdi", "kurucun", "sahibin", "sen kimsin", "adini kim verdi"]
INSULT_WORDS = ["ahmak", "aptal", "beyinsiz", "salak", "gerzek", "mal", "aq", "amk", "siktir", "orospu", "piç", "yarrak"]
FRUSTRATION_PHRASES = ["dalga mı geciyon", "dalga geciyorsun", "dalga geciyon musun", "kafa mı buluyorsun"]

LANGUAGE_PHRASES = {
    "english": ["do you speak english", "can you speak english", "speak english", "ingilizce biliyor musun", "ingilizce konusuyor musun"],
    "russian": ["do you speak russian", "can you speak russian", "speak russian", "rusca biliyor musun", "rusca konusuyor musun",
                "ты говоришь порусски", "говоришь порусски", "вы говорите порусски"],
}
LANGUAGE_RESET_PHRASES = ["turkce konus", "turkceye don", "turkce devam et", "speak turkish", "turkish konus"]

TRANSLATE_TO_EN_TR = re.compile(r'^(.+?)\s*(?:kelimesini|ifadesini|cümlesini|cumlesini)?\s*ingilizceye\s*çevir\.?$', re.IGNORECASE)
TRANSLATE_TO_RU_TR = re.compile(r'^(.+?)\s*(?:kelimesini|ifadesini|cümlesini|cumlesini)?\s*rusçaya\s*çevir\.?$', re.IGNORECASE)
TRANSLATE_TO_EN_ENG = re.compile(r'^translate\s+(.+?)\s+to\s+english\.?$', re.IGNORECASE)
TRANSLATE_TO_RU_ENG = re.compile(r'^translate\s+(.+?)\s+to\s+russian\.?$', re.IGNORECASE)


def normalize_tr(s):
    s = s.replace("İ", "i").replace("I", "ı")
    s = s.lower().strip()
    s = s.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    s = s.rstrip("?!.,")
    return s


def parse_translation_command(text):
    text = text.strip()
    for pattern, target in [(TRANSLATE_TO_EN_TR, 'en'), (TRANSLATE_TO_RU_TR, 'ru'),
                             (TRANSLATE_TO_EN_ENG, 'en'), (TRANSLATE_TO_RU_ENG, 'ru')]:
        m = pattern.match(text)
        if m:
            return m.group(1).strip(), target
    return None, None


def translate_html_preserving_tags(html_text, target_lang):
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


CYRILLIC_PATTERN = re.compile(r'[\u0400-\u04FF]')
ENGLISH_HINT_PATTERN = re.compile(
    r'\b(the|is|are|what|how|why|when|where|which|who|hello|please|thanks|thank you|'
    r'would|could|can you|do you|does|have you|i am|i\'m|you are|you\'re)\b',
    re.IGNORECASE
)
TURKISH_CHAR_PATTERN = re.compile(r'[çğıöşüÇĞİÖŞÜ]')


def add_turkish_translation_to_log_line(log_line):
    is_russian = bool(CYRILLIC_PATTERN.search(log_line))
    is_english = (not is_russian) and bool(ENGLISH_HINT_PATTERN.search(log_line)) and not TURKISH_CHAR_PATTERN.search(log_line)

    if not is_russian and not is_english:
        return log_line
    if not TRANSLATOR_AVAILABLE:
        return log_line
    try:
        if "-> Soru: " in log_line:
            prefix, question_part = log_line.split("-> Soru: ", 1)
            source_lang = 'ru' if is_russian else 'en'
            translated = GoogleTranslator(source=source_lang, target='tr').translate(question_part)
            return f"{prefix}-> Soru: {question_part} (TR: {translated})"
        return log_line
    except Exception:
        return log_line


def format_code_blocks(text):
    if not text:
        return text

    def _replace_block(match):
        lang = (match.group(1) or "").strip()
        code = match.group(2)
        escaped_code = html.escape(code.strip("\n"))
        lang_label = lang if lang else "kod"
        return (
            '<div class="code-block">'
            f'<div class="code-block-header"><span class="code-lang">{html.escape(lang_label)}</span>'
            '<button class="copy-btn" onclick="copyCodeBlock(this)">📋 Kopyala</button></div>'
            f'<pre><code>{escaped_code}</code></pre>'
            '</div>'
        )

    text = re.sub(r'```(\w*)\n?(.*?)```', _replace_block, text, flags=re.DOTALL)
    text = re.sub(r'`([^`\n]+)`', lambda m: f'<code class="inline-code">{html.escape(m.group(1))}</code>', text)

    return text


def build_reply(text):
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


# --------------------------------------------------------------------------
# 🔒 GÜVENLİ MATEMATİK MOTORU (eval() yerine)
# --------------------------------------------------------------------------

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Pow: operator.pow,
}

_ALLOWED_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}

MAX_EXPRESSION_LENGTH = 80
MAX_NUMBER_LENGTH = 15
MAX_POWER_EXPONENT = 20


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
        if isinstance(node.op, ast.Pow) and (abs(right) > MAX_POWER_EXPONENT):
            raise ValueError("Üs değeri çok büyük.")
        return _ALLOWED_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval_node(node.operand))
    if isinstance(node, ast.Call):
        func_name = getattr(node.func, "id", None)
        if func_name in _ALLOWED_FUNCTIONS and len(node.args) == 1 and not node.keywords:
            arg_value = _safe_eval_node(node.args[0])
            try:
                return _ALLOWED_FUNCTIONS[func_name](arg_value)
            except ValueError:
                raise ValueError("Fonksiyon için geçersiz değer (örn. negatif sayının karekökü).")
        raise ValueError("Desteklenmeyen fonksiyon.")
    raise ValueError("Desteklenmeyen işlem.")


def safe_math_eval(expression):
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("İfade çok uzun.")
    tree = ast.parse(expression, mode="eval")
    return _safe_eval_node(tree.body)


def fuzzy_word_in(word, candidates, cutoff=0.8):
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

    if password != "4275":
        return jsonify({"success": False, "message": "Hatalı şifre!"}), 403, response_headers

    if action == 'clear':
        if os.path.exists("sorular.txt"):
            os.remove("sorular.txt")
        return jsonify({"success": True, "logs": []}), 200, response_headers

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
        clean_logs = [add_turkish_translation_to_log_line(line) for line in clean_logs]
        return jsonify({"success": True, "logs": list(reversed(clean_logs)) if clean_logs else ["Henüz hiç soru sorulmadı."]}), 200, response_headers
    return jsonify({"success": True, "logs": ["Henüz hiç soru sorulmadı."]}), 200, response_headers


@app.route('/api/agent-poll', methods=['POST', 'OPTIONS'])
def agent_poll():
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200

    if not AGENT_SECRET:
        return jsonify({"success": False, "error": "Ajan devre dışı (AGENT_SECRET ayarlanmamış)."}), 403

    data = request.json or {}
    if data.get("secret") != AGENT_SECRET:
        return jsonify({"success": False, "error": "Yetkisiz erişim."}), 403

    global pending_agent_commands
    commands = pending_agent_commands
    pending_agent_commands = []
    return jsonify({"success": True, "commands": commands})


@app.route('/ask', methods=['POST', 'OPTIONS'])
def ask():
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, cors_headers

    is_admin_test = request.json.get("admin_password") == "4275"

    # 🚫 KARA LİSTE KONTROLÜ (EKLENTİ) — admin test bypass hariç, banlı IP/cihaz
    # hiçbir motoru çalıştırmadan sabit engel mesajı alır.
    ban_entry = is_banned(request.remote_addr, getattr(request, "aries_device_id", None))
    if ban_entry and not is_admin_test:
        until = ban_entry.get("until")
        if until:
            pretty_until = until[:16].replace("T", " ")
            msg = f"🚫 Erişiminiz {pretty_until} tarihine kadar engellenmiştir. Sebep: {ban_entry.get('reason') or 'belirtilmedi'}"
        else:
            msg = f"🚫 Erişiminiz süresiz olarak engellenmiştir. Sebep: {ban_entry.get('reason') or 'belirtilmedi'}"
        return jsonify({"reply": msg, "banned": True}), 200, cors_headers

    if MAINTENANCE_MODE and not is_admin_test:
        return jsonify({"reply": MAINTENANCE_MESSAGE, "maintenance": True}), 200, cors_headers

    if GOOGLE_LOGIN_ENABLED and not is_admin_test and not session.get('google_user'):
        current_count = session.get('guest_message_count', 0)
        if current_count >= GUEST_MESSAGE_LIMIT:
            return jsonify({
                "reply": f"💬 Misafir kullanıcılar için {GUEST_MESSAGE_LIMIT} mesajlık ücretsiz sınıra ulaştın. "
                         f"Devam etmek için lütfen Google ile giriş yap.",
                "limit_reached": True
            }), 200, cors_headers
        session['guest_message_count'] = current_count + 1

    user_message = request.json.get("message", "").lower().strip()
    raw_message = request.json.get("message", "").strip()
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_ip = request.remote_addr

    def save_log(status_msg):
        # 🚫 KARA LİSTE (EKLENTİ): cihaz kimliğini de log satırına yazıyoruz ki
        # panelde "Son Ziyaretçiler" listesinden tek tıkla banlanabilsin.
        device_id = getattr(request, "aries_device_id", "")
        with open("sorular.txt", "a", encoding="utf-8") as file:
            file.write(f"[{current_time}] IP: {user_ip} | CIHAZ: {device_id} | DURUM: {status_msg} -> Soru: {raw_message}\n")

    user_message = re.sub(r'[.,\?!;\(\)"\'’\-]', '', user_message)
    norm_msg = user_message.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")

    typo_rules = {
        "nber": "naber", "nbr": "naber", "slm": "selam", "mrb": "merhaba",
        "mrhb": "merhaba", "knk": "kanka", "kgo": "coğrafya", "mat": "matematik",
        "fzk": "fizik", "gmt": "geometri", "antm": "anatomi", "akciger": "akciyer",
        "marhaba": "merhaba", "mehraba": "merhaba", "selm": "selam",
        "slam": "selam", "selamm": "selam", "mrhba": "merhaba", "merhabaa": "merhaba",
        "nbr2": "naber", "naberr": "naber", "naaber": "naber", "napiyorsun": "naber",
        "n'aber": "naber", "n'apiyorsun": "naber",
        "tsk": "tesekkurler", "tskler": "tesekkurler", "sagolun": "tesekkurler",
        "eyv": "eyvallah", "eyvl": "eyvallah",
        "cvp": "cevap", "sorug": "soru", "sory": "soru",
        "trh": "tarih", "trih": "tarih", "cogr": "coğrafya", "cografya": "coğrafya",
        "fen": "fen bilgisi", "fenn": "fen bilgisi",
        "din": "dini bilgi", "dinn": "dini bilgi",
        "kanks": "kanka", "kanka2": "kanka", "abi": "kanka", "abicim": "kanka",
        "hocam": "merhaba", "reis": "kanka",
        "napan": "naber", "naapiyon": "naber", "ne haber": "naber",
        "gunaydinn": "gunaydin", "gunaydn": "gunaydin",
        "iyi aksamlarr": "iyi aksamlar", "iyi gecelerr": "iyi geceler",
    }
    words = norm_msg.split()
    fixed_words = [typo_rules.get(w, w) for w in words]
    norm_msg = " ".join(fixed_words)
    norm_msg_nospace = norm_msg.replace(" ", "")

    is_buddy_mode = "kanka" in norm_msg

    agent_reply = try_queue_app_close_command(norm_msg, raw_message)
    if agent_reply:
        save_log("CEVAPLANDI (AGENT)")
        return build_reply(agent_reply)

    phrase_to_translate, translate_target = parse_translation_command(raw_message)
    if phrase_to_translate:
        if translate_target == 'ru':
            key = normalize_tr(phrase_to_translate)
            if key in RU_DICTIONARY:
                ru_word, translit = RU_DICTIONARY[key]
                save_log("CEVAPLANDI")
                return jsonify({"reply": f'<span class="expert-badge badge-sozel">Sözlük (RU)</span><br><b>{phrase_to_translate}</b> → <b>{ru_word}</b><br><span style="opacity:0.7;font-style:italic;">({translit})</span>'})
        if translate_target == 'en':
            key = normalize_tr(phrase_to_translate)
            if key in EN_DICTIONARY:
                en_word = EN_DICTIONARY[key]
                save_log("CEVAPLANDI")
                return jsonify({"reply": f'<span class="expert-badge badge-sozel">Sözlük (EN)</span><br><b>{phrase_to_translate}</b> → <b>{en_word}</b>'})
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

    if any(p in norm_msg for p in LANGUAGE_RESET_PHRASES):
        session['lang'] = None
        save_log("CEVAPLANDI")
        return jsonify({"reply": "Tamam, Türkçe devam ediyorum. 🇹🇷"})

    if any(p in norm_msg for p in LANGUAGE_PHRASES["english"]):
        session['lang'] = 'en'
        save_log("CEVAPLANDI")
        return jsonify({"reply": "Yes, I speak English! From now on I'll reply in English — ask me anything. Say 'türkçe konuş' anytime to switch back. 🇬🇧"})
    if any(p in norm_msg for p in LANGUAGE_PHRASES["russian"]):
        session['lang'] = 'ru'
        save_log("CEVAPLANDI")
        return jsonify({"reply": "Да, я говорю по-русски! Теперь буду отвечать по-русски — спрашивайте что угодно. Скажите «türkçe konuş», чтобы вернуться к турецкому. 🇷🇺"})

    if any(p in norm_msg for p in CREATOR_PHRASES) or any(p.replace(" ", "") in norm_msg_nospace for p in CREATOR_PHRASES):
        save_log("CEVAPLANDI")
        if is_buddy_mode:
            return build_reply('<span class="expert-badge badge-sozel">Sistem Çekirdeği</span><br>Beni tam bir dahi olmam için <b>MİC</b> geliştirdi kanka! Adım <b>ARIES AI</b>. 🚀')
        return build_reply('<span class="expert-badge badge-sozel">Sistem Çekirdeği</span><br>Beni <b>MİC</b> geliştirdi. Adım <b>ARIES AI</b>.')

    if any(fuzzy_word_in(w, GREETING_WORDS) for w in fixed_words):
        save_log("CEVAPLANDI")
        if is_buddy_mode:
            return build_reply("Naber kanka! ARIES AI hazır, ne soruyoruz? 😎")
        return build_reply("Merhaba, ben ARIES AI. Size nasıl yardımcı olabilirim?")

    if any(fuzzy_word_in(w, THANKS_WORDS, cutoff=0.75) for w in fixed_words):
        save_log("CEVAPLANDI")
        if is_buddy_mode:
            return build_reply("Rica ederim kanka, başka bir sorun olursa buradayım! 🙌")
        return build_reply("Rica ederim, başka bir konuda yardımcı olabilirim.")

    if any(p in norm_msg_nospace for p in YOURE_WELCOME_WORDS):
        save_log("CEVAPLANDI")
        return build_reply("Ne demek, her zaman yardımcı olmaktan memnuniyet duyarım. 😊")

    if any(fuzzy_word_in(w, INSULT_WORDS, cutoff=0.85) for w in fixed_words):
        save_log("CEVAPLANDI (SAKINLESTIRME)")
        if is_buddy_mode:
            return build_reply("Sakin ol kanka 😅 Küfür etmene gerek yok, ne sormak istiyorsan yardımcı olurum.")
        return build_reply("Lütfen kibar bir dil kullanalım 🙂 Size nasıl yardımcı olabilirim?")

    if any(p in norm_msg for p in FRUSTRATION_PHRASES):
        save_log("CEVAPLANDI (SAKINLESTIRME)")
        return build_reply("Hayır, dalga geçmiyorum — bazen soruyu tam anlayamayabiliyorum. Sorunu biraz daha farklı bir şekilde yazar mısın?")

    math_source = re.sub(r'[?!;"\'’]', '', raw_message.lower()).replace(",", ".")

    MATH_WORD_OPERATORS = [
        (r'\bkere\b', '*'), (r'\bçarpı\b', '*'), (r'\bcarpi\b', '*'), (r'\bx\b', '*'),
        (r'\bbölü\b', '/'), (r'\bbolu\b', '/'),
        (r'\bartı\b', '+'), (r'\barti\b', '+'), (r'\btopla\b', '+'),
        (r'\beksi\b', '-'), (r'\bçıkar\b', '-'), (r'\bcikar\b', '-'),
    ]
    for _pattern, _symbol in MATH_WORD_OPERATORS:
        math_source = re.sub(_pattern, _symbol, math_source, flags=re.IGNORECASE)
    math_source = re.sub(r'(?<=\d)x(?=\d)', '*', math_source, flags=re.IGNORECASE)
    math_source = re.sub(r'kaç\s*eder|kaçeder|eşittir|kaçtır', '', math_source, flags=re.IGNORECASE).strip()

    MATH_FUNCTION_ALIASES = {
        "karekök": "sqrt", "karekok": "sqrt", "kök": "sqrt", "kok": "sqrt",
        "sinüs": "sin", "sinus": "sin",
        "kosinüs": "cos", "kosinus": "cos",
        "tanjant": "tan",
    }
    math_prepped = math_source
    for tr_name, std_name in MATH_FUNCTION_ALIASES.items():
        math_prepped = math_prepped.replace(tr_name, std_name)
    math_prepped = re.sub(r'(\d+(?:\.\d+)?)\s*%', r'(\1/100)', math_prepped)
    math_prepped = math_prepped.replace('^', '**')

    math_chars = set("0123456789+-*/(). ")
    is_basic_math = any(char in math_source for char in ['+', '-', '*', '/']) and set(math_source).issubset(math_chars)
    is_function_math = bool(re.fullmatch(r'\s*(sqrt|sin|cos|tan)\([^()]*\)\s*', math_prepped))
    has_power_or_percent = ('^' in math_source) or ('%' in math_source)

    if is_basic_math or is_function_math or has_power_or_percent:
        try:
            result = safe_math_eval(math_prepped)
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sayisal">Matematiksel Analiz</span><br><div class="formula-box">{raw_message} = {result}</div>')
        except Exception:
            save_log("HATA")
            if is_buddy_mode:
                return build_reply("İşlem hesaplanamadı kanka, sayılar çok büyük olabilir ya da ifade geçersiz. Kontrol et.")
            return build_reply("İşlem hesaplanamadı. Sayılar çok büyük olabilir ya da ifade geçersiz görünüyor, lütfen kontrol edin.")

    for key, response in science_database.items():
        if key in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sayisal" style="background-color:#00e676; color:black;">Fen Bilimleri & Anatomi</span><br>{response}')

    for key, response in physics_geometry_database.items():
        if key in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sayisal" style="background-color:#ff9100; color:black;">Fizik & Geometri</span><br>{response}')

    for key, response in religious_database.items():
        if key in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sozel" style="background-color:#9c27b0;">İslami Tarih</span><br>{response}')

    for key, response in historical_events.items():
        if key.replace("ı", "i").replace("ğ", "g") in norm_msg:
            save_log("CEVAPLANDI")
            return build_reply(f'<span class="expert-badge badge-sozel">Tarih Bilgisi</span><br>{response}')

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

    conversation_history = session.get('chat_history', [])
    ai_reply = ask_ai_fallback(raw_message, buddy_mode=is_buddy_mode, history=conversation_history)
    if ai_reply:
        save_log("CEVAPLANDI (AI)")
        conversation_history.append({"role": "user", "content": raw_message})
        conversation_history.append({"role": "assistant", "content": ai_reply})
        session['chat_history'] = conversation_history[-10:]
        ai_reply_formatted = format_code_blocks(ai_reply)
        return build_reply(f'<span class="expert-badge badge-sozel" style="background-color:#8e44ad;">Genişletilmiş Zeka</span><br>{ai_reply_formatted}')

    save_log("CEVAPLANAMADI")
    if is_buddy_mode:
        return build_reply("ARIES bu soruyu analiz etti ama tam bir eşleşme bulamadı kanka. Matematik, fen, fizik, geometri, anatomi, tarih veya coğrafya sormayı dene!")
    return build_reply("ARIES bu soruyu analiz etti ancak tam bir eşleşme bulamadı. Matematik, fen bilimleri, fizik, geometri, anatomi, tarih veya coğrafya ile ilgili bir soru sormayı deneyebilirsiniz.")


if __name__ == '__main__':
    app.run(debug=True)
