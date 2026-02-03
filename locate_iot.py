import socket
import platform
import os
import logging
import subprocess

'''
This module verifies that the operating system will be able to obtain a network
address for the named IOT device. If the DNS/mDNS lookup fails, it will attempt
to determine the device's IP address from the supplied MAC address.

The routine will return either the fully qualifed name or its IP address
'''
log = logging.getLogger(__name__)

def locate(iot_name, mac_address):
    # check to see if the host will be able to determine the IP address of the IOT device
    for domain in ('', '.attlocal.net', '.local'):
        full_iot_name = iot_name + domain
        try:
            socket.gethostbyname(full_iot_name)
            break
        except socket.gaierror:
            pass
    else:
        # the system was unable to determine the IOT device's IP address using the name lookup,
        # so use arp-scan to dump the address information of all devices on the subnet
        # and find the IOT device's IP address by searching for its MAC address

        # macOS requires arp-scan to run as admin, so check that first
        # TODO could also change the group_id of the /dev/bpf* devices to staff
        if 'macOS' in platform.platform() and os.getuid() != 0:
            exit('Error: command needs to run as admin for arp-scan')

        # arp-scan also requires the IOT device's MAC address
        if mac_address is None:
            exit(f'''Error:
    Unable to determine the network address of {iot_name} using DNS
    Unable to search using arp-scan as MAC address is missing from configuration''')

        log.info('Searching for %s at MAC address %s', iot_name, mac_address)

        # use netstat to list all of the network interfaces
        ns = subprocess.run(['netstat', '-rnf', 'inet'], capture_output=True)

        # locate the default interface and extract name of the interface
        for line in str(ns.stdout, encoding='utf-8').split('\n'):
            if line.startswith('default') or line.startswith('0.0.0.0'):
                interface = line.split()[-1]
                break
        else:
            exit("Unable to determine the default network interface using arp-scan")
        log.info('Searching on network interface %s', interface)

        for i in range(1,3):      # try this a couple of times as it sometimes doesn't work
            # scan the subnet
            hosts = subprocess.run(['arp-scan', '--localnet', f'--interface={interface}', '--quiet'], capture_output=True)

            # locate the target MAC address from the responses
            text = str(hosts.stdout, encoding='utf-8').split('\n')
            for line in text:
                if mac_address in line:
                    full_iot_name = line.split()[0]
                    break
            else:
                log.info('Attempt %d to locate %s via MAC address failed', i, iot_name)
                continue
            break
        else:
            exit(f'Error: unable to determine IP address of {iot_name}')

    return full_iot_name
