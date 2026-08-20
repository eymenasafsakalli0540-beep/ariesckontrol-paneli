from flask import Flask, render_template, request, jsonify, session, redirect

import ast
import operator
import math
import requests
import os
import re
import html
import json, uuid
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
BANS_FILE = "bans.json"
VISITORS_FILE = "visitors.json"
MAX_VISITORS = 50
ADMIN_PASSWORD = "4275"


def _load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_bans():
    return _load_json_file(BANS_FILE, {})


def save_bans(bans):
    _save_json_file(BANS_FILE, bans)


def _is_ban_active(ban):
    until = ban.get("until")
    if not until:
        return True
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except Exception:
        return True


def _cleanup_bans(bans):
    changed = False
    for key in list(bans.keys()):
        if not _is_ban_active(bans[key]):
            del bans[key]
            changed = True
    if changed:
        save_bans(bans)
    return bans


def get_active_ban(ip=None, device=None):
    bans = _cleanup_bans(load_bans())
    for b in bans.values():
        if b.get("kind") == "ip" and ip and b.get("value") == ip:
            return b
        if b.get("kind") == "device" and device and b.get("value") == device:
            return b
    return None


def load_visitors():
    return _load_json_file(VISITORS_FILE, [])


def save_visitors(visitors):
    _save_json_file(VISITORS_FILE, visitors)


def record_visitor(ip, device, question):
    visitors = load_visitors()
    visitors.append({
        "id": uuid.uuid4().hex,
        "ip": ip,
        "device": device or "",
        "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "question": (question or "")[:200],
    })
    visitors = visitors[-MAX_VISITORS:]
    save_visitors(visitors)


@app.route('/api/banlist', methods=['POST', 'OPTIONS'])
def banlist():
    response_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, response_headers

    data = request.json or {}
    if data.get('password') != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "Hatalı şifre!"}), 403, response_headers

    action = data.get('action', 'list')
    bans = _cleanup_bans(load_bans())

    if action == 'list':
        return jsonify({"success": True, "bans": bans}), 200, response_headers

    if action == 'ban':
        kind = data.get('kind')
        value = (data.get('value') or '').strip()
        reason = (data.get('reason') or '').strip()
        duration = data.get('duration_minutes')

        if kind not in ('ip', 'device') or not value:
            return jsonify({"success": False, "message": "Geçersiz kind/value."}), 400, response_headers

        until = None
        if duration not in (None, ''):
            try:
                until = (datetime.now() + timedelta(minutes=float(duration))).isoformat()
            except Exception:
                until = None

        key = f"{kind}:{value}"
        bans[key] = {
            "kind": kind,
            "value": value,
            "reason": reason,
            "until": until,
            "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        save_bans(bans)
        return jsonify({"success": True, "bans": bans}), 200, response_headers

    if action == 'unban':
        key = data.get('key')
        if key in bans:
            del bans[key]
            save_bans(bans)
            return jsonify({"success": True, "bans": bans}), 200, response_headers
        return jsonify({"success": False, "message": "Ban bulunamadı."}), 404, response_headers

    return jsonify({"success": False, "message": "Bilinmeyen işlem."}), 400, response_headers


@app.route('/api/recent-visitors', methods=['POST', 'OPTIONS'])
def recent_visitors():
    response_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS':
        return jsonify({"success": True}), 200, response_headers

    data = request.json or {}
    if data.get('password') != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "Hatalı şifre!"}), 403, response_headers

    action = data.get('action', 'list')

    if action == 'clear':
        save_visitors([])
        return jsonify({"success": True, "visitors": []}), 200, response_headers

    if action == 'delete':
        visitor_id = data.get('id')
        visitors = load_visitors()
        new_visitors = [v for v in visitors if v.get('id') != visitor_id]
        if len(new_visitors) == len(visitors):
            return jsonify({"success": False, "message": "Ziyaretçi kaydı bulunamadı."}), 404, response_headers
        save_visitors(new_visitors)
        return jsonify({"success": True}), 200, response_headers

    visitors = load_visitors()
    bans = _cleanup_bans(load_bans())
    banned_ips = {b['value'] for b in bans.values() if b.get('kind') == 'ip'}
    banned_devices = {b['value'] for b in bans.values() if b.get('kind') == 'device'}

    out = []
    for v in reversed(visitors):
        v_ip = v.get("ip", "")
        v_device = v.get("device", "")
        out.append({
            "id": v.get("id", ""),
            "ip": v_ip,
            "device": v_device,
            "time": v.get("time", ""),
            "question": v.get("question", ""),
            "is_banned": (v_ip in banned_ips) or (bool(v_device) and v_device in banned_devices),
        })
    return jsonify({"success": True, "visitors": out}), 200, response_headers


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

    # 🧠 GELİŞMİŞ SİSTEM PROMPTU — genel amaçlı asistan + kodlama konusunda
    # daha güçlü/dikkatli davranması için ek talimatlar (AKILLANDIRMA EKLENTİSİ)
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
                    # 🧠 AKILLANDIRMA EKLENTİSİ: uzun kod cevapları yarıda kesilmesin diye
                    # token limiti 800 -> 1500'e çıkarıldı.
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

# 📖 OFİS İÇİ (İNTERNETSİZ) TÜRKÇE-İNGİLİZCE SÖZLÜK
EN_DICTIONARY = {
    'merhaba': 'Hello',
    'gunaydin': 'Good morning',
    'iyi gunler': 'Good day',
    'iyi aksamlar': 'Good evening',
    'iyi geceler': 'Good night',
    'nasilsin': 'How are you?',
    'iyiyim': 'I am fine',
    'fena degilim': 'Not bad',
    'tesekkur ederim': 'Thank you',
    'rica ederim': 'You are welcome',
    'evet': 'Yes',
    'hayir': 'No',
    'lutfen': 'Please',
    'ozur dilerim': 'I am sorry',
    'gule gule': 'Goodbye',
    'gorusuruz': 'See you',
    'adin ne': 'What is your name?',
    'benim adim': 'My name is...',
    'memnun oldum': 'Nice to meet you',
    'kac yasindasin': 'How old are you?',
    'hos geldin': 'Welcome',
    'nerelisin': 'Where are you from?',
    'sifir': 'zero',
    'bir': 'one',
    'iki': 'two',
    'uc': 'three',
    'dort': 'four',
    'bes': 'five',
    'alti': 'six',
    'yedi': 'seven',
    'sekiz': 'eight',
    'dokuz': 'nine',
    'on': 'ten',
    'yirmi': 'twenty',
    'otuz': 'thirty',
    'kirk': 'forty',
    'elli': 'fifty',
    'yuz': 'one hundred',
    'kirmizi': 'red',
    'mavi': 'blue',
    'yesil': 'green',
    'sari': 'yellow',
    'turuncu': 'orange',
    'mor': 'purple',
    'pembe': 'pink',
    'siyah': 'black',
    'beyaz': 'white',
    'kahverengi': 'brown',
    'gri': 'gray',
    'anne': 'mother',
    'baba': 'father',
    'kiz kardes': 'sister',
    'erkek kardes': 'brother',
    'buyukanne': 'grandmother',
    'buyukbaba': 'grandfather',
    'arkadas': 'friend',
    'aile': 'family',
    'okul': 'school',
    'ogretmen': 'teacher',
    'ogrenci': 'student',
    'ders': 'lesson',
    'odev': 'homework',
    'kitap': 'book',
    'defter': 'notebook',
    'silgi': 'eraser',
    'sinif': 'classroom',
    'tahta': 'board',
    'soru': 'question',
    'cevap': 'answer',
    'sinav': 'exam',
    'teneffus': 'break',
    'okumak': 'to read',
    'yazmak': 'to write',
    'dinlemek': 'to listen',
    'konusmak': 'to speak',
    'anlamak': 'to understand',
    'anlamadim': 'I do not understand',
    'anladim': 'I understand',
    'pazartesi': 'Monday',
    'sali': 'Tuesday',
    'carsamba': 'Wednesday',
    'persembe': 'Thursday',
    'cuma': 'Friday',
    'cumartesi': 'Saturday',
    'pazar': 'Sunday',
    'bugun': 'today',
    'yarin': 'tomorrow',
    'dun': 'yesterday',
    'ocak': 'January',
    'subat': 'February',
    'mart': 'March',
    'nisan': 'April',
    'mayis': 'May',
    'haziran': 'June',
    'temmuz': 'July',
    'agustos': 'August',
    'eylul': 'September',
    'ekim': 'October',
    'kasim': 'November',
    'aralik': 'December',
    'ekmek': 'bread',
    'su': 'water',
    'sut': 'milk',
    'elma': 'apple',
    'muz': 'banana',
    'cikolata': 'chocolate',
    'seker': 'candy',
    'yemek': 'food',
    'kahvalti': 'breakfast',
    'ogle yemegi': 'lunch',
    'aksam yemegi': 'dinner',
    'aciktim': 'I am hungry',
    'susadim': 'I am thirsty',
    'kedi': 'cat',
    'kopek': 'dog',
    'kus': 'bird',
    'balik': 'fish',
    'at': 'horse',
    'tavsan': 'rabbit',
    'ayi': 'bear',
    'aslan': 'lion',
    'bas': 'head',
    'el': 'hand',
    'ayak': 'foot',
    'goz': 'eye',
    'kulak': 'ear',
    'agiz': 'mouth',
    'burun': 'nose',
    'sac': 'hair',
    'mutlu': 'happy',
    'uzgun': 'sad',
    'kizgin': 'angry',
    'yorgun': 'tired',
    'iyi': 'good',
    'kotu': 'bad',
    'gitmek': 'to go',
    'gelmek': 'to come',
    'icmek': 'to drink',
    'oynamak': 'to play',
    'kosmak': 'to run',
    'uyumak': 'to sleep',
    'gulmek': 'to laugh',
    'aglamak': 'to cry',
    'bu ne': 'What is this?',
    'bu kim': 'Who is this?',
    'nerede tuvalet': 'Where is the toilet?',
    'yardima ihtiyacim var': 'I need help',
    'harika': 'Great!',
    'tebrikler': 'Congratulations!',
    'gorusmek uzere': 'See you soon',
    'saat kac': 'What time is it?',
    'simdi': 'now',
    'sonra': 'later',
    'erken': 'early',
    'gec': 'late',
    'gunesli': 'sunny',
    'yagmurlu': 'rainy',
    'karli': 'snowy',
    'ruzgarli': 'windy',
    'sicak': 'hot',
    'soguk': 'cold',
    'ne': 'What?',
    'kim': 'Who?',
    'nerede': 'Where?',
    'ne zaman': 'When?',
    'neden': 'Why?',
    'nasil': 'How?',
    'kac tane': 'How many?',
}

