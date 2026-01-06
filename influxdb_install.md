# Installing InfluxDB v2 to Linux
v3 install fails as the jemalloc() used by Influx core is configured for 4k kernal page size which is incompatable with the 16k pages used by raspberian. Raspberrian can be reconfigured to use 4k pages but...

## Add InfluxData Repository to System
wget -q https://repos.influxdata.com/influxdata-archive_compat.key
echo '393e8779c89ac8d958f81f942f9ad7fb82a25e133faddaf92e15b16e6ac9ce4c influxdata-archive_compat.key' | sha256sum -c && cat influxdata-archive_compat.key | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg > /dev/null
echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' | sudo tee /etc/apt/sources.list.d/influxdata.list

_Note - [alternate instructions](https://docs.influxdata.com/influxdb/v2/install/?t=Linux)_

## Install InfluxDB Service
sudo apt-get update && sudo apt-get install influxdb2
sudo systemctl start influxdb

## Relocating the data storage:
sudo systemctl stop influxdb
sudo cp -rp /var/lib/influxdb /mnt/ssd
sudo vim /etc/influxdb/influxdb.conf (or config.toml for alternate install)
    :g?var/lib?s??mnt/ssd?
    :wq
sudo systemctl start influxdb

## Configuration
Open web page at http://<system>.local:8086

- Created 'water watchers' group, 'irrigation' bucket
- Configuration process generated an Operator API token

### Operator API token for CLI:
cat > ~/influx_token
export INFLUX_TOKEN=<administrative token from web page>
^D

### Weather Station Notes
- influx org create -n weather
- influx bucket create -o weather -n measurements
- influx bucket create -o weather -n events -r 24h
- influx auth create -o weather --all-access
