"""API de laboratorio. Sin claves, para datos ficticios y uso temporal exclusivamente."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from .storage import init_db, save, list_data, stats
from .mqtt_ws import broker


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='IoT: HTTP y MQTT', version='2.0', lifespan=lifespan)


class Medicion(BaseModel):
    experimento: str = Field(pattern=r'^[A-Za-z0-9_-]{2,40}$')
    nodo_id: str = Field(pattern=r'^N0[1-3](?:_[a-z0-9]{2,20})?$')
    zona: str = Field(pattern=r'^[A-Za-z0-9_-]{2,30}$')
    ronda: str = Field(pattern=r'^[A-Za-z0-9_-]{2,30}$')
    secuencia: int = Field(ge=1, le=1000000)
    temperatura_c: float = Field(ge=-40, le=90)
    humedad_pct: float = Field(ge=0, le=100)


def http_estimate(request: Request, raw: bytes) -> int:
    # Aproximación a nivel HTTP visible por la aplicación, no bytes reales en la red.
    line = f'POST {request.url.path} HTTP/{request.scope.get("http_version", "1.1")}\r\n'
    hdr = sum(len(k) + 2 + len(v) + 2 for k, v in request.scope.get('headers', []))
    return len(line.encode()) + hdr + 2 + len(raw)


def mqtt_qos0_estimate(payload_size: int, topic: str) -> int:
    # MQTT 3.1.1 PUBLISH, QoS 0: cabecera fija + longitud variable + tópico + payload.
    remaining = 2 + len(topic.encode('utf-8')) + payload_size
    encoded_len = 1
    n = remaining
    while n >= 128:
        encoded_len += 1
        n //= 128
    return 1 + encoded_len + remaining


async def raw_limited(request: Request):
    raw = await request.body()
    if len(raw) > 1024:
        raise HTTPException(status_code=413, detail='JSON demasiado grande (>1024 bytes)')
    return raw


@app.get('/salud')
def salud():
    return {'estado': 'ok', 'http': 'disponible', 'mqtt_wss': '/mqtt'}


@app.post('/mediciones', status_code=201)
async def receive_http(event: Medicion, request: Request):
    raw = await raw_limited(request)
    return {'protocolo': 'http', **save(event, 'http', len(raw), http_estimate(request, raw))}


@app.get('/mediciones')
def measurements(experimento: str = Query(min_length=2),
                 protocolo: str | None = Query(default=None, pattern='^(http|mqtt)$'),
                 nodo_id: str | None = None, ronda: str | None = None,
                 limite: int = Query(default=300, ge=1, le=1000)):
    records = list_data(experimento, protocolo, nodo_id, ronda, limite)
    return {'total': len(records), 'datos': records}


@app.get('/nodos')
def nodes(experimento: str = Query(min_length=2)):
    latest = {}
    now = datetime.now(timezone.utc)
    for item in list_data(experimento, limit=1000):
        if item['nodo_id'] in latest:
            continue
        age = max(0, (now - datetime.fromisoformat(item['recibido_utc'])).total_seconds())
        latest[item['nodo_id']] = {
            'nodo_id': item['nodo_id'], 'zona': item['zona'],
            'temperatura_c': item['temperatura_c'], 'humedad_pct': item['humedad_pct'],
            'ultimo_protocolo': item['protocolo'], 'segundos_desde_ultimo_dato': round(age, 1),
            'reciente': age < 30,
        }
    return {'nodos': sorted(latest.values(), key=lambda item: item['nodo_id'])}


@app.get('/metricas')
def metrics(experimento: str = Query(min_length=2), nodo_id: str | None = None,
            ronda: str | None = None):
    return {
        'experimento': experimento, 'nodo_id': nodo_id, 'ronda': ronda,
        'por_protocolo': stats(experimento, nodo_id, ronda),
        'nota': 'Conteos de mediciones únicas y bytes estimados a nivel de aplicación, NO tráfico real de red ni consumo de energía.'
    }


@app.get('/metricas/serial', response_class=PlainTextResponse)
def metrics_serial(experimento: str = Query(min_length=2), nodo_id: str = Query(min_length=3),
                   ronda: str = Query(min_length=2)):
    results = stats(experimento, nodo_id, ronda)
    lines = [f'METRICAS DEL BACKEND: {experimento} / {nodo_id} / {ronda}',
             'Recibidas = mediciones UNICAS; tamano estimado NO es trafico de red.']
    for proto in ('http', 'mqtt'):
        v = results.get(proto)
        if not v:
            lines.append(f'{proto.upper()}: 0 recibidas')
            continue
        lines.append(f'{proto.upper()}: {v["recibidas"]} recibidas | JSON {v["json_total_bytes"]} B | '
                     f'mensaje estimado {v["mensaje_estimado_total_bytes"]} B | '
                     f'sobrecarga estimada {v["sobrecarga_estimada_total_bytes"]} B | '
                     f'eficiencia {v["eficiencia_porcentaje_estimada"]}% | '
                     f'ventana {v["duracion_recepciones_segundos"]} s')
    lines.append('Excluye TCP/IP, WiFi, TLS, conexion, retransmisiones y energia.')
    return '\n'.join(lines) + '\n'


@app.get('/estado')
def estado():
    return {'mqtt': broker.snapshot(), 'ruta_wss': '/mqtt', 'observacion': 'broker MQTT 3.1.1 limitado a este laboratorio'}


@app.websocket('/mqtt')
async def mqtt_over_websocket(ws: WebSocket):
    await broker.serve(ws, Medicion, mqtt_qos0_estimate)
