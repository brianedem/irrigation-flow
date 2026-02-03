import logging
import requests

log = logging.getLogger(__name__)

def read_meter(name):
    site = f'http://{name}/data.json'
    try:
        r = requests.get(site, timeout=5)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error('Error: %s from %s', e, site)
        return {}

    try:
        data = r.json()
    except requests.exceptions.JSONDecodeError:
        log.error('Error: JSON decode error while processing response from %s', site)
        return {}

    return data
