from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file, send_from_directory, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import bcrypt
import os
import stripe
from dotenv import load_dotenv
import jwt
from datetime import timedelta
import json
import asyncio
import io
import hashlib
import secrets
import hmac
import logging
import re
import posixpath
from email_validator import validate_email, EmailNotValidError
from sqlalchemy.exc import IntegrityError
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

load_dotenv()

app = Flask(__name__)
app.config['APP_ENV'] = os.getenv('APP_ENV', 'production')
if app.config['APP_ENV'] not in ('production', 'development', 'testing'):
    raise RuntimeError('APP_ENV must be production, development or testing.')
secret_key = os.getenv('SECRET_KEY', '')
if len(secret_key) < 32 or any(word in secret_key.lower() for word in ('your-secret', 'your_secret', 'changeme', 'change-me', 'placeholder')):
    raise RuntimeError('Set SECRET_KEY to a unique random value of at least 32 characters.')
app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///apex_xbuild.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'hide_parameters': True}
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=app.config['APP_ENV'] == 'production',
                  MAX_CONTENT_LENGTH=16 * 1024 * 1024)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


class RedactQueryStrings(logging.Filter):
    def filter(self, record):
        # Werkzeug access logs must not retain verification/reset query tokens.
        record.msg = re.sub(r'\?[^\s"\x1b]*', '?[redacted]', record.getMessage())
        record.args = ()
        return True


for access_logger in ('werkzeug', 'gunicorn.access'):
    logging.getLogger(access_logger).addFilter(RedactQueryStrings())


def csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return session['_csrf_token']


app.jinja_env.globals['csrf_token'] = csrf_token


@app.before_request
def protect_mutations():
    if request.endpoint == 'static':
        filename = (request.view_args or {}).get('filename', '')
        normalized = posixpath.normpath(filename.replace('\\', '/')).casefold()
        if normalized == 'uploads/documents' or normalized.startswith('uploads/documents/'):
            # Prevent aliases such as repeated slashes from bypassing the
            # authenticated document route through Flask's generic static route.
            abort(404)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        supplied = request.headers.get('X-CSRF-Token') if request.is_json else request.form.get('csrf_token')
        expected = session.get('_csrf_token')
        if (not isinstance(supplied, str) or not expected or
                not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8'))):
            if request.is_json:
                return jsonify(error='Invalid request token. Reload the page and try again.'), 400
            abort(400, description='Invalid request token. Reload the page and try again.')

# ==================== MODELS ====================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), default='CUSTOMER')
    is_active = db.Column(db.Boolean, default=True)
    is_vip = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    icon = db.Column(db.String(50))
    price = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(500))
    status = db.Column(db.String(20), default='PLANNING')
    is_published = db.Column(db.Boolean, default=True)
    progress = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='projects')

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='NEW')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BlogPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False)
    excerpt = db.Column(db.Text, nullable=False)
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(500))
    category = db.Column(db.String(50), default='General')
    status = db.Column(db.String(20), default='DRAFT')
    views = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    service = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    time = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='PENDING')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='bookings')

class ProjectMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read_admin = db.Column(db.Boolean, default=False)
    is_read_customer = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref='messages')
    sender = db.relationship('User')

class FAQ(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(300), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    file_url = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(50), default='OTHER')
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='documents')

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    number = db.Column(db.String(20), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='DRAFT')
    due_date = db.Column(db.DateTime, nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    items = db.Column(db.Text, default='[]')
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='invoices')

# Fingerprints of the two publicly committed legacy fixture passwords. Rejecting
# them also protects existing databases without changing any stored accounts.
COMPROMISED_PASSWORD_DIGESTS = frozenset(('3eb3fe66b31e3b4d10fa70b5cad49c7112294af6ae4e476a1c405155d45aa121', '588c55f3ce2b8569b153c5abbf13f9f74308b88a20017cc699b835cc93195d16'))


def password_version(user):
    return hashlib.sha256(user.password.encode('utf-8')).hexdigest()


@login_manager.user_loader
def load_user(user_id):
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    if (not user or not user.is_active or not user.is_verified or
            session.get('auth_version') != password_version(user)):
        return None
    return user


def valid_new_password(password):
    return (isinstance(password, str) and 8 <= len(password) and
            len(password.encode('utf-8')) <= 72 and
            hashlib.sha256(password.encode('utf-8')).hexdigest() not in COMPROMISED_PASSWORD_DIGESTS)


def valid_email(email):
    if not isinstance(email, str) or len(email) > 100:
        return False
    try:
        validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password, hashed):
    if not isinstance(password, str) or len(password.encode('utf-8')) > 72:
        return False
    if hashlib.sha256(password.encode('utf-8')).hexdigest() in COMPROMISED_PASSWORD_DIGESTS:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except (ValueError, TypeError):
        return False

# ==================== EMAIL SERVICE ====================

