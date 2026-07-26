# Bosch EasyAir for Home Assistant

Experimental Home Assistant custom integration for Bosch EasyAir thermostats, with early focus on the Bosch BCC110.

This project is not affiliated with, endorsed by, or supported by Bosch.

## Status

This repository is the standalone home for the `bosch_easyair` custom integration. The integration code lives under:

```text
custom_components/bosch_easyair/
```

The first implementation targets the Bosch EasyAir / `smart-climate-ids` cloud API used by the BCC110. Local LAN control is not implemented.

## Installation

### HACS custom repository

1. In Home Assistant, open **HACS**.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add this repository URL.
4. Select **Integration** as the category.
5. Install **Bosch EasyAir**.
6. Restart Home Assistant.
7. Go to **Settings > Devices & services > Add integration** and add **Bosch EasyAir**.

### Manual install

Copy the integration folder into your Home Assistant config directory:

```text
custom_components/bosch_easyair/
```

The final path should look like:

```text
<home-assistant-config>/custom_components/bosch_easyair/manifest.json
```

Restart Home Assistant after copying files.

## Authentication

The EasyAir mobile app uses Bosch/SingleKey sign-in through Cognito and redirects to the registered mobile app URI:

```text
idsmobileapp://
```

Because Home Assistant cannot receive that app callback directly, setup uses a browser login plus a paste-back step for the final redirect URL/code. A manual token setup path may also be available for development and troubleshooting.

## Attribution

This standalone integration began as experimental Bosch EasyAir/BCC110 work developed while testing against [`bosch-thermostat/home-assistant-bosch-custom-component`](https://github.com/bosch-thermostat/home-assistant-bosch-custom-component), which is licensed under the Apache License 2.0.

## License

Apache License 2.0. See [LICENSE](LICENSE).
