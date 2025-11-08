# irrigation-flow
As irrigation systems age pipes can break, emitters can fail, and valves can leak. To detect the failures of pipes and emitters this application will monitor the water flow rate for each zone and report via ntfy when the rate exceeds a threshold set for the zone. To detect leaking valves the application will check for water usage during off time. Leakage will also be reported via ntfy.

In addition to fault detection, the application will also track water use by zone to a database for later analysis.

The irrigation system is managed by a Rachio irrigation controller, which provides notification of state changes via webhooks. The application is responsible for setting up and verifying the webhook configuration.

Webhooks notifications from the irrigation controller are forwarded to the application via a ngrok agent running as a separate process.

Water use monitored by a TUF-2000B ultrasonic flow meter manufactured by Dalian Taosonics Instrument Co. Registers reporting water usage and flow are available via its RS485 interface, which is bridged to the local network using an application running on a Raspberry Pi PicoW.
 
Events and data are recorded in a cloud-based influx database. Eventually this needs to more to a more permanent solution, either a locally hosted influx database or the existing Mariadb database.

The config.ini file is used to specify:
- the Rachio API key and controller name
- the water meter name and MAC address
- the influxDB access token
- the ntfy topic (which defaults to the topic in ~/.ntfy)
- per-zone flow limits