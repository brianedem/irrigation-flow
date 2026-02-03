import argparse
import configparser
import logging
import pprint
import locate_iot
import requests
import rachio
import queue
import enum
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import datetime
import time
import water_meter

# As the webhook mechanism requires a public interface, an additional external mechanism must be set
# up to forward the notification to a system behind a NAT router.
# This implementation will use ngrok as it requires a minimal amount of configuration.
# ngrok can be set up as a background service - see https://ngrok.com/docs/agent#running-ngrok-in-the-background

################################################################################
# process command line arguments
app_description = \
'''This application logs irrigation water usage to a database.

For each irrigation cycle, the zone, cycle duration, flow rate, and water
used during the cycle is logged to the database.

At the end of each day a one-hour leak test is performed. The leakage amount
and the water meter reading at the end of the day are logged to the database.
Water usage while the system is idle will also generate a notification.

A config.ini file is used to establish network addresses and application keys
for accessing the irrigation controller and the water meter.

The application relies on the ngrok external service to forward webhook HTTP POST
requests.
'''
parser = argparse.ArgumentParser(
                    prog=__name__,
                    description=__doc__)
parser.add_argument('--leak_test', action='store_true')
parser.add_argument('--configure', action='store_true')
args = parser.parse_args()

################################################################################
# read configuration
config_file = 'config.ini'
config = configparser.ConfigParser()
files_read = config.read(config_file)

################################################################################
# set up logging
log = logging.getLogger(__name__)
logging.basicConfig(format='%(filename)s %(message)s', level=logging.INFO)

################################################################################
# generate a config.ini file if requested
config_template = '''
[NGROK]
ClientHost      .Host name that NGROK Client will connect to
[RACHIO]
APIkey          .Key supplied by Rachio website
Name            .Host name for the irrigation controller
[WATERMETER]
Name            .Host name for the watermeter
MacAddr         .MAC address for watermeter (for MacOS)
[INFLUXDB]
Server          .URL for the InfluxDB server
Org `           .Organization name 
Token           .Token for accessing the 'irrigation' Bucket
'''
if args.configure:
    if files_read:
        exit(f'{config_file} exists')
    with open(config_file, 'w', encoding='utf-8') as config_fd:
        for line in config_template.splitlines():
            if line.strip() == '':
                continue
            if line[0]=='[':
                config_fd.write(line + '\n')
                print(f'Section {line}')
                continue
            item = line.split()[0]
            item_help = line[line.index('.')+1:]
            print(item_help)
            value = input(f'{item} = ')
            config_fd.write(f'{item} = {value}\n')
    exit('File creation complete')

################################################################################
# The MacOS sometimes has trouble looking up IOT IP addresses
# verify that the system will be able to determine the IP address of the water meter
section_name = 'WATERMETER'
try:
    watermeter_config = config[section_name]
except KeyError:
    exit(f'[{section_name}] section of {config_file} missing')
try:
    watermeter_name = watermeter_config['Name']
    watermeter_mac_addr = watermeter_config.get('MacAddr', None)
except KeyError as a:
    exit(f'Unable to find {a} in [{section_name}] section of {config_file}')
wm_name = locate_iot.locate(watermeter_name, watermeter_mac_addr)

log.debug('water meter located at %s', wm_name)

################################################################################
# verify ngrok tunnel is up and determine the public endpoint url
section_name = 'NGROK'
try:
    ngrok_config = config[section_name]
except KeyError:
    exit(f'[{section_name}] section of {config_file} missing')
try:
    ngrok_host = ngrok_config['ClientHost']
except KeyError as a:
    exit(f'Unable to find {a} in [{section_name}] section of {config_file}')

try:
    ngrok = requests.get(f'http://{ngrok_host}:4040/api/tunnels')
except requests.exceptions.ConnectionError:
    exit('Error - ngrok agent is not running')

tunnel0 = ngrok.json()['tunnels'][0]
tunnel_public_url = tunnel0['public_url']
tunnel_local_addr = tunnel0['config']['addr']
tunnel_local_port = int(tunnel_local_addr.split(':')[-1])
log.debug('ngrok public endpoint at %s', tunnel_public_url)

################################################################################
# determine the rachio valve mapping
section_name = 'RACHIO'
try:
    rachio_config = config['RACHIO']
except KeyError:
    exit(f'{section_name} section missing from {config_file}')

try:
    rachio_api_key = rachio_config['APIkey']
    rachio_name = rachio_config['Name']
except KeyError as a:
    exit(f'Unable to find {a} in [{section_name}] section of {config_file}')

controller = rachio.rachio(rachio_api_key, rachio_name)

