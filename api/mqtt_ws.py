"""Receptor MQTT 3.1.1 de laboratorio sobre WebSocket binario.

Soporta CONNECT, SUBSCRIBE, UNSUBSCRIBE, PUBLISH QoS 0/1, PINGREQ y DISCONNECT.
NO es un broker MQTT general: no implementa QoS 2, sesiones persistentes,
mensajes retenidos ni almacenamiento offline. Pensado para 2-3 estaciones.
"""
import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .storage import save

logger = logging.getLogger('laboratorio.mqtt')
MAX_PACKET = 4096


def encode_length(n: int) -> bytes:
    out = bytearray()
    while True:
        digit = n % 128
        n //= 128
        out.append(digit | (128 if n else 0))
        if not n:
            return bytes(out)


def frame(header: int, payload: bytes) -> bytes:
    return bytes([header]) + encode_length(len(payload)) + payload


def read_field(buf: bytes, offset: int) -> tuple[str, int]:
    if len(buf) < offset + 2:
        raise ValueError('Longitud MQTT incompleta')
    length = int.from_bytes(buf[offset:offset + 2], 'big')
    offset += 2
    if len(buf) < offset + length:
        raise ValueError('Texto MQTT incompleto')
    return buf[offset:offset + length].decode('utf-8'), offset + length


def mqtt_topic_match(pattern: str, topic: str) -> bool:
    a, b = pattern.split('/'), topic.split('/')
    for i, part in enumerate(a):
        if part == '#':
            return i == len(a) - 1
        if i >= len(b):
            return False
        if part != '+' and part != b[i]:
            return False
    return len(a) == len(b)


def parse_next(buf: bytearray):
    if len(buf) < 2:
        return None
    n = multiplier = 0
    pos = 1
    for _ in range(4):
        if len(buf) <= pos:
            return None
        digit = buf[pos]
        pos += 1
        n += (digit & 127) * (128 ** (pos - 2))
        if not digit & 128:
            if n > MAX_PACKET:
                raise ValueError('Paquete MQTT demasiado grande')
            if len(buf) < pos + n:
                return None
            head, payload = buf[0], bytes(buf[pos:pos+n])
            del buf[:pos+n]
            return head, payload
    raise ValueError('Longitud MQTT invalida')


@dataclass(eq=False)
class Session:
    ws: WebSocket
    client_id: str = ''
    filters: set[str] = field(default_factory=set)
    connected: bool = False


