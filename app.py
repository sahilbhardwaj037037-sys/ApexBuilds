from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_cors import CORS
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
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///apex_xbuild.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
CORS(app)

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

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

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# ==================== EMAIL SERVICE ====================

class EmailService:
    def __init__(self):
        self.api_key = os.getenv('RESEND_API_KEY')
        self.from_email = os.getenv('EMAIL_FROM', 'noreply@apexbuildinteriors.com')
        self.base_url = "https://api.resend.com"
    
    async def send_email(self, to, subject, html, text=None):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/emails",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self.from_email,
                        "to": [to],
                        "subject": subject,
                        "html": html,
                        "text": text or html,
                    },
                )
                return response.status_code == 200
        except Exception as e:
            print(f"Email error: {e}")
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

# Mock email if no real API key is configured (missing OR a placeholder like 're_xxxx...')
import httpx
_resend_key = os.getenv('RESEND_API_KEY')
if not _resend_key or _resend_key.startswith('re_xxxx'):
    async def mock_send_email(self, to, subject, html, text=None):
        print(f"\n📧 ===== EMAIL MOCK =====", flush=True)
        print(f"To: {to}", flush=True)
        print(f"Subject: {subject}", flush=True)
        link = html.split('href="')[1].split('"')[0] if 'href="' in html else 'no link'
        print(f"Link: {link}", flush=True)
        print(f"=========================\n", flush=True)
        return True
    EmailService.send_email = mock_send_email
    print("🔧 Using mock email (console output only)", flush=True)

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
        lead = Lead(
            name=request.form.get('name'),
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            message=request.form.get('message')
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
            login_user(user)
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
        db.session.commit()
        # Send verification email
        token = generate_verification_token(email)
        try:
            asyncio.run(email_service.send_verification_email(email, first_name, token))
        except Exception as e:
            print(f"Verification email failed: {e}", flush=True)
        return render_template('register.html', success='Registration successful! Please check your email to verify.')
    return render_template('register.html')

@app.route('/logout')
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
    os.makedirs(DOCUMENT_FOLDER, exist_ok=True)
    filepath = os.path.join(DOCUMENT_FOLDER, filename)
    file.save(filepath)
    doc = Document(
        user_id=current_user.id,
        name=filename,
        file_url=f'/{DOCUMENT_FOLDER}/{filename}',
        file_type=request.form.get('file_type', 'OTHER')
    )
    db.session.add(doc)
    db.session.commit()
    return redirect(url_for('my_documents'))

@app.route('/dashboard/documents/delete/<int:id>')
@login_required
def delete_document(id):
    doc = Document.query.get_or_404(id)
    if doc.user_id != current_user.id:
        return redirect(url_for('my_documents'))
    try:
        os.remove(doc.file_url[1:])
    except:
        pass
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

@app.route('/admin/documents/<int:id>/delete')
@login_required
def admin_delete_document(id):
    if current_user.role != 'ADMIN':
        return redirect(url_for('dashboard'))
    doc = Document.query.get_or_404(id)
    try:
        os.remove(doc.file_url[1:])
    except OSError:
        pass
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

@app.route('/admin/services/delete/<int:id>')
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

@app.route('/admin/projects/delete/<int:id>')
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

@app.route('/admin/users/<int:id>/delete')
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

@app.route('/admin/faq/delete/<int:id>')
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

@app.route('/admin/blog/delete/<int:id>')
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
            except Exception as e:
                print(f"Invoice email failed: {e}", flush=True)

    return redirect(url_for('admin_invoices'))

@app.route('/admin/invoices/<int:id>/delete')
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
    except Exception as e:
        print(f"Booking confirmation email failed: {e}", flush=True)

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
    try:
        data = request.json
        amount = data.get('amount', 10000)
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='usd',
            metadata={'user_id': current_user.id}
        )
        return jsonify({
            'success': True,
            'payment_id': intent.id,
            'client_secret': intent.client_secret
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(email)
            try:
                asyncio.run(email_service.send_password_reset_email(email, user.first_name, token))
            except Exception as e:
                print(f"Password reset email failed: {e}", flush=True)
            return render_template('forgot_password.html', success='Password reset link sent to your email.')
        return render_template('forgot_password.html', error='Email not found.')
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
    return response

# ==================== UTILITY FUNCTIONS ====================

def generate_verification_token(email):
    payload = {
        'email': email,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def verify_verification_token(token):
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload.get('email')
    except:
        return None

def generate_reset_token(email):
    payload = {
        'email': email,
        'exp': datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def verify_reset_token(token):
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload.get('email')
    except:
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
    
    if not User.query.filter_by(email='admin@apexbuild.com').first():
        admin = User(
            email='admin@apexbuild.com',
            password=hash_password('Admin123!'),
            first_name='Admin',
            last_name='User',
            role='ADMIN',
            is_verified=True
        )
        db.session.add(admin)

    if not User.query.filter_by(email='demo@apexbuild.com').first():
        demo_user = User(
            email='demo@apexbuild.com',
            password=hash_password('Demo123!'),
            first_name='Demo',
            last_name='User',
            role='CUSTOMER',
            is_verified=True
        )
        db.session.add(demo_user)
    
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
    print('✅ Database seeded!')
    print('📧 Admin: admin@apexbuild.com')
    print('🔑 Password: Admin123!')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_data()
    app.run(debug=True, host='0.0.0.0', port=5000)

@app.route('/quote')
def quote():
    return render_template('quote.html')

@app.route('/quote/submit', methods=['POST'])
def quote_submit():
    lead = Lead(
        name=request.form.get('name'),
        email=request.form.get('email'),
        phone=request.form.get('phone'),
        service=request.form.get('service'),
        message=request.form.get('description'),
        status='NEW'
    )
    db.session.add(lead)
    db.session.commit()
    return render_template('quote.html', success='Quote submitted! We\'ll get back to you soon.')
