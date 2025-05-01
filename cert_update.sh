#!/bin/bash

# 加载环境变量
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
else
    echo -e "${RED}错误：未找到 .env 文件${NC}"
    exit 1
fi

# 定义颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 打印环境变量
echo "环境变量配置列表:"
echo -e "DOMAIN_NAME: ${GREEN}${DOMAIN_NAME}${NC}"
echo -e "CERT_DNS_TYPE: ${GREEN}${CERT_DNS_TYPE}${NC}"
echo -e "CERT_DNS_SLEEP: ${GREEN}${CERT_DNS_SLEEP}${NC}"
echo -e "CERT_SERVER: ${GREEN}${CERT_SERVER}${NC}"
echo -e "CERT_DOCKER_CONTAINER: ${GREEN}${CERT_DOCKER_CONTAINER}${NC}"

# 检查必要的环境变量
if [ -z "$DOMAIN_NAME" ] || [ -z "$CERT_DNS_TYPE" ] || [ -z "$CERT_DNS_SLEEP" ] || [ -z "$CERT_SERVER" ] || [ -z "$CERT_DOCKER_CONTAINER" ]; then
    echo -e "${RED}错误：必需的环境变量未设置${NC}"
    echo -e "${RED}请确保在 .env 文件中设置了以下变量：DOMAIN_NAME, CERT_DNS_TYPE, CERT_DNS_SLEEP, CERT_SERVER, CERT_DOCKER_CONTAINER${NC}"
    exit 1
fi

# 检查docker容器是否存在
if ! docker ps -a | grep -q "${CERT_DOCKER_CONTAINER}"; then
    echo -e "${RED}错误：未找到 Docker 容器 ${CERT_DOCKER_CONTAINER}${NC}"
    exit 1
fi

generateCrtCommand="acme.sh --force --log --issue --server ${CERT_SERVER} --dns ${CERT_DNS_SLEEP} -d \"${DOMAIN_NAME}\" -d \"*.${DOMAIN_NAME}\""

installCrtCommand="acme.sh --deploy -d \"${DOMAIN_NAME}\" -d \"*.${DOMAIN_NAME}\" --deploy-hook synology_dsm"

docker exec ${CERT_DOCKER_CONTAINER} $generateCrtCommand

docker exec ${CERT_DOCKER_CONTAINER} $installCrtCommand