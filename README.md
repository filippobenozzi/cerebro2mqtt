# Cerebro2MQTT

Bridge BUS/seriale proprietario -> MQTT con discovery automatico Home Assistant, pagina web di configurazione (porta 80), polling manuale/automatico e persistenza locale JSON.

## Funzioni implementate

- Conversione comandi MQTT verso frame BUS compatibili con il protocollo dei file C forniti.
- Parsing risposta polling esteso (`0x40`) e pubblicazione stato su MQTT.
- Configurazione da web UI (`/`, porta 80):
  - seriale
  - broker MQTT
  - polling
  - elenco schede (`luci`, `tapparelle`, `termostato`, `dimmer`)
- toggle `Pubblica MQTT` per singola scheda
  - comando di riavvio servizio
- Salvataggio configurazione in JSON locale (`CEREBRO_CONFIG`, default `./config/config.json`).
- Discovery Home Assistant automatico (entita + pulsanti polling):
  - pulsante globale polling
  - pulsante polling per ogni scheda
  - entita per ogni scheda in base al tipo
- Deploy pronto per Raspberry (systemd) o Docker.

## Requisiti

- Python 3.11+
- Broker MQTT raggiungibile
- Interfaccia seriale BUS (es. `/dev/ttyUSB0`)

## Avvio locale

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Web UI su `http://<host>:80`.

## Deploy Docker

```bash
docker compose up -d --build
```

La configurazione JSON viene salvata fuori dal container in `./data/config.json` (bind mount host `./data:/config`).
Nella web UI puoi anche scaricare il file con il pulsante `Scarica JSON`.
Nel `docker-compose.yml` modifica il device seriale se necessario.

## Deploy systemd (Raspberry)

### Installazione automatica (consigliata)

```bash
sudo bash scripts/install_raspberry.sh
```

Lo script:

- installa dipendenze di sistema (`python3`, `python3-venv`, `python3-pip`, `rsync`)
- disabilita/maska in modo persistente `serial-getty` sulla porta seriale configurata
- copia il progetto in `/opt/cerebro2mqtt`
- crea virtualenv e installa `requirements.txt`
- crea/aggiorna il servizio `systemd`
- abilita e avvia `cerebro2mqtt.service`
- mantiene il `config/config.json` esistente in caso di reinstallazione

Variabili opzionali:

```bash
sudo INSTALL_DIR=/opt/cerebro2mqtt SERVICE_NAME=cerebro2mqtt.service APP_USER=root bash scripts/install_raspberry.sh
```

Per usare una porta diversa:

```bash
sudo SERIAL_PORT=/dev/ttyUSB0 bash scripts/install_raspberry.sh
```

### Installazione manuale

1. Copia progetto in `/opt/cerebro2mqtt`
2. Copia unit file:

```bash
sudo cp systemd/cerebro2mqtt.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cerebro2mqtt.service
```

3. Log:

```bash
sudo journalctl -u cerebro2mqtt.service -f
```

## Struttura configurazione JSON

```json
{
  "serial": {
    "port": "/dev/ttyUSB0",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout_sec": 0.25
  },
  "mqtt": {
    "host": "127.0.0.1",
    "port": 1883,
    "username": "",
    "password": "",
    "client_id": "cerebro2mqtt",
    "base_topic": "cerebro2mqtt",
    "discovery_prefix": "homeassistant",
    "keepalive": 60
  },
  "polling": {
    "interval_sec": 30,
    "auto_start": true
  },
  "web": {
    "host": "0.0.0.0",
    "port": 80
  },
  "service": {
    "restart_command": "systemctl restart cerebro2mqtt.service"
  },
  "boards": [
    {
      "id": "uuid",
      "name": "Luci Piano Terra",
      "type": "luci",
      "address": 2,
      "channel": 1,
      "channel_start": 1,
      "channel_end": 8,
      "topic": "luci_piano_terra",
      "publish_enabled": true,
      "enabled": true
    }
  ]
}
```

## Topic MQTT usati

Base topic default: `cerebro2mqtt`

### Globali

- `cerebro2mqtt/poll_all/set` -> avvia polling di tutti gli indirizzi configurati

