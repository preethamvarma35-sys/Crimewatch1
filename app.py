from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response
import sqlite3
from datetime import datetime, timedelta
import os
import urllib.request
import urllib.parse
import json
import hashlib
import secrets
import csv
import io
import math
import time
from functools import wraps
from werkzeug.utils import secure_filename

def get_time_context():
    """Return time-of-day context string used for safety advice."""
    hour = datetime.now().hour
    if 6 <= hour < 12:
        return 'morning'
    elif 12 <= hour < 17:
        return 'afternoon'
    elif 17 <= hour < 21:
        return 'evening'
    else:
        return 'night'

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

DB_PATH = os.environ.get('DB_PATH', 'crimewatch.db')
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── Simple in-memory rate limiter ──────────────────────────────────────────
_rate_limit_store = {}

def rate_limit(key, max_requests=5, window_seconds=60):
    """Returns True if request is allowed, False if rate limited."""
    now = time.time()
    bucket = _rate_limit_store.get(key, [])
    bucket = [t for t in bucket if now - t < window_seconds]
    if len(bucket) >= max_requests:
        return False
    bucket.append(now)
    _rate_limit_store[key] = bucket
    return True

# ─── Helpers ───────────────────────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def geocode_location(location_text):
    try:
        query = urllib.parse.urlencode({'q': location_text, 'format': 'json', 'limit': 1})
        url = f'https://nominatim.openstreetmap.org/search?{query}'
        req = urllib.request.Request(url, headers={'User-Agent': 'CrimeWatch/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return None, None

def log_activity(action, details='', admin_id=None):
    """Log admin activity to the audit log table."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO activity_log (admin_id, action, details, ip_address) VALUES (?,?,?,?)",
            (admin_id or session.get('admin_id'), action, details,
             request.remote_addr if request else None)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def haversine_distance(lat1, lng1, lat2, lng2):
    """Calculate distance in km between two lat/lng points."""
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# ─── Auth decorators ────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        if session.get('admin_role') != 'superadmin':
            flash('Only the super admin can perform this action.', 'error')
            return redirect(url_for('admin'))
        return f(*args, **kwargs)
    return decorated

# ─── DB Init ────────────────────────────────────────────────────────────────

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS crimes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            crime_type TEXT NOT NULL,
            severity TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Reported',
            description TEXT,
            location TEXT NOT NULL,
            lat REAL,
            lng REAL,
            area TEXT,
            reported_by TEXT DEFAULT 'Anonymous',
            reporter_contact TEXT,
            date_occurred TEXT,
            date_reported TEXT DEFAULT CURRENT_TIMESTAMP,
            verified INTEGER DEFAULT 0,
            featured INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            tags TEXT
        );
        CREATE TABLE IF NOT EXISTS crime_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            caption TEXT,
            uploaded_by TEXT DEFAULT 'Anonymous',
            date_uploaded TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (crime_id) REFERENCES crimes(id)
        );
        CREATE TABLE IF NOT EXISTS suspects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER,
            name TEXT,
            alias TEXT,
            age INTEGER,
            gender TEXT,
            description TEXT,
            status TEXT DEFAULT 'At Large',
            FOREIGN KEY (crime_id) REFERENCES crimes(id)
        );
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER,
            type TEXT,
            description TEXT,
            collected_by TEXT,
            date_collected TEXT,
            status TEXT DEFAULT 'Under Review',
            FOREIGN KEY (crime_id) REFERENCES crimes(id)
        );
        CREATE TABLE IF NOT EXISTS tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER,
            content TEXT NOT NULL,
            submitted_by TEXT DEFAULT 'Anonymous',
            date_submitted TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed INTEGER DEFAULT 0,
            FOREIGN KEY (crime_id) REFERENCES crimes(id)
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            area TEXT,
            severity TEXT DEFAULT 'Info',
            date_issued TEXT DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1,
            created_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            email TEXT,
            role TEXT DEFAULT 'admin',
            created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1,
            last_login TEXT
        );

        -- NEW TABLES --

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER NOT NULL,
            author_name TEXT DEFAULT 'Anonymous',
            content TEXT NOT NULL,
            date_posted TEXT DEFAULT CURRENT_TIMESTAMP,
            approved INTEGER DEFAULT 0,
            FOREIGN KEY (crime_id) REFERENCES crimes(id)
        );

        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            areas TEXT,
            crime_types TEXT,
            subscribed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1,
            unsubscribe_token TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS crime_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER NOT NULL,
            field_changed TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT,
            changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (crime_id) REFERENCES crimes(id)
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            logged_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_token TEXT NOT NULL,
            crime_id INTEGER NOT NULL,
            saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (crime_id) REFERENCES crimes(id),
            UNIQUE(session_token, crime_id)
        );

        CREATE TABLE IF NOT EXISTS crime_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crime_id INTEGER NOT NULL,
            session_token TEXT NOT NULL,
            reaction TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (crime_id) REFERENCES crimes(id),
            UNIQUE(crime_id, session_token)
        );
    ''')

    # Migrate existing tables - add new columns if they don't exist
    existing_cols = [row[1] for row in c.execute("PRAGMA table_info(crimes)").fetchall()]
    for col, defn in [
        ('reporter_contact', 'TEXT'),
        ('featured', 'INTEGER DEFAULT 0'),
        ('view_count', 'INTEGER DEFAULT 0'),
        ('tags', 'TEXT'),
    ]:
        if col not in existing_cols:
            c.execute(f"ALTER TABLE crimes ADD COLUMN {col} {defn}")

    admin_cols = [row[1] for row in c.execute("PRAGMA table_info(admins)").fetchall()]
    if 'last_login' not in admin_cols:
        c.execute("ALTER TABLE admins ADD COLUMN last_login TEXT")

    # Create default superadmin if none exists
    existing = c.execute("SELECT COUNT(*) FROM admins WHERE role='superadmin'").fetchone()[0]
    if existing == 0:
        default_pw = os.environ.get('SUPERADMIN_PASSWORD', 'Admin@1234')
        c.execute(
            "INSERT INTO admins (username, password_hash, full_name, role) VALUES (?,?,?,?)",
            ('superadmin', hash_password(default_pw), 'Super Admin', 'superadmin')
        )
        print(f"✅ Default superadmin created: username=superadmin, password={default_pw}")

    conn.commit()
    conn.close()

# ─── Session token for anonymous users ──────────────────────────────────────

def get_session_token():
    if 'visitor_token' not in session:
        session['visitor_token'] = secrets.token_hex(16)
    return session['visitor_token']

# ─── Safety Measure Engine ──────────────────────────────────────────────────

# Master library of safety measures keyed by crime type + context
SAFETY_MEASURES_LIBRARY = {
    'Theft': [
        {'icon':'🔐','title':'Secure Your Valuables','description':'Keep bags, phones and wallets close and in front of your body in crowded areas. Use anti-theft bags.','priority':'HIGH'},
        {'icon':'🚗','title':'Lock Your Vehicle','description':'Never leave items visible in parked cars. Always lock doors and windows, even for short stops.','priority':'HIGH'},
        {'icon':'👁️','title':'Situational Awareness','description':'Stay alert in markets and public transport. Avoid using your phone while walking.','priority':'MEDIUM'},
        {'icon':'💳','title':'Use Digital Payments','description':'Limit cash on hand. Use UPI/card payments to reduce risk of cash theft.','priority':'LOW'},
    ],
    'Robbery': [
        {'icon':'🌃','title':'Avoid Isolated Areas at Night','description':'Stick to well-lit, populated streets. Avoid shortcuts through alleys or empty roads after dark.','priority':'CRITICAL'},
        {'icon':'📱','title':'Share Your Location','description':'Always share real-time location with a trusted contact when travelling at night.','priority':'HIGH'},
        {'icon':'🏃','title':'Trust Your Instincts','description':'If a situation feels wrong, leave immediately. Your gut is a powerful safety tool.','priority':'HIGH'},
        {'icon':'💰','title':'Carry Minimal Cash','description':'If robbed, comply and report later. Valuables can be replaced — your safety cannot.','priority':'MEDIUM'},
    ],
    'Assault': [
        {'icon':'🚫','title':'Avoid Confrontation','description':'Walk away from arguments or aggressive individuals. Report aggressive behaviour to authorities.','priority':'CRITICAL'},
        {'icon':'👥','title':'Travel in Groups','description':'Especially at night or in high-risk areas, move in groups rather than alone.','priority':'HIGH'},
        {'icon':'🆘','title':'Know Escape Routes','description':'When entering any space, identify exits and safe areas you can move to quickly.','priority':'HIGH'},
        {'icon':'📣','title':'Make Noise if Threatened','description':'Shout, use a personal alarm or call attention to yourself if you feel threatened.','priority':'MEDIUM'},
    ],
    'Burglary': [
        {'icon':'🔒','title':'Secure All Entry Points','description':'Install deadbolts, window locks and door reinforcements. Never leave spare keys outside.','priority':'CRITICAL'},
        {'icon':'💡','title':'Use Motion-Sensor Lights','description':'Install exterior lights with motion sensors to deter intruders from approaching your home.','priority':'HIGH'},
        {'icon':'📸','title':'Install CCTV Cameras','description':'Visible cameras at entrances significantly reduce burglary risk. Use cloud storage for footage.','priority':'HIGH'},
        {'icon':'🏘️','title':'Know Your Neighbours','description':'A connected community watches out for each other. Exchange contacts with trusted neighbours.','priority':'MEDIUM'},
        {'icon':'🚪','title':'Never Advertise Absence','description':'Don\'t post travel plans on social media. Use smart plugs to simulate occupancy with lights.','priority':'MEDIUM'},
    ],
    'Vehicle Crime': [
        {'icon':'🔑','title':'Always Double-Lock','description':'Check that your vehicle is locked after every exit. Many modern cars allow doors to unlock accidentally.','priority':'HIGH'},
        {'icon':'🅿️','title':'Park in Monitored Areas','description':'Choose well-lit, CCTV-monitored parking. Avoid basement or remote parking when possible.','priority':'HIGH'},
        {'icon':'🚗','title':'Install a Steering Lock','description':'Visible deterrents like steering locks dramatically reduce vehicle theft attempts.','priority':'MEDIUM'},
        {'icon':'📍','title':'Use a GPS Tracker','description':'Install a hidden GPS tracker to aid recovery if your vehicle is stolen.','priority':'LOW'},
    ],
    'Fraud': [
        {'icon':'📵','title':'Never Share OTPs or Passwords','description':'No bank, government body, or company will ever ask for your OTP. Hang up on such calls.','priority':'CRITICAL'},
        {'icon':'🔍','title':'Verify Before You Pay','description':'Double-check identities of callers, emails and websites before making any payment.','priority':'CRITICAL'},
        {'icon':'🛡️','title':'Use 2-Factor Authentication','description':'Enable 2FA on all important accounts — email, banking, and social media.','priority':'HIGH'},
        {'icon':'📱','title':'Avoid Unknown Links','description':'Do not click links in SMS or WhatsApp from unknown numbers. Go directly to official websites.','priority':'HIGH'},
    ],
    'Cyber Crime': [
        {'icon':'🔒','title':'Use Strong Unique Passwords','description':'Use a password manager. Never reuse passwords. Use at least 12 characters with symbols.','priority':'CRITICAL'},
        {'icon':'🌐','title':'Avoid Public Wi-Fi for Transactions','description':'Never access banking or sensitive accounts over public Wi-Fi. Use your mobile data instead.','priority':'HIGH'},
        {'icon':'🛡️','title':'Keep Software Updated','description':'Enable automatic updates. Most attacks exploit known vulnerabilities in outdated software.','priority':'HIGH'},
        {'icon':'📧','title':'Beware Phishing Emails','description':'Check sender addresses carefully. Legitimate services never ask for passwords via email.','priority':'MEDIUM'},
    ],
    'Kidnapping': [
        {'icon':'📍','title':'Share Real-Time Location','description':'Always share your live location with family when travelling, especially for children.','priority':'CRITICAL'},
        {'icon':'📞','title':'Establish Check-In Times','description':'Set regular check-in times with a trusted person. If you miss one, they should alert authorities.','priority':'CRITICAL'},
        {'icon':'👧','title':'Educate Children on Safety','description':'Teach children never to accept rides or gifts from strangers, and to shout for help.','priority':'HIGH'},
        {'icon':'🚫','title':'Be Cautious of Surveillance','description':'Notice if the same vehicles or people appear repeatedly around your home or workplace.','priority':'MEDIUM'},
    ],
    'Drug Related': [
        {'icon':'👁️','title':'Report Suspicious Activity','description':'Report drug dealing or suspicious gatherings to police anonymously. Do not confront.','priority':'HIGH'},
        {'icon':'🏫','title':'Keep Children Informed','description':'Have honest conversations with children about the dangers of drugs and how to refuse.','priority':'HIGH'},
        {'icon':'🚪','title':'Secure Your Neighbourhood','description':'Advocate for better lighting, CCTV and community policing in affected areas.','priority':'MEDIUM'},
    ],
    'Murder': [
        {'icon':'🚨','title':'Report Threats Immediately','description':'If you or someone you know has received death threats, report to police without delay.','priority':'CRITICAL'},
        {'icon':'🏠','title':'Strengthen Home Security','description':'Install reinforced doors, alarms and CCTV. Have an emergency plan for your household.','priority':'CRITICAL'},
        {'icon':'📱','title':'Emergency Contacts Ready','description':'Keep police and trusted contacts on speed dial. Know the nearest police station location.','priority':'HIGH'},
    ],
    'Harassment': [
        {'icon':'📝','title':'Document Every Incident','description':'Keep records of dates, times, descriptions and any witnesses. This is vital for legal action.','priority':'HIGH'},
        {'icon':'🚨','title':'Report to Authorities','description':'File a police complaint immediately. Harassment is a criminal offence — you have legal protection.','priority':'HIGH'},
        {'icon':'👥','title':'Build a Support Network','description':'Tell trusted people about the situation. You should not face harassment alone.','priority':'MEDIUM'},
        {'icon':'🔕','title':'Block All Contact','description':'Block on all platforms, change routines and avoid being alone in predictable locations.','priority':'MEDIUM'},
    ],
}

# Generic safety measures for all situations
GENERIC_MEASURES = [
    {'icon':'📞','title':'Save Emergency Numbers','description':'Save Police (100), Ambulance (108), Women Helpline (1091) and Cyber Crime (1930) in your phone.','priority':'MEDIUM'},
    {'icon':'🌙','title':'Night Safety Protocol','description':'Plan your return routes in advance after dark. Inform someone of your plans and expected return time.','priority':'MEDIUM'},
    {'icon':'🤝','title':'Community Watch','description':'Join or start a neighbourhood watch group. Coordinated communities experience significantly less crime.','priority':'LOW'},
    {'icon':'📸','title':'Document & Report','description':'If you witness a crime or suspicious activity, document safely from a distance and report to police.','priority':'LOW'},
]

def compute_safety_data(conn):
    """
    Analyse current crime data and compute:
    - Community safety score (0–100)
    - Threat level
    - Tailored safety measures
    - Key statistics
    """
    total = conn.execute("SELECT COUNT(*) FROM crimes").fetchone()[0]
    if total == 0:
        return {
            'community_score': 95,
            'threat_level': 'LOW',
            'grade': 'Excellent',
            'score_color': '#00d68f',
            'score_description': 'Your community has no active crime reports. Stay vigilant and report suspicious activity.',
            'measures': GENERIC_MEASURES[:4],
            'top_crime': None,
            'hotspot': None,
            'resolution_rate': 100,
            'recent_count': 0,
            'dash_array': '282.74',
            'dash_offset': '14',
        }

    # Weighted severity counts
    severity_scores = conn.execute("""
        SELECT
          SUM(CASE severity WHEN 'Critical' THEN 4 WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END) as ws,
          COUNT(*) as total
        FROM crimes
        WHERE date_reported >= date('now', '-60 days')
    """).fetchone()

    recent_count = conn.execute(
        "SELECT COUNT(*) FROM crimes WHERE date_reported >= date('now', '-30 days')"
    ).fetchone()[0]

    closed = conn.execute("SELECT COUNT(*) FROM crimes WHERE status='Closed'").fetchone()[0]
    resolution_rate = round((closed / total * 100) if total else 0, 1)

    critical_active = conn.execute(
        "SELECT COUNT(*) FROM crimes WHERE severity='Critical' AND status NOT IN ('Closed','False Report')"
    ).fetchone()[0]

    # Top crime type
    top_crime_row = conn.execute(
        "SELECT crime_type, COUNT(*) as c FROM crimes GROUP BY crime_type ORDER BY c DESC LIMIT 1"
    ).fetchone()
    top_crime = top_crime_row['crime_type'] if top_crime_row else None

    # Hotspot area
    hotspot_row = conn.execute(
        "SELECT area, COUNT(*) as c FROM crimes WHERE area IS NOT NULL AND area != '' GROUP BY area ORDER BY c DESC LIMIT 1"
    ).fetchone()
    hotspot = hotspot_row['area'] if hotspot_row else None

    # Compute danger score (0–100, higher = more dangerous)
    ws = severity_scores['ws'] or 0
    ws_total = severity_scores['total'] or 1
    avg_severity = ws / ws_total  # 1–4 range

    # Danger components
    d_severity   = min((avg_severity - 1) / 3 * 40, 40)       # 0–40 pts
    d_critical   = min(critical_active * 5, 25)                 # 0–25 pts
    d_recent     = min(recent_count * 1.5, 20)                  # 0–20 pts
    d_unresolved = max(0, (1 - resolution_rate / 100) * 15)    # 0–15 pts

    danger_score = d_severity + d_critical + d_recent + d_unresolved
    community_score = max(5, min(95, round(100 - danger_score)))

    # Threat level
    if community_score >= 75:
        threat_level = 'LOW'
        grade = 'Good'
        score_color = '#00d68f'
        score_description = f'Your community is relatively safe. {recent_count} crimes reported in the last 30 days. Stay alert and report suspicious activity.'
    elif community_score >= 55:
        threat_level = 'MEDIUM'
        grade = 'Fair'
        score_color = '#ffb020'
        score_description = f'Moderate crime activity detected. {critical_active} critical cases active. Take precautions especially at night and in hotspot areas.'
    elif community_score >= 35:
        threat_level = 'HIGH'
        grade = 'Poor'
        score_color = '#ff6b35'
        score_description = f'Elevated crime levels in your area. {critical_active} critical incidents active. Exercise heightened caution and stay in contact with trusted people.'
    else:
        threat_level = 'CRITICAL'
        grade = 'Severe'
        score_color = '#ff3b3b'
        score_description = f'Very high crime activity detected. {critical_active} critical cases active. Avoid unnecessary travel. Keep emergency contacts ready.'

    # Build tailored safety measures
    measures = []
    seen_titles = set()

    # Get top crime types (up to 3)
    top_types = conn.execute(
        "SELECT crime_type FROM crimes WHERE status NOT IN ('Closed','False Report') GROUP BY crime_type ORDER BY COUNT(*) DESC LIMIT 3"
    ).fetchall()

    for row in top_types:
        ct = row['crime_type']
        if ct in SAFETY_MEASURES_LIBRARY:
            for m in SAFETY_MEASURES_LIBRARY[ct]:
                if m['title'] not in seen_titles:
                    measures.append(m)
                    seen_titles.add(m['title'])

    # Add generic measures to fill
    for m in GENERIC_MEASURES:
        if m['title'] not in seen_titles:
            measures.append(m)
            seen_titles.add(m['title'])

    # For critical threat, boost all priorities
    if threat_level == 'CRITICAL':
        for m in measures:
            if m['priority'] == 'LOW':   m = dict(m); m['priority'] = 'MEDIUM'
            if m['priority'] == 'MEDIUM': m = dict(m); m['priority'] = 'HIGH'

    # Sort by priority
    priority_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
    measures.sort(key=lambda x: priority_order.get(x['priority'], 4))

    # SVG ring math (circumference of r=45 circle)
    circumference = 2 * 3.14159 * 45  # ≈ 282.74
    dash_offset = round(circumference - (community_score / 100) * circumference, 2)

    return {
        'community_score': community_score,
        'threat_level': threat_level,
        'grade': grade,
        'score_color': score_color,
        'score_description': score_description,
        'measures': measures[:12],
        'top_crime': top_crime,
        'hotspot': hotspot,
        'resolution_rate': resolution_rate,
        'recent_count': recent_count,
        'dash_array': f'{circumference:.2f}',
        'dash_offset': str(dash_offset),
    }

# ─── Public Routes ──────────────────────────────────────────────────────────

@app.route('/')
def home():
    conn = get_db()

    # Core stats
    stats = {
        'total':         conn.execute("SELECT COUNT(*) FROM crimes").fetchone()[0],
        'active':        conn.execute("SELECT COUNT(*) FROM crimes WHERE status IN ('Active','Under Investigation','Reported')").fetchone()[0],
        'solved':        conn.execute("SELECT COUNT(*) FROM crimes WHERE status='Closed'").fetchone()[0],
        'critical':      conn.execute("SELECT COUNT(*) FROM crimes WHERE severity='Critical'").fetchone()[0],
        'recent_30':     conn.execute("SELECT COUNT(*) FROM crimes WHERE date_reported >= date('now','-30 days')").fetchone()[0],
        'high_severity': conn.execute("SELECT COUNT(*) FROM crimes WHERE severity='High'").fetchone()[0],
    }

    recent     = conn.execute("SELECT * FROM crimes ORDER BY date_reported DESC LIMIT 6").fetchall()
    featured   = conn.execute("SELECT * FROM crimes WHERE featured=1 ORDER BY date_reported DESC LIMIT 3").fetchall()
    alerts     = conn.execute("SELECT * FROM alerts WHERE active=1 ORDER BY date_issued DESC LIMIT 4").fetchall()
    type_stats = conn.execute("SELECT crime_type, COUNT(*) as cnt FROM crimes GROUP BY crime_type ORDER BY cnt DESC").fetchall()
    area_stats = conn.execute("SELECT area, COUNT(*) as cnt FROM crimes WHERE area IS NOT NULL AND area!='' GROUP BY area ORDER BY cnt DESC LIMIT 6").fetchall()

    # Full safety data (score, threat level, tailored measures)
    safety = compute_safety_data(conn)

    # Safety measures per top crime type (for the expandable panel)
    top_types_rows = conn.execute(
        "SELECT crime_type, COUNT(*) as count FROM crimes GROUP BY crime_type ORDER BY count DESC LIMIT 6"
    ).fetchall()

    safety_by_type = []
    safety_data_dict = {}
    for row in top_types_rows:
        ct = row['crime_type']
        proto = SAFETY_MEASURES_LIBRARY.get(ct, {})
        if not proto:
            measures_list = GENERIC_MEASURES[:3]
        else:
            measures_list = proto
        safety_by_type.append({
            'crime_type': ct,
            'count': row['count'],
            'measures': measures_list,
            'priority': measures_list[0]['priority'] if measures_list else 'MEDIUM',
        })
        safety_data_dict[ct] = {
            'measures': measures_list,
            'count': row['count'],
        }

    conn.close()

    # Chart data
    type_chart_data = json.dumps({
        'labels': [t['crime_type'] for t in type_stats[:8]],
        'values': [t['cnt'] for t in type_stats[:8]],
    })
    area_chart_data = json.dumps({
        'labels': [a['area'] or 'Unknown' for a in area_stats[:6]],
        'values': [a['cnt'] for a in area_stats[:6]],
    })

    time_context = get_time_context()

    return render_template('home.html',
        stats=stats,
        recent=recent,
        featured=featured,
        alerts=alerts,
        type_stats=type_stats,
        area_stats=area_stats,
        safety=safety,
        safety_by_type=safety_by_type,
        safety_data_json=json.dumps(safety_data_dict),
        type_chart_data=type_chart_data,
        area_chart_data=area_chart_data,
        time_context=time_context,
    )


@app.route('/api/safety_data')
def api_safety_data():
    """Public JSON endpoint for safety score and measures."""
    conn = get_db()
    safety = compute_safety_data(conn)
    conn.close()
    return jsonify(safety)

@app.route('/map')
def crime_map():
    return render_template('map.html')

@app.route('/crimes')
def crimes_list():
    conn = get_db()
    crime_type = request.args.get('type', '')
    severity   = request.args.get('severity', '')
    area       = request.args.get('area', '')
    search     = request.args.get('search', '')
    status     = request.args.get('status', '')
    sort       = request.args.get('sort', 'newest')
    page       = max(1, int(request.args.get('page', 1)))
    per_page   = 12

    q = "SELECT * FROM crimes WHERE 1=1"
    p = []
    if crime_type: q += " AND crime_type=?";                                   p.append(crime_type)
    if severity:   q += " AND severity=?";                                     p.append(severity)
    if area:       q += " AND area=?";                                         p.append(area)
    if status:     q += " AND status=?";                                       p.append(status)
    if search:     q += " AND (title LIKE ? OR description LIKE ? OR location LIKE ?)"; p += [f'%{search}%']*3

    sort_map = {
        'newest': 'date_reported DESC',
        'oldest': 'date_reported ASC',
        'severity': "CASE severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END",
        'views': 'view_count DESC',
    }
    q += f" ORDER BY {sort_map.get(sort, 'date_reported DESC')}"

    total_count = conn.execute(q.replace("SELECT *", "SELECT COUNT(*)"), p).fetchone()[0]
    total_pages = max(1, math.ceil(total_count / per_page))
    page = min(page, total_pages)
    q += f" LIMIT {per_page} OFFSET {(page-1)*per_page}"

    all_crimes = conn.execute(q, p).fetchall()
    areas      = conn.execute("SELECT DISTINCT area FROM crimes WHERE area IS NOT NULL AND area != '' ORDER BY area").fetchall()
    conn.close()
    return render_template('crimes.html', crimes=all_crimes, areas=areas,
                           filters={'type': crime_type, 'severity': severity, 'area': area,
                                    'search': search, 'status': status, 'sort': sort},
                           page=page, total_pages=total_pages, total_count=total_count)

@app.route('/crimes/<int:cid>')
def crime_detail(cid):
    conn = get_db()
    crime = conn.execute("SELECT * FROM crimes WHERE id=?", (cid,)).fetchone()
    if not crime:
        conn.close()
        return redirect(url_for('crimes_list'))

    # Increment view count
    conn.execute("UPDATE crimes SET view_count = view_count + 1 WHERE id=?", (cid,))
    conn.commit()

    suspects  = conn.execute("SELECT * FROM suspects WHERE crime_id=?", (cid,)).fetchall()
    evidence  = conn.execute("SELECT * FROM evidence WHERE crime_id=?", (cid,)).fetchall()
    tips      = conn.execute("SELECT * FROM tips WHERE crime_id=? AND reviewed=1", (cid,)).fetchall()
    photos    = conn.execute("SELECT * FROM crime_photos WHERE crime_id=? ORDER BY date_uploaded DESC", (cid,)).fetchall()
    nearby    = conn.execute("SELECT * FROM crimes WHERE area=? AND id!=? LIMIT 3", (crime['area'], cid)).fetchall()
    comments  = conn.execute("SELECT * FROM comments WHERE crime_id=? AND approved=1 ORDER BY date_posted ASC", (cid,)).fetchall()
    history   = conn.execute("SELECT * FROM crime_history WHERE crime_id=? ORDER BY changed_at DESC LIMIT 10", (cid,)).fetchall()

    # Reactions count
    reactions = conn.execute(
        "SELECT reaction, COUNT(*) as cnt FROM crime_reactions WHERE crime_id=? GROUP BY reaction",
        (cid,)
    ).fetchall()
    reaction_counts = {r['reaction']: r['cnt'] for r in reactions}

    token = get_session_token()
    user_reaction = conn.execute(
        "SELECT reaction FROM crime_reactions WHERE crime_id=? AND session_token=?",
        (cid, token)
    ).fetchone()
    is_bookmarked = conn.execute(
        "SELECT id FROM bookmarks WHERE crime_id=? AND session_token=?",
        (cid, token)
    ).fetchone()

    conn.close()
    return render_template('crime_detail.html', crime=crime, suspects=suspects,
                           evidence=evidence, tips=tips, photos=photos, nearby=nearby,
                           comments=comments, history=history,
                           reaction_counts=reaction_counts,
                           user_reaction=user_reaction['reaction'] if user_reaction else None,
                           is_bookmarked=bool(is_bookmarked))

@app.route('/report', methods=['GET', 'POST'])
def report_crime():
    if request.method == 'POST':
        # Rate limiting by IP
        ip = request.remote_addr
        if not rate_limit(f"report:{ip}", max_requests=5, window_seconds=3600):
            flash('Too many reports submitted. Please wait before submitting again.', 'error')
            return render_template('report.html')

        location  = request.form['location']
        area      = request.form.get('area', '')
        lat_input = request.form.get('lat', '').strip()
        lng_input = request.form.get('lng', '').strip()
        if lat_input and lng_input:
            try:   lat, lng = float(lat_input), float(lng_input)
            except ValueError: lat, lng = geocode_location(f"{location}, {area}" if area else location)
        else:
            lat, lng = geocode_location(f"{location}, {area}" if area else location)

        tags = request.form.get('tags', '').strip()

        conn = get_db()
        cur = conn.execute(
            "INSERT INTO crimes (title,crime_type,severity,description,location,area,reported_by,reporter_contact,date_occurred,lat,lng,tags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (request.form['title'], request.form['crime_type'], request.form.get('severity','Medium'),
             request.form['description'], location, area,
             request.form.get('reported_by','Anonymous'),
             request.form.get('reporter_contact',''),
             request.form.get('date_occurred',''), lat, lng, tags)
        )
        crime_id = cur.lastrowid

        # Log history entry
        conn.execute(
            "INSERT INTO crime_history (crime_id, field_changed, old_value, new_value, changed_by) VALUES (?,?,?,?,?)",
            (crime_id, 'status', None, 'Reported', request.form.get('reported_by','Anonymous'))
        )

        for photo in request.files.getlist('photos'):
            if photo and photo.filename and allowed_file(photo.filename):
                filename = secure_filename(f"{crime_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{photo.filename}")
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute("INSERT INTO crime_photos (crime_id,filename,caption,uploaded_by) VALUES (?,?,?,?)",
                             (crime_id, filename, photo.filename, request.form.get('reported_by','Anonymous')))
        conn.commit()
        conn.close()
        flash('Crime report submitted successfully! Thank you for helping your community.', 'success')
        return redirect(url_for('crime_detail', cid=crime_id))
    return render_template('report.html')

@app.route('/crimes/<int:cid>/upload_photo', methods=['POST'])
def upload_photo(cid):
    conn = get_db()
    if not conn.execute("SELECT id FROM crimes WHERE id=?", (cid,)).fetchone():
        conn.close(); return jsonify({'success': False, 'message': 'Crime not found'}), 404
    uploaded = []
    for photo in request.files.getlist('photos'):
        if photo and photo.filename and allowed_file(photo.filename):
            filename = secure_filename(f"{cid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{photo.filename}")
            photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            conn.execute("INSERT INTO crime_photos (crime_id,filename,caption,uploaded_by) VALUES (?,?,?,?)",
                         (cid, filename, request.form.get('caption',''), request.form.get('uploaded_by','Anonymous')))
            uploaded.append(filename)
    conn.commit(); conn.close()
    return jsonify({'success': True, 'uploaded': len(uploaded), 'filenames': uploaded,
                    'message': f'{len(uploaded)} photo(s) uploaded successfully!'})

@app.route('/safety')
def safety_tips():
    conn = get_db()
    alerts = conn.execute("SELECT * FROM alerts WHERE active=1 ORDER BY date_issued DESC").fetchall()
    conn.close()
    return render_template('safety.html', alerts=alerts)

# ─── Analytics / Statistics Page ────────────────────────────────────────────

@app.route('/analytics')
def analytics():
    conn = get_db()
    # Monthly trend (last 12 months)
    monthly_trend = conn.execute("""
        SELECT strftime('%Y-%m', date_reported) as month, COUNT(*) as count
        FROM crimes
        WHERE date_reported >= date('now', '-12 months')
        GROUP BY month ORDER BY month
    """).fetchall()

    # By type
    by_type = conn.execute("""
        SELECT crime_type, COUNT(*) as count,
               SUM(CASE WHEN severity='Critical' THEN 1 ELSE 0 END) as critical_count
        FROM crimes GROUP BY crime_type ORDER BY count DESC
    """).fetchall()

    # By area
    by_area = conn.execute("""
        SELECT area, COUNT(*) as count,
               AVG(CASE severity WHEN 'Critical' THEN 4 WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END) as avg_severity
        FROM crimes WHERE area IS NOT NULL AND area != ''
        GROUP BY area ORDER BY count DESC LIMIT 10
    """).fetchall()

    # By day of week
    by_weekday = conn.execute("""
        SELECT strftime('%w', date_occurred) as dow, COUNT(*) as count
        FROM crimes WHERE date_occurred IS NOT NULL AND date_occurred != ''
        GROUP BY dow ORDER BY dow
    """).fetchall()

    # Status breakdown
    by_status = conn.execute("SELECT status, COUNT(*) as count FROM crimes GROUP BY status").fetchall()

    # Resolution rate (closed / total * 100)
    total = conn.execute("SELECT COUNT(*) FROM crimes").fetchone()[0]
    closed = conn.execute("SELECT COUNT(*) FROM crimes WHERE status='Closed'").fetchone()[0]
    resolution_rate = round((closed / total * 100) if total else 0, 1)

    # Most active areas last 30 days
    hot_areas = conn.execute("""
        SELECT area, COUNT(*) as count FROM crimes
        WHERE date_reported >= date('now', '-30 days') AND area IS NOT NULL AND area != ''
        GROUP BY area ORDER BY count DESC LIMIT 5
    """).fetchall()

    conn.close()
    return render_template('analytics.html',
                           monthly_trend=monthly_trend,
                           by_type=by_type,
                           by_area=by_area,
                           by_weekday=by_weekday,
                           by_status=by_status,
                           resolution_rate=resolution_rate,
                           hot_areas=hot_areas,
                           total=total)

# ─── Bookmarks ───────────────────────────────────────────────────────────────

@app.route('/bookmarks')
def bookmarks():
    token = get_session_token()
    conn = get_db()
    saved = conn.execute("""
        SELECT c.* FROM crimes c
        JOIN bookmarks b ON c.id = b.crime_id
        WHERE b.session_token=?
        ORDER BY b.saved_at DESC
    """, (token,)).fetchall()
    conn.close()
    return render_template('bookmarks.html', crimes=saved)

@app.route('/api/bookmark', methods=['POST'])
def toggle_bookmark():
    token = get_session_token()
    data = request.json
    crime_id = data.get('crime_id')
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM bookmarks WHERE session_token=? AND crime_id=?",
        (token, crime_id)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM bookmarks WHERE session_token=? AND crime_id=?", (token, crime_id))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'bookmarked': False, 'message': 'Bookmark removed.'})
    else:
        conn.execute("INSERT INTO bookmarks (session_token, crime_id) VALUES (?,?)", (token, crime_id))
        conn.commit(); conn.close()
        return jsonify({'success': True, 'bookmarked': True, 'message': 'Crime saved to bookmarks!'})

# ─── Reactions ───────────────────────────────────────────────────────────────

@app.route('/api/react', methods=['POST'])
def react_to_crime():
    token = get_session_token()
    data = request.json
    crime_id = data.get('crime_id')
    reaction = data.get('reaction')  # 'alert', 'concerned', 'helpful'
    VALID_REACTIONS = {'alert', 'concerned', 'helpful', 'witnessed'}
    if reaction not in VALID_REACTIONS:
        return jsonify({'success': False, 'message': 'Invalid reaction.'}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT reaction FROM crime_reactions WHERE crime_id=? AND session_token=?",
        (crime_id, token)
    ).fetchone()

    if existing:
        if existing['reaction'] == reaction:
            # Toggle off
            conn.execute("DELETE FROM crime_reactions WHERE crime_id=? AND session_token=?", (crime_id, token))
        else:
            conn.execute("UPDATE crime_reactions SET reaction=? WHERE crime_id=? AND session_token=?",
                         (reaction, crime_id, token))
    else:
        conn.execute("INSERT INTO crime_reactions (crime_id, session_token, reaction) VALUES (?,?,?)",
                     (crime_id, token, reaction))

    conn.commit()
    counts = conn.execute(
        "SELECT reaction, COUNT(*) as cnt FROM crime_reactions WHERE crime_id=? GROUP BY reaction",
        (crime_id,)
    ).fetchall()
    conn.close()
    return jsonify({'success': True, 'counts': {r['reaction']: r['cnt'] for r in counts}})

# ─── Comments ────────────────────────────────────────────────────────────────

@app.route('/api/comment', methods=['POST'])
def submit_comment():
    ip = request.remote_addr
    if not rate_limit(f"comment:{ip}", max_requests=10, window_seconds=3600):
        return jsonify({'success': False, 'message': 'Too many comments. Please slow down.'}), 429

    data = request.json
    crime_id = data.get('crime_id')
    content = (data.get('content') or '').strip()
    author = (data.get('author_name') or 'Anonymous').strip()

    if not content or len(content) < 5:
        return jsonify({'success': False, 'message': 'Comment is too short.'}), 400
    if len(content) > 1000:
        return jsonify({'success': False, 'message': 'Comment is too long (max 1000 chars).'}), 400

    conn = get_db()
    if not conn.execute("SELECT id FROM crimes WHERE id=?", (crime_id,)).fetchone():
        conn.close()
        return jsonify({'success': False, 'message': 'Crime not found.'}), 404
    conn.execute(
        "INSERT INTO comments (crime_id, author_name, content, approved) VALUES (?,?,?,0)",
        (crime_id, author, content)
    )
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Comment submitted for moderation. It will appear after review.'})

# ─── Subscriptions ───────────────────────────────────────────────────────────

@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', '').strip()
        areas = ','.join(request.form.getlist('areas'))
        crime_types = ','.join(request.form.getlist('crime_types'))

        if not email or '@' not in email:
            flash('Please enter a valid email address.', 'error')
            return render_template('subscribe.html')

        token = secrets.token_hex(20)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO subscribers (email, name, areas, crime_types, unsubscribe_token) VALUES (?,?,?,?,?)",
                (email, name, areas, crime_types, token)
            )
            conn.commit()
            flash(f'Subscribed successfully! You\'ll receive alerts for your selected areas.', 'success')
        except sqlite3.IntegrityError:
            flash('This email is already subscribed.', 'error')
        conn.close()
        return redirect(url_for('home'))

    conn = get_db()
    areas = conn.execute("SELECT DISTINCT area FROM crimes WHERE area IS NOT NULL AND area != '' ORDER BY area").fetchall()
    conn.close()
    return render_template('subscribe.html', areas=areas)

@app.route('/unsubscribe/<token>')
def unsubscribe(token):
    conn = get_db()
    conn.execute("UPDATE subscribers SET active=0 WHERE unsubscribe_token=?", (token,))
    conn.commit(); conn.close()
    flash('You have been unsubscribed from crime alerts.', 'success')
    return redirect(url_for('home'))

# ─── Search Autocomplete API ──────────────────────────────────────────────────

@app.route('/api/search_suggest')
def search_suggest():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    conn = get_db()
    results = conn.execute("""
        SELECT id, title, crime_type, area FROM crimes
        WHERE title LIKE ? OR location LIKE ? OR area LIKE ?
        LIMIT 8
    """, (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'title': r['title'],
                     'crime_type': r['crime_type'], 'area': r['area']} for r in results])

# ─── Area Safety Score API ────────────────────────────────────────────────────

@app.route('/api/area_safety')
def area_safety():
    """Returns a safety score (0-100) per area. Higher = safer."""
    conn = get_db()
    areas = conn.execute("""
        SELECT area,
               COUNT(*) as total,
               SUM(CASE severity WHEN 'Critical' THEN 4 WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END) as severity_sum,
               SUM(CASE WHEN status='Closed' THEN 1 ELSE 0 END) as resolved
        FROM crimes
        WHERE area IS NOT NULL AND area != ''
        GROUP BY area
    """).fetchall()
    conn.close()

    scores = []
    for a in areas:
        # Lower score = more dangerous (weighted by recency & severity)
        raw = (a['severity_sum'] / a['total']) * math.log(a['total'] + 1)
        # Normalize to 0-100 safety score (inverted)
        danger = min(raw * 10, 100)
        safety = round(100 - danger, 1)
        scores.append({
            'area': a['area'],
            'total_crimes': a['total'],
            'resolved': a['resolved'],
            'resolution_rate': round(a['resolved'] / a['total'] * 100, 1),
            'safety_score': max(0, safety)
        })

    scores.sort(key=lambda x: x['safety_score'], reverse=True)
    return jsonify(scores)

# ─── Nearby Crimes API ───────────────────────────────────────────────────────

@app.route('/api/nearby')
def nearby_crimes():
    """Return crimes within radius_km of a lat/lng."""
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        radius = float(request.args.get('radius', 2))  # km
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid coordinates'}), 400

    conn = get_db()
    all_crimes = conn.execute(
        "SELECT id, title, crime_type, severity, lat, lng, location, area, status, date_occurred FROM crimes WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ).fetchall()
    conn.close()

    nearby = []
    for c in all_crimes:
        dist = haversine_distance(lat, lng, c['lat'], c['lng'])
        if dist <= radius:
            item = dict(c)
            item['distance_km'] = round(dist, 2)
            nearby.append(item)

    nearby.sort(key=lambda x: x['distance_km'])
    return jsonify(nearby[:20])

# ─── RSS Feed ────────────────────────────────────────────────────────────────

@app.route('/feed.rss')
def rss_feed():
    conn = get_db()
    crimes = conn.execute("SELECT * FROM crimes ORDER BY date_reported DESC LIMIT 20").fetchall()
    alerts = conn.execute("SELECT * FROM alerts WHERE active=1 ORDER BY date_issued DESC LIMIT 5").fetchall()
    conn.close()

    base_url = request.host_url.rstrip('/')
    items = []
    for c in crimes:
        pub_date = c['date_reported'] or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        items.append(f"""
  <item>
    <title>{c['title']} [{c['crime_type']} - {c['severity']}]</title>
    <link>{base_url}/crimes/{c['id']}</link>
    <description><![CDATA[{c['description'] or 'No description'} | Location: {c['location']}]]></description>
    <pubDate>{pub_date}</pubDate>
    <guid>{base_url}/crimes/{c['id']}</guid>
    <category>{c['crime_type']}</category>
  </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>CrimeWatch - Community Safety Alerts</title>
  <link>{base_url}</link>
  <description>Latest crime reports and community safety alerts</description>
  <language>en</language>
  <lastBuildDate>{datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>
  {''.join(items)}
</channel>
</rss>"""

    return Response(rss, mimetype='application/rss+xml')

# ─── Admin Login ────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in session:
        return redirect(url_for('admin'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # Rate limit login attempts
        if not rate_limit(f"login:{request.remote_addr}", max_requests=10, window_seconds=300):
            error = 'Too many login attempts. Please wait 5 minutes.'
            return render_template('admin_login.html', error=error)

        conn = get_db()
        admin = conn.execute(
            "SELECT * FROM admins WHERE username=? AND password_hash=? AND active=1",
            (username, hash_password(password))
        ).fetchone()
        if admin:
            conn.execute("UPDATE admins SET last_login=? WHERE id=?",
                         (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), admin['id']))
            conn.commit()
            conn.close()
            session['admin_id']       = admin['id']
            session['admin_username'] = admin['username']
            session['admin_name']     = admin['full_name'] or admin['username']
            session['admin_role']     = admin['role']
            log_activity('login', f"Admin '{username}' logged in")
            return redirect(url_for('admin'))
        else:
            conn.close()
            error = 'Invalid username or password.'
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    log_activity('logout', f"Admin '{session.get('admin_username')}' logged out")
    session.clear()
    return redirect(url_for('admin_login'))

# ─── Admin Panel ─────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin():
    conn = get_db()
    stats = {
        'total':      conn.execute("SELECT COUNT(*) FROM crimes").fetchone()[0],
        'unverified': conn.execute("SELECT COUNT(*) FROM crimes WHERE verified=0").fetchone()[0],
        'tips':       conn.execute("SELECT COUNT(*) FROM tips WHERE reviewed=0").fetchone()[0],
        'suspects':   conn.execute("SELECT COUNT(*) FROM suspects WHERE status='At Large'").fetchone()[0],
        'comments':   conn.execute("SELECT COUNT(*) FROM comments WHERE approved=0").fetchone()[0],
        'subscribers': conn.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0],
    }
    crimes  = conn.execute("SELECT * FROM crimes ORDER BY date_reported DESC").fetchall()
    tips    = conn.execute("SELECT t.*, c.title as crime_title FROM tips t LEFT JOIN crimes c ON t.crime_id=c.id ORDER BY t.date_submitted DESC").fetchall()
    comments = conn.execute("SELECT cm.*, c.title as crime_title FROM comments cm LEFT JOIN crimes c ON cm.crime_id=c.id ORDER BY cm.date_posted DESC LIMIT 50").fetchall()
    admins  = conn.execute("SELECT * FROM admins ORDER BY created_at DESC").fetchall() if session.get('admin_role') == 'superadmin' else []
    alerts  = conn.execute("SELECT * FROM alerts ORDER BY date_issued DESC").fetchall()
    activity = conn.execute("""
        SELECT al.*, a.username FROM activity_log al
        LEFT JOIN admins a ON al.admin_id=a.id
        ORDER BY logged_at DESC LIMIT 50
    """).fetchall() if session.get('admin_role') == 'superadmin' else []
    conn.close()
    return render_template('admin.html', stats=stats, crimes=crimes, tips=tips,
                           admins=admins, session=session, alerts=alerts,
                           comments=comments, activity=activity)

# ─── Alert CRUD (admin) ───────────────────────────────────────────────────────

@app.route('/admin/create_alert', methods=['POST'])
@login_required
def create_alert():
    title    = request.form.get('title', '').strip()
    message  = request.form.get('message', '').strip()
    area     = request.form.get('area', '').strip()
    severity = request.form.get('severity', 'Info')
    if not title or not message:
        flash('Title and message are required.', 'error')
        return redirect(url_for('admin'))
    conn = get_db()
    conn.execute(
        "INSERT INTO alerts (title, message, area, severity, created_by) VALUES (?,?,?,?,?)",
        (title, message, area, severity, session['admin_id'])
    )
    conn.commit(); conn.close()
    log_activity('create_alert', f"Alert created: {title}")
    flash('Alert published successfully!', 'success')
    return redirect(url_for('admin'))

@app.route('/api/toggle_alert', methods=['POST'])
@login_required
def toggle_alert():
    data = request.json
    alert_id = data.get('alert_id')
    conn = get_db()
    current = conn.execute("SELECT active FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not current:
        conn.close(); return jsonify({'success': False, 'message': 'Alert not found.'})
    new_state = 0 if current['active'] else 1
    conn.execute("UPDATE alerts SET active=? WHERE id=?", (new_state, alert_id))
    conn.commit(); conn.close()
    log_activity('toggle_alert', f"Alert {alert_id} {'activated' if new_state else 'deactivated'}")
    return jsonify({'success': True, 'active': new_state})

@app.route('/api/delete_alert', methods=['POST'])
@login_required
def delete_alert():
    data = request.json
    alert_id = data.get('alert_id')
    conn = get_db()
    conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    conn.commit(); conn.close()
    log_activity('delete_alert', f"Alert {alert_id} deleted")
    return jsonify({'success': True, 'message': 'Alert deleted.'})

# ─── Comment Moderation ───────────────────────────────────────────────────────

@app.route('/api/approve_comment', methods=['POST'])
@login_required
def approve_comment():
    data = request.json
    comment_id = data.get('comment_id')
    conn = get_db()
    conn.execute("UPDATE comments SET approved=1 WHERE id=?", (comment_id,))
    conn.commit(); conn.close()
    log_activity('approve_comment', f"Comment {comment_id} approved")
    return jsonify({'success': True, 'message': 'Comment approved.'})

@app.route('/api/delete_comment', methods=['POST'])
@login_required
def delete_comment():
    data = request.json
    comment_id = data.get('comment_id')
    conn = get_db()
    conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    conn.commit(); conn.close()
    log_activity('delete_comment', f"Comment {comment_id} deleted")
    return jsonify({'success': True, 'message': 'Comment deleted.'})

# ─── Admin Management (superadmin only) ─────────────────────────────────────

@app.route('/admin/add_admin', methods=['POST'])
@superadmin_required
def add_admin():
    username  = request.form.get('username', '').strip()
    password  = request.form.get('password', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email     = request.form.get('email', '').strip()
    role      = request.form.get('role', 'admin')
    if role not in ('admin', 'superadmin'):
        role = 'admin'
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('admin'))
    conn = get_db()
    existing = conn.execute("SELECT id FROM admins WHERE username=?", (username,)).fetchone()
    if existing:
        conn.close()
        flash(f'Username "{username}" already exists.', 'error')
        return redirect(url_for('admin'))
    conn.execute(
        "INSERT INTO admins (username,password_hash,full_name,email,role,created_by) VALUES (?,?,?,?,?,?)",
        (username, hash_password(password), full_name, email, role, session['admin_id'])
    )
    conn.commit(); conn.close()
    log_activity('add_admin', f"Admin '{username}' ({role}) created")
    flash(f'Admin "{username}" created successfully!', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/toggle_admin', methods=['POST'])
@superadmin_required
def toggle_admin():
    data     = request.json
    admin_id = data.get('admin_id')
    if admin_id == session['admin_id']:
        return jsonify({'success': False, 'message': "You can't deactivate yourself."})
    conn = get_db()
    current = conn.execute("SELECT active FROM admins WHERE id=?", (admin_id,)).fetchone()
    if not current:
        conn.close()
        return jsonify({'success': False, 'message': 'Admin not found.'})
    new_state = 0 if current['active'] else 1
    conn.execute("UPDATE admins SET active=? WHERE id=?", (new_state, admin_id))
    conn.commit(); conn.close()
    log_activity('toggle_admin', f"Admin {admin_id} {'activated' if new_state else 'deactivated'}")
    return jsonify({'success': True, 'active': new_state,
                    'message': f'Admin {"activated" if new_state else "deactivated"} successfully.'})

@app.route('/admin/delete_admin', methods=['POST'])
@superadmin_required
def delete_admin():
    data     = request.json
    admin_id = data.get('admin_id')
    if admin_id == session['admin_id']:
        return jsonify({'success': False, 'message': "You can't delete yourself."})
    conn = get_db()
    conn.execute("DELETE FROM admins WHERE id=? AND role != 'superadmin'", (admin_id,))
    conn.commit(); conn.close()
    log_activity('delete_admin', f"Admin {admin_id} deleted")
    return jsonify({'success': True, 'message': 'Admin removed.'})

@app.route('/admin/change_password', methods=['POST'])
@login_required
def change_password():
    current_pw = request.form.get('current_password', '')
    new_pw     = request.form.get('new_password', '')
    if len(new_pw) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('admin'))
    conn = get_db()
    admin = conn.execute(
        "SELECT * FROM admins WHERE id=? AND password_hash=?",
        (session['admin_id'], hash_password(current_pw))
    ).fetchone()
    if not admin:
        conn.close()
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('admin'))
    conn.execute("UPDATE admins SET password_hash=? WHERE id=?",
                 (hash_password(new_pw), session['admin_id']))
    conn.commit(); conn.close()
    log_activity('change_password', "Password changed")
    flash('Password changed successfully!', 'success')
    return redirect(url_for('admin'))

# ─── Crime Management API ────────────────────────────────────────────────────

@app.route('/api/crimes')
def api_crimes():
    conn = get_db()
    crimes = conn.execute("SELECT * FROM crimes").fetchall()
    conn.close()
    return jsonify([dict(c) for c in crimes])

@app.route('/api/submit_tip', methods=['POST'])
def submit_tip():
    ip = request.remote_addr
    if not rate_limit(f"tip:{ip}", max_requests=10, window_seconds=3600):
        return jsonify({'success': False, 'message': 'Too many tips submitted.'}), 429

    data = request.json
    conn = get_db()
    conn.execute("INSERT INTO tips (crime_id,content,submitted_by) VALUES (?,?,?)",
                 (data['crime_id'], data['content'], data.get('submitted_by','Anonymous')))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Tip submitted. Thank you for helping your community!'})

@app.route('/api/verify_crime', methods=['POST'])
@login_required
def verify_crime():
    data = request.json
    crime_id = data['crime_id']
    conn = get_db()
    old = conn.execute("SELECT status FROM crimes WHERE id=?", (crime_id,)).fetchone()
    conn.execute("UPDATE crimes SET verified=1, status='Under Investigation' WHERE id=?", (crime_id,))
    conn.execute(
        "INSERT INTO crime_history (crime_id, field_changed, old_value, new_value, changed_by) VALUES (?,?,?,?,?)",
        (crime_id, 'status', old['status'] if old else None, 'Under Investigation', session.get('admin_username'))
    )
    conn.commit(); conn.close()
    log_activity('verify_crime', f"Crime {crime_id} verified")
    return jsonify({'success': True, 'message': 'Crime verified and set to Under Investigation.'})

@app.route('/api/update_status', methods=['POST'])
@login_required
def update_status():
    data = request.json
    crime_id = data.get('crime_id')
    new_status = data.get('status')
    VALID_STATUSES = ['Reported', 'Under Investigation', 'Active', 'Closed', 'Cold Case', 'False Report']
    if new_status not in VALID_STATUSES:
        return jsonify({'success': False, 'message': 'Invalid status.'}), 400
    conn = get_db()
    old = conn.execute("SELECT status FROM crimes WHERE id=?", (crime_id,)).fetchone()
    conn.execute("UPDATE crimes SET status=? WHERE id=?", (new_status, crime_id))
    conn.execute(
        "INSERT INTO crime_history (crime_id, field_changed, old_value, new_value, changed_by) VALUES (?,?,?,?,?)",
        (crime_id, 'status', old['status'] if old else None, new_status, session.get('admin_username'))
    )
    conn.commit(); conn.close()
    log_activity('update_status', f"Crime {crime_id} status changed to {new_status}")
    return jsonify({'success': True, 'message': f'Status updated to {new_status}.'})

@app.route('/api/toggle_featured', methods=['POST'])
@login_required
def toggle_featured():
    data = request.json
    crime_id = data.get('crime_id')
    conn = get_db()
    current = conn.execute("SELECT featured FROM crimes WHERE id=?", (crime_id,)).fetchone()
    new_val = 0 if current['featured'] else 1
    conn.execute("UPDATE crimes SET featured=? WHERE id=?", (new_val, crime_id))
    conn.commit(); conn.close()
    log_activity('toggle_featured', f"Crime {crime_id} featured={new_val}")
    return jsonify({'success': True, 'featured': new_val})

@app.route('/api/remove_crime', methods=['POST'])
@login_required
def remove_crime():
    data     = request.json
    crime_id = data.get('crime_id')
    conn     = get_db()
    photos   = conn.execute("SELECT filename FROM crime_photos WHERE crime_id=?", (crime_id,)).fetchall()
    for photo in photos:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
        if os.path.exists(filepath): os.remove(filepath)
    conn.execute("DELETE FROM crime_photos WHERE crime_id=?",   (crime_id,))
    conn.execute("DELETE FROM suspects WHERE crime_id=?",       (crime_id,))
    conn.execute("DELETE FROM evidence WHERE crime_id=?",       (crime_id,))
    conn.execute("DELETE FROM tips WHERE crime_id=?",           (crime_id,))
    conn.execute("DELETE FROM comments WHERE crime_id=?",       (crime_id,))
    conn.execute("DELETE FROM bookmarks WHERE crime_id=?",      (crime_id,))
    conn.execute("DELETE FROM crime_reactions WHERE crime_id=?",(crime_id,))
    conn.execute("DELETE FROM crime_history WHERE crime_id=?",  (crime_id,))
    conn.execute("DELETE FROM crimes WHERE id=?",               (crime_id,))
    conn.commit(); conn.close()
    log_activity('remove_crime', f"Crime {crime_id} deleted")
    return jsonify({'success': True, 'message': 'Crime report removed.'})

@app.route('/api/bulk_action', methods=['POST'])
@login_required
def bulk_action():
    data = request.json
    action = data.get('action')
    crime_ids = data.get('crime_ids', [])
    if not crime_ids:
        return jsonify({'success': False, 'message': 'No crimes selected.'})

    conn = get_db()
    count = 0
    for crime_id in crime_ids:
        if action == 'verify':
            conn.execute("UPDATE crimes SET verified=1, status='Under Investigation' WHERE id=?", (crime_id,))
            count += 1
        elif action == 'close':
            conn.execute("UPDATE crimes SET status='Closed' WHERE id=?", (crime_id,))
            count += 1
        elif action == 'delete':
            photos = conn.execute("SELECT filename FROM crime_photos WHERE crime_id=?", (crime_id,)).fetchall()
            for photo in photos:
                fp = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
                if os.path.exists(fp): os.remove(fp)
            for tbl in ['crime_photos','suspects','evidence','tips','comments','bookmarks','crime_reactions','crime_history']:
                conn.execute(f"DELETE FROM {tbl} WHERE crime_id=?", (crime_id,))
            conn.execute("DELETE FROM crimes WHERE id=?", (crime_id,))
            count += 1

    conn.commit(); conn.close()
    log_activity('bulk_action', f"Bulk '{action}' on {count} crimes")
    return jsonify({'success': True, 'message': f'{action.capitalize()}d {count} crime(s) successfully.'})

@app.route('/api/map_data')
def map_data():
    conn = get_db()
    crimes = conn.execute(
        "SELECT id,title,crime_type,severity,lat,lng,location,area,status,date_occurred,verified FROM crimes WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ).fetchall()
    conn.close()
    return jsonify([dict(c) for c in crimes])

@app.route('/api/review_tip', methods=['POST'])
@login_required
def review_tip():
    data = request.json
    tip_id = data.get('tip_id')
    action = data.get('action', 'approve')  # 'approve' or 'reject'
    conn = get_db()
    if action == 'approve':
        conn.execute("UPDATE tips SET reviewed=1 WHERE id=?", (tip_id,))
        msg = 'Tip approved and made public.'
    else:
        conn.execute("DELETE FROM tips WHERE id=?", (tip_id,))
        msg = 'Tip rejected and deleted.'
    conn.commit(); conn.close()
    log_activity('review_tip', f"Tip {tip_id} {action}d")
    return jsonify({'success': True, 'message': msg})

# ─── Export CSV (admin) ───────────────────────────────────────────────────────

@app.route('/admin/export_csv')
@login_required
def export_csv():
    conn = get_db()
    crimes = conn.execute("SELECT * FROM crimes ORDER BY date_reported DESC").fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Title', 'Type', 'Severity', 'Status', 'Location', 'Area',
                     'Reported By', 'Date Occurred', 'Date Reported', 'Verified',
                     'Lat', 'Lng', 'Views', 'Featured', 'Tags'])
    for c in crimes:
        writer.writerow([c['id'], c['title'], c['crime_type'], c['severity'], c['status'],
                         c['location'], c['area'], c['reported_by'], c['date_occurred'],
                         c['date_reported'], 'Yes' if c['verified'] else 'No',
                         c['lat'], c['lng'], c['view_count'], c['featured'], c['tags']])

    log_activity('export_csv', f"Exported {len(crimes)} crimes to CSV")
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=crimewatch_export_{datetime.now().strftime("%Y%m%d")}.csv'}
    )

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)