"""Rehearse pinned migrations on an empty schema clone, never the dev database."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = ROOT / 'upstream-025f6c0'
RUN = ROOT / 'runs/schema-rehearsal-v1'
PG = 'bifrost-debug-074217d4-postgres-1'
API = 'bifrost-debug-074217d4-api-1'
DATABASE = 'bifrost_pyodide_schema_v1'
DEST = '/tmp/pyodide-ddf316c1-schema-v1'


def call(args, data=None, timeout=30):
    return subprocess.check_output(args, input=data, timeout=timeout)


def sql(database, statement):
    return call(['docker', 'exec', '-i', PG, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                 '-U', 'bifrost', '-d', database, '-Atc', statement]).decode().strip()


def main():
    if call(['hostname']).decode().strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong guest')
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError('Existing disk guard failed')
    if call(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']).decode().strip() != '1d33150eb376b71f8a81b427920eda1b0447f829':
        raise RuntimeError('Source pin changed')
    if sql('postgres', f"SELECT count(*) FROM pg_database WHERE datname='{DATABASE}'") != '0':
        raise RuntimeError('Reconcile existing disposable database; do not retry')
    if sql('bifrost', 'SELECT version_num FROM alembic_version') != '20261031_app_sdk_binding':
        raise RuntimeError('Dev schema changed')
    RUN.mkdir()
    report = {'source': '1d33150eb376b71f8a81b427920eda1b0447f829', 'database': DATABASE,
              'cloud_invocations': 0, 'dev_database_migrated': False, 'passed': False}
    started = time.monotonic()
    created = False
    try:
        schema = call(['docker', 'exec', PG, 'pg_dump', '-U', 'bifrost', '-d', 'bifrost',
                       '--schema-only', '--no-owner', '--no-privileges'], timeout=45)
        report['schema_sha256'] = hashlib.sha256(schema).hexdigest()
        report['schema_bytes'] = len(schema)
        sql('postgres', f'CREATE DATABASE {DATABASE} TEMPLATE template0')
        created = True
        call(['docker', 'exec', '-i', PG, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-q',
              '-U', 'bifrost', '-d', DATABASE], schema, timeout=45)
        sql(DATABASE, "INSERT INTO alembic_version VALUES ('20261031_app_sdk_binding')")
        call(['docker', 'exec', API, 'mkdir', DEST])
        call(['docker', 'cp', str(SOURCE / 'api/alembic'), f'{API}:{DEST}/alembic'])
        script = '''import os
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from src.config import get_settings
url = make_url(get_settings().database_url).set(host='postgres', port=5432, database='bifrost_pyodide_schema_v1')
os.environ['BIFROST_DATABASE_URL'] = url.render_as_string(hide_password=False)
os.environ['BIFROST_DATABASE_URL_SYNC'] = url.set(drivername='postgresql').render_as_string(hide_password=False)
get_settings.cache_clear()
config = Config('/app/alembic.ini')
config.set_main_option('script_location', '/tmp/pyodide-ddf316c1-schema-v1/alembic')
command.upgrade(config, '20261101_table_name_unique')
'''
        call(['docker', 'exec', '-i', API, 'python', '-'], script.encode(), timeout=90)
        report['revision'] = sql(DATABASE, 'SELECT version_num FROM alembic_version')
        report['columns'] = sql(DATABASE, "SELECT column_name FROM information_schema.columns WHERE table_name='execution_workers' AND column_name IN ('active_handler_counts','capacity_scope','capacity_resource_uid','capacity_resource_deleted_at') ORDER BY column_name").splitlines()
        report['indexes'] = sql(DATABASE, "SELECT indexname FROM pg_indexes WHERE indexname IN ('ix_execution_workers_external_cleanup','ix_tables_org_name_unique','ix_tables_global_name_unique') ORDER BY indexname").splitlines()
        report['passed'] = (report['revision'] == '20261101_table_name_unique'
                            and len(report['columns']) == 4 and len(report['indexes']) == 3)
        if not report['passed']:
            raise RuntimeError('Migrated schema assertions failed')
    finally:
        if created:
            sql('postgres', f'DROP DATABASE {DATABASE}')
        report['disposable_database_absent'] = sql('postgres', f"SELECT count(*) FROM pg_database WHERE datname='{DATABASE}'") == '0'
        report['dev_revision_after'] = sql('bifrost', 'SELECT version_num FROM alembic_version')
        report['elapsed_seconds'] = time.monotonic() - started
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