class Broker:
    def __init__(self):
        self.sessions: set[Session] = set()
        self.received = 0
        self.rejected = 0

    def snapshot(self):
        return {'clientes_conectados': len(self.sessions),
                'publicaciones_aceptadas': self.received,
                'publicaciones_rechazadas': self.rejected}

    async def publish(self, topic: str, body: bytes):
        packet = frame(0x30, len(topic.encode()).to_bytes(2, 'big') + topic.encode() + body)
        for s in list(self.sessions):
            if any(mqtt_topic_match(p, topic) for p in s.filters):
                try:
                    await s.ws.send_bytes(packet)
                except (RuntimeError, WebSocketDisconnect):
                    self.sessions.discard(s)

    async def connect(self, sess: Session, body: bytes):
        proto, index = read_field(body, 0)
        if len(body) < index + 4:
            raise ValueError('CONNECT incompleto')
        level, flags = body[index], body[index + 1]
        index += 4  # version, flags, keepalive(2)
        if proto != 'MQTT' or level != 4 or flags & 0x01:
            raise ValueError('Solo MQTT 3.1.1')
        client_id, index = read_field(body, index)
        if not client_id or len(client_id) > 80:
            raise ValueError('Client ID no valido')
        sess.client_id = client_id
        sess.connected = True
        self.sessions.add(sess)
        await sess.ws.send_bytes(b'\x20\x02\x00\x00')  # CONNACK, conexion aceptada
        logger.info('MQTT conectado %s', client_id)

    async def handle_packet(self, sess: Session, header: int, body: bytes, model, estimator):
        kind = header >> 4
        if not sess.connected:
            if kind != 1:
                raise ValueError('Se requiere CONNECT')
            await self.connect(sess, body)
            return True
        if kind == 3:  # PUBLISH
            qos = (header >> 1) & 3
            if qos not in (0, 1):
                raise ValueError('QoS no soportado')
            topic, pos = read_field(body, 0)
            packet_id = None
            if qos == 1:
                if len(body) < pos + 2:
                    raise ValueError('Packet ID incompleto')
                packet_id = body[pos:pos+2]
                pos += 2
            payload = body[pos:]
            parts = topic.split('/')
            if (len(parts) != 4 or parts[0] != 'doctorado' or parts[3] != 'telemetria'
                    or not payload or len(payload) > 1024):
                self.rejected += 1
                logger.warning('MQTT rechazado por topico o tamano: %s', topic)
                return True
            try:
                import json
                data = model.model_validate(json.loads(payload))
                if parts[1] != data.experimento or parts[2] != data.nodo_id:
                    raise ValueError('Identidad no coincide con topico')
                estimated = estimator(len(payload), topic) + (2 if qos == 1 else 0)
                save(data, 'mqtt', len(payload), estimated, topic)
            except (ValueError, ValidationError, UnicodeDecodeError) as exc:
                self.rejected += 1
                logger.warning('MQTT invalido: %s', exc)
                return True
            self.received += 1
            await self.publish(topic, payload)
            if qos == 1 and packet_id:
                await sess.ws.send_bytes(b'\x40\x02' + packet_id)
            return True
        if kind == 8:  # SUBSCRIBE
            if header & 15 != 2 or len(body) < 5:
                raise ValueError('SUBSCRIBE invalido')
            pid, pos = body[:2], 2
            replies = bytearray()
            while pos < len(body):
                topic_filter, pos = read_field(body, pos)
                if pos >= len(body):
                    raise ValueError('SUBSCRIBE sin QoS')
                requested_qos = body[pos]
                pos += 1
                if (not topic_filter.startswith('doctorado/')
                        or '+' not in topic_filter and '#' not in topic_filter and topic_filter.count('/') < 2
                        or requested_qos > 2):
                    replies.append(0x80)
                else:
                    sess.filters.add(topic_filter)
                    replies.append(0x00)  # QoS 0
            await sess.ws.send_bytes(frame(0x90, pid + replies))
            return True
        if kind == 10:  # UNSUBSCRIBE
            if header & 15 != 2 or len(body) < 4:
                raise ValueError('UNSUBSCRIBE invalido')
            pid, pos = body[:2], 2
            while pos < len(body):
                topic_filter, pos = read_field(body, pos)
                sess.filters.discard(topic_filter)
            await sess.ws.send_bytes(frame(0xB0, pid))
            return True
        if kind == 12:  # PINGREQ
            await sess.ws.send_bytes(b'\xd0\x00')
            return True
        if kind == 14:  # DISCONNECT
            return False
        raise ValueError(f'Paquete MQTT no soportado: {kind}')

    async def serve(self, ws: WebSocket, model, estimator):
        offered = ws.scope.get('subprotocols', [])
        await ws.accept(subprotocol='mqtt' if 'mqtt' in offered else None)
        sess = Session(ws)
        buffer = bytearray()
        try:
            while True:
                message = await ws.receive()
                if message.get('type') == 'websocket.disconnect':
                    break
                data = message.get('bytes')
                if data is None:
                    raise ValueError('MQTT requiere frames WebSocket binarios')
                buffer.extend(data)
                if len(buffer) > MAX_PACKET + 8:
                    raise ValueError('Buffer MQTT demasiado grande')
                while True:
                    parsed = parse_next(buffer)
                    if parsed is None:
                        break
                    keep = await self.handle_packet(sess, *parsed, model, estimator)
                    if not keep:
                        return
        except (WebSocketDisconnect, RuntimeError):
            pass
        except ValueError as exc:
            self.rejected += 1
            logger.warning('MQTT sesion invalida: %s', exc)
            try:
                await ws.close(code=1003)
            except RuntimeError:
                pass
        finally:
            self.sessions.discard(sess)


broker = Broker()