# 🌍 COĞRAFYA VERİ TABANI
# ⚠️ DÜZELTME: bu sözlükte kıta bölümleri arasında (Avrupa/Asya/Afrika/Amerika/
# Okyanusya) eksik virgüller ve dictionary'i erken kapatan yanlış yerdeki "}"
# karakterleri vardı. Hepsi tek bir sözlük olacak şekilde birleştirildi.
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
    "slovakya": {"b": "Bratislava", "k": "Avrupa", "lat": 48.14, "lon": 17.10, "bilgi": "Orta Avrupa'da yer alan, kaleleri ve kişi başına düşen otomobil üretimiyle ünlü bir ülkedir."},
    "hirvatistan": {"b": "Zagreb", "k": "Avrupa", "lat": 45.81, "lon": 15.97, "bilgi": "Adriyatik Denizi kıyısında uzun bir sahili ve binlerce adası bulunan bir Balkan ülkesidir."},
    "bosna_hersek": {"b": "Saraybosna", "k": "Avrupa", "lat": 43.85, "lon": 18.41, "bilgi": "Balkanlar'da yer alan, çok kültürlü yapısı ve tarihi Mostar Köprüsü ile tanınan bir ülkedir."},
    "arnavutluk": {"b": "Tiran", "k": "Avrupa", "lat": 41.32, "lon": 19.81, "bilgi": "Balkanlar'ın güneybatısında, Adriyatik kıyısında yer alan dağlık ve tarihi bir ülkedir."},
    "kuzey_makedonya": {"b": "Üsküp", "k": "Avrupa", "lat": 41.99, "lon": 21.43, "bilgi": "Balkanlar'da yer alan, Ohri Gölü ve zengin Osmanlı mirasıyla bilinen bir ülkedir."},
    "slovenya": {"b": "Ljubljana", "k": "Avrupa", "lat": 46.05, "lon": 14.50, "bilgi": "Alpler ile Adriyatik arasında yer alan, yeşil doğası ve Bled Gölü ile ünlü bir ülkedir."},
    "karadag": {"b": "Podgorica", "k": "Avrupa", "lat": 42.43, "lon": 19.26, "bilgi": "Adriyatik kıyısındaki fiyort benzeri Kotor Körfezi ve dik dağlarıyla bilinen bir Balkan ülkesidir."},
    "moldova": {"b": "Kişinev", "k": "Avrupa", "lat": 47.01, "lon": 28.85, "bilgi": "Ukrayna ve Romanya arasında yer alan, şarap bağları ve mahzenleriyle ünlü bir Doğu Avrupa ülkesidir."},
    "belarus": {"b": "Minsk", "k": "Avrupa", "lat": 53.90, "lon": 27.56, "bilgi": "Doğu Avrupa'da denize kıyısı olmayan, geniş ormanları ve Sovyet mimarisiyle bilinen bir ülkedir."},
    "izlanda": {"b": "Reykjavik", "k": "Avrupa", "lat": 64.14, "lon": -21.89, "bilgi": "Kuzey Atlantik'te yer alan, volkanları, gayzerleri ve buzullarıyla ünlü bir ada ülkesidir."},
    "estonya": {"b": "Tallinn", "k": "Avrupa", "lat": 59.43, "lon": 24.75, "bilgi": "Baltık Denizi kıyısında yer alan, gelişmiş dijital altyapısı ve e-devlet uygulamalarıyla tanınan bir ülkedir."},
    "letonya": {"b": "Riga", "k": "Avrupa", "lat": 56.94, "lon": 24.10, "bilgi": "Baltık ülkelerinden biri olan, Art Nouveau mimarisi ve geniş ormanlık alanlarıyla bilinen bir ülkedir."},
    "litvanya": {"b": "Vilnius", "k": "Avrupa", "lat": 54.68, "lon": 25.27, "bilgi": "Baltık bölgesinde yer alan, barok mimarili tarihi eski şehri ve coğrafi merkeziyle ünlü bir ülkedir."},
    "luksemburg": {"b": "Lüksemburg", "k": "Avrupa", "lat": 49.61, "lon": 6.13, "bilgi": "Yüksek yaşam standartları ve güçlü finans sektörüyle bilinen, Avrupa'nın küçük bir dükalığıdır."},
    "malta": {"b": "Valletta", "k": "Avrupa", "lat": 35.89, "lon": 14.51, "bilgi": "Akdeniz'in güneyinde yer alan, tarihi şövalyeleri ve plajlarıyla ünlü küçük bir takımada ülkesidir."},
    "andorra": {"b": "Andorra la Vella", "k": "Avrupa", "lat": 42.50, "lon": 1.52, "bilgi": "Pireneler'de Fransa ve İspanya arasında yer alan, kayak merkezleri ve vergisiz alışverişiyle bilinen bir prensliktir."},
    "lihtenstayn": {"b": "Vaduz", "k": "Avrupa", "lat": 47.14, "lon": 9.52, "bilgi": "Alp Dağları'nda İsviçre ve Avusturya arasında yer alan, dünyanın en küçük mikro devletlerinden biridir."},
    "monako": {"b": "Monako", "k": "Avrupa", "lat": 43.73, "lon": 7.42, "bilgi": "Fransız Rivierası'nda yer alan, lüks yaşamı, kumarhaneleri ve Formula 1 yarışı ile ünlü bir prensliktir."},
    "san_marino": {"b": "San Marino", "k": "Avrupa", "lat": 43.94, "lon": 12.44, "bilgi": "İtalya toprakları ile çevrili, dünyanın en eski cumhuriyetlerinden biri olan tarihi bir mikro devlettir."},
    "vatikan": {"b": "Vatikan", "k": "Avrupa", "lat": 41.90, "lon": 12.45, "bilgi": "Roma şehri içinde yer alan, Katolik kilisesinin yönetim merkezi ve dünyanın en küçük bağımsız devletidir."},

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
    "vietnam": {"b": "Hanoi", "k": "Asya", "lat": 21.03, "lon": 105.83, "bilgi": "Güneydoğu Asya'da yer alan, nehirleri ve zengin tarihiyle tanınan bir ülkedir."},
    "tayland": {"b": "Bangkok", "k": "Asya", "lat": 13.75, "lon": 100.50, "bilgi": "Güneydoğu Asya'da, tropikal plajları ve görkemli kraliyet saraylarıyla ünlü bir ülkedir."},
    "filipinler": {"b": "Manila", "k": "Asya", "lat": 14.60, "lon": 120.98, "bilgi": "Pasifik Okyanusu'nda yer alan, binlerce adadan oluşan bir Güneydoğu Asya ülkesidir."},
    "malezya": {"b": "Kuala Lumpur", "k": "Asya", "lat": 3.14, "lon": 101.69, "bilgi": "Yağmur ormanları ve hareketli başkentindeki ikiz kuleleriyle tanınan bir ülkedir."},
    "singapur": {"b": "Singapur", "k": "Asya", "lat": 1.35, "lon": 103.82, "bilgi": "Güneydoğu Asya'da, küresel bir finans merkezi olan gelişmiş bir ada şehir devletidir."},
    "afganistan": {"b": "Kabil", "k": "Asya", "lat": 34.53, "lon": 69.17, "bilgi": "Güney ve Orta Asya'nın kavşağında yer alan, dağlık ve köklü geçmişe sahip bir ülkedir."},
    "turkmenıstan": {"b": "Aşkabat", "k": "Asya", "lat": 37.96, "lon": 58.38, "bilgi": "Orta Asya'da yer alan, Karakum Çölü ve mermer mimarili başkentiyle tanınan bir ülkedir."},
    "kirgizistan": {"b": "Bişkek", "k": "Asya", "lat": 42.87, "lon": 74.59, "bilgi": "Orta Asya'da yer alan, Tanrı Dağları ve göçebe kültürü mirasıyla bilinen dağlık bir ülkedir."},
    "tacikistan": {"b": "Duşanbe", "k": "Asya", "lat": 38.56, "lon": 68.78, "bilgi": "Orta Asya'da yer alan, Pamir Dağları ile çevrili denize kıyısı olmayan dağlık bir ülkedir."},
    "mogolistan": {"b": "Ulanbatur", "k": "Asya", "lat": 47.88, "lon": 106.89, "bilgi": "Rusya ve Çin arasında yer alan, uçsuz bucaksız bozkırları ve göçebe yaşam tarzıyla ünlü bir ülkedir."},
    "nepal": {"b": "Katmandu", "k": "Asya", "lat": 27.71, "lon": 85.32, "bilgi": "Himalayalar'da yer alan, dünyanın en yüksek zirvesi Everest'e ev sahipliği yapan bir ülkedir."},
    "sri lanka": {"b": "Sri Jayawardenepura Kotte", "k": "Asya", "lat": 6.92, "lon": 79.86, "bilgi": "Hint Okyanusu'nda yer alan, çay tarlaları ve yağmur ormanlarıyla ünlü bir ada ülkesidir."},
    "yemen": {"b": "Sana", "k": "Asya", "lat": 15.35, "lon": 44.20, "bilgi": "Arap Yarımadası'nın güneyinde yer alan, antik mimarisi ve kahve kültürüyle bilinen bir ülkedir."},
    "umman": {"b": "Maskat", "k": "Asya", "lat": 23.58, "lon": 58.40, "bilgi": "Arap Yarımadası'nın güneydoğu kıyısında yer alan, tarihi kaleleri ve çöpleriyle bilinen bir ülkedir."},
    "kuveyt": {"b": "Kuveyt", "k": "Asya", "lat": 29.37, "lon": 47.97, "bilgi": "Basra Körfezi'nde yer alan, modern mimarisi ve zengin petrol yataklarıyla tanınan bir ülkedir."},
    "bahreyn": {"b": "Maname", "k": "Asya", "lat": 26.22, "lon": 50.58, "bilgi": "Basra Körfezi'nde yer alan, küçük adalardan oluşan bir Orta Doğu ülkesidir."},
    "myanmar": {"b": "Naypyidaw", "k": "Asya", "lat": 19.76, "lon": 96.07, "bilgi": "Güneydoğu Asya'da yer alan, altın pagodaları ve Budist kültürüyle tanınan bir ülkedir."},
    "kamboçya": {"b": "Phnom Penh", "k": "Asya", "lat": 11.55, "lon": 104.91, "bilgi": "Güneydoğu Asya'da yer alan, devasa Angkor Wat tapınak kompleksiyle ünlü bir ülkedir."},
    "laos": {"b": "Vientiane", "k": "Asya", "lat": 17.97, "lon": 102.63, "bilgi": "Güneydoğu Asya'da yer alan, denize kıyısı olmayan dağlık ve ormanlık bir ülkedir."},
    "maledivler": {"b": "Male", "k": "Asya", "lat": 4.17, "lon": 73.51, "bilgi": "Hint Okyanusu'nda yer alan, mercan adaları ve lüks su üstü villalarıyla ünlü popüler bir tatil ülkesidir."},
    "bhutan": {"b": "Thimphu", "k": "Asya", "lat": 27.47, "lon": 89.63, "bilgi": "Himalayalar'ın doğusunda yer alan, Budist manastırları ve 'Brüt Ulusal Mutluluk' endeksiyle bilinen krallıktır."},
    "brunei": {"b": "Bandar Seri Begavan", "k": "Asya", "lat": 4.89, "lon": 114.94, "bilgi": "Borneo Adası'nda yer alan, zengin petrol yatakları ve görkemli camileriyle bilinen bir sultanlıktır."},
    "doğu timor": {"b": "Dili", "k": "Asya", "lat": -8.55, "lon": 125.56, "bilgi": "Güneydoğu Asya'da, Timor adasının doğusunda yer alan mercan resifleriyle ünlü bir ada ülkesidir."},

    # Afrika
    "fas": {"b": "Rabat", "k": "Afrika", "lat": 34.02, "lon": -6.84, "bilgi": "Kuzey Afrika'da, Cebelitarık Boğazı'na yakın konumda yer alan bir ülkedir."},
    "cezayir": {"b": "Cezayir", "k": "Afrika", "lat": 36.75, "lon": 3.06, "bilgi": "Kuzey Afrika'da, Akdeniz kıyısında yer alan, yüzölçümü açısından Afrika'nın en büyük ülkesidir."},
    "tunus": {"b": "Tunus", "k": "Afrika", "lat": 36.81, "lon": 10.18, "bilgi": "Kuzey Afrika'da, Akdeniz kıyısında yer alan küçük bir ülkedir."},
    "nijerya": {"b": "Abuja", "k": "Afrika", "lat": 9.08, "lon": 7.40, "bilgi": "Batı Afrika'da yer alan, nüfusu en kalabalık Afrika ülkesidir."},
    "guney afrika": {"b": "Pretoria", "k": "Afrika", "lat": -25.75, "lon": 28.19, "bilgi": "Afrika kıtasının en güneyinde yer alan, üç başkenti olan bir ülkedir."},
    "kenya": {"b": "Nairobi", "k": "Afrika", "lat": -1.29, "lon": 36.82, "bilgi": "Doğu Afrika'da yer alan, safari turizmiyle bilinen bir ülkedir."},
    "etiyopya": {"b": "Addis Ababa", "k": "Afrika", "lat": 9.03, "lon": 38.74, "bilgi": "Doğu Afrika'da yer alan, hiç sömürge olmamış nadir Afrika ülkelerinden biridir."},
    "somali": {"b": "Mogadişu", "k": "Afrika", "lat": 2.04, "lon": 45.34, "bilgi": "Afrika Boynuzu'nda yer alan, kıtanın en uzun kıyı şeridine sahip stratejik bir ülkesidir."},
    "sudan_guney": {"b": "Cuba", "k": "Afrika", "lat": 4.85, "lon": 31.60, "bilgi": "2011 yılında Sudan'dan ayrılarak kurulan, dünyanın en genç bağımsız ülkesidir."},
    "mali": {"b": "Bamako", "k": "Afrika", "lat": 12.63, "lon": -8.00, "bilgi": "Batı Afrika'da yer alan, tarihi Timbuktu şehri ve antik ticaret yollarıyla bilinen bir ülkedir."},
    "nijer": {"b": "Niamey", "k": "Afrika", "lat": 13.51, "lon": 2.11, "bilgi": "Batı Afrika'da denize kıyısı olmayan, büyük bölümü Sahra Çölü ile kaplı bir ülkedir."},
    "cad": {"b": "N'Djamena", "k": "Afrika", "lat": 12.13, "lon": 15.05, "bilgi": "Afrika'nın merkezinde yer alan, adını sınırındaki Çad Gölü'nden alan geniş bir ülkedir."},
    "orta_afrika": {"b": "Bangui", "k": "Afrika", "lat": 4.39, "lon": 18.55, "bilgi": "Kıtanın tam merkezinde yer alan, zengin elmas ve mineral yataklarına sahip bir ülkedir."},
    "kongo_cumhuriyeti": {"b": "Brazzaville", "k": "Afrika", "lat": -4.26, "lon": 15.28, "bilgi": "Orta Afrika'da, Kongo Demokratik Cumhuriyeti'nin komşusu olan ve Kongo Nehri kıyısında yer alan ülkedir."},
    "gabon": {"b": "Libreville", "k": "Afrika", "lat": 0.41, "lon": 9.45, "bilgi": "Batı Afrika kıyısında yer alan, topraklarının büyük kısmı sık yağmur ormanlarıyla kaplı petrol zengini bir ülkedir."},
    "ekvator_ginesi": {"b": "Malabo", "k": "Afrika", "lat": 3.75, "lon": 8.76, "bilgi": "Orta Afrika'da yer alan, anakara ve adalardan oluşan, resmi dili İspanyolca olan tek Afrika ülkesidir."},
    "ruanda": {"b": "Kigali", "k": "Afrika", "lat": -1.94, "lon": 30.06, "bilgi": "Doğu Afrika'da yer alan, dağlık coğrafyası nedeniyle 'Bin Tepeli Ülke' olarak anılan temiz ve düzenli bir ülkedir."},
    "burundi": {"b": "Gitega", "k": "Afrika", "lat": -3.42, "lon": 29.91, "bilgi": "Doğu Afrika'da, Tanganyika Gölü kıyısında yer alan, tepelik ve nüfus yoğunluğu yüksek küçük bir ülkedir."},
    "malavi": {"b": "Lilongwe", "k": "Afrika", "lat": -13.96, "lon": 33.77, "bilgi": "Doğu Afrika'da yer alan, topraklarının büyük kısmını kaplayan devasa Malavi Gölü ile tanınan ülkedir."},
    "lesotho": {"b": "Maseru", "k": "Afrika", "lat": -29.31, "lon": 27.48, "bilgi": "Güney Afrika Cumhuriyeti toprakları tarafından tamamen kuşatılmış, yüksek rakımlı bir dağ krallığıdır."},
    "esvatini": {"b": "Mbabane", "k": "Afrika", "lat": -26.30, "lon": 31.13, "bilgi": "Güney Afrika ve Mozambik arasında yer alan, mutlak monarşiyle yönetilen küçük bir Afrika krallığıdır."},
    "madagaskar": {"b": "Antananarivo", "k": "Afrika", "lat": -18.88, "lon": 47.51, "bilgi": "Hint Okyanusu'nda yer alan, kendine özgü biyoçeşitliliğiyle ünlü dünyanın en büyük ada ülkelerinden biridir."},
    "morityus": {"b": "Port Louis", "k": "Afrika", "lat": -20.16, "lon": 57.50, "bilgi": "Hint Okyanusu'nda yer alan, tropikal plajları, lagünleri ve mercan resifleriyle ünlü turistik bir ada ülkesidir."},
    "seyseller": {"b": "Victoria", "k": "Afrika", "lat": -4.61, "lon": 55.45, "bilgi": "Hint Okyanusu'nda 115 adadan oluşan, Afrika'nın nüfus ve yüzölçümü bakımından en küçük ülkesidir."},
    "komorlar": {"b": "Moroni", "k": "Afrika", "lat": -11.70, "lon": 43.25, "bilgi": "Madagaskar ile Afrika anakarası arasında, Mozambik Kanalı'nda yer alan volkanik bir takımada ülkesidir."},
    "yeşil_burun": {"b": "Praia", "k": "Afrika", "lat": 14.93, "lon": -23.51, "bilgi": "Atlas Okyanusu'nda, Senegal açıklarında yer alan, Portekiz ve Afrika kültürlerinin harmanlandığı bir ada ülkesidir."},
    "sao_tome": {"b": "Sao Tome", "k": "Afrika", "lat": 0.33, "lon": 6.73, "bilgi": "Gine Körfezi'nde, ekvator çizgisi yakınında yer alan, kakao üretimiyle bilinen küçük bir ada ülkesidir."},
    "gine": {"b": "Konakri", "k": "Afrika", "lat": 9.53, "lon": -13.67, "bilgi": "Batı Afrika'da yer alan, dünyadaki boksit (alüminyum cevheri) rezervlerinin büyük kısmına sahip kıyı ülkesidir."},
    "gine_bissau": {"b": "Bissau", "k": "Afrika", "lat": 11.86, "lon": -15.59, "bilgi": "Batı Afrika'da, Atlas Okyanusu kıyısında yer alan, mangrov ormanları ve nehir ağızlarıyla kaplı küçük bir ülkedir."},
    "sierra_leone": {"b": "Freetown", "k": "Afrika", "lat": 8.48, "lon": -13.23, "bilgi": "Batı Afrika'da yer alan, elmas madenleri ve doğal limanıyla bilinen tarihi bir kıyı ülkesidir."},
    "liberya": {"b": "Monrovia", "k": "Afrika", "lat": 6.31, "lon": -10.80, "bilgi": "Batı Afrika'da, azat edilmiş Amerikalı köleler tarafından kurulan Afrika'nın ilk bağımsız cumhuriyetidir."},
    "burkina_faso": {"b": "Vagadugu", "k": "Afrika", "lat": 12.37, "lon": -1.52, "bilgi": "Batı Afrika'da denize kıyısı olmayan, kültürel festivalleri ve sinema sektörüyle tanınan bir ülkedir."},
    "togo": {"b": "Lome", "k": "Afrika", "lat": 6.13, "lon": 1.22, "bilgi": "Batı Afrika'da yer alan, kuzeyden güneye uzanan ince şerit şeklinde palmiye sahilleriyle ünlü bir ülkedir."},
    "benin": {"b": "Porto-Novo", "k": "Afrika", "lat": 6.49, "lon": 2.62, "bilgi": "Batı Afrika'da, eski Dahomey Krallığı'nın topraklarında kurulu, vudu kültürünün doğduğu yer olan ülkedir."},
    "gambiya": {"b": "Banjul", "k": "Afrika", "lat": 13.45, "lon": -16.57, "bilgi": "Gambiya Nehri boyunca uzanan ve Senegal tarafından çevrelenmiş olan Afrika anakarasındaki en küçük ülkedir."},
    "eritre": {"b": "Asmara", "k": "Afrika", "lat": 15.33, "lon": 38.93, "bilgi": "Doğu Afrika'da, Kızıldeniz kıyısında yer alan, İtalyan mimari mirasına sahip dağlık bir ülkedir."},
    "cibuti": {"b": "Cibuti", "k": "Afrika", "lat": 11.58, "lon": 43.14, "bilgi": "Kızıldeniz girişindeki stratejik konumu nedeniyle birçok ülkenin askeri üssüne ev sahipliği yapan küçük ülkedir."},

    # Amerika
    "brezilya": {"b": "Brasilia", "k": "Güney Amerika", "lat": -15.79, "lon": -47.88, "bilgi": "Güney Amerika'da yer alan, Amazon Ormanları'nın büyük kısmını barındıran ülkedir."},
    "arjantin": {"b": "Buenos Aires", "k": "Güney Amerika", "lat": -34.60, "lon": -58.38, "bilgi": "Güney Amerika'da yer alan, tango ve futbolla özdeşleşmiş bir ülkedir."},
    "sili": {"b": "Santiago", "k": "Güney Amerika", "lat": -33.45, "lon": -70.65, "bilgi": "And Dağları boyunca uzanan, ince ve uzun şekliyle bilinen bir Güney Amerika ülkesidir."},
    "meksika": {"b": "Meksiko", "k": "Kuzey Amerika", "lat": 19.43, "lon": -99.13, "bilgi": "Kuzey Amerika'nın güneyinde yer alan, Aztek ve Maya mirasına sahip bir ülkedir."},
    "kanada": {"b": "Ottava", "k": "Kuzey Amerika", "lat": 45.42, "lon": -75.70, "bilgi": "Yüzölçümü bakımından dünyanın en büyük ikinci ülkesidir."},
    "kolombiya": {"b": "Bogota", "k": "Güney Amerika", "lat": 4.71, "lon": -74.07, "bilgi": "Güney Amerika'nın kuzeyinde yer alan, kahve üretimi ve zengin biyolojik çeşitliliğiyle bilinen bir ülkedir."},
    "peru": {"b": "Lima", "k": "Güney Amerika", "lat": -12.04, "lon": -77.03, "bilgi": "İnka İmparatorluğu'nun merkezi olan, dünyaca ünlü Machu Picchu antik kentine ev sahipliği yapan ülkedir."},
    "venezuela": {"b": "Karakas", "k": "Güney Amerika", "lat": 10.48, "lon": -66.90, "bilgi": "Güney Amerika'nın kuzey kıyısında yer alan, dünyanın en büyük kanıtlanmış petrol rezervlerine sahip ülkesidir."},
    "ekvador": {"b": "Quito", "k": "Güney Amerika", "lat": -0.18, "lon": -78.46, "bilgi": "Adını üzerinden geçen ekvator çizgisinden alan, Galapagos Adaları ile ünlü bir Güney Amerika ülkesidir."},
    "bolivya": {"b": "Sucre / La Paz", "k": "Güney Amerika", "lat": -16.50, "lon": -68.11, "bilgi": "And Dağları üzerinde yer alan, dünyanın en büyük tuz çölü Salar de Uyuni'ye sahip bir ülkedir."},
    "paraguay": {"b": "Asuncion", "k": "Güney Amerika", "lat": -25.26, "lon": -57.57, "bilgi": "Güney Amerika'nın merkezinde denize kıyısı olmayan, geniş nehir hatları ve tarım arazileriyle bilinen ülkedir."},
    "uruguay": {"b": "Montevideo", "k": "Güney Amerika", "lat": -34.90, "lon": -56.16, "bilgi": "Yüksek yaşam standartları, plajları ve ilerici sosyal politikalarıyla tanınan küçük bir Güney Amerika ülkesidir."},
    "guyana": {"b": "Georgetown", "k": "Güney Amerika", "lat": 6.80, "lon": -58.15, "bilgi": "Karayip kültürüyle güçlü bağları olan, sık yağmur ormanları ve İngilizce resmi diliyle bilinen Güney Amerika ülkesidir."},
    "surinam": {"b": "Paramaribo", "k": "Güney Amerika", "lat": 5.85, "lon": -55.20, "bilgi": "Yüzölçümü ve nüfus bakımından Güney Amerika'nın en küçük bağımsız ülkesidir, resmi dili Felemenkçedir."},
    "dominik_cumhuriyeti": {"b": "Santo Domingo", "k": "Kuzey Amerika", "lat": 18.48, "lon": -69.93, "bilgi": "Karayipler'deki Hispanyola adasında yer alan, beyaz kumlu plajları ve tatil köyleriyle ünlü turistik bir ülkedir."},
    "haiti": {"b": "Port-au-Prince", "k": "Kuzey Amerika", "lat": 18.53, "lon": -72.33, "bilgi": "Hispanyola adasının batısında yer alan, tarihte köle isyanıyla bağımsızlığını kazanan ilk siyahi cumhuriyettir."},
    "jamaika": {"b": "Kingston", "k": "Kuzey Amerika", "lat": 17.97, "lon": -76.79, "bilgi": "Karayipler'de yer alan, reggae müziğinin doğduğu yer ve dünyaca ünlü atletleriyle tanınan bir ada ülkesidir."},
    "kosta_rika": {"b": "San Jose", "k": "Kuzey Amerika", "lat": 9.92, "lon": -84.08, "bilgi": "Orta Amerika'da ordusu bulunmayan, doğa koruma alanları ve 'Pura Vida' felsefesiyle bilinen bir ülkedir."},
    "panama": {"b": "Panama", "k": "Kuzey Amerika", "lat": 8.98, "lon": -79.51, "bilgi": "Kuzey ve Güney Amerika'yı birleştiren, Atlas ve Büyük Okyanus'u bağlayan kanalıyla ünlü stratejik ülkedir."},
    "guatemala": {"b": "Guatemala", "k": "Kuzey Amerika", "lat": 14.63, "lon": -90.50, "bilgi": "Orta Amerika'da yer alan, zengin Maya kalıntıları, volkanları ve kahve tarlalarıyla tanınan bir ülkedir."},
    "honduras": {"b": "Tegucigalpa", "k": "Kuzey Amerika", "lat": 14.07, "lon": -87.19, "bilgi": "Orta Amerika'da, Karayip Denizi kıyısında yer alan, yağmur ormanları ve muz üretimiyle bilinen bir ülkedir."},
    "nikaragua": {"b": "Managua", "k": "Kuzey Amerika", "lat": 12.11, "lon": -86.23, "bilgi": "Orta Amerika'nın yüzölçümü en büyük ülkesidir, volkanik gölleri ve el değmemiş doğasıyla tanınır."},
    "el_salvador": {"b": "San Salvador", "k": "Kuzey Amerika", "lat": 13.69, "lon": -89.24, "bilgi": "Orta Amerika'da yer alan, yüzölçümü en küçük fakat nüfus yoğunluğu en yüksek olan Pasifik kıyısı ülkesidir."},
    "bahamalar": {"b": "Nassau", "k": "Kuzey Amerika", "lat": 25.04, "lon": -77.34, "bilgi": "Atlas Okyanusu'nda, Florida açıklarında yer alan, yüzlerce mercan adasından oluşan turistik bir ülkedir."},

    # Okyanusya
    "avustralya": {"b": "Kanberra", "k": "Okyanusya", "lat": -35.28, "lon": 149.13, "bilgi": "Hem kıta hem ülke olan, kendine özgü hayvan türleriyle bilinen bir ülkedir."},
    "yeni zelanda": {"b": "Wellington", "k": "Okyanusya", "lat": -41.29, "lon": 174.78, "bilgi": "Pasifik Okyanusu'nda yer alan, doğal manzaralarıyla ünlü bir ada ülkesidir."},
    "papua_yeni_gine": {"b": "Port Moresby", "k": "Okyanusya", "lat": -9.44, "lon": 147.18, "bilgi": "Kültürel ve dilsel çeşitliliğiyle bilinen, yüzlerce yerli kabileye ev sahipliği yapan büyük bir ada ülkesidir."},
    "fiji": {"b": "Suva", "k": "Okyanusya", "lat": -18.12, "lon": 178.45, "bilgi": "Melanezya bölgesinde yer alan, mercan resifleri ve tropikal plajlarıyla ünlü turistik bir ada ülkesidir."},
    "samoa": {"b": "Apia", "k": "Okyanusya", "lat": -13.83, "lon": -171.75, "bilgi": "Polinezya'nın merkezinde yer alan, geleneksel yaşam tarzı ve volkanik adalarıyla tanınan bir ülkedir."},
    "solomon_adalari": {"b": "Honiara", "k": "Okyanusya", "lat": -9.43, "lon": 159.95, "bilgi": "Papua Yeni Gine'nin doğusunda yer alan, dalış noktaları ve II. Dünya Savaşı batıklarıyla ünlü bir takımada ülkesidir."},
    "vanuatu": {"b": "Port Vila", "k": "Okyanusya", "lat": -17.73, "lon": 168.32, "bilgi": "Güney Pasifik'te yer alan, aktif volkanları ve su altı postanesiyle bilinen bir adalar topluluğudur."},
    "tonga": {"b": "Nuku'alofa", "k": "Okyanusya", "lat": -21.14, "lon": -175.20, "bilgi": "Pasifik'te hiçbir zaman sömürge olmamış, günümüzde de monarşiyle yönetilen tek ada krallığıdır."},
    "kiribati": {"b": "Güney Tarawa", "k": "Okyanusya", "lat": 1.46, "lon": 173.03, "bilgi": "Ekvator çizgisi üzerinde yer alan ve küresel ısınma nedeniyle sular altında kalma riski taşıyan bir mercan adaları ülkesidir."},
    "marshall_adalari": {"b": "Majuro", "k": "Okyanusya", "lat": 7.09, "lon": 171.38, "bilgi": "Mikronezya bölgesinde yer alan, ABD ile ilişkili ve geçmişteki nükleer test alanlarıyla bilinen bir ada ülkesidir."},
    "mikronezya": {"b": "Palikir", "k": "Okyanusya", "lat": 6.92, "lon": 158.16, "bilgi": "Batı Pasifik'te yer alan ve yüzlerce küçük adadan oluşan federal bir ada devletidir."},
    "palau": {"b": "Ngerulmud", "k": "Okyanusya", "lat": 7.50, "lon": 134.62, "bilgi": "Deniz biyolojisi açısından dünyanın en zengin bölgelerinden biri olan, koruma altındaki ada ülkesidir."},
    "tuvalu": {"b": "Funafuti", "k": "Okyanusya", "lat": -8.52, "lon": 179.19, "bilgi": "Vatikan'dan sonra dünyanın en az nüfuslu ikinci bağımsız ülkesi olan küçük bir Polinezya ada devletidir."},
    "nauru": {"b": "Yaren", "k": "Okyanusya", "lat": -0.55, "lon": 166.92, "bilgi": "Dünyanın en küçük ada ülkesidir ve resmi olarak belirlenmiş bir başkenti bulunmamaktadır."},
}