class EmailService:
    def __init__(self):
        self.api_key = os.getenv('RESEND_API_KEY')
        self.from_email = os.getenv('EMAIL_FROM', 'noreply@apexbuildinteriors.com')
        self.base_url = "https://api.resend.com"
    
    async def send_email(self, to, subject, html, text=None):
        if not self.api_key or self.api_key.startswith('re_xxxx'):
            app.logger.warning('Email delivery is disabled; no message was sent.')
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/emails",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"from": self.from_email, "to": [to], "subject": subject,
                          "html": html, "text": text or html},
                )
                if response.status_code != 200:
                    app.logger.warning('Email provider rejected delivery.')
                return response.status_code == 200
        except Exception:
            app.logger.warning('Email delivery failed.')
            return False

    async def send_verification_email(self, email, name, token):
        link = f"{os.getenv('FRONTEND_URL')}/verify-email?token={token}"
        html = f"""
        <h2>Welcome {name}!</h2>
        <p>Please verify your email by clicking the link below:</p>
        <a href="{link}">{link}</a>
        <p>This link expires in 24 hours.</p>
        """
        return await self.send_email(email, "Verify Your Email", html)
    
    async def send_password_reset_email(self, email, name, token):
        link = f"{os.getenv('FRONTEND_URL')}/reset-password?token={token}"
        html = f"""
        <h2>Reset Your Password</h2>
        <p>Hello {name},</p>
        <p>Click the link below to reset your password:</p>
        <a href="{link}">{link}</a>
        <p>This link expires in 24 hours.</p>
        <p>If you didn't request this, please ignore this email.</p>
        """
        return await self.send_email(email, "Reset Your Password", html)
    
    async def send_booking_confirmation(self, email, name, booking_data):
        html = f"""
        <h2>Booking Confirmed!</h2>
        <p>Hello {name},</p>
        <p>Your consultation booking has been confirmed.</p>
        <p><strong>Date:</strong> {booking_data.get('date')}</p>
        <p><strong>Time:</strong> {booking_data.get('time')}</p>
        <p><strong>Service:</strong> {booking_data.get('service', 'Consultation')}</p>
        <p>We look forward to meeting you!</p>
        """
        return await self.send_email(email, "Booking Confirmed", html)
    
    async def send_welcome_email(self, email, name):
        html = f"""
        <h2>Welcome to ApexBuild Interiors!</h2>
        <p>Hello {name},</p>
        <p>Thank you for registering with us. We're excited to work with you!</p>
        """
        return await self.send_email(email, "Welcome to ApexBuild", html)

import httpx

email_service = EmailService()

# ==================== ROUTES ====================

@app.route('/')
def home():
    services = Service.query.filter_by(is_active=True).limit(3).all()
    return render_template('index.html', services=services)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/services')
def services():
    services = Service.query.filter_by(is_active=True).all()
    return render_template('services.html', services=services)

@app.route('/projects')
def projects():
    projects = Project.query.filter_by(is_published=True).all()
    return render_template('projects.html', projects=projects)

@app.route('/robots.txt')
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /dashboard",
        "Disallow: /login",
        "Disallow: /register",
        f"Sitemap: {request.url_root}sitemap.xml",
    ]
    return "\n".join(lines), 200, {'Content-Type': 'text/plain'}

@app.route('/sitemap.xml')
def sitemap_xml():
    pages = []
    base = request.url_root.rstrip('/')

    static_urls = ['/', '/services', '/projects', '/blog', '/faq', '/contact', '/about']
    for path in static_urls:
        pages.append({'loc': f"{base}{path}", 'changefreq': 'weekly', 'priority': '0.8'})

    for project in Project.query.filter_by(is_published=True).all():
        pages.append({
            'loc': f"{base}/projects/{project.slug}",
            'lastmod': project.created_at.strftime('%Y-%m-%d'),
            'changefreq': 'monthly',
            'priority': '0.6'
        })

    for post in BlogPost.query.filter_by(status='PUBLISHED').all():
        pages.append({
            'loc': f"{base}/blog/{post.slug}",
            'lastmod': post.created_at.strftime('%Y-%m-%d'),
            'changefreq': 'monthly',
            'priority': '0.6'
        })

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for page in pages:
        xml_parts.append('  <url>')
        xml_parts.append(f"    <loc>{page['loc']}</loc>")
        if 'lastmod' in page:
            xml_parts.append(f"    <lastmod>{page['lastmod']}</lastmod>")
        xml_parts.append(f"    <changefreq>{page['changefreq']}</changefreq>")
        xml_parts.append(f"    <priority>{page['priority']}</priority>")
        xml_parts.append('  </url>')
    xml_parts.append('</urlset>')

    return "\n".join(xml_parts), 200, {'Content-Type': 'application/xml'}

@app.route('/projects/<slug>')
def project_detail(slug):
    project = Project.query.filter_by(slug=slug, is_published=True).first_or_404()
    return render_template('project_detail.html', project=project)

@app.route('/blog')
def blog_list():
    posts = BlogPost.query.filter_by(status='PUBLISHED').order_by(BlogPost.created_at.desc()).all()
    return render_template('blog.html', posts=posts)

