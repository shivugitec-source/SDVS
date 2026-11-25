import smtplib, urllib.parse, urllib.request
from email.message import EmailMessage
from fastapi import HTTPException
from .config import settings
def send_email_otp(to_email:str,code:str):
 if not all([settings.smtp_host,settings.smtp_user,settings.smtp_password,settings.smtp_from]):
  raise HTTPException(503,"Email OTP is not configured. Set SMTP Gmail App Password on the backend.")
 msg=EmailMessage();msg["Subject"]="Your SDVS sign-in verification code";msg["From"]=settings.smtp_from;msg["To"]=to_email
 msg.set_content(f"Your SDVS email verification code is {code}. It expires in {settings.otp_ttl_minutes} minutes. Do not share this code.")
 try:
  with smtplib.SMTP(settings.smtp_host,settings.smtp_port,timeout=15) as server:
   if settings.smtp_starttls: server.starttls()
   server.login(settings.smtp_user,settings.smtp_password);server.send_message(msg)
 except Exception as exc: raise HTTPException(502,"Unable to deliver email OTP. Check SMTP configuration.") from exc
def send_sms_otp(phone:str,code:str):
 if not all([settings.twilio_account_sid,settings.twilio_auth_token,settings.twilio_from_number]):
  raise HTTPException(503,"SMS OTP is not configured. Set Twilio credentials on the backend.")
 url=f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
 data=urllib.parse.urlencode({"To":phone,"From":settings.twilio_from_number,"Body":f"Your SDVS mobile verification code is {code}. Expires in {settings.otp_ttl_minutes} minutes. Do not share it."}).encode()
 req=urllib.request.Request(url,data=data);import base64
 req.add_header("Authorization","Basic "+base64.b64encode(f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()).decode())
 try:
  with urllib.request.urlopen(req,timeout=15) as response:
   if response.status>=300: raise RuntimeError("SMS provider rejected request")
 except Exception as exc: raise HTTPException(502,"Unable to deliver SMS OTP. Check Twilio configuration.") from exc
