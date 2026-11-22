# Nanoleaf (add with existing token)

A small Home Assistant helper that adds a Nanoleaf device to the **built-in Nanoleaf integration** using its IP address and an auth token you already have.

It adds nothing of its own. Once the device is handed over, it is a normal Nanoleaf device in Home Assistant, run entirely by the built-in integration.

## Why it exists

The built-in Nanoleaf integration gets its token by pairing: you hold the device's power button and Home Assistant asks the device for a new token. That works well when Home Assistant can discover the device, or when you can add it by address and reach the button in time.

It leaves two gaps:

- Some Nanoleaf devices are not discovered by Home Assistant.
- There is no place to enter a token you already have, for example one you created earlier for another tool, or for a device whose button is awkward to reach (a ceiling fixture, say).

This helper fills that gap with one form: IP address and token. It checks them against the device and then passes them to the built-in integration, which sets the device up as usual.

## Supported devices

Any Nanoleaf device that:

- speaks the Nanoleaf local Open API (HTTP on port 16021), and
- works with Home Assistant's built-in Nanoleaf integration.

It has been used with a Nanoleaf Skylight. Other models should behave the same, because the helper only reads the device's name and leaves everything else to the built-in integration, but they have not been tried.

Essentials and other Thread/Matter-only Nanoleaf products do not have this API and are not supported.

## Requirements

- Home Assistant 2025.2 or newer. It is tested on 2026.11; the icon needs 2026.3 or newer.
- The built-in Nanoleaf integration (part of Home Assistant; nothing to install).
- The device's IPv4 address. Give the device a fixed address in your router so it does not change.
- An Open API auth token for the device (see below).

### Getting a token

If you do not have one yet:

1. Hold the device's power button for 5–7 seconds until its light starts flashing. On devices without a button, turn on pairing from the Nanoleaf app.
2. Within 30 seconds, send an empty POST request to the device from any computer on the same network:

   ```bash
   curl -X POST http://DEVICE_IP:16021/api/v1/new
   ```

3. The answer contains the token: `{"auth_token":"..."}`.

Treat the token like a password: anyone on your network who has it can control the device.

## Installation

### HACS (custom repository)

1. In HACS, open the menu (top right) and choose **Custom repositories**.
2. Add `https://github.com/OnticEntia/nanoleaf-token` with type **Integration**.
3. Find **Nanoleaf (add with existing token)** in HACS and download it.
4. Restart Home Assistant.

### Manual

1. Download this repository.
2. Copy the `custom_components/nanoleaf_token` folder into the `custom_components` folder of your Home Assistant configuration, so that you have `config/custom_components/nanoleaf_token/manifest.json`.
3. Restart Home Assistant.

## Configuration

There is no YAML. Everything is done in one form:

1. Go to **Settings > Devices & services > Helpers** and choose **Create helper** (it is also listed under **Add integration**).
2. Pick **Nanoleaf (add with existing token)**.
3. Enter the device's IP address and the token, and submit.

The helper then:

1. checks that the address is an IPv4 address and the token is only letters and digits;
2. asks the device for its details using the token, which proves both are right;
3. stops if that device is already set up in the Nanoleaf integration;
4. hands the address and token to the built-in Nanoleaf integration, which creates the device.

When it finishes you see a message naming the device. Find it under **Settings > Devices & services > Nanoleaf**. To add another device, run the form again.

## What it adds

- One setup form.
- No entities, no devices, no services, and no entry of its own under Devices & services. Everything you see afterwards belongs to the built-in Nanoleaf integration.

To remove a device later, delete it from the Nanoleaf integration. Removing this helper does not affect devices it has already handed over.

## How it works

The built-in integration can import a token for a discovered device from a file named `.nanoleaf.conf` in the configuration folder (a leftover from an older way of setting up Nanoleaf). The helper writes the token there, starts the built-in integration's discovery step for the device, and the built-in integration picks the token up, creates its entry and removes the file.

If the handover does not end with the device being added, the helper takes the token back out of the file, so nothing is left behind.

This relies on behaviour inside the built-in integration that is not a public interface. A future Home Assistant release could remove it. If that happens the form reports it and saves nothing.

## Troubleshooting

| Message | What it means | What to do |
|---|---|---|
| Enter an IPv4 address | The address is not in the form `a.b.c.d`. | Use the device's IPv4 address, without `http://` or a port. Host names are not accepted. |
| A Nanoleaf token is only letters and digits | The token has other characters in it. | Copy only the value of `auth_token`, without quotes or spaces. |
| Couldn't reach a Nanoleaf at that address | Nothing answered on port 16021, or it answered with an error. | Check the address, that the device is on, and that Home Assistant can reach it (same network or VLAN, no firewall rule in between). |
| The device rejected that token | The device answered, but does not know the token. | Check for typing mistakes, or create a new token. Resetting a device deletes its tokens. |
| Something answered at that address, but not like a Nanoleaf | A different kind of device is at that address. | Check the address. |
| This Nanoleaf is already set up | The Nanoleaf integration already has a device with that name. | Nothing to do. To change its address, use **Reconfigure** on the device in the Nanoleaf integration. |
| Home Assistant already has a setup waiting for this device | A discovered-device card for it is waiting under Devices & services. | Finish or ignore that card, then try again. |
| The Nanoleaf integration didn't add the device | The handover failed; the message includes the reason given. | Check **Settings > System > Logs**. If it persists after a Home Assistant update, the built-in integration may have changed; please open an issue. |

For more detail in the log, add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.nanoleaf_token: debug
```

The token is never written to the log.

## Development

The tests use only the Python standard library, with stand-ins for Home Assistant:

```bash
python3 -B -m unittest discover -s tests
```

## Licence

MIT; see [LICENSE](LICENSE).

This project is not affiliated with or endorsed by Nanoleaf or Home Assistant.