@app.route('/blog/<slug>')
def blog_detail(slug):
    post = BlogPost.query.filter_by(slug=slug, status='PUBLISHED').first_or_404()
    post.views += 1
    db.session.commit()
    return render_template('blog_detail.html', post=post)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        message = request.form.get('message', '').strip()
        if (not name or len(name) > 100 or not valid_email(email) or len(phone) > 20 or
                not message or len(message) > 10000):
            return render_template('contact.html', error='Please enter valid contact details and a message.'), 400
        lead = Lead(
            name=name, email=email, phone=phone, message=message
        )
        db.session.add(lead)
        db.session.commit()
        return render_template('contact.html', success='Thank you! We\'ll get back to you soon.')
    return render_template('contact.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and verify_password(password, user.password):
            if not user.is_verified:
                return render_template('login.html', error='Please verify your email first.')
            if not user.is_active:
                return render_template('login.html', error='Your account is inactive.')
            session.clear()
            login_user(user)
            session['auth_version'] = password_version(user)
            session['role'] = user.role
            if user.role == 'ADMIN':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        if (not valid_email(email) or not valid_new_password(password) or
                not first_name or not last_name or len(first_name) > 50 or len(last_name) > 50):
            return render_template('register.html', error='Enter valid account details and an 8–72 byte password that is not a known default.'), 400
        if User.query.filter_by(email=email).first():
            return render_template('register.html', error='Email already exists')
        user = User(
            email=email,
            password=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            is_verified=False
        )
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return render_template('register.html', error='Account registration could not be completed.'), 400
        # Send verification email
        token = generate_verification_token(email)
        try:
            result = asyncio.run(
                email_service.send_verification_email(email, first_name, token)
            )
        except Exception:
            result = False
            app.logger.warning('Verification email delivery failed.')
        return render_template(
            'register.html',
            success=('Registration successful! Please check your email to verify.' if result else
                     'Account created. Email delivery is unavailable; verification is still required.')
        )
    return render_template('register.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    unread_counts = {}
    for project in current_user.projects:
        unread_counts[project.id] = ProjectMessage.query.filter_by(project_id=project.id, is_read_customer=False).count()
    return render_template('dashboard.html', user=current_user, unread_counts=unread_counts)

@app.route('/my-projects/<int:id>')
@login_required
def my_project_detail(id):
    project = Project.query.get_or_404(id)
    if project.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    messages = ProjectMessage.query.filter_by(project_id=id).order_by(ProjectMessage.created_at.asc()).all()
    ProjectMessage.query.filter_by(project_id=id, is_read_customer=False).update({'is_read_customer': True})
    db.session.commit()
    return render_template('my_project_detail.html', project=project, messages=messages)

@app.route('/my-projects/<int:id>/message', methods=['POST'])
@login_required
def my_project_message(id):
    project = Project.query.get_or_404(id)
    if project.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    text = request.form.get('message', '').strip()
    if text:
        msg = ProjectMessage(project_id=id, sender_id=current_user.id, message=text, is_read_customer=True, is_read_admin=False)
        db.session.add(msg)
        db.session.commit()
        flash('Message sent!', 'success')
    return redirect(url_for('my_project_detail', id=id))

@app.route('/dashboard/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/dashboard/profile/update', methods=['POST'])
@login_required
def profile_update():
    user = current_user
    user.first_name = request.form.get('first_name')
    user.last_name = request.form.get('last_name')
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/dashboard/documents')
@login_required
def my_documents():
    documents = Document.query.filter_by(user_id=current_user.id).order_by(Document.uploaded_at.desc()).all()
    return render_template('my_documents.html', documents=documents)

@app.route('/dashboard/documents/upload', methods=['POST'])
@login_required
def upload_document():
    from werkzeug.utils import secure_filename
    if 'file' not in request.files:
        return redirect(url_for('my_documents'))
    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('my_documents'))
    if not allowed_document(file.filename):
        return redirect(url_for('my_documents'))
    filename = secure_filename(file.filename)
    if not filename or len(filename) > 200:
        abort(400)
    stored_filename = f'{secrets.token_hex(16)}-{filename}'
    os.makedirs(DOCUMENT_FOLDER, exist_ok=True)
    filepath = os.path.join(DOCUMENT_FOLDER, stored_filename)
    file.save(filepath)
    doc = Document(
        user_id=current_user.id,
        name=filename,
        file_url=f'/static/uploads/documents/{stored_filename}',
        file_type=request.form.get('file_type', 'OTHER')
    )
    db.session.add(doc)
    db.session.commit()
    return redirect(url_for('my_documents'))

@app.route('/static/uploads/documents/<path:filename>')
@login_required
def download_document(filename):
    doc = Document.query.filter_by(file_url=f'/static/uploads/documents/{filename}').first_or_404()
    if current_user.role != 'ADMIN' and doc.user_id != current_user.id:
        abort(403)
    return send_from_directory(DOCUMENT_FOLDER, filename, as_attachment=True, download_name=doc.name)


def remove_document_file(doc):
    prefix = '/static/uploads/documents/'
    filename = doc.file_url.removeprefix(prefix)
    if not doc.file_url.startswith(prefix) or filename != os.path.basename(filename):
        abort(400)
    try:
        os.remove(os.path.join(DOCUMENT_FOLDER, filename))
    except OSError:
        pass


@app.route('/dashboard/documents/delete/<int:id>', methods=['POST'])
@login_required
def delete_document(id):
    doc = Document.query.get_or_404(id)
    if doc.user_id != current_user.id:
        return redirect(url_for('my_documents'))
    remove_document_file(doc)
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for('my_documents'))

