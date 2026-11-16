from datetime import datetime
from src.models.entities import Audit
def audit(s,u,action):
 s.add(Audit(actor=u.email,action=action,created=datetime.now().astimezone().isoformat()));s.commit()
def dump(obj):
 data={c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name!='password'}
 return {k:(v.isoformat() if isinstance(v,datetime) else v) for k,v in data.items()}
