#!/bin/bash
set -e

# Configuration - Modify these variables for your service
SERVICE_NAME="irrigation"
SERVICE_USER="irrigation"
INSTALL_DIR="/opt/${SERVICE_NAME}"
SOURCE_DIR=$PWD
SOURCE_FILES="flow_monitor.py locate_iot.py rachio.py water_meter.py"
PYTHON_SCRIPT="flow_monitor.py"
REQUIREMENTS_FILE="requirements.txt"
PYTHON_VERSION="python3"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

echo -e "${GREEN}Installing Python service: ${SERVICE_NAME}${NC}"

# Create service user (if doesn't exist)
echo -e "${YELLOW}Creating service user...${NC}"
if ! id -u ${SERVICE_USER} > /dev/null 2>&1; then
    useradd --system --no-create-home --shell /bin/false ${SERVICE_USER}
    echo "User ${SERVICE_USER} created"
else
    echo "User ${SERVICE_USER} already exists"
fi

# Create installation directory
echo -e "${YELLOW}Setting up installation directory...${NC}"
mkdir -p ${INSTALL_DIR}

# Copy application files
echo -e "${YELLOW}Copying application files...${NC}"
cp ${SOURCE_FILES} ${INSTALL_DIR}/

cd ${INSTALL_DIR}

# Create virtual environment
echo -e "${YELLOW}Creating Python virtual environment...${NC}"
${PYTHON_VERSION} -m venv venv
source venv/bin/activate

# Install Python dependencies
if [ -f "${SOURCE_DIR}/${REQUIREMENTS_FILE}" ]; then
    echo -e "${YELLOW}Installing Python dependencies...${NC}"
    cp ${SOURCE_DIR}/${REQUIREMENTS_FILE} ${INSTALL_DIR}/
    pip install --upgrade pip
    pip install -r ${REQUIREMENTS_FILE}
else
    echo -e "${YELLOW}Skipping dependencies (no requirements.txt found)${NC}"
fi

# Set permissions
echo -e "${YELLOW}Setting permissions...${NC}"
chown -R ${SERVICE_USER}:${SERVICE_USER} ${INSTALL_DIR}
chmod -R 755 ${INSTALL_DIR}

# Create systemd service file
echo -e "${YELLOW}Creating systemd service...${NC}"
cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=${SERVICE_NAME} Python Service
After=network.target influxdb.service mariadb.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="PATH=${INSTALL_DIR}/venv/bin"
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/${PYTHON_SCRIPT}
Restart=always
RestartSec=10

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable service
echo -e "${YELLOW}Enabling service...${NC}"
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service

echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo "Service management commands:"
echo "  Start service:   sudo systemctl start ${SERVICE_NAME}"
echo "  Stop service:    sudo systemctl stop ${SERVICE_NAME}"
echo "  Restart service: sudo systemctl restart ${SERVICE_NAME}"
echo "  Check status:         systemctl status ${SERVICE_NAME}"
echo "  View logs:            journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "To start the service now, run:"
echo "  sudo systemctl start ${SERVICE_NAME}"
