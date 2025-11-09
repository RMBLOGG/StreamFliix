from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, send_from_directory
from flask_login import login_required, current_user
from models import db, User, Video, Payment, Access, Category, Announcement
from datetime import datetime, timedelta
import os
import pytz

admin_bp = Blueprint('admin', __name__)

def get_indonesia_time():
    """Get current Indonesia time (WIB)"""
    jakarta_tz = pytz.timezone('Asia/Jakarta')
    return datetime.now(jakarta_tz)

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Akses ditolak! Halaman untuk admin saja.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    total_users = User.query.count()
    total_videos = Video.query.count()
    total_premium_videos = Video.query.filter_by(is_premium=True).count()
    total_categories = Category.query.count()
    total_announcements = Announcement.query.count()
    active_announcements = Announcement.query.filter_by(is_active=True).count()
    
    # Total pendapatan dari semua transaksi sukses
    total_revenue = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.status == 'completed'
    ).scalar() or 0
    
    # Pending payments count
    pending_payments_count = Payment.query.filter_by(status='pending').count()
    
    # Recent completed payments
    recent_payments = Payment.query.filter_by(status='completed').order_by(
        Payment.created_at.desc()
    ).limit(10).all()
    
    # Today's revenue - menggunakan waktu Indonesia
    today_start = get_indonesia_time().replace(hour=0, minute=0, second=0, microsecond=0)
    today_revenue = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.status == 'completed',
        Payment.created_at >= today_start
    ).scalar() or 0
    
    # Monthly revenue - menggunakan waktu Indonesia
    month_start = get_indonesia_time().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_revenue = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.status == 'completed',
        Payment.created_at >= month_start
    ).scalar() or 0

    return render_template('admin/dashboard.html',
                         total_users=total_users,
                         total_videos=total_videos,
                         total_premium_videos=total_premium_videos,
                         total_categories=total_categories,
                         total_announcements=total_announcements,
                         active_announcements=active_announcements,
                         total_revenue=total_revenue,
                         pending_payments_count=pending_payments_count,
                         today_revenue=today_revenue,
                         monthly_revenue=monthly_revenue,
                         recent_payments=recent_payments)

@admin_bp.route('/videos')
@login_required
@admin_required
def videos():
    all_videos = Video.query.order_by(Video.created_at.desc()).all()
    return render_template('admin/videos.html', videos=all_videos)

@admin_bp.route('/add_video', methods=['GET', 'POST'])
@login_required
@admin_required
def add_video():
    categories = Category.query.order_by(Category.name).all()
    
    if request.method == 'POST':
        title = request.form.get('title')
        embed_url = request.form.get('embed_url')
        thumbnail_url = request.form.get('thumbnail_url')
        description = request.form.get('description')
        price = float(request.form.get('price', 0))
        is_premium = 'is_premium' in request.form
        category_id = request.form.get('category_id') or None
        
        new_video = Video(
            title=title,
            embed_url=embed_url,
            thumbnail_url=thumbnail_url,
            description=description,
            price=price,
            is_premium=is_premium,
            category_id=category_id
        )
        
        db.session.add(new_video)
        db.session.commit()
        
        flash('Video berhasil ditambahkan!', 'success')
        return redirect(url_for('admin.videos'))
    
    return render_template('admin/add_video.html', categories=categories)

