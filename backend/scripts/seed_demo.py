import random,math
from datetime import datetime,timedelta
from src.config.database import Base,engine,Session
from src.models.entities import User,Claim

# 确定性关联演示样本：跨日链式一组、同窗口但未超限一组、含桥接票的一组。
# 自然键只插入缺失行，不覆盖人工修订。
DEMO_CORRELATION=[
 ('HX-CR-001',2600,'陈一','云栖酒店','2026-11-01T22:00:00'),
 ('HX-CR-002',2600,'陈一','云栖酒店','2026-11-02T20:00:00'),
 ('HX-CR-003',2600,'陈一','云栖酒店','2026-11-03T18:00:00'),
 ('HX-CR-004',1800,'林二','青禾餐厅','2026-11-05T12:00:00'),
 ('HX-CR-005',1900,'林二','青禾餐厅','2026-11-05T20:00:00'),
 ('HX-CR-006',3000,'周桥','远山租车','2026-11-08T09:00:00'),
 ('HX-CR-007',3100,'周桥','远山租车','2026-11-08T21:00:00'),
 ('HX-CR-008',3200,'周桥','远山租车','2026-11-09T20:00:00'),
]
def seed():
 from src.utils.security import hash_password
 Base.metadata.create_all(engine);rng=random.Random(3106)
 with Session() as s:
  for role in ('admin','analyst','viewer'):
   email=role+'@demo.local'
   if not s.query(User).filter_by(email=email).first():s.add(User(email=email,role=role,password=hash_password('Demo123!')))
  people=['陈一','林二','周桥','赵敏','孙禾','吴岚']
  shops=['云栖酒店','青禾餐厅','远山租车','星港咖啡','西岭展馆']
  for i in range(300):
   code=f'HX-1115-{i+1:03}';distance=round(rng.uniform(5,800),1);holiday=rng.random()<.25;amount=round(distance*rng.uniform(1.2,5.5)+rng.uniform(50,900),2)
   risk=-4.7+amount/700+1.1*holiday+amount/(distance+1)/8;label=int(rng.random()<1/(1+math.exp(-risk))) if i<260 else None
   # 绝大多数历史样本有完整关联字段；每 37 笔留一笔缺字段，演示“旧样本信息不全单列”。
   complete=i%37!=0
   person=people[i%len(people)] if complete else None
   shop=shops[i%len(shops)] if complete else None
   at=(datetime(2026,10,1,8,0)+timedelta(hours=i*17)) if complete else None
   if not s.query(Claim).filter_by(code=code).first():
    s.add(Claim(code=code,amount=amount,distance=distance,holiday=holiday,label=label,claimant=person,merchant=shop,occurred_at=at,
                note='历史人工复核标签；仅用于模拟训练，不连接真实报销'))
  for code,amount,person,shop,at in DEMO_CORRELATION:
   if not s.query(Claim).filter_by(code=code).first():
    s.add(Claim(code=code,amount=amount,distance=20,holiday=False,label=None,
                claimant=person,merchant=shop,
                occurred_at=datetime.fromisoformat(at).replace(tzinfo=None),
                note='关联筛查演示样本：同一报销人在同一商户的连续消费'))
  s.commit();print({'users':s.query(User).count(),'claims':s.query(Claim).count()})
if __name__=='__main__':seed()
