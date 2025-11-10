import argparse
import configparser
import logging
import pprint
import socket
import platform
import os
import subprocess
import requests
import rachio
import queue
import uuid
import enum
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
from influxdb_client_3 import InfluxDBClient3, Point
import datetime
import time
import water_meter

# As the webhook mechanism requires a public interface, an additional external mechanism must be set
# up to forward the notification to a system behind a NAT router. This mechanism could be one of the
# following:
# - port forwarding (local router)
# - network tunnel (local app communicates with edge proxy, such as cloudflare or ngrok)
# For this implementation ngrok will be used as it requires the minimal amount of configuration.
# ngrok can be set up as a background service - see https://ngrok.com/docs/agent#running-ngrok-in-the-background

################################################################################
# process command line arguments
app_description = \
'''This application reports excess irrigation water usage.

Water flow rates are monitored per zone, and flow rates exceeding the zone
threshold generate a notification.

Water usage while the system is idle will also generate a notification.

Notifications are generated using ntfy, which can be received on a moble phone.

A config.ini file is used to establish network addresses and application keys
for accessing the irrigation controller, the water meter, and the notification
service.
'''
parser = argparse.ArgumentParser(
                    prog=__name__,
                    description=__doc__)
parser.add_argument('--leak_test', action='store_true')
args = parser.parse_args()

################################################################################
# read configuration
config = configparser.ConfigParser()
config.read(os.path.expanduser('~/.ntfy'))
config.read('config.ini')

log = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s %(filename)s %(message)s', level=logging.INFO)
# check that water meter information is in the configuration
if 'WATERMETER' not in config.sections():
    exit('WATERMETER section missing from config.ini')
wm_name = config['WATERMETER']['name']

################################################################################
# check to see if the host will be able to determine the IP address of the water meter
for domain in ('', '.attlocal.net', '.local'):
    wm_path = wm_name + domain
    try:
        socket.gethostbyname(wm_path)
        wm_name = wm_path
        break
    except:
        pass
else:
    # the system was unable to determine the water meter's IP address using the name lookup,
    # so use arp-scan to dump the address information of all devices on the subnet
    # and find the water meter's IP address by searching for its MAC address

    # macOS requires arp-scan to run as admin, so check that first
    # TODO could also change the group_id of the /dev/bpf* devices to staff
    if 'macOS' in platform.platform() and os.getuid() != 0:
        exit(f'Error: command needs to run as admin for arp-scan')

    # arp-scan also requires the water meter's MAC address
    if (wm_mac_addr := config.get('WATERMETER', 'MAC', fallback=None)) is None:
        exit(f'''Error:
 Unable to determine the network address of {wm_name} using DNS
 Unable to search using arp-scan as MAC address is missing from configuration''')

    log.info('water meter MAC address: %s', wm_mac_addr)

    # use netstat to list all of the network interfaces
    ns = subprocess.run(['netstat', '-rnf', 'inet'], capture_output=True)

    # locate the default interface and extract name of the interface
    for line in str(ns.stdout, encoding='utf-8').split('\n'):
        if line.startswith('default') or line.startswith('0.0.0.0'):
            interface = line.split()[-1]
            break
    else:
        exit("Unable to determine the default network interface using arp-scan")
    log.info('network interface %s', interface)

    # scan the subnet
    hosts = subprocess.run(['arp-scan', '--localnet', f'--interface={interface}', '--quiet'], capture_output=True)

    # locate the target MAC address from the responses
    text = str(hosts.stdout, encoding='utf-8').split('\n')
    for line in text:
        if wm_mac_addr in line:
            wm_name = line.split()[0]
            break
    else:
        exit(f'Error: unable to determine IP address of water meter {wm_name}')
log.info('water meter at %s', wm_name)

################################################################################
# verify ngrok tunnel is up and determine the public endpoint url
try:
    ngrok = requests.get('http://localhost:4040/api/tunnels')
except requests.exceptions.ConnectionError:
    exit('Error - ngrok agent is not running')
#print(ngrok.status_code)
#print(ngrok.text)
#pprint.pp(ngrok.json())
tunnel0 = ngrok.json()['tunnels'][0]
public_url = tunnel0['public_url']
local_addr = tunnel0['config']['addr']
local_port = int(local_addr.split(':')[-1])
#print(public_url)

