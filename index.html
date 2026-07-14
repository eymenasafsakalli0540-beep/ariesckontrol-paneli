<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title> Aries Ai Kontrol Paneli </title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #050505; color: #ffffff; font-family: 'Courier New', Courier, monospace; padding: 20px; }
        /* Mobil uyumlu login kutusu */
        .login-box { max-width: 400px; width: 100%; margin: 80px auto; background: #111; padding: 25px; border-radius: 20px; border: 1px solid #333; text-align: center; }
        .dashboard { display: none; }
        .panel-title { font-size: 22px; font-weight: bold; border-bottom: 2px solid #222; padding-bottom: 10px; margin-bottom: 20px; }
        /* Tablet ve PC için başlık boyutunu büyütelim */
        @media (min-width: 768px) {
            body { padding: 30px; }
            .panel-title { font-size: 26px; }
        }
        .neon-text { color: #00ffff; text-shadow: 0 0 5px #00ffff; }

        /* Butonlar tamamen yuvarlak (pill) stil */
        .btn-custom {
            font-weight: bold;
            padding: 12px 24px;
            flex: 1;
            min-width: 180px;
            border-radius: 50px !important;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }
        .btn-custom:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,255,255,0.15); }

        #passInput { border-radius: 50px !important; }
        .btn-outline-info.w-100 { border-radius: 50px !important; }

        .log-display { background-color: #111111; border: 1px solid #222; border-left: 5px solid #ffeb3b; padding: 15px; border-radius: 12px; min-height: 100px; color: #e0e0e0; font-size: 15px; word-wrap: break-word; }

        /* Bakım modu göstergesi */
        .maintenance-banner {
            display: none;
            background: #3a2a00;
            border: 1px solid #ffb300;
            color: #ffb300;
            border-radius: 50px;
            padding: 10px 20px;
            text-align: center;
            font-weight: bold;
            margin-bottom: 15px;
        }
    </style>
</head>
<body>

<div class="container d-flex justify-content-center">
    <div id="loginSection" class="login-box">
        <h4 style="color:#00ffff; font-weight:bold; margin-bottom:15px;">🔒 SİSTEM KİLİTLİ</h4>
        <p style="color:#666; font-size:13px;">ARIES Kayıt Defterine erişmek için yönetim şifresini gir.</p>
        <input type="password" id="passInput" class="form-control text-center bg-black text-info border-secondary mb-3" placeholder="••••">
        <button class="btn btn-outline-info w-100 fw-bold" onclick="login()">GİRİŞ YAP</button>
    </div>
</div>

<div id="dashboardSection" class="container dashboard">
    <div class="panel-title">
        ARIES AI // <span class="neon-text">Aries Ai Kontrol Paneli</span>
    </div>
    <p class="text-secondary" style="font-size:14px; word-break: break-all;">Canlı Sunucu: <span class="text-success">https://aries-a5bl.onrender.com</span></p>
    <p class="text-secondary" style="font-size:13px; margin-top:-10px;">Sorular başarıyla güncellendi! <span id="statusIndicator">🔴</span> <span id="timeText">(00:00:00)</span></p>

    <div id="maintenanceBanner" class="maintenance-banner">🔧 Şu an bakım arasındayız. Aries tamamen durduruldu.</div>

    <div id="testChatBox" style="display:none; background:#111; border:1px solid #00e5ff; border-radius:12px; padding:15px; margin-bottom:20px;">
        <h6 style="color:#00e5ff; font-weight:bold; margin-bottom:10px;">🧪 Test Sohbeti (Bakım Sırasında Geliştirme Kontrolü)</h6>
        <p style="font-size:12px; color:#888; margin-bottom:10px;">Bu kutudan yazdığın mesajlar, bakım modunda olsa bile gerçek ARIES motoruna gider. Diğer kullanıcılar hâlâ bakım mesajı görür.</p>
        <div id="testChatHistory" style="max-height:250px; overflow-y:auto; margin-bottom:10px;"></div>
        <div class="d-flex gap-2">
            <input type="text" id="testChatInput" class="form-control bg-black text-info border-secondary" placeholder="ARIES'e test mesajı yaz..." onkeypress="if(event.key==='Enter') sendTestMessage()">
            <button class="btn btn-info fw-bold" style="border-radius:50px;" onclick="sendTestMessage()">Gönder</button>
        </div>
    </div>

    <div class="d-flex flex-wrap gap-2 mb-4">
        <button class="btn btn-success btn-custom" onclick="getLogs()">Soruları Getir / Yenile 🔄</button>
        <button class="btn btn-danger btn-custom" onclick="clearLogs()">Tüm Soruları Sunucudan Sil 🗑️</button>
        <button id="maintenanceBtn" class="btn btn-warning btn-custom" onclick="toggleMaintenance()">🛠️ Bakım Moduna Al</button>
        <button class="btn btn-secondary btn-custom" onclick="logout()">Çıkış Yap 🚪</button>
    </div>

    <div class="log-display" id="logBox">
        Yükleniyor...
    </div>
</div>

<script>
    const API_URL = 'https://aries-a5bl.onrender.com/api/get-logs';
    const ASK_URL = 'https://aries-a5bl.onrender.com/ask';
    const PASS = '4235';
    let isMaintenance = false;

    function login() {
        if(document.getElementById('passInput').value === PASS) {
            document.getElementById('loginSection').style.display = 'none';
            document.getElementById('dashboardSection').style.display = 'block';
            getLogs();
            checkStatus();
        } else {
            alert('Hatalı şifre girdin.');
        }
    }

    function logout() {
        document.getElementById('passInput').value = '';
        document.getElementById('dashboardSection').style.display = 'none';
        document.getElementById('loginSection').style.display = 'block';
    }

    function getLogs() {
        fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: PASS, action: 'get' })
        })
        .then(res => res.json())
        .then(data => {
            const now = new Date();
            document.getElementById('timeText').innerText = `(${now.toTimeString().split(' ')[0]})`;
            document.getElementById('statusIndicator').innerText = '🟢';

            if (typeof data.maintenance !== 'undefined') {
                updateMaintenanceUI(data.maintenance);
            }

            const box = document.getElementById('logBox');
            box.innerHTML = '';
            if(data.success && data.logs) {
                if(data.logs.length === 0 || data.logs[0].includes("sorulmadı")) {
                    box.innerText = "Defteriniz şu an boş.";
                } else {
                    data.logs.forEach(line => {
                        const div = document.createElement('div');
                        div.style.padding = '5px 0';
                        div.style.borderBottom = '1px solid #1a1a1a';
                        div.innerHTML = line;
                        box.appendChild(div);
                    });
                }
            }
        })
        .catch(err => {
            document.getElementById('logBox').innerText = 'Sunucuyla el sıkışılamadı. Render uykuda olabilir, azıcık bekleyip yenile.';
        });
    }

    // Sunucudaki bakım durumunu ayrıca kontrol etmek istersen (opsiyonel, get ile de gelebilir)
    function checkStatus() {
        fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: PASS, action: 'status' })
        })
        .then(res => res.json())
        .then(data => {
            if (typeof data.maintenance !== 'undefined') {
                updateMaintenanceUI(data.maintenance);
            }
        })
        .catch(() => {});
    }

    function updateMaintenanceUI(state) {
        isMaintenance = state;
        const banner = document.getElementById('maintenanceBanner');
        const btn = document.getElementById('maintenanceBtn');
        const testBox = document.getElementById('testChatBox');
        if (isMaintenance) {
            banner.style.display = 'block';
            testBox.style.display = 'block';
            btn.innerText = '▶️ AI\'yi Aktif Et';
            btn.classList.remove('btn-warning');
            btn.classList.add('btn-info');
        } else {
            banner.style.display = 'none';
            testBox.style.display = 'none';
            btn.innerText = '🛠️ Bakım Moduna Al';
            btn.classList.remove('btn-info');
            btn.classList.add('btn-warning');
        }
    }

    // 🧪 Bakım sırasında test sohbeti — admin şifresiyle bakım bypass edilir
    function sendTestMessage() {
        const input = document.getElementById('testChatInput');
        const message = input.value.trim();
        if (!message) return;

        const history = document.getElementById('testChatHistory');
        const userLine = document.createElement('div');
        userLine.style.cssText = 'color:#00e5ff; margin-bottom:4px;';
        userLine.innerText = '🧑 ' + message;
        history.appendChild(userLine);
        input.value = '';

        fetch(ASK_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message, admin_password: PASS })
        })
        .then(res => res.json())
        .then(data => {
            const botLine = document.createElement('div');
            botLine.style.cssText = 'color:#e0e0e0; margin-bottom:10px; padding-left:15px;';
            botLine.innerHTML = '🤖 ' + (data.reply || '(cevap alınamadı)');
            history.appendChild(botLine);
            history.scrollTop = history.scrollHeight;
        })
        .catch(() => {
            const errLine = document.createElement('div');
            errLine.style.cssText = 'color:#ff5252; margin-bottom:10px;';
            errLine.innerText = '⚠️ Sunucuya ulaşılamadı.';
            history.appendChild(errLine);
        });
    }

    // Tek tuşla bakım moduna al / AI'yi tekrar aktif et
    function toggleMaintenance() {
        const nextAction = isMaintenance ? 'resume' : 'maintenance';
        const confirmMsg = isMaintenance
            ? 'Aries AI\'yi tekrar başlatıp aktif etmek istediğine emin misin?'
            : 'Aries AI\'yi tamamen durdurup bakım moduna almak istediğine emin misin?';
        if (!confirm(confirmMsg)) return;

        fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: PASS, action: nextAction })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                updateMaintenanceUI(nextAction === 'maintenance');
                alert(nextAction === 'maintenance'
                    ? 'Aries AI tamamen durduruldu. Şu an bakım arasındayız.'
                    : 'Aries AI tekrar başlatıldı ve aktif.');
            } else {
                alert('İşlem başarısız: ' + (data.error || 'bilinmeyen hata'));
            }
        })
        .catch(err => alert('Sunucuya ulaşılamadı.'));
    }

    function clearLogs() {
        if(!confirm('Sunucudaki tüm soru geçmişini silmek istediğine emin misin?')) return;
        fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: PASS, action: 'clear' })
        })
        .then(res => res.json())
        .then(data => {
            if(data.success) {
                alert('Tüm veri geçmişi sıfırlandı.');
                getLogs();
            }
        });
    }
</script>
</body>
</html>
