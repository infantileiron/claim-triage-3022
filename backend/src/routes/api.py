import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import (
    User,Claim,Training,Review,Audit,Screening,CorrelationGroup,GroupMember,GroupEvent,
)
from src.schemas.business import (
    Login,ClaimInput,TrainingInput,ReviewInput,ScreeningInput,GroupReviewInput,GroupApproveInput,
)
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services import correlation
from src.controllers.business import ensure_training
router=APIRouter()
CORRELATION_FIELDS=('claimant','merchant','occurred_at','amount')


def enqueue(screening_id):
 try:
  redis.Redis.from_url(os.environ['REDIS_URL']).rpush('claim:screenings',screening_id)
 except redis.RedisError:
  raise HTTPException(503,'筛查队列暂不可用，请稍后重试')


def payload(row):
 data=dump(row)
 for key in ('occurred_at','started_at','finished_at','created','updated'):
  if data.get(key) is not None and not isinstance(data[key],str):
   data[key]=data[key].isoformat()
 return data


def group_payload(row):
 data=dump(row)
 for key in ('created','updated'):
  if data.get(key) is not None and not isinstance(data[key],str):
   data[key]=data[key].isoformat()
 return data


@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/claims')
def claims(u=Depends(current),s=Depends(db)):return [payload(r) for r in s.query(Claim).order_by(Claim.id).all()]


def _persist_claim(s,data,row):
 for k,v in data.model_dump().items():
  if k=='occurred_at':v=correlation.clean_time(v)
  setattr(row,k,v)


def _maybe_rescreen(s,u,changed):
 """样本补录/更正与分组切换在同一事务内提交，失败整体回滚。"""
 if not changed:return None
 try:
  screen=correlation.screening_for_claim_update(s,u.email)
 except Exception as exc:  # noqa: BLE001
  s.rollback();raise HTTPException(500,'关联分组重算失败，样本变更未保存：'+str(exc)[:200])
 if screen is not None:
  correlation.record_audit(s,u.email,'补录后重算关联分组 '+str(screen.id))
 return screen


@router.post('/claims')
def create_claim(data:ClaimInput,u=Depends(editor),s=Depends(db)):
 r=Claim();_persist_claim(s,data,r);s.add(r)
 try:s.flush()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 screen=_maybe_rescreen(s,u,True)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 audit(s,u,'录入报销 '+r.code);result=payload(r)
 if screen is not None:result['screening']={'id':screen.id,'state':screen.state,'group_count':screen.group_count}
 return result
@router.put('/claims/{i}')
def edit_claim(i:int,data:ClaimInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Claim,i)
 if not r:raise HTTPException(404,'报销样本不存在')
 before={k:getattr(r,k) for k in CORRELATION_FIELDS}
 _persist_claim(s,data,r)
 changed=any(getattr(r,k)!=before[k] for k in CORRELATION_FIELDS)
 try:s.flush()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 screen=_maybe_rescreen(s,u,changed)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 audit(s,u,'修订报销 '+str(i));result=payload(r)
 if screen is not None:result['screening']={'id':screen.id,'state':screen.state,'group_count':screen.group_count}
 return result
@router.get('/claims/{i}/correlation')
def claim_correlation(i:int,u=Depends(current),s=Depends(db)):
 if not s.get(Claim,i):raise HTTPException(404,'报销样本不存在')
 rows=(s.query(GroupMember,CorrelationGroup,Screening)
       .join(CorrelationGroup,GroupMember.group_id==CorrelationGroup.id)
       .join(Screening,CorrelationGroup.screening_id==Screening.id)
       .filter(GroupMember.claim_id==i)
       .order_by(CorrelationGroup.id.desc()).all())
 return [{'screening_id':g.screening_id,'screening_state':sc.state,'limit_amount':sc.limit_amount,
          'group_id':g.id,'group_key':g.group_key,'status':g.status,'decision':g.decision,
          'note':g.note,'stale':g.stale,'stale_reason':g.stale_reason,
          'review_version':g.review_version,'total_amount':g.total_amount,'created':g.created}
         for _m,g,sc in rows]


