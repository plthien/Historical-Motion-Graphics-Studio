"""Dedicated initial History migration; never touch an unsupported existing database."""
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, event
from alembic import context
from app.history.db import HistoryDB

config=context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url=config.get_main_option("sqlalchemy.url")

def run_offline():
    context.configure(url=url,literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_online():
    engine=engine_from_config(config.get_section(config.config_ini_section),
                              prefix="sqlalchemy.",poolclass=pool.NullPool)
    @event.listens_for(engine,"connect")
    def set_fk(connection,_record):
        connection.execute("PRAGMA foreign_keys=ON")
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

if context.is_offline_mode():run_offline()
else:run_online()