@admin_bp.route('/edit_video/<int:video_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_video(video_id):
    video = Video.query.get_or_404(video_id)
    categories = Category.query.order_by(Category.name).all()
    
    if request.method == 'POST':
        video.title = request.form.get('title')
        video.embed_url = request.form.get('embed_url')
        video.thumbnail_url = request.form.get('thumbnail_url')
        video.description = request.form.get('description')
        video.price = float(request.form.get('price', 0))
        video.is_premium = 'is_premium' in request.form
        video.category_id = request.form.get('category_id') or None
        
        db.session.commit()
        flash('Video berhasil diupdate!', 'success')
        return redirect(url_for('admin.videos'))
    
    return render_template('admin/edit_video.html', video=video, categories=categories)

@admin_bp.route('/delete_video/<int:video_id>')
@login_required
@admin_required
def delete_video(video_id):
    video = Video.query.get_or_404(video_id)
    
    # Delete related accesses first
    Access.query.filter_by(video_id=video_id).delete()
    
    db.session.delete(video)
    db.session.commit()
    
    flash('Video berhasil dihapus!', 'success')
    return redirect(url_for('admin.videos'))

@admin_bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=all_users)

@admin_bp.route('/add_balance/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def add_balance(user_id):
    user = User.query.get_or_404(user_id)
    amount = float(request.form.get('amount', 0))
    
    if amount <= 0:
        flash('Jumlah saldo harus lebih dari 0!', 'danger')
        return redirect(url_for('admin.users'))
    
    user.wallet_balance += amount
    
    # Create payment record
    payment = Payment(
        user_id=user_id,
        amount=amount,
        status='completed',
        payment_method='StreamFlix',
        sender_name='ADMIN'
    )
    db.session.add(payment)
    db.session.commit()
    
    flash(f'Saldo Rp {amount:,.0f} berhasil ditambahkan ke {user.email}!', 'success')
    return redirect(url_for('admin.users'))

@admin_bp.route('/payments')
@login_required
@admin_required
def payments():
    status_filter = request.args.get('status', 'all')
    
    query = Payment.query
    
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    all_payments = query.order_by(Payment.created_at.desc()).all()
    
    # Statistics
    total_pending = Payment.query.filter_by(status='pending').count()
    total_completed = Payment.query.filter_by(status='completed').count()
    total_rejected = Payment.query.filter_by(status='rejected').count()
    
    return render_template('admin/payments.html', 
                         payments=all_payments,
                         status_filter=status_filter,
                         total_pending=total_pending,
                         total_completed=total_completed,
                         total_rejected=total_rejected)

@admin_bp.route('/approve_payment/<int:payment_id>')
@login_required
@admin_required
def approve_payment(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    user = User.query.get(payment.user_id)
    
    if payment.status == 'pending':
        # Add balance to user
        user.wallet_balance += payment.amount
        payment.status = 'completed'
        payment.updated_at = get_indonesia_time()
        
        db.session.commit()
        
        flash(f'Pembayaran Rp {payment.amount:,.0f} dari {user.email} berhasil disetujui! Saldo telah ditambahkan.', 'success')
    else:
        flash('Pembayaran sudah diproses sebelumnya.', 'warning')
    
    return redirect(url_for('admin.payments'))

@admin_bp.route('/reject_payment/<int:payment_id>', methods=['POST'])
@login_required
@admin_required
def reject_payment(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    rejection_notes = request.form.get('rejection_notes', '').strip()
    
    if payment.status == 'pending':
        payment.status = 'rejected'
        payment.admin_notes = rejection_notes or 'Pembayaran ditolak oleh admin'
        payment.updated_at = get_indonesia_time()
        
        db.session.commit()
        
        flash(f'Pembayaran Rp {payment.amount:,.0f} dari {payment.user.email} telah ditolak.', 'warning')
    else:
        flash('Pembayaran sudah diproses sebelumnya.', 'warning')
    
    return redirect(url_for('admin.payments'))

@admin_bp.route('/payment_proof/<filename>')
@login_required
@admin_required
def payment_proof(filename):
    """Endpoint untuk melihat bukti transfer"""
    # Untuk simulasi, kita akan menggunakan placeholder
    # Dalam aplikasi real, file disimpan di folder uploads
    uploads_dir = os.path.join(os.getcwd(), 'static', 'uploads')
    
    # Cek jika file exists di uploads folder
    if os.path.exists(os.path.join(uploads_dir, filename)):
        return send_from_directory(uploads_dir, filename)
    else:
        # Return placeholder image jika file tidak ada
        return send_from_directory('static', 'images/no-image.jpg')

@admin_bp.route('/payment_details/<int:payment_id>')
@login_required
@admin_required
def payment_details(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    return render_template('admin/payment_details.html', payment=payment)

@admin_bp.route('/bulk_approve_payments', methods=['POST'])
@login_required
@admin_required
def bulk_approve_payments():
    payment_ids = request.form.getlist('payment_ids')
    
    if not payment_ids:
        flash('Tidak ada pembayaran yang dipilih!', 'warning')
        return redirect(url_for('admin.payments'))
    
    approved_count = 0
    total_amount = 0
    
    for payment_id in payment_ids:
        payment = Payment.query.get(payment_id)
        if payment and payment.status == 'pending':
            user = User.query.get(payment.user_id)
            if user:
                user.wallet_balance += payment.amount
                payment.status = 'completed'
                payment.updated_at = get_indonesia_time()
                approved_count += 1
                total_amount += payment.amount
    
    if approved_count > 0:
        db.session.commit()
        flash(f'Berhasil menyetujui {approved_count} pembayaran dengan total Rp {total_amount:,.0f}!', 'success')
    else:
        flash('Tidak ada pembayaran yang berhasil disetujui.', 'warning')
    
    return redirect(url_for('admin.payments'))

@admin_bp.route('/api/payment_stats')
@login_required
@admin_required
def api_payment_stats():
    # Daily stats for last 7 days - menggunakan waktu Indonesia
    end_date = get_indonesia_time()
    start_date = end_date - timedelta(days=7)
    
    daily_stats = db.session.query(
        db.func.date(Payment.created_at).label('date'),
        db.func.sum(Payment.amount).label('total_amount'),
        db.func.count(Payment.id).label('payment_count')
    ).filter(
        Payment.status == 'completed',
        Payment.created_at >= start_date
    ).group_by(
        db.func.date(Payment.created_at)
    ).order_by(
        db.func.date(Payment.created_at)
    ).all()
    
    stats_data = {
        'dates': [stat.date.strftime('%Y-%m-%d') for stat in daily_stats],
        'amounts': [float(stat.total_amount or 0) for stat in daily_stats],
        'counts': [stat.payment_count for stat in daily_stats]
    }
    
    return jsonify(stats_data)

@admin_bp.route('/export_payments')
@login_required
@admin_required
def export_payments():
    # Simple export functionality (in real app, use libraries like pandas)
    payments = Payment.query.order_by(Payment.created_at.desc()).all()
    
    export_data = "ID,User Email,Amount,Method,Status,Sender Name,Created At\n"
    for payment in payments:
        # Convert to Indonesia time for export
        jakarta_tz = pytz.timezone('Asia/Jakarta')
        created_at_indonesia = payment.created_at.astimezone(jakarta_tz) if payment.created_at.tzinfo else pytz.utc.localize(payment.created_at).astimezone(jakarta_tz)
        
        export_data += f"{payment.id},{payment.user.email},{payment.amount},{payment.payment_method},{payment.status},{payment.sender_name or ''},{created_at_indonesia.strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    # In a real application, you would save this to a file and provide download
    flash('Fitur export akan diimplementasikan lengkap di versi berikutnya.', 'info')
    return redirect(url_for('admin.payments'))

# ============================
# CATEGORY MANAGEMENT ROUTES
# ============================

@admin_bp.route('/categories')
@login_required
@admin_required
def categories():
    all_categories = Category.query.order_by(Category.name).all()
    return render_template('admin/categories.html', categories=all_categories)

@admin_bp.route('/add_category', methods=['POST'])
@login_required
@admin_required
def add_category():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    
    if not name:
        flash('Nama kategori wajib diisi!', 'danger')
        return redirect(url_for('admin.categories'))
    
    # Check if category already exists
    existing_category = Category.query.filter_by(name=name).first()
    if existing_category:
        flash('Kategori sudah ada!', 'danger')
        return redirect(url_for('admin.categories'))
    
    new_category = Category(
        name=name,
        description=description
    )
    
    db.session.add(new_category)
    db.session.commit()
    
    flash(f'Kategori "{name}" berhasil ditambahkan!', 'success')
    return redirect(url_for('admin.categories'))

@admin_bp.route('/edit_category/<int:category_id>', methods=['POST'])
@login_required
@admin_required
def edit_category(category_id):
    category = Category.query.get_or_404(category_id)
    
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    
    if not name:
        flash('Nama kategori wajib diisi!', 'danger')
        return redirect(url_for('admin.categories'))
    
    # Check if category name already exists (excluding current category)
    existing_category = Category.query.filter(
        Category.name == name,
        Category.id != category_id
    ).first()
    
    if existing_category:
        flash('Nama kategori sudah digunakan!', 'danger')
        return redirect(url_for('admin.categories'))
    
    category.name = name
    category.description = description
    
    db.session.commit()
    
    flash(f'Kategori "{name}" berhasil diupdate!', 'success')
    return redirect(url_for('admin.categories'))

@admin_bp.route('/delete_category/<int:category_id>')
@login_required
@admin_required
def delete_category(category_id):
    category = Category.query.get_or_404(category_id)
    
    # Check if category is being used by any videos
    videos_count = Video.query.filter_by(category_id=category_id).count()
    if videos_count > 0:
        flash(f'Tidak bisa menghapus kategori "{category.name}" karena masih digunakan oleh {videos_count} video!', 'danger')
        return redirect(url_for('admin.categories'))
    
    category_name = category.name
    db.session.delete(category)
    db.session.commit()
    
    flash(f'Kategori "{category_name}" berhasil dihapus!', 'success')
    return redirect(url_for('admin.categories'))

@admin_bp.route('/api/category_stats')
@login_required
@admin_required
def api_category_stats():
    """API untuk statistik penggunaan kategori"""
    categories = Category.query.all()
    
    stats_data = []
    for category in categories:
        video_count = Video.query.filter_by(category_id=category.id).count()
        premium_count = Video.query.filter_by(category_id=category.id, is_premium=True).count()
        free_count = Video.query.filter_by(category_id=category.id, is_premium=False).count()
        
        stats_data.append({
            'id': category.id,
            'name': category.name,
            'total_videos': video_count,
            'premium_videos': premium_count,
            'free_videos': free_count,
            'description': category.description or ''
        })
    
    return jsonify(stats_data)

@admin_bp.route('/get_category/<int:category_id>')
@login_required
@admin_required
def get_category(category_id):
    """API untuk mendapatkan data kategori spesifik"""
    category = Category.query.get_or_404(category_id)
    
    return jsonify({
        'id': category.id,
        'name': category.name,
        'description': category.description or '',
        'created_at': category.created_at.strftime('%Y-%m-%d %H:%M:%S')
    })

# ============================
# ANNOUNCEMENT MANAGEMENT ROUTES - DIPERBARUI
# ============================

@admin_bp.route('/announcements')
@login_required
@admin_required
def announcements():
    all_announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template('admin/announcements.html', announcements=all_announcements)

@admin_bp.route('/add_announcement', methods=['GET', 'POST'])
@login_required
@admin_required
def add_announcement():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        text_color = request.form.get('text_color', '#ffffff').strip()
        text_style = request.form.get('text_style', 'normal').strip()  # TAMBAHAN: Style teks
        is_active = 'is_active' in request.form
        
        if not title or not content:
            flash('Judul dan konten pengumuman wajib diisi!', 'danger')
            return redirect(url_for('admin.announcements'))
        
        new_announcement = Announcement(
            title=title,
            content=content,
            text_color=text_color,
            text_style=text_style,  # TAMBAHAN: Style teks
            is_active=is_active
        )
        
        db.session.add(new_announcement)
        db.session.commit()
        
        flash('Pengumuman berhasil ditambahkan!', 'success')
        return redirect(url_for('admin.announcements'))
    
    return render_template('admin/add_announcement.html')

@admin_bp.route('/edit_announcement/<int:announcement_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_announcement(announcement_id):
    announcement = Announcement.query.get_or_404(announcement_id)
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        text_color = request.form.get('text_color', '#ffffff').strip()
        text_style = request.form.get('text_style', 'normal').strip()  # TAMBAHAN: Style teks
        is_active = 'is_active' in request.form
        
        if not title or not content:
            flash('Judul dan konten pengumuman wajib diisi!', 'danger')
            return redirect(url_for('admin.announcements'))
        
        announcement.title = title
        announcement.content = content
        announcement.text_color = text_color
        announcement.text_style = text_style  # TAMBAHAN: Style teks
        announcement.is_active = is_active
        announcement.updated_at = get_indonesia_time()
        
        db.session.commit()
        flash('Pengumuman berhasil diupdate!', 'success')
        return redirect(url_for('admin.announcements'))
    
    return render_template('admin/edit_announcement.html', announcement=announcement)

@admin_bp.route('/toggle_user/<int:user_id>')
@login_required
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Prevent self-deactivation
    if user.id == current_user.id:
        flash('Tidak bisa menonaktifkan akun sendiri!', 'danger')
        return redirect(url_for('admin.users'))
    
    user.is_active = not user.is_active
    db.session.commit()
    
    status = "dinonaktifkan" if not user.is_active else "diaktifkan"
    flash(f'User {user.email} berhasil {status}!', 'success')
    return redirect(url_for('admin.users'))

@admin_bp.route('/delete_user/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        flash('Tidak bisa menghapus akun sendiri!', 'danger')
        return redirect(url_for('admin.users'))
    
    # Soft delete
    user.is_active = False
    user.is_deleted = True
    user.deleted_at = get_indonesia_time()
    
    db.session.commit()
    
    flash(f'User {user.email} berhasil dihapus!', 'success')
    return redirect(url_for('admin.users'))

@admin_bp.route('/delete_announcement/<int:announcement_id>')
@login_required
@admin_required
def delete_announcement(announcement_id):
    announcement = Announcement.query.get_or_404(announcement_id)
    
    db.session.delete(announcement)
    db.session.commit()
    
    flash('Pengumuman berhasil dihapus!', 'success')
    return redirect(url_for('admin.announcements'))

@admin_bp.route('/toggle_announcement/<int:announcement_id>')
@login_required
@admin_required
def toggle_announcement(announcement_id):
    announcement = Announcement.query.get_or_404(announcement_id)
    
    announcement.is_active = not announcement.is_active
    announcement.updated_at = get_indonesia_time()
    
    db.session.commit()
    
    status = "diaktifkan" if announcement.is_active else "dinonaktifkan"
    flash(f'Pengumuman berhasil {status}!', 'success')
    return redirect(url_for('admin.announcements'))

@admin_bp.route('/api/announcement_stats')
@login_required
@admin_required
def api_announcement_stats():
    """API untuk statistik pengumuman"""
    total_announcements = Announcement.query.count()
    active_announcements = Announcement.query.filter_by(is_active=True).count()
    inactive_announcements = Announcement.query.filter_by(is_active=False).count()
    
    # Recent announcements (last 7 days) - menggunakan waktu Indonesia
    week_ago = get_indonesia_time() - timedelta(days=7)
    recent_announcements = Announcement.query.filter(
        Announcement.created_at >= week_ago
    ).count()
    
    return jsonify({
        'total_announcements': total_announcements,
        'active_announcements': active_announcements,
        'inactive_announcements': inactive_announcements,
        'recent_announcements': recent_announcements
    })