################################################################################
# determine the rachio valve mapping
if 'RACHIO' not in config.sections():
    exit('RACHIO section missing from config.ini')

rc = config['RACHIO']
controller = rachio.rachio(rc['APIkey'], rc['Name'])

################################################################################
# set up state variables for each valve
class valve_state:
    def __init__(self, valve_id, valve_name):
        self.valve_open = False
        self.meter_start_value = None
        self.flow_timer = None
        self.flow = None
        self.usage = 0
        self.id = valve_id
        self.name = valve_name

zone_info = controller.get_zones()
zones = {}
for vid in zone_info:
#   print(zone_info[vid])
    valve = zone_info[vid]['valve']
    zones[valve] = valve_state(vid, zone_info[vid]['name'])
    log.debug('%s: %s %s', valve, vid, zone_info[vid]['name'])

################################################################################
# create event queue for webhook and flow measurement callback
event_queue = queue.Queue()
class EVENT_TYPE(enum.Enum):
    WEBHOOK = 1     # received webhook POST message
    FLOW_TIMER = 2  # callback from webhook START message

# create a random string for the webhook path on the server
#webhook_path = f'/rachio/{uuid.uuid4()}'
webhook_path = '/rachio.json'       # use for debug

################################################################################
# set up the web server handler to process the webhook POST messages
class PostHandler(BaseHTTPRequestHandler):
    def validate(s):
        if s.headers['Content-Type'] != 'application/json':
            return None
        if s.path != webhook_path:
            return None
        if 'Content-Length' not in s.headers:
            return None
        try:
            content_length = int(s.headers['Content-Length'])
        except ValueError:
            return None

#       pprint.pp(dict(s.headers))
        post_data = s.rfile.read(content_length)
#       pprint.pp(post_data)
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

    # redefine the log functions as they write directly to stderr
    def log_error(s, format, *args):
        log.error(format, *args)
    def log_message(s, format, *args):  # used by log_request() and log_error()
        log.debug(format, *args)
        
# start up the web server in a separate thread
httpd = HTTPServer(('', local_port), PostHandler)
log.info('Webhook web server listening on %s', local_addr)
server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
server_thread.start()

################################################################################
# install webhooks at Rachio
webhook_url = public_url + webhook_path
controller.add_device_zone_run_webhook(webhook_url)

################################################################################
# set up connection to database
if (token := config.get('INFLUXDB', 'Token')) is None:
    token = os.environ.get("INFLUXDB_TOKEN")
host = "https://us-east-1-1.aws.cloud2.influxdata.com"
database = "irrigation"
client = InfluxDBClient3(host=host, token=token, database=database)

################################################################################
def send_notification(message):
    if topic := config.get('NTFY', 'Topic', fallback=None):
        try:
            requests.post(f'https://ntfy.sh/{topic}', data=message.encode(encoding='utf-8'))
        except:
            log.error('send_notification() failed')
################################################################################
# every night check for leaks over an hour interval, record daily water usage,
# and test the webhook mechanism
test_message_received = threading.Event()
def leak_check(test=False):
    while True:
        if not test:
            # wait until 2300 hours when system will be idle
            now = datetime.datetime.now()
            target_time = now.replace(hour=23,minute=0,second=0,microsecond=0)
            if target_time < now:
                target_time += datetime.timedelta(days=1)
            delay = (target_time - now).seconds
            log.debug('leak_check sleeping for %d seconds', delay)
            time.sleep(delay)

        # make two water meter readings one hour apart
        start_reading = water_meter.read_meter(wm_name)
        log.debug('First leak test meter reading: %s', pprint.pformat(start_reading))
        if not test:
            time.sleep(60*60)
        end_reading = water_meter.read_meter(wm_name)
        log.debug('Second leak test meter reading: %s', pprint.pformat(end_reading))
        test = False

        # check for water usage (leakage)
        start_value = start_reading.get('accumulated', None)
        end_value = end_reading.get('accumulated', None)
        if start_value is None:
            leakage = None
        elif end_value is None:
            leakage = None
        else:
            leakage = end_value - start_value
        log.debug('Leakage was %f', leakage)

        # send ntfy message of leak
        if leakage and leakage > 0.1:
            log.error('One hour leakage of %0.3f detected', leakage)
            send_notification('Irrigation leak detected')

        # log daily meter reading to database
        if end_value is not None:
            point = Point("water_meter").field("reading", end_value)
            client.write(record=point, write_procesion="s")

        # POST test message to public webhook site
        headers = {"content-type": "application/json"}
        payload = {"eventType": "WEBHOOK_TEST"}
        try :
            response = requests.post(webhook_url, json=payload, headers=headers)
        except:
            log.error('POST to webhook URL failed')

        # send notification if the webhook test message is not received
        if not test_message_received.wait(timeout=10):
            log.error('failed to receive daily test message')
            send_notification('Irrigation webhook test failed')
        else:
            test_message_received.clear()

