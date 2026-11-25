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
