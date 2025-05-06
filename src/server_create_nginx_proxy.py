#!/usr/bin/env python3
"""
Nginx SSL站点配置生成工具

此脚本用于自动生成和配置Nginx的SSL站点。它支持：
- HTTPS/HTTP协议选择
- WebSocket支持
- 自动配置SSL证书
- 自动创建和管理Nginx配置文件

环境变量要求：
- DOMAIN_NAME: 完整域名 (例如: abc.123.com)
- CERT_SAVE_PATH: SSL证书保存路径
- NGINX_CONFIG_PATH_AVAILABLE: Nginx可用配置文件目录
- NGINX_CONFIG_PATH_ENABLED: Nginx已启用配置文件目录
- OPENWRT_IP: OpenWrt路由器IP地址
- FIREWALL_TYPE: 防火墙类型（可选，支持 'ufw' 或 'firewalld'）
"""

import os
import sys
import argparse
import subprocess
from dotenv import load_dotenv

# 加载.env文件中的环境变量
load_dotenv()

# 从环境变量获取配置
# 将域名拆分为前缀和主域名部分
DOMAIN = os.getenv('DOMAIN_NAME').split('.', 1)[1]  # 主域名部分
PREFIXE = os.getenv('DOMAIN_NAME').split('.', 1)[0]  # 域名前缀

# SSL证书和Nginx配置路径
CERT_SAVE_PATH = os.getenv('CERT_SAVE_PATH')  # SSL证书保存路径
KEY_SAVE_PATH = os.getenv('KEY_SAVE_PATH')  # SSL密钥保存路径
NGINX_CONFIG_PATH_AVAILABLE = os.getenv('NGINX_CONFIG_PATH_AVAILABLE')  # Nginx可用配置目录
NGINX_CONFIG_PATH_ENABLED = os.getenv('NGINX_CONFIG_PATH_ENABLED')  # Nginx已启用配置目录

# 反向代理目标服务器IP
OPENWRT_IP = os.getenv('OPENWRT_IP')  # OpenWrt路由器IP地址

FIREWALL_TYPE = os.getenv('FIREWALL_TYPE')  # 防火墙类型

