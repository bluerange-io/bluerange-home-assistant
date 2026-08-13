# BlueRange for Home Assistant

> **⚠️ Early beta — expect breakage.** This integration is at an early testing
> stage and is not production ready. Nothing about it is guaranteed to work:
> entity IDs, configuration options, service names and behaviour may change
> from one release to the next, and features may disappear. There is no
> commercial support, no service level, and no warranty of any kind — see
> [LICENSE](LICENSE). Use at your own risk, and please report what you find.

A custom integration that mirrors the devices of a [BlueRange](https://bluerange.io)
IoT installation into Home Assistant: sensors become entities, actuators become
switches, numbers, buttons, lights, thermostats and blinds. Readings arrive live
over the BlueRange message broker, with polling as a fallback.

Tested against Home Assistant **2026.8**.

## Contents

- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Service](#service)
- [Troubleshooting](#troubleshooting)
- [Versioning](#versioning)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## What you get

The integration talks to the API of a BlueRange server, so it works with the
cloud portal as well as with an on-premise installation. Every BlueRange device
becomes one Home Assistant device, placed in the area matching its BlueRange
room, and reached through a service device standing for the organisation it
belongs to.

BlueRange describes a device as a flat list of sensors and actuators, each
addressed by a type and an index. Those lists are interpreted to derive typed
entities:

| BlueRange control                                          | Home Assistant entity |
| ---------------------------------------------------------- | --------------------- |
| Sensor with the `ON_OFF` unit                              | `binary_sensor`       |
| Any other sensor                                           | `sensor`              |
| Actuator that only accepts one value (`min` equals `max`)  | `button`              |
| Actuator with the `ON_OFF` unit                            | `switch`              |
| Actuator with a range                                      | `number`              |
| `SET_DIMMING` plus its on and off commands                 | `light`               |
| `SET_SETPOINT_TEMPERATURE` plus its mode commands          | `climate`             |
| `SET_SLAT_POSITION` or `REQUEST_UP`/`REQUEST_DOWN`, plus `SET_SLAT_ANGLE` | `cover` |

Whenever a writable entity fully mirrors a sensor — a switch and its state, a
setpoint and its readback — that sensor is not exposed a second time. Physical
measurements are always kept as their own sensor, so that they keep their long
term statistics.

Two diagnostic sensors are added per device: its enrollment **Status** and the
timestamp of its **Last connection**.

Units are mapped onto Home Assistant device classes wherever the unit is one
Home Assistant accepts for that class. `KELVIN` is deliberately left without a
device class, because BlueRange uses it for colour temperature rather than for a
temperature reading.

## Requirements

- Home Assistant 2026.8 or newer
- A BlueRange server, either the cloud portal or on-premise
- An API token, created in the BlueRange portal (e.g.
  <https://portal.bluerange.io>) on your user profile page

The token is sent as the `X-User-Access-Token` header. The account behind it
needs read access to devices and sensor data, and — to control anything — write
access to the corresponding building automation controls.

For live updates the server has to be reachable over HTTPS and its message broker
has to be reachable from Home Assistant. Everything else works over plain HTTP as
well.

## Installation

### HACS

1. Add `https://github.com/bluerange-io/bluerange-home-assistant` as a custom repository of type
   *Integration*.
2. Install **BlueRange** and restart Home Assistant.

### Manually

Copy `custom_components/bluerange` into your `config/custom_components`
directory and restart Home Assistant.

## Configuration

Add the integration under **Settings → Devices & services → Add integration →
BlueRange**, then enter:

- **Server URL** — for example `https://portal.bluerange.io`. Anything after the
  host is ignored, so pasting a portal link works.
- **API token** — the token from your user profile.
- **Verify the TLS certificate** — turn this off only for servers with a
  self-signed certificate.

If the token reaches more than one BlueRange organisation, a second step asks
which one to mirror. Organisations sharing a display name are qualified with
their unique name. Every request is then scoped to that organisation, so what
Home Assistant shows does not depend on which one the server would have picked
by itself.

One config entry covers one BlueRange organisation. Adding a second token of the
same organisation is refused; tokens of different organisations can be added
side by side.

### Live updates and polling

The gateways publish every reading to the message broker the server itself
listens on, so the integration subscribes to it and gets the values as they
happen. This is on by default and needs no extra configuration: the broker
address and the credentials are requested from the server, which hands out access
for the same API token.

Polling does not go away, because the broker publishes without the retain flag —
a client that just connected sees nothing until the next reading of each sensor
arrives. So the state is fetched once over REST at startup, and while live
updates flow, polling drops to a safety net of at most every 15 minutes. If the
broker becomes unreachable, the configured interval takes over again immediately.

Live updates are skipped, with a note in the log, when the server has no broker
configured, when it is reached over plain HTTP (the endpoint handing out broker
access requires HTTPS), or when the broker cannot be reached. Polling alone keeps
working in all of those cases. It can also be turned off under **Configure**.

The polling interval defaults to 60 seconds. One update cycle sends one request
per device, capped at six in flight, so raise the interval for large
installations — especially if you turn live updates off.

A value written from Home Assistant travels to the device asynchronously, so the
entity shows the value you set until the server reports it back, or for 90
seconds at most.

## Service

Devices can expose vendor specific actuators that do not map onto any Home
Assistant entity type. `bluerange.set_actuator` writes to them directly:

```yaml
action: bluerange.set_actuator
target:
  device_id: 1a2b3c4d5e6f
data:
  control_type: SET_VENDOR_TEST_DEBUG
  index: 0
  value: 1
```

`module` may be given as well; without it the module from the device metadata is
used.

## Troubleshooting

Download the diagnostics of the config entry (**Settings → Devices & services →
BlueRange → ⋮ → Download diagnostics**). It lists every device with its sensors,
actuators, current readings and the entities they were classified into, which is
the quickest way to see why a control did or did not become the entity you
expected.

The diagnostics also report whether live updates are currently arriving and
which polling interval is in effect.

To see the requests, enable debug logging:

```yaml
logger:
  logs:
    custom_components.bluerange: debug
```

## Versioning

Releases follow Home Assistant's own dating, `YYYY.M.PATCH`, so that a version
says which Home Assistant release it was built against.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

A throwaway Home Assistant instance with the working tree mounted into it is
available under `dev/`:

```bash
docker compose -f dev/docker-compose.yml up -d
```

It serves Home Assistant on <http://localhost:8123> with debug logging for the
integration enabled. Restart the container to pick up code changes.

`dev/config/fake_bluerange.py` stands in for a BlueRange server when there is no
real one at hand. Run it inside the container with
`python3 /config/fake_bluerange.py`, then point an entry at
`http://127.0.0.1:9099` with the token `dev-token`. It serves a thermostat, a
dimmable luminaire and a venetian blind, offers two organisations, and reflects
actuator writes in the sensors that read them back.

The classification rules live in `controls.py` and carry no Home Assistant
imports, so they can be unit tested on their own. `mapping.py` holds the unit and
device class tables; the tests verify every combination against what Home
Assistant actually accepts, and that every translation key used in code exists in
`strings.json`, `translations/en.json` and `translations/de.json`.

The curated name of a control ends in `{channel}`, which carries the channel
number for every channel but the first — a device exposing the same control type
several times would otherwise end up with several identically named entities.
Home Assistant substitutes the placeholder, so a name may not be translated
without it; the tests check that every curated name still has it and that the
names of the device's own sensors, which have no channel, have none.

## Contributing

Bug reports and pull requests are welcome. Please open an
[issue](https://github.com/bluerange-io/bluerange-home-assistant/issues) with a
diagnostics download attached (see *Troubleshooting*) before opening a pull
request for a fix, so the behaviour is captured in one place. Contributions are
accepted under the same Apache License 2.0 that covers the rest of the code.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright © 2026 BlueRange GmbH.
