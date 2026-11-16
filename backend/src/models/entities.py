from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,Boolean,UniqueConstraint,DateTime,Index
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Claim(Base):
 __tablename__='claims'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);amount=Column(Float);distance=Column(Float);holiday=Column(Boolean);label=Column(Integer,nullable=True);note=Column(Text,default='')
 # 关联筛查补录字段；历史样本可能为空，空值样本单列且不参与连边
 claimant=Column(String(100),nullable=True);merchant=Column(String(200),nullable=True);occurred_at=Column(DateTime,nullable=True)
class Training(Base):
 __tablename__='trainings'
 id=Column(Integer,primary_key=True);name=Column(String(100));threshold=Column(Float);state=Column(String(20),default='queued');result=Column(JSON);error=Column(Text,default='')
class Review(Base):
 __tablename__='reviews'
 __table_args__=(UniqueConstraint('training_id','claim_id',name='uq_review_source'),)
 id=Column(Integer,primary_key=True);training_id=Column(Integer,ForeignKey('trainings.id'));claim_id=Column(Integer,ForeignKey('claims.id'));score=Column(Float);decision=Column(String(30),default='pending');note=Column(Text,default='');status=Column(String(20),default='draft')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))

class CorrelationState(Base):
 """关联口径的并发闸门：补录/更正与筛查切换都先锁这一行，避免半套分组。"""
 __tablename__='correlation_states'
 id=Column(Integer,primary_key=True);revision=Column(Integer,default=0);updated=Column(String(40),default='')

class Screening(Base):
 __tablename__='screenings'
 id=Column(Integer,primary_key=True);limit_amount=Column(Float,nullable=False);state=Column(String(20),default='pending');error=Column(Text,default='')
 submitted_by=Column(String(100),default='');trigger=Column(String(30),default='manual');revision=Column(Integer,default=0);created=Column(String(40),default='');started_at=Column(String(40),default='');finished_at=Column(String(40),default='')
 group_count=Column(Integer,default=0);over_limit_count=Column(Integer,default=0);eligible_count=Column(Integer,default=0);incomplete_count=Column(Integer,default=0);total_amount=Column(Float,default=0)
 incomplete=Column(JSON,default=list)
class CorrelationGroup(Base):
 __tablename__='correlation_groups'
 __table_args__=(UniqueConstraint('screening_id','group_key',name='uq_group_screening_key'),Index('ix_group_screening','screening_id'))
 id=Column(Integer,primary_key=True);screening_id=Column(Integer,ForeignKey('screenings.id'),nullable=False)
 group_key=Column(String(400),nullable=False);total_amount=Column(Float,default=0);member_count=Column(Integer,default=0)
 status=Column(String(20),default='pending');decision=Column(String(30),default='pending');note=Column(Text,default='')
 stale=Column(Boolean,default=False);stale_reason=Column(String(30),default='');carried_from=Column(Integer,nullable=True)
 chain=Column(JSON,default=list)
 review_version=Column(Integer,default=1);created=Column(String(40),default='');updated=Column(String(40),default='')
class GroupMember(Base):
 __tablename__='group_members'
 __table_args__=(UniqueConstraint('group_id','claim_id',name='uq_group_claim'),Index('ix_member_claim','claim_id'))
 id=Column(Integer,primary_key=True);group_id=Column(Integer,ForeignKey('correlation_groups.id'),nullable=False);claim_id=Column(Integer,ForeignKey('claims.id'),nullable=False)
 code=Column(String(60));amount=Column(Float);claimant=Column(String(100));merchant=Column(String(200));occurred_at=Column(DateTime,nullable=True)
class GroupEvent(Base):
 __tablename__='group_events'
 __table_args__=(Index('ix_event_screening','screening_id'),Index('ix_event_group','group_id'))
 id=Column(Integer,primary_key=True);screening_id=Column(Integer,ForeignKey('screenings.id'),nullable=True)
 group_id=Column(Integer,nullable=True);related_group_id=Column(Integer,nullable=True);claim_id=Column(Integer,nullable=True)
 event=Column(String(40),default='');detail=Column(JSON);actor=Column(String(100),default='');created=Column(String(40),default='')
