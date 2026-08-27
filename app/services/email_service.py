import os
import httpx
from flask import current_app

class EmailService:
    def __init__(self):
        self.api_key = os.getenv('RESEND_API_KEY')
        self.from_email = os.getenv('EMAIL_FROM', 'onboarding@resend.dev')
        self.base_url = "https://api.resend.com"

    def _is_configured(self):
        """Returns False if no real Resend API key is set (e.g. placeholder like 're_xxxx...')"""
        return bool(self.api_key) and not self.api_key.startswith('re_xxxx')

    async def send_email(self, to, subject, html, text=None):
        """Send email using Resend API"""
        if not self._is_configured():
            print(f"\n📧 [DEV MODE - no real RESEND_API_KEY set] Email not actually sent.", flush=True)
            print(f"   To: {to}", flush=True)
            print(f"   Subject: {subject}", flush=True)
            print(f"   Content:\n{html}\n", flush=True)
            return True
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
        """Send email verification link"""
        link = f"{os.getenv('FRONTEND_URL')}/verify-email?token={token}"
        html = f"""
        <h2>Welcome {name}!</h2>
        <p>Please verify your email by clicking the link below:</p>
        <a href="{link}">{link}</a>
        <p>This link expires in 24 hours.</p>
        """
        return await self.send_email(email, "Verify Your Email", html)
    
    async def send_password_reset_email(self, email, name, token):
        """Send password reset link"""
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
        """Send booking confirmation email"""
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
    
    async def send_payment_confirmation(self, email, name, payment_data):
        """Send payment confirmation email"""
        html = f"""
        <h2>Payment Successful!</h2>
        <p>Hello {name},</p>
        <p>Your payment of <strong>${payment_data.get('amount')}</strong> for <strong>{payment_data.get('description')}</strong> has been confirmed.</p>
        <p>Thank you for your business!</p>
        """
        return await self.send_email(email, "Payment Confirmed", html)
    
    async def send_welcome_email(self, email, name):
        """Send welcome email after registration"""
        html = f"""
        <h2>Welcome to ApexBuild Interiors!</h2>
        <p>Hello {name},</p>
        <p>Thank you for registering with us. We're excited to work with you!</p>
        <p>You can now access your dashboard to track your projects and bookings.</p>
        """
        return await self.send_email(email, "Welcome to ApexBuild", html)

email_service = EmailService()
