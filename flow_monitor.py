import configparser
import logging
import socket
import platform
import os
import subprocess
import requests
import rachio
from http.server import HTTPServer, BaseHTTPRequestHandler
import pprint
import json
import uuid
import threading
import queue
import water_meter
import enum

# As the webhook mechanism requires a public interface, an additional external mechanism must be set
# up to forward the notification to a system behind a NAT router. This mechanism could be one of the
# following:
# - port forwarding (local router)
# - network tunnel (local app communicates with edge proxy, such as cloudflare or ngrok)
# For this implementation ngrok will be used as it requires the minimal amount of configuration.
# ngrok can be set up as a background service - see https://ngrok.com/docs/agent#running-ngrok-in-the-background
#
config = configparser.ConfigParser()
config.read('config.ini')

log = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s %(filename)s %(message)s', level=logging.DEBUG)
# check that water meter information is in the configuration
if 'WATERMETER' not in config.sections():
    exit('WATERMETER section missing from config.ini')
wm_name = config['WATERMETER']['name']

# check to see if the host is able to determine the IP address of the water meter
for domain in ('', '.attlocal.net', '.local'):
    wm_path = wm_name + domain
    try:
        socket.gethostbyname(wm_path)
        wm_name = wm_path
        break
    except:
        pass
else:
    # was unable to locate using name lookup in the usual places, so try to find
    # the water meter's IP address from the MAC address using route and arp-scan system commands
    if (wm_mac_addr := config.get('WATERMETER', 'MAC', fallback=None)) is None:
        exit(f'''Error:
 Unable to determine the network address of {wm_name} using DNS
 Unable to search using arp-scan as MAC address is missing from configuration''')
    log.debug(f'water meter MAC address: {wm_mac_addr}')

    # arp-scan needs the name of the network interface - use route system command
    network_addr = '192.168.1.0'    # FIXME need to determine programatically
    if 'macOS' in platform.platform():
        # arp-scan command on macOS has to run as admin
        if os.getuid() != 0:
            exit(f'''Error:
 Unable to determine network address of {wm_name} using DNS
 Rerun command as admin to allow search by MAC address''')
        paths = subprocess.run(['route', 'get', network_addr], capture_output=True)
        # extract the interface
        text = str(paths.stdout, encoding='utf-8').split('\n')
        for line in text:
            if 'interface' in line:
                interface = line.split()[1]
                break
    else:
        paths = subprocess.run(['route'])
        text = str(paths.stdout, encoding='utf-8').split('\n')
        # extract the interface
        for line in text:
            if network_addr in line:
                interface = line.split()[-1]
                break
    log.debug(f'network interface {interface}')

    # use arp-scan to send ARP requests to all IP4 hosts on local network
    hosts = subprocess.run(['arp-scan', '--localnet', f'--interface={interface}', '--quiet'], capture_output=True)
    # locate the target MAC address from the responses
    text = str(hosts.stdout, encoding='utf-8').split('\n')
    for line in text:
        if wm_mac_addr in line:
            wm_name = line.split()[0]
            break
    else:
        exit(f'Error: unable to determine IP address of water meter {wm_name}')
log.debug(f'water meter at {wm_name}')

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

# determine the rachio valve mapping
if 'RACHIO' not in config.sections():
    exit('RACHIO section missing from config.ini')

rc = config['RACHIO']
controller = rachio.rachio(rc['APIkey'], rc['Name'])

# set up state variables for each valve
class valve_state:
    def __init__(self, valve_id, valve_name):
        self.valve_open = False
        self.meter_start = None
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
    log.info(f'{valve}: {vid} {zone_info[vid]['name']}')

# Event queue for webhook and flow measurement callback
event_queue = queue.Queue()
class EVENTTYPE(enum.Enum):
    WEBHOOK = 1
    FLOW_TIMER = 2
    LEAK_TIMER = 3
    DAY_TIMER = 4

# create a random string for the webhook path on the server
#webhook_path = f'/rachio/{uuid.uuid4()}'
webhook_path = '/rachio.json'       # use for debug

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

        event_queue.put((EVENTTYPE.WEBHOOK, data))

    # redefine the log functions as they write directly to stderr
    def log_error(s, format, *args):
        log.error(format, *args)
    def log_message(s, format, *args):  # used by log_request() and log_error()
        log.info(format, *args)
        
# start up the web server in a separate thread
httpd = HTTPServer(('', local_port), PostHandler)
log.info('Webhook web server listening on %s', local_addr)
server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
server_thread.start()

# install webhooks at Rachio
url = public_url + webhook_path
controller.add_device_zone_run_webhook(url)

# capture daily water usage
# TODO daily at midnight push request on queue to record water meter value

# process events from queue
try:
    while True:
        q = event_queue.get()
        log.info('%s', pprint.pformat(q))
        etype, data = q
        if etype is EVENTTYPE.WEBHOOK:
#           pprint.pp(data)
            eventType = data['eventType']
            if "DEVICE_ZONE_RUN" not in eventType:
                log.warning(f'ignoring {eventType}')
                continue
            eventId = data['eventId']
            payload = data['payload']
            zoneNumber = int(payload['zoneNumber'])
            zone = zones[zoneNumber]

            # read the water usage meter
            meter_data = water_meter.read_meter(wm_name)
#           meter_data = water_meter.read_meter('192.168.1.190')
            log.info('%s', pprint.pformat(meter_data))

            if zone.valve_open:
                if "STARTED" in eventType:
                    log.info('Valve %d started - ignored, valve already open', zoneNumber)
                    continue
                zone.valve_open = False
                usage = zone.usage + meter_data['accumulated'] - zone.meter_start
                if "PAUSED" in eventType:
                    log.info('Valve %d paused', zoneNumber)
                    zone.usage = usage
                    continue
                elif "COMPLETED" in eventType:
                    log.info('Valve %d completed - %dcf, %sgpm', zoneNumber, usage, f'{zone.flow}')
                elif "STOPPED" in eventType:
                    log.info(f'Valve %d stopped - %dcf, %sgpm', zoneNumber, usage, f'{zone.flow}')
                else:
                    log.warning(f'Unknown {eventType}')
                # TODO need to log data collected
                # time/date, zone, flow, usage, runtime
                zone.usage = 0
                zone.flow = None
            else:
                if "STARTED" in eventType:
                    log.info(f'Valve {zoneNumber} started')
                    zone.valve_open = True
                    zone.meter_start = meter_data['accumulated']
                    zone.startId = eventId
                    # wait for line pressure to equalize before reading flow rate
                    if zone.flow is None:
                        args = ((EVENTTYPE.FLOW_TIMER, (zoneNumber,eventId)),)
                        zone.timer = threading.Timer(20, event_queue.put, args=args)
                        zone.timer.start()
        elif etype is EVENTTYPE.FLOW_TIMER:
            # TODO valve event is slow relative to local reading of meter, so flow reading could be off
            # if the valve was turned off ~20s after STARTED
            log.info('%s', pprint.pformat(data))
            zoneNumber, timerId = data
            zone = zones[zoneNumber]
            if not zone.valve_open or zone.startId != timerId:
                continue
            meter_data = water_meter.read_meter('192.168.1.190')
            log.info('%s', pprint.pformat(meter_data))
            zone.flow = meter_data['flow']


        else:
            log.warning(f'Unknown event {etype}')

except KeyboardInterrupt:
    pass
httpd.server_close()
