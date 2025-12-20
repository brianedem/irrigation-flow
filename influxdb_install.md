## Add InfluxData Repository to System
wget -q https://repos.influxdata.com/influxdata-archive_compat.key
echo '393e8779c89ac8d958f81f942f9ad7fb82a25e133faddaf92e15b16e6ac9ce4c influxdata-archive_compat.key' | sha256sum -c && cat influxdata-archive_compat.key | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg > /dev/null
echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' | sudo tee /etc/apt/sources.list.d/influxdata.list

_Note - [alternate instructions](https://docs.influxdata.com/influxdb/v2/install/?t=Linux)_

## Install InfluxDB Service
sudo apt-get update && sudo apt-get install influxdb2
sudo systemctl start influxdb2

## Relocating the data storage:
sudo stop influxdb2
sudo cp -rp /var/lib/influxdb /mnt/ssd
sudo vim /etc/influxdb/influxdb.conf
    :g?var/lib?s??mnt/ssd?
    :wq
sudo start influxdb2

## Configuration
Open web page at http://<system>.local:8086

- Created 'water watchers' group, 'irrigation' bucket
- Configuration process generated an Operator API token

### rp7 Operator API token for CLI:
    export INFLUX_TOKEN=CBjicXJe6Z67KYndCjcCMaJmfwHvdQp7c6vgHp4pO116w-ipkANViWVDsF36q6iXxzvhIdywfaU3AhVDKiAx6g==
