"""SQLite local / PostgreSQL opcional en Render. Sin autenticación: SOLO laboratorio temporal."""
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

FIELDS = ('id', 'experimento', 'nodo_id', 'zona', 'ronda', 'secuencia',
          'temperatura_c', 'humedad_pct', 'protocolo', 'recibido_utc',
          'bytes_json', 'bytes_mensaje_estimados', 'topico')


def is_pg():
    return os.getenv('DATABASE_URL', 'sqlite:///./datos.db').startswith(('postgresql://', 'postgres://'))


def connect():
    db_url = os.getenv('DATABASE_URL', 'sqlite:///./datos.db')
    if is_pg():
        import psycopg2
        return psycopg2.connect(db_url.replace('postgres://', 'postgresql://', 1))
    if not db_url.startswith('sqlite:///'):
        raise ValueError('DATABASE_URL debe iniciar sqlite:/// o postgresql://')
    path = db_url.removeprefix('sqlite:///')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    return connection


@contextmanager
def db():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sql(command):
    return command.replace('?', '%s') if is_pg() else command


def init_db():
    if os.getenv('REQUIRE_POSTGRES') == '1' and not is_pg():
        raise RuntimeError('Configura DATABASE_URL PostgreSQL para almacenamiento persistente')
    with db() as con:
        cur = con.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS mediciones (
            id TEXT PRIMARY KEY,
            experimento TEXT NOT NULL,
            nodo_id TEXT NOT NULL,
            zona TEXT NOT NULL,
            ronda TEXT NOT NULL,
            secuencia INTEGER NOT NULL,
            temperatura_c DOUBLE PRECISION NOT NULL,
            humedad_pct DOUBLE PRECISION NOT NULL,
            protocolo TEXT NOT NULL,
            recibido_utc TEXT NOT NULL,
            bytes_json INTEGER NOT NULL,
            bytes_mensaje_estimados INTEGER NOT NULL,
            topico TEXT,
            UNIQUE(experimento, nodo_id, ronda, protocolo, secuencia)
        )''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_mediciones ON mediciones (experimento, protocolo, recibido_utc)')



def save(m, protocol, bytes_json, bytes_total, topic=None):
    now = datetime.now(timezone.utc).isoformat()
    values = (str(uuid4()), m.experimento, m.nodo_id, m.zona, m.ronda,
              m.secuencia, m.temperatura_c, m.humedad_pct, protocol, now,
              bytes_json, bytes_total, topic)
    with db() as con:
        cur = con.cursor()
        cur.execute(sql('''INSERT INTO mediciones
          (id, experimento, nodo_id, zona, ronda, secuencia, temperatura_c,
           humedad_pct, protocolo, recibido_utc, bytes_json, bytes_mensaje_estimados, topico)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (experimento, nodo_id, ronda, protocolo, secuencia) DO NOTHING'''), values)
        inserted = cur.rowcount == 1
    return {'registrada': inserted, 'duplicada': not inserted, 'recibido_utc': now}


def list_data(experiment, protocol=None, node=None, round_id=None, limit=300):
    conditions, args = ['experimento = ?'], [experiment]
    for field, value in [('protocolo', protocol), ('nodo_id', node), ('ronda', round_id)]:
        if value:
            conditions.append(field + ' = ?')
            args.append(value)
    args.append(limit)
    query = f'SELECT {", ".join(FIELDS)} FROM mediciones WHERE ' + ' AND '.join(conditions) + ' ORDER BY recibido_utc DESC LIMIT ?'
    with db() as con:
        cur = con.cursor()
        cur.execute(sql(query), args)
        return [dict(zip(FIELDS, row)) for row in cur.fetchall()]


def stats(experiment, node=None, round_id=None):
    conditions, args = ['experimento = ?'], [experiment]
    if node:
        conditions.append('nodo_id = ?')
        args.append(node)
    if round_id:
        conditions.append('ronda = ?')
        args.append(round_id)
    query = '''SELECT protocolo, COUNT(*), SUM(bytes_json), SUM(bytes_mensaje_estimados),
                      MIN(recibido_utc), MAX(recibido_utc)
               FROM mediciones WHERE ''' + ' AND '.join(conditions) + ' GROUP BY protocolo'
    with db() as con:
        cur = con.cursor()
        cur.execute(sql(query), args)
        output = {}
        for proto, count, payload, total, first, last in cur.fetchall():
            seconds = max(0.0, (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds())
            output[proto] = {
                'recibidas': count,
                'json_total_bytes': payload,
                'mensaje_estimado_total_bytes': total,
                'sobrecarga_estimada_total_bytes': total - payload,
                'eficiencia_porcentaje_estimada': round(100 * payload / total, 1) if total else 0,
                'duracion_recepciones_segundos': round(seconds, 1),
                'promedio_json_bytes': round(payload / count, 1),
                'promedio_mensaje_estimado_bytes': round(total / count, 1)
            }
        return output