# 📜 TARİH VERİ TABANI
# ⚠️ DÜZELTME: "pruet savasi" satırındaki fazladan "}" kaldırıldı ve dict'i
# erken kapatan yanlış "}" silinerek tüm maddeler tek sözlükte birleştirildi.
historical_events = {
    "istanbulun fethi": "<b>1453 - İstanbul'un Fethi:</b> Fatih Sultan Mehmed liderliğindeki Osmanlı ordusu Bizans'ı yıktı. Orta Çağ kapandı, Yeni Çağ başladı.",
    "cumhuriyetin ilani": "<b>29 Ekim 1923 - Cumhuriyetin İlanı:</b> Gazi Mustafa Kemal Atatürk önderliğinde Türkiye Cumhuriyeti resmen kuruldu. 🇹🇷",
    "malazgirt": "<b>1071 - Malazgirt Meydan Muharebesi:</b> Sultan Alparslan komutasındaki Büyük Selçuklu ordusu, Anadolu'nun kapılarını Türklere açtı.",
    "buyuk taarruz": "<b>1922 - Büyük Taarruz:</b> Türk Kurtuluş Savaşı'nın son evresi. Anadolu düşman işgalinden tamamen temizlendi.",

    # 🇹🇷 Osmanlı Tarihi (EKLENTİ)
    "osmanli devletinin kurulusu": "<b>1299 - Osmanlı Devleti'nin Kuruluşu:</b> Osman Bey liderliğinde Söğüt ve çevresinde küçük bir beylik olarak kuruldu, üç kıtaya yayılan bir imparatorluğa dönüştü.",
    "ankara savasi": "<b>1402 - Ankara Savaşı:</b> Osmanlı Padişahı Yıldırım Bayezid, Timur karşısında yenilgiye uğradı; Osmanlı'da Fetret Devri başladı.",
    "istanbul kusatmasi": "<b>1453 - İstanbul Kuşatması:</b> Fatih Sultan Mehmed'in ordusu 53 gün süren kuşatma sonunda şehri fethetti.",
    "preveze deniz savasi": "<b>1538 - Preveze Deniz Savaşı:</b> Barbaros Hayreddin Paşa komutasındaki Osmanlı donanması, Haçlı donanmasını büyük bir zaferle mağlup etti.",
    "kanuni donemi": "<b>1520-1566 - Kanuni Sultan Süleyman Dönemi:</b> Osmanlı Devleti'nin sınırları ve gücü bakımından zirveye ulaştığı dönemdir.",
    "viyana kusatmasi": "<b>1683 - İkinci Viyana Kuşatması:</b> Osmanlı ordusunun başarısız olduğu bu kuşatma, Osmanlı'nın Avrupa'daki gerileme sürecinin başlangıcı sayılır.",
    "karlofca antlasmasi": "<b>1699 - Karlofça Antlaşması:</b> Osmanlı Devleti'nin ilk kez büyük toprak kaybettiği antlaşmadır; gerileme döneminin resmi başlangıcı kabul edilir.",
    "tanzimat fermani": "<b>1839 - Tanzimat Fermanı (Gülhane Hatt-ı Hümayunu):</b> Osmanlı'da hukuk, vergi ve askerlik alanlarında modernleşme reformlarını başlatan fermandır.",
    "islahat fermani": "<b>1856 - Islahat Fermanı:</b> Gayrimüslim Osmanlı tebaasına dinî ve hukuki eşitlik haklarını genişleten fermandır.",
    "birinci mesrutiyet": "<b>1876 - Birinci Meşrutiyet:</b> Osmanlı'da ilk kez anayasal monarşiye geçişi sağlayan Kanun-i Esasi'nin ilanıdır.",
    "ikinci mesrutiyet": "<b>1908 - İkinci Meşrutiyet:</b> Jön Türkler ve İttihat ve Terakki'nin baskısıyla anayasal düzenin yeniden ilan edilmesidir.",
    "istanbulun kusatilmasi ilk": "<b>1391 - İlk Osmanlı İstanbul Kuşatması:</b> Yıldırım Bayezid tarafından şehrin ilk kez ciddi şekilde abluka altına alındığı askeri harekat.",
    "kosova savasi": "<b>1389 - Birinci Kosova Savaşı:</b> Balkanlar'daki Osmanlı hakimiyetini kesinleştiren, I. Murad'ın savaş meydanında şehit düştüğü tarihi zaferdir.",
    "niğbolu savasi": "<b>1396 - Niğbolu Savaşı:</b> Yıldırım Bayezid'in Haçlı ordusunu büyük bir bozguna uğratarak 'Sultan-ı İklim-i Rum' unvanını aldığı savaştır.",
    "ridaniye savasi": "<b>1517 - Ridaniye Savaşı:</b> Yavuz Sultan Selim'in Memlükleri yıkarak halifeliği Osmanlı Hanedanı'na geçirdiği ve Baharat Yolu'nu kontrol altına aldığı savaştır.",
    "inebahti deniz savasi": "<b>1571 - İnebahtı Deniz Savaşı:</b> Osmanlı donanmasının Haçlı ittifakı karşısında aldığı ve Akdeniz'deki yenilmezlik mitini sarsan ilk büyük deniz mağlubiyetidir.",
    "pruet savasi": "<b>1711 - Prut Savaşı:</b> Baltacı Mehmed Paşa komutasındaki Osmanlı ordusunun Rus Çarı I. Petro'yu kuşatarak büyük bir diplomatik ve askeri başarı kazandığı savaştır.",
    "pasarofca antlasmasi": "<b>1718 - Pasarofça Antlaşması:</b> Osmanlı'da Batı'nın üstünlüğünün kabul edildiği ve zevk, sefa, modernleşme dönemi olan Lale Devri'ni başlatan antlaşmadır.",
    "kucuk kaynarca": "<b>1774 - Küçük Kaynarca Antlaşması:</b> Kırım'ın kaybedildiği ve Osmanlı'nın ilk kez Müslüman bir topraktan vazgeçmek zorunda kaldığı ağır antlaşmadır.",
    "yeniceri ocaginin kaldirilishi": "<b>1826 - Vaka-i Hayriye:</b> Sultan II. Mahmud tarafından Yeniçeri Ocağı'nın kaldırılarak yerine modern 'Asakir-i Mansure-i Muhammediye' ordusunun kurulmasıdır.",

    # 🇹🇷 Kurtuluş Savaşı ve Cumhuriyet Dönemi (EKLENTİ)
    "canakkale savasi": "<b>1915 - Çanakkale Savaşı:</b> Osmanlı kuvvetlerinin İtilaf Devletleri donanma ve kara ordularına karşı Çanakkale Boğazı'nı savunduğu ve büyük bir zafer kazandığı savaştır.",
    "samsuna cikis": "<b>19 Mayıs 1919 - Mustafa Kemal'in Samsun'a Çıkışı:</b> Kurtuluş Savaşı'nın manevi başlangıcı kabul edilen tarihtir.",
    "amasya genelgesi": "<b>1919 - Amasya Genelgesi:</b> Milli mücadelenin gerekçesini ve amacını ortaya koyan, 'milletin istiklalini yine milletin azim ve kararı kurtaracaktır' ilkesini benimseyen genelgedir.",
    "erzurum kongresi": "<b>1919 - Erzurum Kongresi:</b> Doğu illerinin kurtuluş mücadelesine katılımını sağlayan, milli sınırlar kavramının ilk kez ortaya konduğu kongredir.",
    "sivas kongresi": "<b>1919 - Sivas Kongresi:</b> Tüm yurttaki direniş cemiyetlerini 'Anadolu ve Rumeli Müdafaa-i Hukuk Cemiyeti' çatısı altında birleştiren ulusal kongredir.",
    "misak-i milli": "<b>1920 - Misak-ı Millî (Ulusal Ant):</b> Osmanlı Mebusan Meclisi'nin kabul ettiği, Türk milletinin bağımsızlık ve sınırlarına dair temel ilkeleri belirleyen belgedir.",
    "tbmm acilisi": "<b>23 Nisan 1920 - TBMM'nin Açılışı:</b> Türkiye Büyük Millet Meclisi'nin Ankara'da açılarak egemenliğin millete ait olduğunu ilan ettiği tarihtir.",
    "sakarya meydan muharebesi": "<b>1921 - Sakarya Meydan Muharebesi:</b> Türk ordusunun Yunan ordusuna karşı kazandığı ve savaşın kaderini değiştiren dönüm noktası niteliğindeki zaferdir.",
    "dumlupinar": "<b>30 Ağustos 1922 - Başkomutanlık Meydan Muharebesi (Dumlupınar):</b> Türk ordusunun Yunan işgaline son veren kesin zaferi; bu tarih Zafer Bayramı olarak kutlanır.",
    "lozan antlasmasi": "<b>1923 - Lozan Antlaşması:</b> Türkiye Cumhuriyeti'nin bağımsızlığını ve sınırlarını uluslararası alanda tanıtan, Kurtuluş Savaşı'nı hukuken sonuçlandıran antlaşmadır.",
    "harf devrimi": "<b>1928 - Harf Devrimi:</b> Arap alfabesinden Latin alfabesine geçişi sağlayan, okuryazarlığı kolaylaştıran köklü bir eğitim reformudur.",
    "ataturkun olumu": "<b>10 Kasım 1938 - Atatürk'ün Vefatı:</b> Türkiye Cumhuriyeti'nin kurucusu Mustafa Kemal Atatürk, Dolmabahçe Sarayı'nda hayatını kaybetti.",
    "kibris baris harekati": "<b>1974 - Kıbrıs Barış Harekâtı:</b> Türkiye'nin, adadaki Türklerin güvenliğini sağlamak amacıyla gerçekleştirdiği askeri harekâttır.",
    "12 eylul darbesi": "<b>1980 - 12 Eylül Darbesi:</b> Türk Silahlı Kuvvetleri'nin yönetime el koyduğu, ülke siyasetinde derin izler bırakan askeri müdahaledir.",
    "trablusgarp savasi": "<b>1911 - Trablusgarp Savaşı:</b> Mustafa Kemal'in İtalyanlara karşı ilk askeri başarısını kazandığı, Osmanlı'nın Afrika'daki son toprağını kaybettiği savaştır.",
    "balkan savaslari": "<b>1912-1913 - Balkan Savaşları:</b> Osmanlı'nın Avrupa'daki topraklarının neredeyse tamamını kaybettiği ve Anadolu'ya büyük göç dalgalarının başladığı trajik süreçtir.",
    "mondros mütarekesi": "<b>30 Ekim 1918 - Mondros Ateşkes Antlaşması:</b> I. Dünya Savaşı sonrasında Osmanlı Devleti'ni fiilen bitiren ve Anadolu'yu işgale açık hale getiren anlaşmadır.",
    "mudanya mütarekesi": "<b>1922 - Mudanya Ateşkes Antlaşması:</b> Kurtuluş Savaşı'nın askeri safhasını bitiren, İstanbul ve Boğazlar'ın savaşsız kurtarılmasını sağlayan diplomatik zaferdir.",
    "saltanatin kaldirilmasi": "<b>1 Kasım 1922 - Saltanatın Kaldırılması:</b> TBMM tarafından alınan kararla Osmanlı saltanatı resmen sonlandırılmış, egemenlik tamamen millete geçmiştir.",
    "hilafetin kaldirilmasi": "<b>3 Mart 1924 - Hilafetin Kaldırılması:</b> Laiklik ve modernleşme yolundaki en büyük adım atılmış, Tevhid-i Tedrisat Kanunu ile eğitim birleştirilmiştir.",
    "medeni kanun": "<b>1926 - Türk Medeni Kanunu'nun Kabulü:</b> İsviçre'den uyarlanan kanunla aile hukukunda kadın-erkek eşitliği sağlanmış, dini hukuk yerine modern hukuk gelmiştir.",
    "kadınlara secme hakki": "<b>5 Aralık 1934 - Kadınlara Seçme ve Seçilme Hakkı:</b> Türk kadınına milletvekili seçme ve seçilme hakkı verilerek birçok Avrupa ülkesinden önce siyasi haklar tanınmıştır. 🇹🇷",
    "hatayin anavatana katilmasi": "<b>1939 - Hatay'ın Anavatana Katılması:</b> Atatürk'ün şahsi meselesi olarak gördüğü diplomasi mücadelesi sonuçlanmış, Hatay Cumhuriyeti kendi kararıyla Türkiye'ye katılmıştır.",
    "cok partili donem": "<b>1946 - Çok Partili Hayata Geçiş:</b> Türkiye'de ilk çok partili genel seçimlerin yapılmasıyla demokratik süreçte yeni bir döneme girilmiştir.",

    # 🌍 Dünya Tarihi (EKLENTİ)
    "roma imparatorlugunun kurulusu": "<b>M.Ö. 27 - Roma İmparatorluğu'nun Kuruluşu:</b> Augustus'un ilk Roma İmparatoru unvanını almasıyla Roma Cumhuriyeti dönemi sona erip İmparatorluk dönemi başladı.",
    "roma imparatorlugunun yikilisi": "<b>M.S. 476 - Batı Roma İmparatorluğu'nun Yıkılışı:</b> Germen kumandanı Odoaker'in son Roma İmparatoru'nu tahttan indirmesiyle Orta Çağ'ın başlangıcı kabul edilir.",
    "hacli seferleri": "<b>1096-1291 - Haçlı Seferleri:</b> Avrupalı Hristiyanların Kudüs ve çevresini ele geçirmek amacıyla düzenlediği, yaklaşık iki asır süren dini-askeri seferler dizisidir.",
    "ronesans": "<b>14-16. yüzyıllar - Rönesans:</b> İtalya'da başlayıp Avrupa'ya yayılan, sanat, bilim ve düşüncede Antik Yunan-Roma değerlerine dönüşü ve yeniden doğuşu ifade eden dönemdir.",
    "reform hareketi": "<b>1517 - Reform Hareketi:</b> Martin Luther'in Katolik Kilisesi'ne karşı başlattığı, Protestanlığın doğuşuna yol açan dini ve toplumsal hareket.",
    "amerika kitasinin kesfi": "<b>1492 - Amerika Kıtası'nın Keşfi:</b> Kristof Kolomb'un İspanya adına yaptığı seferle Avrupalıların Amerika kıtasıyla tanıştığı tarihtir.",
    "fransiz ihtilali": "<b>1789 - Fransız İhtilali:</b> Mutlak monarşiyi yıkarak 'özgürlük, eşitlik, kardeşlik' ilkelerini yayan, modern milliyetçilik ve demokrasi anlayışını derinden etkileyen devrimdir.",
    "amerikan bagimsizlik savasi": "<b>1775-1783 - Amerikan Bağımsızlık Savaşı:</b> On üç İngiliz kolonisinin Büyük Britanya'ya karşı verdiği ve Amerika Birleşik Devletleri'nin kuruluşuyla sonuçlanan savaştır.",
    "sanayi devrimi": "<b>18. yüzyıl sonu - Sanayi Devrimi:</b> İngiltere'de başlayan, buhar gücü ve makineleşmeyle üretim biçimini kökten değiştiren, tarım toplumundan sanayi toplumuna geçişi sağlayan süreçtir.",
    "birinci dunya savasi": "<b>1914-1918 - Birinci Dünya Savaşı:</b> İtilaf ve İttifak Devletleri arasında geçen, milyonlarca insanın hayatını kaybettiği, imparatorlukların yıkılmasına yol açan küresel savaştır.",
    "ikinci dunya savasi": "<b>1939-1945 - İkinci Dünya Savaşı:</b> Mihver ve Müttefik Devletler arasında geçen, tarihin en yıkıcı ve en çok can kaybına yol açan küresel savaşıdır.",
    "sovyetler birliginin dagilmasi": "<b>1991 - Sovyetler Birliği'nin Dağılması:</b> SSCB'nin 15 bağımsız devlete bölünmesiyle Soğuk Savaş döneminin sona ermesidir.",
    "berlin duvarinin yikilisi": "<b>1989 - Berlin Duvarı'nın Yıkılışı:</b> Doğu ve Batı Almanya'yı ayıran duvarın yıkılması, Soğuk Savaş'ın sembolik sonu ve Almanya'nın birleşme sürecinin başlangıcıdır.",
    "aya ilk inis": "<b>1969 - Ay'a İlk İniş:</b> NASA'nın Apollo 11 görevi ile Neil Armstrong ve Buzz Aldrin, insanlık tarihinde ilk kez Ay yüzeyine ayak bastı.",
    "coğrafi kesifler": "<b>15-17. yüzyıl - Coğrafi Keşifler:</b> Avrupalı gemicilerin yeni ticaret yolları ve kıtalar bulmasıyla dünyanın ekonomik dengesini tamamen değiştiren süreçtir.",
    "otuz yil savaslari": "<b>1618-1648 - Otuz Yıl Savaşları:</b> Avrupa'da mezhep odaklı başlayan, Westphalia Antlaşması ile ulus devlet modelinin ve modern diplomasinin temelini atan küresel savaştır.",
    "bolsevik ihtilali": "<b>1917 - Bolşevik İhtilali (Ekim Devrimi):</b> Rusya'da Çarlık rejiminin yıkılarak Lenin liderliğinde dünyadaki ilk sosyalist devletin (SSCB) kurulması sürecidir.",
    "buyuk buhran": "<b>1929 - Büyük Buhran:</b> New York borsasının çökmesiyle başlayan, tüm dünyayı derinden sarsan ve totaliter rejimlerin yükselmesine yol açan ekonomik krizdir.",
    "atom bombası atilmasi": "<b>1945 - Hiroşima ve Nagazaki'ye Atom Bombası Atılması:</b> ABD'nin nükleer silah kullanmasıyla II. Dünya Savaşı sonlanmış, insanlık nükleer çağın dehşetiyle tanışmıştır.",
    "birlesmis milletler": "<b>1945 - Birleşmiş Milletler'in Kuruluşu:</b> II. Dünya Savaşı sonrasında küresel barış ve güvenliği korumak amacıyla kurulan uluslararası organizasyondur.",
    "nato kurulusu": "<b>1949 - NATO'nun Kuruluşu:</b> Sovyet tehdidine karşı Batı Bloku ülkelerinin bir araya gelerek kurduğu askeri ve siyasi savunma ittifakıdır.",
    "internet dogusu": "<b>1969 - ARPANET (İnternetin Temeli):</b> ABD Savunma Bakanlığı bünyesinde ilk bilgisayar ağının kurulmasıyla dijital çağın temelleri atılmıştır.",

    # 🇹🇷 İslamiyet Öncesi ve Erken Türk Tarihi (EKLENTİ)
    "asya hun imparatorlugu": "<b>M.Ö. 220 - Asya Hun İmparatorluğu'nun Kuruluşu:</b> Teoman tarafından kurulan, tarihte bilinen ilk teşkilatlı Türk devletidir.",
    "kavimler gocu": "<b>375 - Kavimler Göçü:</b> Hunların batıya hareketiyle Avrupa'nın etnik ve siyasi yapısını değiştiren, İlk Çağ'ı kapatıp Orta Çağ'ı açan devasa göç dalgasıdır.",
    "gokturk devleti": "<b>552 - Bumin Kağan ve Göktürk Devleti:</b> Türk adını ilk kez resmi devlet ismi olarak kullanan ve Orhun Kitabeleri'ni bırakan köklü Türk imparatorluğudur.",
    "talas savasi": "<b>751 - Talas Savaşı:</b> Abbasiler ve Çinliler arasındaki bu savaş, Türklerin İslamiyet ile kitlesel olarak tanışmasını sağlayan kırılma noktasıdır.",
}

