from alembic import context
from personal_doctor.config import load_settings
from personal_doctor.db import Base, make_engine

engine = make_engine(load_settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
