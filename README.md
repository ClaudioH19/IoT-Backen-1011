# Backend único para los tres ejercicios

```text
ESP32 de Wokwi (N01 y N02)
   | HTTP POST /mediciones        | MQTT sobre WSS /mqtt
   +-------------+----------------+  (misma URL Render)
                 |
        Render - único Docker
          FastAPI + MQTT WS
                 |
             SQLite
                 |
          GET /mediciones
                 |
         Streamlit local
```

**Qué contiene:** FastAPI (HTTP), receptor MQTT 3.1.1 por WebSockets seguros (`/mqtt`), almacenamiento SQLite, métricas y consultas para Streamlit. Todo se inicia con **un único Dockerfile**, sin `docker-compose`, Mosquitto, Node ni puente aparte.

## Despliegue en Render

1. Sube **los archivos de esta carpeta `BACKEND/`** a la raíz de un repositorio GitHub nuevo (o conserva esta carpeta y configura `Root Directory = BACKEND` en Render).
2. En Render: **New → Web Service → GitHub → Language: Docker**.
3. Usa el Dockerfile incluido y configura **Health Check Path: `/salud`**. No necesitas Build Command, Start Command ni variables de entorno.
4. Despliega. Guarda la URL `https://TU_SERVICIO.onrender.com`.

## Comprobar antes de utilizar Wokwi

1. Abre `https://TU_SERVICIO.onrender.com/salud`. Debe responder `estado: ok`, `mqtt_wss: /mqtt`.
2. En tu computador, abre una terminal **dentro de `BACKEND/`**:

```bash
python -m pip install -r requirements.txt
python tools/probar_publicado.py https://TU_SERVICIO.onrender.com
```

Deben aparecer **cuatro comprobaciones OK**: salud, HTTP, MQTT con suscriptor y métricas. **No comiences los ejercicios si alguna falla.**

## Rutas para el estudiante

| Ruta | Utilidad |
|---|---|
| `POST /mediciones` | ESP32 envía HTTP |
| `wss://TU_SERVICIO.onrender.com/mqtt` | ESP32 publica y escucha MQTT 3.1.1 |
| `GET /nodos?experimento=lab2026` | Nodos participantes |
| `GET /mediciones?experimento=lab2026&protocolo=mqtt` | Datos MQTT para Streamlit |
| `GET /metricas/serial?experimento=lab2026&nodo_id=N01_ana&ronda=R01` | Comparación por estudiante |
| `GET /estado` | Clientes MQTT conectados y mensajes aceptados |
| `GET /docs` | Documentación de la API HTTP |

## Consideraciones

- Solo para la práctica: **sin autenticación**, datos ficticios, sin información sensible. Elimina o restringe el servicio al terminar.
- SQLite está dentro del contenedor: en Render gratuito, el historial **no es persistente** al suspender/reiniciar/desplegar.
- MQTT está implementado para este laboratorio: CONNECT, PUBLISH QoS 0/1, SUBSCRIBE, PING y UNSUBSCRIBE. No incluye QoS 2, sesiones persistentes ni mensajes retenidos. Los sketches utilizan QoS 0.
- La métrica de bytes MQTT considera el paquete MQTT PUBLISH, **no la encapsulación WSS/TLS/TCP/IP**. La métrica HTTP también es una estimación a nivel de aplicación, no tráfico de red real.
- Un mismo servicio Render debe estar activo para HTTP y MQTT; los servicios gratuitos pueden suspenderse tras inactividad.