### Per scheda (slug = `topic` oppure `name` normalizzato)

- Polling singola scheda:
  - `cerebro2mqtt/<slug>/poll/set`
  - esito polling: `cerebro2mqtt/<slug>/poll/last` (JSON con `success`)

- Luci:
  - cmd per canale: `cerebro2mqtt/<slug>/ch/<canale>/set` (`ON`/`OFF`)
  - state per canale: `cerebro2mqtt/<slug>/ch/<canale>/state`
  - esempio: `cerebro2mqtt/luci_piano_terra/ch/1/set`

- Tapparelle:
  - cmd: `cerebro2mqtt/<slug>/set` (`OPEN`/`CLOSE`)
  - state: `cerebro2mqtt/<slug>/state`

- Dimmer:
  - cmd on/off: `cerebro2mqtt/<slug>/set`
  - cmd brightness: `cerebro2mqtt/<slug>/brightness/set` (`0-100` o `0-255`)
  - state on/off: `cerebro2mqtt/<slug>/state`
  - state brightness: `cerebro2mqtt/<slug>/brightness/state` (`0-255`)

- Termostato:
  - setpoint cmd: `cerebro2mqtt/<slug>/setpoint/set`
  - setpoint state: `cerebro2mqtt/<slug>/setpoint/state`
  - temperatura state: `cerebro2mqtt/<slug>/temperature/state`
  - stagione cmd: `cerebro2mqtt/<slug>/season/set` (`WINTER`/`SUMMER`)
  - stagione state: `cerebro2mqtt/<slug>/season/state`

Per debug polling grezzo:
- `cerebro2mqtt/<slug>/polling/raw`

Esito azioni (ack dal BUS):
- `cerebro2mqtt/<slug>/action/result` (JSON con `action`, `success`, `detail`)

## Home Assistant

Il bridge pubblica automaticamente discovery su `homeassistant/.../config` con payload retained.
Se una scheda ha `publish_enabled=false`, le entita discovery di quella scheda vengono rimosse.

Entita create:

- Pulsante `Cerebro Polling` (globale)
- Pulsante polling per ogni scheda
- `switch` per `luci` (uno per ogni canale nel range configurato)
- `cover` per `tapparelle`
- `light` per `dimmer`
- `sensor` temperatura + `number` setpoint + `select` stagione per `termostato`

## Note protocollo usato

Implementazione coerente con i file C forniti:

- Frame: `0x49 <addr> <cmd> <10 data> 0x46`
- Polling extended: `0x40`
- Setpoint temperatura: `0x5A`
- Set stagione: `0x6B`
- Luci: comandi `0x51..0x54` e `0x65..0x68`
- Tapparelle: `0x5C`
- Dimmer: `0x5B`

## Verifica ritorno comandi

Per ogni comando inviato (luci/tapparelle/dimmer/termostato) il bridge aspetta il frame di ritorno della scheda prima di confermare lo stato su MQTT.
Se non arriva risposta entro timeout, pubblica `success=false` su `<slug>/action/result` e non conferma il nuovo stato.
Per il polling vengono accettate risposte sia `0x40` che `0x50`.

## Test base

```bash
python -m unittest discover -s tests
```

## Troubleshooting seriale Raspberry

Se vedi errori come `device reports readiness to read but returned no data`:

1. Verifica che solo un processo usi la porta:

```bash
sudo lsof /dev/ttyS0
```

2. Disabilita login console seriale su Raspberry (spesso occupa `ttyS0`):

```bash
sudo systemctl disable --now serial-getty@ttyS0.service
```

3. Riavvia il servizio bridge:

```bash
sudo systemctl restart cerebro2mqtt.service
```

4. Verifica parametri UART:
   - `bytesize` deve essere 5, 6, 7 o 8 (tipicamente 8)
   - non usare `bytesize=15`: `15` non e la lunghezza frame, e un parametro UART non valido

5. Rendere persistente il blocco di `serial-getty`:

```bash
sudo systemctl mask serial-getty@ttyS0.service
sudo systemctl mask serial-getty@serial0.service
```
