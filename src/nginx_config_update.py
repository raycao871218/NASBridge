#!/usr/bin/env python3
"""
Nginx配置更新工具

此脚本用于读取services.conf配置文件，并根据配置内容自动调用create_nginx_ssl_site.py
来创建或更新Nginx的站点配置。

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

# 加载环境变量
load_dotenv()

def read_services_config(config_file):
    """读取services.conf配置文件

    Args:
        config_file (str): 配置文件路径

    Returns:
        list: 服务配置列表，每个元素为(服务名, 端口, https, ws, proxy_https, proxy_ip)
    """
    services = []
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            for line in f:
                # 跳过注释和空行
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # 解析配置行
                try:
                    name, port, https, ws, proxy_https, proxy_target = line.split()
                    # 处理代理目标地址
                    if proxy_target == 'NAS_IP':
                        proxy_ip = os.getenv('NAS_IP')
                    elif proxy_target == 'OPENWRT_IP':
                        proxy_ip = os.getenv('OPENWRT_IP')
                    else:
                        proxy_ip = proxy_target
                        
                    if not proxy_ip:
                        print(f"⚠️ 代理目标地址未配置: {proxy_target}")
                        continue
                        
                    services.append((
                        name,
                        int(port),
                        https.lower() == 'true',
                        ws.lower() == 'true',
                        proxy_https.lower() == 'true',
                        proxy_ip
                    ))
                except ValueError as e:
                    print(f"⚠️ 配置行格式错误: {line}")
                    continue
                
    except FileNotFoundError:
        print(f"❌ 配置文件不存在: {config_file}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 读取配置文件失败: {e}")
        sys.exit(1)
        
    return services

def get_nginx_config_path(service_name, port):
    """获取Nginx配置文件路径

    Args:
        service_name (str): 服务名称
        port (int): 端口号

    Returns:
        str: 配置文件路径
    """
    nginx_config_path = os.getenv('NGINX_CONFIG_PATH_AVAILABLE')
    if not nginx_config_path:
        print("❌ 环境变量NGINX_CONFIG_PATH_AVAILABLE未设置")
        sys.exit(1)
        
    domain_prefix = os.getenv('DOMAIN_NAME', '').split('.', 1)[0]
    return os.path.join(nginx_config_path, f"{domain_prefix}-{service_name}-{port}.conf")

def create_nginx_config(service):
    """为服务创建Nginx配置

    Args:
        service (tuple): (服务名, 端口, https, ws, proxy_https, proxy_ip)
    """
    name, port, https, ws, proxy_https, proxy_ip = service
    
    # 检查配置文件是否已存在
    config_path = get_nginx_config_path(name, port)
    if os.path.exists(config_path):
        print(f"ℹ️ 跳过已存在的配置: {name} (端口: {port})")
        return
        
    # 构建create_nginx_ssl_site.py的命令行参数
    script_path = os.path.join(os.path.dirname(__file__), 'create_nginx_ssl_site.py')
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
    config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'services.conf')
    
    # 读取服务配置
    services = read_services_config(config_file)
    if not services:
        print("⚠️ 未找到有效的服务配置")
        return
        
    # 为每个服务创建配置
    for service in services:
        create_nginx_config(service)
        
    # 重新加载Nginx配置
    try:
        subprocess.run(['nginx', '-s', 'reload'], check=True)
        print("✅ Nginx配置已重新加载")
    except subprocess.CalledProcessError as e:
        print(f"❌ Nginx配置重新加载失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()