import os
import sqlite3
import json
import csv
import io
import re
import requests
from io import StringIO
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'farm_multi.sqlite')

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_login'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

KST = timezone(timedelta(hours=9))

def safe_int(val):
    if val is None: return 0
    while isinstance(val, (list, tuple)):
        if len(val) > 0: val = val[ 0 ]
        else: return 0
    if isinstance(val, str):
        s = ''.join(filter(lambda c: c.isdigit() or c == '-', val))
        return int(s) if s else 0
    try: return int(val)
    except (ValueError, TypeError): return 0

def get_uid():
    if not current_user or not current_user.is_authenticated:
        return 0
    return safe_int(getattr(current_user, 'id', 0))

class User(UserMixin):
    def __init__(self, id, username, crop_start='', crop_end='', sync_crop=0, farm_name=''):
        self.id = safe_int(id)
        self.username = username
        self.crop_start = crop_start
        self.crop_end = crop_end
        self.sync_crop = sync_crop
        self.farm_name = farm_name

    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    clean_id = safe_int(user_id)
    if not clean_id: return None

    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    try:
        c.execute("SELECT id, username, crop_start, crop_end, sync_crop, farm_name FROM users WHERE id = ?", (clean_id,))
        user = c.fetchone()
        if user: return User(user[ 0 ], user[ 1 ], user[ 2 ] or '', user[ 3 ] or '', user[ 4 ] or 0, user[ 5 ] or '')

        c.execute("SELECT id, username, crop_start, crop_end, sync_crop FROM users WHERE id = ?", (clean_id,))
        user = c.fetchone()
        if user: return User(user[ 0 ], user[ 1 ], user[ 2 ] or '', user[ 3 ] or '', user[ 4 ] or 0, '')

        c.execute("SELECT id, username FROM users WHERE id = ?", (clean_id,))
        user = c.fetchone()
        if user: return User(user[ 0 ], user[ 1 ])
    finally:
        conn.close()
    return None

