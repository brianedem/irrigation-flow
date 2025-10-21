# irrigation-flow
As irrigation systems age pipes can break, emitters can fail, and valves can leak. To detect the failures of pipes and emitters this application will monitor the water flow rate for each zone and report via ntfy when the rate exceeds a threshold set for the zone. To detect leaking valves the application will check for water usage during off time. Leakage will also be reported via ntfy.

In addition to fault detection, the application will also track water use by zone to a database for later analysis.

The irrigation system is managed by a Rachio irrigation controller, which provides notification of state changes via webhooks. These notifications will be forwarded to the application via an ngrok agent.

Water use monitored by a TUF-2000B ultrasonic flow meter manufactured by Dalian Taosonics Instrument Co. Registers reporting water usage and flow are available via its RS485 interface, which is bridged to the local network.