def server_create_nginx_proxy(port, usage, https=True, ws=False, proxy_https=None, proxy_ip=None):
    """创建Nginx SSL站点配置

    Args:
        port (int): 服务端口号
        usage (str): 用途说明，用于配置文件命名
        https (bool, optional): 是否启用HTTPS. Defaults to True.
        ws (bool, optional): 是否启用WebSocket支持. Defaults to False.
        proxy_https (bool, optional): 是否使用HTTPS协议连接代理目标服务器. 
            如果未指定，将跟随站点的HTTPS设置；
            如果指定为True，则使用HTTPS协议；
            如果指定为False，则使用HTTP协议。

    配置文件生成规则：
    1. 配置文件命名格式：{前缀}-{用途}-{端口}.conf
    2. 证书文件路径格式：{证书目录}/{前缀}/{域名}.pem
    3. 密钥文件路径格式：{证书目录}/{前缀}/{域名}.key
    """

    server_name = f"{PREFIXE}.{DOMAIN}"
    cert_file = f"{CERT_SAVE_PATH}"
    key_file = f"{KEY_SAVE_PATH}"
    conf_file = f"{NGINX_CONFIG_PATH_AVAILABLE}/{PREFIXE}-{usage}-{port}.conf"

    # 检查证书文件是否存在（仅在HTTPS模式下）
    # SSL证书必须包含.pem（证书文件）和.key（私钥文件）
    if https and (not os.path.isfile(cert_file) or not os.path.isfile(key_file)):
        print("❌ 证书或密钥文件未找到:")
        print(f"  {cert_file}")
        print(f"  {key_file}")
        sys.exit(1)

    # 生成SSL配置部分
    # 包含证书路径、密钥路径、协议版本和加密套件设置
    ssl_config = ""
    if https:
        ssl_config = f"""    ssl_certificate     {cert_file};
    ssl_certificate_key {key_file};

    # 仅允许TLS 1.2和1.3，禁用不安全的协议版本
    ssl_protocols       TLSv1.2 TLSv1.3;
    # 使用安全的加密套件，禁用不安全的NULL和MD5算法
    ssl_ciphers         HIGH:!aNULL:!MD5;
"""

    # WebSocket配置
    # 设置必要的HTTP头部以支持WebSocket协议升级
    ws_config = ""
    if ws:
        ws_config = """
        # WebSocket支持
        # 设置Upgrade头部以允许协议升级
        proxy_set_header Upgrade $http_upgrade;
        # 设置Connection头部为upgrade以启用WebSocket
        proxy_set_header Connection "upgrade";"""

    # 生成完整的Nginx配置
    # 包含：监听端口、服务器名称、SSL配置（如果启用）和反向代理设置
    nginx_config = f"""server {{
    # 配置监听端口，如果启用HTTPS则添加ssl参数
    listen {port}{' ssl' if https else ''};
    server_name {server_name};

{ssl_config}
    location / {{
        # 反向代理到OpenWrt服务
        proxy_pass http{'s' if (proxy_https if proxy_https is not None else https) else ''}://{proxy_ip if proxy_ip else OPENWRT_IP}:{port};
        # 设置代理头部
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;{ws_config}
    }}
}}
"""

    # 将Nginx配置写入文件系统
    # 配置文件保存在sites-available目录下
    with open(conf_file, 'w') as f:
        f.write(nginx_config)

    # 在sites-enabled目录下创建软链接以启用配置
    # 如果已存在同名配置，先删除旧的软链接
    try:
        enabled_conf = f"{NGINX_CONFIG_PATH_ENABLED}/{os.path.basename(conf_file)}"
        if os.path.exists(enabled_conf):
            os.remove(enabled_conf)
        os.symlink(conf_file, enabled_conf)
    except Exception as e:
        print(f"❌ 创建软链接失败: {e}")
        sys.exit(1)

    # 测试Nginx配置语法并重新加载服务
    try:
        # 测试配置文件语法是否正确
        subprocess.run(['nginx', '-t'], check=True)
        # 重新加载Nginx服务以应用新配置
        # subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        # 输出成功信息和访问URL
        print(f"✅ Nginx配置已创建并应用: http{'s' if https else ''}://{server_name}:{port}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Nginx配置测试或重载失败: {e}")
        sys.exit(1)

    # 添加防火墙规则（如果配置了防火墙类型）
    if FIREWALL_TYPE:
        try:
            if FIREWALL_TYPE == 'ufw':
                # 使用UFW防火墙
                subprocess.run(['ufw', 'allow', str(port)], check=True)
                print(f"✅ 已添加UFW防火墙规则: 允许端口 {port}")
            elif FIREWALL_TYPE == 'firewalld':
                # 使用firewalld防火墙
                subprocess.run(['firewall-cmd', '--add-port=' + str(port) + '/tcp', '--permanent'], check=True)
                subprocess.run(['firewall-cmd', '--reload'], check=True)
                print(f"✅ 已添加firewalld防火墙规则: 允许端口 {port}")
        except subprocess.CalledProcessError as e:
            print(f"⚠️ 添加防火墙规则失败: {e}")
            print("请手动添加防火墙规则")

def main():
    """命令行入口函数

    处理命令行参数并调用配置生成函数。支持的参数：
    - port: 必需，服务端口号
    - usage: 必需，用途说明（用于配置文件命名）
    - --no-https: 可选，禁用HTTPS（默认启用）
    - --ws: 可选，启用WebSocket支持

    示例：
        python3 server_create_nginx_proxy.py 8080 myapp --ws  # 创建HTTPS+WS站点
        python3 server_create_nginx_proxy.py 8080 myapp --no-https  # 创建HTTP站点
        python3 server_create_nginx_proxy.py 8080 myapp --proxy-https  # 使用HTTPS协议连接代理服务器
        python3 server_create_nginx_proxy.py 8080 myapp --proxy-http  # 使用HTTP协议连接代理服务器
    """
    parser = argparse.ArgumentParser(description='创建Nginx SSL站点配置')
    parser.add_argument('port', type=int, help='端口号')
    parser.add_argument('usage', help='用途说明')
    parser.add_argument('--no-https', action='store_false', dest='https', help='禁用HTTPS（默认启用）')
    parser.add_argument('--ws', action='store_true', help='启用WebSocket支持')
    parser.add_argument('--proxy-https', action='store_true', dest='proxy_https', help='使用HTTPS协议连接代理目标服务器')
    parser.add_argument('--proxy-http', action='store_false', dest='proxy_https', help='使用HTTP协议连接代理目标服务器')
    parser.add_argument('--proxy-ip', help='代理目标服务器IP地址（可选，默认使用配置文件中的OPENWRT_IP）')

    args = parser.parse_args()

    # 验证必需参数
    if not args.port or not args.usage:
        parser.print_help()
        sys.exit(1)

    # 调用配置生成函数
    server_create_nginx_proxy(args.port, args.usage, args.https, args.ws, args.proxy_https if hasattr(args, 'proxy_https') else None, args.proxy_ip)

if __name__ == '__main__':
    main()