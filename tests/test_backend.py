import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

# El backend debe poder validarse sin desplegar Render ni conectar un ESP32.
class BackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(cls.tmp.name) / 'datos.db')
        from api.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()
        os.environ.pop('DATABASE_URL', None)

    def payload(self, node='N01', seq=1):
        return {'experimento':'labtest', 'nodo_id':node, 'zona':'exterior',
                'ronda':'R01', 'secuencia':seq, 'temperatura_c':24.5, 'humedad_pct':60.0}

    def test_health(self):
        self.assertEqual(self.client.get('/salud').json()['estado'], 'ok')

    def test_http_and_duplicate(self):
        first = self.client.post('/mediciones', json=self.payload())
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()['registrada'])
        second = self.client.post('/mediciones', json=self.payload())
        self.assertTrue(second.json()['duplicada'])
        x = self.client.get('/metricas',params={'experimento':'labtest','nodo_id':'N01','ronda':'R01'}).json()
        self.assertEqual(x['por_protocolo']['http']['recibidas'], 1)

    def test_mqtt_and_serial(self):
        topic = 'doctorado/labtest/N02/telemetria'
        data = self.payload('N02', 1)
        response = self.client.post('/ingesta/mqtt', json=data,
            headers={'X-MQTT-Topic':topic,'X-MQTT-Payload-Bytes':'143'})
        self.assertEqual(response.status_code, 201)
        serial = self.client.get('/metricas/serial',params={'experimento':'labtest','nodo_id':'N02','ronda':'R01'}).text
        self.assertIn('MQTT: 1 recibidas', serial)

    def test_invalid_topic_and_range(self):
        bad = self.client.post('/ingesta/mqtt', json=self.payload('N03', 1),
             headers={'X-MQTT-Topic':'doctorado/labtest/N01/telemetria'})
        self.assertEqual(bad.status_code, 422)
        bad2 = self.client.post('/mediciones', json={**self.payload(seq=200), 'humedad_pct':200})
        self.assertEqual(bad2.status_code, 422)

    def test_same_node_both_protocols(self):
        payload = self.payload('N03', 50)
        a = self.client.post('/mediciones', json=payload)
        b = self.client.post('/ingesta/mqtt', json=payload,
                             headers={'X-MQTT-Topic': 'doctorado/labtest/N03/telemetria',
                                      'X-MQTT-Payload-Bytes': '143'})
        self.assertEqual((a.status_code, b.status_code), (201, 201))
        s = self.client.get('/metricas', params={'experimento': 'labtest',
                          'nodo_id':'N03','ronda':'R01'}).json()['por_protocolo']
        self.assertEqual(s['http']['recibidas'], 1)
        self.assertEqual(s['mqtt']['recibidas'], 1)
        self.assertGreater(s['http']['mensaje_estimado_total_bytes'], s['http']['json_total_bytes'])
        self.assertGreater(s['mqtt']['mensaje_estimado_total_bytes'], s['mqtt']['json_total_bytes'])

    def test_named_nodes_separate_metrics_and_topics(self):
        # Dos alumnos pueden compartir el prefijo N01 sin mezclar mediciones.
        for node in ('N01_ana', 'N01_pedro'):
            response = self.client.post('/mediciones', json=self.payload(node, 1))
            self.assertEqual(response.status_code, 201)
            self.assertTrue(response.json()['registrada'])
        a = self.client.get('/metricas', params={'experimento':'labtest',
                  'nodo_id':'N01_ana','ronda':'R01'}).json()['por_protocolo']
        b = self.client.get('/metricas', params={'experimento':'labtest',
                  'nodo_id':'N01_pedro','ronda':'R01'}).json()['por_protocolo']
        self.assertEqual(a['http']['recibidas'], 1)
        self.assertEqual(b['http']['recibidas'], 1)
        data = self.payload('N01_ana', 2)
        topic = 'doctorado/labtest/N01_ana/telemetria'
        r = self.client.post('/ingesta/mqtt', json=data, headers={
            'X-MQTT-Topic': topic, 'X-MQTT-Payload-Bytes':'150'})
        self.assertEqual(r.status_code, 201)
        wrong = self.client.post('/ingesta/mqtt', json=self.payload('N01_ana',3),
             headers={'X-MQTT-Topic':'doctorado/labtest/N01_pedro/telemetria'})
        self.assertEqual(wrong.status_code, 422)
        metric = self.client.get('/metricas', params={'experimento':'labtest',
            'nodo_id':'N01_ana','ronda':'R01'}).json()['por_protocolo']
        self.assertEqual(metric['mqtt']['recibidas'], 1)

    def test_reject_bad_student_node(self):
        for node in ('N01_ana maria', 'N01_á', 'N01_name/telemetria', 'N04_ana'):
            response = self.client.post('/mediciones', json=self.payload(node, 1))
            self.assertEqual(response.status_code, 422)

    def test_nodes(self):
        self.client.post('/mediciones', json=self.payload('N01', 11))
        nodes = self.client.get('/nodos',params={'experimento':'labtest'}).json()['nodos']
        self.assertTrue(any(n['nodo_id']=='N01' for n in nodes))


if __name__=='__main__':
    unittest.main()
