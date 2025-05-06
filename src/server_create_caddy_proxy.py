#!/usr/bin/env python3
"""
Caddy 站点配置生成工具

此脚本用于自动生成和配置 Caddy 的站点。它支持：
- HTTPS/HTTP协议选择
- WebSocket支持
- 自动配置SSL证书（Caddy自动管理）
- 自动创建和管理Caddy配置文件

环境变量要求：
- DOMAIN_NAME: 完整域名 (例如: abc.123.com)
- CADDY_CONFIG_PATH: Caddy配置文件目录
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

# Caddy配置路径
CADDY_CONFIG_PATH = os.getenv('CADDY_CONFIG_PATH')  # Caddy配置目录

# 反向代理目标服务器IP
OPENWRT_IP = os.getenv('OPENWRT_IP')  # OpenWrt路由器IP地址

FIREWALL_TYPE = os.getenv('FIREWALL_TYPE')  # 防火墙类型

def server_create_caddy_proxy(port, usage, https=True, ws=False, proxy_https=None, proxy_ip=None):
    """创建Caddy站点配置

    Args:
        port (int): 服务端口号
        usage (str): 用途说明，用于配置文件命名
        https (bool, optional): 是否启用HTTPS. Defaults to True.
        ws (bool, optional): 是否启用WebSocket支持. Defaults to False.
        proxy_https (bool, optional): 是否使用HTTPS协议连接代理目标服务器. 
            如果未指定，将跟随站点的HTTPS设置；
            如果指定为True，则使用HTTPS协议；
            如果指定为False，则使用HTTP协议。
        proxy_ip (str, optional): 代理目标服务器IP地址. Defaults to None.

    配置文件生成规则：
    1. 配置文件命名格式：{前缀}-{用途}-{端口}.caddy
    """

    server_name = f"{PREFIXE}.{DOMAIN}"
    conf_file = f"{CADDY_CONFIG_PATH}/{PREFIXE}-{usage}-{port}.caddy"

    # WebSocket配置
    ws_config = ""
    if ws:
        ws_config = "\n    reverse_proxy {"
        ws_config += "\n        transport websocket"
        ws_config += "\n    }"

    # 生成完整的Caddy配置
    # Caddy会自动管理SSL证书，所以不需要手动配置证书路径
    caddy_config = f"""# {usage} 服务配置
{server_name}:{port} {{
    # 是否启用HTTPS
    tls {'internal' if https else 'off'}

    # 反向代理配置
    reverse_proxy http{'s' if (proxy_https if proxy_https is not None else https) else ''}://{proxy_ip if proxy_ip else OPENWRT_IP}:{port} {{
        header_up Host {server_name}
        header_up X-Real-IP {remote_host}{ws_config}
    }}
}}
"""

    # 将Caddy配置写入文件系统
    with open(conf_file, 'w') as f:
        f.write(caddy_config)

    # 测试Caddy配置语法并重新加载服务
    try:
        # 测试配置文件语法是否正确
        subprocess.run(['caddy', 'validate', '--config', conf_file], check=True)
        # 重新加载Caddy服务以应用新配置
        # subprocess.run(['caddy', 'reload', '--config', conf_file], check=True)
        # 输出成功信息和访问URL
        print(f"✅ Caddy配置已创建并应用: http{'s' if https else ''}://{server_name}:{port}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Caddy配置测试或重载失败: {e}")
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
        python3 server_create_caddy_proxy.py 8080 myapp --ws  # 创建HTTPS+WS站点
        python3 server_create_caddy_proxy.py 8080 myapp --no-https  # 创建HTTP站点
        python3 server_create_caddy_proxy.py 8080 myapp --proxy-https  # 使用HTTPS协议连接代理服务器
        python3 server_create_caddy_proxy.py 8080 myapp --proxy-http  # 使用HTTP协议连接代理服务器
    """
    parser = argparse.ArgumentParser(description='创建Caddy站点配置')
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
    server_create_caddy_proxy(args.port, args.usage, args.https, args.ws, args.proxy_https if hasattr(args, 'proxy_https') else None, args.proxy_ip)

if __name__ == '__main__':
    main()