@app.route('/admin/documents')
@login_required
def admin_documents():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    documents = Document.query.order_by(Document.uploaded_at.desc()).all()
    return render_template('admin_documents.html', documents=documents)

@app.route('/admin/documents/<int:id>/delete', methods=['POST'])
@login_required
def admin_delete_document(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    doc = Document.query.get_or_404(id)
    remove_document_file(doc)
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for('admin_documents'))

@app.route('/dashboard/invoices')
@login_required
def my_invoices():
    invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).all()
    return render_template('my_invoices.html', invoices=invoices)

@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    leads = Lead.query.order_by(Lead.created_at.desc()).limit(10).all()
    services = Service.query.all()
    projects = Project.query.all()
    users = User.query.all()
    bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    return render_template('admin.html', leads=leads, services=services, projects=projects, users=users, bookings=bookings, user=current_user)

@app.route('/admin/bookings')
@login_required
def admin_bookings():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    return render_template('admin_bookings.html', bookings=bookings)

@app.route('/admin/bookings/<int:id>/status', methods=['POST'])
@login_required
def admin_booking_status(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    booking = Booking.query.get_or_404(id)
    new_status = request.form.get('status')
    if new_status in ('PENDING', 'CONFIRMED', 'COMPLETED', 'CANCELLED'):
        booking.status = new_status
        db.session.commit()
    return redirect(url_for('admin_bookings'))

@app.route('/admin/leads')
@login_required
def admin_leads():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    return render_template('admin_leads.html', leads=leads)

@app.route('/admin/services')
@login_required
def admin_services():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    services = Service.query.all()
    return render_template('admin_services.html', services=services)

@app.route('/admin/services/new', methods=['GET', 'POST'])
@login_required
def admin_service_new():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        service = Service(
            name=request.form.get('name'),
            slug=request.form.get('slug'),
            description=request.form.get('description'),
            icon=request.form.get('icon'),
            price=float(request.form.get('price') or 0)
        )
        db.session.add(service)
        db.session.commit()
        return redirect(url_for('admin_services'))
    return render_template('admin_service_form.html', service=None)

@app.route('/admin/services/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_service_edit(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    service = Service.query.get_or_404(id)
    if request.method == 'POST':
        service.name = request.form.get('name')
        service.slug = request.form.get('slug')
        service.description = request.form.get('description')
        service.icon = request.form.get('icon')
        service.price = float(request.form.get('price') or 0)
        db.session.commit()
        return redirect(url_for('admin_services'))
    return render_template('admin_service_form.html', service=service)

@app.route('/admin/services/delete/<int:id>', methods=['POST'])
@login_required
def admin_service_delete(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    service = Service.query.get_or_404(id)
    db.session.delete(service)
    db.session.commit()
    return redirect(url_for('admin_services'))

@app.route('/admin/leads/<int:id>/status', methods=['POST'])
@login_required
def admin_lead_status(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    lead = Lead.query.get_or_404(id)
    new_status = request.form.get('status')
    if new_status in ('NEW', 'CONTACTED', 'CONVERTED'):
        lead.status = new_status
        db.session.commit()
    return redirect(url_for('admin_leads'))

@app.route('/admin/projects')
@login_required
def admin_projects():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    projects = Project.query.all()
    unread_counts = {}
    for project in projects:
        unread_counts[project.id] = ProjectMessage.query.filter_by(project_id=project.id, is_read_admin=False).count()
    return render_template('admin_projects.html', projects=projects, unread_counts=unread_counts)

@app.route('/admin/projects/new', methods=['GET', 'POST'])
@login_required
def admin_project_new():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        project = Project(
            title=request.form.get('title'),
            slug=request.form.get('slug'),
            category=request.form.get('category'),
            location=request.form.get('location'),
            description=request.form.get('description'),
            image=request.form.get('image'),
            status=request.form.get('status'),
            is_published=True if request.form.get('is_published') else False,
            user_id=int(user_id) if user_id else None
        )
        db.session.add(project)
        db.session.commit()
        return redirect(url_for('admin_projects'))
    customers = User.query.filter_by(role='CUSTOMER').all()
    return render_template('admin_project_edit.html', project=None, customers=customers)

@app.route('/admin/projects/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_project_edit(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    project = Project.query.get_or_404(id)
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        project.title = request.form.get('title')
        project.slug = request.form.get('slug')
        project.category = request.form.get('category')
        project.location = request.form.get('location')
        project.description = request.form.get('description')
        project.image = request.form.get('image')
        project.status = request.form.get('status')
        project.is_published = True if request.form.get('is_published') else False
        project.progress = request.form.get('progress', 0, type=int)
        project.user_id = int(user_id) if user_id else None
        db.session.commit()
        return redirect(url_for('admin_projects'))
    customers = User.query.filter_by(role='CUSTOMER').all()
    ProjectMessage.query.filter_by(project_id=id, is_read_admin=False).update({'is_read_admin': True})
    db.session.commit()
    return render_template('admin_project_edit.html', project=project, customers=customers)

@app.route('/admin/projects/<int:id>/message', methods=['POST'])
@login_required
def admin_project_message(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    project = Project.query.get_or_404(id)
    text = request.form.get('message', '').strip()
    if text:
        msg = ProjectMessage(project_id=id, sender_id=current_user.id, message=text, is_read_admin=True, is_read_customer=False)
        db.session.add(msg)
        db.session.commit()
        flash('Message sent!', 'success')
    return redirect(url_for('admin_project_edit', id=id))

@app.route('/admin/projects/delete/<int:id>', methods=['POST'])
@login_required
def admin_project_delete(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    project = Project.query.get_or_404(id)
    ProjectMessage.query.filter_by(project_id=project.id).delete()
    db.session.delete(project)
    db.session.commit()
    return redirect(url_for('admin_projects'))

@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/users/<int:id>/role', methods=['POST'])
@login_required
def admin_user_role(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if id == current_user.id:
        return redirect(url_for('admin_users'))
    user = User.query.get_or_404(id)
    user.role = request.form.get('role')
    db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:id>/toggle', methods=['POST'])
@login_required
def admin_user_toggle(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if id == current_user.id:
        return redirect(url_for('admin_users'))
    user = User.query.get_or_404(id)
    user.is_active = not user.is_active
    db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:id>/delete', methods=['POST'])
@login_required
def admin_user_delete(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if id == current_user.id:
        return redirect(url_for('admin_users'))
    user = User.query.get_or_404(id)
    bookings = Booking.query.filter_by(user_id=user.id).all()
    for b in bookings:
        db.session.delete(b)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:id>/vip', methods=['POST'])
@login_required
def admin_user_vip(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    user = User.query.get_or_404(id)
    user.is_vip = not user.is_vip
    db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:id>/details')
@login_required
def admin_user_details(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    user = User.query.get_or_404(id)
    bookings = Booking.query.filter_by(user_id=user.id).all()
    projects = Project.query.filter_by(user_id=user.id).all()
    return render_template('user_detail.html', user=user, bookings=bookings, projects=projects)

@app.route('/admin/faq')
@login_required
def admin_faq():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    faqs = FAQ.query.order_by(FAQ.order).all()
    return render_template('admin_faq.html', faqs=faqs)

@app.route('/admin/faq/new', methods=['GET', 'POST'])
@login_required
def admin_faq_new():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        faq = FAQ(
            question=request.form.get('question'),
            answer=request.form.get('answer')
        )
        db.session.add(faq)
        db.session.commit()
        return redirect(url_for('admin_faq'))
    return render_template('admin_faq_form.html', faq=None)

@app.route('/admin/faq/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_faq_edit(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    faq = FAQ.query.get_or_404(id)
    if request.method == 'POST':
        faq.question = request.form.get('question')
        faq.answer = request.form.get('answer')
        db.session.commit()
        return redirect(url_for('admin_faq'))
    return render_template('admin_faq_form.html', faq=faq)

@app.route('/admin/faq/delete/<int:id>', methods=['POST'])
@login_required
def admin_faq_delete(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    faq = FAQ.query.get_or_404(id)
    db.session.delete(faq)
    db.session.commit()
    return redirect(url_for('admin_faq'))

@app.route('/admin/blog')
@login_required
def admin_blog():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    posts = BlogPost.query.order_by(BlogPost.created_at.desc()).all()
    return render_template('admin_blog.html', posts=posts)

@app.route('/admin/blog/new', methods=['GET', 'POST'])
@login_required
def admin_blog_new():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        post = BlogPost(
            title=request.form.get('title'),
            slug=request.form.get('slug'),
            excerpt=request.form.get('excerpt'),
            content=request.form.get('content'),
            image=request.form.get('image'),
            category=request.form.get('category'),
            status=request.form.get('status')
        )
        db.session.add(post)
        db.session.commit()
        return redirect(url_for('admin_blog'))
    return render_template('admin_blog_form.html', post=None)

@app.route('/admin/blog/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_blog_edit(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    post = BlogPost.query.get_or_404(id)
    if request.method == 'POST':
        post.title = request.form.get('title')
        post.slug = request.form.get('slug')
        post.excerpt = request.form.get('excerpt')
        post.content = request.form.get('content')
        post.image = request.form.get('image')
        post.category = request.form.get('category')
        post.status = request.form.get('status')
        db.session.commit()
        return redirect(url_for('admin_blog'))
    return render_template('admin_blog_form.html', post=post)

@app.route('/admin/blog/delete/<int:id>', methods=['POST'])
@login_required
def admin_blog_delete(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    post = BlogPost.query.get_or_404(id)
    db.session.delete(post)
    db.session.commit()
    return redirect(url_for('admin_blog'))

@app.route('/admin/invoices')
@login_required
def admin_invoices():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
    users = User.query.all()
    return render_template('admin_invoices.html', invoices=invoices, users=users)

@app.route('/admin/invoices/create', methods=['POST'])
@login_required
def create_invoice():
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    user_id = request.form.get('user_id', type=int)
    amount = request.form.get('amount', type=float)
    due_days = request.form.get('due_days', 30, type=int)
    items = request.form.get('items', '[]')
    notes = request.form.get('notes', '')
    if not user_id or not amount:
        return redirect(url_for('admin_invoices'))
    user = db.session.get(User, user_id)
    if not user:
        return redirect(url_for('admin_invoices'))
    invoice = Invoice(
        user_id=user_id,
        number=generate_invoice_number(),
        amount=amount,
        status='DRAFT',
        due_date=datetime.utcnow() + timedelta(days=due_days),
        items=items,
        notes=notes
    )
    db.session.add(invoice)
    db.session.commit()
    return redirect(url_for('admin_invoices'))

@app.route('/admin/invoices/<int:id>/status', methods=['POST'])
@login_required
def update_invoice_status(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    invoice = Invoice.query.get_or_404(id)
    status = request.form.get('status')
    if status in ['DRAFT', 'SENT', 'PAID', 'OVERDUE', 'CANCELLED']:
        was_sent_already = invoice.status == 'SENT'
        invoice.status = status
        if status == 'PAID':
            invoice.paid_at = datetime.utcnow()
        db.session.commit()

        if status == 'SENT' and not was_sent_already:
            invoice_link = f"{os.getenv('FRONTEND_URL')}/dashboard/invoices"
            html = f"""
            <h2>New Invoice from ApexBuild Interiors</h2>
            <p>Hello {invoice.user.first_name},</p>
            <p>You have a new invoice <strong>#{invoice.number}</strong> for <strong>${invoice.amount:,.2f}</strong>,
            due on <strong>{invoice.due_date.strftime('%B %d, %Y')}</strong>.</p>
            <p><a href="{invoice_link}">View and download your invoice</a></p>
            <p>Thank you for choosing ApexBuild Interiors.</p>
            """
            try:
                asyncio.run(email_service.send_email(invoice.user.email, f"Invoice #{invoice.number} from ApexBuild Interiors", html))
            except Exception:
                app.logger.warning('Invoice email delivery failed.')

    return redirect(url_for('admin_invoices'))

@app.route('/admin/invoices/<int:id>/delete', methods=['POST'])
@login_required
def delete_invoice(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    invoice = Invoice.query.get_or_404(id)
    db.session.delete(invoice)
    db.session.commit()
    return redirect(url_for('admin_invoices'))

@app.route('/invoices/<int:id>/pdf')
@login_required
def invoice_pdf(id):
    invoice = Invoice.query.get_or_404(id)
    if current_user.role != 'ADMIN' and invoice.user_id != current_user.id:
        return redirect(url_for('dashboard'))

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    p.setFont("Helvetica-Bold", 22)
    p.drawString(1 * inch, height - 1 * inch, "ApexBuild Interiors")
    p.setFont("Helvetica", 10)
    p.drawString(1 * inch, height - 1.25 * inch, "Premium Interior Design & Renovation")

    p.setFont("Helvetica-Bold", 16)
    p.drawRightString(width - 1 * inch, height - 1 * inch, "INVOICE")
    p.setFont("Helvetica", 10)
    p.drawRightString(width - 1 * inch, height - 1.25 * inch, f"#{invoice.number}")

    p.line(1 * inch, height - 1.5 * inch, width - 1 * inch, height - 1.5 * inch)

    y = height - 1.9 * inch
    p.setFont("Helvetica-Bold", 11)
    p.drawString(1 * inch, y, "Bill To:")
    p.setFont("Helvetica", 10)
    p.drawString(1 * inch, y - 0.2 * inch, f"{invoice.user.first_name} {invoice.user.last_name}")
    p.drawString(1 * inch, y - 0.4 * inch, invoice.user.email)

    p.setFont("Helvetica-Bold", 11)
    p.drawRightString(width - 1 * inch, y, "Status:")
    p.setFont("Helvetica", 10)
    p.drawRightString(width - 1 * inch, y - 0.2 * inch, invoice.status)
    p.drawRightString(width - 1 * inch, y - 0.4 * inch, f"Due: {invoice.due_date.strftime('%B %d, %Y')}")
    p.drawRightString(width - 1 * inch, y - 0.6 * inch, f"Issued: {invoice.created_at.strftime('%B %d, %Y')}")

    y -= 1.1 * inch
    p.setFont("Helvetica-Bold", 10)
    p.drawString(1 * inch, y, "Description")
    p.drawRightString(width - 1 * inch, y, "Amount")
    y -= 0.1 * inch
    p.line(1 * inch, y, width - 1 * inch, y)
    y -= 0.3 * inch

    p.setFont("Helvetica", 10)
    try:
        items = json.loads(invoice.items) if invoice.items else []
    except (ValueError, TypeError):
        items = []

    if items:
        for item in items:
            desc = item.get('desc') or item.get('description') or item.get('name', 'Item')
            qty = item.get('qty', 1)
            price = item.get('price') or item.get('amount', 0)
            try:
                line_total = float(qty) * float(price)
            except (ValueError, TypeError):
                line_total = price
            p.drawString(1 * inch, y, f"{desc} (x{qty})" if qty else str(desc))
            p.drawRightString(width - 1 * inch, y, f"${line_total:,.2f}")
            y -= 0.3 * inch
    else:
        p.drawString(1 * inch, y, "Services rendered")
        p.drawRightString(width - 1 * inch, y, f"${invoice.amount:,.2f}")
        y -= 0.3 * inch

    y -= 0.1 * inch
    p.line(1 * inch, y, width - 1 * inch, y)
    y -= 0.35 * inch

    p.setFont("Helvetica-Bold", 13)
    p.drawRightString(width - 1 * inch, y, f"Total: ${invoice.amount:,.2f}")

    if invoice.notes:
        y -= 0.6 * inch
        p.setFont("Helvetica-Oblique", 9)
        p.drawString(1 * inch, y, f"Notes: {invoice.notes}")

    p.setFont("Helvetica", 8)
    p.drawCentredString(width / 2, 0.7 * inch, "Thank you for choosing ApexBuild Interiors.")

    p.showPage()
    p.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"invoice_{invoice.number}.pdf",
        mimetype='application/pdf'
    )

@app.route('/faq')
def faq():
    faqs = FAQ.query.order_by(FAQ.order).all()
    return render_template('faq.html', faqs=faqs)

@app.route('/book')
def book():
    services = Service.query.filter_by(is_active=True).all()
    return render_template('book.html', services=services)

@app.route('/book/submit', methods=['POST'])
@login_required
def book_submit():
    booking = Booking(
        user_id=current_user.id,
        service=request.form.get('service'),
        date=request.form.get('date'),
        time=request.form.get('time'),
        notes=request.form.get('notes')
    )
    db.session.add(booking)
    db.session.commit()

    try:
        asyncio.run(email_service.send_booking_confirmation(
            current_user.email,
            current_user.first_name,
            {'date': booking.date, 'time': booking.time, 'service': booking.service}
        ))
    except Exception:
        app.logger.warning('Booking confirmation email delivery failed.')

    return redirect(url_for('dashboard'))

@app.route('/my-bookings/<int:id>/cancel', methods=['POST'])
@login_required
def cancel_booking(id):
    booking = Booking.query.get_or_404(id)
    if booking.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    if booking.status == 'PENDING':
        booking.status = 'CANCELLED'
        db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/payment')
@login_required
def payment():
    return render_template('payment.html')

@app.route('/create-payment-intent', methods=['POST'])
@login_required
def create_payment_intent():
    data = request.get_json(silent=True)
    amount = data.get('amount') if isinstance(data, dict) else None
    if type(amount) is not int or not 50 <= amount <= 99999999:
        return jsonify(error='Enter a valid amount in whole cents between 50 and 99999999.'), 400
    if not stripe.api_key or not stripe.api_key.startswith(('sk_test_', 'rk_test_')):
        return jsonify(error='Payment preparation is unavailable. Live payments are disabled.'), 503
    try:
        intent = stripe.PaymentIntent.create(amount=amount, currency='usd',
                                             metadata={'user_id': str(current_user.id)})
        # Creating an intent is not a charge confirmation. Never mark invoices paid.
        return jsonify(status='pending', payment_id=intent.id,
                       message='Payment intent prepared. Payment has not been confirmed.'), 202
    except Exception:
        app.logger.warning('Payment intent preparation failed.')
        return jsonify(error='Payment preparation is unavailable. Please try again later.'), 502

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(email)
            try:
                asyncio.run(email_service.send_password_reset_email(email, user.first_name, token))
            except Exception:
                app.logger.warning('Password reset email delivery failed.')
            return render_template('forgot_password.html', success='If the account exists, a password reset has been requested.')
        return render_template('forgot_password.html', success='If the account exists, a password reset has been requested.')
    return render_template('forgot_password.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token')
    if request.method == 'POST':
        token = request.form.get('token')
        email = verify_reset_token(token)
        if not email:
            return render_template('reset_password.html', error='Invalid or expired token.')
        password = request.form.get('password')
        if not valid_new_password(password):
            return render_template('reset_password.html', token=token, error='Use an 8–72 byte password that is not a known default.'), 400
        user = User.query.filter_by(email=email).first()
        if user:
            user.password = hash_password(password)
            db.session.commit()
            return render_template('reset_password.html', success='Password reset successfully.')
        return render_template('reset_password.html', error='User not found.')
    return render_template('reset_password.html', token=token)

@app.route('/verify-email')
def verify_email():
    token = request.args.get('token')
    if not token:
        return render_template('verify_email.html', error='Invalid verification link.')
    email = verify_verification_token(token)
    if not email:
        return render_template('verify_email.html', error='Invalid or expired token.')
    user = User.query.filter_by(email=email).first()
    if not user:
        return render_template('verify_email.html', error='User not found.')
    user.is_verified = True
    db.session.commit()
    return render_template('verify_email.html', success='Email verified successfully! You can now login.')

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

# ==================== UTILITY FUNCTIONS ====================

def generate_verification_token(email):
    return jwt.encode({'email': email, 'purpose': 'verify_email',
                       'exp': datetime.utcnow() + timedelta(hours=24)},
                      app.config['SECRET_KEY'], algorithm='HS256')


def decode_token(token, purpose):
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'],
                             options={'require': ['exp', 'email', 'purpose']})
        if payload['purpose'] == purpose and isinstance(payload['email'], str):
            return payload
    except jwt.InvalidTokenError:
        pass
    return None


def verify_verification_token(token):
    payload = decode_token(token, 'verify_email')
    return payload['email'] if payload else None


def generate_reset_token(email):
    user = User.query.filter_by(email=email).first()
    if not user:
        return None
    return jwt.encode({'email': email, 'purpose': 'reset_password',
                       'password_version': password_version(user),
                       'exp': datetime.utcnow() + timedelta(hours=1)},
                      app.config['SECRET_KEY'], algorithm='HS256')


def verify_reset_token(token):
    payload = decode_token(token, 'reset_password')
    if payload:
        user = User.query.filter_by(email=payload['email']).first()
        if user and payload.get('password_version') == password_version(user):
            return user.email
    return None

def generate_invoice_number():
    import random
    import string
    prefix = 'INV-'
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}{random_part}"

DOCUMENT_FOLDER = 'static/uploads/documents'
ALLOWED_DOCUMENT_TYPES = {'pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png'}

def allowed_document(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_DOCUMENT_TYPES

# ==================== SEED DATA ====================

def seed_data():
    services = [
        {'name': 'Residential Design', 'slug': 'residential-design', 'description': 'Custom interior design for homes.', 'icon': '🏠', 'price': 15000},
        {'name': 'Commercial Design', 'slug': 'commercial-design', 'description': 'Professional design for offices.', 'icon': '🏢', 'price': 25000},
        {'name': 'Renovation', 'slug': 'renovation', 'description': 'Complete renovation services.', 'icon': '🔨', 'price': 30000},
    ]
    for data in services:
        if not Service.query.filter_by(slug=data['slug']).first():
            service = Service(**data)
            db.session.add(service)
    
    projects = [
        {'title': 'Modern Manhattan Residence', 'slug': 'modern-manhattan', 'category': 'Residential', 'location': 'New York, NY', 'description': 'Stunning modern renovation.', 'image': 'https://images.unsplash.com/photo-1618220179428-22790b461013?w=600', 'is_published': True},
        {'title': 'Brooklyn Brownstone', 'slug': 'brooklyn-brownstone', 'category': 'Renovation', 'location': 'Brooklyn, NY', 'description': 'Complete brownstone renovation.', 'image': 'https://images.unsplash.com/photo-1586023492125-27b2c045efd7?w=600', 'is_published': True},
    ]
    for data in projects:
        if not Project.query.filter_by(slug=data['slug']).first():
            project = Project(**data)
            db.session.add(project)
    
    if not BlogPost.query.first():
        post = BlogPost(
            title='Welcome to ApexBuild Interiors',
            slug='welcome-to-apexbuild-interiors',
            excerpt='Welcome to our blog! Stay tuned for design insights.',
            content='Welcome to ApexBuild Interiors blog. We will share design tips, trends, and inspiration.',
            category='General',
            status='PUBLISHED'
        )
        db.session.add(post)
    
    db.session.commit()

@app.route('/quote')
def quote():
    return render_template('quote.html')

@app.route('/quote/submit', methods=['POST'])
def quote_submit():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    service = request.form.get('service', '').strip()
    description = request.form.get('description', '').strip()
    if (not name or len(name) > 100 or not valid_email(email) or len(phone) > 20 or
            not description or len(description) > 10000 or
            service not in ('', 'Residential Design', 'Commercial Design', 'Renovation')):
        return render_template('quote.html', error='Please enter valid contact details and a project description.'), 400
    lead = Lead(name=name, email=email, phone=phone,
                message=f'Service: {service or "Not specified"}\n\n{description}', status='NEW')
    db.session.add(lead)
    db.session.commit()
    return render_template('quote.html', success="Quote submitted! We'll get back to you soon.")


def seed_demo_accounts():
    if os.getenv('SEED_DEMO_ACCOUNTS') != '1':
        return
    if app.config['APP_ENV'] != 'development':
        raise RuntimeError('Demo accounts are permitted only in development.')
    accounts = []
    for prefix, role, name in [('DEMO_ADMIN', 'ADMIN', 'Admin'), ('DEMO_CUSTOMER', 'CUSTOMER', 'Demo')]:
        email = os.getenv(f'{prefix}_EMAIL', '')
        password = os.getenv(f'{prefix}_PASSWORD', '')
        if not valid_email(email) or not valid_new_password(password) or len(password) < 12:
            raise RuntimeError('Demo accounts require explicit valid emails and unique passwords of at least 12 characters.')
        accounts.append((email, password, role, name))
    if accounts[0][0] == accounts[1][0] or accounts[0][1] == accounts[1][1]:
        raise RuntimeError('Demo accounts require distinct emails and passwords.')
    for email, password, role, name in accounts:
        if not User.query.filter_by(email=email).first():
            db.session.add(User(email=email, password=hash_password(password), first_name=name,
                                last_name='User', role=role, is_verified=True))
    db.session.commit()


with app.app_context():
    if os.getenv('SEED_DEMO_ACCOUNTS') == '1' and app.config['APP_ENV'] != 'development':
        raise RuntimeError('Demo accounts are permitted only in development.')
    db.create_all()
    seed_data()
    seed_demo_accounts()

if __name__ == '__main__':
    app.run(debug=False, host='127.0.0.1', port=5000)
