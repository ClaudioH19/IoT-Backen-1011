"""Ensayo de extremo a extremo sobre UN servidor: HTTP + MQTT WSS + API.
Ejemplo: python tools/probar_publicado.py https://TU_SERVICIO.onrender.com
Dependencias: las de requirements.txt (incluyen websockets).
"""
import asyncio
import json
import sys
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from uuid import uuid4

from websockets.asyncio.client import connect


def texto(s):
    b = s.encode('utf-8')
    return len(b).to_bytes(2, 'big') + b


def frame(header, data=b''):
    n = len(data)
    length = bytearray()
    while True:
        digit = n & 127
        n >>= 7
        length.append(digit | (128 if n else 0))
        if not n:
            break
    return bytes([header]) + length + data


def unpack(pkt):
    idx = 1
    remaining = 0
    scale = 1
    while True:
        d = pkt[idx]
        idx += 1
        remaining += (d & 127) * scale
        if not d & 128:
            break
        scale *= 128
    return pkt[0] >> 4, pkt[idx:idx+remaining]


def request(url, method='GET', data=None):
    raw = json.dumps(data).encode() if data is not None else None
    req = Request(url, method=method, data=raw,
                  headers={'Content-Type': 'application/json'} if raw else {})
    with urlopen(req, timeout=90) as response:
        return json.load(response)


async def probar(base):
    base = base.rstrip('/')
    salud = request(base + '/salud')
    assert salud['estado'] == 'ok' and salud['mqtt_wss'] == '/mqtt'
    print('1/4 Salud HTTP: OK')
    sufijo = uuid4().hex[:8]
    ronda = f'R{sufijo}'
    dato = {'experimento': 'pruebaintegracion', 'nodo_id': 'N01_ensayo',
            'zona': 'exterior', 'ronda': ronda, 'secuencia': 1,
            'temperatura_c': 25.4, 'humedad_pct': 57.2}
    resp = request(base + '/mediciones', method='POST', data=dato)
    assert resp['registrada']
    print('2/4 HTTP POST almacenado: OK')

    dest = urlparse(base)
    esquema = 'wss' if dest.scheme == 'https' else 'ws'
    target = f'{esquema}://{dest.netloc}/mqtt'
    topic = f'doctorado/{dato["experimento"]}/{dato["nodo_id"]}/telemetria'
    async with asyncio.timeout(30):
      async with connect(target, subprotocols=['mqtt'], max_size=4096) as sub:
        con = texto('MQTT') + bytes([4, 2, 0, 30]) + texto('inspector')
        await sub.send(frame(0x10, con))
        assert await sub.recv() == b'\x20\x02\x00\x00', 'MQTT CONNACK invalido'
        await sub.send(frame(0x82, b'\x00\x01' + texto('doctorado/pruebaintegracion/+/telemetria') + b'\x00'))
        assert unpack(await sub.recv())[0] == 9, 'No llego SUBACK'
        async with connect(target, subprotocols=['mqtt'], max_size=4096) as pub:
            await pub.send(frame(0x10, texto('MQTT') + bytes([4, 2, 0, 30]) + texto('emisor')))
            assert await pub.recv() == b'\x20\x02\x00\x00', 'Sin MQTT CONNACK'
            await pub.send(frame(0x30, texto(topic) + json.dumps(dato).encode()))
            t, body = unpack(await sub.recv())
            assert t == 3 and topic.encode() in body and b'N01_ensayo' in body
            print('3/4 MQTT WSS publico + recepcion en suscriptor: OK')

    metrics = request(base + '/metricas?experimento=pruebaintegracion&nodo_id=N01_ensayo&ronda='+ronda)
    d = metrics['por_protocolo']
    assert d['http']['recibidas'] == 1 and d['mqtt']['recibidas'] == 1
    print('4/4 Metricas HTTP=1, MQTT=1: OK')
    print('PRUEBA COMPLETA: mismo dominio HTTP + MQTT + almacenamiento + suscripcion')


if __name__ == '__main__':
    if len(sys.argv) != 2 or not sys.argv[1].startswith(('http://','https://')):
        print('Uso: python tools/probar_publicado.py https://TU_SERVICIO.onrender.com')
        sys.exit(2)
    asyncio.run(probar(sys.argv[1]))
