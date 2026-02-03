import logging
import requests
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

log = logging.getLogger(__name__)

class rachio():
    def __init__(self, APIkey, device_name):

        # all requests require authorization using the APIkey
        self.authorization = {"Authorization": f"Bearer {APIkey}"}

        # get a userId associated with the auth token (the account)
        try:
            site = '{}/info'.format(public_rachio)
            r = requests.get(site, headers=self.authorization, timeout=5)
        except requests.exceptions.RequestException as e:
            exit(f'Error: {e} from {site}')

        try:
            self.userId = r.json()['id']
            log.info(f'user ID: {self.userId}')
        except requests.exceptions.JSONDecodeError:
            exit(f'Error: JSON decode error while processing response from {site}')
        except KeyError as e:
            exit(f'Error: Unable to locate key {e} in JSON response from {site}')

        # use the userId to get all of the other IDs associated with zones, schedules, etc
        for i in range(1,3):    # sometimes the response times out so try multiple times
            try:
                site = '{}/{}'.format(public_rachio, self.userId)
                r = requests.get(site, headers=self.authorization, timeout=5)
                break
            except requests.exceptions.RequestException as e:
                exit(f'Error: {e} from {site}')

        try:
            self.user = r.json()
        except requests.exceptions.JSONDecodeError:
            exit('Error: JSON decode error while processing rachio public/info response')

        # locate the requested device
        try:
            for d in self.user['devices']:
                if d['name'] == device_name:
                    break
            else:
                raise Exception(f"Controller {device_name} was not found")
        except KeyError as e:
            exit(f'Error: key error {e} while processing response from {site}')
        self.device = d
        log.info('controller ID: %s', d['id'])

    # returns dictonary of zone info sorted and indexed by integer zone number
    def get_zones(self):
        zones = {}
        try:
            for z in self.device['zones']:
                zoneNumber = int(z['zoneNumber'])
                zones[zoneNumber] = {'name': z['name'], 'id': z['id']}
        except KeyError as e:
            exit(f'Error: key {e} not found while extacting zone information in rachio public/info response')
        # sort result by zone number
        return dict(sorted(zones.items()))

    # creates webhook for target_url if not present
    def add_device_zone_run_webhook(self, target_url):

        # check for existing webhook
        webhooks = self.list_webhooks()
        for hook in webhooks:
            if 'DEVICE_ZONE_RUN_' not in ' '.join(hook['eventTypes']):
                continue
            if hook['url'] == target_url:
                log.info(f'Webhook to {target_url} exists')
                return
            exit(f"Error - existing webhook already allocated to {hook['url']}")

        # create the webhook
        site = '{}/{}'.format(cloud_rachio, 'webhook/createWebhook')
        headers = self.authorization | {
            "accept": "application/json",
            "content-type": "application/json",
        }
        payload = {
            "resource_id": {
                "irrigation_controller_id": self.device['id']
            },
            "url": target_url,
            "event_types": [
                "DEVICE_ZONE_RUN_STARTED_EVENT",
                "DEVICE_ZONE_RUN_PAUSED_EVENT",
                "DEVICE_ZONE_RUN_STOPPED_EVENT",
                "DEVICE_ZONE_RUN_COMPLETED_EVENT"
            ],
        }

        try:
            response = requests.post(site, json=payload, headers=headers, timeout=5)
        except requests.exceptions.RequestException as e:
            exit(f'Error: {e} from {site}')
        log.debug(response.text)

    def list_webhooks(self):
        url = '{}/webhook/listWebhooks?resource_id.irrigation_controller_id={}'.format(cloud_rachio, self.device['id'])
        headers = self.authorization | {
            "accept": "application/json",
        }
        try:
            response = requests.get(url, headers=headers, timeout=5)
        except requests.exceptions.RequestException as e:
            exit(f'Error: {e} from {url}')

        try:
            webhooks = response.json()['webhooks']
        except requests.exceptions.JSONDecodeError:
            exit(f'Error: JSON decode error while processing response from {url}')
        except KeyError as e:
            exit(f'Error: key {e} not found in JSON response from {url}')

        return webhooks

    def delete_webhooks(self):
        action = f"webhook/deleteAllWebhooks?resource_id.irrigation_controller_id={self.device['id']}"

        headers = {"accept": "application/json"}

        response = requests.delete('/'.join((cloud_rachio, action)), headers=headers)
        
        log.debug(response.text)

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