# start up the leak_check in its own thread
leak_thread = threading.Thread(target=leak_check, args=(args.leak_test,), daemon=True)
leak_thread.start()

################################################################################
# process webhook events from queue
try:
    while True:
        q = event_queue.get()
        log.debug('%s', pprint.pformat(q))
        etype, data = q
        if etype is EVENT_TYPE.WEBHOOK:

            # decode the message and verify type
            eventType = data['eventType']
            if "WEBHOOK_TEST" in eventType:     # private type to test webhook forwarding
                test_message_received.set()
                continue
            if "DEVICE_ZONE_RUN" not in eventType:
                log.warning(f'ignoring {eventType}')
                continue
            eventId = data['eventId']
            payload = data['payload']
            zoneNumber = int(payload['zoneNumber'])
            zone = zones[zoneNumber]

            # read the water usage meter
            meter_data = water_meter.read_meter(wm_name)
            log.debug('Water meter reading at webhook: %s', pprint.pformat(meter_data))

            if zone.valve_open:
                if "STARTED" in eventType:
                    log.info('Zone %s START - ignored, valve already open', zone.name)
                    continue
                zone.valve_open = False

                # eventType is PAUSED/STOPPED/COMPLETED

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
                point = Point("usage").tag("zone", str(zoneNumber)).field("usage", usage).field("flow", zone.flow)
                client.write(record=point, write_procesion="s")

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
                    log.info('Zone %s stopped - %s, %s', zone.name, usage, flow)
                elif "COMPLETED" in eventType:  # zone schedule has run to completion
                    log.info('Zone %s completed - %s, %s', zone.name, usage, flow)
                else:
                    log.warning('Unexpected %s', eventType)

                # reset zone values
                zone.usage = 0
                zone.flow = None

            else:   # valve is closed
                if "STARTED" in eventType:
                    log.debug('Zone %s started', zone.name)
                    zone.valve_open = True
                    zone.meter_start_value = meter_data.get('accumulated', None)
                    zone.startId = eventId
                    # wait for line pressure to equalize before reading flow rate
                    if zone.flow is None:
                        args = ((EVENT_TYPE.FLOW_TIMER, (zoneNumber,eventId)),)
                        zone.timer = threading.Timer(20, event_queue.put, args=args)
                        zone.timer.start()
                else:
                    log.info('Valve %s is not open - ignoring %s', zone.name, eventType)

        elif etype is EVENT_TYPE.FLOW_TIMER:
            # the delay in receiving zone notifications could result in reading the
            # meter either after the valve has closed or has switched to another
            # zone. This is unlikely as the flow measurement is made 20 seconds into
            # the irrigation cycle, which will probably only occur on the ending
            # cycle of a zone using the 'soak' feature
            zoneNumber, timerId = data
            zone = zones[zoneNumber]
            if not zone.valve_open or zone.startId != timerId:
                continue
            meter_data = water_meter.read_meter(wm_name)
            log.debug(pprint.pformat(meter_data))
            zone.flow = meter_data.get['flow', None]
            # TODO need to verify zone.flow before using
            flow_limit = config.get('FLOW', 'str(zoneNumber)', fallback=None) 
            if flow_limit and zone.flow > flow_limit:
                send_notification('Irrigation leak detected')
        else:
            log.warning('Unknown event %s', etype)

except KeyboardInterrupt:
    pass
httpd.server_close()
