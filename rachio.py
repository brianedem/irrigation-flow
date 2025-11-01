import logging
import requests
import json
# rachio provides an API that allows viewing and modifying the configuration of the
# irrigation controller, but no direct means to monitor the current state of the valves for
# polling.
# Instead, a webhook mechanism must be set up to provide notification of events. The
# number of webhooks that can be registered is 10, which is the exact number of valves that
# are being used, or the controller can be monitored for
# DEVICE_ZONE_RUN_STARTED/PAUSED/STOPPED/COMPLETED_EVENTS
#
public_rachio = 'https://api.rach.io/1/public/person' 
cloud_rachio = 'https://cloud-rest.rach.io'

logger = logging.getLogger(__name__)

class rachio():
    def __init__(self, APIkey, device_name):

        # all requests require authorization using the APIkey
        self.authorization = {"Authorization": f"Bearer {APIkey}"}
#       print(self.authorization)

        # get a userId associated with the auth token (the account)
        try:
            site = '{}/info'.format(public_rachio)
            r = requests.get(site, headers=self.authorization, timeout=5)
        except:
            exit(f'Error: no response from {site} while retrieving public/person/info structure')
#       print(r.json())
        self.userId = r.json()['id']
        logger.info(f'userId: {self.userId}')

        # use the userId to get all of the other IDs associated with zones, schedules, etc
        try:
            site = '{}/{}'.format(public_rachio, self.userId)
            r = requests.get(site, headers=self.authorization, timeout=5)
        except requests.exceptions.ReadTimeout:
            exit(f'Error: Get request to {site} for person/info timed out')

        try:
            self.user = r.json()
        except:
            exit('Data format error in site data structure')

        # locate the requested device
        for d in self.user['devices']:
            if d['name'] == device_name:
                break
        else:
            raise Exception(f"Controller {device_device} was not found")
        self.device = d

    def get_zones(self):

        zones = {}
        for z in self.device['zones']:
            _id = z['id']
            if z['enabled']:
                zones[_id] = {'valve': z['zoneNumber'], 'name': z['name']}

        # sort result by zone number
        return dict(sorted(zones.items(), key=lambda item: item[1]['valve']))

    def add_device_zone_run_webhook(self, target_url):

        url = '/'.join((cloud_rachio, 'webhook/createWebhook')),
        headers = self.authorization | {
            "accept": "application/json",
            "content-type": "application/json",
        }
        payload = {
            "resource_id": { "irrigation_controller_id": self.device['id'] },
            "url": target_url,
            "event_types": ["DEVICE_ZONE_RUN_STARTED_EVENT", "DEVICE_ZONE_RUN_PAUSED_EVENT", "DEVICE_ZONE_RUN_STOPPED_EVENT", "DEVICE_ZONE_RUN_COMPLETED_EVENT"],
        }
        if True:
            print(f'Posting to {url}:')
            pprint.pp(headers, payload)
        else:
            response = requests.post(url, json=payload, headers=headers)

            logger.debug(response.text)

    def delete_webhooks(self):
        action = f"webhook/deleteAllWebhooks?resource_id.irrigation_controller_id={self.device['id']}"

        headers = {"accept": "application/json"}

        response = requests.delete('/'.join((cloud_rachio, action)), headers=headers)
        
        logger.debug(response.text)

if __name__ == '__main__':
    import argparse
    import pprint

    parser = argparse.ArgumentParser()
    parser.add_argument('APIkey')
    parser.add_argument('ControllerName')
    args = parser.parse_args()

    APIkey = args.APIkey
    controllerName = args.ControllerName

    controller = rachio(APIkey, controllerName)

    print(f"Found controller {controllerName}, id is {controller.device['id']}")

    zones = controller.get_zones()

    pprint.pp(dict(sorted(zones.items(), key=lambda item: item[1]['valve'])), width=150)
