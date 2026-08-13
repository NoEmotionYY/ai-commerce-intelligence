from commerce.database import Base, SessionLocal, engine
from commerce.seed import reset_and_seed

Base.metadata.create_all(engine)
with SessionLocal() as session:
    reset_and_seed(session)
print("种子数据初始化完成")