################################################################################
# set up state variables for each valve
class zone_state:
    def __init__(self, zone_id, zone_name):
        self.valve_open = False
        self.meter_start_value = None
        self.flow_timer = None
        self.flow = None
        self.usage = 0
        self.id = zone_id
        self.name = zone_name

zone_info = controller.get_zones()
zones = {}
for zone in zone_info:                  # zone is the integer value number
    zid = zone_info[zone]['id']         # Rachio assigned identifier
    zname = zone_info[zone]['name']     # user assigned name for the zone
    zones[zone] = zone_state(zid, zname)
    log.debug('%d: %s %s', zone, zid, zname)

################################################################################
# create event queue for webhook and flow measurement callback
event_queue = queue.Queue()
class EVENT_TYPE(enum.Enum):
    WEBHOOK = 1     # received webhook POST message
    FLOW_TIMER = 2  # callback from webhook START message

webhook_path = '/rachio.json'

################################################################################
# create a simple web server to receive the webhook POST messages from Rachio
class PostHandler(BaseHTTPRequestHandler):
    def validate(s):
        if s.path != webhook_path:
            return None
        try:
            content_length = int(s.headers['Content-Length'])
            content_type = s.headers['Content-Type']
        except KeyError:
            return None

        if content_length > 1000:
            return None
        if content_type != 'application/json':
            return None

        post_data = s.rfile.read(content_length)

        try:
            data = json.loads(post_data)
        except ValueError:
            return None
        return data
    
    def do_POST(s):
        data = s.validate()
        if data is None:
            s.send_error(400, 'Bad Request')
            return

        s.send_response(200)
        s.end_headers()
        s.wfile.write('OK'.encode('utf-8'))

        event_queue.put((EVENT_TYPE.WEBHOOK, data))

    # redefine the class logging functions to use standard logging rather
    # than writing directly to stderr
    def log_error(s, format, *args):
        log.error(format, *args)
    def log_message(s, format, *args):  # used by log_request() and log_error()
        log.debug(format, *args)
        
# start the web server in its own thread
httpd = HTTPServer(('', tunnel_local_port), PostHandler)
log.debug('Webhook web server listening on %s', tunnel_local_addr)
server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
server_thread.start()

################################################################################
# install webhooks at Rachio
webhook_url = tunnel_public_url + webhook_path
controller.add_device_zone_run_webhook(webhook_url)

################################################################################
# set up connection to database
section_name = 'INFLUXDB'
try:
    influx_config = config[section_name]
except KeyError:
    exit(f'{section_name} section missing from {config_file}')
try:
    influx_server = influx_config['Server']
    influx_token = influx_config['Token']
    influx_org = influx_config['Org']
    influx_bucket = 'irrigation'
except KeyError as a:
    exit(f'Unable to find {a} in [{section_name}] section of {config_file}')

influx_client = InfluxDBClient(url=influx_server, token=influx_token, org=influx_org)
influx_write_api = influx_client.write_api(write_options=SYNCHRONOUS)

################################################################################
# every night check for leaks over an hour interval, record daily water usage,
# and test the webhook mechanism
test_message_received = threading.Event()
def leak_check(test_mode=False):
    while True:
        if not test_mode:
            # wait until 2300 hours when system will be idle
            now = datetime.datetime.now()
            target_time = now.replace(hour=23,minute=0,second=0,microsecond=0)
            if target_time < now:
                target_time += datetime.timedelta(days=1)
            delay = (target_time - now).seconds
            log.debug('leak_check will start in %d seconds', delay)
            time.sleep(delay)

        # make two water meter readings one hour apart
        start_reading = water_meter.read_meter(wm_name)
        log.debug('First leak test meter reading: %s', pprint.pformat(start_reading))
        if not test_mode:
            time.sleep(60*60)
        end_reading = water_meter.read_meter(wm_name)
        log.debug('Second leak test meter reading: %s', pprint.pformat(end_reading))
        test_mode = False

        # check for water usage (leakage)
        start_value = start_reading.get('accumulated', None)
        end_value = end_reading.get('accumulated', None)
        if start_value and end_value:
            leakage = end_value - start_value
        else:
            leakage = None
        log.debug('Leakage was %f', leakage)

        # log the leak
        if leakage and leakage > 0.1:
            log.warning('One hour leakage of %0.3f cf detected', leakage)

        # log daily meter reading and leakage to database
        if end_value is not None:
            point = Point("water_meter").field("reading", end_value)
            point.field("leakage", leakage)
            influx_write_api.write(bucket=influx_bucket, record=point, org=influx_org)

        # send a POST test message to public webhook site
        headers = {"content-type": "application/json"}
        payload = {"eventType": "WEBHOOK_TEST"}
        try :
            requests.post(webhook_url, json=payload, headers=headers)
        except requests.exceptions.RequestException as e:
            log.error('POST to webhook URL failed with %s', e)

        # log failure if the webhook test message is not received
        if test_message_received.wait(timeout=10):
            test_message_received.clear()
        else:
            log.error('failed to receive daily webhook test message')

