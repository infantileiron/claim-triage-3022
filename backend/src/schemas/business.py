from datetime import datetime
from typing import Optional,Literal
from pydantic import BaseModel,Field,field_validator
class Login(BaseModel):email:str;password:str
class ClaimInput(BaseModel):
 code:str=Field(min_length=1,max_length=60)
 amount:float=Field(ge=0,le=100000,allow_inf_nan=False)
 distance:float=Field(ge=0,le=10000,allow_inf_nan=False)
 holiday:bool=False
 label:Optional[Literal[0,1]]=None
 note:str=Field(default='',max_length=1000)
 claimant:Optional[str]=Field(default=None,max_length=100)
 merchant:Optional[str]=Field(default=None,max_length=200)
 occurred_at:Optional[datetime]=None
 @field_validator('claimant','merchant')
 @classmethod
 def strip_text(cls,v):
  if v is None:return None
  v=v.strip()
  return v or None
class TrainingInput(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 threshold:float=Field(default=.5,ge=.1,le=.9,allow_inf_nan=False)
class ReviewInput(BaseModel):
 decision:Literal['valid','flagged']
 note:str=Field(min_length=6,max_length=2000)
class ScreeningInput(BaseModel):
 limit_amount:float=Field(gt=0,le=10000000,allow_inf_nan=False)
class GroupReviewInput(BaseModel):
 decision:Literal['valid','flagged']
 note:str=Field(min_length=6,max_length=2000)
 expected_version:int=Field(ge=1)
class GroupApproveInput(BaseModel):
 expected_version:int=Field(ge=1)