# 🕋 DİNİ TERİMLER VERİ TABANI
# ⚠️ DÜZELTME: dict'i erken kapatan "}" kaldırıldı, tüm maddeler tek sözlükte
# birleştirildi ve gerçek kapanış en sona taşındı.
religious_database = {
    "hicret": "<b>Hicret (622):</b> Hz. Muhammed (s.a.v.) ve Müslümanların Mekke'den Medine'ye göç etmesidir. Hicri takvimin başlangıcıdır.",
    "bedir savasi": "<b>Bedir Savaşı (624):</b> Müslümanlar ile Mekkeli müşrikler arasındaki ilk büyük savaştır. Müslümanlar zafer kazanmıştır.",
    "mekkenin fethi": "<b>Mekke'nin Fethi (630):</b> Hz. Muhammed liderliğindeki İslam ordusu kan dökmeden Mekke'ye girdi.",
    "siyer": "<b>Siyer:</b> Peygamber Efendimiz Hz. Muhammed'in (s.a.v.) hayatını inceleyen bilim dalıdır.",
    "uhud savasi": "<b>Uhud Savaşı (625):</b> Müslümanlar ile Mekkeli müşrikler arasında yaşanan, Müslümanların başlangıçta üstün geldiği ama disiplin hatası sonucu ağır kayıp verdiği savaştır.",
    "hendek savasi": "<b>Hendek Savaşı (627):</b> Medine çevresine kazılan hendeklerle düşman ordusunun şehre girişinin engellendiği savunma savaşıdır.",
    "veda hutbesi": "<b>Veda Hutbesi (632):</b> Hz. Muhammed'in (s.a.v.) hac sırasında verdiği, insan hakları, eşitlik ve kardeşlik ilkelerini vurgulayan son hutbesidir.",
    "miraç": "<b>Miraç Kandili:</b> Hz. Muhammed'in (s.a.v.) İsra ve Miraç mucizesiyle Mescid-i Aksa'ya, oradan da göklere yükseldiğine inanılan gecedir.",
    "kadir gecesi": "<b>Kadir Gecesi:</b> Kur'an-ı Kerim'in indirilmeye başladığına inanılan, Ramazan ayının son on gününde aranan kutsal gecedir.",
    "ramazan orucu": "<b>Ramazan Orucu:</b> İslam'ın beş şartından biri olan, Ramazan ayı boyunca imsaktan iftara kadar yeme, içme ve diğer bazı davranışlardan uzak durmayı içeren ibadettir.",
    "islamin sartlari": "<b>İslam'ın Şartları:</b> Kelime-i şehadet, namaz, oruç, zekât ve hacdan oluşan beş temel ibadettir.",
    "dört halife donemi": "<b>Dört Halife Dönemi (632-661):</b> Hz. Ebubekir, Hz. Ömer, Hz. Osman ve Hz. Ali'nin sırasıyla halifelik yaptığı, İslam'ın hızla yayıldığı dönemdir.",
    "hudeybiye antlasmasi": "<b>Hudeybiye Antlaşması (628):</b> Müslümanlar ile Mekkeli müşrikler arasında yapılan, Müslümanların varlığının hukuken resmen tanındığı barış antlaşmasıdır.",
    "hayberin fethi": "<b>Hayber'in Fethi (629):</b> Şam ticaret yolunun güvenliğini sağlamak amacıyla Yahudilerin elindeki kalelerin Müslümanlar tarafından fethedilmesidir.",
    "mute savasi": "<b>Mute Savaşı (629):</b> Müslümanlar ile Bizans ordusu arasında yapılan ilk büyük savaştır.",
    "tebuk seferi": "<b>Tebük Seferi (631):</b> Hz. Muhammed'in (s.a.v.) Bizans'ın saldırı hazırlığında olduğu istihbaratı üzerine çıktığı son askeri seferdir.",
    "imanin sartlari": "<b>İmanın Şartları:</b> Allah'a, meleklere, kitaplara, peygamberlere, ahiret gününe, kadere ve kazaya inanmaktan oluşan altı inanç esasıdır.",
    "kuranin toplanmasi": "<b>Kur'an'ın Mushaf Haline Getirilmesi:</b> Hz. Ebubekir döneminde, Yemame Savaşı'nda hafızların şehit düşmesi üzerine ayetlerin kitap halinde bir araya getirilmesidir.",
    "kuranin cogaltilmasi": "<b>Kur'an'ın Çoğaltılması:</b> Hz. Osman döneminde, İslam coğrafyasının genişlemesiyle kıraat farklılıklarını önlemek amacıyla ana nüshanın çoğaltılıp merkezlere gönderilmesidir.",
    "hadis": "<b>Hadis:</b> Peygamber Efendimiz Hz. Muhammed'in (s.a.v.) söylediği sözler, yaptığı davranışlar ve onayladığı durumların bütünüdür.",
    "tefsir": "<b>Tefsir:</b> Kur'an-ı Kerim'in ayetlerini nuzül sebeplerine, dil özelliklerine ve tarihi bağlamına göre açıklayan ve yorumlayan bilim dalıdır.",
    "fikih": "<b>Fıkıh:</b> İslam hukuku anlamına gelen, Müslümanların ibadet, muamele ve cezai konularındaki ameli hükümlerini inceleyen bilim dalıdır.",
    "kelam": "<b>Kelam:</b> İslam inanç esaslarını (akaid) akli ve nakli delillerle savunan, açıklayan dini ilim dalıdır.",
    "tasavvuf": "<b>Tasavvuf:</b> İslam'ın kalbi ve ahlaki boyutunu öne çıkaran, nefsi terbiye ederek Allah'a manen yakınlaşmayı amaçlayan düşünce ve yaşam tarzıdır.",
    "mevlit": "<b>Mevlit Kandili:</b> Peygamber Efendimiz Hz. Muhammed'in (s.a.v.) dünyaya geldiği rebiyülevvel ayının on ikinci gecesidir.",
    "regaip": "<b>Regaip Kandili:</b> Üç ayların başlangıcı olan recep ayının ilk perşembeyi cumaya bağlayan mübarek gecesidir.",
    "berat": "<b>Berat Kandili:</b> Şaban ayının on beşinci gecesi olan, günahlardan arınma ve amel defterlerinin yazıldığına inanılan gecedir.",
}

