#!/usr/bin/env python3
"""
代理服务器配置更新工具

此脚本用于读取services.conf配置文件，并根据配置内容自动调用相应的配置生成器
（server_create_nginx_proxy.py 或 server_create_caddy_proxy.py）来创建或更新站点配置。

配置文件格式：
服务名称 端口 是否HTTPS(true/false) 是否需要websocket 反向代理是否为https 反向代理目标地址

示例：
web_app 8080 true false true NAS_IP
chat_app 8443 true true true OPENWRT_IP

注意：
- 反向代理目标地址可以是环境变量名（如NAS_IP, OPENWRT_IP）或具体的IP地址
- 如果使用环境变量，需要在.env文件中配置对应的值
"""

import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import yaml

# 加载环境变量
load_dotenv()

def read_services_config_from_yaml(yaml_path):
    """从domains_config.yaml读取服务配置

    Args:
        yaml_path (str): yaml配置文件路径

    Returns:
        list: 服务配置列表，每个元素为(服务名, 端口, https, ws, proxy_https, proxy_ip)
    """
    services = []
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            zt_ip_map = {item['name']: item['ip'] for item in config.get('zt_ip', [])}
            for svc in config.get('services', []):
                name = svc.get('name')
                port = svc.get('port')
                https = svc.get('https', True)
                ws = svc.get('websocket', False)
                proxy_https = svc.get('proxy_https', True)
                target = svc.get('target')
                # 解析target为实际IP
                proxy_ip = zt_ip_map.get(target, target)
                
                # 如果proxy_ip是环境变量名（如NAS_IP），则从环境变量中获取值
                if proxy_ip and proxy_ip.endswith('_IP') and proxy_ip.isupper():
                    env_value = os.getenv(proxy_ip)
                    if env_value:
                        proxy_ip = env_value
                    else:
                        print(f"⚠️ 环境变量未设置: {proxy_ip}")
                        continue
                        
                if not proxy_ip:
                    print(f"⚠️ 代理目标地址未配置: {target}")
                    continue
                services.append((name, int(port), https, ws, proxy_https, proxy_ip))
    except Exception as e:
        print(f"❌ 读取YAML配置文件失败: {e}")
        sys.exit(1)
    return services

def get_config_path(service_name, port):
    """获取配置文件路径

    Args:
        service_name (str): 服务名称
        port (int): 端口号

    Returns:
        str: 配置文件路径
    """
    proxy_type = os.getenv('PROXY_SERVER_TYPE', 'nginx').lower()
    if proxy_type == 'nginx':
        config_path = os.getenv('NGINX_CONFIG_PATH_AVAILABLE')
        if not config_path:
            print("❌ 环境变量NGINX_CONFIG_PATH_AVAILABLE未设置")
            sys.exit(1)
        ext = '.conf'
    else:  # caddy
        config_path = os.getenv('CADDY_CONFIG_PATH')
        if not config_path:
            print("❌ 环境变量CADDY_CONFIG_PATH未设置")
            sys.exit(1)
        ext = '.caddy'
        
    domain_prefix = os.getenv('DOMAIN_NAME', '').split('.', 1)[0]
    return os.path.join(config_path, f"{domain_prefix}-{service_name}-{port}{ext}")

def create_proxy_config(service):
    """为服务创建代理配置

    Args:
        service (tuple): (服务名, 端口, https, ws, proxy_https, proxy_ip)
    """
    name, port, https, ws, proxy_https, proxy_ip = service
    
    # 检查配置文件是否已存在
    config_path = get_config_path(name, port)
    if os.path.exists(config_path):
        print(f"ℹ️ 跳过已存在的配置: {name} (端口: {port})")
        return
        
    # 根据代理服务器类型选择配置生成器
    proxy_type = os.getenv('PROXY_SERVER_TYPE', 'nginx').lower()
    if proxy_type == 'nginx':
        script_name = 'server_create_nginx_proxy.py'
    else:  # caddy
        script_name = 'server_create_caddy_proxy.py'
        
    script_path = os.path.join(os.path.dirname(__file__), script_name)
    cmd = [
        sys.executable,
        script_path,
        str(port),
        name
    ]
    
    # 添加可选参数
    if not https:
        cmd.append('--no-https')
    if ws:
        cmd.append('--ws')
    if proxy_https:
        cmd.append('--proxy-https')
    else:
        cmd.append('--proxy-http')
    if proxy_ip:
        cmd.extend(['--proxy-ip', proxy_ip])
        
    try:
        subprocess.run(cmd, check=True)
        print(f"✅ 已创建配置: {name} (端口: {port})")
    except subprocess.CalledProcessError as e:
        print(f"❌ 创建配置失败: {name} (端口: {port})")
        print(f"错误信息: {e}")

def main():
    """主函数"""
    # 获取配置文件路径
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'domains_config.yaml')
    # 读取服务配置
    services = read_services_config_from_yaml(yaml_path)
    if not services:
        print("⚠️ 未找到有效的服务配置")
        return
    # 为每个服务创建配置
    for service in services:
        create_proxy_config(service)
    # 重新加载代理服务器配置
    try:
        proxy_type = os.getenv('PROXY_SERVER_TYPE', 'nginx').lower()
        if proxy_type == 'nginx':
            subprocess.run(['nginx', '-s', 'reload'], check=True)
            print("✅ Nginx配置已重新加载")
        else:  # caddy
            subprocess.run(['systemctl', 'reload', 'caddy'], check=True)
            print("✅ Caddy配置已重新加载")
    except subprocess.CalledProcessError as e:
        print(f"❌ 代理服务器配置重新加载失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()