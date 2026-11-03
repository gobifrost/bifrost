"""Rehearse pinned migrations on an empty schema clone, never the dev database."""
import hashlib
import argparse
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


def main(*, duplicate_probe=False):
    global RUN, DATABASE, DEST
    if duplicate_probe:
        RUN = ROOT / 'runs/schema-rehearsal-v2'
        DATABASE = 'bifrost_pyodide_schema_v2'
        DEST = '/tmp/pyodide-ddf316c1-schema-v2'
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
        script = script.replace('bifrost_pyodide_schema_v1', DATABASE).replace(
            '/tmp/pyodide-ddf316c1-schema-v1', DEST)
        if duplicate_probe:
            prefix = script.rsplit('command.upgrade', 1)[0]
            call(['docker', 'exec', '-i', API, 'python', '-'],
                 (prefix + "command.upgrade(config, '20261101_capacity_cleanup')\n").encode(), timeout=90)
            sql(DATABASE, "INSERT INTO tables (id,name) VALUES "
                "('00000000-0000-0000-0000-000000000001','synthetic-duplicate'),"
                "('00000000-0000-0000-0000-000000000002','synthetic-duplicate')")
            before = sql(DATABASE, 'SELECT id,name FROM tables ORDER BY id')
            refusal = prefix + '''try:
    command.upgrade(config, '20261101_table_name_unique')
except RuntimeError as exc:
    if 'duplicate _repo tables exist' not in str(exc):
        raise
    print('EXPECTED_DUPLICATE_REFUSAL')
else:
    raise AssertionError('Duplicate names did not stop migration')
'''
            result = call(['docker', 'exec', '-i', API, 'python', '-'], refusal.encode(), timeout=90)
            report['duplicate_refused'] = b'EXPECTED_DUPLICATE_REFUSAL' in result
            report['rows_unchanged_after_refusal'] = sql(DATABASE, 'SELECT id,name FROM tables ORDER BY id') == before
            report['revision_after_refusal'] = sql(DATABASE, 'SELECT version_num FROM alembic_version')
            report['unique_indexes_after_refusal'] = sql(DATABASE,
                "SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_tables_org_name_unique','ix_tables_global_name_unique')")
            if (not report['duplicate_refused'] or not report['rows_unchanged_after_refusal']
                or report['revision_after_refusal'] != '20261101_capacity_cleanup'
                or report['unique_indexes_after_refusal'] != '0'):
                raise RuntimeError('Duplicate refusal was not transactional')
            sql(DATABASE, "UPDATE tables SET name='synthetic-distinct' WHERE id='00000000-0000-0000-0000-000000000002'")
        call(['docker', 'exec', '-i', API, 'python', '-'], script.encode(), timeout=90)
        if duplicate_probe:
            report['synthetic_rows_after_upgrade'] = sql(DATABASE, 'SELECT count(*) FROM tables')
            if report['synthetic_rows_after_upgrade'] != '2':
                raise RuntimeError('Synthetic rows lost on upgrade')
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--duplicate-probe', action='store_true')
    main(duplicate_probe=parser.parse_args().duplicate_probe)
