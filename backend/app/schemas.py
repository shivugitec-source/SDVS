from pydantic import BaseModel,Field
from typing import Optional
class LoginIn(BaseModel):username:str;password:str
class TokenOut(BaseModel):access_token:str;token_type:str="bearer"
class ExamIn(BaseModel):name:str;academic_year:str
class SubjectIn(BaseModel):code:str;name:str;max_marks:float=Field(gt=0)
class QuestionIn(BaseModel):question_number:str;max_marks:float=Field(gt=0);rubric:Optional[str]=None
class AllocationIn(BaseModel):script_id:str;evaluator_id:str
class MarkIn(BaseModel):question_id:str;marks:float=Field(ge=0);remark:Optional[str]=None;private_note:Optional[str]=None
class EvaluationIn(BaseModel):script_id:str;marks:list[MarkIn]

class OTPRequestIn(BaseModel):
 email:str
class OTPVerifyIn(BaseModel):
 email:str
 channel:str
 code:str=Field(min_length=6,max_length=6)
class AdminUserCreateIn(BaseModel):
 full_name:str=Field(min_length=2,max_length=200)
 email:str
 phone:str=Field(min_length=8,max_length=20)
 role:str="EVALUATOR"