@router.get('/trainings')
def trainings(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Training).order_by(Training.id.desc()).all()]
@router.post('/trainings')
def train(data:TrainingInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Claim).order_by(Claim.id).all()];r=Training(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('claim:trainings',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交训练 '+str(r.id));return dump(r)
@router.get('/trainings/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_training(s,i))
@router.get('/trainings/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_training(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功训练可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="claim-triage-{i}.json"'})
@router.post('/trainings/{i}/reviews')
def generate(i:int,u=Depends(editor),s=Depends(db)):
 r=ensure_training(s,i)
 if r.state!='done':raise HTTPException(409,'训练完成后才能生成复核任务')
 created=0
 for p in r.result['pending']:
  if p['score']>=r.threshold and not s.query(Review).filter_by(training_id=i,claim_id=p['id']).first():s.add(Review(training_id=i,claim_id=p['id'],score=p['score']));created+=1
 s.commit();audit(s,u,'生成风险复核 '+str(i));return {'created':created}
@router.get('/reviews')
def reviews(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Review).order_by(Review.id.desc()).all()]
@router.put('/reviews/{i}')
def review(i:int,data:ReviewInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Review,i)
 if not r:raise HTTPException(404,'复核任务不存在')
 if r.status=='approved':raise HTTPException(409,'已签核复核不可修改')
 r.decision=data.decision;r.note=data.note;s.commit();audit(s,u,'人工复核 '+str(i));return dump(r)
@router.post('/reviews/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Review,i)
 if not r:raise HTTPException(404,'复核任务不存在')
 if r.decision=='pending' or r.status!='draft':raise HTTPException(409,'先填写人工判断，且只签核一次')
 r.status='approved';s.commit();audit(s,u,'签核复核 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]


# ---------- 关联筛查 ----------
def ensure_screening(s,i):
 row=s.get(Screening,i)
 if not row:raise HTTPException(404,'筛查版本不存在')
 return row


def group_detail(s,group):
 data=group_payload(group)
 data['chain']=group.chain or []
 events=(s.query(GroupEvent)
         .filter((GroupEvent.group_id==group.id)|(GroupEvent.related_group_id==group.id))
         .order_by(GroupEvent.id.desc()).all())
 data['window_hours']=correlation.LINK_WINDOW_HOURS
 data['events']=[{**payload(e),'detail':e.detail or {}} for e in events]
 if group.carried_from:
  origin=s.get(CorrelationGroup,group.carried_from)
  data['carried_from']=group_payload(origin) if origin else None
 return data


def members_of(s,group_id):
 members=(s.query(GroupMember).filter_by(group_id=group_id)
          .order_by(GroupMember.occurred_at,GroupMember.claim_id).all())
 return [{
  'id':m.claim_id,'code':m.code,'amount':m.amount,'claimant':m.claimant,
  'merchant':m.merchant,'occurred_at':m.occurred_at.isoformat() if m.occurred_at else None,
 } for m in members]


@router.post('/screenings')
def create_screening(data:ScreeningInput,u=Depends(editor),s=Depends(db)):
 row=Screening(limit_amount=data.limit_amount,state='pending',submitted_by=u.email,
               trigger='manual',created=correlation.now_iso())
 s.add(row);s.commit()
 try:enqueue(row.id)
 except HTTPException:
  row.state='failed';row.error='筛查队列暂不可用，请稍后重试';row.finished_at=correlation.now_iso();s.commit()
  raise
 audit(s,u,'提交关联筛查 '+str(row.id))
 return payload(row)
@router.get('/screenings')
def screenings(u=Depends(current),s=Depends(db)):
 rows=[payload(r) for r in s.query(Screening).order_by(Screening.id.desc()).all()]
 latest=correlation.latest_done_screening(s)
 for row in rows:row['is_current']=latest is not None and row['id']==latest.id
 return rows
@router.get('/screenings/{i}')
def screening_detail(i:int,u=Depends(current),s=Depends(db)):
 row=ensure_screening(s,i)
 groups=(s.query(CorrelationGroup).filter_by(screening_id=i)
         .order_by(CorrelationGroup.total_amount.desc(),CorrelationGroup.id).all())
 latest=correlation.latest_done_screening(s)
 is_current=row.state=='done' and latest is not None and latest.id==row.id
 data=payload(row)
 data['is_current']=is_current
 data['groups']=[{**group_detail(s,g),'is_current':is_current,'members':members_of(s,g.id)} for g in groups]
 data['incomplete']=row.incomplete or []
 data['window_hours']=correlation.LINK_WINDOW_HOURS
 return data
@router.post('/screenings/{i}/retry')
def retry_screening(i:int,u=Depends(editor),s=Depends(db)):
 row=ensure_screening(s,i)
 if row.state!='failed':raise HTTPException(409,'只有失败的筛查可以重试')
 row.state='pending';row.error='';row.finished_at='';s.commit()
 try:enqueue(row.id)
 except HTTPException:
  row.state='failed';row.error='筛查队列暂不可用，请稍后重试';s.commit();raise
 audit(s,u,'重试关联筛查 '+str(i));return payload(row)
@router.get('/groups/{i}')
def group(i:int,u=Depends(current),s=Depends(db)):
 row=s.get(CorrelationGroup,i)
 if not row:raise HTTPException(404,'关联组不存在')
 return group_detail(s,row)


def _guard_group(s,row):
 if row.stale:raise HTTPException(409,'该组依据已过期，请在新筛查版本中复核')
 current=correlation.latest_done_screening(s)
 group_screening=s.get(Screening,row.screening_id)
 if current is None or group_screening is None or group_screening.state!='done' or group_screening.id!=current.id:
  raise HTTPException(409,'该组不属于当前筛查版本，请在新版本中复核')
 if row.status=='below_limit':raise HTTPException(409,'未超限额的组不进入组级复核')
 if row.status=='approved':raise HTTPException(409,'已签核复核不可修改')


@router.put('/groups/{i}/review')
def review_group(i:int,data:GroupReviewInput,u=Depends(editor),s=Depends(db)):
 row=s.get(CorrelationGroup,i)
 if not row:raise HTTPException(404,'关联组不存在')
 _guard_group(s,row)
 stamp=correlation.now_iso()
 # 条件更新把“版本校验”和“写入”合成一步：并发复核时只有版本仍匹配的那次生效。
 changed=(s.query(CorrelationGroup)
          .filter(CorrelationGroup.id==i,
                  CorrelationGroup.review_version==data.expected_version,
                  CorrelationGroup.stale.is_(False),
                  CorrelationGroup.status.in_(('pending','draft')))
          .update({'decision':data.decision,'note':data.note,'status':'draft',
                   'review_version':data.expected_version+1,'updated':stamp},
                  synchronize_session=False))
 if changed!=1:
  s.rollback()
  fresh=s.get(CorrelationGroup,i)
  if not fresh:raise HTTPException(404,'关联组不存在')
  _guard_group(s,fresh)
  raise HTTPException(409,'该组已被重新分组或并发修改，请刷新后在新组复核')
 correlation.add_event(s,row.screening_id,i,'reviewed',u.email,
                       {'decision':data.decision,'note':data.note,'expected_version':data.expected_version})
 s.commit();audit(s,u,'组级复核 '+str(i))
 return group_payload(s.get(CorrelationGroup,i,populate_existing=True))


@router.post('/groups/{i}/approve')
def approve_group(i:int,data:GroupApproveInput,u=Depends(admin),s=Depends(db)):
 row=s.get(CorrelationGroup,i)
 if not row:raise HTTPException(404,'关联组不存在')
 _guard_group(s,row)
 if row.status!='draft' or row.decision=='pending':raise HTTPException(409,'先填写组级判断，且只签核一次')
 stamp=correlation.now_iso()
 changed=(s.query(CorrelationGroup)
          .filter(CorrelationGroup.id==i,
                  CorrelationGroup.review_version==data.expected_version,
                  CorrelationGroup.stale.is_(False),
                  CorrelationGroup.status=='draft')
          .update({'status':'approved','review_version':data.expected_version+1,'updated':stamp},
                  synchronize_session=False))
 if changed!=1:
  s.rollback()
  fresh=s.get(CorrelationGroup,i)
  if not fresh:raise HTTPException(404,'关联组不存在')
  _guard_group(s,fresh)
  if fresh.status!='draft' or fresh.decision=='pending':raise HTTPException(409,'先填写组级判断，且只签核一次')
  raise HTTPException(409,'该组已被重新分组或并发签核，请刷新后再签核')
 correlation.add_event(s,row.screening_id,i,'approved',u.email,
                       {'decision':row.decision,'note':row.note,'expected_version':data.expected_version})
 s.commit();audit(s,u,'组级签核 '+str(i))
 return group_payload(s.get(CorrelationGroup,i,populate_existing=True))
