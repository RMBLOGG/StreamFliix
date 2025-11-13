import os
import secrets
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import cloudinary
import cloudinary.uploader
import uuid

# Flask init
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET', 'dev-secret-key-123456')

# Gunakan SQLite untuk Vercel - lebih reliable
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///streamflix.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'

# Cloudinary config dengan error handling
try:
    cloudinary.config(
        cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
        api_key=os.getenv('CLOUDINARY_API_KEY'),
        api_secret=os.getenv('CLOUDINARY_API_SECRET'),
        secure=True
    )
except:
    print("Cloudinary config skipped")

# Helper function untuk UTC time
def utc_now():
    return datetime.now(timezone.utc)

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(200))
    role = db.Column(db.String(20), default='admin')
    created_at = db.Column(db.DateTime, default=utc_now)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    url = db.Column(db.String(1024))
    public_id = db.Column(db.String(1024))
    created_at = db.Column(db.DateTime, default=utc_now)

class PaymentProof(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(100))
    user_email = db.Column(db.String(120))
    user_phone = db.Column(db.String(20))
    payment_method = db.Column(db.String(50))
    payment_amount = db.Column(db.Integer)
    proof_image = db.Column(db.String(500))
    status = db.Column(db.String(20), default='pending')
    access_code = db.Column(db.String(10))
    device_id = db.Column(db.String(200))
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utc_now)
    approved_at = db.Column(db.DateTime)

class AccessCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True)
    is_used = db.Column(db.Boolean, default=False)
    device_id = db.Column(db.String(200))
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utc_now)
    used_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.String(200))

def generate_access_code():
    return secrets.token_hex(4).upper()

def get_device_id():
    """Generate unique device ID"""
    if 'device_id' not in session:
        session['device_id'] = str(uuid.uuid4())
    return session['device_id']

def cleanup_expired_codes():
    """Remove expired access codes"""
    try:
        expired_codes = AccessCode.query.filter(
            AccessCode.expires_at <= utc_now()
        ).all()
        
        for code in expired_codes:
            db.session.delete(code)
        
        if expired_codes:
            db.session.commit()
    except Exception as e:
        print(f"Error cleaning expired codes: {e}")

def check_access_code():
    """Check if current device has valid access code"""
    try:
        device_id = get_device_id()
        
        # Check for valid access code
        access_code_obj = AccessCode.query.filter_by(
            device_id=device_id, 
            is_used=True,
            is_active=True
        ).filter(AccessCode.expires_at > utc_now()).first()
        
        return access_code_obj is not None
    except Exception as e:
        print(f"Error checking access code: {e}")
        return False

# Context processor untuk membuat check_access_code tersedia di template
@app.context_processor
def utility_processor():
    return dict(check_access_code=check_access_code)

# user_loader for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except:
        return None

# Initialize database
with app.app_context():
    try:
        db.create_all()
        cleanup_expired_codes()
        
        # Create default admin user jika belum ada
        admin_email = os.getenv('ADMIN_EMAIL')
        admin_password = os.getenv('ADMIN_PASSWORD')
        if admin_email and admin_password:
            admin = User.query.filter_by(email=admin_email).first()
            if not admin:
                admin = User(
                    email=admin_email, 
                    password=generate_password_hash(admin_password), 
                    role='admin'
                )
                db.session.add(admin)
                db.session.commit()
                print('Admin user created')
    except Exception as e:
        print(f'Database init error: {e}')

# Error handlers
@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

# Routes --------------------------------------------------------------------

@app.before_request
def require_payment():
    """Check if user needs to go through payment gateway"""
    try:
        if request.endpoint and request.endpoint not in [
            'payment_gateway', 'static', 'access_code', 
            'admin_login', 'logout', 'demo', 'index', 'health'
        ]:
            if not check_access_code() and not current_user.is_authenticated:
                return redirect(url_for('demo'))
    except Exception as e:
        print(f"Error in require_payment: {e}")

@app.route('/')
def index():
    """Home page"""
    try:
        if not check_access_code() and not current_user.is_authenticated:
            return redirect(url_for('demo'))
        
        videos = Video.query.order_by(Video.created_at.desc()).all()
        return render_template('index.html', videos=videos)
    except Exception as e:
        flash('Error loading videos', 'danger')
        return render_template('index.html', videos=[])

@app.route('/demo')
def demo():
    """Halaman demo untuk user yang belum membayar"""
    try:
        videos = Video.query.order_by(Video.created_at.desc()).all()
        return render_template('demo.html', videos=videos)
    except Exception as e:
        flash('Error loading demo videos', 'danger')
        return render_template('demo.html', videos=[])