# start up the leak_check in its own thread
leak_thread = threading.Thread(target=leak_check, args=(args.leak_test,), daemon=True)
leak_thread.start()

################################################################################
# process events from queue
try:
    while True:
        q = event_queue.get()
        log.debug('%s', pprint.pformat(q))
        etype, edata = q
        if etype is EVENT_TYPE.WEBHOOK:

            # decode the message and verify type
            try:
                eventId = edata['eventId']
                eventType = edata['eventType']
                payload = edata['payload']
            except KeyError as e:
                log.warning('Problem extracting %s from webhook POST response', e)
                continue

            if "WEBHOOK_TEST" in eventType:     # private type to test webhook forwarding
                test_message_received.set()
                continue
            if "DEVICE_ZONE_RUN" not in eventType:
                log.warning(f'ignoring {eventType}')
                continue

            try:
                duration = int(payload['durationSeconds'])
                zoneNumber = int(payload['zoneNumber'])
            except KeyError as e:
                log.warning('Problem extracting %s from webhook POST response', e)
                continue

            try:
                zone = zones[zoneNumber]            # zoneNumber could be out of range
            except KeyError as e:
                log.warning('Processing webhook: Zone %s is not an active zone', e)
                continue

            # read the water usage meter
            meter_data = water_meter.read_meter(wm_name)
            log.debug('Water meter reading at webhook: %s', pprint.pformat(meter_data))

            if zone.valve_open:
                if "STARTED" in eventType:
                    log.info('Zone %d %s START - ignored, valve already open', zoneNumber, zone.name)
                    continue
                zone.valve_open = False

                # else eventType must be one of PAUSED/STOPPED/COMPLETED

                # determine water usage - None if any readings failed
                meter_end_value = meter_data.get('accumulated', None)
                if zone.usage is None:
                    usage = None
                elif zone.meter_start_value is None:
                    usage = None
                elif meter_end_value is None:
                    usage = None
                else:
                    usage = zone.usage + meter_end_value - zone.meter_start_value

                if "PAUSED" in eventType:       # operator has paused the zone, to be STARTED later
                    log.debug('Zone %s paused', zone.name)
                    zone.usage = usage
                    continue

                # log data collected
                point = Point(zone.name).field("usage", usage).field("flow", zone.flow)
                influx_write_api.write(bucket='irrigation', record=point)

                # reformat data for logging/messages
                if usage is None:
                    usage = 'unknown usage'
                else:
                    usage = f'{usage:.2f} cf'
                if zone.flow is None:
                    flow = 'unknown flow'
                else:
                    flow = f'{zone.flow:.2f} gpm'

                # log the event
                if "STOPPED" in eventType:    # operator has stopped the zone
                    log.debug('Zone %d %s stopped - %s, %s', zoneNumber, zone.name, usage, flow)
                elif "COMPLETED" in eventType:  # zone schedule has run to completion
                    log.debug('Zone %d %s completed - %s, %s', zoneNumber, zone.name, usage, flow)
                else:
                    log.warning('Received unexpected eventType %s', eventType)

                # reset zone values
                zone.usage = 0
                zone.flow = None

            else:   # valve is closed
                if "STARTED" in eventType:
                    log.debug('Zone %d %s started', zoneNumber, zone.name)
                    zone.valve_open = True
                    zone.meter_start_value = meter_data.get('accumulated', None)
                    zone.startId = eventId
                    # wait for line pressure to equalize before reading flow rate
                    if zone.flow is None:
                        args = ((EVENT_TYPE.FLOW_TIMER, (zoneNumber,eventId)),)
                        zone.timer = threading.Timer(20, event_queue.put, args=args)
                        zone.timer.start()
                else:
                    log.info('Valve %d %s is not open - ignoring %s', zoneNumber, zone.name, eventType)

        elif etype is EVENT_TYPE.FLOW_TIMER:
            # the delay in receiving zone notifications could result in reading the
            # meter either after the valve has closed or has switched to another
            # zone. This is unlikely as the flow measurement is made 20 seconds into
            # the irrigation cycle, which will probably only occur on the ending
            # cycle of a zone using the 'soak' feature
            zoneNumber, timerId = edata
            zone = zones[zoneNumber]
            if not zone.valve_open or zone.startId != timerId:
                continue
            meter_data = water_meter.read_meter(wm_name)
            log.debug(pprint.pformat(meter_data))
            zone.flow = meter_data.get('flow', None)
        else:
            log.warning('Unknown event %s', etype)

except KeyboardInterrupt:
    pass
httpd.server_close()
