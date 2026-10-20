"""Local regression tests: in-memory SQLite, temporary uploads, no outbound sockets."""
import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from flask.testing import FlaskClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_ENV = {'APP_ENV': 'testing', 'SECRET_KEY': secrets.token_hex(32),
            'DATABASE_URL': 'sqlite://', 'RESEND_API_KEY': '', 'STRIPE_SECRET_KEY': ''}
# Never load the operator's .env, database or integrations, including on import.
NETWORK = patch('socket.socket.connect', side_effect=AssertionError('External networking is forbidden'))
NETWORK.start()
with patch.dict(os.environ, TEST_ENV, clear=True), patch('dotenv.load_dotenv', return_value=False):
    import app as application

app = application.app
db = application.db


class RequestContextClient(FlaskClient):
    def open(self, *args, **kwargs):
        # Tests retain an outer context for fixtures. Each real request must have
        # its own g/current_user and SQLAlchemy session, as it does on the server.
        with app.app_context():
            response = super().open(*args, **kwargs)
        db.session.expire_all()
        return response


app.test_client_class = RequestContextClient


class SecurityTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        application.seed_data()
        self.client = app.test_client()
        self.files = tempfile.TemporaryDirectory()
        self.folder = patch.object(application, 'DOCUMENT_FOLDER', self.files.name)
        self.folder.start()
        self.addCleanup(self.files.cleanup)
        self.addCleanup(self.folder.stop)
        self.addCleanup(self.context.pop)
        self.addCleanup(db.session.remove)
        self.password = secrets.token_urlsafe(18)

    def user(self, role='CUSTOMER', email=None, verified=True, active=True):
        user = application.User(email=email or secrets.token_hex(5) + '@example.com',
                                password=application.hash_password(self.password), first_name='Local',
                                last_name='Test', role=role, is_verified=verified, is_active=active)
        db.session.add(user)
        db.session.commit()
        return user

    def token(self, client=None, page='/login'):
        client = client or self.client
        client.get(page)
        with client.session_transaction() as session:
            return session['_csrf_token']

    def post(self, path, data=None, client=None, **kwargs):
        client = client or self.client
        if 'json' in kwargs:
            kwargs['headers'] = {'X-CSRF-Token': self.token(client)}
        else:
            data = dict(data or {}, csrf_token=self.token(client))
        return client.post(path, data=data, **kwargs)

    def login(self, user, client=None):
        return self.post('/login', {'email': user.email, 'password': self.password}, client)

    def test_public_routes_and_no_default_accounts(self):
        self.assertEqual(application.User.query.count(), 0)
        for path in ['/', '/services', '/projects', '/projects/modern-manhattan', '/blog',
                     '/faq', '/about', '/contact', '/quote', '/login', '/register', '/book',
                     '/forgot-password', '/robots.txt', '/sitemap.xml']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_demo_seeding_preserves_existing_account_password_and_role(self):
        user = self.user(email='existing@example.com')
        original_hash = user.password
        config = {'SEED_DEMO_ACCOUNTS': '1', 'DEMO_ADMIN_EMAIL': user.email,
                  'DEMO_ADMIN_PASSWORD': secrets.token_urlsafe(18),
                  'DEMO_CUSTOMER_EMAIL': 'customer@example.com',
                  'DEMO_CUSTOMER_PASSWORD': secrets.token_urlsafe(18)}
        with patch.dict(app.config, APP_ENV='development'), patch.dict(os.environ, config, clear=True):
            application.seed_demo_accounts()
            application.seed_demo_accounts()
        self.assertEqual(application.User.query.count(), 2)
        self.assertEqual(user.password, original_hash)
        self.assertEqual(user.role, 'CUSTOMER')

    def test_all_templates_compile_and_forms_have_csrf(self):
        for name in app.jinja_env.list_templates():
            app.jinja_env.get_template(name)
        for file in (ROOT / 'templates').glob('*.html'):
            for form in re.findall(r'<form\b[^>]*method="POST"[^>]*>.*?</form>', file.read_text(), re.S | re.I):
                self.assertIn('name="csrf_token"', form, file.name)

    def test_csrf_blocks_mutations_before_writes_or_provider_calls(self):
        with patch.object(application.stripe.PaymentIntent, 'create') as create:
            for path in ['/register', '/login', '/quote/submit', '/contact', '/forgot-password',
                         '/reset-password', '/create-payment-intent', '/admin/users/1/role']:
                self.assertEqual(self.client.post(path, json={'amount': 100}).status_code, 400)
            create.assert_not_called()
        self.assertEqual(application.User.query.count(), 0)
        self.assertEqual(application.Lead.query.count(), 0)
        self.token()
        self.assertEqual(self.client.post('/quote/submit', data={'csrf_token': '不正'}).status_code, 400)

    def test_authentication_and_role_boundaries(self):
        user = self.user()
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.assertEqual(self.login(user).status_code, 302)
        self.assertEqual(self.client.get('/dashboard').status_code, 200)
        self.assertEqual(self.client.get('/admin').location, '/dashboard')
        self.assertEqual(self.post('/admin/users/1/role', {'role': 'ADMIN'}).status_code, 302)
        self.assertEqual(user.role, 'CUSTOMER')
        self.assertEqual(self.post('/logout').status_code, 302)
        admin = self.user('ADMIN')
        self.login(admin)
        for path in ['/admin', '/admin/users', '/admin/services', '/admin/projects', '/admin/leads',
                     '/admin/bookings', '/admin/documents', '/admin/invoices', '/admin/blog', '/admin/faq']:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_inactive_unverified_and_malformed_password_login_rejected(self):
        for verified, active in [(False, True), (True, False)]:
            user = self.user(verified=verified, active=active)
            self.login(user)
            self.assertEqual(self.client.get('/dashboard').status_code, 302)
        for password in [None, '', 'x' * 73]:
            self.assertFalse(application.verify_password(password, 'invalid-hash'))

    def test_known_compromised_passwords_are_rejected(self):
        # Fixed fixture passwords from historical commits are represented only by
        # SHA256 fingerprints in current source. Exercise the denylist without
        # reproducing those passwords in the test suite.
        with patch.object(application, 'COMPROMISED_PASSWORD_DIGESTS',
                          {hashlib.sha256(self.password.encode()).hexdigest()}):
            user = self.user()
            self.assertFalse(application.valid_new_password(self.password))
            self.login(user)
            self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_legacy_and_password_changed_sessions_are_invalid(self):
        user = self.user()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.login(user)
        user.password = application.hash_password(secrets.token_urlsafe(18))
        db.session.commit()
        self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_registration_validation_and_verification_flow(self):
        data = {'email': 'new@example.com', 'first_name': 'New', 'last_name': 'User', 'password': self.password}
        bad = dict(data, password='short')
        self.assertEqual(self.post('/register', bad).status_code, 400)
        self.assertEqual(application.User.query.count(), 0)
        with patch.object(application.email_service, 'send_verification_email', new_callable=AsyncMock, return_value=True) as send:
            self.assertEqual(self.post('/register', data).status_code, 200)
            token = send.call_args.args[2]
        user = application.User.query.one()
        self.assertFalse(user.is_verified)
        self.assertEqual(self.client.get('/verify-email', query_string={'token': token}).status_code, 200)
        self.assertTrue(user.is_verified)
        self.assertEqual(self.login(user).status_code, 302)
        self.assertEqual(self.client.get('/dashboard').status_code, 200)

    def test_token_purposes_expiry_reset_replay_and_sessions(self):
        user = self.user()
        verify = application.generate_verification_token(user.email)
        reset = application.generate_reset_token(user.email)
        self.assertIsNone(application.verify_reset_token(verify))
        self.assertIsNone(application.verify_verification_token(reset))
        expired = application.jwt.encode({'email': user.email, 'purpose': 'reset_password',
                                          'exp': datetime.utcnow() - timedelta(seconds=1),
                                          'password_version': application.password_version(user)},
                                         app.config['SECRET_KEY'], algorithm='HS256')
        self.assertIsNone(application.verify_reset_token(expired))
        self.assertIsNone(application.verify_reset_token('malformed'))
        self.login(user)
        other = app.test_client()
        self.assertEqual(self.post('/reset-password', {'token': reset, 'password': 'short'}, other).status_code, 400)
        self.assertIsNotNone(application.verify_reset_token(reset))
        self.assertEqual(self.post('/reset-password', {'token': reset, 'password': secrets.token_urlsafe(18)}, other).status_code, 200)
        self.assertIsNone(application.verify_reset_token(reset))
        self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_reset_request_does_not_enumerate_accounts(self):
        user = self.user()
        with patch.object(application.email_service, 'send_password_reset_email', new_callable=AsyncMock, return_value=False):
            existing = self.post('/forgot-password', {'email': user.email})
            absent = self.post('/forgot-password', {'email': 'missing@example.com'})
        phrase = b'If the account exists, a password reset has been requested.'
        self.assertIn(phrase, existing.data)
        self.assertIn(phrase, absent.data)

    def test_quote_records_service_in_existing_lead_message(self):
        data = {'name': 'Local Test', 'email': 'quote@example.com', 'phone': '12345678',
                'service': 'Renovation', 'description': 'A local project enquiry.'}
        response = self.post('/quote/submit', data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Quote submitted!', response.data)
        lead = application.Lead.query.one()
        self.assertIn('Renovation', lead.message)
        self.assertIn(data['description'], lead.message)
        for invalid in [dict(data, name=''), dict(data, email='invalid'), dict(data, description=''),
                        dict(data, service='invalid'), dict(data, description='x' * 10001)]:
            self.assertEqual(self.post('/quote/submit', invalid).status_code, 400)
        self.assertEqual(application.Lead.query.count(), 1)

    def test_contact_and_admin_post_delete_preserve_behavior(self):
        self.assertEqual(self.post('/contact', {'name': 'Missing fields'}).status_code, 400)
        self.assertEqual(self.post('/contact', {'name': 'Local Test', 'email': 'local@example.com',
                                              'phone': '', 'message': 'Local enquiry'}).status_code, 200)
        self.assertEqual(application.Lead.query.count(), 1)
        admin = self.user('ADMIN')
        self.login(admin)
        service = application.Service.query.first()
        service_id = service.id
        self.assertEqual(self.client.get(f'/admin/services/delete/{service_id}').status_code, 405)
        self.assertEqual(self.client.post(f'/admin/services/delete/{service_id}').status_code, 400)
        self.assertIsNotNone(db.session.get(application.Service, service_id))
        self.assertEqual(self.post(f'/admin/services/delete/{service_id}').status_code, 302)
        self.assertIsNone(db.session.get(application.Service, service_id))
        self.assertEqual(self.client.get('/logout').status_code, 405)

    def test_admin_invoice_status_and_customer_pdf_permissions(self):
        owner = self.user()
        invoice = application.Invoice(user_id=owner.id, number='LOCAL-PDF', amount=10,
                                      status='DRAFT', due_date=datetime.utcnow() + timedelta(days=1))
        db.session.add(invoice)
        db.session.commit()
        invoice_id = invoice.id
        self.login(owner)
        self.assertEqual(self.post(f'/admin/invoices/{invoice_id}/status', {'status': 'PAID'}).status_code, 302)
        self.assertEqual(invoice.status, 'DRAFT')
        pdf = self.client.get(f'/invoices/{invoice_id}/pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b'%PDF'))
        pdf.close()
        admin = self.user('ADMIN')
        self.post('/logout')
        self.login(admin)
        # This preserves the explicit admin bookkeeping action; payment
        # preparation itself is tested separately and never changes this status.
        self.assertEqual(self.post(f'/admin/invoices/{invoice_id}/status', {'status': 'PAID'}).status_code, 302)
        self.assertEqual(invoice.status, 'PAID')
        self.assertIsNotNone(invoice.paid_at)

    def test_every_delete_route_requires_post(self):
        routes = [r for r in app.url_map.iter_rules() if '/delete' in r.rule or r.rule == '/logout']
        self.assertEqual(len(routes), 9)
        for rule in routes:
            self.assertNotIn('GET', rule.methods, rule.rule)
            self.assertIn('POST', rule.methods, rule.rule)

    def test_payment_auth_and_input_validation(self):
        with patch.object(application.stripe.PaymentIntent, 'create') as create:
            self.assertEqual(self.post('/create-payment-intent', json={'amount': 100}).status_code, 302)
            user = self.user()
            self.login(user)
            for data in [None, [], {}, {'amount': True}, {'amount': 0}, {'amount': 49}, {'amount': -100},
                         {'amount': 100000000}, {'amount': 100.5}, {'amount': '100'}]:
                if data is None:
                    result = self.post('/create-payment-intent')
                else:
                    result = self.post('/create-payment-intent', json=data)
                self.assertEqual(result.status_code, 400)
            create.assert_not_called()

    def test_payment_is_test_only_and_pending_never_marks_invoice_paid(self):
        user = self.user()
        self.login(user)
        invoice = application.Invoice(user_id=user.id, number='LOCAL-1', amount=10, status='SENT',
                                      due_date=datetime.utcnow() + timedelta(days=1))
        db.session.add(invoice)
        db.session.commit()
        with patch.object(application.stripe, 'api_key', 'sk_test_local_fixture'), patch.object(application.stripe.PaymentIntent, 'create', return_value=SimpleNamespace(id='pi_local_fixture')) as create:
            result = self.post('/create-payment-intent', json={'amount': 1000})
            self.assertEqual(result.status_code, 202)
            self.assertEqual(result.json['status'], 'pending')
            self.assertNotIn('success', result.json)
            self.assertNotIn('client_secret', result.json)
            self.assertEqual(create.call_args.kwargs['metadata']['user_id'], str(user.id))
        self.assertEqual(invoice.status, 'SENT')
        self.assertIsNone(invoice.paid_at)
        for key in [None, 'sk_live_local_fixture']:
            with patch.object(application.stripe, 'api_key', key), patch.object(application.stripe.PaymentIntent, 'create') as create:
                self.assertEqual(self.post('/create-payment-intent', json={'amount': 1000}).status_code, 503)
                create.assert_not_called()

    def test_payment_errors_are_redacted_and_card_entry_is_absent(self):
        user = self.user()
        self.login(user)
        sentinel = secrets.token_hex(24)
        with patch.object(application.stripe, 'api_key', 'sk_test_local_fixture'), patch.object(application.stripe.PaymentIntent, 'create', side_effect=RuntimeError(sentinel)), self.assertLogs(app.logger, level='WARNING') as logs:
            result = self.post('/create-payment-intent', json={'amount': 100})
        self.assertEqual(result.status_code, 502)
        self.assertNotIn(sentinel, result.get_data(as_text=True) + ''.join(logs.output))
        html = self.client.get('/payment').get_data(as_text=True)
        self.assertNotIn('id="card-number"', html)
        self.assertNotIn('Payment successful!', html)
        self.assertIn('No payment has been confirmed.', html)

    def test_documents_are_private_unique_and_owner_scoped(self):
        owner = self.user()
        self.login(owner)
        for _ in range(2):
            response = self.post('/dashboard/documents/upload', {'file': (io.BytesIO(b'%PDF-local-fixture'), 'same.pdf')})
            self.assertEqual(response.status_code, 302)
        docs = application.Document.query.all()
        self.assertEqual(len({doc.file_url for doc in docs}), 2)
        self.assertEqual(len(list(Path(self.files.name).glob('*'))), 2)
        url = docs[0].file_url
        anonymous = app.test_client()
        self.assertEqual(anonymous.get(url).status_code, 302)
        stranger = self.user()
        other = app.test_client()
        self.login(stranger, other)
        self.assertEqual(other.get(url).status_code, 403)
        self.assertEqual(self.post(f'/dashboard/documents/delete/{docs[0].id}', client=other).status_code, 302)
        self.assertEqual(application.Document.query.count(), 2)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        response.close()
        admin = self.user('ADMIN')
        self.post('/logout', client=other)
        self.login(admin, other)
        response = other.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.post(f'/dashboard/documents/delete/{docs[0].id}').status_code, 302)
        self.assertEqual(application.Document.query.count(), 1)

    def test_legacy_document_urls_are_protected_and_delete_paths_confined(self):
        user = self.user()
        file = Path(self.files.name) / 'legacy.pdf'
        file.write_bytes(b'%PDF-legacy-fixture')
        doc = application.Document(user_id=user.id, name='legacy.pdf', file_type='OTHER', file_url='/static/uploads/documents/legacy.pdf')
        db.session.add(doc)
        db.session.commit()
        self.assertEqual(self.client.get(doc.file_url).status_code, 302)
        for alias in ['/static/uploads//documents/legacy.pdf',
                      '/static/uploads/Documents/legacy.pdf',
                      '/static/css/../uploads/documents/legacy.pdf']:
            self.assertNotEqual(self.client.get(alias).status_code, 200)
        self.login(user)
        response = self.client.get(doc.file_url)
        self.assertEqual(response.status_code, 200)
        response.close()
        doc.file_url = '/unrelated/keep.txt'
        db.session.commit()
        self.assertEqual(self.post(f'/dashboard/documents/delete/{doc.id}').status_code, 400)
        self.assertTrue(file.exists())

    def test_query_tokens_and_referrers_are_redacted(self):
        sentinel = secrets.token_hex(24)
        record = logging.LogRecord('werkzeug', logging.INFO, '', 0, '"%s" %s',
                                   (f'GET /verify-email?token={sentinel} HTTP/1.1', '200'), None)
        application.RedactQueryStrings().filter(record)
        self.assertNotIn(sentinel, record.getMessage())
        self.assertIn('[redacted]', record.getMessage())
        self.assertEqual(self.client.get('/').headers['Referrer-Policy'], 'no-referrer')

    def test_both_email_transports_never_log_sensitive_values(self):
        spec = importlib.util.spec_from_file_location('standalone_email', ROOT / 'app/services/email_service.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, TEST_ENV, clear=True):
            spec.loader.exec_module(module)
        sentinel = secrets.token_hex(24)
        for cls in [application.EmailService, module.EmailService]:
            for mode in ['disabled', 'rejected', 'exception', 'sent']:
                with self.subTest(service=cls.__module__, mode=mode):
                    service = cls()
                    service.api_key = '' if mode == 'disabled' else sentinel
                    client = MagicMock()
                    client.post = AsyncMock(return_value=SimpleNamespace(status_code=200 if mode == 'sent' else 422, text=sentinel))
                    if mode == 'exception':
                        client.post.side_effect = RuntimeError(sentinel)
                    manager = MagicMock()
                    manager.__aenter__ = AsyncMock(return_value=client)
                    manager.__aexit__ = AsyncMock(return_value=False)
                    output = io.StringIO()
                    handler = logging.StreamHandler(output)
                    logging.getLogger().addHandler(handler)
                    try:
                        with patch.object(application.httpx, 'AsyncClient', return_value=manager), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                            result = asyncio.run(service.send_email(sentinel, sentinel, sentinel))
                    finally:
                        logging.getLogger().removeHandler(handler)
                    self.assertEqual(result, mode == 'sent')
                    self.assertNotIn(sentinel, output.getvalue())
                    self.assertNotIn(sentinel[:7], output.getvalue())
                    if mode == 'disabled':
                        client.post.assert_not_called()


class StartupTests(unittest.TestCase):
    def run_startup(self, overrides=None):
        values = dict(TEST_ENV, **(overrides or {}))
        script = '''import os,sys,socket
from unittest.mock import patch
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("No outbound networking"))
with patch("dotenv.load_dotenv", return_value=False):
 import app
 with app.app.app_context():
  print("USERS", app.User.query.count())
  print("SECURE_COOKIE", app.app.config["SESSION_COOKIE_SECURE"])
'''
        return subprocess.run([sys.executable, '-c', script], env=dict(values, PATH=os.defpath), cwd=ROOT,
                              text=True, capture_output=True, timeout=30)

    def test_normal_startup_creates_no_users(self):
        result = self.run_startup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('USERS 0', result.stdout)

    def test_production_secure_cookie_and_secret_requirement(self):
        result = self.run_startup({'APP_ENV': 'production'})
        self.assertEqual(result.returncode, 0)
        self.assertIn('SECURE_COOKIE True', result.stdout)
        for key in ['', 'short', 'your-secret-placeholder-value-32-characters']:
            result = self.run_startup({'SECRET_KEY': key})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Set SECRET_KEY', result.stderr)

    def test_demo_seeding_requires_development_opt_in_and_explicit_passwords(self):
        credentials = {'SEED_DEMO_ACCOUNTS': '1', 'DEMO_ADMIN_EMAIL': 'admin@example.com',
                       'DEMO_CUSTOMER_EMAIL': 'customer@example.com',
                       'DEMO_ADMIN_PASSWORD': secrets.token_urlsafe(18),
                       'DEMO_CUSTOMER_PASSWORD': secrets.token_urlsafe(18)}
        result = self.run_startup(dict(credentials, APP_ENV='production'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('only in development', result.stderr)
        result = self.run_startup({'SEED_DEMO_ACCOUNTS': '1', 'APP_ENV': 'development'})
        self.assertNotEqual(result.returncode, 0)
        result = self.run_startup(dict(credentials, APP_ENV='development'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('USERS 2', result.stdout)
        for value in credentials.values():
            if len(value) > 8:
                self.assertNotIn(value, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