# 🧬 ANATOMİ VE FEN VERİ TABANI
# ⚠️ DÜZELTME: erken kapatan "}" kaldırıldı, tüm maddeler birleştirildi.
science_database = {
    "kalp": "<b>Anatomi - Kalp:</b> Göğüs boşluğunda yer alan, kaslı bir pompadır. Vücuda kan pompalar. Üstte iki kulakçık, altta iki karıncık olmak üzere 4 odacıktan oluşur.",
    "akciyer": "<b>Anatomi - Akciğerler:</b> Solunum sisteminin ana organıdır. Göğüs kafesinde sağ ve sol olmak üzere iki adettir. Kana oksijen sağlar, karbondioksiti dışarı atar.",
    "karaciyer": "<b>Anatomi - Karaciğer:</b> Vücudun en büyük iç organıdır ve adeta bir kimya fabrikası gibi çalışır. Safra üretir, toksinleri temizler ve glikoz depolar.",
    "hucre": "<b>Fen Bilgisi - Hücre:</b> Canlıların canlılık özelliği gösteren en küçük yapı taşıdır. Hücre zarı, sitoplazma ve çekirdek olmak üzere üç temel kısımdan oluşur.",
    "fotosentez": "<b>Fen Bilgisi - Fotosentez:</b> Bitkilerin kloroplast organelinde, güneş ışığı yardımıyla su ve karbondioksiti birleştirerek besin (glikoz) ve oksijen üretmesi olayıdır.",
    "mitokondri": "<b>Fen Bilgisi - Mitokondri:</b> Hücrenin enerji santralidir. Oksijenli solunum yaparak hücre için gerekli olan ATP (enerji) molekülünü üretir.",
    "bobrek": "<b>Anatomi - Böbrekler:</b> Kanı süzerek vücuttaki zararlı atık maddeleri idrar yoluyla dışarı atan, sırt bölgesinde çift olarak bulunan organlardır.",
    "beyin": "<b>Anatomi - Beyin:</b> Sinir sisteminin merkezi olan, düşünme, hafıza, duygular ve vücut hareketlerini kontrol eden organdır. Beyin, beyincik ve omurilik soğanından oluşur.",
    "mide": "<b>Anatomi - Mide:</b> Sindirim sisteminin bir parçası olan, yemek borusundan gelen besinleri asit ve enzimlerle parçalayan torba şeklindeki organdır.",
    "dna": "<b>Fen Bilgisi - DNA:</b> Canlıların genetik bilgisini taşıyan, çift sarmal yapıya sahip nükleik asittir. Kalıtsal özelliklerin nesilden nesile aktarılmasını sağlar.",
    "solunum sistemi": "<b>Fen Bilgisi - Solunum Sistemi:</b> Burun, soluk borusu ve akciğerlerden oluşan, vücuda oksijen alıp karbondioksit veren sistemdir.",
    "dolasim sistemi": "<b>Fen Bilgisi - Dolaşım Sistemi:</b> Kalp, damarlar ve kandan oluşan, besin ve oksijeni vücuda taşıyan sistemdir.",
    "sindirim sistemi": "<b>Fen Bilgisi - Sindirim Sistemi:</b> Ağızdan başlayıp bağırsaklara kadar uzanan, besinlerin parçalanıp vücut tarafından kullanılabilir hale getirildiği sistemdir.",
    "kromozom": "<b>Fen Bilgisi - Kromozom:</b> Hücre çekirdeğinde bulunan, DNA ve proteinden oluşan, genetik bilgiyi taşıyan yapılardır. İnsanda 23 çift kromozom bulunur.",
    "enzim": "<b>Fen Bilgisi - Enzim:</b> Canlı hücrelerde üretilen, kimyasal tepkimeleri hızlandıran özel protein yapılı biyokatalizörlerdir.",
    "pankreas": "<b>Anatomi - Pankreas:</b> Hem sindirim enzimleri üreten hem de insülin ve glukagon gibi kan şekerini düzenleyen hormonları salgılayan karma bir bezdir.",
    "dalak": "<b>Anatomi - Dalak:</b> Karın boşluğunun sol üst kısmında yer alan, eski alyuvarları yok eden, kanı süzen ve bağışıklık sistemine yardımcı olan organdır.",
    "kloroplast": "<b>Fen Bilgisi - Kloroplast:</b> Sadece bitki hücrelerinde bulunan, yeşil rengini veren klorofil pigmentini barındıran ve fotosentezin gerçekleştiği organeldir.",
    "ribozom": "<b>Fen Bilgisi - Ribozom:</b> Tüm canlı hücrelerde ortak olarak bulunan, amino asitleri birleştirerek hücrenin ihtiyacı olan proteinleri sentezleyen organeldir.",
    "rna": "<b>Fen Bilgisi - RNA:</b> Genellikle tek zincirli olan, DNA'daki genetik şifreye göre protein sentezinde doğrudan görev alan nükleik asittir.",
    "mitoz": "<b>Fen Bilgisi - Mitoz Bölünme:</b> Bir hücreden kalıtsal özellikleri ana hücreyle tamamen aynı olan iki yeni hücrenin oluşmasını sağlayan, büyüme ve onarımı gerçekleştiren bölünmedir.",
    "mayoz": "<b>Fen Bilgisi - Mayoz Bölünme:</b> Üreme ana hücrelerinde görülen, kromozom sayısını yarıya indiren ve parça değişimi (krossing-over) ile çeşitlilik sağlayan bölünmedir.",
    "omurilik": "<b>Anatomi - Omurilik:</b> Omurga kanalı içinde yer alan, beyin ile organlar arasındaki bilgi iletimini sağlayan ve refleks hareketlerini kontrol eden sinir sistemi yapısıdır.",
    "deri": "<b>Anatomi - Deri:</b> Vücudun en büyük organı olup dış etkenlerden korur, solunuma ve boşaltıma yardımcı olur, dokunma duyusunu algılar.",
    "goz": "<b>Anatomi - Göz:</b> Işığı odaklayarak görmeyi sağlayan duyu organıdır. Sert tabaka, damar tabaka ve ağ tabaka (retina) olmak üzere üç katmandan oluşur.",
    "kulak": "<b>Anatomi - Kulak:</b> İşitme ve denge organıdır. Dış kulak, orta kulak ve iç kulak olmak üzere üç kısımdan oluşur, iç kulaktaki yarım daire kanalları dengeyi sağlar.",
    "ince_bagirsak": "<b>Anatomi - İnce Bağırsak:</b> Sindirim sisteminde besinlerin kimyasal sindiriminin tamamlandığı ve emilerek kana geçtiği, villus adı verilen kıvrımlara sahip organdır.",
    "kalin_bagirsak": "<b>Anatomi - Kalın Bağırsak:</b> Sindirilmeyen besin atıklarındaki su, vitamin ve minerallerin geri emilimini gerçekleştiren, sindirim sisteminin son kısımlarından biridir.",
    "akyuvar": "<b>Anatomi - Akyuvarlar (Lökositler):</b> Vücudu mikroplara, virüslere ve enfeksiyonlara karşı koruyan, bağışıklık sisteminin temel taşı olan beyaz kan hücreleridir.",
    "alyuvar": "<b>Anatomi - Alyuvarlar (Eritrositler):</b> İçerdiği hemoglobin sayesinde akciğerlerden aldığı oksijeni dokulara, dokulardaki karbondioksiti ise akciğerlere taşıyan kırmızı kan hücreleridir.",
}

