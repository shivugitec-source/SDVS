from datetime import datetime,timedelta,timezone
from jose import jwt,JWTError
from pwdlib import PasswordHash
from fastapi import Depends,HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import User
password_hash=PasswordHash.recommended();oauth2=OAuth2PasswordBearer(tokenUrl="/auth/login")
def hash_password(password:str)->str:return password_hash.hash(password)
def verify_password(password:str,hashed:str)->bool:return password_hash.verify(password,hashed)
def create_token(user:User):
 exp=datetime.now(timezone.utc)+timedelta(minutes=settings.access_token_minutes)
 return jwt.encode({"sub":user.id,"inst":user.institution_id,"role":user.role.value,"exp":exp},settings.jwt_secret,algorithm=settings.jwt_algorithm)
def current_user(token:str=Depends(oauth2),db:Session=Depends(get_db)):
 try:payload=jwt.decode(token,settings.jwt_secret,algorithms=[settings.jwt_algorithm]);uid=payload.get("sub")
 except JWTError:raise HTTPException(401,"Invalid or expired token")
 u=db.get(User,uid)
 if not u or not u.active:raise HTTPException(401,"Inactive user")
 return u
def require_roles(*roles):
 def dependency(user:User=Depends(current_user)):
  if user.role.value not in roles:raise HTTPException(403,"Insufficient permissions")
  return user
 return dependency
