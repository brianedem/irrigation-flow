import logging
import requests

log = logging.getLogger(__name__)

def read_meter(name):
    site = f'http://{name}/data.json'
    try:
        r = requests.get(site, timeout=5)
        r.raise_for_status()
    except:
        log.error('GET %s failed', site)
        return {}

    try:
        data = r.json()
    except:
        log.error('Data format error in response from %s', site)
        return {}

#   print(data)
    return data