# ⚡ FİZİK VE GEOMETRİ VERİ TABANI
# ⚠️ DÜZELTME: erken kapatan "}" kaldırıldı, tüm maddeler birleştirildi ve
# hiç var olmayan gerçek kapanış "}" en sona eklendi.
physics_geometry_database = {
    "yercekimi": "<b>Fizik - Yerçekimi Kuvveti:</b> Kütlesi olan cisimlerin birbirini çekmesidir. Dünyadaki yerçekimi ivmesi yaklaşık olarak $g = 9.81 m/s^2$ kabul edilir. Keşfeden bilim insanı Isaac Newton'dır.",
    "surtunme": "<b>Fizik - Sürtünme Kuvveti:</b> Harekete karşı koyan zorlayıcı kuvvettir. Temas eden yüzeyler arasında oluşur ve kinetik enerjiyi ısı enerjisine dönüştürür.",
    "ohm kanunu": "<b>Fizik - Ohm Kanunu:</b> Bir elektrik devresinde gerilim (V), akım (I) ve direnç (R) arasındaki ilişkiyi açıklar. Formülü: $V = I \\cdot R$ şeklindedir.",
    "ucgen": "<b>Geometri - Üçgen:</b> Üç doğrunun kesişmesiyle oluşan kapalı şekildir. İç açılarının toplamı her zaman **180°**, dış açılarının toplamı ise **360°**'dir.",
    "kare": "<b>Geometri - Kare:</b> Tüm kenarları birbirine eşit ve tüm iç açıları **90°** olan düzgün bir dörtgendir. Alanı bir kenarının karesidir ($A = a^2$).",
    "dikdortgen": "<b>Geometri - Dikdörtgen:</b> Karşılıklı kenarları eşit ve paralel, tüm iç açıları **90°** olan dörtgendir. Çevresi: $2(a+b)$, Alanı: $a \\cdot b$ formülüyle bulunur.",
    "newtonun hareket kanunlari": "<b>Fizik - Newton'un Hareket Kanunları:</b> Eylemsizlik (1. kanun), F=ma (2. kanun) ve etki-tepki (3. kanun) ilkelerinden oluşan, klasik mekaniğin temelini oluşturan kanunlardır.",
    "enerjinin korunumu": "<b>Fizik - Enerjinin Korunumu Kanunu:</b> Enerji yoktan var edilemez, var olan enerji yok edilemez; sadece bir biçimden diğerine dönüşür.",
    "isik kirilmasi": "<b>Fizik - Işığın Kırılması:</b> Işığın bir ortamdan farklı yoğunluktaki başka bir ortama geçerken yön değiştirmesi olayıdır (örneğin suya batırılan bir çubuğun kırık görünmesi).",
    "basinç": "<b>Fizik - Basınç:</b> Birim yüzeye etki eden dik kuvvettir. Formülü: $P = F / A$ şeklindedir, birimi Pascal (Pa)'dır.",
    "daire": "<b>Geometri - Daire:</b> Bir merkez noktadan eşit uzaklıktaki noktaların oluşturduğu düzlemsel şekildir. Alanı $A = \\pi r^2$, çevresi $2\\pi r$ formülüyle bulunur.",
    "pisagor teoremi": "<b>Geometri - Pisagor Teoremi:</b> Dik üçgende hipotenüsün karesi, diğer iki kenarın karelerinin toplamına eşittir: $a^2 + b^2 = c^2$.",
    "hacim": "<b>Geometri - Hacim:</b> Bir cismin uzayda kapladığı yerin ölçüsüdür. Küpün hacmi $V = a^3$, dikdörtgenler prizmasının hacmi $V = a \\cdot b \\cdot c$ formülüyle bulunur.",
    "kaldirma_kuvveti": "<b>Fizik - Kaldırma Kuvveti:</b> Sıvı veya gaz içindeki bir cisme, yerini değiştirdiği akışkanın ağırlığına eşit miktarda uygulanan yukarı yönlü kuvvettir. Arşimet tarafından keşfedilmiştir.",
    "is": "<b>Fizik - İş (W):</b> Bir kuvvete maruz kalan cismin kuvvet doğrultusunda yer değiştirmesidir. Formülü: $W = F \\cdot \\Delta x$ şeklindedir, birimi Joule (J)'dür.",
    "güç": "<b>Fizik - Güç (P):</b> Birim zamanda yapılan iş miktarıdır. Formülü: $P = W / t$ şeklindedir, birimi Watt (W)'tır.",
    "kütle_ve_agirlik": "<b>Fizik - Kütle ve Ağırlık:</b> Kütle (m) değişmeyen madde miktarı olup skalerdir; ağırlık (G) ise kütleye etki eden yerçekimi kuvvetidir ($G=m \\cdot g$) ve vektöreldir.",
    "özgül_kütle": "<b>Fizik - Özgül Kütle (Yoğunluk):</b> Birim hacimdeki madde miktarıdır. Maddeler için ayırt edici bir özelliktir. Formülü: $d = m / v$ şeklindedir.",
    "potansiyel_enerji": "<b>Fizik - Potansiyel Enerji:</b> Cismin konumundan veya durumundan dolayı sahip olduğu enerjidir. Yerçekimi potansiyel enerji formülü: $E_p = m \\cdot g \\cdot h$'tır.",
    "kinetik_enerji": "<b>Fizik - Kinetik Enerji:</b> Hareket halindeki bir cismin hızından dolayı sahip olduğu enerjidir. Formülü: $E_k = \\frac{1}{2} m \\cdot v^2$ şeklindedir.",
    "ses_hizi": "<b>Fizik - Ses Hızı:</b> Ses dalgalarının bir ortamda yayılma hızıdır. Ortamın yoğunluğu arttıkça artar (katılarda en hızlı, gazlarda en yavaştır).",
    "isik_hizi": "<b>Fizik - Işık Hızı:</b> Işığın boşluktaki yayılma hızıdır ve evrensel bir sabittir ($c \\approx 3 \\cdot 10^8 m/s$).",
    "paralelkenar": "<b>Geometri - Paralelkenar:</b> Karşılıklı kenarları eşit ve paralel olan dörtgendir. Karşılıklı açıları eşittir, alanı taban ile o tabana ait yüksekliğin çarpımıdır ($A = a \\cdot h$).",
    "yamuk": "<b>Geometri - Yamuk:</b> En az iki kenarı birbirine paralel olan dörtgendir. Alanı, alt ve üst taban toplamının yarısı ile yüksekliğin çarpımıdır ($A = \\frac{a+c}{2} \\cdot h$).",
    "çember": "<b>Geometri - Çember:</b> Düzlemde sabit bir noktaya eşit uzaklıkta bulunan noktaların oluşturduğu içi boş eğridir. İçi dolu olan daireden farkı sadece çevresinin ($2\\pi r$) olmasıdır.",
    "silindir": "<b>Geometri - Silindir:</b> Alt ve üst tabanı birbirine eş iki daireden oluşan geometrik cisimdir. Hacmi: $V = \\pi r^2 \\cdot h$, yanal alanı: $2\\pi r \\cdot h$ formülüyle bulunur.",
    "koni": "<b>Geometri - Koni:</b> Dairesel bir taban ve bu taban düzleminin dışındaki bir tepe noktasını birleştiren doğruların oluşturduğu cisimdir. Hacmi: $V = \\frac{1}{3} \\pi r^2 \\cdot h$'tır.",
    "küre": "<b>Geometri - Küre:</b> Uzayda sabit bir noktadan eşit uzaklıktaki noktaların oluşturduğu üç boyutlu geometrik şekildir. Hacmi $V = \\frac{4}{3} \\pi r^3$, yüzey alanı $A = 4\\pi r^2$ formülüyle bulunur.",
}

