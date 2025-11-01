import requests

def read_meter(name):
    site = f'http://{name}/data.json'
    try:
        r = requests.get(site, timeout=5)
    except:
        exit(f'Error: GET request to {site} failed')

    try:
        data = r.json()
    except:
        exit(f'Error: Data format error in {site} response')

#   print(data)
    return data