def add_col_safe(c, table, col, col_type):
    c.execute(f"PRAGMA table_info({table})")
    existing_cols = [row[ 1 ] for row in c.fetchall()]
    if col not in existing_cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    try:
        c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS pesticides (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, mech TEXT, pest TEXT, unit INTEGER, price INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, area TEXT, crop TEXT, name TEXT, mech TEXT, pest TEXT, count INTEGER, amount INTEGER, price INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS shipments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, destination TEXT, quantity INTEGER, bid_price INTEGER, sales_amount INTEGER, shipping_cost INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS seeds (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, category TEXT, quantity REAL, unit TEXT, cost INTEGER, note TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS fertilizers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, category TEXT, type TEXT, quantity REAL, cost INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS fuels (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, fuel_type TEXT, quantity REAL, cost INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS electricity (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, usage_kw REAL, cost INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS other_costs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, material_name TEXT, quantity REAL, unit TEXT, cost INTEGER, replace_cycle TEXT, burden_ratio INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS income_manual (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE, data TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS irrigations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, area TEXT, water_amount TEXT, nutrients TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS memos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, content TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nutrient_list (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, type TEXT, default_unit TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS board (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, content TEXT, created_at TEXT, views INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER, content TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS farm_info (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, house_name TEXT, area TEXT, vinyl_size TEXT, facilities TEXT, machinery TEXT, note TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS growth_data (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, house_name TEXT, plant_height REAL, leaf_count INTEGER, leaf_length REAL, leaf_width REAL, avg_temp REAL, avg_humidity REAL, dif REAL, acc_temp REAL, note TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS farm_profile (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE, data TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, content TEXT, created_at TEXT)''')

        add_col_safe(c, 'records', 'event_id', 'TEXT')
        add_col_safe(c, 'fertilizers', 'product_name', 'TEXT')
        add_col_safe(c, 'fertilizers', 'unit', 'TEXT')
        add_col_safe(c, 'irrigations', 'nutrient_amount', 'REAL')
        add_col_safe(c, 'irrigations', 'nutrient_unit', 'TEXT')
        add_col_safe(c, 'irrigations', 'event_id', 'TEXT')
        add_col_safe(c, 'users', 'crop_start', 'TEXT')
        add_col_safe(c, 'users', 'crop_end', 'TEXT')
        add_col_safe(c, 'users', 'sync_crop', 'INTEGER')
        add_col_safe(c, 'users', 'farm_name', 'TEXT')
        add_col_safe(c, 'users', 'last_active', 'TEXT')
        add_col_safe(c, 'farm_info', 'address', 'TEXT')
        add_col_safe(c, 'farm_info', 'planting_date', 'TEXT')
        add_col_safe(c, 'growth_data', 'plant_num', 'INTEGER')
        add_col_safe(c, 'growth_data', 'fruiting_habit', 'TEXT')
        add_col_safe(c, 'growth_data', 'stem_type', 'TEXT')
        add_col_safe(c, 'growth_data', 'main_node', 'INTEGER')
        add_col_safe(c, 'growth_data', 'sub_node', 'INTEGER')
        add_col_safe(c, 'growth_data', 'internode_len', 'REAL')
        add_col_safe(c, 'growth_data', 'stem_thick', 'REAL')
        add_col_safe(c, 'growth_data', 'leaf_status', 'INTEGER')
        add_col_safe(c, 'growth_data', 'female_flower', 'INTEGER')
        add_col_safe(c, 'growth_data', 'harvest', 'INTEGER')
        add_col_safe(c, 'growth_data', 'fruit_status', 'INTEGER')
        add_col_safe(c, 'growth_data', 'fruit_curved', 'REAL')
        add_col_safe(c, 'growth_data', 'fruit_straight', 'REAL')
        add_col_safe(c, 'growth_data', 'fruit_width', 'REAL')
        add_col_safe(c, 'comments', 'parent_id', 'INTEGER')
        add_col_safe(c, 'board', 'is_notice', 'INTEGER DEFAULT 0')
        add_col_safe(c, 'users', 'is_approved', 'INTEGER DEFAULT 0')

        conn.commit()
    except Exception: pass
    finally: conn.close()

init_db()

def approved_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.username == 'admin':
            return f(*args, **kwargs)
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        is_approved = 0
        try:
            c.execute("SELECT COALESCE(is_approved, 0) FROM users WHERE id=?", (current_user.id,))
            row = c.fetchone()
            if row: is_approved = safe_int(row[ 0 ])
        except Exception: pass
        finally: conn.close()

        if not is_approved:
            flash('🚨 오이연구회 정회원만 입장 가능합니다. 관리자에게 승인을 요청하세요!')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def update_last_active():
    if current_user.is_authenticated:
        uid = get_uid()
        if uid:
            conn = sqlite3.connect(DB_FILE, timeout=10.0)
            c = conn.cursor()
            try:
                now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                c.execute("UPDATE users SET last_active = ? WHERE id = ?", (now_str, uid))
                conn.commit()
            except Exception: pass
            finally: conn.close()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        hashed_pw = generate_password_hash(password)
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_pw))
            conn.commit()
            flash('가입 완료! 관리자 승인 후 커뮤니티 이용이 가능합니다.')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('이미 존재하는 아이디입니다.')
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        req_username = request.form.get('username')
        req_password = request.form.get('password')

        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT id, username, password FROM users WHERE username = ?", (req_username,))
        user_data = c.fetchone()
        conn.close()

        if user_data:
            db_id, db_name, db_pw = user_data
            if check_password_hash(db_pw, req_password):
                login_user(User(db_id, db_name))
                return redirect(url_for('home'))
            else:
                flash('아이디나 비밀번호가 틀렸습니다.')
        else:
            flash('아이디나 비밀번호가 틀렸습니다.')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    uid = get_uid()
    if request.method == 'POST':
        farm_name = request.form.get('farm_name', '')
        crop_start = request.form.get('crop_start', '')
        crop_end = request.form.get('crop_end', '')
        sync_crop = 1 if request.form.get('sync_crop') == 'on' else 0

        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        c.execute("UPDATE users SET farm_name=?, crop_start=?, crop_end=?, sync_crop=? WHERE id=?",
                  (farm_name, crop_start, crop_end, sync_crop, uid))
        conn.commit()
        conn.close()

        flash('프로필이 성공적으로 저장되었습니다.')
        return redirect(url_for('profile'))

    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT farm_name, crop_start, crop_end, sync_crop FROM users WHERE id=?", (uid,))
    u = c.fetchone()
    conn.close()
    return render_template('profile.html', farm_name=u[ 0 ] if u else '', crop_start=u[ 1 ] if u else '', crop_end=u[ 2 ] if u else '', sync_crop=u[ 3 ] if u else 0)

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    uid = get_uid()
    current_pw = request.form.get('current_password')
    new_pw = request.form.get('new_password')
    confirm_pw = request.form.get('confirm_password')

    if new_pw != confirm_pw:
        flash('새 비밀번호가 서로 일치하지 않습니다.')
        return redirect(url_for('profile'))

    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE id = ?", (uid,))
    user_data = c.fetchone()

    if user_data and check_password_hash(user_data[ 0 ], current_pw):
        hashed_pw = generate_password_hash(new_pw)
        c.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pw, uid))
        conn.commit()
        flash('비밀번호 변경 완료.')
    else:
        flash('현재 비밀번호가 틀렸습니다.')

    conn.close()
    return redirect(url_for('profile'))

@app.route('/')
@login_required
def home():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()

    c.execute("SELECT date, '방제' as type, name FROM records WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 3", (uid,))
    pest = c.fetchall()
    c.execute("SELECT date, '관주' as type, area || ' ' || water_amount FROM irrigations WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 3", (uid,))
    irri = c.fetchall()
    c.execute("SELECT date, '출하' as type, quantity || '박스' FROM shipments WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 3", (uid,))
    ship = c.fetchall()
    c.execute("SELECT date, '메모' as type, substr(content, 1, 15) || '...' FROM memos WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 3", (uid,))
    memo = c.fetchall()
    c.execute("SELECT substr(created_at, 1, 10) as date, '💬새글' as type, title FROM board ORDER BY id DESC LIMIT 3")
    board_posts = c.fetchall()

    yesterday_str = (datetime.now(KST) - timedelta(days=1)).strftime('%Y-%m-%d %H:%M')
    c.execute("SELECT COUNT(*) FROM board WHERE created_at >= ?", (yesterday_str,))
    row = c.fetchone()
    new_post_count = safe_int(row[ 0 ] if row else 0)
    has_new_post = True if new_post_count > 0 else False

    c.execute("SELECT data FROM farm_profile WHERE user_id = ?", (uid,))
    prof_row = c.fetchone()
    farm_address = ""
    if prof_row and prof_row[ 0 ]:
        try:
            prof_data = json.loads(prof_row[ 0 ])
            if isinstance(prof_data, str): prof_data = json.loads(prof_data)
            if isinstance(prof_data, dict):
                farm_address = prof_data.get('field_address_1', '').strip()
                if not farm_address: farm_address = prof_data.get('home_address', '').strip()
        except Exception: pass

    conn.close()

    all_recent = pest + irri + ship + memo + board_posts
    all_recent.sort(key=lambda x: str(x[ 0 ]) if x and x[ 0 ] else '', reverse=True)
    top_recent = all_recent[:6]

    ticker_text = "🥒 오늘도 오이를 스마트하게! 새로운 기록을 남겨보세요!"
    if top_recent:
        ticker_text = " ✦ ".join([f"[{r[ 0 ]}] {r[ 1 ]} : {r[ 2 ]}" for r in top_recent]) + " ✦ "

    return render_template('home.html', ticker_text=ticker_text, has_new_post=has_new_post, farm_address=farm_address)

@app.route('/farming_menu')
@login_required
def farming_menu(): return render_template('farming_menu.html')

@app.route('/community_menu')
@login_required
@approved_required
def community_menu(): return render_template('community_menu.html')

@app.route('/farm_info')
@login_required
def farm_info():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT data FROM farm_profile WHERE user_id = ?", (uid,)); row = c.fetchone(); conn.close()
    farm_data = json.loads(row[ 0 ]) if row else {}
    return render_template('farm_info.html', data=farm_data)

@app.route('/save_farm_profile', methods=['POST'])
@login_required
def save_farm_profile():
    uid = get_uid(); json_data = json.dumps(dict(request.form), ensure_ascii=False)
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT id FROM farm_profile WHERE user_id = ?", (uid,))
    if c.fetchone(): c.execute("UPDATE farm_profile SET data = ? WHERE user_id = ?", (json_data, uid))
    else: c.execute("INSERT INTO farm_profile (user_id, data) VALUES (?, ?)", (uid, json_data))
    conn.commit(); conn.close(); flash("저장 완료"); return redirect(url_for('farm_info'))

# ==========================================
# 📊 1. 농가 현황판 엑셀 다운로드 엔진 (표 형식)
# ==========================================
@app.route('/export_farm_profile')
@login_required
@approved_required
def export_farm_profile():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT data FROM farm_profile WHERE user_id = ?", (uid,))
    row = c.fetchone()
    conn.close()

    if not row:
        flash("데이터가 없습니다.")
        return redirect(url_for('farm_info'))

    (json_string,) = row
    if not json_string:
        flash("데이터가 없습니다.")
        return redirect(url_for('farm_info'))

    try:
        data = json.loads(json_string)
        if isinstance(data, str): data = json.loads(data)
    except Exception:
        data = dict()

    si = StringIO()
    cw = csv.writer(si)

    cw.writerow(('--- 기본 인적사항 및 영농규모 ---',))
    basic_keys = (
        ('crop_name', '작물명'), ('survey_year', '조사연도'), ('cultivation_type', '재배형태'),
        ('farm_name', '농장(농가)명'), ('phone', '연락처'), ('home_address', '자택 주소'),
        ('field_address_1', '농장 주소 1'), ('field_address_2', '농장 주소 2'),
        ('mgr_age', '경영주 연령(세)'), ('mgr_exp', '영농경력(년)'), ('mgr_crop_exp', '해당작물 경력(년)'),
        ('mgr_family', '영농참여 가족수(명)'), ('scale_rice', '영농규모-벼(평)'), ('scale_veg', '영농규모-채소(평)'),
        ('scale_fruit', '영농규모-과수(평)'), ('scale_etc', '영농규모-기타(평)'), ('scale_stock', '영농규모-축산')
    )
    for (k, v_name) in basic_keys:
        val = data.get(k, '')
        if val: cw.writerow((v_name, val))

    cw.writerow(('',))
    cw.writerow(('--- 보유 시설 현황 ---',))
    cw.writerow(('시설명', '설치/구입연도', '규모(평/톤)', '금액(천원)', '보조비율(%)'))
    fac_list = (
        ('fac_house', '온실(하우스)'), ('fac_whouse', '작업장'), ('fac_storage', '저장고'),
        ('fac_sort', '선별장'), ('fac_well', '관정'), ('fac_water', '관수시설')
    )
    for (f_key, f_name) in fac_list:
        yr, sc, co, ra = data.get(f_key+'_yr',''), data.get(f_key+'_scale',''), data.get(f_key+'_cost',''), data.get(f_key+'_ratio','')
        if yr or sc or co or ra: cw.writerow((f_name, yr, sc, co, ra))

    cw.writerow(('',))
    cw.writerow(('--- 보유 농기계 현황 ---',))
    cw.writerow(('기종명', '구입연도', '보유대수', '금액(천원)', '보조비율(%)'))
    mac_list = (
        ('mac_1', '트랙터'), ('mac_2', '관리기'), ('mac_3', '이앙기'),
        ('mac_4', '운반기'), ('mac_5', '방제기'), ('mac_6', '예초기')
    )
    for (m_key, m_name) in mac_list:
        yr, cnt, co, ra = data.get(m_key+'_yr',''), data.get(m_key+'_cnt',''), data.get(m_key+'_cost',''), data.get(m_key+'_ratio','')
        if yr or cnt or co or ra: cw.writerow((m_name, yr, cnt, co, ra))

    dyn_ids = set()
    for k in data.keys():
        if k.startswith('mac_dyn_'):
            it = iter(k.split('_'))
            next(it); next(it)
            dyn_ids.add(next(it))

    for dyn_id in dyn_ids:
        name = data.get('mac_dyn_'+dyn_id+'_name', '추가기계')
        yr = data.get('mac_dyn_'+dyn_id+'_yr', '')
        cnt = data.get('mac_dyn_'+dyn_id+'_cnt', '')
        co = data.get('mac_dyn_'+dyn_id+'_cost', '')
        ra = data.get('mac_dyn_'+dyn_id+'_ratio', '')
        if name or yr or cnt or co or ra: cw.writerow((name, yr, cnt, co, ra))

    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=farm_profile_table.csv"})

@app.route('/growth')
@login_required
def growth():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT g.id, g.user_id, g.date, g.house_name, g.plant_num, g.fruiting_habit, g.stem_type, g.main_node, g.sub_node, g.internode_len, g.stem_thick, g.leaf_length, g.leaf_width, g.leaf_status, g.female_flower, g.harvest, g.fruit_status, g.fruit_curved, g.fruit_straight, g.fruit_width, g.avg_temp, g.avg_humidity, g.dif, g.acc_temp, g.note FROM growth_data g WHERE g.user_id = ? ORDER BY g.date DESC, g.id DESC", (uid,))
    g_data_raw = c.fetchall(); conn.close()
    g_data = [list(r) + [""] for r in g_data_raw]
    chart_data = list(reversed(g_data))
    dates_json = json.dumps([f"{r[ 2 ][5:]}({r[ 7 ]}마디)" if r[ 7 ] else r[ 2 ][5:] for r in chart_data])
    internode_json = json.dumps([safe_int(r[ 9 ]) for r in chart_data])
    thick_json = json.dumps([safe_int(r[ 10 ]) for r in chart_data])
    return render_template('growth.html', g_data=g_data, houses=["우리농장 전체"], dates=dates_json, internode_len=internode_json, stem_thick=thick_json)

@app.route('/add_growth', methods=['POST'])
@login_required
def add_growth():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO growth_data (user_id, date, house_name, plant_num, fruiting_habit, stem_type, main_node, sub_node, internode_len, stem_thick, leaf_length, leaf_width, leaf_status, female_flower, harvest, fruit_status, fruit_curved, fruit_straight, fruit_width, avg_temp, avg_humidity, dif, acc_temp, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('house_name'), d.get('plant_num') or None, d.get('fruiting_habit'), d.get('stem_type'), d.get('main_node') or None, d.get('sub_node') or None, d.get('internode_len') or None, d.get('stem_thick') or None, d.get('leaf_length') or None, d.get('leaf_width') or None, d.get('leaf_status') or None, d.get('female_flower') or None, d.get('harvest') or None, d.get('fruit_status') or None, d.get('fruit_curved') or None, d.get('fruit_straight') or None, d.get('fruit_width') or None, d.get('avg_temp') or None, d.get('avg_humidity') or None, d.get('dif') or None, d.get('acc_temp') or None, d.get('note')))
    conn.commit(); conn.close(); return redirect(url_for('growth'))

@app.route('/edit_growth', methods=['POST'])
@login_required
def edit_growth():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE growth_data SET date=?, house_name=?, plant_num=?, fruiting_habit=?, stem_type=?, main_node=?, sub_node=?, internode_len=?, stem_thick=?, leaf_length=?, leaf_width=?, leaf_status=?, female_flower=?, harvest=?, fruit_status=?, fruit_curved=?, fruit_straight=?, fruit_width=?, avg_temp=?, avg_humidity=?, dif=?, acc_temp=?, note=? WHERE id=? AND user_id=?", (d.get('date'), d.get('house_name'), d.get('plant_num') or None, d.get('fruiting_habit'), d.get('stem_type'), d.get('main_node') or None, d.get('sub_node') or None, d.get('internode_len') or None, d.get('stem_thick') or None, d.get('leaf_length') or None, d.get('leaf_width') or None, d.get('leaf_status') or None, d.get('female_flower') or None, d.get('harvest') or None, d.get('fruit_status') or None, d.get('fruit_curved') or None, d.get('fruit_straight') or None, d.get('fruit_width') or None, d.get('avg_temp') or None, d.get('avg_humidity') or None, d.get('dif') or None, d.get('acc_temp') or None, d.get('note'), d.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('growth'))

@app.route('/growth_sim')
@login_required
def growth_sim():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()

    # 생육 데이터에서 날짜와 주지마디수만 날짜 오름차순(과거->최신)으로 가져옵니다.
    c.execute("SELECT date, main_node FROM growth_data WHERE user_id = ? AND main_node IS NOT NULL ORDER BY date ASC", (uid,))
    rows = c.fetchall()
    conn.close()

    # 프론트엔드 JavaScript에서 사용하기 좋게 JSON 형태로 가공합니다.
    sim_data = []
    for r in rows:
        # ✨ 대괄호 증발 버그를 막기 위해 값을 미리 꺼내어 이름을 붙여줍니다.
        date_val, node_val = r

        sim_data.append({
            'date': str(date_val),
            'nodes': safe_int(node_val)
        })

    return render_template('growth_sim.html', sim_data_json=json.dumps(sim_data))

@app.route('/board')
@login_required
@approved_required
def board():
    search = request.args.get('search', '')
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    query = "SELECT b.id, b.title, u.username, u.farm_name, b.created_at, b.views, b.user_id, COALESCE(b.is_notice, 0) FROM board b JOIN users u ON b.user_id = u.id"
    params = []
    if search:
        query += " WHERE b.title LIKE ? "
        params.append(f'%{search}%')
    query += " ORDER BY COALESCE(b.is_notice, 0) DESC, b.id DESC"
    c.execute(query, params)
    posts = c.fetchall()
    conn.close()
    return render_template('board.html', posts=posts, search=search)

@app.route('/board_view/<int:post_id>')
@login_required
@approved_required
def board_view(post_id):
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("UPDATE board SET views = views + 1 WHERE id = ?", (post_id,))
    conn.commit()
    c.execute("SELECT b.id, b.title, b.content, u.username, u.farm_name, b.created_at, b.views, b.user_id FROM board b JOIN users u ON b.user_id = u.id WHERE b.id = ?", (post_id,))
    post = c.fetchone()
    c.execute("SELECT c.id, c.content, c.created_at, u.username, u.farm_name, c.user_id, COALESCE(c.parent_id, 0) FROM comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.id ASC", (post_id,))
    all_comments = c.fetchall()
    conn.close()
    if not post: return redirect(url_for('board'))

    comments = []; replies = {}
    for cmt in all_comments:
        pid = safe_int(cmt[ 6 ])
        if pid == 0: comments.append(cmt)
        else:
            if pid not in replies: replies[pid] = []
            replies[pid].append(cmt)
    return render_template('board_view.html', post=post, comments=comments, replies=replies)

@app.route('/board_write', methods=['GET', 'POST'])
@login_required
@approved_required
def board_write():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        is_notice = 1 if request.form.get('is_notice') == 'on' else 0
        now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M')
        uid = get_uid()
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        c.execute("INSERT INTO board (user_id, title, content, created_at, views, is_notice) VALUES (?, ?, ?, ?, 0, ?)", (uid, title, content, now_str, is_notice))
        conn.commit()
        conn.close()
        return redirect(url_for('board'))
    return render_template('board_form.html', post=None)

@app.route('/board_edit/<int:post_id>', methods=['GET', 'POST'])
@login_required
@approved_required
def board_edit(post_id):
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT id, user_id, title, content, COALESCE(is_notice, 0) FROM board WHERE id = ?", (post_id,))
    post = c.fetchone()
    if not post or (post[ 1 ] != uid and current_user.username != 'admin'):
        conn.close(); flash('수정 권한이 없습니다.'); return redirect(url_for('board_view', post_id=post_id))
    if request.method == 'POST':
        title = request.form.get('title'); content = request.form.get('content')
        is_notice = 1 if request.form.get('is_notice') == 'on' else 0
        c.execute("UPDATE board SET title=?, content=?, is_notice=? WHERE id=?", (title, content, is_notice, post_id))
        conn.commit(); conn.close(); return redirect(url_for('board_view', post_id=post_id))
    conn.close()
    return render_template('board_form.html', post=post)

@app.route('/board_delete/<int:post_id>')
@login_required
@approved_required
def board_delete(post_id):
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT user_id FROM board WHERE id = ?", (post_id,)); post = c.fetchone()
    if post and (post[ 0 ] == uid or current_user.username == 'admin'):
        c.execute("DELETE FROM board WHERE id = ?", (post_id,))
        c.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        conn.commit()
    conn.close(); return redirect(url_for('board'))

@app.route('/add_comment/<int:post_id>', methods=['POST'])
@login_required
@approved_required
def add_comment(post_id):
    content = request.form.get('content'); parent_id = safe_int(request.form.get('parent_id', 0)); uid = get_uid()
    if content and content.strip():
        now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M')
        conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
        c.execute("INSERT INTO comments (post_id, user_id, content, created_at, parent_id) VALUES (?, ?, ?, ?, ?)", (post_id, uid, content.strip(), now_str, parent_id))
        conn.commit(); conn.close()
    return redirect(url_for('board_view', post_id=post_id))

@app.route('/delete_comment/<int:comment_id>')
@login_required
@approved_required
def delete_comment(comment_id):
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT user_id, post_id FROM comments WHERE id = ?", (comment_id,)); comment = c.fetchone()
    if comment:
        if comment[ 0 ] == uid or current_user.username == 'admin':
            c.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            c.execute("DELETE FROM comments WHERE parent_id = ?", (comment_id,))
            conn.commit()
        post_id = comment[ 1 ]
    else: post_id = 0
    conn.close()
    if post_id: return redirect(url_for('board_view', post_id=post_id))
    return redirect(url_for('board'))

@app.route('/pesticide')
@login_required
def pesticide():
    uid = get_uid(); start_date = request.args.get('start_date'); end_date = request.args.get('end_date')
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM pesticides ORDER BY name ASC"); db_list = c.fetchall()
    c.execute("SELECT * FROM records WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,)); records = c.fetchall()
    c.execute("SELECT name, SUM(price), COUNT(id) FROM records WHERE user_id = ? GROUP BY name ORDER BY SUM(price) DESC", (uid,)); stats_raw = c.fetchall()
    conn.close()

    events_dict = {}
    for r in records:
        r_list = list(r)
        while len(r_list) < 12: r_list.append(None)
        event_id = str(r_list[ 11 ]) if r_list[ 11 ] is not None else f"old_{r_list[ 0 ]}"
        item_price = safe_int(r_list[ 10 ])
        try: item_count = float(r_list[ 8 ]) if r_list[ 8 ] is not None else 1.0
        except ValueError: item_count = 1.0
        if event_id not in events_dict: events_dict[event_id] = {'event_id': event_id, 'date': str(r_list[ 2 ]) if r_list[ 2 ] else '', 'area': str(r_list[ 3 ]) if r_list[ 3 ] and str(r_list[ 3 ]) != 'None' else '', 'crop': str(r_list[ 4 ]) if r_list[ 4 ] else '', 'items': [], 'total_price': 0, 'item_count': 0}
        events_dict[event_id]['items'].append({'id': r_list[ 0 ], 'name': str(r_list[ 5 ]) if r_list[ 5 ] else '알수없음', 'count': item_count, 'price': item_price})
        events_dict[event_id]['total_price'] += item_price; events_dict[event_id]['item_count'] += 1
    events_list = list(events_dict.values()); events_list.sort(key=lambda x: (x['date'], x['event_id']), reverse=True)
    cal_dict = {}
    for ev in events_list:
        date_str = ev['date']
        if date_str not in cal_dict: cal_dict[date_str] = []
        names_str = ", ".join([item['name'] for item in ev['items']])
        cal_dict[date_str].append({'name': names_str, 'crop': ev['crop'], 'pest': '혼용 방제' if ev['item_count'] > 1 else '단독 방제'})
    total_cost_calc = sum([safe_int(r[ 1 ]) for r in stats_raw])
    return render_template('pesticide.html', db_list=db_list, events=events_list, stats_labels=json.dumps([str(r[ 0 ]) for r in stats_raw]), stats_prices=json.dumps([safe_int(r[ 1 ]) for r in stats_raw]), stats_counts=json.dumps([safe_int(r[ 2 ]) for r in stats_raw]), calendar_data=json.dumps(cal_dict), total_cost=total_cost_calc, start_date=start_date, end_date=end_date)

@app.route('/add_db', methods=['POST'])
@login_required
def add_db():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO pesticides (user_id, name, mech, pest, unit, price) VALUES (?,?,?,?,?,?)", (uid, request.form.get('name'), request.form.get('mech'), request.form.get('pest'), request.form.get('unit',0), request.form.get('price',0)))
    conn.commit(); conn.close(); return redirect(url_for('pesticide'))

@app.route('/edit_db', methods=['POST'])
@login_required
def edit_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE pesticides SET name=?, mech=?, pest=?, unit=?, price=? WHERE id=?", (request.form.get('name'), request.form.get('mech'), request.form.get('pest'), request.form.get('unit',0), request.form.get('price',0), request.form.get('id')))
    conn.commit(); conn.close(); return redirect(url_for('pesticide'))

@app.route('/add_record', methods=['POST'])
@login_required
def add_record():
    d = request.form
    names = request.form.getlist('pest_name')  # ✨ 바뀐 이름표 적용
    counts = request.form.getlist('pest_count') # ✨ 바뀐 이름표 적용
    event_id = str(int(datetime.now(KST).timestamp() * 1000)); uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    for i in range(len(names)):
        name = names[i]
        if not name: continue
        try: count = float(counts[i]) if i < len(counts) and counts[i] else 1.0
        except ValueError: count = 1.0
        c.execute("SELECT mech, pest, unit, price FROM pesticides WHERE name=?", (name,)); p = c.fetchone()
        if p: c.execute("INSERT INTO records (user_id, date, area, crop, name, mech, pest, count, amount, price, event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('area'), d.get('crop'), name, p[ 0 ], p[ 1 ], count, int(safe_int(p[ 2 ])*count), int(safe_int(p[ 3 ])*count), event_id))
    conn.commit(); conn.close(); return redirect(url_for('pesticide'))

@app.route('/edit_record', methods=['POST'])
@login_required
def edit_record():
    d = request.form; event_id = str(d.get('event_id')); uid = get_uid()
    names = request.form.getlist('pest_name')   # ✨ 바뀐 이름표 적용
    counts = request.form.getlist('pest_count')  # ✨ 바뀐 이름표 적용
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    if event_id.startswith('old_'): c.execute("DELETE FROM records WHERE id=? AND user_id=?", (int(event_id.replace('old_', '')), uid)); new_event_id = str(int(datetime.now(KST).timestamp() * 1000))
    else: c.execute("DELETE FROM records WHERE event_id=? AND user_id=?", (event_id, uid)); new_event_id = event_id
    for i in range(len(names)):
        name = names[i]
        if not name: continue
        try: count = float(counts[i]) if i < len(counts) and counts[i] else 1.0
        except ValueError: count = 1.0
        c.execute("SELECT mech, pest, unit, price FROM pesticides WHERE name=?", (name,)); p = c.fetchone()
        if p: c.execute("INSERT INTO records (user_id, date, area, crop, name, mech, pest, count, amount, price, event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('area'), d.get('crop'), name, p[ 0 ], p[ 1 ], count, int(safe_int(p[ 2 ])*count), int(safe_int(p[ 3 ])*count), new_event_id))
    conn.commit(); conn.close(); return redirect(url_for('pesticide'))

@app.route('/delete_record_event/<event_id>')
@login_required
def delete_record_event(event_id):
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    if str(event_id).startswith('old_'): c.execute("DELETE FROM records WHERE id=? AND user_id=?", (int(str(event_id).replace('old_', '')), uid))
    else: c.execute("DELETE FROM records WHERE event_id=? AND user_id=?", (str(event_id), uid))
    conn.commit(); conn.close(); return redirect(url_for('pesticide'))

@app.route('/irrigation')
@login_required
def irrigation():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM nutrient_list ORDER BY name ASC"); db_list = c.fetchall()
    c.execute("SELECT * FROM irrigations WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,)); records = c.fetchall()
    c.execute("SELECT nutrients, SUM(nutrient_amount), COUNT(id) FROM irrigations WHERE user_id = ? AND nutrients != '' AND nutrients IS NOT NULL GROUP BY nutrients ORDER BY SUM(nutrient_amount) DESC", (uid,)); stats_raw = c.fetchall()
    conn.close()
    events_dict = {}
    for r in records:
        r_list = list(r)
        while len(r_list) < 9: r_list.append(None)
        event_id = str(r_list[ 8 ]) if r_list[ 8 ] is not None else f"old_{r_list[ 0 ]}"
        if event_id not in events_dict: events_dict[event_id] = {'event_id': event_id, 'date': str(r_list[ 2 ]) if r_list[ 2 ] else '', 'area': str(r_list[ 3 ]) if r_list[ 3 ] else '', 'water_amount': str(r_list[ 4 ]) if r_list[ 4 ] else '', 'items': [], 'item_count': 0}
        n_name = r_list[ 5 ]
        if n_name and n_name != '맹물관주': events_dict[event_id]['items'].append({'id': r_list[ 0 ], 'name': n_name, 'amount': r_list[ 6 ] if r_list[ 6 ] is not None else 0.0, 'unit': r_list[ 7 ] if r_list[ 7 ] else 'L'}); events_dict[event_id]['item_count'] += 1
    events_list = list(events_dict.values()); events_list.sort(key=lambda x: (x['date'], x['event_id']), reverse=True)
    cal_dict = {}
    for ev in events_list:
        date_str = ev['date']
        if date_str not in cal_dict: cal_dict[date_str] = []
        cal_dict[date_str].append(ev)
    return render_template('irrigation.html', db_list=db_list, events=events_list, stats_labels=json.dumps([str(r[ 0 ]) for r in stats_raw]), stats_amounts=json.dumps([float(r[ 1 ]) if r[ 1 ] is not None else 0 for r in stats_raw]), stats_counts=json.dumps([safe_int(r[ 2 ]) for r in stats_raw]), start_date="", end_date="", calendar_data=json.dumps(cal_dict))

@app.route('/add_nutrient_db', methods=['POST'])
@login_required
def add_nutrient_db():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor(); c.execute("INSERT INTO nutrient_list (user_id, name, type, default_unit) VALUES (?,?,?,?)", (uid, request.form.get('name'), request.form.get('type'), request.form.get('default_unit'))); conn.commit(); conn.close(); return redirect(url_for('irrigation'))

@app.route('/edit_nutrient_db', methods=['POST'])
@login_required
def edit_nutrient_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor(); c.execute("UPDATE nutrient_list SET name=?, type=?, default_unit=? WHERE id=?", (request.form.get('name'), request.form.get('type'), request.form.get('default_unit'), request.form.get('id'))); conn.commit(); conn.close(); return redirect(url_for('irrigation'))

@app.route('/add_irrigation', methods=['POST'])
@login_required
def add_irrigation():
    d = request.form; names = request.form.getlist('nutrients[]'); amounts = request.form.getlist('nutrient_amount[]'); units = request.form.getlist('nutrient_unit[]'); event_id = str(int(datetime.now(KST).timestamp() * 1000)); uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    has_nutrients = any(name and name.strip() for name in names)
    if not has_nutrients: c.execute("INSERT INTO irrigations (user_id, date, area, water_amount, nutrients, nutrient_amount, nutrient_unit, event_id) VALUES (?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('area'), d.get('water_amount'), '', 0.0, 'L', event_id))
    else:
        for i, name in enumerate(names):
            name = name.strip()
            if not name: continue
            try: amt = float(amounts[i]) if i < len(amounts) and amounts[i] else 0.0
            except ValueError: amt = 0.0
            c.execute("INSERT INTO irrigations (user_id, date, area, water_amount, nutrients, nutrient_amount, nutrient_unit, event_id) VALUES (?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('area'), d.get('water_amount'), name, amt, units[i] if i < len(units) and units[i] else 'L', event_id))
    conn.commit(); conn.close(); return redirect(url_for('irrigation'))

@app.route('/edit_irrigation', methods=['POST'])
@login_required
def edit_irrigation():
    d = request.form; event_id = str(d.get('event_id')); names = request.form.getlist('nutrients[]'); amounts = request.form.getlist('nutrient_amount[]'); units = request.form.getlist('nutrient_unit[]'); uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    if event_id.startswith('old_'): c.execute("DELETE FROM irrigations WHERE id=? AND user_id=?", (int(event_id.replace('old_', '')), uid)); new_event_id = str(int(datetime.now(KST).timestamp() * 1000))
    else: c.execute("DELETE FROM irrigations WHERE event_id=? AND user_id=?", (event_id, uid)); new_event_id = event_id
    has_nutrients = any(name and name.strip() for name in names)
    if not has_nutrients: c.execute("INSERT INTO irrigations (user_id, date, area, water_amount, nutrients, nutrient_amount, nutrient_unit, event_id) VALUES (?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('area'), d.get('water_amount'), '', 0.0, 'L', new_event_id))
    else:
        for i, name in enumerate(names):
            name = name.strip()
            if not name: continue
            try: amt = float(amounts[i]) if i < len(amounts) and amounts[i] else 0.0
            except ValueError: amt = 0.0
            c.execute("INSERT INTO irrigations (user_id, date, area, water_amount, nutrients, nutrient_amount, nutrient_unit, event_id) VALUES (?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('area'), d.get('water_amount'), name, amt, units[i] if i < len(units) and units[i] else 'L', new_event_id))
    conn.commit(); conn.close(); return redirect(url_for('irrigation'))

@app.route('/delete_irrigation_event/<event_id>')
@login_required
def delete_irrigation_event(event_id):
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    if str(event_id).startswith('old_'): c.execute("DELETE FROM irrigations WHERE id=? AND user_id=?", (int(str(event_id).replace('old_', '')), uid))
    else: c.execute("DELETE FROM irrigations WHERE event_id=? AND user_id=?", (str(event_id), uid))
    conn.commit(); conn.close(); return redirect(url_for('irrigation'))

@app.route('/fertilizer')
@login_required
def fertilizer():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT DISTINCT substr(date, 1, 4) FROM fertilizers WHERE user_id = ? ORDER BY date DESC", (uid,)); years = [row[ 0 ] for row in c.fetchall() if row[ 0 ]]
    selected_year = request.args.get('year')
    if not selected_year: selected_year = years[ 0 ] if years else str(datetime.now(KST).year)
    if selected_year == 'all': c.execute("SELECT * FROM fertilizers WHERE user_id = ? ORDER BY date DESC", (uid,)); f_raw = c.fetchall(); c.execute("SELECT category, SUM(cost) FROM fertilizers WHERE user_id = ? GROUP BY category", (uid,)); sr = c.fetchall()
    else: c.execute("SELECT * FROM fertilizers WHERE user_id = ? AND substr(date, 1, 4) = ? ORDER BY date DESC", (uid, selected_year)); f_raw = c.fetchall(); c.execute("SELECT category, SUM(cost) FROM fertilizers WHERE user_id = ? AND substr(date, 1, 4) = ? GROUP BY category", (uid, selected_year)); sr = c.fetchall()
    conn.close()
    safe_f = [list(r) + [None]*(9-len(r)) for r in f_raw]
    inorganic_total = sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='무기질비료']); organic_total = sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='유기질비료'])
    return render_template('fertilizer.html', fertilizers=safe_f, inorganic_total=inorganic_total, organic_total=organic_total, years=years, selected_year=selected_year)

@app.route('/add_fertilizer', methods=['POST'])
@login_required
def add_fertilizer():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO fertilizers (user_id, date, category, type, quantity, cost, product_name, unit) VALUES (?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('category'), d.get('type'), d.get('quantity',0), d.get('cost',0), d.get('product_name',''), d.get('unit', 'Kg')))
    conn.commit(); conn.close(); return redirect(url_for('fertilizer'))

@app.route('/edit_fertilizer', methods=['POST'])
@login_required
def edit_fertilizer():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE fertilizers SET date=?, category=?, type=?, quantity=?, cost=?, product_name=?, unit=? WHERE id=? AND user_id=?", (d.get('date'), d.get('category'), d.get('type'), d.get('quantity',0), d.get('cost',0), d.get('product_name',''), d.get('unit', 'Kg'), d.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('fertilizer'))

# ==========================================
# 📦 출하내역 관리 및 통계 엔진
# ==========================================
# ==========================================
# 1. 화면 출력 엔진 (회장님의 원본 완벽 복원 -> 500 에러 차단)
# ==========================================
@app.route('/shipment')
@login_required
def shipment():
    import sqlite3, json, traceback  # traceback 임포트 추가

    try: # ✨ 여기서부터 메인 로직을 try로 감싸줍니다!
        uid = get_uid()
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()

        # 원본 코드 그대로 복구 (전체 데이터 가져오기)
        c.execute("SELECT * FROM shipments WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
        s = c.fetchall()

        c.execute("SELECT substr(date, 1, 7) as month, SUM(quantity), SUM(sales_amount), SUM(shipping_cost) FROM shipments WHERE user_id = ? GROUP BY month ORDER BY month DESC", (uid,))
        ms = c.fetchall()

        try:
            c.execute("SELECT crop_start FROM users WHERE id = ?", (uid,))
            p_row = c.fetchone()

            # p_row와 p_row이 모두 존재하는지 확인하도록 안전하게 수정
            if p_row and p_row:
                clean_date = str(p_row).replace("'", "").replace('"', '').replace("(", "").replace(")", "").replace(",", "").strip()
                season_start = clean_date if clean_date else "2025-09-01"
            else:
                season_start = "2025-09-01"
        except Exception as e:
            season_start = "2025-09-01"

        conn.close()

        shipments_json = []
        for row in s:
            # 회장님 원본 방식 그대로 8개 변수에 담기!
            r_id, r_uid, r_date, r_dest, r_qty, r_bid, r_sales, r_cost = row
            shipments_json.append({
                'id': r_id, 'date': r_date, 'destination': r_dest,
                'quantity': float(r_qty) if r_qty else 0.0,
                'bid_price': float(r_bid) if r_bid else 0.0,
                'sales_amount': float(r_sales) if r_sales else 0.0,
                'shipping_cost': float(r_cost) if r_cost else 0.0
            })

        from flask import render_template
        return render_template('shipment.html', shipments=s, monthly_stats=ms, shipments_json=json.dumps(shipments_json), season_start=season_start)

    except Exception as e: # ✨ 누락되었던 try와 마침내 짝을 이룹니다!
        # 🚨 파이썬이 또 기절하면 500 에러창 대신, 화면에 "왜 기절했는지" 범인을 띄웁니다!!
        err_msg = traceback.format_exc()
        return f"<div style='padding:20px; font-size:16px; color:red;'><b>🚨 파이썬 서버 에러 발생!!</b><br>회장님! 이 화면을 캡처해서 저에게 보여주십시오!<br><br><pre>{err_msg}</pre></div>"

# ==========================================
# 📊 엑셀 흡수 엔진 (에러 원천 차단 & 1:1 정밀 주차)
# ==========================================
@app.route('/upload_shipment_csv', methods=['POST'])
@login_required
def upload_shipment_csv():
    import sqlite3, re, io, csv, traceback
    from flask import request, flash, redirect, url_for

    try:
        uid_str = str(get_uid())
        safe_uid = int(''.join(ch for ch in uid_str if ch.isdigit()) or 1)

        file = request.files.get('csv_file')
        if not file or file.filename == '':
            flash("파일이 없습니다.")
            return redirect(url_for('shipment') + '#register')

        raw_data = file.stream.read()
        if not raw_data:
            return redirect(url_for('shipment') + '#register')

        text_data = ""
        for enc in ['utf-8-sig', 'cp949', 'utf-8', 'euc-kr']:
            try:
                text_data = raw_data.decode(enc)
                break
            except: pass

        text_data = str(text_data).replace('\t', ',').replace(';', ',').replace('\r\n', '\n').replace('\r', '\n')
        stream = io.StringIO(text_data)
        reader = csv.reader(stream)

        conn = sqlite3.connect(DB_FILE, timeout=20.0)
        try:
            c = conn.cursor()

            # 깨진 날짜 찌꺼기 청소
            c.execute("DELETE FROM shipments WHERE date LIKE '%]%' OR date LIKE '%''%' OR length(date) != 10")

            success_count = 0
            for row in reader:
                if not row: continue

                # 혹시 리스트가 아니면 강제로 리스트로 만듦
                if not isinstance(row, list): row = [row]

                # 엑셀이 한 줄로 묶었을 때 분리
                if len(row) == 1 and ',' in str(row):
                    try: row = list(csv.reader([str(row)]))
                    except: pass

                if len(row) < 5: continue

                # ✨ 핵심 방어막: 모든 칸을 무조건 '순수 문자열(str)'로 강제 변환하여 TypeError 완벽 차단!
                clean_row = []
                for x in row:
                    clean_x = str(x).replace('=', '').replace('"', '').replace("'", '').strip()
                    clean_row.append(clean_x)

                def get_val(idx):
                    return clean_row[idx] if idx < len(clean_row) else ""

                raw_date = get_val(0)
                if '일자' in raw_date or '수량' in raw_date: continue

                # 날짜 추출
                nums = re.sub(r'[^0-9]', '', str(raw_date))
                if len(nums) >= 8: date_val = f"{nums[:4]}-{nums[4:6]}-{nums[6:8]}"
                elif len(nums) == 6: date_val = f"20{nums[:2]}-{nums[2:4]}-{nums[4:6]}"
                else: continue

                # 출하처 누락(밀림) 감지 (강제로 str을 씌워서 정밀 검사)
                col2_str = get_val(1)
                is_shifted = not bool(re.search(r'[가-힣a-zA-Z]', str(col2_str)))

                def get_num(val):
                    try: return float(re.sub(r'[^0-9.-]', '', str(val)) or 0.0)
                    except: return 0.0

                # 밀림 여부에 따라 정확하게 1:1 이름표 매칭
                if is_shifted:
                    dest = "한국청과"
                    qty = get_num(get_val(1))
                    price = get_num(get_val(2))
                    sales = get_num(get_val(3))
                    cost = get_num(get_val(4))
                else:
                    dest = get_val(1) if get_val(1) else "한국청과"
                    qty = get_num(get_val(2))
                    price = get_num(get_val(3))
                    sales = get_num(get_val(4))
                    cost = get_num(get_val(5))

                if qty == 0 and sales == 0: continue

                # 중복 데이터 지우고 정확한 기둥 이름에 1:1로 꽂아 넣기!
                c.execute("DELETE FROM shipments WHERE user_id=? AND date=? AND destination=?", (safe_uid, date_val, dest))

                c.execute("""
                    INSERT INTO shipments (user_id, date, destination, quantity, bid_price, sales_amount, shipping_cost)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (safe_uid, date_val, dest, qty, price, sales, cost))

                success_count += 1

            conn.commit()
            if success_count > 0:
                flash(f"성공! 총 {success_count}건을 제자리에 완벽하게 저장했습니다! 🚀")
            else:
                flash("데이터를 찾을 수 없습니다. 형식을 확인해주세요.")

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    except Exception as e:
        err_msg = traceback.format_exc().split('\n')
        short_err = " | ".join(err_msg[-3:])
        flash(f"오류 발생: {short_err}")

    return redirect(url_for('shipment') + '#register')

# ==========================================
# 💾 수동 입력 저장 엔진 (Not Found 해결!)
# ==========================================
@app.route('/add_shipment', methods=['POST'])
@login_required
def add_shipment():
    import sqlite3
    from flask import request, flash, redirect, url_for

    try:
        uid = get_uid()

        # 화면에서 입력한 데이터 받아오기
        date = request.form.get('date')
        destination = request.form.get('destination', '한국청과') # 출하처 안 쓰면 기본값 한국청과
        quantity = request.form.get('quantity', 0, type=float)
        bid_price = request.form.get('bid_price', 0, type=float)
        sales_amount = request.form.get('sales_amount', 0, type=float)
        shipping_cost = request.form.get('shipping_cost', 0, type=float)

        # DB에 쏙 집어넣기
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        c.execute("""
            INSERT INTO shipments (user_id, date, destination, quantity, bid_price, sales_amount, shipping_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (uid, date, destination, quantity, bid_price, sales_amount, shipping_cost))

        conn.commit()
        conn.close()

        flash("출하 내역이 성공적으로 쏙! 저장되었습니다. 🚀")

    except Exception as e:
        flash(f"저장 중 오류가 발생했습니다: {str(e)}")

    return redirect(url_for('shipment') + '#register')

# ==========================================
# 🗑️ 개별 내역 삭제 엔진 (Not Found 해결!)
# ==========================================
@app.route('/delete_shipment', methods=['POST'])
@login_required
def delete_shipment():
    import sqlite3
    from flask import request, flash, redirect, url_for

    try:
        uid = get_uid()
        shipment_id = request.form.get('id')

        if shipment_id:
            conn = sqlite3.connect(DB_FILE, timeout=10.0)
            c = conn.cursor()
            # 회장님의 데이터가 맞는지 확인하고 안전하게 삭제!
            c.execute("DELETE FROM shipments WHERE id = ? AND user_id = ?", (shipment_id, uid))
            conn.commit()
            conn.close()

            flash("🗑️ 선택하신 출하 내역이 깔끔하게 삭제되었습니다!")
        else:
            flash("삭제할 대상을 찾을 수 없습니다.")

    except Exception as e:
        flash(f"삭제 중 오류가 발생했습니다: {str(e)}")

    return redirect(url_for('shipment'))

# ==========================================
# ✏️ 개별 내역 수정 엔진 (Not Found 완벽 해결!)
# ==========================================
@app.route('/edit_shipment', methods=['POST'])
@login_required
def edit_shipment():
    import sqlite3
    from flask import request, flash, redirect, url_for

    try:
        uid = get_uid()

        # 수정할 데이터 받아오기
        shipment_id = request.form.get('id')
        date = request.form.get('date')
        destination = request.form.get('destination', '한국청과') # 출하처 지워지면 기본값 한국청과
        quantity = request.form.get('quantity', 0, type=float)
        bid_price = request.form.get('bid_price', 0, type=float)
        sales_amount = request.form.get('sales_amount', 0, type=float)
        shipping_cost = request.form.get('shipping_cost', 0, type=float)

        # DB에서 해당 내역만 쏙 찾아서 예쁘게 업데이트!
        if shipment_id:
            conn = sqlite3.connect(DB_FILE, timeout=10.0)
            c = conn.cursor()
            c.execute("""
                UPDATE shipments
                SET date=?, destination=?, quantity=?, bid_price=?, sales_amount=?, shipping_cost=?
                WHERE id=? AND user_id=?
            """, (date, destination, quantity, bid_price, sales_amount, shipping_cost, shipment_id, uid))

            conn.commit()
            conn.close()

            flash("✏️ 출하 내역이 성공적으로 수정되었습니다! 🚀")
        else:
            flash("수정할 대상을 찾을 수 없습니다.")

    except Exception as e:
        flash(f"수정 중 오류가 발생했습니다: {str(e)}")

    return redirect(url_for('shipment'))

@app.route('/seed')
@login_required
def seed():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM seeds WHERE user_id = ? ORDER BY date DESC", (uid,)); s = c.fetchall()
    c.execute("SELECT category, SUM(cost) FROM seeds WHERE user_id = ? GROUP BY category", (uid,)); sr = c.fetchall()
    conn.close(); return render_template('seed.html', seeds=s, seed_total=sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='종자']), seedling_total=sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='종묘']))

@app.route('/add_seed', methods=['POST'])
@login_required
def add_seed():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO seeds (user_id, date, category, quantity, unit, cost, note) VALUES (?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('category'), d.get('quantity',0), d.get('unit'), d.get('cost',0), d.get('note','')))
    conn.commit(); conn.close(); return redirect(url_for('seed'))

@app.route('/edit_seed', methods=['POST'])
@login_required
def edit_seed():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE seeds SET date=?, category=?, quantity=?, unit=?, cost=?, note=? WHERE id=? AND user_id=?", (d.get('date'), d.get('category'), d.get('quantity',0), d.get('unit'), d.get('cost',0), d.get('note',''), d.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('seed'))

@app.route('/fuel')
@login_required
def fuel():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM fuels WHERE user_id = ? ORDER BY date DESC", (uid,)); f = c.fetchall()
    c.execute("SELECT fuel_type, SUM(cost) FROM fuels WHERE user_id = ? GROUP BY fuel_type", (uid,)); sr = c.fetchall()
    conn.close()
    return render_template('fuel.html', fuels=f, diesel_total=sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='경유']), gasoline_total=sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='휘발유']), kerosene_total=sum([safe_int(r[ 1 ]) for r in sr if r[ 0 ]=='등유']))

@app.route('/add_fuel', methods=['POST'])
@login_required
def add_fuel():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO fuels (user_id, date, fuel_type, quantity, cost) VALUES (?,?,?,?,?)", (uid, d.get('date'), d.get('fuel_type'), d.get('quantity',0), d.get('cost',0)))
    conn.commit(); conn.close(); return redirect(url_for('fuel'))

@app.route('/edit_fuel', methods=['POST'])
@login_required
def edit_fuel():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE fuels SET date=?, fuel_type=?, quantity=?, cost=? WHERE id=? AND user_id=?", (d.get('date'), d.get('fuel_type'), d.get('quantity',0), d.get('cost',0), d.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('fuel'))

@app.route('/electricity')
@login_required
def electricity():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM electricity WHERE user_id=? ORDER BY date DESC", (uid,)); r = c.fetchall()
    c.execute("SELECT SUM(cost), SUM(usage_kw) FROM electricity WHERE user_id=?", (uid,)); st = c.fetchone()
    conn.close()
    return render_template('electricity.html', records=r, total_cost=safe_int(st[ 0 ]) if st else 0, total_kw=safe_int(st[ 1 ]) if st else 0)

@app.route('/add_electricity', methods=['POST'])
@login_required
def add_electricity():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO electricity (user_id, date, usage_kw, cost) VALUES (?,?,?,?)", (uid, d.get('date'), d.get('usage_kw',0), d.get('cost',0)))
    conn.commit(); conn.close(); return redirect(url_for('electricity'))

@app.route('/edit_electricity', methods=['POST'])
@login_required
def edit_electricity():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE electricity SET date=?, usage_kw=?, cost=? WHERE id=? AND user_id=?", (d.get('date'), d.get('usage_kw',0), d.get('cost',0), d.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('electricity'))

@app.route('/other_cost')
@login_required
def other_cost():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM other_costs WHERE user_id=? ORDER BY date DESC", (uid,)); r = c.fetchall()
    c.execute("SELECT SUM(cost) FROM other_costs WHERE user_id=?", (uid,)); st = c.fetchone()
    conn.close()
    return render_template('other_cost.html', records=r, total_cost=safe_int(st[ 0 ]) if st else 0)

@app.route('/add_other_cost', methods=['POST'])
@login_required
def add_other_cost():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO other_costs (user_id, date, material_name, quantity, unit, cost, replace_cycle, burden_ratio) VALUES (?,?,?,?,?,?,?,?)", (uid, d.get('date'), d.get('material_name'), d.get('quantity',0), d.get('unit'), d.get('cost',0), d.get('replace_cycle',''), d.get('burden_ratio',0)))
    conn.commit(); conn.close(); return redirect(url_for('other_cost'))

@app.route('/edit_other_cost', methods=['POST'])
@login_required
def edit_other_cost():
    d = request.form; uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE other_costs SET date=?, material_name=?, quantity=?, unit=?, cost=?, replace_cycle=?, burden_ratio=? WHERE id=? AND user_id=?", (d.get('date'), d.get('material_name'), d.get('quantity',0), d.get('unit'), d.get('cost',0), d.get('replace_cycle',''), d.get('burden_ratio',0), d.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('other_cost'))

@app.route('/memo')
@login_required
def memo():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT * FROM memos WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,)); records = c.fetchall(); conn.close()
    return render_template('memo.html', records=records)

@app.route('/add_memo', methods=['POST'])
@login_required
def add_memo():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("INSERT INTO memos (user_id, date, content) VALUES (?,?,?)", (uid, request.form.get('date'), request.form.get('content')))
    conn.commit(); conn.close(); return redirect(url_for('memo'))

@app.route('/edit_memo', methods=['POST'])
@login_required
def edit_memo():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("UPDATE memos SET date=?, content=? WHERE id=? AND user_id=?", (request.form.get('date'), request.form.get('content'), request.form.get('id'), uid))
    conn.commit(); conn.close(); return redirect(url_for('memo'))

@app.route('/admin_page')
@login_required
def admin_page():
    if current_user.username != 'admin': flash('관리자 전용'); return redirect(url_for('home'))
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT id, username, farm_name, crop_start, crop_end, last_active, COALESCE(is_approved, 0) FROM users ORDER BY id ASC"); users_list = c.fetchall()
    user_stats = []; now = datetime.now(KST).replace(tzinfo=None)
    for u in users_list:
        target_uid = u[ 0 ]
        c.execute("SELECT COUNT(*) FROM records WHERE user_id=?", (target_uid,)); rc = safe_int(c.fetchone())
        c.execute("SELECT COUNT(*) FROM irrigations WHERE user_id=?", (target_uid,)); ic = safe_int(c.fetchone())
        last_active_str = u[ 5 ]; is_online = False; last_active_display = "기록없음"
        if last_active_str:
            try:
                last_active_dt = datetime.strptime(last_active_str, '%Y-%m-%d %H:%M:%S')
                if (now - last_active_dt).total_seconds() < 600: is_online = True
                last_active_display = last_active_str[5:16]
            except Exception: pass
        user_stats.append({'id': u[ 0 ], 'username': u[ 1 ], 'farm_name': u[ 2 ] or '미설정', 'crop': f"{u[ 3 ]} ~ {u[ 4 ]}" if u[ 3 ] else '미설정', 'data_cnt': rc + ic, 'is_online': is_online, 'last_active': last_active_display, 'is_approved': u[ 6 ]})
    conn.close(); return render_template('admin.html', users=user_stats)

@app.route('/delete_user_admin/<int:user_id>')
@login_required
def delete_user_admin(user_id):
    if current_user.username != 'admin': return redirect(url_for('home'))
    uid = get_uid()
    if user_id == uid: flash('최고 관리자 본인 계정은 삭제 불가'); return redirect(url_for('admin_page'))
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit(); conn.close(); flash('삭제 완료'); return redirect(url_for('admin_page'))

@app.route('/toggle_approval/<int:user_id>')
@login_required
def toggle_approval(user_id):
    if current_user.username != 'admin': return redirect(url_for('home'))
    conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    c.execute("SELECT COALESCE(is_approved, 0) FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if row:
        new_status = 0 if row[ 0 ] == 1 else 1
        c.execute("UPDATE users SET is_approved=? WHERE id=?", (new_status, user_id))
        conn.commit()
    conn.close()
    return redirect(url_for('admin_page'))

@app.route('/analysis', methods=['GET', 'POST'])
@login_required
def analysis():
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    if request.method == 'POST':
        manual_data_json = json.dumps(dict(request.form))
        c.execute("SELECT id FROM income_manual WHERE user_id=?", (uid,))
        if c.fetchone(): c.execute("UPDATE income_manual SET data=? WHERE user_id=?", (manual_data_json, uid))
        else: c.execute("INSERT INTO income_manual (user_id, data) VALUES (?, ?)", (uid, manual_data_json))
        conn.commit(); flash("저장됨"); return redirect(url_for('analysis'))

    c.execute("SELECT SUM(sales_amount), SUM(quantity) FROM shipments WHERE user_id=?", (uid,)); ship = c.fetchone()
    auto_main_amt = safe_int(ship[ 0 ]) if ship else 0; auto_main_qty = safe_int(ship[ 1 ]) if ship else 0
    c.execute("SELECT SUM(cost) FROM seeds WHERE user_id=?", (uid,)); auto_seed = safe_int(c.fetchone())
    c.execute("SELECT SUM(cost) FROM fertilizers WHERE user_id=? AND category='무기질비료'", (uid,)); auto_inorg = safe_int(c.fetchone())
    c.execute("SELECT SUM(cost) FROM fertilizers WHERE user_id=? AND category='유기질비료'", (uid,)); auto_org = safe_int(c.fetchone())
    c.execute("SELECT SUM(price) FROM records WHERE user_id=?", (uid,)); auto_pest = safe_int(c.fetchone())
    c.execute("SELECT SUM(cost) FROM fuels WHERE user_id=?", (uid,)); fuel = safe_int(c.fetchone())
    c.execute("SELECT SUM(cost) FROM electricity WHERE user_id=?", (uid,)); elec = safe_int(c.fetchone()); auto_wl = fuel + elec
    c.execute("SELECT SUM(cost) FROM other_costs WHERE user_id=?", (uid,)); auto_other = safe_int(c.fetchone())

    auto_data = {'main_amt': auto_main_amt, 'main_qty': auto_main_qty, 'seed': auto_seed, 'inorg': auto_inorg, 'org': auto_org, 'pest': auto_pest, 'wl': auto_wl, 'other': auto_other}

    c.execute("SELECT data FROM income_manual WHERE user_id=?", (uid,)); m_row = c.fetchone()
    manual_data = json.loads(m_row[ 0 ]) if m_row else {}
    conn.close()
    return render_template('analysis.html', auto=auto_data, manual=manual_data)

@app.route('/delete/<type>/<int:id>')
@login_required
def delete(type, id):
    uid = get_uid(); conn = sqlite3.connect(DB_FILE, timeout=10.0); c = conn.cursor()
    tables = {"db": "pesticides", "record": "records", "shipment": "shipments", "seed": "seeds", "fertilizer": "fertilizers", "fuel": "fuels", "electricity": "electricity", "other_cost": "other_costs", "irrigation": "irrigations", "memo": "memos", "nutrient_db": "nutrient_list", "farm": "farm_info", "growth": "growth_data"}
    if type in tables:
        if type in ['db', 'nutrient_db']: c.execute(f"DELETE FROM {tables[type]} WHERE id=?", (id,))
        else: c.execute(f"DELETE FROM {tables[type]} WHERE id=? AND user_id=?", (id, uid))
    conn.commit(); conn.close()
    return redirect(request.referrer or url_for('home'))

# ==========================================
# 💬 2. 채팅방 엔진
# ==========================================
@app.route('/chat')
@login_required
@approved_required
def chat():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, content TEXT, created_at TEXT)''')
    add_col_safe(c, 'chat_messages', 'is_notice', 'INTEGER DEFAULT 0')
    conn.commit()
    conn.close()
    return render_template('chat.html')

@app.route('/send_chat', methods=('POST',))
@login_required
@approved_required
def send_chat():
    uid = get_uid()
    content = request.form.get('content')
    is_notice = 1 if request.form.get('is_notice') == 'true' else 0
    if content and content.strip():
        now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (user_id, content, created_at, is_notice) VALUES (?, ?, ?, ?)", (uid, content.strip(), now_str, is_notice))
        conn.commit()
        conn.close()
    return 'OK'

@app.route('/get_chat')
@login_required
@approved_required
def get_chat():
    uid = get_uid()
    last_id_raw = request.args.get('last_id', '0')
    last_id = int(last_id_raw) if last_id_raw.isdigit() else 0
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT c.id, c.content, c.created_at, u.username, u.farm_name, c.user_id FROM chat_messages c JOIN users u ON c.user_id = u.id WHERE c.id > ? ORDER BY c.id ASC LIMIT 100", (last_id,))
    msgs = c.fetchall()
    conn.close()

    result = list()
    for m in msgs:
        (m_id, m_content, m_created_at, m_username, m_farm_name, m_user_id) = m
        name = m_farm_name if m_farm_name else m_username
        is_me = True if str(m_user_id) == str(uid) else False
        time_str = m_created_at[11:16]
        result.append(dict(id=m_id, content=m_content, time=time_str, name=name, is_me=is_me))

    return Response(json.dumps(result), mimetype='application/json')

@app.route('/get_chat_info')
@login_required
@approved_required
def get_chat_info():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT c.content, u.username, u.farm_name, c.created_at FROM chat_messages c JOIN users u ON c.user_id = u.id WHERE c.is_notice = 1 ORDER BY c.id DESC LIMIT 1")
    notice_row = c.fetchone()
    notice = None
    if notice_row:
        (n_content, n_uname, n_fname, n_time) = notice_row
        name = n_fname if n_fname else n_uname
        notice = dict(content=n_content, name=name, time=n_time)

    now = datetime.now(KST).replace(tzinfo=None)
    c.execute("SELECT username, farm_name, last_active FROM users WHERE last_active IS NOT NULL")
    all_users = c.fetchall()
    active_users = list()

    for u in all_users:
        (u_name, f_name, last_act) = u
        try:
            act_dt = datetime.strptime(last_act, '%Y-%m-%d %H:%M:%S')
            if (now - act_dt).total_seconds() < 300:
                active_users.append(f_name if f_name else u_name)
        except Exception:
            pass

    conn.close()
    return Response(json.dumps(dict(notice=notice, active_users=active_users)), mimetype='application/json')

# ==========================================
# 📊 엑셀 다운로드 엔진 모음
# ==========================================

@app.route('/export_growth')
@login_required
def export_growth():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, house_name, plant_num, fruiting_habit, stem_type, main_node, sub_node, internode_len, stem_thick, leaf_length, leaf_width, leaf_status, female_flower, harvest, fruit_status, fruit_curved, fruit_straight, fruit_width, avg_temp, avg_humidity, dif, acc_temp, note FROM growth_data WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['조사일자', '동명/구역', '조사주수', '착과습성', '초세', '주지마디수', '측지마디수', '절간장(cm)', '경출(mm)', '엽장(cm)', '엽폭(cm)', '엽상태', '암꽃수', '수확과수', '과실상태', '곡과수', '정상과수', '과폭(cm)', '평균온도(℃)', '평균습도(%)', 'DIF', '적산온도', '비고'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=growth_data.csv"})

@app.route('/export_pesticide')
@login_required
def export_pesticide():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, area, crop, name, mech, pest, count, amount, price FROM records WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['방제일자', '구역/동', '작물', '농약/자재명', '작용기작', '적용병해충', '사용량', '총용량', '금액(원)'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=pesticide_records.csv"})

@app.route('/export_irrigation')
@login_required
def export_irrigation():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, area, water_amount, nutrients, nutrient_amount, nutrient_unit FROM irrigations WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['관주일자', '구역/동', '관주량', '영양제/비료명', '투입량', '단위'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=irrigation_records.csv"})

@app.route('/export_fertilizer')
@login_required
def export_fertilizer():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, category, type, product_name, quantity, unit, cost FROM fertilizers WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['일자', '분류', '비료종류', '제품명', '수량', '단위', '비용(원)'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=fertilizer_records.csv"})

@app.route('/export_shipment')
@login_required
def export_shipment():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, destination, quantity, bid_price, sales_amount, shipping_cost FROM shipments WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['출하일자', '출하처', '수량(박스)', '낙찰단가(원)', '매출액(원)', '운송비/수수료(원)'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=shipment_records.csv"})

@app.route('/export_seed')
@login_required
def export_seed():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, category, quantity, unit, cost, note FROM seeds WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['일자', '분류(종자/종묘)', '수량', '단위', '비용(원)', '비고'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=seed_records.csv"})

@app.route('/export_fuel')
@login_required
def export_fuel():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, fuel_type, quantity, cost FROM fuels WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['일자', '유종', '수량(L)', '비용(원)'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=fuel_records.csv"})

@app.route('/export_electricity')
@login_required
def export_electricity():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, usage_kw, cost FROM electricity WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['청구년월/일자', '사용량(kWh)', '비용(원)'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=electricity_records.csv"})

@app.route('/export_other_cost')
@login_required
def export_other_cost():
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT date, material_name, quantity, unit, cost, replace_cycle, burden_ratio FROM other_costs WHERE user_id = ? ORDER BY date DESC, id DESC", (uid,))
    records = c.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['일자', '자재/내역명', '수량', '단위', '비용(원)', '교체주기', '자부담비율(%)'])
    for r in records: cw.writerow(r)
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=other_costs_records.csv"})

# ==========================================
# 🛒 공동구매 게시판 전용 엔진 (타임머신 복구용 완전체)
# ==========================================

def init_gp_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS group_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, content TEXT, created_at TEXT, views INTEGER DEFAULT 0, status TEXT DEFAULT '진행중')''')
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER, content TEXT, created_at TEXT)''')
    add_col_safe(c, 'group_purchases', 'items', 'TEXT')
    add_col_safe(c, 'purchase_comments', 'parent_id', 'INTEGER DEFAULT 0')
    add_col_safe(c, 'purchase_comments', 'item_orders', 'TEXT')
    conn.commit()
    conn.close()

# 서버 켜질 때 DB 자동 점검
init_gp_db()

@app.route('/group_purchase')
@login_required
@approved_required
def group_purchase():
    search = request.args.get('search', '')
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    query = "SELECT g.id, g.title, u.username, u.farm_name, g.created_at, g.views, g.user_id, g.status FROM group_purchases g JOIN users u ON g.user_id = u.id"
    params = list()
    if search:
        query += " WHERE g.title LIKE ?"
        params.append(f'%{search}%')
    query += " ORDER BY g.id DESC"
    c.execute(query, params)
    posts = c.fetchall()
    conn.close()
    return render_template('group_purchase.html', posts=posts, search=search)

@app.route('/group_purchase_view/<int:post_id>')
@login_required
@approved_required
def group_purchase_view(post_id):
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("UPDATE group_purchases SET views = views + 1 WHERE id = ?", (post_id,))
    conn.commit()
    c.execute("SELECT g.id, g.title, g.content, u.username, u.farm_name, g.created_at, g.views, g.user_id, g.status, g.items FROM group_purchases g JOIN users u ON g.user_id = u.id WHERE g.id = ?", (post_id,))
    post = c.fetchone()
    c.execute("SELECT c.id, c.content, c.created_at, u.username, u.farm_name, c.user_id, c.parent_id, c.item_orders FROM purchase_comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? ORDER BY c.id ASC", (post_id,))
    all_comments = c.fetchall()
    conn.close()

    if not post:
        return redirect(url_for('group_purchase'))

    # ✨ 대괄호 에러 원천 차단 (튜플 언패킹)
    (g_id, g_title, g_content, g_uname, g_fname, g_created_at, g_views, g_uid, g_status, g_items) = post
    can_edit_post = (str(g_uid) == str(uid) or current_user.username == 'admin')

    post_items = list()
    if g_items:
        try:
            post_items = json.loads(g_items)
        except Exception: pass

    parent_comments = list()
    replies = dict()
    totals = dict()
    for item in post_items: totals[item] = 0

    has_ordered = False
    my_comment = None

    for cmt in all_comments:
        # ✨ 대괄호 에러 원천 차단 (튜플 언패킹)
        (c_id, c_content, c_created_at, c_uname, c_fname, c_uid, c_pid, c_orders) = cmt
        p_id = int(c_pid) if c_pid else 0

        if p_id == 0:
            parent_comments.append(cmt)
            if str(c_uid) == str(uid):
                has_ordered = True
                my_comment = cmt
            if c_orders:
                try:
                    orders = json.loads(c_orders)
                    if isinstance(orders, str): orders = json.loads(orders)
                    for k, v in orders.items():
                        if k in totals: totals[k] += float(v)
                        else: totals[k] = float(v)
                except Exception: pass
        else:
            if p_id not in replies: replies[p_id] = list()
            replies.get(p_id).append(cmt)

    return render_template('group_purchase_view.html', post=post, post_items=post_items, comments=parent_comments, replies=replies, totals=totals, has_ordered=has_ordered, my_comment=my_comment, can_edit_post=can_edit_post, uid_str=str(uid))

@app.route('/group_purchase_write', methods=('GET', 'POST'))
@login_required
@approved_required
def group_purchase_write():
    uid = get_uid()
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        status = request.form.get('status', '진행중')
        items_json = request.form.get('items_json', '[]')

        now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M')
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()
        c.execute("INSERT INTO group_purchases (user_id, title, content, created_at, views, status, items) VALUES (?, ?, ?, ?, 0, ?, ?)", (uid, title, content, now_str, status, items_json))
        conn.commit()
        conn.close()
        return redirect(url_for('group_purchase'))
    return render_template('group_purchase_form.html', post=None, post_items=list())

@app.route('/group_purchase_edit/<int:post_id>', methods=('GET', 'POST'))
@login_required
@approved_required
def group_purchase_edit(post_id):
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT id, user_id, title, content, status, items FROM group_purchases WHERE id = ?", (post_id,))
    post = c.fetchone()

    if not post:
        conn.close(); flash('수정 권한이 없습니다.'); return redirect(url_for('group_purchase_view', post_id=post_id))

    (p_id, p_uid, p_title, p_content, p_status, p_items) = post

    if str(p_uid) != str(uid) and current_user.username != 'admin':
        conn.close(); flash('수정 권한이 없습니다.'); return redirect(url_for('group_purchase_view', post_id=post_id))

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        status = request.form.get('status', '진행중')
        items_json = request.form.get('items_json', '[]')

        c.execute("UPDATE group_purchases SET title=?, content=?, status=?, items=? WHERE id=?", (title, content, status, items_json, post_id))
        conn.commit(); conn.close(); return redirect(url_for('group_purchase_view', post_id=post_id))

    post_items = list()
    if p_items:
        try: post_items = json.loads(p_items)
        except Exception: pass
    conn.close()
    return render_template('group_purchase_form.html', post=post, post_items=post_items)

@app.route('/add_purchase_comment/<int:post_id>', methods=('POST',))
@login_required
@approved_required
def add_purchase_comment(post_id):
    uid = get_uid()
    parent_id = int(request.form.get('parent_id', 0)) if request.form.get('parent_id') else 0
    content = request.form.get('content', '')
    orders_json = request.form.get('orders_json', '{}')
    now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M')

    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("INSERT INTO purchase_comments (post_id, user_id, content, created_at, parent_id, item_orders) VALUES (?, ?, ?, ?, ?, ?)", (post_id, uid, content.strip(), now_str, parent_id, orders_json))
    conn.commit(); conn.close()
    return redirect(url_for('group_purchase_view', post_id=post_id))

@app.route('/edit_purchase_comment/<int:comment_id>', methods=('POST',))
@login_required
@approved_required
def edit_purchase_comment(comment_id):
    uid = get_uid()
    content = request.form.get('content', '')
    post_id_raw = request.form.get('post_id', '0')
    post_id = int(post_id_raw) if post_id_raw.isdigit() else 0
    orders_json = request.form.get('orders_json', '{}')

    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT user_id FROM purchase_comments WHERE id=?", (comment_id,))
    row = c.fetchone()

    if row:
        (r_uid,) = row
        if str(r_uid) == str(uid) or current_user.username == 'admin':
            c.execute("UPDATE purchase_comments SET content=?, item_orders=? WHERE id=?", (content.strip(), orders_json, comment_id))
            conn.commit()
    conn.close()
    if post_id: return redirect(url_for('group_purchase_view', post_id=post_id))
    return redirect(url_for('group_purchase'))

@app.route('/group_purchase_delete/<int:post_id>')
@login_required
@approved_required
def group_purchase_delete(post_id):
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT user_id FROM group_purchases WHERE id = ?", (post_id,))
    post = c.fetchone()
    if post:
        (p_uid,) = post
        if str(p_uid) == str(uid) or current_user.username == 'admin':
            c.execute("DELETE FROM group_purchases WHERE id = ?", (post_id,))
            c.execute("DELETE FROM purchase_comments WHERE post_id = ?", (post_id,))
            conn.commit()
    conn.close()
    return redirect(url_for('group_purchase'))

@app.route('/delete_purchase_comment/<int:comment_id>')
@login_required
@approved_required
def delete_purchase_comment(comment_id):
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    c.execute("SELECT user_id, post_id FROM purchase_comments WHERE id = ?", (comment_id,))
    comment = c.fetchone()
    if comment:
        (c_uid, c_post_id) = comment
        if str(c_uid) == str(uid) or current_user.username == 'admin':
            c.execute("DELETE FROM purchase_comments WHERE id = ?", (comment_id,))
            c.execute("DELETE FROM purchase_comments WHERE parent_id = ?", (comment_id,))
            conn.commit()
        post_id = c_post_id
    else:
        post_id = 0
    conn.close()
    if post_id: return redirect(url_for('group_purchase_view', post_id=post_id))
    return redirect(url_for('group_purchase'))

# ==========================================
# 📥 공동구매 엑셀 다운로드 엔진 (주최자 전용, 통계 포함)
# ==========================================
@app.route('/export_group_purchase/<int:post_id>')
@login_required
@approved_required
def export_group_purchase(post_id):
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()

    # 1. 글 정보 및 권한 확인 (주최자 또는 관리자만 가능)
    c.execute("SELECT user_id, title, items FROM group_purchases WHERE id = ?", (post_id,))
    post = c.fetchone()

    is_admin = current_user.username == 'admin'
    if not post or (str(post) != str(uid) and not is_admin):
        conn.close()
        flash('엑셀 다운로드 권한이 없습니다.')
        return redirect(url_for('group_purchase_view', post_id=post_id))

    (p_uid, p_title, p_items) = post

    # 2. 참여자 댓글(주문내역) 모두 가져오기 (대댓글 제외)
    c.execute("SELECT u.username, u.farm_name, c.item_orders, c.content, c.created_at FROM purchase_comments c JOIN users u ON c.user_id = u.id WHERE c.post_id = ? AND c.parent_id = 0 ORDER BY c.id ASC", (post_id,))
    orders_raw = c.fetchall()
    conn.close()

    # 3. 품목 파싱 및 합산(통계) 준비
    post_items = list()
    if p_items:
        try: post_items = json.loads(p_items)
        except Exception: pass

    totals = dict()
    for item in post_items: totals[item] = 0

    parsed_orders = list()
    for row in orders_raw:
        (r_uname, r_fname, r_orders, r_content, r_time) = row
        name = r_fname if r_fname else r_uname
        order_dict = dict()
        if r_orders:
            try:
                order_dict = json.loads(r_orders)
                if isinstance(order_dict, str): order_dict = json.loads(order_dict)
                for k, v in order_dict.items():
                    if k in totals: totals[k] += float(v)
                    else: totals[k] = float(v)
            except Exception: pass

        # 엑셀 칸에 예쁘게 넣기 위해 문자열로 조립 (예: "지주대 5개, 집게 2개")
        order_str_list = list()
        for k, v in order_dict.items():
            order_str_list.append(f"{k} {v}개")
        order_str = ", ".join(order_str_list)

        parsed_orders.append((name, order_str, r_content, r_time))

    # 4. 엑셀(CSV) 파일 만들기
    si = StringIO()
    cw = csv.writer(si)

    cw.writerow((f"--- 공동구매 정산 내역: {p_title} ---",))
    cw.writerow(('',))

    # [통계 영역] 품목별 총합
    cw.writerow(('--- 품목별 총 신청 수량 (통계) ---',))
    cw.writerow(('품목명', '총 수량 합계'))
    for item, total in totals.items():
        # 소수점 끝자리가 .0이면 깔끔하게 정수로 표시
        display_total = int(total) if total == int(total) else total
        cw.writerow((item, f"{display_total}개"))

    cw.writerow(('',))

    # [상세 영역] 참여자별 주문 내역
    cw.writerow(('--- 참여자별 상세 신청 내역 ---',))
    cw.writerow(('신청자명', '신청품목 및 수량', '남긴 메시지/주소', '신청일시'))
    for po in parsed_orders:
        cw.writerow(po)

    filename = f"group_purchase_result_{post_id}.csv"
    return Response(si.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename={filename}"})

# ==========================================
# 🗑️ 출하내역 [전체 삭제] 핵폭탄 엔진
# ==========================================
@app.route('/delete_all_shipments', methods=['POST'])
@login_required
def delete_all_shipments():
    import sqlite3
    from flask import flash, redirect, url_for

    try:
        uid_str = str(get_uid())
        safe_uid = int(''.join(ch for ch in uid_str if ch.isdigit()) or 1)

        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        c = conn.cursor()

        # 💣 회장님의 모든 데이터를 가리지 않고 100% 싹 비웁니다! (좀비 데이터 포함)
        c.execute("DELETE FROM shipments WHERE user_id=? OR user_id=? OR CAST(user_id AS TEXT) LIKE ?", (safe_uid, str(safe_uid), f"%{safe_uid}%"))

        conn.commit()
        conn.close()

        flash("🗑️ 펑! 모든 출하 내역이 깨끗하게 싹 지워졌습니다! (새 도화지가 되었습니다)")
    except Exception as e:
        flash(f"삭제 중 오류가 발생했습니다: {str(e)}")

    return redirect(url_for('shipment') + '#register')

# ==========================================
# 🗑️ 생육 데이터 전용 삭제 엔진 (404 에러 원천 차단)
# ==========================================
@app.route('/delete_growth/<int:id>')
@login_required
def delete_growth(id):
    uid = get_uid()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    c = conn.cursor()
    # 회장님 본인의 데이터가 맞는지 확인 후 안전하게 삭제
    c.execute("DELETE FROM growth_data WHERE id=? AND user_id=?", (id, uid))
    conn.commit()
    conn.close()

    # 삭제 후 길을 잃지 않고 무조건 생육 대시보드로 돌아갑니다!
    return redirect(url_for('growth'))

@app.route('/manifest.json')
def manifest():
    manifest_data = {"name": "오이연구회 스마트 영농일지", "short_name": "오이영농", "start_url": "/", "display": "standalone", "background_color": "#f4f6f3", "theme_color": "#2e4d2e", "icons": [{"src": "/icon.svg", "sizes": "192x192 512x512", "type": "image/svg+xml"}]}
    return Response(json.dumps(manifest_data), mimetype='application/json')

@app.route('/icon.svg')
def serve_icon(): return Response('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="20" fill="#38a169"/><text x="50" y="65" font-size="50" text-anchor="middle" fill="white">🥒</text></svg>''', mimetype='image/svg+xml')

@app.route('/sw.js')
def sw(): return Response('''self.addEventListener('install', (e) => { console.log('[Service Worker] Install'); }); self.addEventListener('fetch', (e) => { e.respondWith(fetch(e.request)); });''', mimetype='application/javascript')

if __name__ == '__main__':
    app.run(debug=True, port=5000)