# 👋 SELAMLAŞMA KELİMELERİ (fuzzy eşleşme için)
GREETING_WORDS = ["selam", "merhaba", "naber", "selamlar", "merhabalar", "hey", "hi", "hello", "selaminaleykum", "aleykumselam", "gunaydin", "iyi gunler", "iyi aksamlar"]

# 🙏 TEŞEKKÜR / NEZAKET KELİMELERİ (fuzzy eşleşme için)
THANKS_WORDS = ["tesekkurler", "tesekkur", "sagol", "sagolasin", "eyvallah", "sagolun", "minnettarim", "ellerinesaglik", "harikasin", "cok iyisin", "super", "mukemmel"]

# 😊 "RİCA EDERİM" TÜRÜ KARŞILIK KALIPLARI
YOURE_WELCOME_WORDS = ["ricaederim", "ricaederiz", "birseydegil", "nedemek", "onemlidegil"]

# 🏗️ "KİM YAPTI" SORU KALIPLARI
CREATOR_PHRASES = ["kim yapti", "yapimcin", "kim gelistirdi", "kurucun", "sahibin", "sen kimsin", "adini kim verdi"]

# 😤 ARGO / HAKARET KELİMELERİ (EKLENTİ)
INSULT_WORDS = ["ahmak", "am", "amcık", "amık", "amk", "amq", "ananın", "aptal", "aq", "baba", "başak", "beyinsiz", "dalyarak", "dangalak", "daşşak", "domal", "gaval", "gavat", "geri zekalı", "gerzek", "godoş", "göt", "götelek", "götveren", "ibne", "mal", "oc", "oe", "orospu", "orospu çocuğu", "orospu evladı", "piç", "puşt", "salak", "sik", "sikiş", "sikm", "sikmek", "sikti", "siktir", "sokuş", "sürtük", "taşşak", "yarak", "yarrak"]

# 😤 "DALGA MI GEÇİYORSUN" TÜRÜ SİNİRLİ İFADELER (EKLENTİ)
FRUSTRATION_PHRASES = ["dalga mı geciyon", "dalga geciyorsun", "dalga geciyon musun", "kafa mı buluyorsun"]

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
        with open("sorular.txt", "a", encoding="utf-8") as file:
            file.write(f"[{current_time}] IP: {user_ip} | DURUM: {status_msg} -> Soru: {raw_message}\n")

    device_id = (request.json.get("device_id") or "").strip()
    record_visitor(user_ip, device_id, raw_message)

    active_ban = get_active_ban(ip=user_ip, device=device_id or None)
    if active_ban and not is_admin_test:
        save_log(f"ENGELLENDI (BANLI-{active_ban.get('kind', '').upper()})")
        ban_reason = active_ban.get("reason") or ""
        reply_text = "🚫 Erişimin kısıtlandı, bu IP/cihaz kara listeye alınmış."
        if ban_reason:
            reply_text += f" Sebep: {ban_reason}"
        return jsonify({"reply": reply_text, "banned": True}), 200, cors_headers

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
