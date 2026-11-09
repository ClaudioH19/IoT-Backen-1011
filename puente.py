"""Un consumidor MQTT reenvía al backend HTTP. Un puente por laboratorio."""
import json
import logging
import os
import re
import time
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
TOPIC = re.compile(r'^doctorado/([A-Za-z0-9_-]{2,40})/(N0[1-3])/telemetria$')


def forward(topic, payload, api_url):
    match = TOPIC.fullmatch(topic)
    if not match:
        raise ValueError('Tópico fuera del esquema')
    event = json.loads(payload.decode('utf-8'))
    if (event.get('experimento'), event.get('nodo_id')) != match.groups():
        raise ValueError('Nodo en tópico y JSON no coinciden')
    data = json.dumps(event, separators=(',', ':')).encode()
    req = Request(api_url.rstrip('/') + '/ingesta/mqtt', data=data, method='POST', headers={
        'Content-Type': 'application/json', 'X-MQTT-Topic': topic,
        'X-MQTT-Payload-Bytes': str(len(payload))
    })
    with urlopen(req, timeout=15) as response:
        return response.status


def main():
    import paho.mqtt.client as mqtt
    host = os.getenv('MQTT_HOST', 'broker')
    port = int(os.getenv('MQTT_PORT', '1883'))
    api_url = os.getenv('API_URL', 'http://api:8000')
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='puente-laboratorio', protocol=mqtt.MQTTv311)
    client.reconnect_delay_set(min_delay=2, max_delay=15)

    def connected(client, userdata, flags, reason_code, properties):
        logging.info('Broker: %s', reason_code)
        if reason_code == 0:
            client.subscribe('doctorado/+/+/telemetria', qos=0)

    def message(client, userdata, msg):
        try:
            status = forward(msg.topic, msg.payload, api_url)
            logging.info('%s -> API %s (%d B)', msg.topic, status, len(msg.payload))
        except Exception as exc:
            logging.warning('No reenviado %s: %s', msg.topic, exc)
            # Sin cola persistente. Este puente no asegura entregar durante fallas.

    client.on_connect = connected
    client.on_message = message
    while True:
        try:
            client.connect(host, port, 60)
            client.loop_forever(retry_first_connection=True)
        except Exception as exc:
            logging.error('MQTT no disponible: %s', exc)
            time.sleep(3)


if __name__ == '__main__':
    main()
