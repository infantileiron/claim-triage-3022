import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
url=os.environ['DATABASE_URL']
connect_args={'check_same_thread':False} if url.startswith('sqlite') else {}
engine=create_engine(url,pool_pre_ping=True,connect_args=connect_args)
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()
def db():
 with Session() as s: yield s
