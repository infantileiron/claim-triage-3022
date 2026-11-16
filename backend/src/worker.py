import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Training,Screening
from src.services.analysis import train
from src.services.correlation import run_screening
r=redis.Redis.from_url(os.environ['REDIS_URL'])
with Session() as recovery:
 for pending in recovery.query(Training).filter(Training.state.in_(['queued','running'])).all():
  pending.state='queued';r.rpush('claim:trainings',pending.id)
 for pending in recovery.query(Screening).filter_by(state='running').all():
  pending.state='pending';r.rpush('claim:screenings',pending.id)
 recovery.commit()
while True:
 Path('/tmp/heartbeat').touch()
 try:
  job=r.blpop(['claim:trainings','claim:screenings'],timeout=3)
  if not job:continue
  queue,raw=job[0].decode(),int(job[1])
  if queue=='claim:screenings':
   run_screening(raw);continue
  with Session() as s:
   row=s.get(Training,raw)
   if not row or row.state!='queued':continue
   snapshot=row.result['snapshot'];row.state='running';s.commit()
   try:
    row.result={**train(snapshot,row.threshold),'snapshot':snapshot};row.state='done'
    Path('/artifacts/'+str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
   except Exception as e:row.state='failed';row.error=str(e)
   s.commit()
 except Exception:logging.exception('worker');time.sleep(2)
