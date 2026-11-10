import json
import os
import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.mqtt_ws import frame, read_field


def strf(text):
    b = text.encode()
    return len(b).to_bytes(2, 'big') + b


def connect(id='cliente-a'):
    body = strf('MQTT') + bytes([4, 2, 0, 30]) + strf(id)
    return frame(0x10, body)


def subscribe(pattern):
    return frame(0x82, bytes([0, 1]) + strf(pattern) + b'\x00')


def publish(topic, data):
    return frame(0x30, strf(topic) + json.dumps(data).encode())


def make_data(nodo, seq):
    return {'experimento': 'lab2026', 'nodo_id': nodo, 'zona': 'exterior',
            'ronda': 'R01', 'secuencia': seq, 'temperatura_c': 25.0,
            'humedad_pct': 55.0}


@pytest.fixture

def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'lab.db'))
    with TestClient(app) as c:
        yield c


def test_http_y_mqtt_misma_api(client):
    resp = client.get('/salud')
    assert resp.status_code == 200 and resp.json()['mqtt_wss'] == '/mqtt'
    h = client.post('/mediciones', json=make_data('N01_ana', 1))
    assert h.status_code == 201 and h.json()['registrada']
    with client.websocket_connect('/mqtt', subprotocols=['mqtt']) as ws:
        ws.send_bytes(connect())
        assert ws.receive_bytes() == b'\x20\x02\x00\x00'
        ws.send_bytes(publish('doctorado/lab2026/N01_ana/telemetria', make_data('N01_ana', 1)))
        # Se almacena independientemente del registro HTTP
        assert client.get('/metricas', params={'experimento':'lab2026','nodo_id':'N01_ana','ronda':'R01'}).json()['por_protocolo'].keys() == {'http','mqtt'}
        ws.send_bytes(b'\xc0\x00')
        assert ws.receive_bytes() == b'\xd0\x00'
    data = client.get('/mediciones', params={'experimento':'lab2026'}).json()['datos']
    assert {d['protocolo'] for d in data} == {'http','mqtt'}
    assert client.post('/mediciones', json=make_data('N01_ana', 1)).json()['duplicada']


def test_suscripcion_recibe_datos_de_companero(client):
    with client.websocket_connect('/mqtt', subprotocols=['mqtt']) as a:
      a.send_bytes(connect('cliente-a'))
      assert a.receive_bytes() == b'\x20\x02\x00\x00'
      a.send_bytes(subscribe('doctorado/lab2026/+/telemetria'))
      ack = a.receive_bytes()
      assert ack == b'\x90\x03\x00\x01\x00'
      with client.websocket_connect('/mqtt', subprotocols=['mqtt']) as b:
        b.send_bytes(connect('cliente-b'))
        assert b.receive_bytes() == b'\x20\x02\x00\x00'
        b.send_bytes(publish('doctorado/lab2026/N02_luis/telemetria', make_data('N02_luis', 1)))
        received = a.receive_bytes()
        assert received[0] == 0x30
        # remaining length < 128 for payload used? len JSON ~155 => >= 128
        pos = 1
        while received[pos] & 0x80:
            pos += 1
        pos += 1
        topic, where = read_field(received, pos)
        assert topic == 'doctorado/lab2026/N02_luis/telemetria'
        assert json.loads(received[where:])['nodo_id'] == 'N02_luis'
    assert client.get('/metricas', params={'experimento':'lab2026'}).json()['por_protocolo']['mqtt']['recibidas'] == 1


def test_id_invalido_no_se_almacena(client):
    with client.websocket_connect('/mqtt', subprotocols=['mqtt']) as ws:
        ws.send_bytes(connect())
        assert ws.receive_bytes() == b'\x20\x02\x00\x00'
        ws.send_bytes(publish('doctorado/lab2026/N03_maria/telemetria', make_data('N01_ana', 9)))
    assert client.get('/mediciones', params={'experimento':'lab2026'}).json()['total'] == 0


def test_duplicado_mqtt(client):
    with client.websocket_connect('/mqtt', subprotocols=['mqtt']) as ws:
        ws.send_bytes(connect())
        assert ws.receive_bytes() == b'\x20\x02\x00\x00'
        b = publish('doctorado/lab2026/N01_ana/telemetria', make_data('N01_ana', 1))
        ws.send_bytes(b)
        ws.send_bytes(b)
    assert client.get('/metricas', params={'experimento':'lab2026'}).json()['por_protocolo']['mqtt']['recibidas'] == 1