# Payment Gateway
@app.route('/payment', methods=['GET', 'POST'])
def payment_gateway():
    try:
        if check_access_code():
            return redirect(url_for('index'))
        
        if request.method == 'POST':
            user_name = request.form.get('name', '').strip()
            user_email = request.form.get('email', '').strip()
            user_phone = request.form.get('phone', '').strip()
            payment_method = request.form.get('payment_method', '').strip()
            payment_amount = request.form.get('amount', '10000').strip()
            proof_image = request.files.get('proof_image')
            
            if not all([user_name, user_email, user_phone, payment_method, proof_image]):
                flash('Harap lengkapi semua field', 'danger')
                return render_template('payment.html')
            
            upload_result = cloudinary.uploader.upload(
                proof_image,
                resource_type='image',
                folder='streamflix_payments'
            )
            
            payment_proof = PaymentProof(
                user_name=user_name,
                user_email=user_email,
                user_phone=user_phone,
                payment_method=payment_method,
                payment_amount=int(payment_amount),
                proof_image=upload_result.get('secure_url')
            )
            
            db.session.add(payment_proof)
            db.session.commit()
            
            flash('Bukti pembayaran berhasil dikirim! Admin akan memverifikasi dalam 1x24 jam.', 'success')
            return redirect(url_for('payment_gateway'))
        
        return render_template('payment.html')
        
    except Exception as e:
        flash('Gagal mengupload bukti pembayaran: ' + str(e), 'danger')
        return render_template('payment.html')

# Access Code Entry
@app.route('/access-code', methods=['GET', 'POST'])
def access_code():
    try:
        if check_access_code():
            return redirect(url_for('index'))
        
        cleanup_expired_codes()
        
        if request.method == 'POST':
            code = request.form.get('code', '').strip().upper().replace('-', '').replace(' ', '')
            
            if not code or len(code) != 8:
                flash('Masukkan kode akses yang valid (8 karakter)', 'danger')
                return render_template('access_code.html')
            
            access_code_obj = AccessCode.query.filter_by(code=code).first()
            
            if not access_code_obj:
                flash('Kode akses tidak valid', 'danger')
                return render_template('access_code.html')
            
            if not access_code_obj.is_active:
                flash('Kode akses telah dinonaktifkan oleh admin', 'danger')
                return render_template('access_code.html')
            
            if access_code_obj.is_used:
                flash('Kode akses sudah digunakan', 'danger')
                return render_template('access_code.html')
            
            if access_code_obj.expires_at.replace(tzinfo=timezone.utc) < utc_now():
                flash('Kode akses sudah kadaluarsa', 'danger')
                return render_template('access_code.html')
            
            device_id = get_device_id()
            access_code_obj.is_used = True
            access_code_obj.device_id = device_id
            access_code_obj.used_at = utc_now()
            
            db.session.commit()
            
            flash('Akses berhasil! Selamat menikmati StreamFlix.', 'success')
            return redirect(url_for('index'))
        
        return render_template('access_code.html')
    
    except Exception as e:
        flash('Error processing access code: ' + str(e), 'danger')
        return render_template('access_code.html')

# Admin Routes
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        try:
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            
            if not email or not password:
                flash('Email dan password harus diisi', 'danger')
                return render_template('login.html')
            
            user = User.query.filter_by(email=email).first()
            
            if user and check_password_hash(user.password, password) and user.role == 'admin':
                login_user(user)
                flash('Login berhasil', 'success')
                return redirect(url_for('admin_dashboard'))
            else:
                flash('Email atau password salah', 'danger')
                
        except Exception as e:
            flash('Terjadi error saat login. Silakan coba lagi.', 'danger')
    
    return render_template('login.html')

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    try:
        if current_user.role != 'admin':
            flash('Akses ditolak', 'danger')
            return redirect(url_for('index'))
        
        pending_payments = PaymentProof.query.filter_by(status='pending').order_by(PaymentProof.created_at.desc()).all()
        videos = Video.query.order_by(Video.created_at.desc()).all()
        
        # Get all access codes
        page = request.args.get('page', 1, type=int)
        per_page = 10
        
        # Filter options
        status_filter = request.args.get('status', 'all')
        search_query = request.args.get('search', '')
        
        # Base query
        access_codes_query = AccessCode.query
        
        # Apply filters
        if status_filter == 'active':
            access_codes_query = access_codes_query.filter_by(is_active=True)
        elif status_filter == 'inactive':
            access_codes_query = access_codes_query.filter_by(is_active=False)
        elif status_filter == 'used':
            access_codes_query = access_codes_query.filter_by(is_used=True)
        elif status_filter == 'unused':
            access_codes_query = access_codes_query.filter_by(is_used=False)
        elif status_filter == 'expired':
            access_codes_query = access_codes_query.filter(AccessCode.expires_at <= utc_now())
        
        # Apply search
        if search_query:
            access_codes_query = access_codes_query.filter(
                db.or_(
                    AccessCode.code.ilike(f'%{search_query}%'),
                    AccessCode.device_id.ilike(f'%{search_query}%'),
                    AccessCode.notes.ilike(f'%{search_query}%')
                )
            )
        
        # Order and paginate
        access_codes = access_codes_query.order_by(AccessCode.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Stats for dashboard
        total_codes = AccessCode.query.count()
        active_codes = AccessCode.query.filter_by(is_active=True).filter(AccessCode.expires_at > utc_now()).count()
        used_codes = AccessCode.query.filter_by(is_used=True).count()
        expired_codes = AccessCode.query.filter(AccessCode.expires_at <= utc_now()).count()
        
        current_time = utc_now().replace(tzinfo=None)
        
        return render_template('admin.html', 
                             videos=videos, 
                             pending_payments=pending_payments,
                             access_codes=access_codes,
                             total_codes=total_codes,
                             active_codes=active_codes,
                             used_codes=used_codes,
                             expired_codes=expired_codes,
                             status_filter=status_filter,
                             search_query=search_query,
                             current_time=current_time)
    
    except Exception as e:
        flash('Error loading dashboard', 'danger')
        return render_template('admin.html', 
                             videos=[], 
                             pending_payments=[],
                             access_codes=None,
                             total_codes=0,
                             active_codes=0,
                             used_codes=0,
                             expired_codes=0)

@app.route('/admin/approve-payment/<int:payment_id>', methods=['POST'])
@login_required
def approve_payment(payment_id):
    try:
        if current_user.role != 'admin':
            flash('Akses ditolak', 'danger')
            return redirect(url_for('index'))
        
        payment = PaymentProof.query.get_or_404(payment_id)
        
        if payment.status != 'pending':
            flash('Pembayaran sudah diproses', 'warning')
            return redirect(url_for('admin_dashboard'))
        
        # Generate unique access code
        while True:
            code = generate_access_code()
            existing_code = AccessCode.query.filter_by(code=code).first()
            if not existing_code:
                break
        
        expires_at = utc_now() + timedelta(days=30)
        
        access_code_obj = AccessCode(
            code=code,
            expires_at=expires_at,
            notes=f"Auto-generated for {payment.user_name}"
        )
        
        payment.status = 'approved'
        payment.access_code = code
        payment.approved_at = utc_now()
        
        db.session.add(access_code_obj)
        db.session.commit()
        
        flash(f'Pembayaran disetujui! Kode akses: {code}', 'success')
        return redirect(url_for('admin_dashboard'))
    
    except Exception as e:
        flash('Error approving payment: ' + str(e), 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject-payment/<int:payment_id>', methods=['POST'])
@login_required
def reject_payment(payment_id):
    try:
        if current_user.role != 'admin':
            flash('Akses ditolak', 'danger')
            return redirect(url_for('index'))
        
        payment = PaymentProof.query.get_or_404(payment_id)
        
        if payment.status != 'pending':
            flash('Pembayaran sudah diproses', 'warning')
            return redirect(url_for('admin_dashboard'))
        
        payment.status = 'rejected'
        db.session.commit()
        
        flash('Pembayaran ditolak', 'success')
        return redirect(url_for('admin_dashboard'))
    
    except Exception as e:
        flash('Error rejecting payment: ' + str(e), 'danger')
        return redirect(url_for('admin_dashboard'))

# Fitur Manajemen Kode Akses
@app.route('/admin/generate-code', methods=['POST'])
@login_required
def generate_manual_code():
    try:
        if current_user.role != 'admin':
            flash('Akses ditolak', 'danger')
            return redirect(url_for('index'))
        
        days_valid = int(request.form.get('days', 30))
        notes = request.form.get('notes', '').strip()
        
        # Generate unique code
        while True:
            code = generate_access_code()
            existing_code = AccessCode.query.filter_by(code=code).first()
            if not existing_code:
                break
        
        expires_at = utc_now() + timedelta(days=days_valid)
        
        access_code_obj = AccessCode(
            code=code,
            expires_at=expires_at,
            notes=notes
        )
        
        db.session.add(access_code_obj)
        db.session.commit()
        
        flash(f'Kode akses berhasil dibuat: {code} (Berlaku {days_valid} hari)', 'success')
        return redirect(url_for('admin_dashboard'))
    
    except Exception as e:
        flash('Error generating access code: ' + str(e), 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/deactivate-code/<int:code_id>', methods=['POST'])
@login_required
def deactivate_code(code_id):
    try:
        if current_user.role != 'admin':
            flash('Akses ditolak', 'danger')
            return redirect(url_for('index'))
        
        code = AccessCode.query.get_or_404(code_id)
        code.is_active = False
        db.session.commit()
        
        flash(f'Kode {code.code} berhasil dinonaktifkan', 'success')
        return redirect(url_for('admin_dashboard'))
    
    except Exception as e:
        flash('Error deactivating code: ' + str(e), 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/activate-code/<int:code_id>', methods=['POST'])
@login_required
def activate_code(code_id):
    try:
        if current_user.role != 'admin':
            flash('Akses ditolak', 'danger')
            return redirect(url_for('index'))
        
        code = AccessCode.query.get_or_404(code_id)
        code.is_active = True
        db.session.commit()
        
        flash(f'Kode {code.code} berhasil diaktifkan', 'success')
        return redirect(url_for('admin_dashboard'))
    
    except Exception as e:
        flash('Error activating code: ' + str(e), 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-code/<int:code_id>', methods=['POST'])
@login_required
def del
